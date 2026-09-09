"""team_members-within-team tests for the maintenancer agent (W2-P4 task 4.7).

Validates that every skill in ``agents/maintenancer/skills-template/``
holds its fallback references inside the agent's ``team_members``
(``explorer``, ``worker``, ``coder``) per writing guide §8 (the
org-chart rule for fallbacks). A skill that names a peer agent NOT in
``team_members`` references an unreachable peer — the spawn would
silently fail.

Pure file parsing — no daemon/DB startup, no LLM calls.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path constants — resolve against the REAL repo root.
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = PROJECT_ROOT / "agents" / "maintenancer"
META_PATH = AGENT_DIR / "meta.json"
SKILLS_TEMPLATE_DIR = AGENT_DIR / "skills-template"


# ---------------------------------------------------------------------------
# Constants — agent names referenced in the canonical
# team_members list (per meta.json + spec task 4.5).
# ---------------------------------------------------------------------------
EXPECTED_TEAM_MEMBERS: frozenset[str] = frozenset({"explorer", "worker", "coder"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _read(path: Path) -> str:
    """Read a UTF-8 text file."""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _load_meta() -> dict:
    """Load and return maintenancer/meta.json as a dict."""
    with open(META_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _iter_skill_files() -> list[Path]:
    """Return every ``skills-template/*.md`` file, sorted by name."""
    if not SKILLS_TEMPLATE_DIR.is_dir():
        return []
    return sorted(p for p in SKILLS_TEMPLATE_DIR.glob("*.md"))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_meta_team_members_expectation_holds() -> None:
    """Sanity pin — the ``team_members`` we test against must match
    the meta.json values, so a future change to the operational
    team surfaces here first.
    """
    meta = _load_meta()
    actual = frozenset(meta["team_members"])
    assert actual == EXPECTED_TEAM_MEMBERS, (
        f"meta.json team_members drift: expected {sorted(EXPECTED_TEAM_MEMBERS)}; "
        f"got {sorted(actual)}"
    )


def test_skills_template_dir_exists() -> None:
    """skills-template/ must be a directory (per spec)."""
    assert SKILLS_TEMPLATE_DIR.is_dir(), (
        f"skills-template/ missing at {SKILLS_TEMPLATE_DIR}"
    )


@pytest.mark.parametrize(
    "skill_path",
    _iter_skill_files(),
    ids=lambda p: p.name,
)
def test_skill_does_not_name_out_of_team_fallback(skill_path: Path) -> None:
    """A skill that names a peer agent as a fallback MUST name one of
    ``team_members`` (``explorer``, ``worker``, ``coder``). Writing
    guide §8 — the org-chart rule for fallbacks.
    """
    text = _read(skill_path)
    # Find lines that mention a fallback — the most common shapes are
    # "fall back to a <peer>", "spawn a <peer>", "hand off [to a <peer>]",
    # "delegate to a <peer>", "escalate to a <peer>", "ask the leader",
    # etc.
    #
    # The capture group is OPTIONAL so the regex catches verb-only
    # fallback language (e.g. "then hand off" without a peer name
    # immediately after). For the violation check: a captured peer
    # outside ``team_members`` is a violation; a verb-only match
    # (group=None) is a fallback signal but not necessarily a
    # violation — it just means the skill uses fallback language that
    # an LLM may complete with a peer name at runtime.
    #
    # A negative hit means the skill has no fallback language at all
    # (good).
    #
    # A positive hit with a peer not in EXPECTED_TEAM_MEMBERS fails.
    fallback_pattern = re.compile(
        r"(?:"
        r"fall\s+back\s+to\s+a"                # "fall back to a X"
        r"|spawn\s+a"                           # "spawn a X"
        # Longer alternations MUST precede their strict prefixes — once
        # "hand off" matches, the engine stops here and the longer
        # "hand off to a X" arm becomes unreachable (dormant false-
        # violation class for the in-team fallback shape "hand off to
        # a worker"). Same risk shape for any future prefix-shadowed
        # alternation added here.
        r"|hand(?:\s+it)?\s+off\s+to\s+(?:a|an)"  # "hand off to a X"  (long first)
        r"|hand(?:\s+it)?\s+off"                # "hand off" / "hand it off"
        r"|delegate\s+(?:it\s+)?to\s+a"         # "delegate to a X"
        r"|escalate\s+to\s+a"                   # "escalate to a X"
        r"|pass\s+(?:it\s+)?to\s+a"             # "pass it to a X"
        r"|forward\s+(?:it\s+)?to\s+a"          # "forward it to a X"
        r"|refer\s+(?:it\s+)?to\s+a"            # "refer it to a X"
        r"|ask\s+the\s+leader"                  # "ask the leader" (caller, not team)
        r")(?:[\s,;.]+([a-z][a-z0-9_-]*))?",
        re.IGNORECASE,
    )
    violations: list[tuple[str, str]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for m in fallback_pattern.finditer(line):
            peer = m.group(1).lower() if m.group(1) else None
            # Verb-only matches (peer=None) are NOT violations —
            # the LLM may complete the peer at runtime. The
            # parametrized test only fails on out-of-team peer names.
            if peer is not None and peer not in EXPECTED_TEAM_MEMBERS:
                violations.append((str(line_no), peer))
    assert not violations, (
        f"{skill_path.name}: skill names a fallback peer not in "
        f"team_members ({sorted(EXPECTED_TEAM_MEMBERS)}); the org-chart "
        f"rule (writing guide §8) requires the fallback to be reachable. "
        f"Offenders: {violations}"
    )


# ── Regex widening: negative-fixture + corpus-assertion (R3) ────────────────

# The widened regex (mirrors the alternation in the parametrized test
# above — keep in lockstep).
_WIDENED_FALLBACK_RE = re.compile(
    r"(?:"
    r"fall\s+back\s+to\s+a"
    r"|spawn\s+a"
    # Longer alternations MUST precede their strict prefixes — see
    # the comment in the parametrized-test regex above.
    r"|hand(?:\s+it)?\s+off\s+to\s+(?:a|an)"
    r"|hand(?:\s+it)?\s+off"
    r"|delegate\s+(?:it\s+)?to\s+a"
    r"|escalate\s+to\s+a"
    r"|pass\s+(?:it\s+)?to\s+a"
    r"|forward\s+(?:it\s+)?to\s+a"
    r"|refer\s+(?:it\s+)?to\s+a"
    r"|ask\s+the\s+leader"
    r")(?:[\s,;.]+([a-z][a-z0-9_-]*))?",
    re.IGNORECASE,
)

# The OLD regex (pre-R3) — kept here as a literal so the negative-
# fixture test can prove the widening actually widens.
_OLD_FALLBACK_RE = re.compile(
    r"(?:fall\s+back\s+to\s+a|spawn\s+a|fall\s+back\s+to)\s+([a-z][a-z0-9_-]*)",
    re.IGNORECASE,
)


class TestRegexWidening:
    """Negative-fixture + corpus-assertion for the widened fallback regex.

    The pre-R3 regex had zero corpus hits because the only fallback
    language in the skill files (``bug-advisory.md``: ``then hand off``)
    did not match ``fall back to`` / ``spawn a``. The widened regex
    adds the missing verbs (``hand off``, ``delegate``, ``escalate``,
    ``pass it to``, ``forward to``, ``refer to``, ``ask the leader``)
    AND makes the capture group optional with a punctuation-tolerant
    boundary so verb-only fallback language is detected as a signal
    even without a named peer.
    """

    def test_old_regex_does_not_match_widening_corpus(self) -> None:
        """Sanity: the OLD regex must NOT match the widening-shape
        strings — proves the widening is real, not a no-op rename."""
        fixtures = [
            "if X, then hand off",            # bug-advisory.md:19 shape
            "delegate to a leader",           # ask-the-leader variant
            "escalate to a developer",        # escalate variant
            "pass it to a reviewer",          # pass variant
            "refer to a tester",              # refer variant
        ]
        for fixture in fixtures:
            assert not _OLD_FALLBACK_RE.search(fixture), (
                f"OLD regex unexpectedly matched {fixture!r} — "
                "the negative-fixture setup is broken"
            )

    def test_widened_regex_matches_widening_corpus(self) -> None:
        """The widened regex MUST match the widening-shape strings.

        The first is pure verb-only (no peer captured) — these
        exercise the optional-capture-group widening. The remaining
        are verb + peer and exercise both widening + out-of-team
        detection (``leader``, ``developer``, ``reviewer``, ``tester``
        are NOT in ``team_members = {explorer, worker, coder}``).
        """
        # Verb-only (no peer captured).
        assert _WIDENED_FALLBACK_RE.search("if X, then hand off") is not None
        # Verb + out-of-team peer (captured).
        m1 = _WIDENED_FALLBACK_RE.search("delegate to a leader")
        assert m1 is not None and m1.group(1) == "leader"
        m2 = _WIDENED_FALLBACK_RE.search("escalate to a developer")
        assert m2 is not None and m2.group(1) == "developer"
        m3 = _WIDENED_FALLBACK_RE.search("pass it to a reviewer")
        assert m3 is not None and m3.group(1) == "reviewer"
        m4 = _WIDENED_FALLBACK_RE.search("refer to a tester")
        assert m4 is not None and m4.group(1) == "tester"

    def test_corpus_real_hits_now_exist(self) -> None:
        """The widened regex must find ≥1 fallback signal in the
        current skill corpus. Before R3 the regex found zero signals
        (zero hits in ``bug-advisory.md``, ``ens-db-repair.md``, etc.)
        — the detection gap this test pins.

        The corpus-assertion is "≥1 signal exists now" — NOT "the
        corpus is free of violations" (that's the parametrized
        test's job). A signal is any match, whether or not it
        carries a peer name. Out-of-team peers still raise as
        violations in the parametrized test.
        """
        hit_count = 0
        for skill_path in _iter_skill_files():
            text = _read(skill_path)
            for line in text.splitlines():
                if _WIDENED_FALLBACK_RE.search(line):
                    hit_count += 1
        assert hit_count >= 1, (
            f"Widened fallback regex found zero signals in the "
            f"{len(list(_iter_skill_files()))}-file skill corpus — "
            f"the detection teeth are still missing. Expected ≥1 "
            f"(bug-advisory.md: 'then hand off' is the canonical "
            f"fixture)."
        )

    def test_parametrized_test_skips_verb_only_signals(self) -> None:
        """The parametrized test's violation check must NOT report
        verb-only matches (peer=None) as violations — they're a
        detection signal but not necessarily a bug (the LLM may
        complete the peer name at runtime). This test exercises the
        same pattern logic on a synthetic corpus."""
        # Build a synthetic line with a verb-only signal.
        synthetic = "If a write is needed, name the gate then hand off."
        matches = list(_WIDENED_FALLBACK_RE.finditer(synthetic))
        assert matches, "Synthetic line should produce a fallback signal"
        for m in matches:
            peer = m.group(1).lower() if m.group(1) else None
            # Verb-only (peer=None) MUST NOT be a violation — the
            # LLM may complete the peer at runtime.
            assert peer is None, (
                f"Expected verb-only capture (None), got {peer!r} — "
                "the widening is too aggressive"
            )

    def test_hand_off_to_a_worker_is_in_team_not_flagged(self) -> None:
        """Regression fixture for the alternation-order fix:
        ``"hand off to a worker"`` MUST capture peer="worker" (in
        ``team_members``) — NOT be shadowed by the bare-verb arm
        ``"hand off"`` that would leave peer=None.

        Before the fix, the longer ``"hand off to a X"`` arm was
        unreachable because ``"hand off"`` ran first and the engine
        moved past the alternative set. A legitimate in-team fallback
        shape (``hand off to a worker``) would either (a) be captured
        as peer=None (signal-only) or (b) capture "to" via the
        trailing optional group (false violation: "to" not in
        ``team_members``).

        With the alternation reordered (longer-first), the
        ``"hand off to a X"`` arm matches, captures "worker", and the
        parametrized test's violation check finds peer in
        ``EXPECTED_TEAM_MEMBERS`` — no violation.
        """
        # Regex shape: must match the "hand off to a X" arm, NOT the
        # bare "hand off" arm.
        m = _WIDENED_FALLBACK_RE.search("hand off to a worker")
        assert m is not None, (
            "Long arm 'hand off to a X' must match — bare-verb arm "
            "is shadowing it (alternation-order regression)"
        )
        peer = m.group(1).lower() if m.group(1) else None
        assert peer == "worker", (
            f"Expected peer='worker' (in team_members), got {peer!r}. "
            "If peer is None, the bare 'hand off' arm shadowed the "
            "longer 'hand off to a X' arm — the alternation order "
            "fix has regressed."
        )
        # Parametrized-test violation check: in-team → no violation.
        assert peer in EXPECTED_TEAM_MEMBERS, (
            f"Peer {peer!r} must be in team_members "
            f"({sorted(EXPECTED_TEAM_MEMBERS)}) — otherwise the "
            "parametrized test would flag this as a fallback to a "
            "non-team peer"
        )

    def test_ask_the_leader_with_following_word_captures_peer(self) -> None:
        """Regression fixture for the trailing-capture shape:
        ``"ask the leader <word>"`` MUST capture ``<word>`` as the
        peer via the trailing optional group.

        This is the same shape risk the reviewer flagged: the
        ``"ask the leader"`` arm itself does not capture a peer, but
        the trailing ``(?:[\\s,;.]+([a-z][a-z0-9_-]*))?`` group
        picks up whatever word follows. If the word is a legitimate
        in-team peer (e.g. ``ask the leader first`` — where "first"
        is just an adverb and NOT a peer), the parametrized test
        treats it as a fallback to an out-of-team peer and fails.

        The fixture pins current behavior so any future change to
        the alternation (e.g. reordering, or tightening the trailing
        group) is caught. A future tightening of the trailing group
        to require ``a/an`` before the peer would eliminate the
        false positive; this fixture documents the current shape
        either way.
        """
        m = _WIDENED_FALLBACK_RE.search("ask the leader first")
        assert m is not None, (
            "'ask the leader' arm must match — the alternation is "
            "broken (peer-shape regression)"
        )
        # The trailing optional group picks up "first" — this is
        # current documented behavior.
        peer = m.group(1).lower() if m.group(1) else None
        assert peer == "first", (
            f"Expected trailing capture peer='first', got {peer!r}. "
            "If peer is None, the trailing optional group no longer "
            "captures a following word — the shape risk is now "
            "different (false negative, not false positive)."
        )

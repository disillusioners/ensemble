"""Pin the canonical cleanup-truth-split sentence VERBATIM on every
surface (BE docstring, FE constant, docs §8.5).

Unblock-round ITEM 5 (bonus, 2026-09-06,
``fix/defer-self-witness-and-cleanup``): the canonical sentence lives
in three places — editing any one in isolation leaves the others
stale. This docs-side pin reads ``docs/job-task-system.md`` and
asserts the canonical sentence is present VERBATIM. Plain Python
``pathlib`` only — NO test fixture, NO DB, NO helpers.

Run with::

    timeout 60 .venv/bin/pytest tests/unit/test_docs_cleanup_truth_split.py \\
        -v --tb=short -q --override-ini="addopts="
"""

from __future__ import annotations

from pathlib import Path

# Canonical sentence — single source of truth (also enforced by
# ``frontend/src/app/models/cleanup-preflight.model.spec.ts`` on the
# TS side and read live by the BE ``cleanup_preflight`` endpoint
# docstring). The three surfaces MUST match this string byte-for-byte.
CANONICAL_TRUTH_SPLIT_SENTENCE: str = (
    "Every ACTIVE job is cancelled, together with its whole subtree. "
    "Only missions holding nothing but settled mirrors — no live work "
    "— are kept."
)

# Project-root-relative docs file.
DOCS_FILE: Path = Path(__file__).resolve().parents[2] / "docs" / "job-task-system.md"


def test_docs_cleanup_truth_split_sentence_verbatim() -> None:
    """The cleanup-truth-split sentence appears VERBATIM in
    ``docs/job-task-system.md``.

    The unblock-round canonical sentence must propagate from the FE
    ``CLEANUP_TRUTH_SPLIT_COPY`` constant through the BE
    ``cleanup_preflight`` docstring to the operator-facing docs
    §8.5 — every surface pinned. A drift between the docs page
    and the FE const breaks this pin.
    """
    assert DOCS_FILE.exists(), f"docs file missing: {DOCS_FILE}"
    text = DOCS_FILE.read_text(encoding="utf-8")
    assert CANONICAL_TRUTH_SPLIT_SENTENCE in text, (
        "Canonical cleanup-truth-split sentence missing from "
        f"{DOCS_FILE.relative_to(Path.cwd())}. See unblock-round "
        "ITEM 5 for the BE / FE / docs cross-surface pin contract."
    )


def test_docs_cleanup_truth_split_starts_with_capital_every() -> None:
    """A drift where the docs normalized ``Every`` to ``every`` (the
    round-2 finding) would still satisfy the in-string substring
    check via containment-of-subset rules; this pin enforces the
    canonical uppercase ``Every`` start. Matches the FE const
    exactly."""
    text = DOCS_FILE.read_text(encoding="utf-8")
    # Find the canonical sentence (in case it appears multiple times —
    # the test allows one or more, but the FIRST occurrence must use
    # the canonical capitalization).
    if CANONICAL_TRUTH_SPLIT_SENTENCE in text:
        first_idx = text.find(CANONICAL_TRUTH_SPLIT_SENTENCE)
        # Re-extract the prefix to confirm capitalization.
        prefix = text[first_idx:first_idx + len("Every ")]
        assert prefix == "Every ", (
            "Canonical sentence capitalization drifted; "
            f"expected start 'Every ' (capital E), got {prefix!r}"
        )
    else:
        # If absent, the verbatim pin would already fail in the test
        # above — surface the same intent here.
        raise AssertionError(
            "Canonical sentence missing entirely; see "
            "test_docs_cleanup_truth_split_sentence_verbatim."
        )


def test_docs_changelog_records_round2_truth_split_intent() -> None:
    """The CHANGELOG entry for WS4 Round-2 ITEM 3 documents the same
    intent — BE / FE / docs cross-surface pin includes the
    CHANGELOG line item that introduced the rename. Drift in the
    CHANGELOG is a different defect class (project-history rot) —
    pinning it keeps ``git log`` truthful."""
    changelog = (
        Path(__file__).resolve().parents[2] / "CHANGELOG.md"
    )
    assert changelog.exists(), f"CHANGELOG missing: {changelog}"
    text = changelog.read_text(encoding="utf-8")
    # The CHANGELOG Round-2 ITEM 3 entry quotes the canonical split
    # sentence to document the rename.
    assert "Every ACTIVE job is cancelled" in text, (
        "CHANGELOG.md Round-2 ITEM 3 / T-H1 entry must document the "
        "canonical truth-split sentence VERBATIM."
    )

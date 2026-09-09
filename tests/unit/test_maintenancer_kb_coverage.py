"""KB coverage tests for the maintenancer agent (W1-P3, tasks 3.1-3.8).

Validates the local ensemble KB delivered under
``agents/maintenancer/knowledge/*.md``:

  1.  All six KB docs exist and are non-stub.
  2.  Staleness headers are well-formed
      (``last-verified-against: v<semver>``) and pin to the
      **release tag** (NOT a rolling SHA — architect §6.3).
  3.  The 10 required trap strings land in the right docs.
  4.  Release-tag pinning equals the merge-base tag at KB-file-change
      time (commit-time check), not ``git rev-parse HEAD``.
  5.  Zero 40-hex SHA pins anywhere in the KB.
  6.  No content duplication across docs (each KB doc carries its
      own unique anchors).
  7.  The KB INDEX in ``memory.md`` lists triggers + verification
      pointers only — does NOT duplicate KB content. (Tolerant skip
      when ``memory.md`` is absent, pre-merge of P1 — see WRINKLE.)
  8.  The ``kb-curator`` skill frontmatter version matches and carries
      the three responsibilities (a/b/c) without startup-hook claims.

Runtime divergence (anchor drift after merge) is warn-only — refuse-to-
cite is unenforceable against an LLM per architect §6.3.

Modelled after ``tests/unit/test_project_manager_agent.py`` — pure
file parsing, no daemon/DB startup, no LLM calls.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Path constants — resolve against the REAL repo root.
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = PROJECT_ROOT / "agents" / "maintenancer"
KB_DIR = AGENT_DIR / "knowledge"
SKILLS_DIR = AGENT_DIR / "skills-template"
MEMORY_PATH = AGENT_DIR / "memory.md"

KB_DOCS: tuple[tuple[str, Path], ...] = (
    ("01", KB_DIR / "01-architecture-overview.md"),
    ("02", KB_DIR / "02-jobs-missions-admission-state.md"),
    ("03", KB_DIR / "03-log-forensics.md"),
    ("04", KB_DIR / "04-known-traps.md"),
    ("05", KB_DIR / "05-repair-runbooks.md"),
    ("06", KB_DIR / "06-restart-upgrade-runbook.md"),
)
KB_CURATOR_PATH = SKILLS_DIR / "kb-curator.md"

# Staleness pinned to the release tag (NOT a rolling SHA — this repo
# merges 20+/day). Read from pyproject.toml at test time.
EXPECTED_RELEASE_TAG = "v0.12.4"

# 40-hex SHA pattern — must NEVER appear in any KB doc.
SHA40_RE = re.compile(r"\b[0-9a-f]{40}\b")
STALENESS_HEADER_RE = re.compile(
    r"^last-verified-against:\s*v(\d+\.\d+\.\d+)\s*$",
    re.MULTILINE,
)

# The 10 required trap strings + which docs they MUST appear in.
# (Per spec task 3.8.)
REQUIRED_TRAP_STRINGS: tuple[tuple[str, str], ...] = (
    # (string, doc-id-it-must-appear-in)
    ("time-bracket", "03"),
    ("time-bracket", "04"),  # also lands in traps doc
    ("data/instances.db", "04"),
    ("runner.py:486-491", "04"),
    ("feb5e915", "04"),
    ("pause-first", "04"),
    ("DROP NOT NULL", "04"),
    ("report_injections.content", "04"),
    ("sentinel", "04"),
    ("3-factor", "04"),
    ("adopt_stale_txn", "04"),
    # Cross-doc: pause-first also appears in 06 (runbook)
    ("pause-first", "06"),
    # 01 architecture cites the registry singleton — pinned by
    # `1170-1175` should appear in 01 OR 02 (architecture/jobs pages)
    # — not required by trap list but covered by content checks.
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _read(path: Path) -> str:
    """Read a UTF-8 text file; raise a clear error if missing."""
    if not path.exists():
        pytest.fail(f"Required KB file missing: {path}")
    return path.read_text(encoding="utf-8")


def _release_tag_from_pyproject() -> str:
    """Parse pyproject.toml version and return ``v<version>``."""
    pyproject = PROJECT_ROOT / "pyproject.toml"
    if not pyproject.exists():
        pytest.fail(f"pyproject.toml missing at {pyproject}")
    text = pyproject.read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not m:
        pytest.fail(f"Could not parse version from {pyproject}")
    return f"v{m.group(1)}"


def _git_merge_base_tag() -> str | None:
    """Best-effort merge-base release tag at HEAD.

    Returns ``None`` if the working tree has no tags reachable from
    HEAD (fresh worktree). The release-tag pin test uses
    ``EXPECTED_RELEASE_TAG`` (from pyproject.toml) as the primary
    truth; this helper is reserved for the commit-time drift gate.
    """
    try:
        out = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0", "HEAD"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        tag = out.stdout.strip()
        return tag or None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


# ---------------------------------------------------------------------------
# 1. All six KB docs exist + non-stub
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "doc_id,path",
    KB_DOCS,
    ids=lambda v: v if isinstance(v, str) and not str(v).endswith(".md") else Path(v).name,
)
def test_kb_doc_present_and_non_stub(doc_id: str, path: Path) -> None:
    """Each KB doc must exist, have content, and start with a header."""
    text = _read(path)
    assert len(text) > 200, f"{path} is too short ({len(text)} chars) — looks like a stub"
    # Must start with a level-1 heading
    assert text.lstrip().startswith("#"), f"{path} does not start with a markdown header"
    # Doc id in the heading
    assert doc_id in text.split("\n", 1)[0], (
        f"{path} first line should contain doc id '{doc_id}': "
        f"got {text.split(chr(10), 1)[0]!r}"
    )


# ---------------------------------------------------------------------------
# 2. Staleness headers well-formed
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "doc_id,path",
    KB_DOCS,
    ids=lambda v: v if isinstance(v, str) and not str(v).endswith(".md") else Path(v).name,
)
def test_kb_doc_staleness_header(doc_id: str, path: Path) -> None:
    """Every KB doc carries a well-formed ``last-verified-against`` header.

    Format: ``last-verified-against: v<semver>`` on its own line.
    """
    text = _read(path)
    m = STALENESS_HEADER_RE.search(text)
    assert m is not None, (
        f"{path} missing or malformed 'last-verified-against: vX.Y.Z' header. "
        f"First 200 chars: {text[:200]!r}"
    )


# ---------------------------------------------------------------------------
# 3. The 10 required trap strings land in the right docs
# ---------------------------------------------------------------------------
_DOC_FILENAMES: dict[str, str] = {
    "01": "01-architecture-overview.md",
    "02": "02-jobs-missions-admission-state.md",
    "03": "03-log-forensics.md",
    "04": "04-known-traps.md",
    "05": "05-repair-runbooks.md",
    "06": "06-restart-upgrade-runbook.md",
}


@pytest.mark.parametrize("string,doc_id", REQUIRED_TRAP_STRINGS)
def test_required_trap_string_in_doc(string: str, doc_id: str) -> None:
    """Each required trap string must appear in the doc(s) named in the spec."""
    path = KB_DIR / _DOC_FILENAMES[doc_id]
    text = _read(path)
    assert string in text, (
        f"Required trap string {string!r} missing from {path.name}"
    )


# ---------------------------------------------------------------------------
# 4. Release-tag pinning equals the merge-base release tag
# ---------------------------------------------------------------------------
def test_release_tag_pin_matches_pyproject() -> None:
    """All six KB docs' ``last-verified-against`` MUST equal the
    pyproject.toml release tag (the merge-base release tag at
    KB-file-change time), NOT ``git rev-parse HEAD``.

    Architect §6.3: rolling SHA pins warn perpetually and train the
    agent to ignore warnings — release tags are immutable.
    """
    expected = _release_tag_from_pyproject()
    assert expected == EXPECTED_RELEASE_TAG, (
        f"Test expectation drift: EXPECTED_RELEASE_TAG={EXPECTED_RELEASE_TAG} "
        f"vs pyproject.toml={expected}. Update the constant when bumping."
    )
    mismatches: list[str] = []
    for doc_id, path in KB_DOCS:
        text = _read(path)
        m = STALENESS_HEADER_RE.search(text)
        assert m is not None, f"{path} missing staleness header"
        pinned = f"v{m.group(1)}"
        if pinned != expected:
            mismatches.append(f"{path.name}: {pinned} != {expected}")
    assert not mismatches, (
        "KB docs not pinned to current release tag "
        f"(expected {expected}):\n  " + "\n  ".join(mismatches)
    )


def test_release_tag_pin_not_rolling_sha() -> None:
    """Belt-and-suspenders: no doc may pin against a rolling SHA,
    even if the staleness header is well-formed.

    The 40-hex pattern is checked as a substring on each KB doc and
    on the kb-curator skill. A release-tag-only pin would never
    accidentally contain a 40-hex sequence.
    """
    leaks: list[str] = []
    for _doc_id, path in KB_DOCS:
        text = _read(path)
        if SHA40_RE.search(text):
            leaks.append(path.name)
    if KB_CURATOR_PATH.exists():
        text = KB_CURATOR_PATH.read_text(encoding="utf-8")
        if SHA40_RE.search(text):
            leaks.append(KB_CURATOR_PATH.name)
    assert not leaks, (
        "40-hex SHA pins found in (forbidden per architect §6.3): "
        + ", ".join(leaks)
    )


# ---------------------------------------------------------------------------
# 5. No duplication across docs (architect §6.2 Tier 1 invariant)
# ---------------------------------------------------------------------------
def test_no_full_content_duplication_across_docs() -> None:
    """KB docs must not be near-identical (each owns its own concerns).

    We compare sorted unique-line counts pairwise. A doc that simply
    restates another fails this. Small overlap (cross-refs, headers)
    is acceptable; large overlap (>50%) is a finding.
    """
    texts: dict[str, set[str]] = {}
    for doc_id, path in KB_DOCS:
        texts[doc_id] = set(_read(path).splitlines())

    ids = [d for d, _ in KB_DOCS]
    findings: list[str] = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            overlap = len(texts[a] & texts[b])
            smaller = min(len(texts[a]), len(texts[b]))
            if smaller == 0:
                continue
            ratio = overlap / smaller
            # Allow cross-refs and headers to overlap; flag if >50%.
            if ratio > 0.5:
                findings.append(
                    f"{a} vs {b}: {ratio:.0%} line overlap ({overlap}/{smaller})"
                )
    assert not findings, "KB docs appear to duplicate each other:\n  " + "\n  ".join(findings)


# ---------------------------------------------------------------------------
# 6. KB INDEX in memory.md — tolerant skip if absent (pre-merge of P1)
# ---------------------------------------------------------------------------
def test_memory_md_kb_index_no_content_duplication() -> None:
    """The KB INDEX in ``memory.md`` must list triggers + verification
    pointers only — NOT duplicate KB content (architect §6.2 Tier 1,
    load-bearing).

    WRINKLE: ``memory.md`` is authored by P1 in a parallel worktree
    and is NOT YET PRESENT in this worktree (P3-only). The test
    must pass both pre-merge (memory.md absent → tolerant skip with
    warning) and post-merge (memory.md present → assert no KB
    content duplication).
    """
    if not MEMORY_PATH.exists():
        pytest.skip(
            f"{MEMORY_PATH} not yet authored by P1 (parallel worktree). "
            "This test will activate on W1 merge."
        )
    text = MEMORY_PATH.read_text(encoding="utf-8")

    # 1. INDEX must mention each KB doc id at least once.
    for doc_id, _path in KB_DOCS:
        assert doc_id in text, (
            f"{MEMORY_PATH} KB INDEX missing doc id {doc_id}"
        )

    # 2. The INDEX must NOT carry anchor-style references that look
    # like file:line citations (which would mean the INDEX is
    # duplicating KB content). Allow section references like "§04
    # trap (iv)" — forbid file paths with line numbers.
    forbidden_in_index = re.compile(
        r"(?:daemon|agents|routes|services)/[\w/\.\-]+\.py?:\d{2,5}"
    )
    bad = forbidden_in_index.findall(text)
    assert not bad, (
        f"{MEMORY_PATH} KB INDEX contains file:line anchors that "
        f"duplicate KB content: {bad}"
    )


# ---------------------------------------------------------------------------
# 7. kb-curator skill frontmatter + responsibilities
# ---------------------------------------------------------------------------
def test_kb_curator_skill_exists_and_frontmatter() -> None:
    """The kb-curator skill must exist with a well-formed frontmatter
    version matching the manifest version (writing guide §6).
    """
    text = _read(KB_CURATOR_PATH)
    # Frontmatter starts with --- on line 1.
    lines = text.splitlines()
    assert lines and lines[0].strip() == "---", (
        f"{KB_CURATOR_PATH} does not start with '---' frontmatter"
    )
    # Find the closing --- of the frontmatter.
    end = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = i
            break
    assert end is not None, (
        f"{KB_CURATOR_PATH} frontmatter not closed with '---'"
    )
    frontmatter = "\n".join(lines[1:end])
    m = re.search(r"^version:\s*(\S+)\s*$", frontmatter, re.MULTILINE)
    assert m is not None, (
        f"{KB_CURATOR_PATH} frontmatter missing 'version' field"
    )
    assert m.group(1) == "1.0.0", (
        f"{KB_CURATOR_PATH} frontmatter version {m.group(1)!r} "
        f"!= '1.0.0' (manifest version must match per writing guide §6)"
    )


@pytest.mark.parametrize("responsibility_keyword", [
    "Index",
    "Content path",
    "first-turn",
])
def test_kb_curator_skill_three_responsibilities(
    responsibility_keyword: str,
) -> None:
    """The skill body must include the three responsibility sections
    (a) Index, (b) Content path, (c) First-turn RAG mirror.

    Per spec task 3.7 — TIERED design.
    """
    text = _read(KB_CURATOR_PATH)
    assert responsibility_keyword in text, (
        f"{KB_CURATOR_PATH} missing responsibility keyword "
        f"{responsibility_keyword!r}"
    )


def test_kb_curator_skill_no_startup_hook_claims() -> None:
    """The skill MUST NOT claim a startup-recording capability
    (architecturally impossible — skills are static prompt text;
    no startup execution hook exists).
    """
    text = _read(KB_CURATOR_PATH)
    # Forbidden patterns: "auto-record on startup", "startup
    # execution hook", "auto-record at boot", etc.
    bad_patterns = [
        re.compile(r"auto[- ]record(?:s|ing)?\s+(?:on|at|during)\s+startup", re.IGNORECASE),
        re.compile(r"startup\s+hook\s+(?:records|executes|runs)", re.IGNORECASE),
        re.compile(r"records?\s+on\s+boot", re.IGNORECASE),
        re.compile(r"automatic(?:ally)?\s+records?\s+(?:on|at)\s+(?:startup|boot)", re.IGNORECASE),
    ]
    found: list[str] = []
    for pat in bad_patterns:
        if pat.search(text):
            found.append(pat.pattern)
    assert not found, (
        f"{KB_CURATOR_PATH} claims a startup-recording capability "
        f"(architecturally impossible): {found}"
    )


# ---------------------------------------------------------------------------
# 8. Meta — runtime divergence is warn-only (architect §6.3)
# ---------------------------------------------------------------------------
def test_release_tag_merge_base_drift_is_warn_only() -> None:
    """Runtime divergence between the pinned release tag and the
    working-tree HEAD tag is warn-only — refuse-to-cite is
    unenforceable against an LLM.

    If the working tree's HEAD is ahead of the pinned tag (this is
    normal in a fresh worktree), emit a warning but do not fail.
    """
    head_tag = _git_merge_base_tag()
    if head_tag is None:
        pytest.skip("No tag reachable from HEAD (fresh worktree).")
    expected = _release_tag_from_pyproject()
    if head_tag != expected:
        # Warn but do not fail — the coverage test is the merge-time
        # gate, not runtime.
        import warnings

        warnings.warn(
            f"Runtime tag drift: HEAD={head_tag} != pyproject={expected}. "
            f"Coverage test is merge-time-only; runtime divergence is "
            f"warn-only per architect §6.3.",
            stacklevel=2,
        )

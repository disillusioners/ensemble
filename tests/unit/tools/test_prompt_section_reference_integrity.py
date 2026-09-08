"""Mechanical-integrity tests for agent prompt-section references (convention v2).

Encoded detectors (per audit 2026-09-08):

1. **Parity / empty-capture / glue detectors** — sweep the agents/ prompt
   surfaces for the same corruption patterns the council REJECTED in
   commit c400bb87 / b2ccebce / 2e02e724 / 86edd75e (greedy regex sweep).
   These are pure static scans — no DB, no LLM calls.

2. **Closure grep with bare ``agents/`` prefix pattern** — verify zero
   in-scope cross-reference violations remain. Bare ``agents/`` prefix
   tokens are violations ONLY when they function as cross-references to
   prompt sections (per §12.5 #0 controlling exclusion interpretation);
   operational filesystem paths are excluded by design.

The tests follow the existing per-test-file convention under tests/unit/tools/
(static, no DB). They use the actual repo tree (REPO_ROOT discovery via
``__file__``) and run against the current checkout, so they double as a
regression guard against future re-introduction of the defect class.

Background: the v0.12.2 prompt-section-references feature (commits
c400bb87 / b2ccebce / 2e02e724 / 86edd75e + repair 2f7cda83 etc.) had a
corruption class where the greedy substitution produced:

- Empty captures: ``(See )`` / ``(See .`` / ``(See,``
- Glued words: ``whichload_skill`` / ``matchedload_skill`` /
  ``andcore.md`` / ``eitherAllowed``
- Bare ``agents/`` prefix as cross-reference (e.g.
  ``agents/See leader's ...``)
- Lost backticks / quotes / parens (e.g. ``/go test ./...`` /
  ``( Cardinal #3)`` / ``decision contract .``)

The council REJECTED the sweep and mandated this mechanical-integrity
gate so the defect class cannot silently re-emerge.
"""
from __future__ import annotations

import re
from pathlib import Path
from unicodedata import normalize

import pytest


# Repo root — three parents up from tests/unit/tools/<this>.py
REPO_ROOT = Path(__file__).resolve().parents[3]
AGENTS_DIR = REPO_ROOT / "agents"


# ---------------------------------------------------------------------------
# Scope definition (mirrors audit §1 + §12.5)
# ---------------------------------------------------------------------------

# Agent-prompt surface (subject to v2 closure grep)
PROMPT_SURFACE_GLOBS = [
    "*/soul.md",
    "*/rule.md",
    "*/tools_note.md",
    "*/workflow.md",
    "*/memory.md",
    "*/skills-template/*.md",
]

# Per-instance scaffolding (loaded but NOT subject to closure grep)
SCAFFOLDING_FILES = {
    "*/growth.md",
    "*/builder-prompt.md",
    "_prompt_system/knowledge.md",
    "_prompt_system/project-experience.md",
    "_prompt_system/critical-notes.md",
}

# Non-surface: tooling / metadata
NON_SURFACE_PATTERNS = (
    "meta.json",
    "skill-set.yaml",
)


def _is_in_scope(rel_path: str) -> bool:
    """Return True if ``rel_path`` is an agent-prompt surface (v2 closure grep applies)."""
    # 2e (R3 2026-09-08) DEAD-SCOPE-BRANCH FIX: the prior prefix was
    # ``_prompt_system/innate-skills/`` — but every rel_path iterated here begins
    # with ``agents/``, so the branch never fired and the 8 innate-skill files
    # were silently OUTSIDE the sweep. The ``agents/`` prefix is now part of the
    # match, and test_innate_skill_files_in_scope guards the branch's liveness.
    if rel_path.startswith("agents/_prompt_system/innate-skills/"):
        return True
    parts = rel_path.split("/")
    if len(parts) < 3:
        # Need at least agents/<agent>/<file>
        return False
    if parts[0] != "agents":
        return False
    fname = parts[-1]
    agent_dir = parts[1]  # e.g., "_baby_template", "_mother", "coder"
    # Internal scaffolding agents (per audit §12.5 #6): not in meta.json registry.
    if agent_dir.startswith("_") and agent_dir not in {"_prompt_system", "_mother"}:
        # _baby_template, _inner_soul, etc.
        # _mother IS in scope (it builds baby prompts — its tools_note.md is
        # part of the agent-prompt surface).
        return False
    if fname in {"growth.md", "builder-prompt.md"}:
        return False
    for ng in NON_SURFACE_PATTERNS:
        if fname == ng:
            return False
    for glob in PROMPT_SURFACE_GLOBS:
        # glob like "*/soul.md" — agent_dir matches "*"
        head = glob.split("/")[0]  # "*"
        tail = glob.split("/", 1)[1]  # "soul.md"
        if head == "*" and fname == tail:
            return True
    # Also include skills-template subdirectory files
    if "skills-template" in parts:
        return True
    return False


def _is_operational_self_reference(path: Path, token: str) -> bool:
    """Return True if ``token`` is a self-reference of the file ``path``.

    Strategy-skill files commonly mention their own filename when documenting
    what they are. These self-references are operational (the file describing
    itself), not cross-references to prompt sections.
    """
    if not token.endswith(".md"):
        return False
    fname = path.name
    return token == fname


def _iter_prompt_files() -> list[Path]:
    """Return all in-scope prompt files under agents/."""
    if not AGENTS_DIR.exists():
        return []
    out = []
    for path in AGENTS_DIR.rglob("*.md"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if _is_in_scope(rel):
            out.append(path)
    return out


# 2g (R3): NFKC normalization — cheap unicode lookalike fold (fullwidth parens,
# NBSP, compatibility forms) so detectors cannot be evaded by visually-identical
# codepoints. Applied to every prompt text before matching.
def _read_prompt_text(path: Path) -> str:
    """Return the file's text, NFKC-normalized (2g)."""
    return normalize("NFKC", path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Corruption-pattern detectors (mirror audit §12 + repair commit series)
# ---------------------------------------------------------------------------

# Empty captures like ``(See )``, ``(See .``, ``(See,``
# (2c, R3 2026-09-08): re.IGNORECASE added — the sweep also produced lowercase
# headwords (``(see )``), which the case-sensitive form let through.
EMPTY_CAPTURE_RE = re.compile(r"\(See\s*[^A-Za-z]*\)", re.IGNORECASE)

# Known glued words from the council list (and the general camelcase junction).
KNOWN_GLUED_WORDS = [
    re.compile(r"\bwhichload_skill\b"),
    re.compile(r"\beitherAllowed\b"),
    re.compile(r"\bandcore\.md\b"),
    re.compile(r"\bmatchedload_skill\b"),
]

# Broken-prose pattern from v2-sweep over-conversion.
#
# R3 GENERIC WIDENING (2026-09-08, final iteration — replaces verb enumeration):
# The R2 form enumerated splice headwords (`lives in` / `in` / `from`) and the R2
# review proved enumeration LEAKS — 10 survivor sites used splice-words outside the
# enumerated set (`handle See X`, `is See X`, `Apply See X`, `template See X`,
# `and See X`, `optimizations See X`, `approval See X`, bold `**See X`). The
# headword class is therefore GENERIC: any `\w+` spliced directly before an
# imperative `See <Capital>` is the defect, regardless of part of speech.
#
#     r"\b\w+[ \t`*]+See\b(?![\w-])(?=[ \t]+[A-Z])"
#
# - Separators `[ \t`*]+` SUBSUME the R2 hardening class (`[\s`*]+` restricted to
#   the same line): plain (`word See X`), backtick-mechanically-evading
#   (`word `See X``), and bold-wrapped (`word **See X**) forms all match.
# - Trailing negative lookahead `(?![\w-])` keeps the hyphen-compound guard:
#   `See-layer`, `See_X`, `Seesomething` do NOT match.
# - The lookahead `(?=[ \t]+[A-Z])` requires the `See` to govern a Capitalized
#   section name (the sweep's output shape).
# - Same-line spacer ONLY: wrap-spanning splices (`See ⏎ > See` class, where the
#   splice crosses a line break or blockquote marker) are caught by the SEPARATE
#   collapsed pass (2b) — test_no_broken_prose_see_prefix_wrap_spanning — so line
#   attribution stays reportable on the primary pass.
#
# Legit shapes that must NOT match (each verified against the full 205-file corpus
# at 27f023ea; pinned as negative controls in test_see_splice_negative_controls):
#   - sentence-initial `See X` (preceded by `.` / `!` / `?` / line start) — no
#     word directly before `See`
#   - parenthetical `(See X)` — `(` is not a word char
#   - after a `>` blockquote marker — not a word char
#   - lowercase `see X` — the pattern requires the capitalized imperative `See`
#   - hyphen/underscore compounds (`See-layer`, `See_X`) — lookahead guard
BROKEN_PROSE_RE = re.compile(r"\b\w+[ \t`*]+See\b(?![\w-])(?=[ \t]+[A-Z])")

# 2b (R3): pre-collapsed newline→space SECOND PASS for the wrap-spanning class
# (`See ⏎ > See` — a splice whose headword and `See` are separated by a line
# break, optionally with `> ` blockquote prefixes between). The same generic
# pattern is applied to normalized text (blockquote prefixes stripped per line,
# then every whitespace run collapsed to one space) so wrapped splices are
# visible to the detector. Kept as a SEPARATE pass — the collapsed text has no
# meaningful line numbers, so the primary pass stays line-attributable.
_BLOCKQUOTE_PREFIX_RE = re.compile(r"^[ \t]*>[ \t]?", re.MULTILINE)
_WHITESPACE_RUN_RE = re.compile(r"\s+")


def _collapse_wrap_whitespace(text: str) -> str:
    """Strip ``> `` blockquote prefixes per line, then collapse whitespace runs (2b)."""
    return _WHITESPACE_RUN_RE.sub(" ", _BLOCKQUOTE_PREFIX_RE.sub("", text))
# A general lowercase-then-Capital pattern (e.g., ``<lowercase-word><Capital-word>``
# glued at a word boundary, like ``eitherAllowed``) is NOT included here because
# it cannot reliably distinguish the corruption class from legitimate CamelCase
# identifiers (``classDef`` in Mermaid diagrams, ``loadSkill`` tool names,
# ``selectedProject``, etc.). Future corruption of this shape should be added
# to KNOWN_GLUED_WORDS as it is discovered — the regex tuning below was
# attempted and rejected as too noisy.


# ---------------------------------------------------------------------------
# Closure patterns (§12.5 — variant-tolerant)
# ---------------------------------------------------------------------------

# Bare .md filename tokens (the canonical §12.5 pattern). Matches both
# standard filenames (rule.md, workflow.md) and dated memory files
# (2026-04-23-architecture-report.md) — the latter are operational own-
# memory paths (per §12.5 #0 controlling exclusion).
BARE_MD_RE = re.compile(r"\b(?:\d{4}-\d{2}-\d{2}-[\w-]+\.md|[a-zA-Z][\w./-]*\.md)\b")

# Cross-agent path tokens like ``giter/workflow.md``.
# Excludes `.agents/<agent>/memory.md` (operational own-memory path per §12.5 #0).
CROSS_AGENT_PATH_RE = re.compile(r"(?<!agents/)\b[a-zA-Z][\w-]*/(rule|workflow|soul|tools_note|builder-prompt|growth)\.md\b")

# Bare ``agents/`` prefix tokens (added in the 2026-09-08 repair iteration).
# This pattern catches the corruption class ``agents/See <agent>'s ...`` where
# the sweep accidentally produced a literal ``agents/`` followed by a See form.
# 2d (R3 2026-09-08): the lookahead was widened so SINGLE-CHAR SEPARATORS are
# caught — the prior form (`(?=[A-Z]|see\b|See\b)`) required `See` to start at
# the character right after `agents/`, missing the backtick/bold-wrapped
# corruption shapes (``agents/`See ...``, ``agents/**See ...**``). The zero-width
# separator run `[\s`*]*` skips any mix of whitespace/backtick/asterisk before
# the capital or paren; lowercase path forms (`agents/ari/workflow.md`,
# `agents/_prompt_system/`) still do not match.
BARE_AGENTS_PREFIX_RE = re.compile(r"\bagents/[\s`*]*(?=[A-Z(])")

# §12.5 #0 controlling exclusion: operational filesystem paths are NOT cross-references.
# These patterns describe where a bare ``agents/`` hit is OPERATIONAL and stays exempt.
OPERATIONAL_AGENTS_PATHS_RE = re.compile(
    r"(?:"
    r"\.agents/shared/planning/"           # planning-dir write-scope
    r"|\.agents/shared/conventions\.md"   # convention docs
    r"|\.agents/tester/rules/"             # tester's own rules dir
    r"|\.agents/tester/packs/"             # tester's packs dir
    r"|\.agents/tester/RESULTS/"           # tester's RESULTS dir
    r"|\.agents/<agent>/memory\.md"        # own memory file reference (path)
    r"|agents/_prompt_system/"             # system hooks
    r"|agents/_mother/"                    # mother scaffolding
    r"|agents/_baby_template/"             # baby template scaffolding
    r"|agents/_inner_soul/"                # inner-soul scaffolding
    r")"
)


def _is_operational_agents_path(line: str) -> bool:
    """Return True if a ``agents/``-prefix hit is operational (§12.5 #0 exclusion)."""
    return bool(OPERATIONAL_AGENTS_PATHS_RE.search(line))


# ---------------------------------------------------------------------------
# 1. Corruption-pattern tests (parity / empty-capture / glue)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", _iter_prompt_files(), ids=lambda p: p.name)
def test_no_empty_capture_in_see_form(path: Path) -> None:
    """No ``(See )`` / ``(See .`` / ``(See,`` empty captures in prompt text.

    Background: the council REJECTED the c400bb87 sweep because it produced
    (See ) empty parens in many sites (FIX CLASS B in the audit). This test
    enforces zero empty-capture violations across all in-scope prompt surfaces.
    """
    text = _read_prompt_text(path)
    matches = list(EMPTY_CAPTURE_RE.finditer(text))
    assert not matches, (
        f"{path.relative_to(REPO_ROOT)} contains {len(matches)} empty-capture violation(s): "
        + ", ".join(m.group(0) for m in matches[:5])
    )


@pytest.mark.parametrize("path", _iter_prompt_files(), ids=lambda p: p.name)
def test_no_known_glued_words(path: Path) -> None:
    """No known glued-word artifacts (``whichload_skill`` etc.).

    Background: the council flagged 4 glued words (FIX CLASS C) — these are
    products of the greedy regex sweep gluing adjacent lowercase + Capitalized
    tokens (e.g., ``which`` + ``load_skill`` -> ``whichload_skill``).

    This test covers the 4 documented patterns explicitly. A general
    CamelCase-junction detector was considered but rejected because it
    cannot reliably distinguish the corruption class (``eitherAllowed``)
    from legitimate CamelCase identifiers (``classDef`` in Mermaid
    diagrams, ``loadSkill`` tool names, etc.). Future corruption of
    this shape should be added to KNOWN_GLUED_WORDS as it is discovered.
    """
    text = _read_prompt_text(path)
    hits = []
    for pat in KNOWN_GLUED_WORDS:
        for m in pat.finditer(text):
            hits.append(m.group(0))
    assert not hits, (
        f"{path.relative_to(REPO_ROOT)} contains {len(hits)} glued-word violation(s): "
        + ", ".join(hits[:5])
    )


# ---------------------------------------------------------------------------
# Broken-prose detector (added 2026-09-08 cleanup pass)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", _iter_prompt_files(), ids=lambda p: p.name)
def test_no_broken_prose_see_prefix(path: Path) -> None:
    r"""No word-spliced imperative `See` (generic splice heuristic, R3 widened).

    Background: the v2 sweep over-converted prepositions (`lives in`, `in`, `from`) before
    file references into broken prose like "lives in See X" / "from See X". The natural
    English form replaces `in See X` with `in **X**` (bold or backticked section name
    without the See headword) — the preposition stays but no longer governs `See`.

    R3 GENERIC WIDENING (final iteration, 2026-09-08): the R2 pattern enumerated splice
    headwords and the R2 review proved enumeration leaks (10 survivor sites used
    `handle See X` / `is See X` / `Apply See X` / `template See X` / `and See X` /
    `optimizations See X` / `approval See X` / bold `**See X`). BROKEN_PROSE_RE is now
    GENERIC — any `\w+` + separators + imperative `See` + Capitalized section name:

        r"\b\w+[ \t`*]+See\b(?![\w-])(?=[ \t]+[A-Z])"

    Evasion-tolerance is retained and WIDENED: the separator class (whitespace,
    backtick, asterisk — same line) catches the plain form (`word See X`),
    backtick-wrapped variants (`word `See X``), and bold-wrapped variants
    (`word **See X**`). **Hyphen-compound guard:** the trailing negative lookahead
    `(?![\w-])` ensures compound words like `See-layer`, `See_X`, `Seesomething` (no
    whitespace separator after `See`) do NOT match — only genuine space/boundary-
    separated `See` headwords trigger the violation.

    Legit shapes stay un-flagged (pinned in test_see_splice_negative_controls):
    sentence-initial `See X`, parenthetical `(See X)`, after a `>` blockquote marker,
    lowercase `see X`, and hyphen/underscore compounds.

    Wrap-spanning splices (`See ⏎ > See` class) are caught by the SEPARATE collapsed
    pass — test_no_broken_prose_see_prefix_wrap_spanning (2b) — keeping this pass
    line-attributable.

    Per audit (restore iteration, 2026-09-08), the G6 fix performs genuine natural-prose
    rewrites (drop the preposition-or-See headword pair; keep the section reference as
    `**Section Name**` or a parenthetical), so the prose reads naturally AND the detector
    remains green because the actual grammar was repaired, not because the regex was
    dodged. The detector's evasion-tolerance is the safety net against re-introduction
    via future sweeps. Test file: `agents/` only — 44 sites / 26 files pre-fix (audited at 65659cd5).
    """
    text = _read_prompt_text(path)
    matches = list(BROKEN_PROSE_RE.finditer(text))
    assert not matches, (
        f"{path.relative_to(REPO_ROOT)} contains {len(matches)} broken-prose violation(s): "
        + ", ".join(m.group(0) for m in matches[:5])
    )


@pytest.mark.parametrize("path", _iter_prompt_files(), ids=lambda p: p.name)
def test_no_broken_prose_see_prefix_wrap_spanning(path: Path) -> None:
    r"""No WRAP-SPANNING word-spliced `See` (2b pre-collapsed second pass).

    The wrap-spanning class is a splice whose headword and `See` are separated by a
    line break — with or without `> ` blockquote markers between:

        Holding the turn open blocks report delivery (deadlocks the run). See
        > See Why END TURN After Dispatch.

    The primary pass (test_no_broken_prose_see_prefix) uses a same-line separator
    class, so it cannot see across the break. This pass normalizes the text first
    (strip `> ` prefixes per line, collapse every whitespace run to one space) and
    applies the SAME generic pattern — catching the class at the cost of line
    attribution (collapsed text has no meaningful line numbers, hence a separate
    check; failures report the collapsed context around the match instead).
    """
    text = _read_prompt_text(path)
    collapsed = _collapse_wrap_whitespace(text)
    matches = list(BROKEN_PROSE_RE.finditer(collapsed))
    assert not matches, (
        f"{path.relative_to(REPO_ROOT)} contains {len(matches)} wrap-spanning broken-prose "
        "violation(s) (collapsed context — line numbers not attributable): "
        + " | ".join(
            "…" + collapsed[max(0, m.start() - 50):m.end() + 50] + "…"
            for m in matches[:5]
        )
    )


# ---------------------------------------------------------------------------
# 2. Closure-grep tests (variant-tolerant, with §12.5 #0 operational exclusion)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", _iter_prompt_files(), ids=lambda p: p.name)
def test_no_bare_md_filename_tokens_in_prompts(path: Path) -> None:
    """No bare ``*.md`` filename tokens in prompt text.

    Per §3 convention v2, any filename or path token is forbidden in prompt
    text (excluding §12.5 #0 operational filesystem paths like
    ``conventions.md`` / ``PACKS.md`` / ``QUARANTINE.md`` / own memory path).
    """
    text = _read_prompt_text(path)
    # Allowed operational filesystem paths (per §12.5 #0 controlling exclusion).
    # These are operational references (convention docs, planning paths, etc.),
    # NOT cross-references to prompt sections.
    allowed_operational = {
        "PACKS.md",
        "QUARANTINE.md",
        "conventions.md",
        "context.md",
        "ensure.md",
        "MOCK_TESTS.md",
        # Per-instance scaffolding references (own growth.md / builder-prompt.md
        # / etc. — these are referenced as the agent's OWN scaffolding files,
        # not cross-references to prompt sections).
        "growth.md",
        "builder-prompt.md",
        "meta.json",
        "core.md",
        "active.md",
        # Operational planner write targets (per §12.5 #0 controlling exclusion:
        # "operational project-infra paths (.agents/shared/planning/, ..., phase files)").
        # These are worker-written plan artifacts the planner orchestrates and
        # cites — operational filesystem references, not cross-references.
        "plan-overview.md",
        "phase1-plan.md",
        "phase2-plan.md",
        "phaseN-plan.md",
        "requirements.md",
        "technical-analysis.md",
        "roadmap.md",
        # Operational architect write targets.
        "architecture-recommendation.md",
        "approach-comparison.md",
        "architecture-decision-record.md",
        # Operational pandoc example argument (doc-writer uses pandoc input.md -o output.{ext}):
        # `input.md` is a literal command-line argument in pandoc examples, NOT a cross-reference
        # to a prompt section. Whitelisting is contractually scoped: a use of `input.md` as a
        # prose cross-reference (e.g., "See input.md") is still a violation — only the pandoc-
        # command-argument use is exempt (per the W4 use-blindness contract).
        "input.md",
        # Operational versioned forms of operational write targets (architect appends a version
        # suffix when a file with the same name already exists; the versioned form is still an
        # operational filesystem reference, not a prompt-section cross-reference).
        # W4 contract: whitelisting the versioned form does NOT unban a use of it as a prose
        # cross-reference (e.g., "See architecture-recommendation-v2.md" remains a violation).
        "architecture-recommendation-v2.md",
        # Operational SMALL-scope single-file plan (planner uses `plan.md` for SMALL-scope work).
        # W4 contract applies: this whitelist entry is for the operational filename use only.
        "plan.md",
        # Operational approver tracking files (approver uses `{plan-slug}-tracking.md` for each
        # plan's tracking; the suffix `tracking.md` is operational filesystem reference, not
        # prompt-section cross-reference). W4 contract applies.
        "tracking.md",
        # Operational tester convention docs (W3, 2026-09-08 cleanup):
        # - `COVERAGE.md` — tester's coverage tracking file
        # - `UPPERCASE.md` — naming convention reference (UPPERCASE.md for standard docs)
        # - `API_TESTING.md` (and similar descriptive-name tokens) — example of descriptive naming
        # These are operational filesystem references, not prompt-section cross-references.
        # W4 contract applies.
        "COVERAGE.md",
        "UPPERCASE.md",
        "API_TESTING.md",
        # Operational same-agent workflow.md cross-reference (tester references its own
        # workflow.md for cross-section pointers like "see Planning Phase in workflow.md").
        # Same-agent cross-refs are permitted by §12.5 #0 — operational filesystem shape.
        # W4 contract applies.
        "workflow.md",
        # Operational README.md (tester's own README in .agents/tester/ — file inventory).
        "README.md",
    }
    # W4 use-awareness — HONEST STATE (2026-09-08 restore, second pass):
    #
    # ARCHITECTURE IS CURRENTLY USE-BLIND. Whitelisting a token exempts its bare-filename
    # appearance *regardless of surrounding prose*. Empirically, prose-prelude + whitelisted
    # combos like "See input.md", "Per tracking.md", "in PACKS.md", "from QUARANTINE.md"
    # all pass this test (the test does not parse prose intent — only the bare token).
    #
    # The earlier comment claimed "See X.md / per X.md remains a violation" — that was a
    # FALSE contract claim, not enforced by the test. The cross-reference shape (prose-prelude
    # + bare .md filename) is NOT actually caught by this test; an LLM could reintroduce
    # such prose and the test would still pass.
    #
    # Why the tightening is deferred (W4 follow-up, blast-radius blocker):
    # Implementing the tightening — flagging <See|Per|per|in|In|from|From|via|according to>
    # + whitelisted-token combos as violations — catches 27 pre-existing LEGITIMATE
    # operational uses in tester/blueprinter prompts (e.g., "tests in QUARANTINE.md are
    # skipped", "registered in PACKS.md", "per MOCK_TESTS.md"). All 27 are operational
    # filesystem references to tester convention docs at `.agents/tester/` (per audit
    # §12.5 #0 controlling exclusion), NOT cross-references to prompt sections.
    # Adding the tightening without per-occurrence narrowing would fail the suite
    # 27 times — which exceeds the ~10-line adjudication cap mentioned in the W4 spec.
    #
    # Deferred until next iteration: per-occurrence narrowing (allow specific
    # <(prelude, token)> pairs as legitimate operational uses in tester/blueprinter
    # contexts; everything else is a violation).
    #
    # Cross-reference detection is currently enforced by:
    #   (a) BARE_MD_RE matching any .md token in non-whitelisted, non-path, non-self-ref form
    #   (b) OPERATIONAL_PATH_RE catching path-shaped .md tokens (per §12.5 #0)
    #   (c) BARE_AGENTS_PREFIX_RE catching the corruption class `agents/See <agent>'s ...`
    # The (a)+(b)+(c) tests are use-blind by design; prose-intent is not parsed.
    # Match memory file references (operational own-memory path) -- the agent's
    # own memory file references ARE operational filesystem paths, not prompt-
    # section cross-references (per §12.5 #0 controlling exclusion).
    BARE_MD_RE = re.compile(r"\b(?:\d{4}-\d{2}-\d{2}-[\w-]+\.md|[a-zA-Z][\w./-]*\.md)\b")
    # Operational workspace-path shapes (per §12.5 #0). These are filesystem
    # references, NOT cross-references to prompt sections.
    OPERATIONAL_PATH_RE = re.compile(
        r"(?:"
        r"\.agents/"                 # shared planning/convention dir (with leading dot)
        r"|agents/shared/"           # shared conventions/planning/context dir (no leading dot)
        # Latent `agents/<any-agent>/` hole (W1, 2026-09-08 cleanup):
        # This pattern matches any agent's directory, including cross-agent references
        # like `agents/approver/active.md` referenced from a non-approver file. The
        # narrowing would require either (a) per-agent enumeration (too brittle),
        # (b) self-reference detection (compare the agent in the path to the agent
        # owning the referencing file — possible but requires the test to know the
        # owning agent, which is currently per-file), or (c) requiring a specific
        # filename suffix like /memory.md, /active.md, /rule.md, etc. We accept the
        # latent hole: cross-agent refs like `agents/approver/active.md` are treated
        # as operational filesystem references, even though they MIGHT be a cross-
        # reference. The audit doc's §12.5 #0 enumeration covers the common cases.
        # TODO (post-cleanup): implement self-reference detection for tighter closure.
        r"|agents/[a-zA-Z0-9_\[\]-]+/"  # any agent's own subdir (notes, rules, etc.)
        r"|daemon/"                  # daemon code paths (operational tool/runtime targets)
        r"|agents/_prompt_system/"   # system hooks
        r"|agents/_mother/"          # mother scaffolding
        r"|agents/_baby_template/"   # baby template scaffolding
        r"|agents/_inner_soul/"      # inner-soul scaffolding
        r"|projects/"                # user-project example paths in docs
        r"|path/to/"                 # example paths in docs (path/to/plan.md etc.)
        r")"
    )
    # Fenced code blocks (` ```...``` `) contain code/tool-call examples, not
    # prose cross-references. Tokens inside them are operational.
    #
    # W2 (2026-09-08 cleanup pass, restored 2026-09-08) — odd-fence guard:
    # A file with an ODD number of ``` fences is malformed (one fence is unmatched).
    # FENCED_CODE_BLOCKS_RE below uses non-greedy `.*?` matching, which works for
    # balanced fence pairs but silently misbehaves when fences are unbalanced (the regex
    # matches the first balanced pair and the un-matched fence content slips through as
    # "non-fenced" — leading to false negatives in this test). The `test_odd_fence_count_fails`
    # test (below, in section 4) is a dedicated per-file runtime guard: it counts
    # ``` fences and fails LOUD when the count is odd, naming the file. Without this guard,
    # an unbalanced fence would silently corrupt the bare-md sweep.
    FENCED_CODE_BLOCKS_RE = re.compile(r"```.*?```", re.DOTALL)
    # W2 odd-fence sentinel (count ``` occurrences, not regex-paired). Cheap; runs first.
    # Harmonized with _FENCE_LINE_RE (both allow leading whitespace) — same regex form; distinct names retained for
    # the reader's mental model (counting vs. locating).
    _FENCE_COUNT_RE = re.compile(r"^\s*```", re.MULTILINE)
    hits = []
    for m in BARE_MD_RE.finditer(text):
        tok = m.group(0)
        # Skip tokens inside fenced code blocks (code/tool-call examples)
        block_start = 0
        in_fenced = False
        for block_m in FENCED_CODE_BLOCKS_RE.finditer(text):
            if block_m.start() <= m.start() < block_m.end():
                in_fenced = True
                break
        if in_fenced:
            continue
        if tok in allowed_operational:
            continue
        # Date-prefixed memory files are operational own-memory paths
        if re.match(r"^\d{4}-\d{2}-\d{2}-[\w-]+\.md$", tok):
            continue
        # Operational workspace-path shapes (per §12.5 #0): .agents/...,
        # daemon/..., and internal scaffolding agents. These are filesystem
        # references where the path token is part of an operational target.
        if "/" in tok and OPERATIONAL_PATH_RE.search(tok):
            continue
        # Exclude self-references (the file mentioning its own filename).
        if _is_operational_self_reference(path, tok):
            continue
        hits.append(tok)
    assert not hits, (
        f"{path.relative_to(REPO_ROOT)} contains {len(hits)} bare-md filename token(s): "
        + ", ".join(hits[:5])
    )


def test_no_cross_agent_path_tokens_in_prompts() -> None:
    """No cross-agent path tokens like ``giter/workflow.md`` in prompt text."""
    violations = []
    for path in _iter_prompt_files():
        text = _read_prompt_text(path)
        for m in CROSS_AGENT_PATH_RE.finditer(text):
            violations.append((path, m.group(0)))
    assert not violations, (
        "cross-agent path tokens: "
        + ", ".join(f"{p.relative_to(REPO_ROOT)}:{tok}" for p, tok in violations[:5])
    )


def test_no_bare_agents_prefix_as_cross_reference() -> None:
    """No bare ``agents/`` prefix tokens functioning as cross-references.

    Per audit §12.5 (2026-09-08 repair), the closure grep must include the
    bare-``agents/``-prefix pattern to catch the corruption class
    ``agents/See <agent>'s ...`` (e.g. leader/workflow.md:662). A hit is a
    violation ONLY when it functions as a cross-reference to a prompt
    section; operational filesystem paths (where ``agents/`` is a literal
    path component) are excluded by §12.5 #0.
    """
    violations: list[tuple[Path, str]] = []
    for path in _iter_prompt_files():
        text = _read_prompt_text(path)
        for m in BARE_AGENTS_PREFIX_RE.finditer(text):
            # Get the line containing the hit
            start = text.rfind("\n", 0, m.start()) + 1
            end = text.find("\n", m.end())
            if end == -1:
                end = len(text)
            line = text[start:end]
            if _is_operational_agents_path(line):
                continue
            violations.append((path, line.strip()[:80]))
    assert not violations, (
        "bare-agents/ prefix cross-reference violations: "
        + ", ".join(f"{p.relative_to(REPO_ROOT)}:{line}" for p, line in violations[:5])
    )


# ---------------------------------------------------------------------------
# 4. W2 fence guard (per-file runtime odd-count check)
# ---------------------------------------------------------------------------


# Match lines starting with ``` (fence opener), excluding ```text inline uses.
# Distinct from FENCED_CODE_BLOCKS_RE above (which uses non-greedy pair matching).
_FENCE_LINE_RE = re.compile(r"^\s*```", re.MULTILINE)


def _count_fences(text: str) -> int:
    """Return the count of ```-fence LINES in ``text`` (NOT paired)."""
    return sum(1 for _ in _FENCE_LINE_RE.finditer(text))


@pytest.mark.parametrize("path", _iter_prompt_files(), ids=lambda p: p.name)
def test_odd_fence_count_fails(path: Path) -> None:
    """All prompt files must have an EVEN number of ``` fences (paired).

    W2 (2026-09-08 cleanup pass, restored 2026-09-08). Odd fence count means
    one fence is unmatched → FENCED_CODE_BLOCKS_RE (non-greedy `.*?` matching)
    silently misbehaves: it matches the FIRST balanced pair, and the orphan
    fence plus everything after it is NOT recognized as fenced (so any
    bare-md tokens there would be checked as prose and may spuriously fail
    OR PASS when they shouldn't). Without this guard, an unbalanced fence
    corrupts the bare-md sweep silently.

    The guard counts ```-opening lines per file and fails LOUD when the
    count is odd, naming the file. This is cheap (one regex per file)
    and runs early enough to diagnose the malformed file before other
    detectors report misleading results.

    The guard also reports the offenders with line numbers so the
    fix-up is mechanical: the missing fence is at the indicated line.
    """
    text = _read_prompt_text(path)
    count = _count_fences(text)
    assert count % 2 == 0, (
        f"{path.relative_to(REPO_ROOT)} has {count} ``` fences (odd). "
        "Fence count must be even (paired open/close). "
        "FENCE_LINE_RE locates each fence; the unmatched fence is at one of these lines:"
        + ", ".join(
            f"L{i + 1}"
            for i, line in enumerate(text.splitlines())
            if _FENCE_LINE_RE.match(line)
        )
    )


# ---------------------------------------------------------------------------
# 3. Sanity: at least one file in scope (otherwise the grep is vacuous)
# ---------------------------------------------------------------------------


def test_scope_is_nonempty() -> None:
    files = _iter_prompt_files()
    assert len(files) > 10, (
        f"prompt-surface scope returned only {len(files)} files — "
        "scope detection may be broken (regression guard)"
    )


# ---------------------------------------------------------------------------
# 5. R3 scope-liveness + positive/negative controls (2026-09-08 final iteration)
# ---------------------------------------------------------------------------


def test_innate_skill_files_in_scope() -> None:
    """Scope-liveness guard (2e): innate-skill files MUST be swept.

    Regression guard for the dead-scope-branch defect: `_is_in_scope` tested the
    prefix ``_prompt_system/innate-skills/`` against rel paths that all begin with
    ``agents/``, so the branch never fired and every innate skill.md was silently
    OUTSIDE the sweep (never scanned by any detector). The prefix is fixed; this
    test fails if the innate files are ever collected as zero again.
    """
    innate = [
        p
        for p in _iter_prompt_files()
        if "_prompt_system/innate-skills/" in p.relative_to(REPO_ROOT).as_posix()
    ]
    assert innate, (
        "zero innate-skill files in sweep scope — the dead-scope-branch defect "
        "(2e) has regressed; the 8 innate skill.md files are unswept"
    )


# 2h (R3): positive controls — one fixture per R2 survivor shape (the 10 review
# sites, in file order) plus the wrap-spanning class, the bold-See form, and the
# backtick form. Each asserts the WIDENED gate matches (same-line pass and/or
# collapsed wrap pass). RED PROOF (R2 pattern vs these fixtures): the R2 form
# `r"\b(?:lives\s+in|in|from)[\s`*]+See\b(?![-\w])"` matched ONLY the backtick
# fixture — 11 of 12 shapes escaped it (see the R3 report for the throwaway run).
R2_SURVIVOR_FIXTURES = [
    # (fixture_id, text, same_line_matches, collapsed_matches)
    ("site1-verb-splice-ari",
     "- cancelled / dead_letter → handle See Handle Failures Gracefully",
     True, True),
    ("site2-noun-splice-ari",
     "5. Verify result quality, translate to user, handle failure See Handle Failures Gracefully.",
     True, True),
    ("site3-copula-splice-leader",
     "**Note:** This decision tree is a FALLBACK for quick decisions. "
     "Primary routing is See Implementation Workflow (domain routing).",
     True, True),
    ("site4-imperative-splice-worker",
     "- Apply See Handle Skill System Errors Gracefully:",
     True, True),
    ("site5-compound-noun-splice-devops",
     "1. Explicit confirmation (or TrueAuto self-approval See TrueAuto Self-Approval Protocol)",
     True, True),
    ("site6-bold-see-tester",
     "the dispatch rules live canonically in the auto-loaded "
     "**See Worker Skill Selection (Dispatcher Contract)**.",
     True, True),
    ("site7-wrap-spanning-tidier-tools-note",
     "Holding the turn open blocks report delivery (deadlocks the run). See\n"
     "> See Why END TURN After Dispatch.",
     False, True),  # wrap-spanning ONLY — the same-line pass cannot see across the break
    ("site8-quote-wrap-plus-and-splice-tidier-template",
     "dispatcher responsibility** (see\n"
     "> See tidier[v2]'s 6. Aggregate & Verify (DISPATCHER STEP)` and See Aggregation Strategy).",
     True, True),  # same-line via the `and See` splice; the `see ⏎ > See` duplication via the wrap pass
    ("site9-flow-noun-splice-project-manager",
     "Default is Terse; switch to Full or a named flow template See Cardinal #3.",
     True, True),
    ("site10-verb-splice-innate-test-pack",
     "When timeout occurs, apply TTQA optimizations See TTQA & Test Architecture Maintenance.",
     True, True),
    ("site11-conjunction-splice-tester-soul",
     "Reuse the same worker with a fresh `load_skill=\"quick-fix\"` if context is relevant; "
     "otherwise spawn fresh. See Quick Fix (under Must) for criteria and See Quick Fix Process for examples.",
     True, True),
    ("hardening-backtick-form",
     "the canonical copy lives in `See Handle Failures Gracefully` for recovery",
     True, True),
]


@pytest.mark.parametrize("fixture_id,text,same_line,collapsed", R2_SURVIVOR_FIXTURES, ids=lambda v: v if isinstance(v, str) else None)
def test_r2_survivor_positive_controls(fixture_id: str, text: str, same_line: bool, collapsed: bool) -> None:
    """Each R2 survivor shape MUST be caught by the widened gate (2h).

    Red→green contract: against the R2 pattern, 11 of these 12 fixtures produced
    ZERO matches (only the backtick form was caught); against the R3 widened gate,
    every fixture matches on at least one pass, with the per-shape pass attribution
    pinned by the ``same_line`` / ``collapsed`` expectations.
    """
    same_match = BROKEN_PROSE_RE.search(text)
    collapsed_match = BROKEN_PROSE_RE.search(_collapse_wrap_whitespace(text))
    assert (same_match is not None) == same_line, (
        f"[{fixture_id}] same-line pass expectation violated: "
        f"matched={same_match is not None} (group={same_match.group(0)!r} if matched)"
    )
    assert (collapsed_match is not None) == collapsed, (
        f"[{fixture_id}] collapsed wrap-pass expectation violated: "
        f"matched={collapsed_match is not None}"
    )


# 2h negative controls: the legit `See` shapes that MUST keep passing (the
# whitelist contract of the 2a widening, each documented at BROKEN_PROSE_RE).
LEGIT_SEE_SHAPE_FIXTURES = [
    ("sentence-initial", "Decision tree is a FALLBACK for quick decisions. See Implementation Workflow (domain routing)."),
    ("parenthetical", "- MEDIUM+ scope: 2-3 opencode sessions — run SEQUENTIALLY (one at a time, See Resource Constraint (STRICT))"),
    ("blockquote-marker", "> See Why END TURN After Dispatch."),
    ("lowercase-see", "If execution returned an error, see Handle Skill System Errors Gracefully for recovery."),
    ("hyphen-compound-guard", "the See-layer marker and the See_X identifier are not splices"),
]


@pytest.mark.parametrize("fixture_id,text", LEGIT_SEE_SHAPE_FIXTURES, ids=lambda v: v if isinstance(v, str) else None)
def test_see_splice_negative_controls(fixture_id: str, text: str) -> None:
    """Legit `See` shapes MUST NOT match either pass (whitelist contract of 2a)."""
    assert BROKEN_PROSE_RE.search(text) is None, f"[{fixture_id}] legit shape flagged by same-line pass"
    assert BROKEN_PROSE_RE.search(_collapse_wrap_whitespace(text)) is None, (
        f"[{fixture_id}] legit shape flagged by collapsed wrap pass"
    )
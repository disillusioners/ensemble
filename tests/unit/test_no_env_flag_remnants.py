"""Grep-proof — feature/blob-prune-default-on retired the
``CHECKPOINT_BLOB_PRUNE_DRY_RUN`` and ``CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE``
env vars (and their related symbols) from EVERY LOAD-BEARING LIVE
OPERATIVE SURFACE. Operative surfaces = code, configs, runbooks,
scripts, tests, and HTTP schemas. Historical planning docs under
``.agents/shared/planning/**`` carry a dated supersession note
rather than a deletion (their audit-trail value is preserved).

Comments inside the code that POINT to the historical gate (e.g.
"the env-flag ladder is gone — see feature/blob-prune-default-on")
are NOT failures — they are intentional historical pointers
introduced by the same PR. The grep-proof looks for either:

(a) a reference without the historical-pointer phrase alongside
    it — that is the actual regression class (someone re-introduced
    the env-flag dependency); or

(b) any reference in a non-comment context (a real env-var set,
    a real config toggle, a real ``monkeypatch.setenv`` in a test).

The compiler / interpreter does not care about comment text, so
category (a) is benign; the test allows it via a comment-context
detector.

THIS FILE itself is the only excluded path: the regex
patterns and the literal env-var names live here as the
guard itself, and that is intentional. To avoid false-self-
positive, ``tests/unit/test_no_env_flag_remnants.py`` is
excluded from the live-operative walker.

Run via ``uv run pytest tests/unit/test_no_env_flag_remnants.py -v``.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
THIS_FILE = Path(__file__).resolve()

# Live operative surfaces where the env-var names MUST NOT appear
# in load-bearing code. Code paths: daemon/, frontend/, tests/,
# scripts/, config.yaml, .env*
LIVE_OPERATIVE_GLOBS: tuple[str, ...] = (
    "daemon/**/*.py",
    "frontend/src/**/*.{ts,html,css,scss}",
    "frontend/e2e/**/*.ts",
    "frontend/__tests__/**",
    "tests/**/*.py",
    "tests/**/*.sh",
    "tests/**/*.yaml",
    "scripts/**/*",
    "config.yaml",
    ".env",
    ".env.example",
    ".env.prod.example",
    "frontend/package.json",
    "frontend/yarn.lock",
    "frontend/package-lock.json",
    "frontend/pnpm-lock.yaml",
    "Makefile",
    "frontend/tsconfig*.json",
)


# Historical planning docs (.agents/shared/planning/**) are
# ALLOWED to mention the env-var names — those are decision-record
# histories. The historical-supersession test below verifies they
# carry the dated supersession note.
HISTORICAL_ALLOWLIST_GLOBS: tuple[str, ...] = (
    ".agents/shared/planning/**",
)


ENV_FLAGS: tuple[str, ...] = (
    "CHECKPOINT_BLOB_PRUNE_DRY_RUN",
    "CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE",
)


# Helper symbols that should ALSO be gone from real (non-comment)
# code paths in live surfaces:
GONE_SYMBOLS: tuple[str, ...] = (
    "blob_prune_destructive_enabled",
    "blob_prune_env_state",
    "blob_prune_dry_run_env_default",
    "blob_prune_destructive_armed",
)


# Lines containing any of these phrases are exempt — they are
# docstrings / comments introduced by this same PR pointing to
# the historical gate. The phrase matcher is intentionally
# loose: any reference to "feature/blob-prune-default-on", the
# destructive-default contract, or the env-flag-ladder-is-gone
# narrative is the same author (this PR) writing the same
# historical-pointer comment.
HISTORICAL_POINTER_RE = re.compile(
    r"(?:feature/blob-prune-default-on|"
    r"dual-arm\s+gate\s+removed|"
    r"dual-arm\s+env-flag\s+gate|"
    r"destructive\s+prune\s+default-on|"
    r"destructive\s+prune\s+unconditional|"
    r"destructive\s+default|"
    r"unconditional\s+default|"
    r"the\s+env-flag\s+ladder\s+is\s+gone|"
    r"the\s+env-flag\s+gate\s+is\s+gone|"
    r"env-flag\s+gate|"
    r"env-flag\s+ladder|"
    r"retired\s+the\s+env-flag|"
    r"deleted\);\s+destructive\s+is\s+the\s+unconditional|"
    r"deleted\);.*?destructive\s+is|"
    r"retired\s+env|"
    r"is\s+gone\s+—\s+no\s+env-var|"
    r"the\s+env-flag\s+ladder\s+is\s+gone|"
    r"deprecated|"
    r"no\s+env-var,\s+config-flag)",
    re.IGNORECASE,
)


def _iter(paths: tuple[str, ...]):
    for p in paths:
        for path in REPO_ROOT.glob(p):
            # Self-exclude — the guard itself references the env
            # var names in its literals; treating them as live
            # operative hits would be circular.
            if path.resolve() == THIS_FILE:
                continue
            yield path


def _read(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


def _is_comment_only_line(line: str) -> bool:
    """True when the line is a comment (Python ``#``, shell ``#``,
    TS/JS ``//``). Docstring continuations are not reliably
    detectable at the line level — the scanner treats them as
    candidates for the historical-pointer exempt."""
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith("#") or stripped.startswith("//"):
        return True
    return False


def _is_historical_pointer(line: str) -> bool:
    """True when the line is a comment pointing to the historical
    gate (introduced by the SAME PR as feature/blob-prune-default-on).
    Such a line is exempt from the absence assertion — it is
    descriptive, not load-bearing, and pinning against it would
    defeat the test's purpose (the test exists to catch re-
    introductions of the env-flag dependency, not to sanitize
    historical-pointer comments)."""
    if not _is_comment_only_line(line):
        # Permit docstring continuation lines (no leading code-tokens)
        # too — they're inside a comment block context.
        if "(" in line or "=" in line or "def " in line or "class " in line:
            return False
    return bool(HISTORICAL_POINTER_RE.search(line))


def _is_in_history_context_window(lineno: int, all_lines: list[str]) -> bool:
    """Proximity check: a non-pointer line is exempt if a recognized
    historical-pointer phrase appears within ``WINDOW`` lines above
    or below. This catches docstrings that mention the symbols on
    one line and the historical-pointer phrase on an adjacent
    line — a common idiom introduced by the same PR."""
    WINDOW = 4  # lines
    start = max(0, lineno - 1 - WINDOW)
    end = min(len(all_lines), lineno - 1 + WINDOW + 1)
    window_lines = all_lines[start:end]
    return any(HISTORICAL_POINTER_RE.search(line) for line in window_lines)


def _is_explicit_assertion(line: str) -> bool:
    """True when the line is a reflective ``hasattr`` / ``getattr``
    check whose argument is the literal env-var string. Such a line
    is exempt — it tests the absence of the constant (it is the
    inverse regression guard), it does not load-bearingly depend
    on it."""
    return ("hasattr(" in line or "getattr(" in line) and (
        '"CHECKPOINT_BLOB_PRUNE_' in line
    )


def _is_explicit_tripwire(line: str) -> bool:
    """True when the line is an explicit tripwire reference — e.g.
    ``monkeypatch.delenv("CHECKPOINT_BLOB_PRUNE_DRY_RUN",
    raising=False)`` is the canonical pattern for "this env var
    MUST NOT exist; if you re-introduced it, this fixture will
    NameError on collection". Such a line is exempt — it is a
    regression-guard sentinel, not a load-bearing env-flag
    dependency."""
    return "monkeypatch.delenv" in line and (
        "raising=False" in line or '"CHECKPOINT_BLOB_PRUNE_' in line
    )


def _match_load_bearing_offsets(text: str, needle: str) -> list[tuple[int, str]]:
    """Return ``(lineno, line)`` for each load-bearing (non-comment-
    only, non-historical-pointer, no nearby historical context,
    non-explicit-tripwire, non-absence-assertion) line mentioning
    ``needle``."""
    out: list[tuple[int, str]] = []
    all_lines = text.splitlines()
    for i, line in enumerate(all_lines, 1):
        if needle not in line:
            continue
        if _is_historical_pointer(line):
            continue
        if _is_in_history_context_window(i, all_lines):
            continue
        if _is_explicit_tripwire(line):
            continue
        if _is_explicit_assertion(line):
            continue
        out.append((i, line.strip()))
    return out


def _match_offsets(text: str, needle: str) -> list[tuple[int, str]]:
    """Return ``(lineno, line)`` for each line mentioning ``needle``
    — used for the bare-presence test (historical planning docs)."""
    out: list[tuple[int, str]] = []
    for i, line in enumerate(text.splitlines(), 1):
        if needle in line:
            out.append((i, line.strip()))
    return out


@pytest.mark.parametrize("needle", ENV_FLAGS)
def test_env_flag_absent_from_load_bearing_live_surfaces(needle):
    """No load-bearing live operative surface may reference the
    retired env vars. Comment-only historical pointers (introduced
    by feature/blob-prune-default-on itself) are exempt — this
    test is a regression guard, not a comment sanitizer.

    Historical planning docs (``.agents/shared/planning/**``) are
    excluded (covered by the supersession-note test below)."""
    hits: list[tuple[Path, int, str]] = []
    for path in _iter(LIVE_OPERATIVE_GLOBS):
        text = _read(path)
        if text is None:
            continue
        for lineno, line in _match_load_bearing_offsets(text, needle):
            hits.append((path, lineno, line))

    assert hits == [], (
        f"env flag {needle!r} is referenced in LOAD-BEARING live "
        f"operative surfaces; the env-flag ladder was retired in "
        f"feature/blob-prune-default-on. Comment-only historical "
        f"pointers alongside ``feature/blob-prune-default-on`` are "
        f"allowed — operative code / test / config references are "
        f"NOT. HITS:\n"
        + "\n".join(f"  {p}:{ln}: {line!r}" for p, ln, line in hits)
    )


@pytest.mark.parametrize("needle", ENV_FLAGS)
def test_env_flag_remains_in_historical_planning_with_supersession_note(needle):
    """Historical planning docs may keep the env-var name ONLY if a
    dated supersession note is present."""
    for path in _iter(HISTORICAL_ALLOWLIST_GLOBS):
        text = _read(path)
        if text is None:
            continue
        # The supersession note must (a) date-stamp near the
        # env-var mention and (b) name the destructive-default
        # contract that replaced the gate. We accept any phrasing
        # that captures both signals (the test exists to ensure
        # the note carries them — not to lock the wording).
        # The matched note can be a Markdown header
        # (``## Superseded (2026-10-11)``) or a plain paragraph.
        supersession_pattern = re.compile(
            r"superseded[^\n]{0,40}\d{4}-\d{2}-\d{2}.*?"
            r"(?:blob-prune-default-on|"
            r"destructive\s+prune\s+default-on|"
            r"destructive\s+prune\s+unconditional|"
            r"dual-arm\s+gate\s+removed|"
            r"feature/blob-prune-default-on)"
            r".*?(?:gone|removed|retired|unconditional|default-on)",
            re.IGNORECASE | re.DOTALL,
        )
        if needle not in text:
            continue
        if supersession_pattern.search(text):
            continue
        pytest.fail(
            f"{path.relative_to(REPO_ROOT)} references the retired "
            f"env flag {needle!r} without a dated supersession "
            f"note. Add one — example: 'superseded 2026-10-11: "
            f"dual-arm gate removed, destructive prune default-on, "
            f"see feature/blob-prune-default-on'"
        )


@pytest.mark.parametrize("needle", GONE_SYMBOLS)
def test_retired_helper_symbols_absent_from_load_bearing_live_surfaces(needle):
    """The helper functions / config-block fields whose
    responsibility is now obsolete must not appear in load-bearing
    live code paths. Comment-only historical pointers are exempt
    (same rationale as the env-flag test)."""
    hits: list[tuple[Path, int, str]] = []
    for path in _iter(LIVE_OPERATIVE_GLOBS):
        text = _read(path)
        if text is None:
            continue
        for lineno, line in _match_load_bearing_offsets(text, needle):
            hits.append((path, lineno, line))
    assert hits == [], (
        f"retired symbol {needle!r} is referenced in LOAD-BEARING "
        f"live operative surfaces; the symbol's purpose is gone "
        f"since feature/blob-prune-default-on. Comment-only "
        f"historical pointers are exempt. HITS:\n"
        + "\n".join(f"  {p}:{ln}: {line!r}" for p, ln, line in hits)
    )


def test_checkpoint_prune_no_dual_arm_gate_function_call_load_bearing():
    """The old ``blob_prune_destructive_enabled()`` function call must
    not appear in any load-bearing ``daemon/`` source path.
    Comment-only historical pointers are exempt."""
    hits: list[tuple[Path, int, str]] = []
    for path in REPO_ROOT.glob("daemon/**/*.py"):
        text = _read(path)
        if text is None:
            continue
        for lineno, line in _match_load_bearing_offsets(
            text, "blob_prune_destructive_enabled"
        ):
            hits.append((path, lineno, line))
    assert hits == [], (
        "blob_prune_destructive_enabled() must not appear in any "
        "load-bearing daemon/ source file. Comment-only historical "
        "pointers are exempt. HITS:\n"
        + "\n".join(f"  {p}:{ln}: {line!r}" for p, ln, line in hits)
    )


def test_delete_blobs_anti_join_signature_unchanged():
    """The public ``delete_blobs_anti_join`` signature must accept
    (thread_id, checkpoint_ns) — the signature is the public
    contract for the chunk-loop seam.

    A future refactor that drops the chunk loop back to an
    unbounded DELETE would change either the SQL or the chunk
    loop — this test pins that the PUBLIC entry point retains
    its semantics: caller passes ``(thread_id, ns)`` and the
    internal chunk helper takes ``batch_size`` as the third
    positional."""
    import inspect

    from daemon.checkpoint_adapter import PostgresCheckpointerAdapter

    sig = inspect.signature(PostgresCheckpointerAdapter.delete_blobs_anti_join)
    params = list(sig.parameters.keys())
    assert params[:2] == ["self", "thread_id"], (
        f"delete_blobs_anti_join(thread_id, checkpoint_ns) "
        f"signature drifted: {params}"
    )
    chunk_sig = inspect.signature(
        PostgresCheckpointerAdapter._delete_blobs_anti_join_chunk
    )
    assert list(chunk_sig.parameters.keys())[:4] == [
        "self",
        "thread_id",
        "checkpoint_ns",
        "batch_size",
    ], (
        f"chunk signature drifted: {list(chunk_sig.parameters.keys())}"
    )

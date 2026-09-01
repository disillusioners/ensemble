"""Tests for the Phase 0 constitution census (job-task system).

Mirrors the tool-name drift precedent in
``tests/unit/tools/test_frozen_tool_name_discovery.py``. Bidirectional
drift detection between source and the static ``KNOWN_*`` universes
in ``daemon.job_state.constitution``.

Drift semantics: bidirectional for writers/creators; subset-only for
mints — a new source mint is a registration obligation (D4 checklist),
not a test failure. Concretely:

    * Writers/creators: a new site lands in source but the static set
      hasn't been regenerated → test fails (caller must run
      ``regenerate_sets()`` and paste the writer/creator literals).
    * Writers/creators: a static entry references a function that no
      longer exists in source (writer deleted but static set not
      updated) → test fails the same way.
    * Mints: the test enforces only ``KNOWN_MINT_SITES ⊆ source`` — a
      NEW source mint passes silently; registering it is a D4
      checklist obligation for the human reviewer.

The frozen-binary contract is enforced as a separate test:
``discover_*_paths()`` MUST raise ``RuntimeError`` when zero source
files are readable (PyInstaller bytecode-only builds).
"""
from __future__ import annotations

from pathlib import Path

import pytest

import daemon.job_state.constitution as constitution
from daemon.job_state.constitution import (
    KNOWN_ADMISSION_STATE_WRITERS,
    KNOWN_JOBITEM_CREATORS,
    KNOWN_MINT_SITES,
    discover_admission_state_writer_paths,
    discover_jobitem_creator_paths,
    discover_work_id_mint_paths,
)


# ============================================================
# Bidirectionality — the regen source-of-truth test
# ============================================================

def test_known_admission_state_writers_matches_source_exactly_no_drift() -> None:
    """Bidirectional drift detector: ``KNOWN_ADMISSION_STATE_WRITERS``
    must equal the source-discovered set exactly.

    The merge in ``get_all_admission_state_writers()``
    (``source ∪ KNOWN_ADMISSION_STATE_WRITERS``) only catches ADDITIONS
    to source — a writer removed/renamed in source but still listed in
    the static set would silently pass the merge. This test closes
    that seam by comparing the static set against the pure source-only
    universe, and surfaces BOTH diff directions in the failure message
    so the next maintainer can act without re-running anything.
    """
    static_set = set(KNOWN_ADMISSION_STATE_WRITERS)
    source_set = discover_admission_state_writer_paths()

    assert static_set == source_set, (
        "KNOWN_ADMISSION_STATE_WRITERS drift vs source: "
        f"only_in_static={sorted(static_set - source_set)} "
        f"only_in_source={sorted(source_set - static_set)}"
    )


def test_known_jobitem_creators_matches_source_exactly_no_drift() -> None:
    """Bidirectional drift detector: ``KNOWN_JOBITEM_CREATORS`` must
    equal the source-discovered set exactly.

    The JAFP boundary (I4) — every JobItem constructor site is registered
    so any future internal creator lands on the constitution (amendment
    trigger). Drift in either direction is a constitution break.
    """
    static_set = set(KNOWN_JOBITEM_CREATORS)
    source_set = discover_jobitem_creator_paths()

    assert static_set == source_set, (
        "KNOWN_JOBITEM_CREATORS drift vs source: "
        f"only_in_static={sorted(static_set - source_set)} "
        f"only_in_source={sorted(source_set - static_set)}"
    )


def test_known_mint_sites_is_subset_of_source() -> None:
    """The ``KNOWN_MINT_SITES`` static set is a curated SUBSET of the
    source mint set — it covers only mints that produce ``work_id``-
    shaped handles (D4 fail-open register). General-purpose UUID mints
    (model PKs, message ids, instance ids, ...) live in source but are
    not part of the constitution.

    The constraint is therefore: ``KNOWN_MINT_SITES ⊆ source_mints``.
    Bidirectional for writers/creators; subset-only for mints — a new
    source mint missing from the static set is a registration
    obligation (D4 checklist), not a test failure.
    """
    source_set = discover_work_id_mint_paths()
    static_set = set(KNOWN_MINT_SITES)

    missing = static_set - source_set
    assert not missing, (
        "KNOWN_MINT_SITES references mints not in source "
        "(stale registration — remove from the static set): "
        f"{sorted(missing)}"
    )


# ============================================================
# Frozen-binary contract — the loud-failure guarantee
# ============================================================

def test_writer_discovery_raises_in_frozen_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``discover_admission_state_writer_paths()`` must fail loudly
    with ``RuntimeError`` when zero source files are readable.

    Simulates the PyInstaller frozen-binary case by replacing the
    scanner's source root with a tmpdir that contains no .py files.
    Drift detection from bytecode-only is not meaningful; callers
    that need the frozen-safe universe should use
    :func:`get_all_admission_state_writers` instead.
    """
    # Force the scanner's _iter_source_files() to yield nothing by
    # rewriting the source root to an empty directory.
    empty_dir = tmp_path / "empty_daemon"
    empty_dir.mkdir()
    monkeypatch.setattr(constitution, "_SOURCE_ROOT", empty_dir)

    with pytest.raises(RuntimeError, match="no daemon/ source files readable"):
        discover_admission_state_writer_paths()


def test_creator_discovery_raises_in_frozen_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``discover_jobitem_creator_paths()`` must fail loudly with
    ``RuntimeError`` when zero source files are readable.
    """
    empty_dir = tmp_path / "empty_daemon"
    empty_dir.mkdir()
    monkeypatch.setattr(constitution, "_SOURCE_ROOT", empty_dir)

    with pytest.raises(RuntimeError, match="no daemon/ source files readable"):
        discover_jobitem_creator_paths()


def test_mint_discovery_raises_in_frozen_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``discover_work_id_mint_paths()`` must fail loudly with
    ``RuntimeError`` when zero source files are readable.
    """
    empty_dir = tmp_path / "empty_daemon"
    empty_dir.mkdir()
    monkeypatch.setattr(constitution, "_SOURCE_ROOT", empty_dir)

    with pytest.raises(RuntimeError, match="no daemon/ source files readable"):
        discover_work_id_mint_paths()


# ============================================================
# Frozen-safe merge — source ∪ static fallback
# ============================================================

def test_get_all_admission_state_writers_merges_source_and_static(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When source IS readable, the merge must equal source ∪ static.

    After the bidirectionality test passes, source == static exactly;
    this test pins the merge semantics for the partial-source case
    (some files readable, others not) — source is canonical where
    present, static covers the rest.
    """
    # Construct a tmp-dir daemon with one writer that is NOT in the
    # static set — confirms the merge picks up new source additions
    # even if the static set hasn't been regenerated yet.
    fake_daemon = tmp_path / "daemon"
    fake_daemon.mkdir()
    (fake_daemon / "fake_module.py").write_text(
        'class JobItem:\n    pass\n'
        'def unknown_writer():\n'
        '    return JobItem(admission_state="queued")\n'
    )
    monkeypatch.setattr(constitution, "_SOURCE_ROOT", fake_daemon)
    monkeypatch.setattr(constitution, "_REPO_ROOT", tmp_path)

    merged = constitution.get_all_admission_state_writers()

    # The fake writer should be picked up from source even though it's
    # not in the static set — that's the partial-source defensive merge.
    assert "daemon/fake_module.py:unknown_writer" in merged


def test_get_all_admission_state_writers_falls_back_in_frozen_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When NO source files are readable, the merge returns the
    static fallback universe (frozen-safe path).
    """
    empty_dir = tmp_path / "empty_daemon"
    empty_dir.mkdir()
    monkeypatch.setattr(constitution, "_SOURCE_ROOT", empty_dir)

    merged = constitution.get_all_admission_state_writers()
    assert merged == set(KNOWN_ADMISSION_STATE_WRITERS)


# ============================================================
# Mint-idiom completeness — the spec's OPEN ITEM coverage
# ============================================================

def test_mint_scanner_recognises_all_documented_idioms() -> None:
    """The mint scanner must recognise every idiom listed in the
    spec's OPEN ITEM (spec §5: ``uuid.uuid4`` vs ``token_hex`` /
    ``uuid7``). Bidirectional for writers/creators; subset-only for
    mints — a new source mint is a registration obligation (D4
    checklist), not a test failure. This test pins the per-idiom
    recognition coverage: the scanner must never silently lose one of
    the documented idioms, since recognition is the only completeness
    mechanism it has.

    Constructs an in-memory daemon tree with one file per idiom and
    confirms the scanner finds each one.
    """
    import tempfile
    import os

    with tempfile.TemporaryDirectory() as tmp:
        daemon_dir = os.path.join(tmp, "daemon")
        os.makedirs(daemon_dir)
        repo = tmp

        # uuid.uuid4()
        with open(os.path.join(daemon_dir, "a_uuid4.py"), "w") as f:
            f.write("import uuid\nx = uuid.uuid4()\n")
        # bare uuid4 (from uuid import uuid4)
        with open(os.path.join(daemon_dir, "b_bare_uuid4.py"), "w") as f:
            f.write("from uuid import uuid4\nx = uuid4()\n")
        # uuid.uuid7()
        with open(os.path.join(daemon_dir, "c_uuid7.py"), "w") as f:
            f.write("import uuid\nx = uuid.uuid7()\n")
        # secrets.token_hex
        with open(os.path.join(daemon_dir, "d_token_hex.py"), "w") as f:
            f.write("import secrets\nx = secrets.token_hex(16)\n")
        # secrets.token_urlsafe
        with open(os.path.join(daemon_dir, "e_token_urlsafe.py"), "w") as f:
            f.write("import secrets\nx = secrets.token_urlsafe(16)\n")

        from unittest.mock import patch
        with patch.object(constitution, "_SOURCE_ROOT", Path(daemon_dir)), \
             patch.object(constitution, "_REPO_ROOT", Path(repo)):
            found = constitution.discover_work_id_mint_paths()

    # Each idiom must be present. Note: the scanner returns
    # ``<relpath>:<line>:<token>`` keys so we check substring presence.
    joined = "\n".join(found)
    for expected_token in ("uuid.uuid4", "uuid.uuid7", "secrets.token_hex",
                           "secrets.token_urlsafe", "uuid4"):
        assert any(expected_token in key for key in found), (
            f"mint idiom {expected_token!r} not recognised by scanner — "
            f"D4 tripwire. Found: {sorted(found)}"
        )


def test_scanner_skips_constitution_module_self_references() -> None:
    """The scanner must NOT count the constitution module itself as a
    writer. The module's own docstrings contain ``SET admission_state``
    tokens in comments / descriptions; including them would conflate
    the registry with its subject.
    """
    src = discover_admission_state_writer_paths()
    assert not any("job_state/constitution.py" in s for s in src), (
        "Scanner self-references detected — should skip its own module. "
        f"Found: {sorted(s for s in src if 'constitution' in s)}"
    )

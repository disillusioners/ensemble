"""The P6 H2 module split — the joined-room block's new home.

Agent group chat P6 (2026-11-16). The P5 tidier's H2 recommendation was
to extract the joined-room builder + its constants out of
``daemon/services/context_messages.py`` (the file had crossed 3000
lines — the breach pre-dates P5). The extraction moved TWO blocks:

* ``daemon/services/joined_room_context.py`` — the joined-room builder
  + its constants (``ROOM_GUIDELINES_TEXT`` and friends);
* ``daemon/services/context_block.py`` — the shared block primitives
  every context builder uses (``CONTEXT_PREFIX`` / ``CONTEXT_SUFFIX`` /
  ``_make_context_message`` / ``_stable_id_for`` /
  ``escape_for_context_block``).

Both are MOVES, not forks: every moved name is re-exported from
``context_messages``, so the historical import surface is unchanged.

What this file pins:

* the size criterion — ``context_messages.py`` is UNDER 3000 lines
  (the measured before/after is recorded in the A-P6 decision entry);
* the OWNERSHIP — the joined-room builder is DEFINED in the new module
  and only IMPORTED by ``context_messages`` (a second definition would
  be a fork);
* the IMPORT COMPATIBILITY — every moved name resolves from the old
  home and IS the very same object as the new home's (no drift);
* the shared primitives' new home.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

CONTEXT_MESSAGES = REPO_ROOT / "daemon" / "services" / "context_messages.py"
JOINED_ROOM = REPO_ROOT / "daemon" / "services" / "joined_room_context.py"
CONTEXT_BLOCK = REPO_ROOT / "daemon" / "services" / "context_block.py"

#: The names the joined-room module owns (moved out of
#: ``context_messages``).
JOINED_ROOM_NAMES = (
    "CONTEXT_KIND_JOINED_ROOM",
    "ROOM_GUIDELINES_TEXT",
    "ROOM_DIGEST_MAX_CHARS",
    "ROOM_BLOCK_MAX_CHARS",
    "ROOM_ROSTER_MAX_CHARS",
    "ROOM_LABEL_MAX_CHARS",
    "ROOM_LABEL_MIN_CHARS",
    "ROOM_DIGEST_LAST_N",
    "ROOM_DIGEST_LINE_MAX_CHARS",
    "build_joined_room_context_message",
)

#: The names the shared-primitives module owns.
CONTEXT_BLOCK_NAMES = (
    "CONTEXT_PREFIX",
    "CONTEXT_SUFFIX",
    "_make_context_message",
    "_stable_id_for",
    "escape_for_context_block",
)


def _module_source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_under_ceiling(context_messages: Path) -> None:
    """The H2 criterion, as a PURE check over a given file.

    The pin calls it with the production path; the mutation legs call
    it with a BROKEN copy — so the leg proves the check has teeth
    without the production file ever being written.
    """
    lines = len(_module_source(context_messages).splitlines())
    assert lines < 3000, (
        f"daemon/services/context_messages.py is {lines} lines — the H2 "
        "extraction must keep it under 3000"
    )


def _assert_defined_in(path: Path, names: tuple[str, ...]) -> None:
    """The OWNERSHIP check, as a PURE check over a given file."""
    defined = _defined_names(path)
    for name in names:
        assert name in defined, f"{name} is not defined in {path.name}"


def _assert_not_defined_in(path: Path, names: tuple[str, ...]) -> None:
    """The FORK check, as a PURE check over a given file."""
    defined = _defined_names(path)
    for name in names:
        assert name not in defined, (
            f"{name} is still DEFINED in context_messages.py — the moved "
            "names must be imported, not re-defined"
        )


def _defined_names(path: Path) -> set[str]:
    """The module-level names DEFINED by a file (the defs + the
    assignments — an imported name is not a definition)."""
    tree = ast.parse(_module_source(path))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                names.add(node.target.id)
    return names


def test_context_messages_is_under_three_thousand_lines() -> None:
    """The H2 criterion — the file is back UNDER the 3000-line ceiling.

    The breach pre-dates P5 (the P5 tidier measured 3494 before P5's
    own +284); this pin keeps the extraction honest — a future
    append that re-crosses the ceiling REDS here.
    """
    _assert_under_ceiling(CONTEXT_MESSAGES)


def test_the_joined_room_builder_is_defined_in_its_own_module() -> None:
    """The OWNERSHIP — the builder is DEFINED in the new module."""
    _assert_defined_in(JOINED_ROOM, JOINED_ROOM_NAMES)


def test_context_messages_does_not_define_the_moved_names() -> None:
    """A second DEFINITION would be a fork — ``context_messages`` only
    imports the moved names."""
    _assert_not_defined_in(CONTEXT_MESSAGES, JOINED_ROOM_NAMES + CONTEXT_BLOCK_NAMES)


def test_the_shared_primitives_live_in_the_context_block_module() -> None:
    """The shared block primitives have their own home."""
    _assert_defined_in(CONTEXT_BLOCK, CONTEXT_BLOCK_NAMES)


def test_the_historical_import_surface_resolves_the_same_objects() -> None:
    """A MOVE, not a fork — every moved name resolves from the old home
    and IS the new home's object."""
    from daemon.services import context_block, context_messages
    from daemon.services import joined_room_context

    for name in JOINED_ROOM_NAMES:
        assert getattr(context_messages, name) is getattr(
            joined_room_context, name
        ), name
    for name in CONTEXT_BLOCK_NAMES:
        assert getattr(context_messages, name) is getattr(
            context_block, name
        ), name


# ── the MUTATION LEGS (the split pins must have TEETH) ──────────────────
#
# Three of the five split pins had NO mutation leg: the size
# criterion, the joined-room OWNERSHIP and the shared-primitives
# OWNERSHIP. A pin nobody ever broke is a pin nobody can trust.
#
# DISCIPLINE (the A-P6-29 posture — the probe runs on a COPY): each
# leg plants the mutation in a SCRATCH COPY of the production source
# and never writes the production file — so an interruption can
# never leave a broken tree behind, and the production bytes are
# sha-verified UNCHANGED after every leg ("restored byte-identically",
# trivially, because nothing was ever written).
#
# The REAL-TREE form of the same three legs (the mutation plants the
# byte in the production file, the pack goes RED via the intended
# assertion, and the file is restored byte-identically) is the
# mutation MATRIX's job — ``test/packs/group_chat_room_mutation_matrix_test.py``
# (legs GC-M07/GC-M08/GC-M09). The legs here are the cheap, always-on
# half: they run in the ordinary unit gate.


def _broken_copy(
    tmp_path: Path,
    source: Path,
    name: str,
    find: str,
    replace: str,
) -> Path:
    """A scratch COPY of the production source with one mutation planted.

    The copy carries the production bytes byte-for-byte apart from the
    mutation; the production file is not opened for writing.
    """
    text = source.read_text(encoding="utf-8")
    assert text.count(find) == 1, (
        f"the mutation anchor is not unique in {source.name} "
        f"({text.count(find)})"
    )
    broken = tmp_path / name
    broken.write_text(text.replace(find, replace, 1), encoding="utf-8")
    return broken


def test_the_size_pin_reds_when_the_ceiling_is_crossed(tmp_path: Path) -> None:
    """MUTATION LEG 1 (the size criterion): growing the file back over
    3000 lines MUST red the size pin."""
    before = _sha256(CONTEXT_MESSAGES)
    grown = tmp_path / "context_messages.py"
    grown.write_text(
        _module_source(CONTEXT_MESSAGES) + "\n" * 400,
        encoding="utf-8",
    )
    with pytest.raises(AssertionError):
        _assert_under_ceiling(grown)
    # the production file was never written
    assert _sha256(CONTEXT_MESSAGES) == before


def test_the_joined_room_ownership_pin_reds_when_a_name_leaves_its_module(
    tmp_path: Path,
) -> None:
    """MUTATION LEG 2 (the joined-room ownership): a constant that is no
    longer a MODULE-LEVEL definition MUST red the ownership pin.

    The mutation wraps one constant in ``if True:`` — the name is
    still importable at runtime, but the module-level DEFINITION the
    pin looks for is gone (a fork/move, not a crash).
    """
    before = _sha256(JOINED_ROOM)
    broken = _broken_copy(
        tmp_path,
        JOINED_ROOM,
        "joined_room_context.py",
        "ROOM_DIGEST_MAX_CHARS = 2000",
        "if True:  # MUTATION: no longer a module-level definition\n"
        "    ROOM_DIGEST_MAX_CHARS = 2000",
    )
    with pytest.raises(AssertionError):
        _assert_defined_in(broken, JOINED_ROOM_NAMES)
    assert _sha256(JOINED_ROOM) == before


def test_the_shared_primitives_pin_reds_when_a_primitive_leaves_its_module(
    tmp_path: Path,
) -> None:
    """MUTATION LEG 3 (the shared-primitives ownership): the same break
    in the shared-primitives module MUST red ITS pin."""
    before = _sha256(CONTEXT_BLOCK)
    broken = _broken_copy(
        tmp_path,
        CONTEXT_BLOCK,
        "context_block.py",
        'CONTEXT_SUFFIX = "]\\n\\n"',
        "if True:  # MUTATION: no longer a module-level definition\n"
        '    CONTEXT_SUFFIX = "]\\n\\n"',
    )
    with pytest.raises(AssertionError):
        _assert_defined_in(broken, CONTEXT_BLOCK_NAMES)
    assert _sha256(CONTEXT_BLOCK) == before

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
from pathlib import Path

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
    lines = len(_module_source(CONTEXT_MESSAGES).splitlines())
    assert lines < 3000, (
        f"daemon/services/context_messages.py is {lines} lines — the H2 "
        "extraction must keep it under 3000"
    )


def test_the_joined_room_builder_is_defined_in_its_own_module() -> None:
    """The OWNERSHIP — the builder is DEFINED in the new module."""
    defined = _defined_names(JOINED_ROOM)
    for name in JOINED_ROOM_NAMES:
        assert name in defined, f"{name} is not defined in joined_room_context.py"


def test_context_messages_does_not_define_the_moved_names() -> None:
    """A second DEFINITION would be a fork — ``context_messages`` only
    imports the moved names."""
    defined = _defined_names(CONTEXT_MESSAGES)
    for name in JOINED_ROOM_NAMES + CONTEXT_BLOCK_NAMES:
        assert name not in defined, (
            f"{name} is still DEFINED in context_messages.py — the moved "
            "names must be imported, not re-defined"
        )


def test_the_shared_primitives_live_in_the_context_block_module() -> None:
    """The shared block primitives have their own home."""
    defined = _defined_names(CONTEXT_BLOCK)
    for name in CONTEXT_BLOCK_NAMES:
        assert name in defined, f"{name} is not defined in context_block.py"


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

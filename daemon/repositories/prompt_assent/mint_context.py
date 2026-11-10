"""The AR-1 private-channel classifier — fail-closed by construction.

The pure, lane-scoped half of the AR-1 mint-context derivation: given the
NESTED ``metadata["discord"]`` dict the Discord adapter builds
(``daemon/sources/adapters/discord/adapter.py`` — the ``discord_meta``
build site), decide whether the channel is a PRIVATE 1:1 DM.

AR-1 leg 2 (K-3): ``private = is_dm AND NOT is_group_dm``. The ruling's
fail-closed rule is absolute — an ABSENT or UNPARSEABLE discriminator ⇒
NOT private. This bounds a stale/older message row to an AVAILABILITY
problem (the lane refuses to mint/approve there), never an AUTHORITY
problem: only literal ``True``/``False`` booleans are trusted.

The adapter-side discriminator (``is_group_dm`` added to the nested
``discord_meta``, additive metadata, no schema change) is task 1.12 and
P1-owned; this classifier is the consumption contract for the P2 tool
gate (mint step M-4c) and the P3 approving-context check (step 4f).

NEVER derive authority from this module: it is a pure function over
daemon-owned metadata. The mint context itself is derived from the
factor-2 window's message row — never from tool arguments.
"""

from __future__ import annotations

from typing import Any, Mapping


def is_private_assent_channel(discord_meta: Mapping[str, Any] | None) -> bool:
    """``True`` ONLY for a proven 1:1 DM (AR-1 leg 2 / K-3).

    Fail-closed on every non-proven shape:

    * ``meta`` ``None`` / empty ⇒ ``False`` (not private);
    * ``is_dm`` absent, non-bool, or ``False`` ⇒ ``False``;
    * ``is_group_dm`` absent, non-bool, or ``True`` ⇒ ``False`` — this is
      the fail-closed discriminator rule: an older row that predates the
      discriminator cannot mint or approve until the discriminator ships
      (ruling: *"absent/unparseable discriminator = NOT private"*);
    * only ``is_dm is True AND is_group_dm is False`` ⇒ ``True``.

    The identity checks (``is True`` / ``is False``) are deliberate:
    truthy strings like ``"yes"`` or ``1`` are UNPARSEABLE under the
    ruling and classify as NOT private.
    """
    if not discord_meta:
        return False
    return discord_meta.get("is_dm") is True and (
        discord_meta.get("is_group_dm") is False
    )

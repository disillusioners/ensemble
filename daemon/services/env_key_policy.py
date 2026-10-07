"""Shared env-key secret-shape policy — ONE conservative word list.

Single home for the secret-shape classification so the WRITE gate
(``daemon.tools.infra::mcp_set_env``) and the READ/redact surface
(``daemon.routers.mcp_servers::redact_secrets``) cannot drift apart.
W2 fix, reviewer council APPROVE-WITH-FIXES on 8b520d54..HEAD
(2026-10-02); leader decision: shared helper + broadened list,
kept conservative — NO words beyond the reviewer's eight.

The eight words
    ``KEY`` / ``TOKEN`` / ``SECRET`` / ``PASSWORD`` /
    ``CREDENTIAL`` / ``PRIVATE`` / ``PWD`` / ``AUTH``

matched as case-insensitive substrings of the upper-cased key name
(same matching discipline both consumers used pre-consolidation).

Write-side vs read-side scope
-----------------------------

* WRITE gate (``mcp_set_env``): uses :data:`SECRET_MARKER_WORDS` ONLY.
  ``BASE`` / ``HEADERS`` are deliberately NOT write-gate words — a
  base URL is a non-secret value the tool is ALLOWED to write (e.g.
  a ``*_BASE_URL``-shaped env), a stance documented at the tool since
  its introduction.
* READ/redact (``redact_secrets``): uses :data:`SECRET_MARKER_WORDS`
  PLUS the two presentation-only extras ``BASE`` / ``HEADERS``
  (``*_API_BASE`` pins internal endpoints; ``*_EXTRA_HEADERS``
  carries ``Authorization`` tokens). Those extras stay redact-side
  only — they are over-redaction guards, not write bans.

ASCII-identifier discipline (W2)
--------------------------------

:func:`is_ascii_env_key` is the WRITE-path validator: env keys must
match ``[A-Za-z_][A-Za-z0-9_]*``. Non-ASCII / homoglyph key names
(e.g. Cyrillic-lookalike ``КЕY``) could otherwise dodge substring
classification while still landing in ``config.env``. The HTTP lane
(``POST`` create accepts full config dicts) is NOT write-gated —
the shared word list applied at the redact/read surface is the
backstop there; this validator closes the agent write lane.
"""

from __future__ import annotations

import re

__all__ = [
    "SECRET_MARKER_WORDS",
    "REDACT_ONLY_MARKER_WORDS",
    "env_key_is_secret_shaped",
    "is_ascii_env_key",
]

#: The conservative secret-shape word list (exactly the reviewer's
#: eight; no beyond-reviewer additions). Substring, case-insensitive.
SECRET_MARKER_WORDS: tuple[str, ...] = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "CREDENTIAL",
    "PRIVATE",
    "PWD",
    "AUTH",
)

#: Presentation-only extras the READ/redact surface adds on top of
#: :data:`SECRET_MARKER_WORDS`. Never used by write gates.
REDACT_ONLY_MARKER_WORDS: tuple[str, ...] = ("BASE", "HEADERS")

_ASCII_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def env_key_is_secret_shaped(env_key: str) -> bool:
    """True when ``env_key`` contains any shared secret word.

    Case-insensitive substring match on the upper-cased key — the
    same discipline both consumers used pre-consolidation, now
    single-sourced.
    """
    upper = env_key.upper()
    return any(m in upper for m in SECRET_MARKER_WORDS)


def is_ascii_env_key(env_key: str) -> bool:
    """True iff ``env_key`` is an ASCII identifier (``[A-Za-z_][A-Za-z0-9_]*``).

    Write-path validator: rejects non-ASCII / homoglyph key names that
    could smuggle past substring classification.
    """
    return (
        isinstance(env_key, str)
        and _ASCII_ENV_KEY_RE.fullmatch(env_key) is not None
    )

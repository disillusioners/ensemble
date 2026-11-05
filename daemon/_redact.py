"""Exception-string redaction for log lines (incident 2026-10-10 W2).

Verbose libpq / psycopg error messages carry SERVER IDENTIFIERS —
``host:port``, unix socket paths, database names, DSN URIs (sometimes
with embedded credentials). When such an exception is stringified into
a log line (e.g. ``str(exc)[:200]`` in the checkpoint-saver retry
warning), those identifiers leak into logs, journals, and transcripts.

:func:`redact_exc_str` is a small, pure, dependency-free helper that
masks the known libpq/DSN identifier shapes BEFORE truncation. It is
deliberately CONSERVATIVE: only patterns that are unambiguous database
identifiers are masked, so the diagnostic value of the message (error
class, SQLSTATE, plain-English cause) is preserved.

Redaction patterns applied (in order):

1. **URI netloc** — ``scheme://[user:password@]host[:port]/…``
   → ``scheme://***/…`` (userinfo, credentials, host and port all
   masked together — applied to ANY URI regardless of credentials;
   URIs in error strings are almost always connection strings, and
   matches any RFC-3986 scheme so ``postgresql://``, ``postgres://``,
   ``https://`` … all covered).
2. **libpq verbose TCP shape** — ``connection to server at "10.1.2.3",
   port 5432 failed`` → host → ``<redacted>``, port → ``***``.
3. **libpq verbose unix-socket shape** — ``connection to server on
   socket "/var/run/postgresql/.s.PGSQL.5432" failed`` →
   ``on socket "<redacted>"``.
4. **Keyword-value DSN fields** — ``host=… port=… dbname=… user=…
   password=… sslcert=… sslkey=… sslrootcert=…`` → each value → ``***``.
5. **Bare IPv4:port literals** — ``10.1.2.3:5432`` → ``***:***``
   (regex is anchored on the IPv4 octet shape + 2-5 digit port, so
   timestamps and versions don't match).

Hostname:port pairs WITHOUT a scheme or DSN keyword (e.g.
``postgres.internal:5432``) are NOT masked — indistinguishable from
arbitrary ``word:word`` text without a parser, and a false positive
here would corrupt unrelated messages. Documented limitation.

Failure mode: never raises. On any internal error it falls back to the
plain truncated string (a redaction bug must never break logging).

:func:`redact_secret_shapes` is the SECOND, ADDITIVE masker — it
covers the SECRET-VALUE shapes that survive the identifier masker
above (the AM-11 fence for the ``nest-anomaly-report`` lane). It is
ADDITIVE: :func:`redact_exc_str` keeps its own behaviour and its own
callers; the two are composed by the caller, in that order.
"""

from __future__ import annotations

import re

__all__ = ["redact_exc_str", "redact_secret_shapes"]

# 1. scheme://[user:password@]host:port → scheme://***  (userinfo,
#    credentials, host and port masked together — any URI scheme)
_URI_NETLOC_RE = re.compile(r"([A-Za-z][A-Za-z0-9+.\-]*://)[^\s/]+")

# 2. at "host", port N (libpq TCP failure verbose shape)
_LIBPQ_TCP_RE = re.compile(r'at "[^"]*", port \d+')

# 3. on socket "/path/.s.PGSQL.5432" (libpq unix-socket verbose shape)
_LIBPQ_SOCKET_RE = re.compile(r'on socket "[^"]*"')

# 4. keyword=value DSN fields (host/port/dbname/user/password/ssl*)
_KV_DSN_RE = re.compile(
    r"\b(host|hostname|port|dbname|database|user|password|"
    r"sslcert|sslkey|sslrootcert|sslpassword)=(\"[^\"]*\"|\S+)",
    re.IGNORECASE,
)

# 5. bare IPv4:port literal
_IPV4_PORT_RE = re.compile(
    r"\b\d{1,3}(?:\.\d{1,3}){3}:\d{2,5}\b"
)

_DEFAULT_LIMIT = 200


def redact_exc_str(exc: object, limit: int = _DEFAULT_LIMIT) -> str:
    """Return ``str(exc)`` with server identifiers masked, truncated.

    Args:
        exc: Any exception (or plain string — both are stringified).
        limit: Maximum returned length (applied AFTER redaction so the
            most informative tail of the message survives).

    Returns:
        The redacted, truncated message. Never raises: on an internal
        error the plain truncated string is returned unchanged.
    """
    try:
        text = str(exc)
        text = _URI_NETLOC_RE.sub(r"\1***", text)
        text = _LIBPQ_TCP_RE.sub('at "<redacted>", port ***', text)
        text = _LIBPQ_SOCKET_RE.sub('on socket "<redacted>"', text)
        text = _KV_DSN_RE.sub(r"\1=***", text)
        text = _IPV4_PORT_RE.sub("***:***", text)
        return text if len(text) <= limit else text[:limit]
    except Exception:  # pragma: no cover — redaction must never break logging
        try:
            return str(exc)[:limit]
        except Exception:
            return "<unstringifiable exception>"


# ── the additive SECRET-SHAPE masker (AM-11) ───────────────────────────
#
# Why a SECOND masker (AM-11): the identifier masker above covers
# ``host:port`` / DSN / URI shapes. It does NOT cover the SECRET-VALUE
# shapes that the ``nest-anomaly-report`` lane proved survive it:
#
#   * ``POSTGRES_PASSWORD=…`` — the house ``_KV_DSN_RE`` requires a
#     ``\b`` word boundary, which does NOT exist after ``POSTGRES_``.
#   * bare ``OPENAI_API_KEY=sk-…`` — the same boundary problem.
#   * URL QUERY-PARAM secrets (``?api_key=…``) — the netloc masker
#     stops at the first ``/``.
#   * JSON secret dumps (``{"password": "hunter2"}``) — the keyword
#     pattern needs ``:``/``=`` IMMEDIATELY after the keyword, but JSON
#     puts a QUOTE there.
#   * bearer tokens.
#
# SOLE-DEFENCE STATEMENT (C1c): for a NON-STORED secret the pattern
# masker is the ONLY defence — the exact-match scrubber
# (``daemon/services/secret_persistence_scrub.py``) knows only STORED
# secret values, so scrubber PRESENCE is irrelevant to that class.

# 1. ``sk-…`` provider key shape.
_SK_KEY_RE = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")

# 2. ``Bearer <token>`` — the value is the whole non-space run.
_BEARER_RE = re.compile(r"(?i)\b(Bearer\s+)(\S+)")

# 3. The keyword FAMILY — ``api_key`` / ``token`` / ``password`` /
#    ``secret`` / ``access_key`` / ``credential`` / ``pwd`` /
#    ``private_key`` (the W2 council widening, 2026-11-05) PLUS the
#    SUFFIX-ANCHORED ``*_key`` / ``*_auth`` class (Condition B, code
#    council delta 2026-11-05: the bare-KEY word class measured
#    UNMASKED — ``encryption_key`` / ``SIGNING_KEY`` / ``auth_key`` /
#    ``ENCRYPTION_KEY=`` / ``SSH_KEY=`` / ``MCP_AUTH=``).
#    The suffix anchor is DELIBERATE: ``monkey`` / ``keyword`` /
#    ``primary key: id`` stay CLEAR (the anchor needs the underscore
#    separator), so the widening is fail-safe without destroying
#    diagnostic value.  NOTE the anchor is NARROWER than
#    ``env_key_policy``'s substring test (which classifies ``monkey``
#    as secret-shaped): the masker keeps diagnostics readable, the
#    write/read policy keeps the surface closed.  A bare ``key`` /
#    ``auth`` / ``private`` word is NOT carried (it would mask
#    ``auth flow`` / ``private channel``).
#    Each family member carries an OPTIONAL QUOTE on either side of
#    the name and a TRAILING WORD SEGMENT (R-a: the keyword masks the
#    FAMILY — ``secret_note`` / ``password_hint`` /
#    ``client_secret_id`` are all masked; over-masking is the fail-safe
#    direction).
#
#    The VALUE class is QUOTE-AWARE (E1): ``(?:"[^"]*"|'[^']*'|\S+)``.
#    The former whitespace-excluding class masked a quoted value only
#    up to its first SPACE, so a PASSPHRASE — the most common DB/API
#    credential shape — leaked its tail in cleartext.
#    The captured NAME is the FULL key name (prefix + keyword + suffix)
#    so the E3 benign-name denylist can be applied to it.
_KEYWORD_SECRET_RE = re.compile(
    r"(?i)"
    r"([\"']?)"                                            # 1: optional opening quote
    r"([A-Za-z0-9_]*"                                      # 2: FULL key name
    r"(?:api[_-]?key|token|password|secret|access[_-]?key|credential|pwd|private[_-]?key"
    r"|_key|_auth)"
    r"[a-z_]*)"
    r"([\"']?\s*[:=]\s*)"                                  # 3: closing quote + separator
    r"(\"[^\"]*\"|'[^']*'|\S+)"                            # 4: the value
)

# 4. URL query-parameter secrets (``?api_key=…``).
_QUERY_SECRET_RE = re.compile(
    r"(?i)(\?[^\s]*(?:key|token|secret)=)([^&\s]+)"
)

# 5. Environment-variable shapes — ``*_API_KEY=`` / ``*_TOKEN=`` /
#    ``*_PASSWORD=`` / ``*_SECRET=`` / ``*_CREDENTIAL=`` / ``*_PWD=`` /
#    ``*_PRIVATE_KEY=`` (the W2 council widening — the same three
#    secret-shaped words the keyword FAMILY gained).
#    REDUNDANT-BY-CONSTRUCTION (code-council delta, 2026-11-05): the
#    keyword FAMILY above already matches the env form (a
#    case-insensitive name followed by ``=``), so this branch normally
#    never fires — it is kept as a second, narrower net and must stay
#    in sync with the family it mirrors.
_ENV_SECRET_RE = re.compile(
    r"(?i)([A-Za-z0-9_]*_(?:API_KEY|TOKEN|PASSWORD|SECRET|CREDENTIAL|PWD|PRIVATE_KEY)\s*=\s*)(\S+)"
)

#: The E3 benign-name denylist — the TOKEN-METERING diagnostics. These
#: are the fields of a token-exhaustion error, i.e. exactly the text
#: the excerpt exists to capture; masking them would destroy the
#: diagnostic value the house doctrine protects (see the module
#: docstring: "only patterns that are unambiguous database
#: identifiers"). ``*_tokens`` covers the plural family.
_BENIGN_TOKEN_NAMES: frozenset[str] = frozenset(
    {
        "max_tokens",
        "input_tokens",
        "output_tokens",
        "token_budget",
        "token_count",
    }
)


def _is_benign_token_name(name: str) -> bool:
    """True when the matched key name is a token-metering field (E3)."""
    lowered = name.lower()
    if lowered in _BENIGN_TOKEN_NAMES:
        return True
    return lowered.endswith("_tokens")


def _mask_keyword_secret(match: re.Match[str]) -> str:
    """Replacement for :data:`_KEYWORD_SECRET_RE` — masks the VALUE.

    The KEY is preserved (a redacted dump stays diagnosable — the key
    NAME is the signal, the value is the secret). A benign
    token-metering name (E3) is returned UNCHANGED.
    """
    open_quote, name, separator, value = match.groups()
    if _is_benign_token_name(name):
        return match.group(0)
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        masked = f"{value[0]}***{value[-1]}"
    else:
        masked = "***"
    return f"{open_quote}{name}{separator}{masked}"


def redact_secret_shapes(text: object, limit: int = 200) -> str:
    """Mask the SECRET-VALUE shapes in ``text`` (the AM-11 fence).

    ADDITIVE sibling of :func:`redact_exc_str` — it does NOT replace
    it and does NOT touch its callers. The two are composed by the
    caller, secret-shapes FIRST (it must see the FULL text), then the
    identifier masker.

    Masked shapes:

    1. ``sk-…`` provider keys.
    2. ``Bearer <token>``.
    3. The keyword FAMILY (``api_key`` / ``token`` / ``password`` /
       ``secret`` / ``access_key`` / ``credential`` / ``pwd`` /
       ``private_key`` PLUS the suffix-anchored ``*_key`` / ``*_auth``
       class — ``encryption_key`` / ``SIGNING_KEY`` / ``auth_key`` /
       ``SSH_KEY`` / ``MCP_AUTH`` — while ``monkey`` / ``keyword`` /
       ``primary key: id`` stay clear; with an optional quote on either
       side of the name and a trailing word segment) — the value is
       masked WHOLE, including a quoted multi-word passphrase (E1).
    4. URL query-parameter secrets.
    5. Environment-variable shapes (``*_API_KEY=`` / ``*_TOKEN=`` /
       ``*_PASSWORD=`` / ``*_SECRET=`` / ``*_CREDENTIAL=`` /
       ``*_PWD=`` / ``*_PRIVATE_KEY=``). Defence-in-depth: the keyword
       FAMILY already matches the env form (case-insensitive name +
       ``=``), so this branch is redundant-by-construction for every
       name the family covers — kept as a second, narrower net.

    NOT masked (E3): the token-metering diagnostics — ``max_tokens``,
    ``input_tokens``, ``output_tokens``, ``token_budget``,
    ``token_count`` and the ``*_tokens`` family. They are the fields of
    a token-exhaustion error — precisely the text an excerpt exists to
    capture.

    Args:
        text: Any object; a non-string is stringified defensively.
        limit: Maximum returned length — applied AFTER the masking (the
            house ``redact_exc_str`` contract: the cap must never
            pre-empt the masker, or a value straddling the cap boundary
            would be cut into an unmatchable fragment and survive).

    Returns:
        The masked text, truncated to ``limit``. Never raises: on an
        internal error the plain truncated string is returned.
    """
    try:
        try:
            source = text if isinstance(text, str) else str(text)
        except Exception:  # noqa: BLE001 — an unstringifiable input
            return "<unstringifiable>"[:limit] if limit > 0 else ""
        masked = _SK_KEY_RE.sub("***", source)
        masked = _BEARER_RE.sub(r"\1***", masked)
        masked = _KEYWORD_SECRET_RE.sub(_mask_keyword_secret, masked)
        masked = _QUERY_SECRET_RE.sub(r"\1***", masked)
        masked = _ENV_SECRET_RE.sub(r"\1***", masked)
        return masked if len(masked) <= limit else masked[:limit]
    except Exception:  # pragma: no cover — redaction must never break logging
        try:
            return str(text)[:limit]
        except Exception:
            return "<unstringifiable>"

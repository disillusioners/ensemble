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
"""

from __future__ import annotations

import re

__all__ = ["redact_exc_str"]

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

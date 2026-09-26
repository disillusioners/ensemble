"""Root-key hardening for ``SYSTEM_ENCRYPTION_KEY`` (P3-WP13a, designer-agent).

Day-1 lightweight hardening per the §10.1 triage verdict in
``.agents/shared/planning/designer-agent/implementation-plan/phase3-bootstrap-kms-lite.md``:

* file permission mode ``<= 0o600`` (no group/other read, write, or exec);
* file mtime age strictly less than the configured threshold (default
  90 days, configurable via ``SYSTEM_ENCRYPTION_KEY_MAX_AGE_DAYS``);
* file exists and is readable (clear, fail-closed message otherwise).

When ``SYSTEM_ENCRYPTION_KEY`` is set INLINE (no file), perm/age checks
are impossible — the function reports ``ok=True`` with a note that the
operator must read ``docs/operations/kms-lite-hardening.md`` to
understand the residual risk. Inline acceptance is day-1 only; OS-keychain
integration is deferred until ``§7.5a`` (policy / rotation lands —
PD-15 revisit trigger).

OS-keychain integration is explicitly **NOT** included in day-1 surface
— macOS Keychain / Linux libsecret / Windows DPAPI would explode the
day-1 surface and there is no rotation / policy layer to consume a
keychain key yet. The revisit trigger is documented in
``docs/operations/kms-lite-hardening.md``.

Security invariants — never violated:

1. The key value NEVER appears in the return value, in any violation
   message, or in any log line emitted by this module. The function
   inspects metadata (mode, mtime, existence) only.
2. The function never crashes the daemon — on internal errors it
   returns a fail-closed ``KeyHardeningResult`` so the caller (the
   mint-time gate in ``daemon.services.kms_lite``) can refuse to issue
   a handle with a structured, actionable message.
3. Failures are reported as a list of structured violations (each with
   a stable ``code`` for downstream tooling / logs) plus a human-readable
   ``message`` for operators.
"""

from __future__ import annotations

import logging
import os
import stat
import time
from dataclasses import dataclass, field
from typing import Final

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public config surface (env var names + defaults)
# ---------------------------------------------------------------------------

#: When set, the KMS-Lite store reads the key from this file rather than
#: from the inline ``SYSTEM_ENCRYPTION_KEY`` env var. Trailing whitespace
#: (incl. trailing newline) is stripped.
SYSTEM_ENCRYPTION_KEY_FILE_ENV: Final[str] = "SYSTEM_ENCRYPTION_KEY_FILE"

#: Canonical inline key env var (read by the store at boot).
SYSTEM_ENCRYPTION_KEY_ENV: Final[str] = "SYSTEM_ENCRYPTION_KEY"

#: Maximum acceptable age of the key file in days. Files older than this
#: threshold trigger a ``KEY_FILE_STALE`` violation. Default 90 days.
SYSTEM_ENCRYPTION_KEY_MAX_AGE_DAYS_ENV: Final[str] = "SYSTEM_ENCRYPTION_KEY_MAX_AGE_DAYS"

#: Default max-age threshold when the env var is unset or empty.
DEFAULT_MAX_AGE_DAYS: Final[int] = 90

#: Maximum file mode permitted. Group / other bits beyond this are a
#: violation. ``0o600`` = owner read+write only.
MAX_PERMISSIBLE_MODE: Final[int] = 0o600

#: Permission bits that MUST be absent (group + other read/write/exec).
#: A file is "safe" when ``stat.S_IMODE(mode) & _WORLD_GROUP_MASK == 0``.
_WORLD_GROUP_MASK: Final[int] = 0o077


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KeyHardeningViolation:
    """A single fail-closed violation of the key-source contract.

    The ``code`` field is the stable identifier operators should grep
    for; downstream alerting can pin exact equality on these codes. The
    ``detail`` field is a human-readable explanation that contains NO
    key material (never fragments, prefixes, or hex digests of the key).
    """

    code: str
    detail: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.code}: {self.detail}"


#: Stable violation codes — pin equality in tests + downstream alerts.
KEY_FILE_MISSING: Final[str] = "KEY_FILE_MISSING"
KEY_FILE_NOT_READABLE: Final[str] = "KEY_FILE_NOT_READABLE"
KEY_FILE_PERMS: Final[str] = "KEY_FILE_PERMS"
KEY_FILE_STALE: Final[str] = "KEY_FILE_STALE"
KEY_NOT_SET: Final[str] = "KEY_NOT_SET"


@dataclass(frozen=True)
class KeyHardeningResult:
    """Structured outcome of :func:`validate_key_source`.

    Attributes:
        ok: ``True`` iff the caller may proceed with a mint. The mint-time
            gate refuses to issue a handle when ``ok is False``.
        source: Where the key was found — ``"file"``, ``"inline"``, or
            ``"none"``. When ``"none"`` is returned, ``ok`` is always
            ``False`` (no key at all). The KMS-Lite store also raises
            on the unset path independently; this signal is for the
            hardening gate to surface the same fact with a structured
            code.
        violations: Empty when ``ok is True``. Each entry has a stable
            ``code`` for downstream tooling and a detail string for
            operators.
        notes: Non-fatal advisory notes (e.g. inline-key acceptance
            warning). NEVER carries a ``code``; the operator should
            treat these as informational.
        message: Human-readable summary — safe to log / surface to the
            escalation envelope. NEVER contains the key value or any
            fragment thereof.

    The result is intentionally a plain dataclass (not a Pydantic model)
    so the KMS-Lite store does not pull in additional dependencies and
    so the type stays trivially JSON-serialisable for the escalation
    envelope.
    """

    ok: bool
    source: str
    violations: list[KeyHardeningViolation] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    message: str = ""

    def __post_init__(self) -> None:  # pragma: no cover - invariant
        if self.source not in {"file", "inline", "none"}:
            raise ValueError(
                f"KeyHardeningResult.source must be file|inline|none, got {self.source!r}"
            )
        if self.ok and self.violations:
            raise ValueError(
                "KeyHardeningResult.ok=True is inconsistent with non-empty violations"
            )
        if not self.ok and not self.violations and self.source != "none":
            # source=='none' is already covered by KEY_NOT_SET in the
            # caller-visible violation list below; this guard catches
            # future regressions where ok=False with empty violations
            # would otherwise be a no-op refusal.
            raise ValueError(
                "KeyHardeningResult.ok=False requires non-empty violations "
                "unless source='none' (KEY_NOT_SET)"
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_max_age_days(raw: str | None) -> int:
    """Parse the max-age-days env var; fall back to default on bad input.

    Day-1 acceptance: a malformed value falls back to the default and
    emits a WARNING log line. This is consistent with the inline-key
    pattern (warn-but-accept) and avoids breaking the daemon on a typo.
    The function never raises — fail-soft on input parsing, fail-closed
    on the substantive checks (the actual perm / mtime enforcement).
    """
    if raw is None or not raw.strip():
        return DEFAULT_MAX_AGE_DAYS
    try:
        value = int(raw.strip())
    except ValueError:
        logger.warning(
            "key_hardening: %s=%r is not an int; falling back to %d days",
            SYSTEM_ENCRYPTION_KEY_MAX_AGE_DAYS_ENV,
            raw,
            DEFAULT_MAX_AGE_DAYS,
        )
        return DEFAULT_MAX_AGE_DAYS
    if value <= 0:
        # Zero or negative would make every file stale — log and fall
        # back. Operators who want "any age" should pass a very large
        # value, not zero.
        logger.warning(
            "key_hardening: %s=%d must be > 0; falling back to %d days",
            SYSTEM_ENCRYPTION_KEY_MAX_AGE_DAYS_ENV,
            value,
            DEFAULT_MAX_AGE_DAYS,
        )
        return DEFAULT_MAX_AGE_DAYS
    return value


def _env_lookup(env: dict[str, str], name: str) -> str | None:
    """Read ``name`` from ``env``; treat empty string as unset."""
    raw = env.get(name)
    if raw is None or not raw.strip():
        return None
    return raw


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_key_source(
    env: dict[str, str] | None = None,
    *,
    now: float | None = None,
) -> KeyHardeningResult:
    """Validate the custody of ``SYSTEM_ENCRYPTION_KEY`` for KMS-Lite.

    Decision matrix (per P3-WP13a §10.1 verdict):

    +-----------------------------+--------+--------------------------------+
    | Key source                  | ok?    | Reason                         |
    +=============================+========+================================+
    | ``SYSTEM_ENCRYPTION_KEY_    | depends| File perms must be ``<= 0o600`` |
    | FILE`` set, file valid      |        | and mtime younger than the     |
    |                             |        | configured threshold.          |
    +-----------------------------+--------+--------------------------------+
    | ``SYSTEM_ENCRYPTION_KEY``   | True   | Inline key — no perm / age     |
    | inline only (no file)       |        | checks possible; day-1 accept  |
    |                             |        | with note. See ops doc.         |
    +-----------------------------+--------+--------------------------------+
    | Neither set                 | False  | Caller (the mint-time gate)    |
    |                             |        | MUST refuse to issue — KMS-Lite |
    |                             |        | raises ``KMSUnavailableError`` |
    |                             |        | on the existing unset path.    |
    +-----------------------------+--------+--------------------------------+

    Args:
        env: Optional env-var mapping (defaults to ``os.environ``). Tests
            pass a ``monkeypatch``-shaped dict; production code lets it
            default.
        now: Optional current time in seconds-since-epoch (defaults to
            ``time.time()``). Tests pass a fixed value to make the
            mtime-age check deterministic without ``os.utime`` on the
            fixture.

    Returns:
        A :class:`KeyHardeningResult`. Never raises — failures surface
        as ``ok=False`` with a populated ``violations`` list.

    Notes:
        The function NEVER reads the key value itself (only metadata
        about the file). The result and all log messages are
        key-material-free by construction.
    """
    if env is None:
        env = dict(os.environ)
    if now is None:
        now = time.time()

    file_path = _env_lookup(env, SYSTEM_ENCRYPTION_KEY_FILE_ENV)
    inline_key = _env_lookup(env, SYSTEM_ENCRYPTION_KEY_ENV)
    max_age_days = _parse_max_age_days(
        _env_lookup(env, SYSTEM_ENCRYPTION_KEY_MAX_AGE_DAYS_ENV)
    )

    # ---- FILE PATH -----------------------------------------------------
    if file_path is not None:
        violations: list[KeyHardeningViolation] = []
        notes: list[str] = []

        # Existence + readability check first — most actionable error
        # message for the common "I forgot to provision the file"
        # case.
        if not os.path.exists(file_path):
            violations.append(
                KeyHardeningViolation(
                    code=KEY_FILE_MISSING,
                    detail=(
                        f"{SYSTEM_ENCRYPTION_KEY_FILE_ENV}={file_path!r} "
                        "does not exist"
                    ),
                )
            )
        elif not os.access(file_path, os.R_OK):
            violations.append(
                KeyHardeningViolation(
                    code=KEY_FILE_NOT_READABLE,
                    detail=(
                        f"{SYSTEM_ENCRYPTION_KEY_FILE_ENV}={file_path!r} "
                        "exists but is not readable by the daemon process"
                    ),
                )
            )
        else:
            try:
                st = os.stat(file_path)
            except OSError as exc:
                # Race: file existed at the exists/readable check but
                # vanished (or perms flipped) by the time we stat()ed.
                # Fail-closed with a structured message.
                violations.append(
                    KeyHardeningViolation(
                        code=KEY_FILE_NOT_READABLE,
                        detail=(
                            f"{SYSTEM_ENCRYPTION_KEY_FILE_ENV}={file_path!r} "
                            f"stat() failed: {exc.__class__.__name__}"
                        ),
                    )
                )
            else:
                mode = stat.S_IMODE(st.st_mode)
                if mode & _WORLD_GROUP_MASK:
                    violations.append(
                        KeyHardeningViolation(
                            code=KEY_FILE_PERMS,
                            detail=(
                                f"{SYSTEM_ENCRYPTION_KEY_FILE_ENV}={file_path!r} "
                                f"mode=0o{mode:o} is wider than 0o600 "
                                "(group/other bits set)"
                            ),
                        )
                    )
                age_seconds = now - st.st_mtime
                age_days = age_seconds / 86400.0
                if age_days >= max_age_days:
                    violations.append(
                        KeyHardeningViolation(
                            code=KEY_FILE_STALE,
                            detail=(
                                f"{SYSTEM_ENCRYPTION_KEY_FILE_ENV}={file_path!r} "
                                f"is {age_days:.1f} days old "
                                f"(threshold: {max_age_days} days)"
                            ),
                        )
                    )

        if inline_key is not None:
            notes.append(
                "inline SYSTEM_ENCRYPTION_KEY is also set; the file path "
                "takes precedence (the inline value is shadowed for "
                "this run)"
            )

        if violations:
            codes = ", ".join(sorted({v.code for v in violations}))
            return KeyHardeningResult(
                ok=False,
                source="file",
                violations=violations,
                notes=notes,
                message=(
                    f"key hardening refusal: file source failed [{codes}]; "
                    "see violations list"
                ),
            )

        # File path + everything green.
        return KeyHardeningResult(
            ok=True,
            source="file",
            violations=[],
            notes=notes,
            message=(
                f"key hardening ok: file source valid "
                f"(mode=<=0o600, age<{max_age_days}d)"
            ),
        )

    # ---- INLINE PATH ---------------------------------------------------
    if inline_key is not None:
        return KeyHardeningResult(
            ok=True,
            source="inline",
            violations=[],
            notes=[
                (
                    "inline SYSTEM_ENCRYPTION_KEY is set; file-level "
                    "perm/age checks are not possible. Residual risk: "
                    "the key may appear in process listings, shell "
                    "history, or env dumps. Migrate to a 0o600 key file "
                    "when convenient — see "
                    "docs/operations/kms-lite-hardening.md."
                )
            ],
            message="key hardening ok-with-note: inline key (no file)",
        )

    # ---- NEITHER -------------------------------------------------------
    return KeyHardeningResult(
        ok=False,
        source="none",
        violations=[
            KeyHardeningViolation(
                code=KEY_NOT_SET,
                detail=(
                    f"neither {SYSTEM_ENCRYPTION_KEY_FILE_ENV} nor "
                    f"{SYSTEM_ENCRYPTION_KEY_ENV} is set"
                ),
            )
        ],
        notes=[],
        message="key hardening refusal: no key source configured",
    )

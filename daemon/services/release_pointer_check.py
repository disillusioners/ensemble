"""Boot-time release-pointer invariant check (relcur-pointer M2).

CHEAP, LOUD, NON-BLOCKING.  This module answers one question at boot: do
the two ``current`` pointers agree with the journal?

Layout (live install)::

    <install>/current                 -> releases/vX.Y.Z   (install-root current)
    <install>/releases/current        -> vX.Y.Z            (convenience/fence alias)
    <install>/releases/state.json     {"current": "vX.Y.Z", ...}

The daemon executable resolves through the INSTALL-ROOT current (launcher
contract); the ``releases/current`` alias is a convenience/fence pointer.
A drift between them (or against the journal) is METADATA drift, not an
outage.  Blocking boot would convert that drift into an availability loss,
so this module only LOGS (WARNING with the greppable token
``release-pointer-drift``) and surfaces a flag via :func:`last_boot_check`
for operator repair (sibling module M1 lands the repair script).  It never
writes the journal, never speaks HTTP, and touches only the filesystem +
JSON, completing in well under 100 ms.

Import is SIDE-EFFECT-FREE: nothing runs until :func:`check_release_pointer`
(or the boot helper :func:`run_boot_check`) is called explicitly.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "ReleasePointerCheckResult",
    "check_release_pointer",
    "run_boot_check",
    "last_boot_check",
    "DRIFT_TOKEN",
]

# Greppable token carried by every drift WARNING.
DRIFT_TOKEN = "release-pointer-drift"

# Last result observed by the boot helper.  None until run_boot_check() runs
# (or a caller explicitly records one).  Read via last_boot_check().
_LAST_BOOT_CHECK: ReleasePointerCheckResult | None = None


class ReleasePointerCheckResult(dict):
    """Structured result of :func:`check_release_pointer`.

    A ``dict`` subclass (so ``result["ok"]`` and dict equality work) that
    ALSO supports attribute access (``result.ok``) for callers that prefer
    it.  Keys mirror the module brief verbatim.
    """

    __slots__ = ()

    def __init__(self, **fields: Any) -> None:
        super().__init__(fields)

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:  # pragma: no cover - standard attr protocol
            raise AttributeError(name) from exc


def _basename(target: str | None) -> str | None:
    """Release name from a symlink target, tolerating trailing slashes."""
    if not target:
        return None
    stripped = target.rstrip("/")
    return stripped.rsplit("/", 1)[-1] or None


def _journal_current(install_dir: Path) -> str | None:
    """Tolerant READ of ``releases/state.json`` -> ``current`` value.

    Returns None for an absent / empty / torn / non-object document or an
    unreadable file.  NEVER writes, NEVER locks, NEVER raises.
    """
    jp = install_dir / "releases" / "state.json"
    try:
        if not jp.is_file():
            return None
        raw = jp.read_text(encoding="utf-8")
    except OSError:
        return None
    if not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    value = data.get("current")
    return value if isinstance(value, str) and value else None


def _readlink(path: Path) -> str | None:
    """os.readlink a symlink, returning None when absent/unreadable/not one."""
    try:
        if not path.is_symlink():
            return None
        return os.readlink(path)
    except OSError:
        return None


def check_release_pointer(install_dir: Path | str | None) -> ReleasePointerCheckResult:
    """Check the release-pointer invariant for ``install_dir`` (pure, sync).

    Reads only the filesystem + JSON (no HTTP, no journal writes).  Returns
    a :class:`ReleasePointerCheckResult` carrying:

    journal_current, top_target, alias_present, alias_target, alias_dangling,
    alias_matches_journal, top_matches_journal, ok, reasons

    ``top_target`` / ``alias_target`` are the RAW ``os.readlink`` values
    (e.g. ``"releases/v0.31.42"`` and ``"v0.31.42"``).  Matching compares
    their basenames against ``journal_current``.
    """
    root = Path(install_dir) if install_dir is not None else None

    journal_current: str | None = None
    top_target: str | None = None
    alias_present = False
    alias_target: str | None = None
    alias_dangling = False
    top_dangling = False

    if root is not None:
        journal_current = _journal_current(root)
        top_target = _readlink(root / "current")
        alias_path = root / "releases" / "current"
        alias_target = _readlink(alias_path)
        alias_present = alias_target is not None

        if alias_target is not None:
            # The alias lives in <root>/releases, so its target is relative
            # to that directory (an absolute target stays absolute).
            alias_resolved = root / "releases" / alias_target
            try:
                alias_dangling = not alias_resolved.is_dir()
            except OSError:
                alias_dangling = True
        if top_target is not None:
            # The install-root current lives in <root>, so its target
            # ("releases/vX.Y.Z") is relative to <root>.
            top_resolved = root / top_target
            try:
                top_dangling = not top_resolved.is_dir()
            except OSError:
                top_dangling = True

    alias_name = _basename(alias_target)
    top_name = _basename(top_target)
    has_journal = bool(journal_current)

    alias_matches_journal = has_journal and alias_name == journal_current
    top_matches_journal = has_journal and top_name == journal_current

    reasons: list[str] = []
    if not has_journal:
        # Fresh install / torn journal: NOT silently satisfied — say so.
        reasons.append(
            "nothing-to-check: journal current is null/absent "
            "(fresh install, pre-first-promote, or unreadable state.json)"
        )
        if root is None:
            reasons.append(
                "nothing-to-check: no install dir resolved (dev/unresolved "
                "boot); no pointers probed"
            )
    else:
        if not alias_present:
            reasons.append(
                f"alias-absent: releases/current symlink missing "
                f"while journal current={journal_current}"
            )
        if alias_dangling:
            reasons.append(
                f"alias-dangling: releases/current -> {alias_target} "
                f"resolves to a missing directory"
            )
        elif alias_present and not alias_matches_journal:
            reasons.append(
                f"alias-mismatch: releases/current -> {alias_name} "
                f"!= journal current={journal_current}"
            )
        if top_target is None:
            reasons.append(
                f"top-absent: install-root current symlink missing "
                f"while journal current={journal_current}"
            )
        elif top_dangling:
            reasons.append(
                f"top-dangling: current -> {top_target} resolves to a missing directory"
            )
        elif not top_matches_journal:
            reasons.append(
                f"top-mismatch: current -> {top_name} "
                f"!= journal current={journal_current}"
            )

    warn_reasons = [r for r in reasons if not r.startswith("nothing-to-check")]
    ok = not warn_reasons

    return ReleasePointerCheckResult(
        journal_current=journal_current,
        top_target=top_target,
        alias_present=alias_present,
        alias_target=alias_target,
        alias_dangling=alias_dangling,
        alias_matches_journal=bool(alias_matches_journal),
        top_matches_journal=bool(top_matches_journal),
        ok=ok,
        reasons=reasons,
    )


def last_boot_check() -> ReleasePointerCheckResult | None:
    """Return the last result recorded by :func:`run_boot_check` (or None).

    Read-only accessor for existing health surfaces; adding an endpoint is
    explicitly out of scope — readyz stays green and untouched.
    """
    return _LAST_BOOT_CHECK


def _resolve_install_dir() -> Path | None:
    """Resolve this daemon's install dir via the existing SELF-ENV machinery.

    LAZY import (no import cycle / no module-import side effect).  Deliberately
    has NO canonical-live fallback: a dev/unresolved boot resolves to None and
    the check reports "nothing-to-check" instead of claiming the live root.
    """
    try:
        from daemon.tools.upgrade_tools import (  # noqa: PLC0415 - lazy by design
            _resolve_install_dir as _resolve,
            _self_env_marker,
        )

        resolved = _resolve(_self_env_marker())
        return Path(resolved) if resolved is not None else None
    except Exception:  # noqa: BLE001 - resolution is advisory, never fatal
        return None


def run_boot_check(install_dir: Path | str | None = None) -> ReleasePointerCheckResult:
    """Run the check ONCE at boot: cache the result and log drift/clean.

    When ``install_dir`` is omitted it is resolved from the daemon's own
    SELF-ENV identity (mirrors the boot wiring in ``daemon/api.py``).  Never
    raises: any unexpected failure is logged as a drift WARNING, so the
    detection net stays loud rather than silently disappearing.
    """
    global _LAST_BOOT_CHECK  # noqa: PLW0603 - single boot-time cache write
    try:
        target = _resolve_install_dir() if install_dir is None else install_dir
        result = check_release_pointer(target)
    except Exception as exc:  # noqa: BLE001 - fail LOUD, never block boot
        result = ReleasePointerCheckResult(
            journal_current=None,
            top_target=None,
            alias_present=False,
            alias_target=None,
            alias_dangling=False,
            alias_matches_journal=False,
            top_matches_journal=False,
            ok=False,
            reasons=[f"check-error: {type(exc).__name__}: {exc}"],
        )

    _LAST_BOOT_CHECK = result

    if result.ok:
        if any(r.startswith("nothing-to-check") for r in result.reasons):
            logger.info(
                "[release-pointer-check] nothing-to-check: %s",
                "; ".join(result.reasons),
            )
        else:
            logger.info(
                "[release-pointer-check] ok: journal_current=%s top=%s alias=%s",
                result.journal_current,
                result.top_target,
                result.alias_target,
            )
    else:
        logger.warning(
            "%s: journal_current=%s top_target=%s alias_target=%s reasons=%s",
            DRIFT_TOKEN,
            result.journal_current,
            result.top_target,
            result.alias_target,
            "; ".join(result.reasons),
        )
    return result

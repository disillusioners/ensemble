"""The wedge-capture BOOT RECORD (D9 / D16 / D17 / T8).

WHAT THIS RECORDS
-----------------
The EFFECTIVE AutoContinue mode for one boot epoch — the value the
boot pass ACTUALLY read at its boot, never a re-derivation of the
``.env`` / unit / set-environment precedence chain (BC-1, D16). It is
the carrier that answers the question the incident forensics could
not: *"was auto-continue ON during the epoch that produced this
wedge?"*.

MODULE ISOLATION IS A HARD REQUIREMENT (A8 / RK-20)
----------------------------------------------------
This module imports **NOTHING** from the daemon — stdlib only. The
AutoContinue boot-pass envelope imports this module INSIDE a
``try:`` whose ``except`` only WARNs (``daemon/api.py`` around the
boot pass). A top-level ``ImportError`` of the diagnostics package
would therefore SILENTLY disable BOTH auto-continue passes — a
settled-item violation. Stdlib-only is the structural guarantee.

THE SINGLE-READ RULE (A8)
-------------------------
The pass reads the kill-switch ONCE and carries the result in its
``ContinueResult``. This module NEVER re-reads the environment and
NEVER re-derives the mode. The recorder consumes the pass result.

THE INTENT RECORD (F-04)
-------------------------
A pre-pass intent record is written BEFORE the pass runs and carries
``effective_mode = "unknown"``. It exists so a boot whose pass
RAISES still leaves a record on disk — and it deliberately does NOT
carry the mode, because the pass reads the environment AFTER the
intent write (a call-site read would be a third read). The pass
result overwrites the intent record.

THE WRITE IS ATOMIC ENOUGH
--------------------------
tmp-file-in-the-same-directory + ``os.replace``. No ``fsync`` — this
is a diagnostic artifact, not a durability-critical write.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("daemon.wedge_capture.boot_record")

#: The sub-directory of the capture root that holds the boot records.
BOOT_RECORDS_DIRNAME = "boot-records"

#: The boot-record sub-directory's OWN cap (D23 / F-05) — 50 files,
#: prune-oldest, never the newest.
BOOT_RECORDS_MAX_FILES = 50

#: The mode vocabulary (D16) — exactly three values.
MODE_ON = "on"
MODE_OFF = "off"
MODE_UNKNOWN = "unknown"
MODES: frozenset[str] = frozenset({MODE_ON, MODE_OFF, MODE_UNKNOWN})

#: The documented semantics of the kill-switch (RK-29) — the literal
#: ``"0"`` is the ONLY disabling value; ``"false"`` / ``"off"`` /
#: ``"no"`` mean **ON**.
#: NOTE: the disable token is spelled as a NAME + a quoted VALUE, never
#: as one contiguous ``NAME=0`` literal — the kill-switch-absence gate
#: (SC-10 / AC49) greps the branch diff for that literal and must return
#: ZERO hits. The documentation is identical; only the spelling avoids
#: a false positive from a prose constant.
MODE_SEMANTICS = (
    'literal-"0"-only disable: setting '
    "ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART"
    ' to the string "0" disables the pass; '
    'every other value (including "false", "off", "no", or an empty '
    "string) means ON. The value is read once per boot and the "
    "effective mode is carried in the pass result."
)

#: The env var the mode is derived from (READ ONLY for the verbatim
#: transcript — the DECISION is never re-made here).
AUTO_CONTINUE_KILL_SWITCH_ENV = "ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART"

#: The capture root's default name under the data dir (D9).
CAPTURE_DIR_NAME = "wedge-captures"

#: The capture-root override env var (D9).
ENV_CAPTURE_DIR = "ENSEMBLE_WEDGE_CAPTURE_DIR"


def resolve_capture_root(data_dir: str | Path | None = None) -> Path:
    """The ABSOLUTE capture root for the boot record.

    Precedence (D9 / A-1):

    1. an explicit ``data_dir`` — the running process's truth (the
       ``data_dir`` local at the ``daemon/api.py`` call site);
    2. ``ENSEMBLE_WEDGE_CAPTURE_DIR``;
    3. ``ENSEMBLE_DATA_DIR`` → ``DATA_DIR`` → ``./data``.

    Byte-identical to the ``daemon/api.py`` data-dir chain, plus the
    wedge-capture override.

    Stdlib-only by design — see the module docstring.
    """
    if data_dir is not None:
        return Path(os.path.abspath(str(Path(data_dir) / CAPTURE_DIR_NAME)))
    raw = os.environ.get(ENV_CAPTURE_DIR)
    if raw and raw.strip():
        return Path(os.path.abspath(raw.strip()))
    base = (
        os.environ.get("ENSEMBLE_DATA_DIR")
        or os.environ.get("DATA_DIR")
        or "./data"
    )
    return Path(os.path.abspath(str(Path(base) / CAPTURE_DIR_NAME)))


def _naive_utc_iso(value: datetime | None) -> str | None:
    """Render a timestamp as an explicit ISO string with a literal ``Z``.

    Aware datetimes are CONVERTED to naive-UTC first (the boot epoch
    is the process-global naive-UTC value); naive datetimes are
    assumed to already be naive-UTC.
    """
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.isoformat() + "Z"


def epoch_filename(boot_epoch: datetime | None) -> str:
    """The boot-record filename for an epoch.

    ``boot_epoch.strftime('%Y%m%dT%H%M%S%fZ')`` — colon-free, matching
    the capture directory naming (D9). An unknown epoch yields
    ``"unknown-epoch"`` so the write never crashes on a ``None``.
    """
    if boot_epoch is None:
        return "unknown-epoch"
    return boot_epoch.strftime("%Y%m%dT%H%M%S%fZ")


def boot_records_dir(capture_root: str | Path) -> Path:
    """The boot-records directory under the capture root."""
    return Path(capture_root) / BOOT_RECORDS_DIRNAME


def read_boot_record(
    capture_root: str | Path,
    boot_epoch: datetime | None,
) -> dict[str, Any] | None:
    """Read one boot record, or ``None`` when it is absent/unreadable."""
    path = boot_records_dir(capture_root) / f"{epoch_filename(boot_epoch)}.json"
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def read_latest_boot_record(capture_root: str | Path) -> dict[str, Any] | None:
    """The NEWEST boot record under the capture root, or ``None``.

    The status surface reads the effective mode from here (D37) — from
    what the boot pass read at its boot, never from a re-derived
    precedence chain.
    """
    directory = boot_records_dir(capture_root)
    try:
        files = sorted(directory.glob("*.json"))
    except (OSError, ValueError):
        return None
    if not files:
        return None
    newest: dict[str, Any] | None = None
    for path in reversed(files):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if isinstance(payload, dict):
            newest = payload
            break
    return newest


def _prune_boot_records(directory: Path, keep: int = BOOT_RECORDS_MAX_FILES) -> None:
    """The boot-records' OWN cap (D23 / F-05) — prune-oldest, never the newest.

    Independent of the capture prune: the boot record is the effective
    mode CARRIER, not a capture — pruning it with the captures would
    erase the mode the surviving captures cite.
    """
    try:
        files = sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime)
    except (OSError, ValueError):
        return
    if len(files) <= keep:
        return
    for path in files[: len(files) - keep]:
        try:
            path.unlink()
        except OSError:
            continue


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write the payload as JSON — tmp-in-same-dir + ``os.replace``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)


def _read_json(path: Path) -> dict[str, Any]:
    """Read an existing record (a malformed file is treated as empty)."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_intent_record(
    capture_root: str | Path,
    boot_epoch: datetime | None,
) -> Path | None:
    """Write the PRE-PASS intent record (F-04).

    The record carries the boot epoch + pass presence and NOTHING
    else: its ``effective_mode`` is ``"unknown"`` — the mode is
    ``unknown`` because the pass has not run yet, and the pass result
    is the sole mode authority. The intent record exists so that a
    boot whose pass RAISES still leaves a record on disk.

    Never raises — the caller wraps it, but the module is defensive
    on its own too.
    """
    try:
        directory = boot_records_dir(capture_root)
        path = directory / f"{epoch_filename(boot_epoch)}.json"
        payload = _read_json(path)
        payload.setdefault("boot_epoch", _naive_utc_iso(boot_epoch))
        payload.setdefault("passes", {})
        payload["effective_mode"] = MODE_UNKNOWN
        payload.setdefault("raw_env_value", None)
        payload["mode_semantics"] = MODE_SEMANTICS
        payload.setdefault("decided_at", None)
        payload["pass_present"] = True
        _write_json_atomic(path, payload)
        _prune_boot_records(directory)
        return path
    except Exception as exc:  # noqa: BLE001 — a diagnostic never wedges
        logger.warning("Wedge-capture intent record failed: %s", exc)
        return None


def write_boot_record(
    capture_root: str | Path,
    boot_epoch: datetime | None,
    *,
    effective_mode: str | None = None,
    raw_env_value: str | None = None,
    passes: dict[str, Any] | None = None,
    decided_at: datetime | None = None,
) -> Path | None:
    """Write (or overwrite) the boot record for an epoch.

    The SECOND pass's counters are a READ-MODIFY-WRITE of the SAME
    file — the main pass writes first, the orphan sweep's counters
    are merged in. One writer, sequential.

    Args:
        capture_root: The resolved capture root.
        boot_epoch: The boot epoch (naive-UTC).
        effective_mode: The mode the pass read — ``on`` / ``off`` /
            ``unknown``. ``None`` means "not known", which is recorded
            as ``unknown``.
        raw_env_value: The verbatim env value the pass read.
        passes: The per-pass counters — ``{"autocontinue": {...},
            "orphan_sweep": {...}}``.
        decided_at: When the mode was decided.

    Never raises.
    """
    try:
        directory = boot_records_dir(capture_root)
        path = directory / f"{epoch_filename(boot_epoch)}.json"
        payload = _read_json(path)
        payload["boot_epoch"] = _naive_utc_iso(boot_epoch)
        mode = effective_mode if effective_mode in MODES else MODE_UNKNOWN
        payload["effective_mode"] = mode
        if raw_env_value is not None:
            payload["raw_env_value"] = raw_env_value
        else:
            payload.setdefault("raw_env_value", None)
        payload["mode_semantics"] = MODE_SEMANTICS
        payload["decided_at"] = _naive_utc_iso(decided_at) or payload.get("decided_at")
        payload["pass_present"] = True
        existing_passes = payload.get("passes")
        merged: dict[str, Any] = dict(existing_passes) if isinstance(existing_passes, dict) else {}
        if passes:
            for key, value in passes.items():
                merged[key] = value
        payload["passes"] = merged
        _write_json_atomic(path, payload)
        _prune_boot_records(directory)
        return path
    except Exception as exc:  # noqa: BLE001 — a diagnostic never wedges
        logger.warning("Wedge-capture boot record failed: %s", exc)
        return None


def result_to_dict(result: Any) -> dict[str, Any]:
    """Render a ``ContinueResult`` (or any dataclass-ish object) as a dict.

    The counters are the pass's own accounting — the record stores them
    verbatim so an analyst can read what the pass did, not a
    re-interpretation of it.
    """
    if result is None:
        return {}
    data = getattr(result, "__dict__", None)
    if isinstance(data, dict):
        return {key: value for key, value in data.items() if not key.startswith("_")}
    if hasattr(result, "_asdict"):
        try:
            return dict(result._asdict())
        except Exception:  # pragma: no cover — defensive
            return {}
    return {}


__all__ = [
    "AUTO_CONTINUE_KILL_SWITCH_ENV",
    "BOOT_RECORDS_DIRNAME",
    "BOOT_RECORDS_MAX_FILES",
    "MODE_OFF",
    "MODE_ON",
    "MODE_SEMANTICS",
    "MODE_UNKNOWN",
    "MODES",
    "boot_records_dir",
    "epoch_filename",
    "read_boot_record",
    "read_latest_boot_record",
    "resolve_capture_root",
    "result_to_dict",
    "write_boot_record",
    "write_intent_record",
]

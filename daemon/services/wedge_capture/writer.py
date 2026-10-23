"""The file-first capture writer (D8 / D12 / D14 / D23 / A3 / T6 / T14).

FILE-FIRST, ROW-SECOND (D12)
----------------------------
Every artifact is written to disk and ``manifest.json`` is FINALISED
on disk BEFORE the metadata row is attempted. The row is the fragile
part (the DB may be unreachable, the pool exhausted, the disk full);
the FILES are the evidence. A capture whose row is missing but whose
files exist is still discoverable through the filesystem fallback.

THE FILE SET IS FIXED (D8)
--------------------------
``manifest.json``, ``stacks.txt``, ``threads.csv``, ``lag.json``,
``metrics.json``, ``db.json``, ``readyz.json``,
``checkpoint_perf_tail.json`` — and NOTHING else. ``readyz.json`` is
**OMITTED** when the readiness composite is not published yet (D25).

EVERY ARTIFACT WRITE IS INDIVIDUALLY GUARDED
---------------------------------------------
One failing artifact never loses the others, and the writer NEVER
raises. Each artifact's outcome is recorded in the manifest.

THE DEADLINE IS END-TO-END (D3 / A5 / W2)
------------------------------------------
The whole capture is bounded by ``deadline_s`` (20 s by default) —
measured from the CAPTURE'S ENTRY, not from the writer's own start:
the pre-writer phases (the DB sub-capture above all — a 10 s connect
plus three 5 s statements in the pre-fix shape) consume the SAME
budget the write phase spends. The caller passes its clock via
``started_monotonic``. A step whose budget is exhausted is **skipped**
and recorded as ``skipped:budget`` in the manifest — the capture still
completes. The DB steps are scaled to their remaining budget the same
way (``db_capture``), so no phase can run against a fresh clock.

RETENTION IS **DIRECTORY-AUTHORITATIVE** (D23 / A3 / F-05)
---------------------------------------------------------
The count / age / disk caps are computed by WALKING the capture
directory (mtime sort), never from the table: a missed row write
would otherwise hide an orphan directory forever — a direct reprise of
the unbounded-growth failure mode this commission exists to
instrument. Two EXCLUSIONS: ``boot-records/`` (the effective-mode
carrier, pruned by its own 50-file cap) and ``.pending_row.json`` (a
sidecar INSIDE a capture — it must never be deleted independently of
its parent capture, and must never be mistaken for a capture).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("daemon.wedge_capture.writer")

#: The D8 file set — FIXED (the read-back surface enumerates exactly
#: these and EXCLUDES the ``.pending_row.json`` sidecar).
CAPTURE_FILE_NAMES: tuple[str, ...] = (
    "manifest.json",
    "stacks.txt",
    "threads.csv",
    "lag.json",
    "metrics.json",
    "db.json",
    "readyz.json",
    "checkpoint_perf_tail.json",
)

#: The sidecar written when the metadata row could not be written.
PENDING_ROW_SIDECAR = ".pending_row.json"

#: The boot-records directory name — EXCLUDED from the capture prune.
BOOT_RECORDS_DIRNAME = "boot-records"

#: The threshold reason tokens.
SKIPPED_BUDGET = "skipped:budget"
SKIPPED_NA = "skipped:not_applicable"


@dataclass
class CaptureResult:
    """The outcome of one capture.

    Attributes:
        capture_id: The pre-allocated capture id (the directory name).
        capture_dir: The ABSOLUTE capture directory.
        ok: Whether the manifest was written (the capture is valid
            even when some artifacts failed).
        reason: The canonical trigger reason.
        trigger_source: ``manual`` or the automatic reason token.
        executor_native_id: The OS tid of the thread that executed the
            capture (the watchdog thread's tid for every capture).
        artifacts: The per-artifact outcome map.
        duration_s: The capture's wall time.
        manifest: The manifest payload (as written).
        row_written: Whether the metadata row was written.
    """

    capture_id: str
    capture_dir: Path
    ok: bool = False
    reason: str = ""
    trigger_source: str = ""
    executor_native_id: int | None = None
    artifacts: dict[str, Any] = field(default_factory=dict)
    duration_s: float = 0.0
    manifest: dict[str, Any] = field(default_factory=dict)
    row_written: bool = False

    def as_dict(self) -> dict[str, Any]:
        """A JSON-safe view of the result."""
        return {
            "capture_id": self.capture_id,
            "capture_dir": str(self.capture_dir),
            "ok": self.ok,
            "reason": self.reason,
            "trigger_source": self.trigger_source,
            "executor_native_id": self.executor_native_id,
            "artifacts": dict(self.artifacts),
            "duration_s": round(self.duration_s, 6),
            "row_written": self.row_written,
        }


def make_capture_id(
    *,
    captured_at: str,
    host: str,
    reason: str,
    suffix: str | None = None,
) -> str:
    """The capture directory name (D9).

    ``<UTC-ts>-<host>-<reason>-<uuid8>`` — e.g.
    ``20261022T050351Z-web01-sustained_cpu-3f2a91c4``. The timestamp
    and the host make the directory triage-able by ``ls`` alone.
    """
    import uuid

    token = suffix if suffix else uuid.uuid4().hex[:8]
    safe_host = "".join(ch if (ch.isalnum() or ch in ".-") else "_" for ch in (host or "unknown"))
    safe_reason = "".join(
        ch if (ch.isalnum() or ch in "._-") else "_" for ch in (reason or "manual")
    )
    return f"{captured_at}-{safe_host}-{safe_reason}-{token}"


def _write_text(path: Path, text: str) -> None:
    """Write a text artifact (the caller guards the failure)."""
    with open(path, "w", encoding="utf-8", errors="replace") as handle:
        handle.write(text)


def _write_json(path: Path, payload: Any) -> None:
    """Write a JSON artifact (the caller guards the failure)."""
    _write_text(path, json.dumps(payload, indent=2, sort_keys=True, default=str))


def render_threads_csv(threads: list[dict[str, Any]]) -> str:
    """Render the per-thread attribution as CSV.

    The columns are the canonical matrix §9 fields: ``tid``,
    ``native_id``, ``name``, ``comm``, ``cpu_delta_s``, ``share_pct``.
    """
    lines = ["tid,native_id,name,comm,cpu_delta_s,share_pct"]
    for row in threads:
        name = str(row.get("name") or "")
        comm = str(row.get("comm") or "")
        lines.append(
            ",".join(
                [
                    str(row.get("tid", "")),
                    "" if row.get("native_id") is None else str(row.get("native_id")),
                    f'"{name}"',
                    f'"{comm}"',
                    str(row.get("cpu_delta_s", 0.0)),
                    str(row.get("share_pct", 0.0)),
                ]
            )
        )
    return "\n".join(lines) + "\n"


def capture_directories(root: Path) -> list[Path]:
    """Every CAPTURE directory under the root (the F-05 exclusions applied).

    A directory is a capture iff it contains a ``manifest.json`` and
    is NOT named ``boot-records``.
    """
    if not root.exists():
        return []
    out: list[Path] = []
    try:
        entries = list(root.iterdir())
    except OSError:
        return []
    for entry in entries:
        if not entry.is_dir():
            continue
        if entry.name == BOOT_RECORDS_DIRNAME:
            continue
        if not (entry / "manifest.json").exists():
            continue
        out.append(entry)
    out.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0.0)
    return out


def prune_captures(
    root: Path,
    *,
    max_captures: int,
    max_age_hours: int,
    max_disk_mb: int,
    now: float | None = None,
    reserve: int = 0,
) -> list[Path]:
    """Prune the capture directories until the caps hold (D23 / A3).

    **The directories are the authority, never the table.** The walk
    is mtime-ordered (oldest first) and prunes the oldest until the
    count / age / disk caps hold.

    Args:
        root: The capture root.
        max_captures: The COUNT cap (the number of captures that may
            REMAIN after the prune).
        max_age_hours: The age cap.
        max_disk_mb: The disk cap.
        now: The reference instant (tests inject it).
        reserve: Slots to leave free for the capture that is ABOUT to be
            written. ``run_capture`` prunes BEFORE writing, so it passes
            ``reserve=1`` — otherwise a ``max_captures=3`` run would sit
            at 3 survivors and the new capture would take it to 4
            (SC-7/AC21's "a 5-capture run leaves exactly 3").

    Returns:
        The pruned (deleted) directories, oldest first.
    """
    removed: list[Path] = []
    directories = capture_directories(root)
    if not directories:
        return removed

    moment = time.time() if now is None else now
    age_limit_s = max_age_hours * 3600.0

    def _total_bytes(paths: list[Path]) -> int:
        total = 0
        for path in paths:
            try:
                for entry in path.rglob("*"):
                    if entry.is_file():
                        total += entry.stat().st_size
            except OSError:
                continue
        return total

    # Age cap — anything older than the limit goes, oldest first.
    survivors: list[Path] = []
    for path in directories:
        try:
            age = moment - path.stat().st_mtime
        except OSError:
            survivors.append(path)
            continue
        if age > age_limit_s:
            _remove_capture(path)
            removed.append(path)
        else:
            survivors.append(path)

    # Count cap — the oldest go first, leaving ``reserve`` slots free.
    keep = max(1, max_captures - max(0, reserve))
    while len(survivors) > keep:
        victim = survivors.pop(0)
        _remove_capture(victim)
        removed.append(victim)

    # Disk cap — the oldest go first.
    disk_limit = max_disk_mb * 1024 * 1024
    if disk_limit > 0:
        while survivors and _total_bytes(survivors) > disk_limit:
            victim = survivors.pop(0)
            _remove_capture(victim)
            removed.append(victim)

    return removed


def _remove_capture(path: Path) -> None:
    """Delete one capture directory (never raises)."""
    try:
        shutil.rmtree(path)
    except OSError as exc:
        logger.warning("Wedge-capture: failed to prune %s: %s", path, exc)


class CaptureWriter:
    """Writes one capture directory (file-first).

    Args:
        root: The capture root (the directory that holds the captures).
        deadline_s: The whole-CAPTURE wall-clock budget — measured from
            the capture's entry (the caller passes ``started_monotonic``
            to :meth:`write_capture`), never from the writer's own
            start (W2: the deadline is end-to-end).
        object_census: Whether the O(heap) object census runs.
    """

    def __init__(
        self,
        root: Path | str,
        *,
        deadline_s: float = 20.0,
        object_census: bool = False,
    ) -> None:
        self.root = Path(root)
        self.deadline_s = float(deadline_s)
        self.object_census = bool(object_census)

    # ── the budget ─────────────────────────────────────────────────────
    def _budget_left(self, started: float) -> float:
        """Seconds left in the capture budget (never negative).

        ``started`` is the CAPTURE'S entry instant (the caller's clock
        — W2), not the writer's.
        """
        return max(0.0, self.deadline_s - (time.monotonic() - started))

    # ── the capture ────────────────────────────────────────────────────
    def write_capture(
        self,
        *,
        capture_id: str,
        reason: str,
        trigger_source: str = "",
        executor_native_id: int | None = None,
        boot_epoch: str | None = None,
        captured_at: str = "",
        host: str = "",
        release_version: str = "",
        summary: str = "",
        auto_continue_mode: str = "unknown",
        stacks: Callable[[Path], dict[str, Any]] | None = None,
        threads: list[dict[str, Any]] | None = None,
        lag: dict[str, Any] | None = None,
        metrics: dict[str, Any] | None = None,
        db: dict[str, Any] | None = None,
        readyz: dict[str, Any] | None = None,
        checkpoint_perf_tail: list[dict[str, Any]] | None = None,
        extra_manifest: dict[str, Any] | None = None,
        started_monotonic: float | None = None,
    ) -> CaptureResult:
        """Write the whole capture directory.

        Never raises. Returns a :class:`CaptureResult` carrying the
        per-artifact outcome map.

        Args:
            started_monotonic: The CAPTURE'S entry instant (the caller's
                budget clock — W2). ``None`` (the default) starts the
                budget at the writer's own entry — the hermetic shape
                the unit packs drive directly; the production path
                (``run_capture``) ALWAYS passes its entry so the
                deadline is end-to-end.
        """
        started = (
            time.monotonic()
            if started_monotonic is None
            else float(started_monotonic)
        )
        capture_dir = self.root / capture_id
        result = CaptureResult(
            capture_id=capture_id,
            capture_dir=capture_dir,
            reason=reason,
            trigger_source=trigger_source or reason,
            executor_native_id=executor_native_id,
        )
        artifacts: dict[str, Any] = {}

        try:
            capture_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            result.artifacts = {"capture_dir": f"failed:{type(exc).__name__}"}
            result.duration_s = time.monotonic() - started
            return result

        # ── stacks.txt ──────────────────────────────────────────────
        if stacks is not None:
            artifacts["stacks.txt"] = self._guarded(
                started, lambda: stacks(capture_dir / "stacks.txt")
            )
        else:
            artifacts["stacks.txt"] = SKIPPED_NA

        # ── threads.csv ─────────────────────────────────────────────
        artifacts["threads.csv"] = self._guarded(
            started,
            lambda: self._write_threads(capture_dir / "threads.csv", threads or []),
        )

        # ── lag.json ─────────────────────────────────────────────────
        artifacts["lag.json"] = self._guarded(
            started,
            lambda: self._write_json(capture_dir / "lag.json", lag or {}),
        )

        # ── metrics.json ─────────────────────────────────────────────
        artifacts["metrics.json"] = self._guarded(
            started,
            lambda: self._write_json(capture_dir / "metrics.json", metrics or {}),
        )

        # ── db.json ──────────────────────────────────────────────────
        artifacts["db.json"] = self._guarded(
            started,
            lambda: self._write_json(capture_dir / "db.json", db or {}),
        )

        # ── readyz.json (OMITTED when the composite is absent — D25) ──
        if readyz is None:
            artifacts["readyz.json"] = SKIPPED_NA
        else:
            artifacts["readyz.json"] = self._guarded(
                started,
                lambda: self._write_json(capture_dir / "readyz.json", readyz),
            )

        # ── checkpoint_perf_tail.json ────────────────────────────────
        artifacts["checkpoint_perf_tail.json"] = self._guarded(
            started,
            lambda: self._write_json(
                capture_dir / "checkpoint_perf_tail.json",
                {"ops": checkpoint_perf_tail or []},
            ),
        )

        # ── manifest.json (FINAL) ────────────────────────────────────
        file_count = 0
        total_bytes = 0
        try:
            for entry in capture_dir.iterdir():
                if entry.is_file() and entry.name != "manifest.json":
                    file_count += 1
                    total_bytes += entry.stat().st_size
        except OSError:
            pass

        manifest: dict[str, Any] = {
            "capture_id": capture_id,
            "trigger_reason": reason,
            "trigger_source": result.trigger_source,
            "executor_native_id": executor_native_id,
            "captured_at": captured_at,
            "boot_epoch": boot_epoch,
            "host": host,
            "release_version": release_version,
            "summary": summary,
            "auto_continue_mode": auto_continue_mode,
            "artifacts": artifacts,
            "file_count": file_count,
            "total_bytes": total_bytes,
            "deadline_s": self.deadline_s,
        }
        if extra_manifest:
            manifest.update(extra_manifest)

        # The manifest is NEVER budget-gated (AC9 / A-F7-iv): it is the
        # capture's INDEX, so skipping it would turn a partial capture
        # into an UNDISCOVERABLE one. Every data step above may be
        # ``skipped:budget``; the manifest write is always ATTEMPTED, and
        # its failure is still swallowed into ``ok=False`` (never raises).
        try:
            self._write_json(capture_dir / "manifest.json", manifest)
            manifest_status = "ok"
        except Exception as exc:  # noqa: BLE001 — one artifact degrades
            manifest_status = f"failed:{type(exc).__name__}"
        artifacts["manifest.json"] = manifest_status
        manifest["artifacts"] = artifacts
        result.manifest = manifest
        result.artifacts = artifacts
        result.ok = manifest_status == "ok"
        result.duration_s = time.monotonic() - started
        return result

    # ── the guarded steps ──────────────────────────────────────────────
    def _guarded(self, started: float, action: Callable[[], Any]) -> Any:
        """Run one artifact step under the deadline, swallowing failures."""
        if self._budget_left(started) <= 0:
            return SKIPPED_BUDGET
        try:
            return action()
        except Exception as exc:  # noqa: BLE001 — one artifact degrades
            return f"failed:{type(exc).__name__}"

    def _write_threads(self, path: Path, threads: list[dict[str, Any]]) -> str:
        _write_text(path, render_threads_csv(threads))
        return "ok"

    def _write_json(self, path: Path, payload: Any) -> str:
        _write_json(path, payload)
        return "ok"


def write_pending_row_sidecar(capture_dir: Path, row: dict[str, Any]) -> bool:
    """Write the ``.pending_row.json`` sidecar (T11 / A3).

    The sidecar is the reconciler's hand-off: the capture is valid on
    disk even though its metadata row could not be written.
    """
    try:
        capture_dir.mkdir(parents=True, exist_ok=True)
        path = capture_dir / PENDING_ROW_SIDECAR
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(row, indent=2, sort_keys=True, default=str))
        os.replace(tmp, path)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Wedge-capture: pending-row sidecar write failed for %s: %s",
            capture_dir, exc,
        )
        return False


def read_pending_row_sidecars(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    """Every pending-row sidecar under the root (T12's reconciler input).

    The sidecars live INSIDE their capture directory — they are never
    treated as captures themselves.
    """
    out: list[tuple[Path, dict[str, Any]]] = []
    if not root.exists():
        return out
    try:
        entries = list(root.iterdir())
    except OSError:
        return out
    for entry in entries:
        if not entry.is_dir() or entry.name == BOOT_RECORDS_DIRNAME:
            continue
        sidecar = entry / PENDING_ROW_SIDECAR
        if not sidecar.exists():
            continue
        try:
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if isinstance(payload, dict):
            out.append((sidecar, payload))
    return out


__all__ = [
    "BOOT_RECORDS_DIRNAME",
    "CAPTURE_FILE_NAMES",
    "CaptureResult",
    "CaptureWriter",
    "PENDING_ROW_SIDECAR",
    "SKIPPED_BUDGET",
    "SKIPPED_NA",
    "capture_directories",
    "make_capture_id",
    "prune_captures",
    "read_pending_row_sidecars",
    "render_threads_csv",
    "write_pending_row_sidecar",
]

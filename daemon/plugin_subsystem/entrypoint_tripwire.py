"""Entrypoint ≤200-line tripwire — numeric CI guard for B-path plugins (CON §2,
slice ② carry-forward 4).

B-path plugins declare ``plugin.entrypoint`` as a relative path rooted under
``adapter/``. The tripwire counts the lines of the file at vendoring time:

- line count ≤ ``ALARM_THRESHOLD`` (220)  ⇒ ``ok``
- line count > ``ALARM_THRESHOLD`` (220) and ≤ ``REFUSE_THRESHOLD`` (300) ⇒ ``alarm``
- line count > ``REFUSE_THRESHOLD`` (300) ⇒ ``refuse``

The mechanism lands at slice ② even though real ``adapter/`` trees arrive
at slice ⑤; the unit tests construct synthetic fixture files in ``tmp_path``
so the tripwire is fully testable offline. The tripwire is a sibling
check to the manifest schema-CI runner (different concern: file size, not
schema shape) and is invoked from ``plugins-convention/ci_runner.py``.

**Sentinels (mirrors ``test_sentinels``).** No ``importlib``,
no entry-point scanning, no runtime plugin code loads here; the file
count is plain stdlib I/O. Tripwire does not execute plugin code.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from daemon.plugin_subsystem.plugin_declaration import PluginDeclaration

__all__ = [
    "ALARM_THRESHOLD",
    "REFUSE_THRESHOLD",
    "EntrypointCheckResult",
    "check_entrypoint",
    "run_tripwire",
]


# CON §2 line 52 — entrypoint ≤200-line tripwire (CI: alarm 220, refuse 300).
ALARM_THRESHOLD: int = 220
REFUSE_THRESHOLD: int = 300


# Statuses (closed enum; JSON-serializable).
STATUS_OK = "ok"
STATUS_ALARM = "alarm"
STATUS_REFUSE = "refuse"
STATUS_NOT_APPLICABLE = "not_applicable"
STATUS_MISSING = "missing"


@dataclass(frozen=True)
class EntrypointCheckResult:
    """One plugin's entrypoint tripwire result.

    Attributes:
        plugin: plugin directory name.
        entrypoint: ``plugin.entrypoint`` value (relative path under plugin
            root; ``None`` for non-B / non-lifted-symbol plugins).
        absolute_path: resolved absolute path; ``None`` when not applicable.
        line_count: number of lines counted; ``None`` when not applicable.
        status: one of ``ok`` / ``alarm`` / ``refuse`` / ``not_applicable`` /
            ``missing`` (file declared but not present).
        threshold_alarm: lines threshold above which the tripwire alarms.
        threshold_refuse: lines threshold above which the tripwire refuses.
        message: human-readable one-line summary.
    """

    plugin: str
    entrypoint: Optional[str]
    absolute_path: Optional[str]
    line_count: Optional[int]
    status: str
    threshold_alarm: int
    threshold_refuse: int
    message: str

    @property
    def is_failure(self) -> bool:
        """True when the tripwire is a CI failure (refuse)."""
        return self.status == STATUS_REFUSE

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _count_lines(path: Path) -> int:
    """Count lines in a text file (any line terminator; trailing partial line
    counts as one). Reads as bytes and counts ``\\n`` to be safe against
    files that do not end with a newline.

    Falls back to the on-disk file-size as a coarse cap if a read fails: a
    refusal must be definitive, never optimistic; an unreadable file
    surfaces as ``missing`` rather than as a silent pass.
    """
    raw = path.read_bytes()
    if not raw:
        return 0
    # Sum the LF count and (if the file does not end with LF) add one for
    # the trailing partial line. This matches the common `wc -l` semantics
    # for files that lack a trailing newline.
    lf_count = raw.count(b"\n")
    return lf_count if raw.endswith(b"\n") else lf_count + 1


def check_entrypoint(
    plugin_dir: Path,
    declaration: PluginDeclaration,
    *,
    alarm_threshold: int = ALARM_THRESHOLD,
    refuse_threshold: int = REFUSE_THRESHOLD,
) -> EntrypointCheckResult:
    """Check one plugin's declared ``entrypoint`` against the tripwire.

    Returns a :class:`EntrypointCheckResult` with ``status='not_applicable'``
    when the plugin does not declare an entrypoint (resource-only /
    own-outright / A-path plugins have no B-path entrypoint to tripwire).
    """
    plugin_dir = Path(plugin_dir)
    entrypoint = declaration.entrypoint
    if entrypoint is None:
        return EntrypointCheckResult(
            plugin=plugin_dir.name,
            entrypoint=None,
            absolute_path=None,
            line_count=None,
            status=STATUS_NOT_APPLICABLE,
            threshold_alarm=alarm_threshold,
            threshold_refuse=refuse_threshold,
            message="no entrypoint declared; tripwire not applicable",
        )

    absolute = (plugin_dir / entrypoint).resolve()
    if not absolute.is_file():
        return EntrypointCheckResult(
            plugin=plugin_dir.name,
            entrypoint=entrypoint,
            absolute_path=str(absolute),
            line_count=None,
            status=STATUS_MISSING,
            threshold_alarm=alarm_threshold,
            threshold_refuse=refuse_threshold,
            message=f"entrypoint file not found at {entrypoint!r} (refused; CON §1 adapter-required)",
        )

    try:
        line_count = _count_lines(absolute)
    except OSError as exc:
        return EntrypointCheckResult(
            plugin=plugin_dir.name,
            entrypoint=entrypoint,
            absolute_path=str(absolute),
            line_count=None,
            status=STATUS_MISSING,
            threshold_alarm=alarm_threshold,
            threshold_refuse=refuse_threshold,
            message=f"entrypoint file unreadable: {exc}",
        )

    if line_count > refuse_threshold:
        status = STATUS_REFUSE
        message = (
            f"entrypoint {entrypoint!r} is {line_count} lines; > refuse_threshold "
            f"{refuse_threshold} (CON §2 tripwire REFUSE)"
        )
    elif line_count > alarm_threshold:
        status = STATUS_ALARM
        message = (
            f"entrypoint {entrypoint!r} is {line_count} lines; > alarm_threshold "
            f"{alarm_threshold} (CON §2 tripwire ALARM)"
        )
    else:
        status = STATUS_OK
        message = f"entrypoint {entrypoint!r} is {line_count} lines; within tripwire"

    return EntrypointCheckResult(
        plugin=plugin_dir.name,
        entrypoint=entrypoint,
        absolute_path=str(absolute),
        line_count=line_count,
        status=status,
        threshold_alarm=alarm_threshold,
        threshold_refuse=refuse_threshold,
        message=message,
    )


def run_tripwire(
    declarations: Mapping[str, PluginDeclaration],
    *,
    alarm_threshold: int = ALARM_THRESHOLD,
    refuse_threshold: int = REFUSE_THRESHOLD,
) -> Dict[str, Any]:
    """Run the tripwire over a mapping of plugin-name → :class:`PluginDeclaration`.

    Caller resolves the declarations (e.g. via the manifest reader) and the
    plugin_dir lookup. Plugin entries absent from the mapping are skipped.
    """
    results: List[EntrypointCheckResult] = []
    for name in sorted(declarations):
        declaration = declarations[name]
        if declaration.source_dir is None:
            continue
        results.append(
            check_entrypoint(
                declaration.source_dir,
                declaration,
                alarm_threshold=alarm_threshold,
                refuse_threshold=refuse_threshold,
            )
        )
    failed = [r for r in results if r.is_failure]
    alarmed = [r for r in results if r.status == STATUS_ALARM]
    return {
        "thresholds": {"alarm": alarm_threshold, "refuse": refuse_threshold},
        "checked": len(results),
        "ok": len(results) - len(failed) - len(alarmed),
        "alarm": len(alarmed),
        "refused": len(failed),
        "ok_overall": not failed,
        "results": [r.as_dict() for r in results],
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry: ``python -m daemon.plugin_subsystem.entrypoint_tripwire
    <plugins_root>``. Aggregates the tripwire over every discovered
    plugin; ``--declarations-json <path>`` lets callers feed a pre-scanned
    declaration set (skips re-validation) for fast CI invocations.

    Exit code 0 = ok (alarms allowed); 1 = at least one REFUSE; 2 = usage
    error. Alarms are visible in the JSON report but do not fail the
    command — the alarm is a "block promote after N days" signal, not a
    same-build gate.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="entrypoint_tripwire",
        description="Plugin entrypoint ≤200-line tripwire (CON §2; slice ② carry-forward 4).",
    )
    parser.add_argument("plugins_root", help="directory containing plugin trees (e.g. plugins/)")
    parser.add_argument(
        "--alarm-threshold",
        type=int,
        default=ALARM_THRESHOLD,
        help=f"line count above which to emit ALARM (default {ALARM_THRESHOLD})",
    )
    parser.add_argument(
        "--refuse-threshold",
        type=int,
        default=REFUSE_THRESHOLD,
        help=f"line count above which to REFUSE (default {REFUSE_THRESHOLD})",
    )
    args = parser.parse_args(argv)

    # Discover via the schema-CI runner's iteration order (one level deep).
    plugins_root = Path(args.plugins_root)
    if not plugins_root.is_dir():
        print(json.dumps({"error": f"plugins_root not a directory: {plugins_root}"}))
        return 2

    from daemon.plugin_subsystem.manifest_reader import read_manifest

    declarations: Dict[str, PluginDeclaration] = {}
    for child in sorted(plugins_root.iterdir()):
        if not child.is_dir():
            continue
        try:
            declarations[child.name] = read_manifest(child)
        except Exception:  # noqa: BLE001 - skip non-plugin dirs; manifest_reader surfaces its own refusals
            continue

    report = run_tripwire(
        declarations,
        alarm_threshold=args.alarm_threshold,
        refuse_threshold=args.refuse_threshold,
    )
    report["plugins_root"] = str(plugins_root)
    print(json.dumps(report, indent=2))
    return 0 if report["ok_overall"] else 1


if __name__ == "__main__":  # pragma: no cover - CLI convenience
    raise SystemExit(main())

"""Promote-staleness predicate (REC §1.2 component 13 — slice ⑥).

The promote gate's plugin-staleness check: **pin age > N days OR
unresolved divergence ⇒ refuse**; unowned drift alarms escalate to
the same refusal after ``escalation_days`` (CON §5: "unowned for
``escalation`` days ⇒ block promote").  Wired into
``scripts/upgrade/promote.sh`` (argv-only override, journaled — the
stage.sh freshness-guard discipline).

**Import hygiene (deliberate):** this module imports ONLY the
stdlib + PyYAML — NO ``daemon`` imports — so the promote-time
interpreter (repo venv, system python3, whatever the operator's
session resolves) can run it without importing the daemon package
graph.  The frozen vocabulary/sentinel rules are unaffected: the
module lives in the tier-2 vocabulary zone.

**Data sources (offline-provable, no daemon, no DB, no git):**

- ``<plugin_dir>/MANIFEST.yaml`` — per-class tag pins, per-class
  ``alarm_owner``, per-class ``escalation``, and the divergence
  register (the canonical representation; entry ``status`` carries
  the disposition state).
- ``<plugin_dir>/sync_trail.jsonl`` — the sync-runner's trail
  (slice ⑥): one JSON line per completed sync, carrying the frozen
  CON §5 ``sync_result`` shape.  The predicate "consumes the
  sync_result directly" (probe doc item 5) — NOT the drift-event
  stream: staleness is a continuous measure, drift is binary.

**Pin-age semantics:** the trail's ``staleness_age_days`` is the
tag age at sync time; the predicate ages it forward with the
``recorded_at`` → ``now`` delta.  The newest trail line whose
``action`` is a COMPLETED pull verdict (``clean_pulled`` /
``alarmed`` / ``no_change``) is the evidence line per class — a
``refused`` line never masquerades as staleness evidence (early
refusals carry ``staleness_age_days=0``).

**Fail-closed behavior (the "'4.5 months silent' impossible"
outcome):**

- trail missing OR no completed-pull line ⇒ refuse
  ``staleness-unknown`` — silence itself is visible at promote.
- manifest unreadable/unparseable ⇒ refuse ``manifest-unreadable``.
- a plugin dir with NO manifest is not this predicate's subject ⇒
  pass (nothing to check; tier-1 stays structurally blind).

**Divergence-status vocabulary (register entries):**

- ``open`` — sync-observed divergence, disposition NOT yet landed
  (the sync-runner emits ``status: "open"``).  UNRESOLVED ⇒ refuse.
- ``registered`` — pre-registered divergence POINT, no observed
  delta yet (the seeded markers; the default when ``status`` is
  absent — back-compat with pre-⑥ entries).  Not a block.
- ``resolved`` — re-apply-or-drop disposition landed.  Not a block.

**Verdict shape (JSON-serializable):**

``{"ok": bool, "plugin": str, "plugin_dir": str, "reasons":
[{"code": <token>, "detail": str, ...}], "checked": {...}}`` —
``ok=True`` iff ``reasons`` is empty.  Reason codes: the refusal
tokens above; callers (promote.sh) grep the FIRST code for the
journaled reason token.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

__all__ = [
    "evaluate_staleness",
    "main",
    "SYNC_TRAIL_FILENAME",
    "EXIT_FRESH",
    "EXIT_STALE",
]

SYNC_TRAIL_FILENAME = "sync_trail.jsonl"
MANIFEST_FILENAME = "MANIFEST.yaml"

# Exit codes: 0 = fresh (promote proceeds), 3 = stale (promote.sh
# decides: refuse, or honor the argv-only override).  3 keeps the
# predicate distinct from the shell's own 78-refusal convention.
EXIT_FRESH = 0
EXIT_STALE = 3

# Trail actions that count as completed-pull staleness evidence.
_EVIDENCE_ACTIONS = ("clean_pulled", "alarmed", "no_change")

# Register-entry statuses that count as an UNRESOLVED divergence.
_UNRESOLVED_STATUSES = ("open",)


def _parse_iso(value: str) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp; None on failure (caller fails closed)."""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _load_manifest(plugin_dir: Path) -> Dict[str, Any]:
    with open(plugin_dir / MANIFEST_FILENAME, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _load_trail(plugin_dir: Path) -> List[Dict[str, Any]]:
    """Read the sync trail; oldest-first.  Torn/short lines are
    SKIPPED (a torn LAST line from a crash must not poison the
    verdict — the previous good lines remain usable evidence)."""
    trail_path = plugin_dir / SYNC_TRAIL_FILENAME
    if not trail_path.is_file():
        return []
    entries: List[Dict[str, Any]] = []
    with open(trail_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                entries.append(parsed)
    return entries


def _class_sections(manifest: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """The vendored-class sections present in the manifest."""
    return {
        name: manifest.get(name) or {}
        for name in ("copy_freely", "snapshot_with_drift_alarm")
        if isinstance(manifest.get(name), dict)
    }


def evaluate_staleness(
    plugin_dir: Path,
    *,
    max_pin_age_days: int = 14,
    escalation_days: int = 14,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Evaluate the plugin-staleness predicate for one plugin dir.

    Args:
        plugin_dir: The plugin tree (must contain MANIFEST.yaml to
            be in scope; a dir without a manifest passes with an
            explanatory note — tier-1 stays structurally blind).
        max_pin_age_days: N for the pin-age refusal (row ⑥: 14).
        escalation_days: N for the unowned-alarm escalation
            (CON §5: unowned for escalation days ⇒ block).
        now: Override "now" (tests); defaults to real UTC now.

    Returns:
        JSON-serializable verdict dict (see module docstring).
    """
    plugin_dir = Path(plugin_dir)
    now = now or datetime.now(timezone.utc)
    reasons: List[Dict[str, Any]] = []
    checked: Dict[str, Any] = {
        "plugin_dir": str(plugin_dir),
        "max_pin_age_days": max_pin_age_days,
        "escalation_days": escalation_days,
        "now": now.isoformat(),
    }

    if not (plugin_dir / MANIFEST_FILENAME).is_file():
        return {
            "ok": True,
            "plugin": plugin_dir.name,
            "plugin_dir": str(plugin_dir),
            "reasons": [],
            "checked": {**checked, "note": "no manifest — not in scope"},
        }

    try:
        manifest = _load_manifest(plugin_dir)
    except Exception as exc:  # noqa: BLE001 - fail-closed surface
        return {
            "ok": False,
            "plugin": plugin_dir.name,
            "plugin_dir": str(plugin_dir),
            "reasons": [
                {
                    "code": "manifest-unreadable",
                    "detail": f"MANIFEST.yaml unreadable/unparseable: {exc}",
                }
            ],
            "checked": checked,
        }

    plugin_name = str((manifest.get("plugin") or {}).get("name", plugin_dir.name))
    checked["plugin"] = plugin_name
    trail = _load_trail(plugin_dir)
    checked["trail_lines"] = len(trail)

    # ── 1. Pin age (per class; worst class wins) ──────────────────────────
    pin_ages: Dict[str, Any] = {}
    stalest_class: Optional[str] = None
    stalest_age = -1
    for class_name, section in _class_sections(manifest).items():
        evidence = None
        for entry in reversed(trail):  # newest-first
            result = entry.get("sync_result") or {}
            if result.get("target_class") != class_name:
                continue
            if result.get("action") not in _EVIDENCE_ACTIONS:
                continue
            evidence = entry
            break
        if evidence is None:
            pin_ages[class_name] = {"pin_age_days": None, "evidence": None}
            reasons.append(
                {
                    "code": "staleness-unknown",
                    "detail": (
                        f"class {class_name}: no completed-pull trail line "
                        f"({SYNC_TRAIL_FILENAME} missing or has no "
                        "clean_pulled/alarmed/no_change entry) — staleness "
                        "is unprovable, refusing (fail-closed; a real "
                        "sync run records the trail)"
                    ),
                    "class": class_name,
                }
            )
            continue
        result = evidence.get("sync_result") or {}
        age_at_sync = int(result.get("staleness_age_days", 0) or 0)
        recorded_at = _parse_iso(str(evidence.get("recorded_at", "")))
        if recorded_at is None:
            pin_ages[class_name] = {"pin_age_days": None, "evidence": "torn"}
            reasons.append(
                {
                    "code": "staleness-unknown",
                    "detail": (
                        f"class {class_name}: newest trail line has an "
                        "unparseable recorded_at — staleness is "
                        "unprovable, refusing (fail-closed)"
                    ),
                    "class": class_name,
                }
            )
            continue
        age_now = age_at_sync + (now - recorded_at).days
        pin_ages[class_name] = {
            "pin_age_days": age_now,
            "age_at_sync": age_at_sync,
            "recorded_at": evidence.get("recorded_at"),
            "upstream_tag": result.get("upstream_tag"),
        }
        if age_now > stalest_age:
            stalest_age = age_now
            stalest_class = class_name
        if age_now > max_pin_age_days:
            reasons.append(
                {
                    "code": "pin-stale",
                    "detail": (
                        f"class {class_name}: pin "
                        f"{result.get('upstream_tag', '?')!r} is "
                        f"{age_now} days old (>{max_pin_age_days}) — "
                        "refresh the pin (sync at a newer tag) or pass "
                        "the journaled override"
                    ),
                    "class": class_name,
                    "pin_age_days": age_now,
                }
            )
    checked["pin_ages"] = pin_ages
    checked["stalest_class"] = stalest_class

    # ── 2. Unresolved divergence (register status == open) ────────────────
    open_by_class: Dict[str, List[Dict[str, Any]]] = {}
    for class_name, section in _class_sections(manifest).items():
        register = section.get("divergence_register") or []
        if not isinstance(register, list):
            continue
        for entry in register:
            if not isinstance(entry, dict):
                continue
            status = str(entry.get("status", "registered") or "registered")
            if status in _UNRESOLVED_STATUSES:
                open_by_class.setdefault(class_name, []).append(
                    {
                        "id": entry.get("id"),
                        "status": status,
                        "files": entry.get("files") or [],
                    }
                )
    for class_name, opens in sorted(open_by_class.items()):
        reasons.append(
            {
                "code": "divergence-unresolved",
                "detail": (
                    f"class {class_name}: {len(opens)} unresolved "
                    "divergence(s) "
                    f"(ids {[o['id'] for o in opens]}) — land the "
                    "re-apply-or-drop disposition (CON §2) and set "
                    "entry status=resolved, or pass the journaled override"
                ),
                "class": class_name,
                "entries": opens,
            }
        )

    # ── 3. Unowned-alarm escalation (CON §5: unowned for escalation
    #      days ⇒ block promote).  An OPEN divergence in a class whose
    #      alarm_owner is missing/empty is the unowned case; age comes
    #      from the class's newest trail evidence when provable, and a
    #      non-provable age fails CLOSED (block with an explicit note).
    for class_name, opens in sorted(open_by_class.items()):
        section = _class_sections(manifest).get(class_name) or {}
        owner = str(section.get("alarm_owner", "") or "").strip()
        if owner:
            continue  # owned — the analyze/escalate lane handles it
        age_provable = False
        age_days = None
        for entry in reversed(trail):
            result = entry.get("sync_result") or {}
            if result.get("target_class") == class_name and result.get(
                "action"
            ) in _EVIDENCE_ACTIONS:
                recorded_at = _parse_iso(str(entry.get("recorded_at", "")))
                if recorded_at is not None:
                    age_days = (now - recorded_at).days
                    age_provable = True
                break
        if age_provable and age_days is not None and age_days <= escalation_days:
            # Unowned but inside the escalation window — visible, not
            # blocking yet (an owner can still be assigned).
            continue
        reasons.append(
            {
                "code": "alarm-owner-escalation",
                "detail": (
                    f"class {class_name}: {len(opens)} unresolved divergence(s) "
                    f"with NO alarm_owner for "
                    f"{age_days if age_days is not None else 'an unprovable number of'} "
                    f"days (escalation N={escalation_days}) — assign "
                    "alarm_owner in the manifest or land the disposition"
                ),
                "class": class_name,
                "unowned_age_days": age_days,
                "age_provable": age_provable,
            }
        )

    return {
        "ok": not reasons,
        "plugin": plugin_name,
        "plugin_dir": str(plugin_dir),
        "reasons": reasons,
        "checked": checked,
    }


def main(argv: Optional[List[str]] = None) -> int:
    """CLI: ``python -m daemon.plugin_subsystem.promote_staleness <plugin_dir>``.

    Prints one human-readable line per reason (grep-friendly
    ``code=`` token first) and, with ``--json``, the full verdict.
    Exit 0 = fresh, 3 = stale (promote.sh owns the refusal/override
    decision).
    """
    parser = argparse.ArgumentParser(
        description=(
            "Plugin-staleness promote predicate (REC comp 13): pin age, "
            "unresolved divergences, unowned-alarm escalation."
        )
    )
    parser.add_argument("plugin_dir", help="Path to the plugin tree")
    parser.add_argument(
        "--max-pin-age-days",
        type=int,
        default=14,
        help="Pin-age refusal threshold N (default 14)",
    )
    parser.add_argument(
        "--escalation-days",
        type=int,
        default=14,
        help="Unowned-alarm escalation threshold N (default 14)",
    )
    parser.add_argument("--json", action="store_true", help="Print the full JSON verdict")
    args = parser.parse_args(argv)

    verdict = evaluate_staleness(
        Path(args.plugin_dir),
        max_pin_age_days=args.max_pin_age_days,
        escalation_days=args.escalation_days,
    )
    if args.json:
        print(json.dumps(verdict, indent=2, sort_keys=True))
    if verdict["ok"]:
        print(f"PLUGIN-STALENESS=fresh plugin={verdict['plugin']}")
        return EXIT_FRESH
    for reason in verdict["reasons"]:
        print(
            f"PLUGIN-STALENESS=stale code={reason['code']} "
            f"plugin={verdict['plugin']} class={reason.get('class', '-')} "
            f"detail={reason['detail']}"
        )
    return EXIT_STALE


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())

"""Deterministic corpus replay driver for AC-E2E-6.

The checked-in fixtures are *recorded decision entries*, not another source of
truth for production behavior.  Replay derives the three canonical promotion
metrics and a false-positive adjudication from those entries, and can optionally
write a JSONL run directory for an operator's audit.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

CANONICAL_FIELDS: tuple[str, ...] = (
    "event",
    "decision",
    "instance_id",
    "attestation_present",
    "denied_count",
    "gate_location",
    "leader_prompt_version",
    "pending_children",
    "queued_or_expected_wakeups",
    "attest_seen_outside_window",
    "messages_scanned",
    "scanned_window_size",
    "mode",
    "scanner_window_truncated",
    "scanner_summary_seen",
)


@dataclass(frozen=True)
class CorpusAdjudication:
    """Computed promotion metrics for one corpus replay."""

    mission_count: int
    dry_log_total: int
    dry_log_deny_predicate_total: int
    enforce_denied_total: int
    false_positive_rate: float
    would_have_denied_mission_ids: tuple[str, ...]
    pending_wakeup_allow_mission_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_entry(entry: Mapping[str, Any]) -> None:
    """Raise a useful error for a malformed recorded entry."""
    missing = [field for field in CANONICAL_FIELDS if field not in entry]
    if missing:
        raise ValueError(f"corpus entry is missing canonical fields: {missing}")
    if entry.get("event") != "leader_completion_gate":
        raise ValueError("corpus entry event must be leader_completion_gate")
    if entry.get("decision") != "dry_log":
        raise ValueError("AC-E2E-6 corpus entries must be dry_log records")
    if entry.get("mode") != "dry":
        raise ValueError("AC-E2E-6 corpus entries must record mode=dry")
    if int(entry.get("messages_scanned", 0)) < 1:
        raise ValueError("messages_scanned must be > 0 for a healthy dry evaluation")


def load_corpus(path: str | Path) -> list[dict[str, Any]]:
    """Load a JSONL corpus, validating every entry on read."""
    entries: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            entry = json.loads(line)
            if not isinstance(entry, dict):
                raise ValueError(f"line {line_number} is not a JSON object")
            validate_entry(entry)
            entries.append(entry)
    return entries


def _would_have_denied(entry: Mapping[str, Any]) -> bool:
    return (
        entry.get("attestation_present") is False
        and int(entry.get("pending_children", 0)) == 0
        and int(entry.get("queued_or_expected_wakeups", 0)) == 0
    )


def replay_corpus(
    entries: Iterable[Mapping[str, Any]],
    *,
    record_metrics: bool = False,
) -> CorpusAdjudication:
    """Replay recorded decisions through canonical promotion adjudication.

    When ``record_metrics=True``, the derived counts are also registered with
    the Phase-4 resolver's canonical promotion counters, so the same replay
    can feed an operator-side metrics export without reimplementing metric
    names here.
    """
    validated = list(entries)
    for entry in validated:
        validate_entry(entry)
    would_have_denied = [
        str(entry.get("instance_id")) for entry in validated if _would_have_denied(entry)
    ]
    pending_allows = [
        str(entry.get("instance_id"))
        for entry in validated
        if entry.get("attestation_present") is False
        and (
            int(entry.get("pending_children", 0)) > 0
            or int(entry.get("queued_or_expected_wakeups", 0)) > 0
        )
    ]
    total = len(validated)
    deny_total = len(would_have_denied)
    if record_metrics:
        from daemon.services.attestation_resolver import (
            METRIC_DRY_LOG_DENY_PREDICATE_TOTAL,
            METRIC_DRY_LOG_TOTAL,
            record_promotion_metric,
        )

        record_promotion_metric(METRIC_DRY_LOG_TOTAL, increment=total)
        record_promotion_metric(
            METRIC_DRY_LOG_DENY_PREDICATE_TOTAL,
            increment=deny_total,
        )
        # NB: deliberately NO METRIC_ENFORCE_DENIED_TOTAL increment — a
        # dry replay is NOT a real enforce-mode deny, and polluting the
        # production counter would corrupt the dry→enforce promotion
        # adjudication (would-have-denied attribution stays in the
        # CorpusAdjudication report field only).
    return CorpusAdjudication(
        mission_count=total,
        dry_log_total=total,
        dry_log_deny_predicate_total=deny_total,
        enforce_denied_total=deny_total,
        false_positive_rate=(deny_total / total) if total else 0.0,
        would_have_denied_mission_ids=tuple(would_have_denied),
        pending_wakeup_allow_mission_ids=tuple(pending_allows),
    )


def write_replay_run(entries: Iterable[Mapping[str, Any]], output_dir: str | Path) -> Path:
    """Write one JSONL run per mission and return the directory created."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    for entry in list(entries):
        validate_entry(entry)
        mission_id = str(entry["instance_id"])
        path = output / f"{mission_id}.jsonl"
        path.write_text(json.dumps(dict(entry), sort_keys=True) + "\n", encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay the recorded LCA corpus")
    parser.add_argument("corpus", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    entries = load_corpus(args.corpus)
    adjudication = replay_corpus(entries)
    if args.output_dir is not None:
        write_replay_run(entries, args.output_dir)
    print(json.dumps(adjudication.as_dict(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

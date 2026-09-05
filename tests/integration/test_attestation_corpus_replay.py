"""Mode: dry — AC-E2E-6 recorded-corpus replay and promotion adjudication."""

from __future__ import annotations

from pathlib import Path

from tests.support.recorded_corpus_replay import (
    CANONICAL_FIELDS,
    load_corpus,
    replay_corpus,
    write_replay_run,
)
from daemon.services.attestation_resolver import get_promotion_metrics, reset_attestation_resolver_for_tests


def _corpus_path() -> Path:
    return Path(__file__).parents[1] / "fixtures" / "recorded_leader_missions"


def test_recorded_dry_corpus_replays_to_promotion_adjudication(tmp_path):
    entries = []
    for path in sorted(_corpus_path().glob("*.jsonl")):
        entries.extend(load_corpus(path))

    assert len(entries) == 4
    for entry in entries:
        assert set(CANONICAL_FIELDS).issubset(entry)
        assert entry["event"] == "leader_completion_gate"
        assert entry["decision"] == "dry_log"
        assert entry["mode"] == "dry"
        assert entry["messages_scanned"] > 0

    reset_attestation_resolver_for_tests()
    try:
        result = replay_corpus(entries, record_metrics=True)
        assert result.mission_count == 4
        assert result.dry_log_total == 4
        assert result.dry_log_deny_predicate_total == 2
        assert result.enforce_denied_total == 2
        assert result.false_positive_rate == 0.5
        metrics = get_promotion_metrics()
        assert metrics["dry_log_total"] == 4
        assert metrics["dry_log_deny_predicate_total"] == 2
        assert metrics["enforce_denied_total"] == 2
    finally:
        reset_attestation_resolver_for_tests()
    assert set(result.would_have_denied_mission_ids) == {
        "recorded-deny-001",
        "recorded-deny-002",
    }
    assert set(result.pending_wakeup_allow_mission_ids) == {
        "recorded-r2-allow-001",
        "recorded-r2-allow-002",
    }

    output = write_replay_run(entries, tmp_path / "runs")
    run_files = sorted(output.glob("*.jsonl"))
    assert len(run_files) == 4
    assert {path.name for path in run_files} == {f"{entry['instance_id']}.jsonl" for entry in entries}

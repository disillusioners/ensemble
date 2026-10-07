"""Tests for the promote-staleness predicate (REC §1.2 comp 13, slice ⑥).

The promote gate's behavior matrix, at predicate level:

* **fresh** — trail-backed pin age ≤ 14, no open divergences,
  alarms owned ⇒ ok, exit 0.
* **stale (pin)** — pin age > 14 ⇒ ``pin-stale``.
* **stale (staleness-unknown)** — missing/torn trail ⇒ refuse
  (fail-closed: "'4.5 months silent' impossible").
* **stale (divergence-unresolved)** — register entry ``status: open``.
* **escalation** — OPEN divergence + missing/empty ``alarm_owner``:
  inside the window ⇒ visible-but-passing; beyond it (or age
  unprovable) ⇒ ``alarm-owner-escalation``.
* registered/resolved statuses do NOT block; missing status reads
  as registered (back-compat).
* manifest absent ⇒ pass (out of scope); manifest unreadable ⇒
  ``manifest-unreadable``.
* CLI exit codes (0 fresh / 3 stale).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from daemon.plugin_subsystem.promote_staleness import (
    EXIT_FRESH,
    EXIT_STALE,
    evaluate_staleness,
    main,
)

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)


def _write_manifest(plugin_dir: Path, **overrides) -> None:
    doc: dict = {
        "schema_version": "1.0.2",
        "plugin": {
            "name": "testplugin",
            "license": "Apache-2.0",
            "upstream": {
                "repo": "/some/local/checkout",
                "tag_pin_per_class": {"copy_freely": "v1.0.0"},
            },
            "integration_path": "C",
        },
        "copy_freely": {
            "paths": ["copy_freely/data/"],
            "alarm_owner": "owner-a",
            "escalation": "block-promote-after-days",
        },
        "snapshot_with_drift_alarm": {
            "paths": ["snapshot_with_drift_alarm/prompts/"],
            "alarm_owner": "owner-b",
            "escalation": "block-promote-after-days",
            "divergence_register": [
                {
                    "id": 1,
                    "files": ["snapshot_with_drift_alarm/prompts/x.ts"],
                    "delta": "pre-registered marker",
                    "rationale": "r",
                    "pinning_test": "tests/unit/test_x.py::test_y",
                    "status": "registered",
                }
            ],
        },
        "parity_boundary": {
            "intentionally_not_vendored": [],
            "not_executed": [],
        },
    }
    for key, value in overrides.items():
        if value is None:
            doc.pop(key, None)
        else:
            doc[key] = value
    (plugin_dir / "MANIFEST.yaml").write_text(
        yaml.safe_dump(doc, sort_keys=False), encoding="utf-8"
    )


def _append_trail(plugin_dir: Path, lines: list[dict]) -> None:
    with open(plugin_dir / "sync_trail.jsonl", "a", encoding="utf-8") as fh:
        for line in lines:
            fh.write(json.dumps(line) + "\n")


def _trail_line(
    *,
    target_class: str = "copy_freely",
    action: str = "no_change",
    staleness: int = 3,
    recorded_days_ago: int = 0,
) -> dict:
    return {
        "recorded_at": (NOW - timedelta(days=recorded_days_ago)).isoformat(),
        "sync_result": {
            "plugin": "testplugin",
            "target_class": target_class,
            "upstream_tag": "v1.0.0",
            "action": action,
            "diff_summary": {"files_added": 0, "files_modified": 0, "files_removed": 0},
            "staleness_age_days": staleness,
        },
    }


def _codes(verdict: dict) -> list[str]:
    return [r["code"] for r in verdict["reasons"]]


# ══════════════════════════════════════════════════════════════════════════════
# Fresh / stale / override matrix (predicate level)
# ══════════════════════════════════════════════════════════════════════════════


class TestFreshStates:
    def test_fresh_passes(self, tmp_path):
        _write_manifest(tmp_path)
        _append_trail(
            tmp_path,
            [
                _trail_line(target_class="copy_freely", staleness=10, recorded_days_ago=2),
                _trail_line(target_class="snapshot_with_drift_alarm", staleness=10, recorded_days_ago=2),
            ],
        )
        verdict = evaluate_staleness(tmp_path, now=NOW)
        assert verdict["ok"] is True, verdict["reasons"]
        assert verdict["reasons"] == []
        # aged forward: 10 + 2 = 12 ≤ 14
        assert verdict["checked"]["pin_ages"]["copy_freely"]["pin_age_days"] == 12

    def test_no_manifest_passes_out_of_scope(self, tmp_path):
        verdict = evaluate_staleness(tmp_path, now=NOW)
        assert verdict["ok"] is True
        assert "no manifest" in verdict["checked"]["note"]

    def test_refused_trail_lines_are_not_evidence(self, tmp_path):
        # A refused-only trail must NOT masquerade as staleness evidence
        # (early refusals carry staleness_age_days=0).
        _write_manifest(tmp_path)
        _append_trail(tmp_path, [_trail_line(action="refused", staleness=0)])
        verdict = evaluate_staleness(tmp_path, now=NOW)
        assert set(_codes(verdict)) == {"staleness-unknown"}


class TestStaleStates:
    def test_pin_stale_refuses(self, tmp_path):
        _write_manifest(tmp_path)
        _append_trail(
            tmp_path,
            [
                _trail_line(target_class="copy_freely", staleness=13, recorded_days_ago=3),
                _trail_line(target_class="snapshot_with_drift_alarm", staleness=13, recorded_days_ago=3),
            ],
        )
        verdict = evaluate_staleness(tmp_path, now=NOW)
        # aged forward: 13 + 3 = 16 > 14
        assert "pin-stale" in _codes(verdict)
        assert verdict["ok"] is False

    def test_pin_exactly_at_threshold_passes(self, tmp_path):
        _write_manifest(tmp_path)
        _append_trail(
            tmp_path,
            [
                _trail_line(target_class="copy_freely", staleness=12, recorded_days_ago=2),
                _trail_line(target_class="snapshot_with_drift_alarm", staleness=12, recorded_days_ago=2),
            ],
        )
        verdict = evaluate_staleness(tmp_path, now=NOW)
        assert verdict["ok"] is True  # 14 ≤ 14: not stale

    def test_missing_trail_is_staleness_unknown(self, tmp_path):
        _write_manifest(tmp_path)
        verdict = evaluate_staleness(tmp_path, now=NOW)
        assert "staleness-unknown" in _codes(verdict)
        assert verdict["ok"] is False

    def test_torn_last_trail_line_skipped_older_lines_still_usable(self, tmp_path):
        _write_manifest(tmp_path)
        _append_trail(
            tmp_path,
            [
                _trail_line(target_class="copy_freely", staleness=5, recorded_days_ago=1),
                _trail_line(target_class="snapshot_with_drift_alarm", staleness=5, recorded_days_ago=1),
            ],
        )
        with open(tmp_path / "sync_trail.jsonl", "a", encoding="utf-8") as fh:
            fh.write('{"recorded_at": "TORN"')  # crash-torn last line
        verdict = evaluate_staleness(tmp_path, now=NOW)
        assert verdict["ok"] is True, verdict["reasons"]

    def test_unparseable_recorded_at_fails_closed(self, tmp_path):
        _write_manifest(tmp_path)
        line = _trail_line(target_class="copy_freely", staleness=5)
        line["recorded_at"] = "not-a-timestamp"
        _append_trail(
            tmp_path,
            [line, _trail_line(target_class="snapshot_with_drift_alarm", staleness=5)],
        )
        verdict = evaluate_staleness(tmp_path, now=NOW)
        assert "staleness-unknown" in _codes(verdict)

    def test_unreadable_manifest_refuses(self, tmp_path):
        _write_manifest(tmp_path)
        (tmp_path / "MANIFEST.yaml").write_text("{ torn yaml: [", encoding="utf-8")
        verdict = evaluate_staleness(tmp_path, now=NOW)
        assert _codes(verdict) == ["manifest-unreadable"]


# ══════════════════════════════════════════════════════════════════════════════
# Divergence-status semantics
# ══════════════════════════════════════════════════════════════════════════════


class TestDivergenceStatuses:
    def _manifest_with_register(self, tmp_path: Path, entries: list[dict]) -> None:
        _write_manifest(
            tmp_path,
            snapshot_with_drift_alarm={
                "paths": ["snapshot_with_drift_alarm/prompts/"],
                "alarm_owner": "owner-b",
                "escalation": "block-promote-after-days",
                "divergence_register": entries,
            },
        )
        _append_trail(
            tmp_path,
            [
                _trail_line(target_class="copy_freely", staleness=1),
                _trail_line(target_class="snapshot_with_drift_alarm", staleness=1),
            ],
        )

    def test_open_entry_blocks(self, tmp_path):
        self._manifest_with_register(
            tmp_path,
            [
                {
                    "id": 5,
                    "files": ["x.ts"],
                    "delta": "+1",
                    "rationale": "r",
                    "pinning_test": "t",
                    "status": "open",
                }
            ],
        )
        verdict = evaluate_staleness(tmp_path, now=NOW)
        assert "divergence-unresolved" in _codes(verdict)
        assert verdict["ok"] is False

    def test_registered_and_resolved_do_not_block(self, tmp_path):
        self._manifest_with_register(
            tmp_path,
            [
                {
                    "id": 1,
                    "files": ["x.ts"],
                    "delta": "marker",
                    "rationale": "r",
                    "pinning_test": "t",
                    "status": "registered",
                },
                {
                    "id": 2,
                    "files": ["y.ts"],
                    "delta": "d",
                    "rationale": "r",
                    "pinning_test": "t",
                    "status": "resolved",
                },
            ],
        )
        verdict = evaluate_staleness(tmp_path, now=NOW)
        assert verdict["ok"] is True, verdict["reasons"]

    def test_missing_status_reads_as_registered_backcompat(self, tmp_path):
        self._manifest_with_register(
            tmp_path,
            [
                # a pre-⑥ entry with no status field at all
                {
                    "id": 1,
                    "files": ["x.ts"],
                    "delta": "marker",
                    "rationale": "r",
                    "pinning_test": "t",
                }
            ],
        )
        verdict = evaluate_staleness(tmp_path, now=NOW)
        assert verdict["ok"] is True, verdict["reasons"]


# ══════════════════════════════════════════════════════════════════════════════
# Unowned-alarm escalation matrix (CON §5: unowned for N days ⇒ block)
# ══════════════════════════════════════════════════════════════════════════════


class TestAlarmOwnerEscalation:
    def _manifest_unowned_open(self, tmp_path: Path) -> None:
        _write_manifest(
            tmp_path,
            snapshot_with_drift_alarm={
                "paths": ["snapshot_with_drift_alarm/prompts/"],
                # alarm_owner MISSING — the unowned case
                "escalation": "block-promote-after-days",
                "divergence_register": [
                    {
                        "id": 5,
                        "files": ["x.ts"],
                        "delta": "+1",
                        "rationale": "r",
                        "pinning_test": "t",
                        "status": "open",
                    }
                ],
            },
        )

    def _trail_both_classes(self, tmp_path: Path, recorded_days_ago: int) -> None:
        _append_trail(
            tmp_path,
            [
                _trail_line(target_class="copy_freely", staleness=1, recorded_days_ago=recorded_days_ago),
                _trail_line(
                    target_class="snapshot_with_drift_alarm",
                    staleness=1,
                    recorded_days_ago=recorded_days_ago,
                ),
            ],
        )

    def test_unowned_inside_window_passes_visible(self, tmp_path):
        self._manifest_unowned_open(tmp_path)
        self._trail_both_classes(tmp_path, recorded_days_ago=5)
        verdict = evaluate_staleness(tmp_path, now=NOW)
        # unowned for 5 days ≤ 14 — visible but not blocking (an owner
        # can still be assigned); note the divergence-unresolved code
        # STILL fires (the disposition is open) — the escalation check
        # adds nothing here.
        assert verdict["ok"] is False  # unresolved divergence blocks
        assert "alarm-owner-escalation" not in _codes(verdict)

    def test_unowned_beyond_window_escalates(self, tmp_path):
        self._manifest_unowned_open(tmp_path)
        self._trail_both_classes(tmp_path, recorded_days_ago=20)
        verdict = evaluate_staleness(tmp_path, now=NOW)
        assert "alarm-owner-escalation" in _codes(verdict)

    def test_unowned_with_unprovable_age_fails_closed(self, tmp_path):
        self._manifest_unowned_open(tmp_path)
        # NO trail at all: the unowned age is unprovable ⇒ block
        verdict = evaluate_staleness(tmp_path, now=NOW)
        assert "alarm-owner-escalation" in _codes(verdict)
        row = next(r for r in verdict["reasons"] if r["code"] == "alarm-owner-escalation")
        assert row["age_provable"] is False

    def test_owned_alarm_never_escalates(self, tmp_path):
        # owned + open: the analyze/escalate lane owns it; only the
        # divergence-unresolved code fires (no ownership escalation).
        _write_manifest(tmp_path)  # snapshot alarm_owner: owner-b
        _append_trail(
            tmp_path,
            [
                _trail_line(target_class="copy_freely", staleness=1),
                _trail_line(target_class="snapshot_with_drift_alarm", staleness=1),
            ],
        )
        doc = yaml.safe_load((tmp_path / "MANIFEST.yaml").read_text())
        doc["snapshot_with_drift_alarm"]["divergence_register"].append(
            {
                "id": 9,
                "files": ["z.ts"],
                "delta": "+1",
                "rationale": "r",
                "pinning_test": "t",
                "status": "open",
            }
        )
        (tmp_path / "MANIFEST.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
        verdict = evaluate_staleness(tmp_path, now=NOW)
        assert "alarm-owner-escalation" not in _codes(verdict)
        assert "divergence-unresolved" in _codes(verdict)


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════


class TestCli:
    def test_exit_fresh(self, tmp_path, capsys):
        _write_manifest(tmp_path)
        _append_trail(
            tmp_path,
            [
                _trail_line(target_class="copy_freely", staleness=1),
                _trail_line(target_class="snapshot_with_drift_alarm", staleness=1),
            ],
        )
        rc = main([str(tmp_path)])
        assert rc == EXIT_FRESH
        assert "PLUGIN-STALENESS=fresh" in capsys.readouterr().out

    def test_exit_stale_with_token(self, tmp_path, capsys):
        _write_manifest(tmp_path)
        rc = main([str(tmp_path)])
        assert rc == EXIT_STALE
        out = capsys.readouterr().out
        assert "PLUGIN-STALENESS=stale code=staleness-unknown" in out

    def test_json_flag_emits_verdict(self, tmp_path, capsys):
        _write_manifest(tmp_path)
        main([str(tmp_path), "--json"])
        out = capsys.readouterr().out
        # the JSON verdict is followed by the human-readable line
        verdict, _ = json.JSONDecoder().raw_decode(out[out.index("{"):])
        assert verdict["ok"] is False

"""FIX-WAVE-2 pins — C2 tools seam + anomaly-service import trap.

Registered pack: ``skill_capture_observability_unit_test`` (EXTENDED) — see
``.agents/tester/PACKS.md``.

Two fixes, four discriminating pins. Each pin names its OWN mutation:

* **C2 tools seam — counter + sink record**: when ``skill_execute_capture``
  is invoked with capture disabled (default OFF), it increments the
  ``capture_lane_off`` ADMISSION-refusal counter AND writes a sink record
  carrying the prompt SHAPE only (never the raw prompt).
  (Named mutation: make the helper call a no-op → RED).
* **C2 tools seam — containment**: the seam must NEVER raise into the
  tool lane, even if the observability helper raises.
  (Named mutation: remove the tool's try/except around the helper call → RED).
* **Anomaly service import purity**: importing ``daemon.services.anomaly_service``
  registers NO metric families in the process-wide registry (mirrors M-1b).
  (Named mutation: restore the eager module-level vocab import in
  ``anomaly_service.py`` → RED).
* **Deferred vocabulary canonical parity**: the deferred and fallback vocabularies
  (``ANOMALY_CATEGORIES``, ``ANOMALY_SEVERITIES``, ``RESERVED``) match the
  canonical ones at ``daemon/services/metrics_query/wiring/vocab.py``.
  (Named mutation: add/remove a category in either list → RED).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import daemon.services.skill_capture_observability as observability
from daemon.tools.skill_evolution_tools import create_skill_evolution_tools

REPO_ROOT = Path(__file__).resolve().parents[2]
CANARY_PROMPT = "TOP_SECRET_USER_PROMPT_CANARY_ABCD1234XYZ"


def _run_in_fresh_interpreter(code: str) -> str:
    """Run ``code`` in a FRESH interpreter rooted at THIS tree."""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=120,
    )
    assert completed.returncode == 0, (
        "the subprocess probe failed:\n"
        f"stdout: {completed.stdout[-2000:]}\n"
        f"stderr: {completed.stderr[-2000:]}"
    )
    return completed.stdout.strip()


# ── Pin 1: C2 tools seam counter + sink record (with NO raw prompt) ─────────


@pytest.mark.asyncio
async def test_c2_tools_seam_increments_counter_and_writes_sink_record(
    tmp_path, monkeypatch
):
    """C2 — the tools-lane refusal increments counter and writes sink record.

    Named mutation: make the helper call a no-op in
    ``daemon/tools/skill_evolution_tools.py`` → counter does not increment
    and no sink record is written → this pin goes RED.
    """
    monkeypatch.delenv("ENSEMBLE_SKILL_CAPTURE_ENABLED", raising=False)
    monkeypatch.setenv("ENSEMBLE_DATA_DIR", str(tmp_path))
    observability.reset_counters()

    manager = MagicMock()
    mock_instance = MagicMock()
    mock_instance.agent_id = "test-agent"
    mock_instance.project_id = "test-project"
    manager._instance_repository.get.return_value = mock_instance

    tools = {t.name: t for t in create_skill_evolution_tools(manager, "inst-closure")}
    tool = tools["skill_execute_capture"]

    result = await tool.ainvoke({
        "instance_id": "inst-origin",
        "task_message": CANARY_PROMPT,
        "iterations": 3,
        "duration_seconds": 45,
    })

    envelope = json.loads(result)
    assert envelope["skipped"] is True
    assert "disabled" in envelope["reason"].lower()

    # 1. Counter must increment
    snapshot = observability.counters_snapshot()
    assert snapshot["admission_refusals"].get("capture_lane_off") == 1, (
        "the capture-lane-off refusal in skill_execute_capture must increment "
        "the ADMISSION-refusal counter"
    )

    # 2. Sink record must be written
    sink_dir = tmp_path / observability.SINK_DIR_NAME
    assert sink_dir.exists(), "sink directory was not created"
    sink_files = list(sink_dir.glob("*.jsonl"))
    assert sink_files, "no sink jsonl files written"

    all_content = "".join(f.read_text(encoding="utf-8") for f in sink_files)
    records = [json.loads(line) for line in all_content.splitlines() if line.strip()]
    refusal_records = [
        r
        for r in records
        if r.get("stage") == "admission" and r.get("refusal_reason") == "capture_lane_off"
    ]
    assert len(refusal_records) == 1, (
        f"expected 1 capture_lane_off refusal record, got {len(refusal_records)}"
    )
    rec = refusal_records[0]
    assert rec["outcome"] == "refused"
    assert rec["prompt_source"] == "skill_execute_capture"
    assert rec["agent_id"] == "test-agent"
    assert "sha256_16" in rec["prompt_shape"]
    assert rec["prompt_shape"]["sha256_16"]
    assert "length" in rec["prompt_shape"]
    assert "length_bucket" in rec["prompt_shape"]

    # 3. Canary prompt MUST NEVER appear in the sink
    assert CANARY_PROMPT not in all_content, (
        "the raw prompt text leaked into the sink log (DS-18 / F7 violation)"
    )


# ── Pin 2: C2 tools seam never raises into tool lane ─────────────────────────


@pytest.mark.asyncio
async def test_c2_tools_seam_never_raises_into_tool_lane(monkeypatch):
    """C2 — the tools-lane refusal seam must NEVER raise into the tool lane.

    Named mutation: remove the try/except around the observability helper call
    in ``skill_execute_capture`` → this pin goes RED.
    """
    monkeypatch.delenv("ENSEMBLE_SKILL_CAPTURE_ENABLED", raising=False)

    manager = MagicMock()
    tools = {t.name: t for t in create_skill_evolution_tools(manager, "inst-closure")}
    tool = tools["skill_execute_capture"]

    with patch(
        "daemon.services.skill_capture_observability.record_capture_lane_disabled_refusal",
        side_effect=RuntimeError("simulated sink crash"),
    ):
        result = await tool.ainvoke({
            "instance_id": "inst-origin",
            "task_message": "test prompt",
            "iterations": 1,
            "duration_seconds": 10,
        })

    envelope = json.loads(result)
    assert envelope["skipped"] is True


# ── Pin 3: Anomaly service import registers no metric families ───────────────


def test_anomaly_service_import_registers_no_families():
    """Importing daemon.services.anomaly_service must NOT pull in metrics wiring.

    Named mutation: restore the module-level import of
    ``daemon.services.metrics_query.wiring.vocab`` in
    ``daemon/services/anomaly_service.py`` → this pin goes RED.
    """
    output = _run_in_fresh_interpreter(
        "import sys\n"
        "import daemon.services.anomaly_service\n"
        "print('daemon.services.metrics_query.wiring' in sys.modules)\n"
    )

    assert output.splitlines()[-1] == "False", (
        "importing daemon.services.anomaly_service pulled in the metrics wiring "
        "package — its __init__ registers metric families at import time, which "
        "diverges the committed exposition artifact"
    )


# ── Pin 4: Deferred vocabulary matches canonical vocabulary ─────────────────


def test_anomaly_service_deferred_vocabulary_matches_canonical():
    """The deferred vocabulary and fallback tuples must match canonical ones.

    Named mutation: add or remove a category in _ANOMALY_CATEGORIES_FALLBACK
    or ANOMALY_CATEGORIES → this pin goes RED.
    """
    output = _run_in_fresh_interpreter(
        "from daemon.services.anomaly_service import (\n"
        "    _ANOMALY_CATEGORIES_FALLBACK,\n"
        "    _ANOMALY_SEVERITIES_FALLBACK,\n"
        "    _RESERVED_FALLBACK,\n"
        "    ANOMALY_CATEGORIES,\n"
        "    ANOMALY_SEVERITIES,\n"
        "    RESERVED,\n"
        ")\n"
        "from daemon.services.metrics_query.wiring.vocab import (\n"
        "    ANOMALY_CATEGORIES as CANONICAL_CATEGORIES,\n"
        "    ANOMALY_SEVERITIES as CANONICAL_SEVERITIES,\n"
        "    RESERVED as CANONICAL_RESERVED,\n"
        ")\n"
        "categories_ok = (\n"
        "    tuple(ANOMALY_CATEGORIES)\n"
        "    == tuple(CANONICAL_CATEGORIES)\n"
        "    == tuple(_ANOMALY_CATEGORIES_FALLBACK)\n"
        ")\n"
        "severities_ok = (\n"
        "    tuple(ANOMALY_SEVERITIES)\n"
        "    == tuple(CANONICAL_SEVERITIES)\n"
        "    == tuple(_ANOMALY_SEVERITIES_FALLBACK)\n"
        ")\n"
        "reserved_ok = (\n"
        "    str(RESERVED)\n"
        "    == str(CANONICAL_RESERVED)\n"
        "    == str(_RESERVED_FALLBACK)\n"
        ")\n"
        "print(categories_ok and severities_ok and reserved_ok)\n"
    )

    assert output.splitlines()[-1] == "True", (
        "the anomaly_service deferred or fallback vocabulary has drifted from "
        "the canonical vocabulary at daemon/services/metrics_query/wiring/vocab.py"
    )

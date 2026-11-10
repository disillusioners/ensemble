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

# The lane's SAFE sink redirect + the shared-path sentinel (the
# order-dependent leak fix: a teardown may never restore None).
from tests.helpers.skill_sink_redirect import (
    lane_test_end,
    lane_test_start,
    restore_safe_sink_redirect,
    snapshot_real_sink_dir,
)

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


@pytest.fixture(autouse=True)
def _observability_sandbox(tmp_path, request):
    """Point the sink at a SCRATCH dir for every test in this file.

    The teardown NEVER restores the override to ``None``: that
    re-exposed the checkout's REAL data dir for every later write (the
    order-dependent sink leak).  It restores the SAFE session redirect
    and runs the shared-path sentinel.
    """
    lane_test_start(request.node.nodeid)
    before = snapshot_real_sink_dir()
    observability.configure_sink(tmp_path)
    observability.reset_counters()
    try:
        yield tmp_path
    finally:
        restore_safe_sink_redirect()
        observability.reset_counters()
        lane_test_end(request.node.nodeid, before)


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


# ── Pin 5: M9 — the two canonicalisers agree on BEHAVIOUR ─────────────────


def test_m9_both_entry_points_canonicalise_identically():
    """The same input string must canonicalise the SAME way through BOTH
    entry points.

    The lane's own canonicaliser (``skill_capture_observability``) did
    NEITHER lowercase NOR apply ``CATEGORY_SYNONYMS``, while the
    canonical one (``AnomalyService.normalize_category``) did both — so
    the SAME string canonicalised two ways ("PERF" -> "performance" in
    the anomaly service, -> "other" in the observability lane).

    Named mutation: make the observability canonicaliser skip the
    lowercase -> this pin goes RED.
    """
    output = _run_in_fresh_interpreter(
        "from daemon.services.anomaly_service import AnomalyService\n"
        "from daemon.services.skill_capture_observability import (\n"
        "    _canonical_category,\n"
        ")\n"
        "service = AnomalyService.__new__(AnomalyService)\n"
        "samples = [\n"
        "    'tool-failure', 'TOOL-FAILURE', ' Tool Failure ', 'perf',\n"
        "    'PERF', 'oom', 'OOM', 'unknown-thing', '', 'Wedge', 'queue',\n"
        "]\n"
        "ok = True\n"
        "for raw in samples:\n"
        "    a = service.normalize_category(raw)[0]\n"
        "    b = _canonical_category(raw)\n"
        "    if a != b:\n"
        "        ok = False\n"
        "print(ok)\n"
    )

    assert output.splitlines()[-1] == "True", (
        "the two anomaly-canonicalisation entry points DISAGREE — the same "
        "input string canonicalises two ways. The vocabulary + the "
        "canonicaliser must live ONCE (daemon/services/anomaly_vocab.py) "
        "and both services must delegate to it."
    )


def test_m9_the_canonicalisation_behaviour_is_the_canonical_one():
    """The canonicaliser must lowercase AND apply the synonym map.

    The agreement pin above catches DIVERGENCE between the two entry
    points; this one catches a SHARED defect (both entry points
    delegating to a canonicaliser that stopped folding the synonyms).

    Named mutation: drop the ``CATEGORY_SYNONYMS.get(...)`` fold from
    ``daemon/services/anomaly_vocab.py`` → this pin goes RED.
    """
    output = _run_in_fresh_interpreter(
        "from daemon.services.anomaly_service import AnomalyService\n"
        "from daemon.services.skill_capture_observability import (\n"
        "    _canonical_category,\n"
        ")\n"
        "service = AnomalyService.__new__(AnomalyService)\n"
        "expected = {\n"
        "    'tool-failure': 'tool-failure',\n"
        "    'TOOL-FAILURE': 'tool-failure',\n"
        "    ' Tool Failure ': 'tool-failure',\n"
        "    'perf': 'performance',\n"
        "    'PERF': 'performance',\n"
        "    'oom': 'resource-exhaustion',\n"
        "    'OOM': 'resource-exhaustion',\n"
        "    'unknown-thing': 'other',\n"
        "    '': 'other',\n"
        "    'Wedge': 'wedge-suspected',\n"
        "    'queue': 'queue-scheduling',\n"
        "}\n"
        "ok = True\n"
        "for raw, want in expected.items():\n"
        "    if service.normalize_category(raw)[0] != want:\n"
        "        ok = False\n"
        "    if _canonical_category(raw) != want:\n"
        "        ok = False\n"
        "print(ok)\n"
    )

    assert output.splitlines()[-1] == "True", (
        "the anomaly canonicalisation BEHAVIOUR drifted — the canonicaliser "
        "must lowercase + strip, match the closed vocabulary, and then "
        "apply CATEGORY_SYNONYMS (e.g. 'PERF' -> 'performance', "
        "'Wedge' -> 'wedge-suspected'); an unknown category folds to the "
        "reserved 'other'"
    )


def test_m9_the_shared_vocabulary_module_is_import_pure():
    """The shared vocabulary module must register NO metric families.

    Importing ANY submodule of the metrics wiring PACKAGE executes its
    ``__init__``, which registers the phase's metric families at import
    time (an import-time side effect that diverged the committed
    exposition artifact).

    Named mutation: import the wiring vocabulary at module level in
    ``daemon/services/anomaly_vocab.py`` -> this pin goes RED.
    """
    output = _run_in_fresh_interpreter(
        "import sys\n"
        "import daemon.services.anomaly_vocab\n"
        "print('daemon.services.metrics_query.wiring' in sys.modules)\n"
    )

    assert output.splitlines()[-1] == "False", (
        "importing daemon.services.anomaly_vocab pulled in the metrics "
        "wiring package — its __init__ registers metric families at "
        "import time, which diverges the committed exposition artifact"
    )

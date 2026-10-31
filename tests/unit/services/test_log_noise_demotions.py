"""Log-noise demotion spot checks (step-5 log hygiene, part 2).

Behavioral caplog checks for the cheaply-drivable sites (the
critical-notes routine telemetry emitters) plus pure-helper checks
for the wipe-id cap and the PP1 zero-watcher dedupe, plus
source-level level pins for the demotion sites whose drive path
needs full service fixtures (job_queue [TRACE], task_processor,
MCP-load, stale-pause, cleanup_non_terminal_jobs, RDRS lane 2,
compaction seam / no-op skips).

Contract under test:
  * demoted lines are NOT captured when the logger runs at INFO,
    but ARE captured at DEBUG;
  * the wipe JOURNAL id payload is count + first-5-ids (< 300
    chars) instead of a 500-id dump — level stays WARNING;
  * the PP1 zero-watcher WARN fires ONCE per work_id per process;
  * the pinned PP1 literal prefix survives the shortening.
"""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path

import pytest

from daemon.repositories.task.repository import (
    _DOOMED_ID_LOG_CAP,
    _capped_doomed_id_repr,
)
from daemon.services.critical_notes_selection_orchestrator import (
    _emit_critical_notes_hint,
    _emit_critical_notes_log,
)
from daemon.services import work_notifier as wn

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _src(relpath: str) -> str:
    return (_REPO_ROOT / relpath).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Behavioral: critical-notes routine telemetry (demoted INFO → DEBUG)
# ---------------------------------------------------------------------------

_TELEMETRY = {
    "selected": 7,
    "total": 12,
    "floor_applied": False,
    "instance_id": "deadbeef0001",
}


class TestCriticalNotesRoutineDemotion:
    def test_routine_line_not_at_info(self, caplog):
        """At INFO capture level the routine telemetry line must NOT
        be emitted; the ops-alertable Degraded lines stay INFO and
        keep working.
        """
        with caplog.at_level(logging.INFO):
            _emit_critical_notes_log(_TELEMETRY)
            _emit_critical_notes_hint(8)
        routine = [
            r for r in caplog.records
            if "[CriticalNotes] selected=" in r.message
            or "[CriticalNotes] hint_drop_count=" in r.message
            or "tiered=gated_off" in r.message
        ]
        assert routine == [], (
            "routine [CriticalNotes] telemetry must be DEBUG-only after "
            "the step-5 demotion"
        )
        # The Degraded prefix is untouched — but floor_applied=False
        # here, so no Degraded line is expected either.
        assert not any("stage=floor" in r.message for r in caplog.records)

    def test_routine_lines_present_at_debug(self, caplog):
        with caplog.at_level(logging.DEBUG):
            _emit_critical_notes_log(_TELEMETRY)
            _emit_critical_notes_hint(8)
        assert any(
            "[CriticalNotes] selected=7 total=12 floor_applied=False "
            "instance=deadbeef0001" in r.message
            for r in caplog.records
        )
        assert any(
            "[CriticalNotes] hint_drop_count=8" in r.message
            for r in caplog.records
        )

    def test_degraded_floor_line_stays_info(self, caplog):
        """The Degraded prefix is the ops-alertable surface — it must
        still be visible at INFO capture level.
        """
        with caplog.at_level(logging.INFO):
            _emit_critical_notes_log({**_TELEMETRY, "floor_applied": True})
        assert any(
            "[CriticalNotes:Degraded] stage=floor" in r.message
            for r in caplog.records
        )


# ---------------------------------------------------------------------------
# Pure helper: wipe JOURNAL doomed-id shortening (cap 500 → 5)
# ---------------------------------------------------------------------------


class TestDoomedIdShortening:
    def test_cap_is_five(self):
        assert _DOOMED_ID_LOG_CAP == 5

    def test_large_list_renders_count_plus_first_five_under_300(self):
        ids = [str(uuid.uuid4()) for _ in range(1350)]
        rendered = _capped_doomed_id_repr(ids)
        # Count + first 5 ids + truncation marker — payload < 300.
        assert len(rendered) < 300, rendered
        for sample in ids[:5]:
            assert sample in rendered, sample
        assert ids[5] not in rendered, (
            "the 6th id must be truncated away"
        )
        assert "[+ 1345 more truncated]" in rendered

    def test_small_list_renders_verbatim_without_marker(self):
        ids = [str(uuid.uuid4()) for _ in range(3)]
        rendered = _capped_doomed_id_repr(ids)
        assert rendered == repr(ids)
        assert "more truncated" not in rendered

    def test_full_line_shape_is_far_below_the_old_kb_scale(self):
        """The rendered payload keeps every wipe JOURNAL line in the
        hundreds-of-chars range (was ~30-40KB at the 500-id cap).
        """
        prefix = (
            "JOURNAL: TaskRepository.clear_all preserve_in_flight=True "
            "about to DELETE 1350 task row(s) (env-poison family audit). "
            "doomed_work_ids="
        )
        ids = [str(uuid.uuid4()) for _ in range(1350)]
        assert len(prefix + _capped_doomed_id_repr(ids)) < 400


# ---------------------------------------------------------------------------
# PP1 zero-watcher WARN: once-per-work_id dedupe + pinned literal
# ---------------------------------------------------------------------------


class TestPP1ZeroWatcherDedupe:
    @pytest.fixture(autouse=True)
    def _fresh_dedupe_state(self):
        wn._PP1_ZERO_WATCHER_WARN_SEEN.clear()
        yield
        wn._PP1_ZERO_WATCHER_WARN_SEEN.clear()

    def test_second_fire_same_work_id_is_silent(self):
        wid = str(uuid.uuid4())
        assert wn._pp1_zero_watcher_should_warn(wid) is True
        assert wn._pp1_zero_watcher_should_warn(wid) is False
        assert wn._pp1_zero_watcher_should_warn(wid) is False

    def test_distinct_work_ids_each_warn_once(self):
        assert wn._pp1_zero_watcher_should_warn(str(uuid.uuid4())) is True
        assert wn._pp1_zero_watcher_should_warn(str(uuid.uuid4())) is True

    def test_none_work_id_is_its_own_key(self):
        assert wn._pp1_zero_watcher_should_warn(None) is True
        assert wn._pp1_zero_watcher_should_warn(None) is False

    def test_dedupe_set_is_bounded(self):
        """Flooding past the cap clears the set (re-warn after reset)
        instead of growing without limit.
        """
        for _ in range(wn._PP1_ZERO_WATCHER_WARN_SEEN_CAP):
            wn._pp1_zero_watcher_should_warn(str(uuid.uuid4()))
        flood_id = str(uuid.uuid4())
        # Set is at cap — flood_id is new, so the cap-clear fires and
        # the set resets; flood_id still warns (it is added post-clear
        # on this same call), and the set is no longer at cap.
        assert wn._pp1_zero_watcher_should_warn(flood_id) is True
        assert len(wn._PP1_ZERO_WATCHER_WARN_SEEN) == 1
        # A repeat of the pre-clear ids re-warns once after the reset.
        assert wn._pp1_zero_watcher_should_warn("anything") is True

    def test_pinned_pp1_literal_survives_in_source(self):
        """The source-level pin (test_report_delivery_bug_family_pins)
        requires the literal ``PP1 zero-watcher terminal fire`` in
        work_notifier.py — the dedupe + shortening must not drop it.
        """
        src = _src("daemon/services/work_notifier.py")
        assert re.search(r"PP1 zero-watcher terminal fire", src)
        # The dedupe helper is wired into the gate.
        assert "_pp1_zero_watcher_should_warn(work_id)" in src


# ---------------------------------------------------------------------------
# Source-level level pins for fixture-heavy demotion sites
# ---------------------------------------------------------------------------


class TestDemotionSourcePins:
    """These sites need full service fixtures to drive behaviorally;
    the source pin asserts the call-site LEVEL (the step-5 contract)
    so an accidental re-promotion cannot regress silently.
    """

    def test_job_queue_trace_lines_are_debug(self):
        src = _src("daemon/services/job_queue_service.py")
        assert re.search(
            r"logger\.debug\(\s*f\"\[TRACE\] start_job: using instance_id",
            src,
        ), "[TRACE] start_job: using instance_id must be logger.debug"
        assert re.search(
            r"logger\.debug\(\s*f\"\[TRACE\] start_job: acquiring queue lock",
            src,
        ), "[TRACE] start_job: acquiring queue lock must be logger.debug"
        assert not re.search(
            r"logger\.info\(\s*f\"\[TRACE\] start_job: "
            r"(using instance_id|acquiring queue lock)",
            src,
        ), "[TRACE] start_job lock traces must not be logger.info"

    def test_task_processor_processing_line_is_debug(self):
        src = _src("daemon/services/task_processor.py")
        assert re.search(
            r"logger\.debug\(\s*f\"Processing message task", src
        ), "'Processing message task' must be logger.debug"
        assert not re.search(
            r"logger\.info\(\s*f\"Processing message task", src
        )

    def test_mcp_load_lines_are_debug_and_count_only(self):
        instance_src = _src("daemon/tools/instance.py")
        assert re.search(
            r"logger\.debug\(\s*f\"Loaded \{len\(mcp_tools\)\} MCP tools",
            instance_src,
        ), "the Loaded-MCP-tools line must be logger.debug"
        assert "{mcp_tool_names[:5]}" not in instance_src, (
            "the Loaded-MCP-tools line must not inline tool names"
        )
        service_src = _src("daemon/services/mcp_service.py")
        assert re.search(
            r"logger\.debug\(\s*f\"Lazy-loaded \{len\(all_tools\)\} MCP tool schemas",
            service_src,
        ), "the Lazy-loaded MCP schemas line must be logger.debug"

    def test_stale_pause_lines_are_outcome_gated(self):
        src = _src("daemon/services/stale_pause_sweep.py")
        assert re.search(
            r"logger\.debug\(\s*\n\s*\"stale-pause sweep: candidates=0",
            src,
        ), "the candidates=0 idle line must be logger.debug"
        assert "logger.info(_stale_pause_summary" in src
        assert "logger.debug(_stale_pause_summary" in src

    def test_cleanup_non_terminal_summary_is_outcome_gated(self):
        src = _src("daemon/services/job_queue_service.py")
        assert "logger.info(_cleanup_summary" in src
        assert "logger.debug(_cleanup_summary" in src

    def test_rdrs_lane2_marker_is_outcome_gated(self):
        src = _src("daemon/services/report_delivery_recovery.py")
        # The unconditional entry marker is gone.
        assert not re.search(
            r"logger\.info\(\s*\n\s*\"RDRS lane 2: post-wipe recovery",
            src,
        ), "the lane-2 marker must not log unconditionally at INFO"
        assert "logger.info(_lane2_marker)" in src
        assert "logger.debug(_lane2_marker)" in src

    def test_compaction_routine_logs_are_debug(self):
        messaging_src = _src("daemon/services/instance_messaging.py")
        assert 'logger.debug(" ".join(log_parts))' in messaging_src
        assert 'logger.info(" ".join(log_parts))' not in messaging_src
        seam_src = _src("daemon/services/_compaction_persist_seam.py")
        assert re.search(
            r"logger\.debug\(\s*\n\s*\"\[Compaction seam\] stamp-only anti-refire",
            seam_src,
        )

    def test_under_budget_noop_skips_do_not_log_at_all(self):
        src = _src("daemon/compaction.py")
        assert "under-budget all-injected skip" not in src
        assert "under-budget min-messages skip" not in src
        # The skip RETURNS survive — only the log calls were removed.
        assert 'skip_reason="skipped_injections_dominate"' in src
        assert 'skip_reason="skipped_below_min_messages"' in src

"""The LEAK PIN — no test of this lane may write OUTSIDE ``tmp_path``.

THE REGRESSION THIS GUARDS (E1/D lane's L7 finding, 2026-11-10).  The
observability sink writes its ``skill-capture-*.jsonl`` records into the
resolved data dir — ``ENSEMBLE_DATA_DIR`` > ``DATA_DIR`` > ``./data`` — and
``./data`` is **CWD-relative**.  A test that drives the production
refusal seam without pointing the sink at a scratch dir therefore writes
into the **checkout's** ``data/skill-capture-observability/`` tree.  The
lane's C2 seam test did exactly that: **~1,260 B per run**, recreated on
every run.

WHY A PIN AND NOT A HYGIENE NOTE.  The residue is **invisible to every
gate**: the tree is **gitignored** (``.gitignore:28`` → ``data/``), so
``git status`` never shows it, the residue gate never sees it, and the
staging gate never sweeps it.  A leaked file is therefore invisible to
exactly the mechanisms that are supposed to notice it.  This pin is the
only guard.

WHY IT IS NOT VACUOUS.  The pin does not assert "the sink is configured" —
it **OBSERVES THE WRITE TARGET**: it drives the production refusal seam
and asserts the record landed under ``tmp_path``, and (the second node) it
runs the leaking file in a SUBPROCESS and asserts the write landed in the
sandbox while **nothing** carrying that file's marker appeared in the
checkout's real sink dir.

Mutation leg (pack ``metrics_capture_lane_off_seam_unit_test``):
``M-C2d`` — remove the autouse ``configure_sink(tmp_path)`` fixture from
``tests/unit/test_skill_metrics_capture_lane_off_seam.py`` ⇒ the refusal
record escapes into the checkout's ``data/`` tree ⇒ this pin REDs (both
the "the write landed in the sandbox" and the "nothing escaped" halves).
GREEN again after the sha-verified restore.

WHY THE MARKER, NOT "THE REAL DIR IS UNCHANGED".  The checkout's
``data/`` tree is **shared** with every other lane working in this
worktree, so a global "the real dir is byte-identical across the run"
assertion would be racy against a concurrent lane's test run.  This pin
therefore looks for **its own marker** — a value only this lane's records
carry — which is race-tolerant by construction.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from daemon.services import skill_capture_observability as observability
from daemon.services.skill_metrics_service import (
    INJECTED_SKILLS_METADATA_KEY,
    SkillMetricsService,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The checkout's REAL sink dir — the tree the leak polluted.
REAL_SINK_DIR = REPO_ROOT / "data" / "skill-capture-observability"

#: The lane file that leaked (round-3 C2 seam test).
LEAKING_FILE = "tests/unit/test_skill_metrics_capture_lane_off_seam.py"

#: A value ONLY that file's refusal records carry (``agent_id``).
C2_MARKER = "agent-c2"


class _Config:
    capture_min_iterations = 5
    capture_min_duration_seconds = 60


def _service(agent_id: str):
    return SkillMetricsService(
        usage_repo=SimpleNamespace(
            has_genuinely_evolved_for_instance=lambda instance_id: False
        ),
        skill_repo=SimpleNamespace(),
        trigger_repo=SimpleNamespace(),
        ab_test_repo=SimpleNamespace(),
        config=_Config(),
        instance_repo=SimpleNamespace(
            get=lambda instance_id: SimpleNamespace(
                instance_id=instance_id,
                instance_metadata={INJECTED_SKILLS_METADATA_KEY: ["skill-1"]},
            )
        ),
        evolution_service=SimpleNamespace(),
        agent_id_resolver=lambda a, instance_id=None: SimpleNamespace(
            skill_injection=True
        ),
    )


@pytest.fixture(autouse=True)
def _observability_sandbox(tmp_path):
    """This file must not leak either — it drives the same seam."""
    observability.configure_sink(tmp_path)
    observability.reset_counters()
    observability.set_anomaly_reporter(None)
    try:
        yield tmp_path
    finally:
        observability.configure_sink(None)
        observability.reset_counters()
        observability.set_anomaly_reporter(None)


def _real_sink_records() -> list[tuple[Path, str]]:
    """Every record in the checkout's REAL sink dir, as ``(path, text)``."""
    if not REAL_SINK_DIR.is_dir():
        return []
    out: list[tuple[Path, str]] = []
    for path in sorted(REAL_SINK_DIR.glob("skill-capture-*.jsonl")):
        try:
            out.append((path, path.read_text(encoding="utf-8", errors="ignore")))
        except OSError:  # pragma: no cover — a racing prune
            continue
    return out


class TestTheRefusalWriteLandsUnderTmpPath:
    """The write TARGET is observed, not assumed."""

    @pytest.mark.asyncio
    async def test_the_refusal_record_is_written_under_tmp_path(
        self, tmp_path
    ) -> None:
        marker = f"leak-pin-{uuid4().hex}"
        service = _service(marker)
        assert (
            await service._check_capture_eligibility(
                instance_id="inst-leak-pin",
                agent_id=marker,
                project_id="p-leak-pin",
                task_message="the leak canary",
                task_succeeded=True,
                iterations=10,
                duration_seconds=120,
            )
            is None
        ), "the gate must still refuse (the capture lane is off)"

        # The sink's resolved target lies under tmp_path.
        sink_dir = observability.resolve_sink_dir()
        assert sink_dir.is_relative_to(tmp_path), (
            f"the sink must resolve under tmp_path, not {sink_dir}"
        )

        # The write REALLY happened (non-vacuity) — and it happened HERE.
        written = list(tmp_path.rglob("skill-capture-*.jsonl"))
        assert written, (
            "the refusal record must actually be written — otherwise this "
            "pin proves nothing"
        )
        blob = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore") for path in written
        )
        assert marker in blob, "the record must carry this run's marker"

        # …and NOTHING escaped into the checkout's real sink dir.
        for path, text in _real_sink_records():
            assert marker not in text, (
                f"{path} carries this run's refusal record — the write "
                f"escaped tmp_path (the L7 leak)"
            )

    @pytest.mark.asyncio
    async def test_the_sink_never_resolves_into_the_checkout(
        self, tmp_path
    ) -> None:
        """The resolved sink dir is never the checkout's ``data/`` tree."""
        sink_dir = observability.resolve_sink_dir()
        assert sink_dir.is_relative_to(tmp_path)
        assert not sink_dir.is_relative_to(REPO_ROOT / "data"), (
            f"the sink resolved into the checkout's data tree: {sink_dir}"
        )


class TestTheLaneFileItselfCannotLeak:
    """The second half: the leaking FILE, run as a subprocess."""

    def test_running_the_lane_file_writes_nothing_outside_tmp_path(
        self, tmp_path
    ) -> None:
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in ("ENSEMBLE_DATA_DIR", "DATA_DIR")
        }
        # pytest's tmp_path base follows TMPDIR ⇒ the subprocess's writes
        # are observable under the sandbox.
        env["TMPDIR"] = str(sandbox)
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                LEAKING_FILE,
                "-q",
                "--tb=line",
                "-p",
                "no:randomly",
                "--override-ini=addopts=",
            ],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert completed.returncode == 0, (
            f"the lane file must pass on its own: {completed.stdout[-2000:]}"
        )

        # NON-VACUITY: the refusal write really happened during that run…
        sandboxed = list(sandbox.rglob("skill-capture-*.jsonl"))
        assert sandboxed, (
            "the refusal write must actually happen during the run — "
            "otherwise this pin proves nothing"
        )
        # …and it happened in the SANDBOX, not in the checkout.
        for path, text in _real_sink_records():
            assert C2_MARKER not in text, (
                f"{path} carries the lane file's refusal record — the test "
                f"wrote OUTSIDE tmp_path (the L7 leak)"
            )

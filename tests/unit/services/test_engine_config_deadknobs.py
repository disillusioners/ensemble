"""Dead-knob cleanup + misfiled re-label pins (Engine Arc P1 — T1.12/T1.13).

Covers acceptance gates:

* **AC10** — ``lease_heartbeat_interval_seconds``,
  ``worker_poll_interval`` and ``checkpoint_interval`` are gone from their
  config classes, and ZERO references remain anywhere in ``daemon/``,
  ``tests/``, ``test/`` or ``config.yaml``. The COLLECTION-level proof
  (no pydantic ``unexpected keyword``, no ``MagicMock(spec=…)``
  ``AttributeError``) is the pack's ``--collect-only`` gate, not this
  file — a pytest module cannot collect itself twice.
* **AC11** — ``observer_health_check_interval_seconds`` and
  ``min_interval_seconds`` keep their class AND their env var; only the
  descriptions changed (no field moves).

Fence discipline: static/source scans + config-class introspection only;
no daemon boot, no DB.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from daemon.config import (
    Config,
    JobSystemConfig,
    NotifyUserConfig,
    PersistenceConfig,
    ServicesConfig,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

#: The three deleted knobs, and the class each used to live on.
DELETED = {
    "lease_heartbeat_interval_seconds": ServicesConfig,
    "worker_poll_interval": ServicesConfig,
    "checkpoint_interval": PersistenceConfig,
}

#: Scanned trees/files for the AC10 grep gate (repo-wide, not tests-only —
#: review fold F-2).
SCAN_TARGETS = ("daemon", "tests", "test")
SCAN_TOP_FILES = ("config.yaml",)


#: Files DELIBERATELY excluded from the reference sweep — they are the
#: fence itself, so they must be allowed to NAME the retired knobs:
#: the assertion below proves the names cannot be USED, which requires
#: spelling them in the pin. This is the standard house carve-out for a
#: retirement pin (the metrics-family adjudication pack does the same).
SELF_EXCLUSIONS = {
    "tests/unit/services/test_engine_config_deadknobs.py",
    "test/packs/engine_substrate_unit_test.sh",
}


def _iter_py_files(root: Path):
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if ".venv" in path.parts:
            continue
        if str(path.relative_to(REPO_ROOT)) in SELF_EXCLUSIONS:
            continue
        yield path


class TestDeadKnobsAreGone:
    @pytest.mark.parametrize("knob", sorted(DELETED))
    def test_knob_absent_from_its_config_class(self, knob):
        cls = DELETED[knob]
        assert knob not in cls.model_fields, f"{cls.__name__}.{knob} still exists"

    def test_knob_absent_from_every_config_class(self):
        classes = (
            ServicesConfig,
            PersistenceConfig,
            JobSystemConfig,
            NotifyUserConfig,
        )
        for knob in DELETED:
            for cls in classes:
                assert knob not in cls.model_fields, f"{cls.__name__}.{knob}"

    def test_services_config_field_count_dropped_by_two(self):
        """F-30: no COUNT pin exists — this pin is a DELTA, not an absolute.

        The two removed ``ServicesConfig`` fields (``worker_poll_interval``,
        ``lease_heartbeat_interval_seconds``) must be gone; the class must
        still be constructible with all its remaining defaults. There is
        deliberately NO ``len(model_fields) == N`` assertion here (fence
        F-30: do not invent a count pin).
        """
        ServicesConfig()  # constructible
        assert len(ServicesConfig.model_fields) > 50

    def test_persistence_config_still_constructible_without_the_knob(self):
        cfg = PersistenceConfig(db_path=":memory:", checkpoint_ttl_hours=168)
        assert cfg.db_path == ":memory:"
        assert not hasattr(cfg, "checkpoint_interval")
        with pytest.raises(Exception):
            PersistenceConfig(checkpoint_interval=1)  # type: ignore[call-arg]

    def test_services_config_rejects_the_removed_kwargs(self):
        with pytest.raises(Exception):
            ServicesConfig(worker_poll_interval=0.5)  # type: ignore[call-arg]
        with pytest.raises(Exception):
            ServicesConfig(lease_heartbeat_interval_seconds=30.0)  # type: ignore[call-arg]


class TestNoRemainingReferences:
    @pytest.mark.parametrize("knob", sorted(DELETED))
    def test_zero_references_in_python_trees(self, knob):
        pattern = re.compile(rf"\b{re.escape(knob)}\b")
        offenders: list[str] = []
        for tree in SCAN_TARGETS:
            root = REPO_ROOT / tree
            if not root.is_dir():
                continue
            for path in _iter_py_files(root):
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:  # pragma: no cover — unreadable file
                    continue
                if pattern.search(text):
                    offenders.append(str(path.relative_to(REPO_ROOT)))
        assert offenders == [], f"{knob} still referenced in: {offenders}"

    @pytest.mark.parametrize("knob", sorted(DELETED))
    def test_zero_references_in_config_yaml(self, knob):
        pattern = re.compile(rf"\b{re.escape(knob)}\b")
        for name in SCAN_TOP_FILES:
            path = REPO_ROOT / name
            if not path.is_file():
                continue
            assert not pattern.search(path.read_text(encoding="utf-8")), (
                f"{knob} still present in {name}"
            )

    def test_config_yaml_persistence_section_lost_only_that_key(self):
        text = (REPO_ROOT / "config.yaml").read_text(encoding="utf-8")
        block = text.split("\npersistence:\n", 1)[1].split("\n# ", 1)[0]
        assert "checkpoint_ttl_hours" in block
        assert "checkpoint_cleanup_interval" in block
        assert "checkpoint_interval" not in block


class TestMisfiledRelabels:
    def test_observer_health_check_keeps_its_class_and_env_var(self):
        assert (
            "observer_health_check_interval_seconds"
            in JobSystemConfig.model_fields
        )
        field = JobSystemConfig.model_fields["observer_health_check_interval_seconds"]
        assert field.default == 300
        # The env var is derived from the CLASS prefix — a move would have
        # renamed it. Prove the prefix is still ENSEMBLE_JOB_SYSTEM_.
        assert JobSystemConfig.model_config["env_prefix"] == "ENSEMBLE_JOB_SYSTEM_"

    def test_observer_health_check_description_names_the_true_owner(self):
        field = JobSystemConfig.model_fields["observer_health_check_interval_seconds"]
        desc = field.description or ""
        assert "JobSystemConfig" in desc
        assert "job_feedback_observer" in desc
        assert "must not be moved" in desc

    def test_min_interval_keeps_its_class_and_env_var(self):
        assert "min_interval_seconds" in NotifyUserConfig.model_fields
        field = NotifyUserConfig.model_fields["min_interval_seconds"]
        assert field.default == 60
        assert NotifyUserConfig.model_config["env_prefix"] == "ENSEMBLE_NOTIFY_USER_"

    def test_min_interval_description_names_the_true_owner(self):
        field = NotifyUserConfig.model_fields["min_interval_seconds"]
        desc = field.description or ""
        assert "NotifyUserConfig" in desc
        assert "ENSEMBLE_NOTIFY_USER_MIN_INTERVAL_SECONDS" in desc
        assert "must not be moved" in desc

    def test_neither_knob_moved_into_services_config(self):
        assert "observer_health_check_interval_seconds" not in ServicesConfig.model_fields
        assert "min_interval_seconds" not in ServicesConfig.model_fields

    def test_config_still_mounts_both_sections(self):
        assert Config.model_fields["job_system"].default_factory is JobSystemConfig
        assert Config.model_fields["notify_user"].default_factory is NotifyUserConfig

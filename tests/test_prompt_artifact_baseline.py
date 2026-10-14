"""Phase A T13 — prompt-artifact baseline drift-detection tests (S9 / O-7).

Covers the D-BASH-ESCAPE accepted-risk control:
- ``daemon/tools/prompt_artifacts.py`` drift core: baseline generation,
  deterministic format, parse, check (modified/missing/added/malformed
  classification), read-only posture, agents-root resolution seam.
- The boot WARN leg that ``daemon/api.py`` invokes in lifespan
  (``log_baseline_drift_at_boot`` — the function IS the boot WARN; booting
  the full app in tests would drag the whole manager + DB stack, so the
  thin-wrapper seam is the honest test target).
- The generator CLI (``scripts/protection/generate_prompt_artifact_baseline.py``)
  generate + --check modes incl. exit-code contract consumed by the
  stage.sh leg (0 clean/absent · 1 drift · 2 no-agents-tree).
- ``sha256sum -c`` compatibility of the committed baseline format.

**Fence discipline** (2026-10-12 critical note, zero tolerance): every
fixture lives under ``tmp_path``; the agents-root resolution is redirected
via ``monkeypatch.setattr`` on ``_DEFAULT_AGENTS_ROOTS_CACHE`` + the
``reset_agents_roots_cache`` seam (same pattern as
``tests/test_prompt_artifact_guards.py``). NO live-shaped (HOME-derived)
path is ever resolved or mutated.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import daemon.tools.prompt_artifacts as pa

REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATOR = REPO_ROOT / "scripts" / "protection" / "generate_prompt_artifact_baseline.py"

ARTIFACTS = ("soul.md", "rule.md", "workflow.md", "tools_note.md",
             "tools.md", "skill.md")

_LINE_RE = re.compile(r"^[0-9a-f]{64}  agents/[^/]+/[^/]+$")


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def override_agents_roots(tmp_path, monkeypatch):
    """Redirect the guard's agents-root resolution at a tmp tree (fence).

    Mirrors the fixture in tests/test_prompt_artifact_guards.py: clears the
    cached tuple, builds ``<tmp_path>/agents/<agent>/...``, pins the cache,
    resets on teardown so no stale cache leaks between tests.
    """
    pa.reset_agents_roots_cache()
    agents_root = tmp_path / "agents"
    agents_root.mkdir()
    for agent in ("alpha", "beta"):
        agent_dir = agents_root / agent
        agent_dir.mkdir()
        for fname in ARTIFACTS:
            (agent_dir / fname).write_text(f"# {agent} {fname}\n", encoding="utf-8")
    monkeypatch.setattr(pa, "_DEFAULT_AGENTS_ROOTS_CACHE", (agents_root,))
    yield {"agents_root": agents_root, "tree": tmp_path}
    pa.reset_agents_roots_cache()


def _write_baseline(agents_root: Path) -> Path:
    baseline = pa.baseline_path_for(agents_root)
    baseline.write_text(pa.generate_baseline_text(agents_root), encoding="utf-8")
    return baseline


# ===========================================================================
# Baseline generation — format + determinism
# ===========================================================================


class TestBaselineGeneration:
    def test_roundtrip_clean_tree_reports_ok(self, override_agents_roots):
        root = override_agents_roots["agents_root"]
        _write_baseline(root)
        result = pa.check_baseline_drift()
        assert result.status == "ok"
        assert result.drift_count == 0
        # alpha(6 artifacts) + beta(6 artifacts); the baseline file itself
        # is a root-level dotfile — never matched by the depth-2 rule.
        assert result.verified_count == 12

    def test_format_is_deterministic_and_sha256sum_shaped(self, override_agents_roots):
        root = override_agents_roots["agents_root"]
        text1 = pa.generate_baseline_text(root)
        text2 = pa.generate_baseline_text(root)
        assert text1 == text2  # deterministic: same tree → byte-identical
        lines = text1.splitlines()
        assert len(lines) == 12
        assert all(_LINE_RE.match(line) for line in lines), lines
        paths = [line.split("  ", 1)[1] for line in lines]
        assert paths == sorted(paths)  # sorted by path
        # Tree-root-relative paths, agents/ prefix, exactly depth-2.
        assert all(p.startswith("agents/") and len(p.split("/")) == 3 for p in paths)

    def test_sha256sum_dash_c_validates_clean_tree(self, override_agents_roots):
        if shutil.which("sha256sum") is None:  # pragma: no cover
            pytest.skip("sha256sum not available on this host")
        root = override_agents_roots["agents_root"]
        _write_baseline(root)
        proc = subprocess.run(
            ["sha256sum", "-c", "agents/.prompt-artifacts.sha256"],
            cwd=override_agents_roots["tree"],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "WARNING" not in proc.stdout  # no improperly-formatted lines

    def test_underscore_dirs_covered_depth3_excluded(self, override_agents_roots):
        root = override_agents_roots["agents_root"]
        baby = root / "_baby_template"
        baby.mkdir()
        (baby / "soul.md").write_text("template\n", encoding="utf-8")
        # depth-3 under an agent dir — the guard ignores it, so the
        # baseline must too (same match rule, single-sourced).
        nested = root / "alpha" / "sub"
        nested.mkdir()
        (nested / "soul.md").write_text("nested\n", encoding="utf-8")
        text = pa.generate_baseline_text(root)
        assert "agents/_baby_template/soul.md" in text
        assert "agents/alpha/sub/soul.md" not in text


# ===========================================================================
# check_baseline_drift — classification + posture
# ===========================================================================


class TestBaselineCheck:
    def test_modified_file_is_reported(self, override_agents_roots):
        root = override_agents_roots["agents_root"]
        _write_baseline(root)
        (root / "alpha" / "soul.md").write_text("TAMPERED\n", encoding="utf-8")
        result = pa.check_baseline_drift()
        assert result.status == "drift"
        assert result.modified == ("agents/alpha/soul.md",)
        assert not result.missing and not result.added

    def test_missing_and_added_files_are_reported(self, override_agents_roots):
        root = override_agents_roots["agents_root"]
        _write_baseline(root)
        (root / "alpha" / "soul.md").unlink()          # missing
        (root / "beta" / "skill.md").unlink()
        gamma = root / "gamma"
        gamma.mkdir()
        (gamma / "rule.md").write_text("new agent\n", encoding="utf-8")  # added
        result = pa.check_baseline_drift()
        assert result.status == "drift"
        assert result.missing == ("agents/alpha/soul.md", "agents/beta/skill.md")
        assert result.added == ("agents/gamma/rule.md",)

    def test_malformed_baseline_line_is_reported(self, override_agents_roots):
        root = override_agents_roots["agents_root"]
        baseline = _write_baseline(root)
        baseline.write_text(
            "not-a-checksum-line\n" + baseline.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        result = pa.check_baseline_drift()
        assert result.status == "drift"
        assert len(result.malformed) == 1
        assert "not-a-checksum-line" in result.malformed[0]

    def test_check_is_read_only(self, override_agents_roots):
        root = override_agents_roots["agents_root"]
        baseline = _write_baseline(root)
        before = baseline.read_bytes()
        (root / "alpha" / "soul.md").write_text("drift\n", encoding="utf-8")
        pa.check_baseline_drift()
        assert baseline.read_bytes() == before  # never rewritten by a check
        assert not list(baseline.parent.glob("*.tmp.*"))  # no temp residue

    def test_absent_baseline_is_not_drift(self, override_agents_roots):
        root = override_agents_roots["agents_root"]
        result = pa.check_baseline_drift()
        assert result.status == "baseline_absent"
        assert result.drift_count == 0

    def test_explicit_roots_param_overrides_resolution(self, override_agents_roots):
        # The test seam mirrors check_write: explicit roots win over the
        # (monkeypatched) cache — prove the parameter is honored.
        root = override_agents_roots["agents_root"]
        _write_baseline(root)
        other = root.parent / "agents-other"
        other.mkdir()
        (other / "solo").mkdir()
        (other / "solo" / "soul.md").write_text("x\n", encoding="utf-8")
        (pa.baseline_path_for(other)).write_text(
            pa.generate_baseline_text(other), encoding="utf-8"
        )
        result = pa.check_baseline_drift(agents_roots=(other,))
        assert result.agents_root == other
        assert result.status == "ok"
        assert result.verified_count == 1

    def test_detail_lines_cap_keeps_output_bounded(self, override_agents_roots):
        root = override_agents_roots["agents_root"]
        _write_baseline(root)
        for i in range(30):
            extra = root / f"agent{i:02d}"
            extra.mkdir()
            (extra / "soul.md").write_text(f"x{i}\n", encoding="utf-8")
        result = pa.check_baseline_drift()
        lines = result.detail_lines(cap=20)
        assert len(lines) == 21  # 20 shown + the overflow marker
        assert lines[-1].startswith("... (+")
        assert result.summary().startswith("PROMPT-DRIFT:")


# ===========================================================================
# Boot WARN leg (the exact function api.py lifespan calls)
# ===========================================================================


class TestBootWarnLeg:
    @pytest.fixture
    def boot_logger(self, caplog):
        name = "test.prompt-artifact.boot"
        caplog.set_level(logging.DEBUG, logger=name)
        return logging.getLogger(name)

    def test_warn_fires_on_intentionally_mutated_fixture(
        self, override_agents_roots, boot_logger, caplog
    ):
        root = override_agents_roots["agents_root"]
        _write_baseline(root)
        (root / "alpha" / "workflow.md").write_text(
            "bash-wrote-this\n", encoding="utf-8"
        )
        status = pa.log_baseline_drift_at_boot(logger_=boot_logger)
        assert status == "drift"
        warns = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warns) == 1
        assert "Prompt-artifact baseline DRIFT" in warns[0].getMessage()
        assert "agents/alpha/workflow.md" in warns[0].getMessage()
        assert "NOT boot-blocking" in warns[0].getMessage()

    def test_absent_baseline_is_single_info_no_warn(
        self, override_agents_roots, boot_logger, caplog
    ):
        status = pa.log_baseline_drift_at_boot(logger_=boot_logger)
        assert status == "baseline_absent"
        assert not [r for r in caplog.records if r.levelno == logging.WARNING]
        infos = [r for r in caplog.records if r.levelno == logging.INFO]
        assert len(infos) == 1  # exactly ONE info line, per the T13 spec
        assert "baseline absent" in infos[0].getMessage()

    def test_clean_tree_logs_info_ok(self, override_agents_roots, boot_logger, caplog):
        root = override_agents_roots["agents_root"]
        _write_baseline(root)
        status = pa.log_baseline_drift_at_boot(logger_=boot_logger)
        assert status == "ok"
        assert not [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any(
            "baseline clean" in r.getMessage()
            for r in caplog.records
            if r.levelno == logging.INFO
        )


# ===========================================================================
# Generator CLI — the exact surface the stage.sh leg + operators invoke
# ===========================================================================


class TestGeneratorCli:
    def _run(self, *argv: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(GENERATOR), *argv],
            capture_output=True,
            text=True,
        )

    def test_generate_then_check_clean_exits_zero(self, override_agents_roots):
        tree = override_agents_roots["tree"]
        proc = self._run("--tree", str(tree))
        assert proc.returncode == 0, proc.stderr
        assert "GENERATED" in proc.stdout
        baseline = tree / "agents" / ".prompt-artifacts.sha256"
        assert baseline.is_file()
        check = self._run("--check", "--tree", str(tree))
        assert check.returncode == 0, check.stdout + check.stderr
        assert "PROMPT-BASELINE: OK" in check.stdout

    def test_check_drift_exits_one_with_file_lines(self, override_agents_roots):
        tree = override_agents_roots["tree"]
        assert self._run("--tree", str(tree)).returncode == 0
        (tree / "agents" / "beta" / "soul.md").write_text(
            "mutated\n", encoding="utf-8"
        )
        check = self._run("--check", "--tree", str(tree))
        assert check.returncode == 1
        assert "PROMPT-DRIFT:" in check.stdout
        assert "PROMPT-DRIFT-FILE: agents/beta/soul.md (modified)" in check.stdout

    def test_check_absent_baseline_exits_zero_with_absent_token(
        self, override_agents_roots
    ):
        tree = override_agents_roots["tree"]
        check = self._run("--check", "--tree", str(tree))
        assert check.returncode == 0
        assert "PROMPT-BASELINE: ABSENT" in check.stdout

    def test_check_missing_agents_tree_exits_two(self, tmp_path):
        empty = tmp_path / "not-a-tree"
        empty.mkdir()
        proc = self._run("--check", "--tree", str(empty))
        assert proc.returncode == 2
        assert "ERROR" in proc.stderr

    def test_stage_leg_journal_shape_contract(self, override_agents_roots):
        """The tokens the stage.sh leg greps/journals stay stable."""
        tree = override_agents_roots["tree"]
        assert self._run("--tree", str(tree)).returncode == 0
        (tree / "agents" / "alpha" / "rule.md").write_text(
            "x\n", encoding="utf-8"
        )
        out = self._run("--check", "--tree", str(tree))
        first = out.stdout.splitlines()[0]
        assert first.startswith("PROMPT-DRIFT: ")
        assert "modified=1" in first
        assert any(
            line.startswith("PROMPT-DRIFT-FILE:") for line in out.stdout.splitlines()
        )

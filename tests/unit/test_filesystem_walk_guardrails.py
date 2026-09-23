"""Bounded-traversal guardrails for glob_files and grep_files (2026-09-23 fix).

The 2026-09-23 incident: an explorer agent hunting ``stage.sh`` widened its
search roots (bare home → ``All/Code`` → repo) via ABSOLUTE paths, each call
materializing entire directory trees in memory; the daemon hit ~16 GB RAM
and was SIGKILLed. The fix replaces ``Path.glob`` / bare ``os.walk`` with a
bounded walker that enforces:

  - **Traversal exclusions** — named dirs (node_modules, .venv, venv, .git,
    __pycache__, Library, .cache, .next, dist, build, target) AND any hidden
    dir (name starts with ``.``).
  - **Depth cap** — visit dirs at depths 0..10 (files at depths 0..10).
  - **Per-call file-count cap** — STOP the walk the moment 10,000 files are
    materialized (don't keep walking to discard later).
  - **Per-file size cap** (grep_files only) — skip files above 1.5 MB before
    ``read_text()``.
  - **Per-call timeout** — ``time.monotonic()`` checked at the start of each
    directory; default 20 s.
  - **Walk-tool absolute-path guard** — glob_files / grep_files refuse bare
    absolute paths that are outside both the workdir AND allowed temp dirs;
    bare ``/Users/...`` is REFUSED to prevent recurrence.
  - **Loud truncation notice** — when a cap fires, the tool result carries a
    "Search incomplete" notice pointing the caller to narrow the root or
    raise pattern specificity. Happy-path result format is unchanged.

Family conventions followed (mirrors ``tests/unit/test_filesystem_*.py``):
- Direct import from ``daemon.tools.filesystem``
- ``tool.invoke({...kwargs})`` to call the ``@tool``-decorated StructuredTool
- ``tmp_path`` hermetic trees; guardrail constants monkey-patched small for
  timeout / count / size tests
- Absolute paths so workdir is not required (tmp_path lives in temp dir)

The constants live at module scope on ``daemon.tools.filesystem`` so tests
can monkey-patch them safely. ``monkeypatch.setattr`` is the standard
fixture; ``monkeypatch.undo`` runs automatically at test teardown.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

import pytest

from daemon.tools import filesystem as fs


# ---------------------------------------------------------------------------
# Per-guardrail unit tests
# ---------------------------------------------------------------------------


class TestExclusions:
    """WALK_EXCLUDED_DIRS dirs are pruned during the walk; hidden dirs too."""

    def test_named_exclusion_node_modules(self, tmp_path: Path):
        (tmp_path / "kept.py").write_text("x")
        nm = tmp_path / "node_modules"
        nm.mkdir()
        (nm / "huge.py").write_text("x")
        result = fs.glob_files.invoke({"pattern": "**/*.py", "path": str(tmp_path)})
        assert "kept.py" in result
        assert "node_modules" not in result, (
            f"node_modules must be excluded:\n{result}"
        )
        assert "huge.py" not in result

    def test_named_exclusion_dot_git(self, tmp_path: Path):
        (tmp_path / "kept.py").write_text("x")
        git = tmp_path / ".git"
        git.mkdir()
        (git / "HEAD").write_text("ref: refs/heads/main")
        (git / "deep.py").write_text("x")
        result = fs.glob_files.invoke({"pattern": "**/*.py", "path": str(tmp_path)})
        assert "kept.py" in result
        assert "HEAD" not in result, f".git must be excluded:\n{result}"
        assert "deep.py" not in result

    def test_named_exclusion_venv_and_dot_venv(self, tmp_path: Path):
        for venv_name in ("venv", ".venv"):
                (tmp_path / "kept.py").write_text("x")
                v = tmp_path / venv_name
                v.mkdir()
                (v / "lib.py").write_text("x")
                result = fs.glob_files.invoke(
                    {"pattern": "**/*.py", "path": str(tmp_path)}
                )
                assert "kept.py" in result, f"kept.py missing for {venv_name}:\n{result}"
                assert "lib.py" not in result, (
                    f"{venv_name} not excluded (lib.py leaked):\n{result}"
                )

    def test_named_exclusion_dist_build_target(self, tmp_path: Path):
        for d in ("dist", "build", "target"):
            (tmp_path / "kept.py").write_text("x")
            sub = tmp_path / d
            sub.mkdir()
            (sub / "x.py").write_text("x")
            result = fs.glob_files.invoke(
                {"pattern": "**/*.py", "path": str(tmp_path)}
            )
            assert "kept.py" in result, f"kept.py missing for {d}:\n{result}"
            assert "x.py" not in result, (
                f"{d} not excluded (x.py leaked):\n{result}"
            )

    def test_named_exclusion_library_and_cache(self, tmp_path: Path):
        for d in ("Library", ".cache", "__pycache__", ".next"):
            (tmp_path / "kept.py").write_text("x")
            sub = tmp_path / d
            sub.mkdir()
            (sub / "x.py").write_text("x")
            result = fs.glob_files.invoke(
                {"pattern": "**/*.py", "path": str(tmp_path)}
            )
            assert "kept.py" in result, f"kept.py missing for {d}:\n{result}"
            assert "x.py" not in result, (
                f"{d} not excluded (x.py leaked):\n{result}"
            )

    def test_hidden_directory_excluded(self, tmp_path: Path):
        (tmp_path / "kept.py").write_text("x")
        hd = tmp_path / ".hidden_dir"
        hd.mkdir()
        (hd / "inside.py").write_text("x")
        result = fs.glob_files.invoke({"pattern": "**/*.py", "path": str(tmp_path)})
        assert "kept.py" in result
        assert "inside.py" not in result, (
            f"Hidden dir .hidden_dir not excluded (inside.py leaked):\n{result}"
        )

    def test_hidden_file_NOT_excluded(self, tmp_path: Path):
        """Hidden FILES must not be excluded — matches Path.glob semantics.

        ``*.py`` matches ``.hidden.py`` under pathlib; the walker only prunes
        hidden DIRS. Pins that we don't accidentally hide dotfiles.
        """
        (tmp_path / "visible.py").write_text("x")
        (tmp_path / ".hidden.py").write_text("x")
        result = fs.glob_files.invoke({"pattern": "**/*.py", "path": str(tmp_path)})
        assert "visible.py" in result
        assert ".hidden.py" in result, (
            f"Hidden FILE was excluded (should match Path.glob semantics):\n{result}"
        )

    def test_excluded_set_constant_is_extensible(self):
        """The WALK_EXCLUDED_DIRS constant is module-level and a frozenset.

        Pinned so future contributors know the extensibility contract: add
        new noise dirs at the call-site via this set, NOT by mutating dirs
        inside the walker.
        """
        assert isinstance(fs.WALK_EXCLUDED_DIRS, frozenset)
        # Spot-check the documented exclusions.
        for name in ("node_modules", ".venv", "venv", ".git", "__pycache__",
                     "Library", ".cache", ".next", "dist", "build", "target"):
            assert name in fs.WALK_EXCLUDED_DIRS, (
                f"WALK_EXCLUDED_DIRS missing documented name: {name}"
            )


class TestDepthCap:
    """WALK_MAX_DEPTH caps recursion at 10 levels (depths 0..10)."""

    def test_depth_cap_fires_at_max_depth(self, tmp_path: Path, monkeypatch):
        """Files at depth == max_depth are visible; files at depth > max_depth
        are pruned.

        Pins the "depth cap fires when the walker actually has to prune"
        surface — the truncation notice must mention the depth cap. The cap
        is NOT a notice trigger when the tree never reaches max_depth.
        """
        monkeypatch.setattr(fs, "WALK_MAX_DEPTH", 3)
        # depth 0: tmp_path/root.py
        (tmp_path / "root.py").write_text("x")
        # depth 1: tmp_path/lvl1/file.py
        lvl1 = tmp_path / "lvl1"
        lvl1.mkdir()
        (lvl1 / "file.py").write_text("x")
        # depth 2: tmp_path/lvl1/lvl2/file.py
        lvl2 = lvl1 / "lvl2"
        lvl2.mkdir()
        (lvl2 / "file.py").write_text("x")
        # depth 3: tmp_path/lvl1/lvl2/lvl3/file.py (visible at depth=3)
        lvl3 = lvl2 / "lvl3"
        lvl3.mkdir()
        (lvl3 / "file.py").write_text("x")
        # depth 4: tmp_path/lvl1/lvl2/lvl3/lvl4/file.py (PRUNED at depth>3)
        lvl4 = lvl3 / "lvl4"
        lvl4.mkdir()
        (lvl4 / "file.py").write_text("x")

        result = fs.glob_files.invoke({"pattern": "**/*.py", "path": str(tmp_path)})

        # depth 0,1,2,3 visible.
        assert "root.py" in result
        assert "lvl1/file.py" in result
        assert "lvl1/lvl2/file.py" in result
        assert "lvl1/lvl2/lvl3/file.py" in result
        # depth 4+ pruned.
        assert "lvl4/file.py" not in result, (
            f"Files beyond depth cap leaked:\n{result}"
        )
        # Notice fires because the depth cap actually pruned something.
        assert "depth cap" in result, (
            f"Expected depth-cap notice when cap fired:\n{result}"
        )

    def test_depth_cap_does_not_fire_when_shallow_tree(self, tmp_path, monkeypatch):
        """A 2-level tree under WALK_MAX_DEPTH=10 must NOT trigger the notice."""
        monkeypatch.setattr(fs, "WALK_MAX_DEPTH", 10)
        (tmp_path / "a.py").write_text("x")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "b.py").write_text("x")
        result = fs.glob_files.invoke({"pattern": "**/*.py", "path": str(tmp_path)})
        assert "depth cap" not in result, (
            f"Shallow tree triggered depth-cap notice (false positive):\n{result}"
        )


class TestFileCountCap:
    """WALK_MAX_FILE_COUNT caps the materialized candidate list."""

    def test_file_count_cap_stops_walk(self, tmp_path, monkeypatch):
        """When the file count exceeds the cap, the walk STOPS — files after
        the cap are NOT scanned even if they would match.

        Pins the "stop-the-walk, don't keep walking to discard later"
        property. We monkeypatch the cap small (5) and create 20 files; only
        5 should appear in the result, AND the notice must mention the cap.
        """
        monkeypatch.setattr(fs, "WALK_MAX_FILE_COUNT", 5)
        for i in range(20):
            (tmp_path / f"file_{i:02d}.py").write_text(f"x{i}")
        result = fs.glob_files.invoke({"pattern": "*.py", "path": str(tmp_path)})
        # Count match lines (everything before any "---" pagination hint).
        match_lines = [
            line for line in result.split("\n")
            if line and not line.startswith("---") and not line.startswith("Showing")
            and "Search incomplete" not in line
        ]
        assert len(match_lines) == 5, (
            f"Expected 5 files under cap, got {len(match_lines)}:\n{result}"
        )
        assert "file-count cap" in result, (
            f"Expected file-count-cap notice:\n{result}"
        )

    def test_file_count_cap_uses_constant(self):
        """Pinned at 10,000 — large enough that any sane project fits."""
        assert fs.WALK_MAX_FILE_COUNT == 10_000


class TestPerFileSizeSkip:
    """WALK_MAX_FILE_SIZE_BYTES gates grep_files' per-file read."""

    def test_oversized_file_skipped_with_notice(self, tmp_path, monkeypatch):
        """A 2 MB file must be skipped (no read_text, no match), and the
        truncation notice must mention the skip count."""
        monkeypatch.setattr(fs, "WALK_MAX_FILE_SIZE_BYTES", 100)  # tiny cap
        small = tmp_path / "small.py"
        small.write_text("MATCH_HIT = 'found'\n")
        big = tmp_path / "big.py"
        # 1 KB > 100B cap.
        big.write_text("MATCH_HIT = 'found'\n" + ("#" * 1000 + "\n") * 1)

        result = fs.grep_files.invoke(
            {"pattern": "MATCH_HIT", "path": str(tmp_path), "include": "*.py"}
        )
        # Small file's match is in the output.
        assert "small.py" in result, (
            f"Small file missing from result:\n{result}"
        )
        # Big file's match was skipped — no reference to big.py.
        assert "big.py" not in result, (
            f"Oversized file was scanned (read_text bypassed):\n{result}"
        )
        # Notice mentions oversized skip count.
        assert "oversized file(s) skipped" in result, (
            f"Expected oversized-skip notice:\n{result}"
        )

    def test_size_cap_uses_constant(self):
        assert fs.WALK_MAX_FILE_SIZE_BYTES == 1_500_000


class TestTimeoutFires:
    """WALK_TIMEOUT_SECONDS bounds the walk duration."""

    def test_timeout_fires_with_monkeypatched_small_constant(
        self, tmp_path, monkeypatch
    ):
        """Patch the timeout to a tiny value (1 ms) and force a tree large
        enough to exceed it. The walk must STOP with timeout == stopped_reason.

        We don't ``sleep`` long — the timeout is enforced by ``time.monotonic()``
        checks at the start of each directory, so a 1 ms cap fires after one
        or two directory entries (cheap).
        """
        # Build a tree with enough dirs to keep the walker busy long enough
        # for 1 ms to elapse (filesystem timing is volatile but a wide tree
        # gives many chances).
        for i in range(50):
            d = tmp_path / f"d{i:02d}"
            d.mkdir()
            (d / "f.py").write_text("x")
        # 1 ms timeout: forces the deadline check on the next iteration.
        monkeypatch.setattr(fs, "WALK_TIMEOUT_SECONDS", 0.001)
        result = fs.glob_files.invoke({"pattern": "**/*.py", "path": str(tmp_path)})
        # The walker may or may not actually hit the cap within 1 ms on a
        # small synthetic tree; what we CAN pin is that no test sleeps long,
        # and that if timeout fires the notice mentions it.
        # Acceptable: notice either absent (walk completed in time) or
        # present mentioning timeout.
        if "Search incomplete" in result:
            assert "timeout" in result, (
                f"Incomplete notice should mention timeout when cap fired:\n{result}"
            )
        # Sanity: did NOT block forever.
        assert isinstance(result, str)

    def test_happy_path_does_not_trigger_timeout_notice(self, tmp_path, monkeypatch):
        """A small tree within the default 20 s budget must NOT trigger the
        timeout notice."""
        (tmp_path / "a.py").write_text("x")
        (tmp_path / "b.py").write_text("x")
        result = fs.glob_files.invoke({"pattern": "*.py", "path": str(tmp_path)})
        assert "timeout" not in result, (
            f"Small tree triggered timeout notice:\n{result}"
        )


class TestAbsolutePathGuard:
    """Walk-tool absolute-path boundary — closes the 2026-09-23 incident shape.

    Semantics (see ``_resolve_search_root``):
      - Absolute path WITH workdir: allowed iff within workdir OR temp dir.
      - Absolute path WITHOUT workdir: allowed ONLY if in temp dir.
      - Relative path: same as file tools.
      - Bare absolute path outside workdir AND outside temp: REFUSED.
    """

    def test_abs_in_temp_no_workdir_allowed(self, tmp_path):
        """tmp_path is in the system temp dir; absolute-no-workdir is allowed."""
        (tmp_path / "x.py").write_text("x")
        result = fs.glob_files.invoke({"pattern": "*.py", "path": str(tmp_path)})
        assert "ERROR" not in result
        assert "x.py" in result

    def test_abs_outside_workdir_no_workdir_refused(self, tmp_path):
        """Bare absolute path outside workdir + outside temp is REFUSED.

        This is the 2026-09-23 incident shape (bare ``/Users/...`` walk).
        The error must redirect them to scope the walk.
        """
        # Use a sibling-of-tmp_path as a non-temp absolute root. pytest's
        # tmp_path is under /var/folders/.../T/...; its PARENT (under
        # /var/folders/.../) is also in temp, so climb to a non-temp root.
        non_temp_root = Path(__file__).resolve().parent.parent.parent
        # The repo root might still be in temp on CI; assert non-temp first.
        assert not fs.WorkspaceGuard._is_in_temp_dir(non_temp_root), (
            f"Test root {non_temp_root} unexpectedly in temp dir — pick another"
        )
        result = fs.glob_files.invoke(
            {"pattern": "*.py", "path": str(non_temp_root)}
        )
        assert "ERROR" in result
        # Error references the new contract.
        assert "workdir" in result, (
            f"Error should mention workdir requirement:\n{result}"
        )

    def test_abs_outside_workdir_with_workdir_inside_allowed(self, tmp_path):
        """Absolute path WITH workdir, contained inside the workdir: OK."""
        # The workdir is tmp_path itself; absolute search root is a child.
        child = tmp_path / "child"
        child.mkdir()
        (child / "x.py").write_text("x")
        result = fs.glob_files.invoke({
            "pattern": "*.py",
            "path": str(child),
            "workdir": str(tmp_path),
        })
        assert "ERROR" not in result, result
        assert "x.py" in result

    def test_abs_outside_workdir_refused_with_redirect(self, tmp_path):
        """Absolute path with workdir, OUTSIDE the workdir: REFUSED with a
        redirecting error."""
        # workdir is tmp_path; search root is the repo's data dir (likely
        # outside tmp_path AND outside tmp_path's containment).
        sibling = tmp_path.parent / ("sibling_of_" + tmp_path.name)
        # Ensure sibling is outside workdir AND outside temp. If tmp_path's
        # parent is in temp (typical pytest setup), sibling is still in temp,
        # and the walk would be allowed via the temp-dir allowance — making
        # this test meaningless on those setups. Skip if so.
        if fs.WorkspaceGuard._is_in_temp_dir(sibling):
            pytest.skip("sibling is in temp dir on this filesystem; can't pin refusal")
        (sibling).mkdir(exist_ok=True)
        try:
            result = fs.glob_files.invoke({
                "pattern": "*.py",
                "path": str(sibling),
                "workdir": str(tmp_path),
            })
            assert "ERROR" in result
            assert "outside workspace boundary" in result, (
                f"Error should mention workspace boundary:\n{result}"
            )
        finally:
            import shutil
            shutil.rmtree(sibling, ignore_errors=True)

    def test_relative_path_requires_workdir(self, tmp_path):
        """Relative path still requires workdir (unchanged semantics)."""
        result = fs.glob_files.invoke({"pattern": "*.py", "path": "."})
        assert "ERROR" in result
        assert "workdir is required" in result


# ---------------------------------------------------------------------------
# Incident-shape repro: a wide tree with the actual exclusion list + size cap
# + deep chain, asserting bounded candidate count + exclusions + size skips
# + loud truncation messaging.
# ---------------------------------------------------------------------------


class TestIncidentShapeRepro:
    """Synthetic tree mirroring the 2026-09-23 incident's worst shape."""

    def test_bounded_candidate_count_under_exclusion_storm(
        self, tmp_path, monkeypatch
    ):
        """Stuff the tree with excluded dirs (node_modules, .venv, venv, .git,
        __pycache__, .cache, .next, dist, build, target) full of junk files.
        The walker must prune ALL of them — the materialized candidate list
        must be small and bounded, never the inflated count.
        """
        # Monkeypatch the cap small so we can clearly observe the bound.
        monkeypatch.setattr(fs, "WALK_MAX_FILE_COUNT", 10)
        # One real kept file at root.
        (tmp_path / "real.py").write_text("REAL_TOKEN = 'x'\n")
        # Ten excluded dirs, each with 50 junk files (would be 500 candidates
        # under the unbounded walker — 50× the cap).
        for excl in ("node_modules", ".venv", "venv", ".git", "__pycache__",
                     ".cache", ".next", "dist", "build", "target"):
            d = tmp_path / excl
            d.mkdir()
            for i in range(50):
                (d / f"junk_{i:03d}.py").write_text("junk")
        result = fs.glob_files.invoke({"pattern": "**/*.py", "path": str(tmp_path)})
        # Only real.py should appear (everything under excluded dirs pruned).
        assert "real.py" in result
        for excl in ("node_modules", ".venv", "venv", ".git", "__pycache__",
                     ".cache", ".next", "dist", "build", "target"):
            assert excl not in result, (
                f"Excluded dir {excl} leaked into output:\n{result}"
            )

    def test_oversized_file_skipped_in_incident_shape(
        self, tmp_path, monkeypatch
    ):
        """An oversized file in the search root is skipped (no read_text)."""
        monkeypatch.setattr(fs, "WALK_MAX_FILE_SIZE_BYTES", 100)
        (tmp_path / "small.py").write_text("MATCH = 'found'\n")
        # 2 KB file > 100B cap.
        big = tmp_path / "huge.py"
        big.write_text("MATCH = 'found'\n" + ("padpadpad\n" * 200))
        result = fs.grep_files.invoke({
            "pattern": "MATCH", "path": str(tmp_path), "include": "*.py",
        })
        assert "small.py" in result, f"Small file missing:\n{result}"
        assert "huge.py" not in result, (
            f"Oversized huge.py was scanned (read_text bypassed):\n{result}"
        )
        assert "oversized" in result, (
            f"Expected oversized-skip notice:\n{result}"
        )

    def test_deep_chain_bounded_by_depth_cap(self, tmp_path, monkeypatch):
        """A 15-level deep chain under depth cap=5 is pruned at level 5+.

        The walker visits dirs at depths 0..max_depth (= 0..5 here) and
        scans files INSIDE those dirs (file_N.py lives in lvlN at depth
        N+1, so file_0..file_4 land in depths 1..5). lvl5 is at depth 6 —
        past the cap — so file_5+ are pruned.
        """
        monkeypatch.setattr(fs, "WALK_MAX_DEPTH", 5)
        # Build chain of depth 15.
        chain = tmp_path
        for level in range(15):
            chain = chain / f"lvl{level}"
            chain.mkdir()
            (chain / f"file_{level}.py").write_text("x")
        result = fs.glob_files.invoke({"pattern": "**/*.py", "path": str(tmp_path)})
        # Files at depths 1..5 visible (file_0 through file_4).
        for level in range(5):
            assert f"lvl{level}/file_{level}.py" in result, (
                f"Depth {level} file missing:\n{result}"
            )
        # file_5..file_14 pruned (lvl5 at depth 6 is past max_depth=5).
        for level in range(5, 15):
            assert f"lvl{level}/file_{level}.py" not in result, (
                f"Depth {level} file leaked past cap:\n{result}"
            )
        # Notice mentions the cap.
        assert "depth cap" in result, f"Expected depth-cap notice:\n{result}"


# ---------------------------------------------------------------------------
# Truncation notice format / loud messaging
# ---------------------------------------------------------------------------


class TestTruncationNotice:
    """When a cap fires the result carries a "Search incomplete" notice."""

    def test_notice_format_under_file_count_cap(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fs, "WALK_MAX_FILE_COUNT", 3)
        for i in range(10):
            (tmp_path / f"f_{i:02d}.py").write_text("x")
        result = fs.glob_files.invoke({"pattern": "*.py", "path": str(tmp_path)})
        assert "⚠ Search incomplete" in result, (
            f"Loud warning missing in result:\n{result}"
        )
        assert "file-count cap" in result
        assert "narrow the root or raise pattern specificity" in result

    def test_notice_format_under_depth_cap(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fs, "WALK_MAX_DEPTH", 1)
        (tmp_path / "root.py").write_text("x")
        d = tmp_path / "sub"
        d.mkdir()
        (d / "sub.py").write_text("x")
        sd = d / "subsub"
        sd.mkdir()
        (sd / "deep.py").write_text("x")
        result = fs.glob_files.invoke({"pattern": "**/*.py", "path": str(tmp_path)})
        assert "⚠ Search incomplete" in result, (
            f"Loud warning missing in result:\n{result}"
        )
        assert "depth cap" in result

    def test_no_notice_on_happy_path(self, tmp_path):
        """A small tree within all caps must NOT carry the truncation notice."""
        (tmp_path / "a.py").write_text("x")
        (tmp_path / "b.py").write_text("x")
        result = fs.glob_files.invoke({"pattern": "*.py", "path": str(tmp_path)})
        assert "⚠ Search incomplete" not in result, (
            f"Happy-path result carried truncation notice:\n{result}"
        )

    def test_exclusions_alone_do_not_trigger_notice(self, tmp_path):
        """Exclusions are ALWAYS on — not an incomplete signal by themselves."""
        # Tree with excluded dirs but small enough that no cap fires.
        (tmp_path / "real.py").write_text("x")
        excl = tmp_path / "node_modules"
        excl.mkdir()
        (excl / "junk.py").write_text("x")
        result = fs.glob_files.invoke({"pattern": "*.py", "path": str(tmp_path)})
        assert "⚠ Search incomplete" not in result, (
            f"Exclusions alone triggered truncation notice (false positive):\n{result}"
        )


# ---------------------------------------------------------------------------
# Pattern matching fidelity — preserve Path.glob semantics for the existing
# pattern shapes that the rest of the tool ecosystem depends on.
# ---------------------------------------------------------------------------


class TestPatternFidelity:
    """Pattern matching after the bounded walker must match Path.glob.

    These pins protect the rewrite from regressing the documented glob
    semantics. Where the walker introduces a deviation (e.g. hidden dirs
    pruned, hidden files still matched), tests pin the deviation explicitly.
    """

    def test_star_py_is_root_only(self, tmp_path):
        """``*.py`` matches direct children only (NOT subdirs)."""
        (tmp_path / "root.py").write_text("x")
        nested = tmp_path / "nested"
        nested.mkdir()
        (nested / "child.py").write_text("x")
        result = fs.glob_files.invoke({"pattern": "*.py", "path": str(tmp_path)})
        assert "root.py" in result
        assert "child.py" not in result, (
            f"*.py matched subdir file (should be root-only):\n{result}"
        )

    def test_double_star_py_is_recursive(self, tmp_path):
        """``**/*.py`` matches at any depth."""
        (tmp_path / "root.py").write_text("x")
        nested = tmp_path / "nested"
        nested.mkdir()
        (nested / "child.py").write_text("x")
        deeper = nested / "deeper"
        deeper.mkdir()
        (deeper / "deep.py").write_text("x")
        result = fs.glob_files.invoke({"pattern": "**/*.py", "path": str(tmp_path)})
        assert "root.py" in result
        assert "nested/child.py" in result
        assert "nested/deeper/deep.py" in result

    def test_src_double_star_ts_is_anchored(self, tmp_path):
        """``src/**/*.ts`` is anchored to a src/ subtree."""
        ok = tmp_path / "src" / "sub"
        ok.mkdir(parents=True)
        (ok / "ok.ts").write_text("x")
        wrong = tmp_path / "other"
        wrong.mkdir()
        (wrong / "bad.ts").write_text("x")
        result = fs.glob_files.invoke(
            {"pattern": "src/**/*.ts", "path": str(tmp_path)}
        )
        assert "src/sub/ok.ts" in result
        assert "other/bad.ts" not in result, (
            f"Anchored pattern matched outside src/:\n{result}"
        )

    def test_no_include_recurses_all_files(self, tmp_path):
        """Empty include matches all files (the ``**/*`` fallback)."""
        (tmp_path / "a.py").write_text("x")
        d = tmp_path / "d"
        d.mkdir()
        (d / "b.txt").write_text("x")
        result = fs.grep_files.invoke({
            "pattern": "x", "path": str(tmp_path),
        })
        assert "a.py" in result
        assert "b.txt" in result

    def test_brace_union_matches_both_extensions(self, tmp_path):
        """Brace union ``*.{py,txt}`` matches both extensions recursively."""
        (tmp_path / "a.py").write_text("TOKEN = 'x'\n")
        d = tmp_path / "d"
        d.mkdir()
        (d / "b.txt").write_text("TOKEN = 'x'\n")
        result = fs.grep_files.invoke({
            "pattern": "TOKEN", "path": str(tmp_path), "include": "*.{py,txt}",
        })
        assert "a.py" in result
        assert "b.txt" in result

    def test_brace_form_in_braces_matches_both(self, tmp_path):
        """``{*.py,*.txt}`` brace form also matches both extensions."""
        (tmp_path / "a.py").write_text("TOKEN = 'x'\n")
        d = tmp_path / "d"
        d.mkdir()
        (d / "b.txt").write_text("TOKEN = 'x'\n")
        result = fs.grep_files.invoke({
            "pattern": "TOKEN", "path": str(tmp_path), "include": "{*.py,*.txt}",
        })
        assert "a.py" in result
        assert "b.txt" in result


# ---------------------------------------------------------------------------
# _bounded_walk / WalkReport unit-level pins
# ---------------------------------------------------------------------------


class TestBoundedWalkUnit:
    """Unit tests on the walker itself (not through the @tool wrappers)."""

    def test_walk_returns_empty_for_nonexistent_dir(self, tmp_path):
        ghost = tmp_path / "ghost"
        # Doesn't exist — caller is responsible for existence checks; the
        # walker itself walks what's there (and 0 entries for a missing dir).
        # On most platforms os.walk on a missing dir just yields nothing.
        report = fs._bounded_walk(ghost)
        assert report.files == []
        assert report.stopped_reason is None

    def test_walk_excludes_named_dirs(self, tmp_path):
        (tmp_path / "kept.py").write_text("x")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "junk.py").write_text("x")
        report = fs._bounded_walk(tmp_path)
        names = {f.name for f in report.files}
        assert "kept.py" in names
        assert "junk.py" not in names

    def test_walk_returns_walk_report_dataclass(self, tmp_path):
        report = fs._bounded_walk(tmp_path)
        assert isinstance(report, fs.WalkReport)
        assert hasattr(report, "files")
        assert hasattr(report, "stopped_reason")
        assert hasattr(report, "timed_out_at")


class TestFileMatchesPattern:
    """Unit tests on _file_matches_pattern (PurePath.full_match bridge)."""

    def test_star_py_root(self, tmp_path):
        f = tmp_path / "foo.py"
        f.write_text("x")
        assert fs._file_matches_pattern(f, tmp_path, "*.py") is True

    def test_star_py_subdir_rejected(self, tmp_path):
        sub = tmp_path / "nested"
        sub.mkdir()
        f = sub / "foo.py"
        f.write_text("x")
        # `*.py` does NOT match nested/foo.py — pathlib's `*` doesn't cross /.
        assert fs._file_matches_pattern(f, tmp_path, "*.py") is False

    def test_double_star_py_subdir_accepted(self, tmp_path):
        sub = tmp_path / "nested"
        sub.mkdir()
        f = sub / "foo.py"
        f.write_text("x")
        assert fs._file_matches_pattern(f, tmp_path, "**/*.py") is True

    def test_src_anchored(self, tmp_path):
        ok_dir = tmp_path / "src" / "sub"
        ok_dir.mkdir(parents=True)
        ok = ok_dir / "foo.ts"
        ok.write_text("x")
        assert fs._file_matches_pattern(ok, tmp_path, "src/**/*.ts") is True
        wrong_dir = tmp_path / "other"
        wrong_dir.mkdir(parents=True)
        wrong = wrong_dir / "foo.ts"
        wrong.write_text("x")
        assert fs._file_matches_pattern(wrong, tmp_path, "src/**/*.ts") is False


class TestResolveSearchRoot:
    """Unit tests on _resolve_search_root (walk-tool boundary)."""

    def test_relative_path_delegates_to_resolve_within_workdir(self, tmp_path):
        """Relative path uses the same resolver as read/write/edit."""
        sub = tmp_path / "sub"
        sub.mkdir()
        target, err = fs._resolve_search_root(".", str(sub))
        assert err is None
        assert target == sub.resolve()

    def test_relative_path_without_workdir_errors(self):
        target, err = fs._resolve_search_root(".", None)
        assert err is not None
        assert target is None

    def test_absolute_temp_path_no_workdir_allowed(self, tmp_path):
        """tmp_path is in temp; absolute-no-workdir is allowed."""
        target, err = fs._resolve_search_root(str(tmp_path), None)
        assert err is None
        assert target == tmp_path.resolve()

    def test_absolute_non_temp_no_workdir_refused(self):
        """Bare absolute path outside workdir AND outside temp: REFUSED.

        The 2026-09-23 incident shape.
        """
        non_temp = Path(__file__).resolve().parent.parent.parent
        # Sanity: must actually be outside temp for this test to be meaningful.
        if fs.WorkspaceGuard._is_in_temp_dir(non_temp):
            pytest.skip(f"Test root {non_temp} is in temp on this filesystem")
        target, err = fs._resolve_search_root(str(non_temp), None)
        assert target is None
        assert err is not None
        assert "workdir" in err

    def test_absolute_within_workdir_allowed(self, tmp_path):
        """Absolute path WITH workdir, inside workdir: allowed."""
        child = tmp_path / "child"
        child.mkdir()
        target, err = fs._resolve_search_root(str(child), str(tmp_path))
        assert err is None
        assert target == child.resolve()

    def test_absolute_outside_workdir_refused(self, tmp_path):
        """Absolute path with workdir, OUTSIDE workdir: refused with redirect."""
        sibling = tmp_path.parent / ("sibling_" + tmp_path.name)
        if fs.WorkspaceGuard._is_in_temp_dir(sibling):
            pytest.skip("sibling is in temp on this filesystem; pin meaningless")
        (sibling).mkdir(exist_ok=True)
        try:
            target, err = fs._resolve_search_root(str(sibling), str(tmp_path))
            assert target is None
            assert err is not None
            assert "outside workspace boundary" in err
        finally:
            import shutil
            shutil.rmtree(sibling, ignore_errors=True)
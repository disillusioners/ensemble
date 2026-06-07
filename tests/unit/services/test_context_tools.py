"""Unit tests for ``daemon.services.context_tools`` (low-level FS helpers)."""

import json
from pathlib import Path
from unittest.mock import patch

from daemon.services.context_tools import (
    list_context_files,
    read_context_file,
    resolve_context_dir,
)


def _make_context_dir(tmp_path: Path, context_key: str) -> Path:
    context_dir = tmp_path / "ensemble" / "context" / context_key
    context_dir.mkdir(parents=True, exist_ok=True)
    return context_dir


# ─── resolve_context_dir ────────────────────────────────────────────────────────


class TestResolveContextDir:
    def test_returns_expected_path(self, tmp_path):
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = resolve_context_dir("ctx-1")
        assert result == tmp_path / "ensemble" / "context" / "ctx-1"

    def test_does_not_raise_on_gettempdir_error(self):
        with patch("daemon.services.context_tools.tempfile.gettempdir", side_effect=OSError):
            result = resolve_context_dir("any")
        # Falls back to a Path under "/unknown" — never raises.
        assert isinstance(result, Path)

    def test_none_context_key_yields_empty_segment(self, tmp_path):
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = resolve_context_dir(None)
        assert result == tmp_path / "ensemble" / "context" / ""


# ─── list_context_files ────────────────────────────────────────────────────────


class TestListContextFiles:
    def test_nonexistent_dir_returns_empty(self, tmp_path):
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = list_context_files("does-not-exist")
        assert result == []

    def test_empty_dir_returns_empty(self, tmp_path):
        _make_context_dir(tmp_path, "ctx-empty")
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = list_context_files("ctx-empty")
        assert result == []

    def test_populated_dir_returns_metadata(self, tmp_path):
        context_dir = _make_context_dir(tmp_path, "ctx-pop")
        (context_dir / "auth-flow_20260601_120000.md").write_text(
            "First line of the file.\nMore content.\n"
        )
        (context_dir / "notes_20260602_130000.md").write_text(
            "Just a one-liner.\n"
        )

        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = list_context_files("ctx-pop")

        assert len(result) == 2
        # Result is sorted alphabetically by filename
        assert result[0]["filename"] == "auth-flow_20260601_120000.md"
        assert result[1]["filename"] == "notes_20260602_130000.md"

        first = result[0]
        assert first["slug"] == "auth-flow"
        assert isinstance(first["size_bytes"], int)
        assert first["size_bytes"] > 0
        assert first["modified_at"]  # ISO 8601 timestamp, non-empty
        assert first["concise_preview"] == "First line of the file."

        # One-liner file — preview is the first non-empty line, truncated to 120
        assert result[1]["slug"] == "notes"
        assert result[1]["concise_preview"] == "Just a one-liner."

    def test_non_md_files_are_skipped(self, tmp_path):
        context_dir = _make_context_dir(tmp_path, "ctx-mixed")
        (context_dir / "good.md").write_text("ok")
        (context_dir / "ignored.txt").write_text("skip me")
        (context_dir / "ignored.json").write_text("{}")

        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = list_context_files("ctx-mixed")

        assert [r["filename"] for r in result] == ["good.md"]

    def test_subdirectories_are_skipped(self, tmp_path):
        context_dir = _make_context_dir(tmp_path, "ctx-subdirs")
        (context_dir / "subdir").mkdir()
        (context_dir / "subdir" / "nested.md").write_text("nested")
        (context_dir / "top.md").write_text("top")

        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = list_context_files("ctx-subdirs")

        assert [r["filename"] for r in result] == ["top.md"]

    def test_corrupt_file_does_not_crash_scan(self, tmp_path):
        context_dir = _make_context_dir(tmp_path, "ctx-corrupt")
        (context_dir / "good.md").write_text("ok")
        (context_dir / "bad.md").write_text("seed")

        original_open = Path.open

        def flaky_open(self, *args, **kwargs):
            if self.name == "bad.md":
                raise OSError("disk error")
            return original_open(self, *args, **kwargs)

        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)), \
             patch.object(Path, "open", flaky_open):
            result = list_context_files("ctx-corrupt")

        # Both files appear in the listing — the bad one has an empty preview
        # because the per-file read error is caught and logged, not raised.
        filenames = [r["filename"] for r in result]
        assert sorted(filenames) == ["bad.md", "good.md"]
        by_name = {r["filename"]: r for r in result}
        assert by_name["good.md"]["concise_preview"] == "ok"
        assert by_name["bad.md"]["concise_preview"] == ""

    def test_preview_truncated_to_120_chars(self, tmp_path):
        context_dir = _make_context_dir(tmp_path, "ctx-trunc")
        long_line = "x" * 500
        (context_dir / "long.md").write_text(long_line + "\n")

        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = list_context_files("ctx-trunc")

        assert len(result) == 1
        assert len(result[0]["concise_preview"]) == 120

    def test_empty_file_has_no_preview(self, tmp_path):
        context_dir = _make_context_dir(tmp_path, "ctx-empty-file")
        (context_dir / "empty.md").write_text("")

        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = list_context_files("ctx-empty-file")

        assert len(result) == 1
        assert result[0]["concise_preview"] == ""


# ─── read_context_file ─────────────────────────────────────────────────────────


class TestReadContextFile:
    def test_happy_path(self, tmp_path):
        context_dir = _make_context_dir(tmp_path, "ctx-rd")
        (context_dir / "doc.md").write_text("hello world")

        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = read_context_file("ctx-rd", "doc.md")

        assert result == "hello world"

    def test_missing_file_returns_none(self, tmp_path):
        _make_context_dir(tmp_path, "ctx-missing")
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            assert read_context_file("ctx-missing", "nope.md") is None

    def test_nonexistent_dir_returns_none(self, tmp_path):
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            assert read_context_file("no-such-dir", "anything.md") is None

    def test_path_traversal_rejected_forward_slash(self, tmp_path):
        context_dir = _make_context_dir(tmp_path, "ctx-trav")
        (context_dir / "real.md").write_text("real")
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            assert read_context_file("ctx-trav", "../etc/passwd") is None
            assert read_context_file("ctx-trav", "sub/file.md") is None

    def test_path_traversal_rejected_backslash(self, tmp_path):
        _make_context_dir(tmp_path, "ctx-trav2")
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            assert read_context_file("ctx-trav2", "..\\etc\\passwd") is None
            assert read_context_file("ctx-trav2", "sub\\file.md") is None

    def test_non_md_extension_rejected(self, tmp_path):
        context_dir = _make_context_dir(tmp_path, "ctx-ext")
        (context_dir / "secret.txt").write_text("should not leak")
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            assert read_context_file("ctx-ext", "secret.txt") is None

    def test_empty_filename_rejected(self, tmp_path):
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            assert read_context_file("any", "") is None
            assert read_context_file("any", None) is None

    def test_filename_with_dotdot_component_rejected(self, tmp_path):
        _make_context_dir(tmp_path, "ctx-dot")
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            assert read_context_file("ctx-dot", "..file.md") is None
            assert read_context_file("ctx-dot", "file..md") is None

    def test_subdirectory_escape_rejected(self, tmp_path):
        context_dir = _make_context_dir(tmp_path, "ctx-sub")
        (context_dir / "sub").mkdir()
        (context_dir / "sub" / "deep.md").write_text("deep")

        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            # This filename contains a path separator → rejected outright.
            assert read_context_file("ctx-sub", "sub/deep.md") is None

    def test_result_is_valid_json_when_round_tripped(self, tmp_path):
        """`list_context_files` output JSON-decodes cleanly."""
        context_dir = _make_context_dir(tmp_path, "ctx-json")
        (context_dir / "alpha_20260601_000000.md").write_text("alpha content")
        (context_dir / "beta_20260602_000000.md").write_text("beta content")

        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = list_context_files("ctx-json")

        decoded = json.loads(json.dumps(result))
        assert isinstance(decoded, list)
        assert len(decoded) == 2

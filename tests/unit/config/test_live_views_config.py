"""Unit tests for ``LiveViewsRootConfig`` (HARDENING M11 + M11 carry).

The validator on ``LiveViewsRootConfig`` is the loud-fail-at-load
gate the M11 hardening added: a structurally invalid root entry
(empty path on a directory-backed root, ``path`` set on a
``tmp_images`` root, ``required_rel_subpath`` on a non-project_scoped
type, or an empty segment in the subpath) must raise a
``ValidationError`` at config load — the resolver would catch most
of these at request time and serve the uniform 404, but a config
that boots with a broken root is operationally worse than one that
refuses to boot. First release has no legacy configs to break.

The M11 carry exception: ``LiveViewsRootConfig(enabled=False)``
SKIPS every shape check above (a disabled root never resolves, so
its shape is moot). This is the canonical operator-override-model
disable shorthand documented in the ``_seed_phase1_roots``
docstring (``{enabled: false}`` disables a single seeded root).
The pin lives in ``TestDisabledRootShortHand`` below.

Test env: ``uv`` not on PATH; the convention is
``/home/nea/ensemble-src/.venv/bin/python -m pytest`` from the
worktree.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from daemon.config import (
    DEFAULT_LIVE_VIEWS_MAX_MEDIA_BYTES,
    ENSEMBLE_LIVE_VIEWS_MAX_MEDIA_BYTES,
    LiveViewsConfig,
    LiveViewsRootConfig,
    _resolve_live_views_max_media_bytes,
)


# ── filesystem / project_scoped path-required gate ────────────────


class TestPathRequiredForDirectoryBackedTypes:
    """``filesystem`` and ``project_scoped`` roots MUST have a
    non-empty ``path``; the resolver cannot anchor a directory
    against ``None``."""

    def test_filesystem_root_with_empty_path_raises(self):
        with pytest.raises(ValidationError) as excinfo:
            LiveViewsRootConfig(type="filesystem", path="")
        # The actionable message names the type and the bad
        # path value so an operator can find the misconfig
        # from a single log line.
        assert "filesystem" in str(excinfo.value)
        assert "path" in str(excinfo.value).lower()

    def test_filesystem_root_with_whitespace_path_raises(self):
        with pytest.raises(ValidationError) as excinfo:
            LiveViewsRootConfig(type="filesystem", path="   ")
        assert "filesystem" in str(excinfo.value)

    def test_filesystem_root_with_none_path_raises(self):
        with pytest.raises(ValidationError) as excinfo:
            LiveViewsRootConfig(type="filesystem", path=None)
        assert "filesystem" in str(excinfo.value)

    def test_project_scoped_root_with_empty_path_raises(self):
        with pytest.raises(ValidationError) as excinfo:
            LiveViewsRootConfig(type="project_scoped", path="")
        assert "project_scoped" in str(excinfo.value)

    def test_filesystem_root_with_valid_path_passes(self, tmp_path):
        # Sanity: a real path does NOT trigger the validator.
        LiveViewsRootConfig(
            type="filesystem", path=str(tmp_path / "root")
        )

    def test_project_scoped_root_with_valid_path_passes(self):
        LiveViewsRootConfig(
            type="project_scoped", path=".agents/shared/planning"
        )


# ── tmp_images path-must-be-unset gate ───────────────────────────


class TestTmpImagesPathUnset:
    """``tmp_images`` roots MUST NOT have ``path`` set — the
    resolver delegates to the per-app ``TmpImageStore``
    substrate. A ``path`` on a ``tmp_images`` root is almost
    certainly a miscopy from one of the other two types."""

    def test_tmp_images_root_with_path_raises(self, tmp_path):
        with pytest.raises(ValidationError) as excinfo:
            LiveViewsRootConfig(type="tmp_images", path=str(tmp_path / "x"))
        msg = str(excinfo.value)
        assert "tmp_images" in msg
        # The actionable message hints at the cure (switch
        # type), so an operator doesn't just delete the
        # path and ship a misconfigured filesystem root.
        assert "filesystem" in msg or "project_scoped" in msg

    def test_tmp_images_root_with_none_path_passes(self):
        # Sanity: the seeded tmp-images shape is the
        # default (no path).
        LiveViewsRootConfig(type="tmp_images", path=None)

    def test_tmp_images_root_with_empty_path_raises(self):
        with pytest.raises(ValidationError) as excinfo:
            LiveViewsRootConfig(type="tmp_images", path="")
        assert "tmp_images" in str(excinfo.value)


# ── required_rel_subpath type + element gate ──────────────────────


class TestRequiredRelSubpathGating:
    """``required_rel_subpath`` is a project_scoped-only
    structural gate; setting it on filesystem / tmp_images is
    silently ignored at request time and a config bug we want
    to surface now."""

    def test_filesystem_root_with_required_rel_subpath_raises(self):
        with pytest.raises(ValidationError) as excinfo:
            LiveViewsRootConfig(
                type="filesystem",
                path="/tmp/x",
                required_rel_subpath=["design", "mockups"],
            )
        msg = str(excinfo.value)
        assert "required_rel_subpath" in msg
        assert "project_scoped" in msg

    def test_tmp_images_root_with_required_rel_subpath_raises(self):
        with pytest.raises(ValidationError) as excinfo:
            LiveViewsRootConfig(
                type="tmp_images",
                required_rel_subpath=["design", "mockups"],
            )
        msg = str(excinfo.value)
        assert "required_rel_subpath" in msg

    def test_empty_segment_in_required_rel_subpath_raises(self):
        with pytest.raises(ValidationError) as excinfo:
            LiveViewsRootConfig(
                type="project_scoped",
                path=".agents/shared/planning",
                required_rel_subpath=["design", ""],
            )
        msg = str(excinfo.value)
        # The actionable message points at the segment-level
        # bug, not just the field name.
        assert "empty segment" in msg or "empty" in msg

    def test_valid_required_rel_subpath_passes(self):
        # Sanity: the seeded designer-artifact shape.
        LiveViewsRootConfig(
            type="project_scoped",
            path=".agents/shared/planning",
            required_rel_subpath=["design", "mockups"],
        )


# ── disabled-root shorthand gate (M11 carry) ─────────────────────


class TestDisabledRootShortHand:
    """``LiveViewsRootConfig(enabled=False)`` MUST load cleanly —
    the documented operator-override-model disable shorthand
    (``_seed_phase1_roots`` docstring: ``{enabled: false}``
    disables a single seeded root). The default field values
    are ``type='filesystem'`` + ``path=None``; without the
    carry-through, the M11 validator would hard-fail this
    construction at boot, breaking the disable-shorthand the
    docstring itself documents.

    The fix is a one-line early-return at the top of
    ``_validate_root_shape``: a disabled root never resolves,
    so its shape is moot. ENABLED roots still go through every
    M11 rule unchanged — this is the pin.
    """

    def test_disabled_filesystem_default_shorthand_loads(self):
        # The default-args shape (the canonical
        # ``{enabled: false}`` disable from the
        # _seed_phase1_roots docstring at config.py:2266-2271):
        # type='filesystem', path=None, required_rel_subpath=None.
        # This is the live reproduction of the M11 carry bug.
        cfg = LiveViewsRootConfig(enabled=False)
        assert cfg.enabled is False
        assert cfg.type == "filesystem"
        assert cfg.path is None
        assert cfg.required_rel_subpath is None

    def test_disabled_project_scoped_without_path_loads(self):
        # ``type='project_scoped'`` with ``path=None`` is also
        # an enabled-root failure (path-required rule); with
        # the disable shorthand, it must also load.
        cfg = LiveViewsRootConfig(
            type="project_scoped", path=None, enabled=False
        )
        assert cfg.enabled is False
        assert cfg.type == "project_scoped"
        assert cfg.path is None

    def test_disabled_tmp_images_with_path_loads(self):
        # The opposite direction: ``tmp_images`` with a path
        # would fail the path-must-be-unset rule for enabled
        # roots. The disable shorthand bypasses that too —
        # which is correct, because a disabled root's shape
        # is irrelevant (it never resolves).
        cfg = LiveViewsRootConfig(
            type="tmp_images", path="/should/not/matter", enabled=False
        )
        assert cfg.enabled is False
        assert cfg.type == "tmp_images"

    def test_disabled_root_with_empty_subpath_segment_loads(self):
        # The required_rel_subpath empty-segment rule also
        # applies only to enabled roots. A disabled root
        # with a structurally-broken subpath is harmless
        # (never resolves) and must load.
        cfg = LiveViewsRootConfig(
            type="project_scoped",
            path=".agents/shared/planning",
            required_rel_subpath=["design", ""],
            enabled=False,
        )
        assert cfg.enabled is False
        assert cfg.required_rel_subpath == ["design", ""]


# ── LiveViewsConfig integration: seeded defaults still load ──────


class TestLiveViewsConfigSeeds:
    """The seeded Phase-1 roots must continue to load cleanly
    after the M11 validator lands. First-release breakage of
    the seeded defaults would be a HARD regression."""

    def test_seeded_designer_artifact_loads(self):
        # Force the seeds by constructing a LiveViewsConfig
        # with no operator override and reading the seeded
        # dict back. The seed function is canonical (it's
        # the operator-override-model source of truth in
        # ``daemon/config.py``).
        from daemon.config import _seed_phase1_roots

        seeds = _seed_phase1_roots()
        assert "designer-artifact" in seeds
        assert "planning" in seeds
        assert "tmp-images" in seeds
        # The validator ran on each seed entry as it was
        # constructed (the M11 invariant). If any seed
        # violates the new rules, this import would have
        # raised at module load.
        assert seeds["designer-artifact"].type == "project_scoped"
        assert seeds["designer-artifact"].path == ".agents/shared/planning"
        assert seeds["planning"].type == "project_scoped"
        assert seeds["tmp-images"].type == "tmp_images"
        assert seeds["tmp-images"].path is None


# ===========================================================================
# Group: max_media_bytes
# ===========================================================================


class TestMaxMediaBytes:
    def test_default(self):
        assert LiveViewsConfig().max_media_bytes == 2 * 1024 * 1024 * 1024

    def test_default_literal_is_the_shared_constant(self):
        """Single source of truth: Field default + resolver agree.

        ``DEFAULT_LIVE_VIEWS_MAX_MEDIA_BYTES`` (:5176) backs the
        ``max_media_bytes`` Field default, both resolver fallbacks and
        the router's no-service fallback. Before the dedupe the literal
        ``2 * 1024 * 1024 * 1024`` appeared five times; a change to one
        copy silently disagreed with the others.

        Mutation that flips this RED: change any one copy back to a
        different literal (e.g. resolver default → 1 GiB).
        """
        assert DEFAULT_LIVE_VIEWS_MAX_MEDIA_BYTES == 2 * 1024 * 1024 * 1024
        assert LiveViewsConfig().max_media_bytes == DEFAULT_LIVE_VIEWS_MAX_MEDIA_BYTES
        assert (
            _resolve_live_views_max_media_bytes(None, None)
            == DEFAULT_LIVE_VIEWS_MAX_MEDIA_BYTES
        )

    def test_ge_one_rejects_zero(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            LiveViewsConfig(max_media_bytes=0)

    def test_resolver_env_wins_over_yaml(self, monkeypatch):
        monkeypatch.setenv(ENSEMBLE_LIVE_VIEWS_MAX_MEDIA_BYTES, "2048")
        assert _resolve_live_views_max_media_bytes("2048", 4096) == 2048

    def test_resolver_yaml_wins_over_default(self):
        assert _resolve_live_views_max_media_bytes(None, 4096) == 4096

    def test_resolver_both_absent_default(self):
        assert _resolve_live_views_max_media_bytes(None, None) == 2 * 1024 * 1024 * 1024

    def test_resolver_blank_env_unset(self, monkeypatch):
        monkeypatch.delenv(ENSEMBLE_LIVE_VIEWS_MAX_MEDIA_BYTES, raising=False)
        assert _resolve_live_views_max_media_bytes("", None) == 2 * 1024 * 1024 * 1024
        assert _resolve_live_views_max_media_bytes("   ", None) == 2 * 1024 * 1024 * 1024

    def test_resolver_whitespace_yaml_unset(self):
        assert _resolve_live_views_max_media_bytes(None, "   ") == 2 * 1024 * 1024 * 1024

    def test_resolver_nonint_env_raises_naming_env(self):
        import pytest as _p

        with _p.raises(ValueError) as ei:
            _resolve_live_views_max_media_bytes("abc", None)
        assert ENSEMBLE_LIVE_VIEWS_MAX_MEDIA_BYTES in str(ei.value)

    def test_resolver_zero_env_raises(self):
        import pytest as _p

        with _p.raises(ValueError):
            _resolve_live_views_max_media_bytes("0", None)

    def test_resolver_negative_env_raises(self):
        import pytest as _p

        with _p.raises(ValueError):
            _resolve_live_views_max_media_bytes("-5", None)

    def test_resolver_nonint_yaml_raises(self):
        import pytest as _p

        with _p.raises(ValueError):
            _resolve_live_views_max_media_bytes(None, "abc")

    def test_resolver_float_yaml_raises_not_truncated(self):
        """A yaml float is a CONFIG ERROR — never silently truncated.

        ``int(4096.9)`` would have shipped 4096 (a cap the operator
        never wrote). The resolver now raises the same boot-failing
        ``ValueError`` for every non-int yaml value.

        Mutation that flips this RED: restore the ``int(yaml_value)``
        coercion tail — 4096.9 resolves to 4096 instead of raising.
        """
        import pytest as _p

        with _p.raises(ValueError) as ei:
            _resolve_live_views_max_media_bytes(None, 4096.9)
        assert "live_views.max_media_bytes" in str(ei.value)

    def test_resolver_float_whole_number_yaml_raises(self):
        """``4096.0`` is a float, not an int — still a config error.

        Guards against a "truncation is harmless when lossless" patch
        (``if float(v).is_integer(): return int(v)``): the type is the
        operator's contract, and a float in a byte-count field is a
        typo the boot should refuse.
        """
        import pytest as _p

        with _p.raises(ValueError):
            _resolve_live_views_max_media_bytes(None, 4096.0)

    def test_resolver_bool_yaml_raises(self):
        """``True`` is an ``int`` subclass in Python — must still raise."""
        import pytest as _p

        with _p.raises(ValueError):
            _resolve_live_views_max_media_bytes(None, True)

    def test_resolver_list_and_dict_yaml_raise(self):
        """Container shapes fail loud too (no ``TypeError`` leak)."""
        import pytest as _p

        with _p.raises(ValueError):
            _resolve_live_views_max_media_bytes(None, [4096])
        with _p.raises(ValueError):
            _resolve_live_views_max_media_bytes(None, {"v": 4096})

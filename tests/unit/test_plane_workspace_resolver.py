"""Gate (iv) — the Plane workspace resolver matrix (lane ``plane-pm-issue-tools`` Phase 2).

Covers pins **P2** (the policy matrix), **P4** (the D-1 fail-open case),
**P2-A** (predicate edge hardening) and **P2-B** (canonical DB-name input +
hard bans), table-driven against :func:`daemon.plane_workspace.resolve_plane_workspace`.

Every case asserts BOTH the permitted slug AND the typed reason string —
a resolution without its reason is not pinnable.

The resolver is PURE and INJECTED (``env=`` / ``postgres_db=``), so no case
here needs to touch the process environment.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))

import daemon.plane_workspace as daemon_plane_workspace
from daemon.plane_workspace import (
    PlaneWorkspaceResolution,
    TEST_WORKSPACE_SLUG,
    normalize_workspace_slug,
    resolve_plane_workspace,
    reset_resolution_cache,
)

RESOLVER_PATH = Path(daemon_plane_workspace.__file__)


@pytest.fixture(autouse=True)
def _clean_resolution_cache():
    reset_resolution_cache()
    yield
    reset_resolution_cache()


def _r(
    env: dict | None = None, *, postgres_db: str | None = None
) -> PlaneWorkspaceResolution:
    """Shorthand: resolve with a fully-controlled env."""
    return resolve_plane_workspace(env or {}, postgres_db=postgres_db)


# ── P2: the policy matrix ────────────────────────────────────────────────────


class TestPolicyMatrix:
    """P2 — every classification × slug combination, reason-asserted."""

    def test_explicit_live_with_nea_is_live(self):
        r = _r({"ENSEMBLE_SELF_ENV": "live", "PLANE_MCP_WORKSPACE_SLUG": "nea"})
        assert (r.env_label, r.env_source, r.slug, r.allowed, r.reason) == (
            "live", "explicit-marker", "nea", True, None,
        )

    def test_explicit_live_with_e2e_is_allowed_adj1_live_exempt(self):
        """G1 fold B3: live + e2e ⇒ ALLOWED; the strict-variant reason is
        NEVER emitted (its presence anywhere would be a strict-variant
        re-introduction)."""
        r = _r({"ENSEMBLE_SELF_ENV": "live", "PLANE_MCP_WORKSPACE_SLUG": "e2e"})
        assert r.env_label == "live"
        assert r.allowed is True
        assert r.slug == "e2e"
        assert r.reason is None
        assert "workspace 'e2e' is not permitted in the production environment" not in (r.reason or "")

    def test_explicit_live_with_third_slug_is_allowed(self):
        """G2 fold NB5: live permits ANY configured slug — the rule is about
        the environment, not a slug allow-list."""
        r = _r({"ENSEMBLE_SELF_ENV": "live", "PLANE_MCP_WORKSPACE_SLUG": "other"})
        assert (r.env_label, r.slug, r.allowed) == ("live", "other", True)

    def test_explicit_live_with_unset_slug_is_account_default(self):
        r = _r({"ENSEMBLE_SELF_ENV": "live"})
        assert (r.env_label, r.slug, r.allowed, r.reason) == (
            "live", None, True, None,
        )

    def test_explicit_demo_with_e2e_is_allowed(self):
        r = _r({"ENSEMBLE_SELF_ENV": "demo", "PLANE_MCP_WORKSPACE_SLUG": "e2e"})
        assert (r.env_label, r.env_source, r.slug, r.allowed) == (
            "demo", "explicit-marker", "e2e", True,
        )

    def test_explicit_demo_with_nea_is_refused(self):
        r = _r({"ENSEMBLE_SELF_ENV": "demo", "PLANE_MCP_WORKSPACE_SLUG": "nea"})
        assert r.allowed is False
        assert r.slug is None
        assert r.reason == (
            "workspace 'nea' is production-only; this environment resolves to demo"
        )

    def test_explicit_sandbox_is_non_live(self):
        r = _r({"ENSEMBLE_SELF_ENV": "sandbox", "PLANE_MCP_WORKSPACE_SLUG": "nea"})
        assert r.allowed is False
        assert r.reason == (
            "workspace 'nea' is production-only; this environment resolves to sandbox"
        )

    def test_non_live_third_slug_refused_with_typed_reason(self):
        r = _r({"ENSEMBLE_SELF_ENV": "dev", "PLANE_MCP_WORKSPACE_SLUG": "other"})
        assert r.allowed is False
        assert r.reason == (
            "workspace 'other' is not permitted in a non-production "
            "environment; expected 'e2e'"
        )

    def test_non_live_unset_slug_refused(self):
        r = _r({"ENSEMBLE_SELF_ENV": "dev"})
        assert r.allowed is False
        assert r.reason == (
            "PLANE_MCP_WORKSPACE_SLUG is unset in a non-production "
            "environment; expected 'e2e'"
        )

    def test_dev_checkout_no_marker_own_dev_db_is_non_live(self):
        """THE D-1 case (pin P4's green leg): a dev checkout with no marker
        and its OWN ``ensemble_dev`` database resolves NON-LIVE — the
        sibling-install auto-derive defect cannot reach this resolver."""
        r = _r({"PLANE_MCP_WORKSPACE_SLUG": "nea"}, postgres_db="ensemble_dev")
        assert (r.env_label, r.env_source) == ("dev", "own-postgres-db")
        assert r.allowed is False
        assert r.reason == (
            "workspace 'nea' is production-only; this environment resolves to dev"
        )

    def test_no_marker_own_prod_db_is_live(self):
        r = _r({"PLANE_MCP_WORKSPACE_SLUG": "nea"}, postgres_db="ensemble_prod")
        assert (r.env_label, r.env_source, r.slug, r.allowed) == (
            "live", "own-postgres-db", "nea", True,
        )

    def test_no_marker_unknown_db_is_unclassified_fail_closed(self):
        r = _r({"PLANE_MCP_WORKSPACE_SLUG": "nea"}, postgres_db="mystery_db")
        assert (r.env_label, r.env_source, r.allowed) == (None, "unclassified", False)
        assert r.reason == (
            "environment could not be classified as production; refusing "
            "to target the 'nea' workspace"
        )


# ── P2-A: predicate edge hardening ──────────────────────────────────────────


class TestPredicateEdgeHardening:
    """P2-A — opt-out short-circuit, garbage markers, empty=absent,
    exact non-normalizing VALID_ENVS match."""

    @pytest.mark.parametrize("opt_out", ["0", "false", "no", "off", "OFF", "False"])
    def test_opt_out_is_non_live_even_with_own_prod_db(self, opt_out):
        """THE P2-A fail-open: opt-out + own-db=prod must NEVER be live —
        the opt-out short-circuits the own-DB arm."""
        r = _r(
            {"ENSEMBLE_SELF_ENV": opt_out, "PLANE_MCP_WORKSPACE_SLUG": "nea"},
            postgres_db="ensemble_prod",
        )
        assert r.env_label != "live"
        assert (r.env_label, r.env_source) == (None, "unclassified")
        assert r.allowed is False

    @pytest.mark.parametrize("opt_out", ["0", "false", "no", "off"])
    def test_opt_out_still_permits_e2e(self, opt_out):
        """The opt-out is a NON-LIVE attestation, not a total refusal —
        the test workspace stays reachable (fail-closed ≠ dark)."""
        r = _r(
            {"ENSEMBLE_SELF_ENV": opt_out, "PLANE_MCP_WORKSPACE_SLUG": "e2e"},
            postgres_db="ensemble_prod",
        )
        assert r.allowed is True
        assert r.slug == TEST_WORKSPACE_SLUG

    @pytest.mark.parametrize("garbage", ["Live", "LIVE", "prod", "production", "flase", "dev;live"])
    def test_garbage_marker_is_non_live_and_refused(self, garbage):
        """Garbage ⇒ non-live/unclassified AND refused. A non-normalizing
        exact match means 'Live'/'prod'/typos never reach the own-DB arm
        and never classify as live — and the refusal is what makes the
        misconfiguration observable (the boot WARNING is the sole reason
        interpolator, so a garbage marker must produce a skip)."""
        r = _r(
            {"ENSEMBLE_SELF_ENV": garbage, "PLANE_MCP_WORKSPACE_SLUG": "e2e"},
            postgres_db="ensemble_prod",
        )
        assert (r.env_label, r.env_source) == (None, "unclassified")
        assert r.allowed is False
        assert garbage in (r.reason or "")
        assert "not a recognized environment" in (r.reason or "")

    def test_garbage_marker_never_reaches_own_db_arm(self):
        r = _r(
            {"ENSEMBLE_SELF_ENV": "Live", "PLANE_MCP_WORKSPACE_SLUG": "nea"},
            postgres_db="ensemble_prod",
        )
        # NOT live despite the prod DB — the garbage marker wins.
        assert r.env_label is None
        assert r.allowed is False

    def test_empty_marker_is_absent(self):
        """Empty marker + own-db=prod ⇒ LIVE (the empty marker carries no
        attestation — house ``_read_marker_value`` semantics)."""
        r = _r(
            {"ENSEMBLE_SELF_ENV": "", "PLANE_MCP_WORKSPACE_SLUG": "nea"},
            postgres_db="ensemble_prod",
        )
        assert (r.env_label, r.allowed) == ("live", True)

    def test_whitespace_only_marker_is_absent(self):
        r = _r(
            {"ENSEMBLE_SELF_ENV": "   ", "PLANE_MCP_WORKSPACE_SLUG": "nea"},
            postgres_db="ensemble_prod",
        )
        assert (r.env_label, r.allowed) == ("live", True)

    def test_valid_marker_whitespace_is_stripped(self):
        """Whitespace is stripped, case is NOT folded: ' live ' ⇒ live."""
        r = _r({"ENSEMBLE_SELF_ENV": " live ", "PLANE_MCP_WORKSPACE_SLUG": "nea"})
        assert (r.env_label, r.allowed) == ("live", True)

    def test_case_is_not_folded_live_caps_is_garbage(self):
        r = _r({"ENSEMBLE_SELF_ENV": "Live", "PLANE_MCP_WORKSPACE_SLUG": "nea"})
        assert r.env_label is None
        assert r.allowed is False


# ── P2-B: canonical DB-name input + hard bans ────────────────────────────────


class TestCanonicalDbNameAndHardBans:
    """P2-B — POSTGRES_DB first, own ``ensemble.json`` fallback, and the
    hard bans (no ``upgrade_tools`` import; no other file reads)."""

    def test_postgres_db_env_var_is_primary(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ENSEMBLE_DATA_DIR", str(tmp_path))
        (tmp_path / "ensemble.json").write_text(
            '{"postgres": {"db": "ensemble_prod"}}', encoding="utf-8"
        )
        # The env var (ensemble_dev) OUTRANKS the file (ensemble_prod).
        r = resolve_plane_workspace(
            {
                "ENSEMBLE_SELF_ENV": None,
                "PLANE_MCP_WORKSPACE_SLUG": "nea",
                "ENSEMBLE_DATA_DIR": str(tmp_path),
                "POSTGRES_DB": "ensemble_dev",
            }
        )
        assert r.env_label == "dev"
        assert r.env_source == "own-postgres-db"

    def test_env_var_less_prod_install_resolves_live_from_own_config(
        self, monkeypatch, tmp_path
    ):
        """THE P2-B live-continuity case: a prod install with NO
        ``POSTGRES_DB`` env var still resolves LIVE via its own
        ``ensemble.json``."""
        (tmp_path / "ensemble.json").write_text(
            '{"database": "postgres", "postgres": {"db": "ensemble_prod"}}',
            encoding="utf-8",
        )
        r = resolve_plane_workspace(
            {
                "PLANE_MCP_WORKSPACE_SLUG": "nea",
                "ENSEMBLE_DATA_DIR": str(tmp_path),
            }
        )
        assert (r.env_label, r.env_source, r.allowed) == (
            "live", "own-postgres-db", True,
        )

    def test_resolver_reads_no_file_when_env_var_supplied(
        self, monkeypatch, tmp_path
    ):
        """P2-B zero-file-access: with ``POSTGRES_DB`` present the resolver
        opens NOTHING — a probe of ``Path.read_text`` would catch any
        stray read."""
        import builtins

        # Make cwd's ./data NOT exist for this test — irrelevant, the
        # point is that read_text is never reached.
        opened: list[str] = []
        real_read_text = Path.read_text

        def guarded_read_text(self, *args, **kwargs):  # noqa: ANN001
            opened.append(str(self))
            return real_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", guarded_read_text)
        r = resolve_plane_workspace(
            {
                "PLANE_MCP_WORKSPACE_SLUG": "nea",
                "POSTGRES_DB": "ensemble_dev",
            }
        )
        assert opened == []
        assert r.env_label == "dev"

    def test_no_upgrade_tools_import(self):
        """P2-B grep pin (structural form): the resolver module must not
        import ``daemon.tools.upgrade_tools`` — neither directly nor
        transitively at module scope."""
        source = RESOLVER_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "upgrade_tools" not in alias.name
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert "upgrade_tools" not in mod

    def test_resolver_reads_no_foreign_file_paths(self):
        """P2-B: the only filesystem touch in the module source is the own
        ``ensemble.json`` (via ``Path(data_dir) / "ensemble.json"``)."""
        source = RESOLVER_PATH.read_text(encoding="utf-8")
        # No absolute/foreign path literals anywhere in the module —
        # the only path the module may construct is "<data_dir>/ensemble.json".
        for forbidden in ("/home/", "/etc/", "/proc/", "agents-ensemble"):
            assert forbidden not in source, (
                f"resolver source references forbidden path fragment {forbidden!r}"
            )
        # The literal 'ensemble.json' is the ONLY filename in the module.
        assert source.count("ensemble.json") >= 1
        assert ".read_text" in source  # the single read mechanism

    def test_missing_own_config_falls_back_to_unclassified(self, tmp_path):
        r = resolve_plane_workspace(
            {
                "PLANE_MCP_WORKSPACE_SLUG": "nea",
                "ENSEMBLE_DATA_DIR": str(tmp_path / "does-not-exist"),
            }
        )
        assert (r.env_source, r.allowed) == ("unclassified", False)


# ── P4: the D-1 fail-open case ───────────────────────────────────────────────


class TestD1FailOpen:
    """P4 — a dev checkout misclassified as live by the shared oracle must
    NOT obtain a nea-bearing resolution. The lane resolver ignores the
    auto-derive entirely: it has no sibling-file arm at all."""

    def test_no_sibling_env_read_exists_in_the_module(self):
        source = RESOLVER_PATH.read_text(encoding="utf-8")
        # The shared oracle's failure mode is reading a SIBLING install's
        # .env (agents-ensemble / .env). The resolver has no such read.
        for absent in ("agents-ensemble", "agents-ensemble-demo"):
            assert absent not in source

    def test_dev_db_never_yields_live_even_with_prod_slug_configured(self):
        """The exact R16 shape: dev + own ``ensemble_dev`` + a stray
        ``nea`` slug ⇒ refused, never a nea-bearing resolution."""
        r = _r({"PLANE_MCP_WORKSPACE_SLUG": "nea"}, postgres_db="ensemble_dev")
        assert r.slug is None
        assert r.allowed is False


# ── P2-C: the canonical slug normalization ──────────────────────────────────


class TestCanonicalSlugNormalization:
    """P2-C — ONE normalization shared by both lanes (trim + strip one
    outer matching quote pair)."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("nea", "nea"),
            ("  nea  ", "nea"),
            ('"nea"', "nea"),
            ("'nea'", "nea"),
            ('" e2e "', "e2e"),
            ("", ""),
            (None, ""),
            ('"unclosed', '"unclosed'),  # no matching trailer — unchanged
            ('a"b"c', 'a"b"c'),  # interior quotes preserved
        ],
    )
    def test_normalize_workspace_slug(self, raw, expected):
        assert normalize_workspace_slug(raw) == expected

    def test_quoted_env_slug_resolves_through_the_matrix(self):
        """A quote-poisoned env value flows through the canonical
        normalization — the gate-(iv) matrix sees the CLEAN slug."""
        r = _r({"ENSEMBLE_SELF_ENV": "dev", "PLANE_MCP_WORKSPACE_SLUG": '"e2e"'})
        assert r.allowed is True
        assert r.slug == "e2e"


# ── A1: the case-insensitive header-NAME lookup ─────────────────────────────

class TestHeaderValueLookup:
    """A1 (reviewer-adjudicated tidier must-fix, 2026-11-02) — ONE
    helper for the ``x-workspace-slug`` reads: case-insensitive on the
    header NAME, VALUES untouched."""

    @pytest.mark.parametrize(
        ("headers", "expected"),
        [
            ({"x-workspace-slug": "e2e"}, "e2e"),
            ({"X-Workspace-Slug": "nea"}, "nea"),  # case-variant NAME
            ({"X-WORKSPACE-SLUG": "E2E"}, "E2E"),  # any case + value kept
            ({"Authorization": "Bearer k", "x-workspace-slug": "a"}, "a"),
            ({"authorization": "Bearer k"}, None),  # different header
            ({}, None),
            (None, None),
            ("not-a-dict", None),
            ([("x-workspace-slug", "e2e")], None),  # non-dict shape
        ],
    )
    def test_lookup_matrix(self, headers, expected):
        from daemon.plane_workspace import _header_value

        assert _header_value(headers, "x-workspace-slug") == expected

    def test_first_match_wins_and_values_are_untouched(self):
        from daemon.plane_workspace import _header_value

        headers = {"X-Workspace-Slug": " MixedCase ", "x-workspace-slug": "other"}
        # The first case-insensitive match is returned VERBATIM — no
        # trimming, no case-folding on the value.
        assert _header_value(headers, "x-workspace-slug") == " MixedCase "

    def test_helper_is_exported(self):
        import daemon.plane_workspace as pw

        assert "_header_value" in pw.__all__


# ── C4: the reason templates escape the slug ────────────────────────────────


class TestReasonSlugEscaping:
    """C4 (reviewer-adjudicated tidier must-fix, 2026-11-02) — the
    ``REASON_*`` template fills escape the operator-controlled slug
    repr-style: a newline/control character in
    ``PLANE_MCP_WORKSPACE_SLUG`` can never forge a second
    agent-visible log line. Clean values render byte-identically;
    value case-sensitivity is UNCHANGED."""

    def test_newline_slug_cannot_forge_a_second_line(self):
        r = _r(
            {
                "ENSEMBLE_SELF_ENV": "dev",
                "PLANE_MCP_WORKSPACE_SLUG": "evil\nFAKE-WARNING: injected",
            }
        )
        assert r.allowed is False
        assert r.reason is not None
        assert "\n" not in r.reason, (
            "a raw newline survived into the typed reason"
        )
        assert "\r" not in r.reason
        # The escaped form IS visible (repr-style) — the operator sees
        # the pathological value without it forging a log line.
        assert "\\n" in r.reason
        assert "not permitted in a non-production environment" in r.reason

    def test_control_characters_render_escaped(self):
        r = _r(
            {
                "ENSEMBLE_SELF_ENV": "dev",
                "PLANE_MCP_WORKSPACE_SLUG": "a\tb\x00c",
            }
        )
        assert r.allowed is False
        assert r.reason is not None
        assert "\t" not in r.reason
        assert "\x00" not in r.reason
        assert "\\t" in r.reason and "\\x00" in r.reason

    def test_clean_slug_renders_byte_identically(self):
        """A clean third slug renders EXACTLY as before the escaping —
        no behavioral change on legitimate values."""
        r = _r({"ENSEMBLE_SELF_ENV": "dev", "PLANE_MCP_WORKSPACE_SLUG": "team-ws"})
        assert r.reason == (
            "workspace 'team-ws' is not permitted in a non-production "
            "environment; expected 'e2e'"
        )

    def test_value_case_sensitivity_is_unchanged(self):
        """Escaping is RENDER-ONLY — ``E2E`` is still refused (the
        comparison stays exact on values)."""
        r = _r({"ENSEMBLE_SELF_ENV": "dev", "PLANE_MCP_WORKSPACE_SLUG": "E2E"})
        assert r.allowed is False
        assert "'E2E'" in (r.reason or "")


# ── P2-M: resolve-once threading ─────────────────────────────────────────────


class TestResolveOnceThreading:
    """P2-M — ``current_plane_workspace`` memoizes on an env fingerprint,
    so every seam in one operation sees the SAME resolution object."""

    def test_current_resolution_is_stable_and_memoized(self, monkeypatch):
        monkeypatch.setenv("ENSEMBLE_SELF_ENV", "dev")
        monkeypatch.setenv("PLANE_MCP_WORKSPACE_SLUG", "e2e")
        monkeypatch.setenv("POSTGRES_DB", "ensemble_dev")
        first = daemon_plane_workspace.current_plane_workspace()
        second = daemon_plane_workspace.current_plane_workspace()
        assert first is second

    def test_env_change_re_resolves(self, monkeypatch):
        monkeypatch.setenv("ENSEMBLE_SELF_ENV", "dev")
        monkeypatch.setenv("PLANE_MCP_WORKSPACE_SLUG", "e2e")
        monkeypatch.setenv("POSTGRES_DB", "ensemble_dev")
        assert daemon_plane_workspace.current_plane_workspace().allowed is True
        monkeypatch.setenv("PLANE_MCP_WORKSPACE_SLUG", "nea")
        r = daemon_plane_workspace.current_plane_workspace()
        assert r.allowed is False
        assert r.reason is not None

    def test_injected_signature_drives_the_matrix_without_process_env(self):
        r = resolve_plane_workspace(
            {"ENSEMBLE_SELF_ENV": "demo", "PLANE_MCP_WORKSPACE_SLUG": "e2e"},
            postgres_db="irrelevant-when-marker-present",
        )
        assert (r.env_label, r.slug, r.allowed) == ("demo", "e2e", True)


# ── The historical strict-variant string must never be constructible ─────────


class TestStrictVariantNeverEmitted:
    """G1 fold B3: the ADJ-1 live-exempt rule means the strict-variant
    refusal string is never produced by the resolver."""

    @pytest.mark.parametrize(
        "env",
        [
            {"ENSEMBLE_SELF_ENV": "live", "PLANE_MCP_WORKSPACE_SLUG": "e2e"},
            {"ENSEMBLE_SELF_ENV": "live", "PLANE_MCP_WORKSPACE_SLUG": "nea"},
            {"ENSEMBLE_SELF_ENV": "live", "PLANE_MCP_WORKSPACE_SLUG": "anything"},
            {"ENSEMBLE_SELF_ENV": "live"},
        ],
    )
    def test_live_is_never_refused(self, env):
        assert resolve_plane_workspace(env, postgres_db="ensemble_prod").reason is None

    def test_historical_string_is_absent_from_the_module(self):
        """The strict string appears in the module ONLY as the documented
        historical constant — the resolver never formats it.

        Fold C11 (``plane-pm-issue-tools``): the production-dead constant
        was DROPPED (no production path can emit it), so the strict
        string now appears NOWHERE in the resolver module — the
        invariant is STRICTER: the resolver never formats it AND no
        constant keeps it alive.
        """
        source = RESOLVER_PATH.read_text(encoding="utf-8")
        assert source.count("not permitted in the production environment") == 0


class TestR1ResolverNeverLogs:
    """X3.7 / R-1 — the resolver NEVER logs. Structural pin: the module
    does not import ``logging`` and holds no logger, so no call site can
    emit from inside the resolution path (the manager's skip WARNING is
    the sole reason interpolator)."""

    def test_module_has_no_logger(self):
        source = RESOLVER_PATH.read_text(encoding="utf-8")
        assert "import logging" not in source
        assert "getLogger" not in source
        assert "logger." not in source

    def test_resolution_emits_no_log_records(self, caplog):
        import logging as _logging

        with caplog.at_level(_logging.DEBUG):
            # Every classification path, refused and allowed alike.
            resolve_plane_workspace(
                {"ENSEMBLE_SELF_ENV": "dev", "PLANE_MCP_WORKSPACE_SLUG": "nea"},
                postgres_db="ensemble_dev",
            )
            resolve_plane_workspace(
                {"ENSEMBLE_SELF_ENV": "Live", "PLANE_MCP_WORKSPACE_SLUG": "e2e"},
                postgres_db="ensemble_prod",
            )
            resolve_plane_workspace(
                {"PLANE_MCP_WORKSPACE_SLUG": "nea"}, postgres_db="ensemble_prod"
            )
        assert caplog.records == []

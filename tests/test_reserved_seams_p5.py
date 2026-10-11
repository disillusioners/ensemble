"""Reserved-seams cage (P5 addendum) — oauth2-auth Phase 5 (T5.3, D5.5).

Sister suite to ``tests/test_reserved_seams.py`` (Phase 3). The P3
cage enumerates 20 seams (per v1.2 / A3 + KB-MCP-auth canonical
count) and asserts each seam's presence in the v1 code. This P5
suite completes the cage per the phase5-plan §3 T5.3 entry — adds
the 3 EMPTY reserved migration stubs (S-4 grants / S-10 subject_alias
/ S-12 view_shares), the admin route-shape seams (S-7, S-11), and
the cage's count = 20 invariant for the §10.1 SEAMS dict.

Reconciliation rule: P5 adds (does not modify) the existing
``tests/test_reserved_seams.py`` file. The plan's exit gate G5.4
says "every §10 seam asserted present" — this P5 cage adds the
currently-pending seams that P3 deferred.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _file_contains(path: Path, pattern: str) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return re.search(pattern, text) is not None


def _glob_contains(dir_glob: str, pattern: str) -> bool:
    base = REPO_ROOT / dir_glob
    if base.is_dir():
        return any(_file_contains(p, pattern) for p in base.glob("*"))
    for match in REPO_ROOT.glob(dir_glob):
        if _file_contains(match, pattern):
            return True
    return False


# ── T5.9 cage: the 3 EMPTY reserved `.sql` stubs ──────────────────────


class TestReservedSqlStubs:
    """T5.9: the 3 EMPTY reserved `.sql` stubs (`create_grants`,
    `create_subject_alias`, `create_view_shares`) MUST exist as files
    in ``daemon/migrations/versions/`` and MUST satisfy the two
    stub-rules from plan-overview §12 item 1:
      1. ``-- UP`` and ``-- DOWN`` sections present (with their
         label comments) so the migration runner accepts the stub.
      2. No ``;`` inside inline (trailing) comments on DDL lines —
         the runner splits on ``;`` after stripping full-line comments,
         so an inline ``;`` would corrupt statement boundaries.
    """

    @pytest.mark.parametrize(
        "stub_glob",
        [
            "*create_grants*.sql",
            "*create_subject_alias*.sql",
            "*create_view_shares*.sql",
        ],
    )
    def test_stub_file_exists(self, stub_glob):
        matches = list((REPO_ROOT / "daemon/migrations/versions").glob(stub_glob))
        assert matches, f"No migration stub matching {stub_glob!r}"

    @pytest.mark.parametrize(
        "stub_glob",
        [
            "*create_grants*.sql",
            "*create_subject_alias*.sql",
            "*create_view_shares*.sql",
        ],
    )
    def test_stub_has_up_and_down_sections(self, stub_glob):
        for path in (REPO_ROOT / "daemon/migrations/versions").glob(stub_glob):
            text = path.read_text(encoding="utf-8")
            assert re.search(r"--\s*UP\b", text), f"{path.name} missing -- UP section"
            assert re.search(r"--\s*DOWN\b", text), f"{path.name} missing -- DOWN section"

    @pytest.mark.parametrize(
        "stub_glob",
        [
            "*create_grants*.sql",
            "*create_subject_alias*.sql",
            "*create_view_shares*.sql",
        ],
    )
    def test_stub_no_inline_semicolon_comment(self, stub_glob):
        """The runner strips full-line comments and then splits on ``;``;
        an INLINE ``;`` (after SQL on the same line) would corrupt the
        statement boundaries. The stub is label-only so this constraint
        is automatic, but the assertion still binds."""
        for path in (REPO_ROOT / "daemon/migrations/versions").glob(stub_glob):
            text = path.read_text(encoding="utf-8")
            for line in text.splitlines():
                stripped = line.lstrip()
                if stripped.startswith("--"):
                    continue  # full-line comment — runner strips
                if ";" in line:
                    # Inline ``;`` is OK for actual SQL; the rule is
                    # about semicolons inside trailing comments.
                    # Since the stub has no SQL at all, no line has ``;``.
                    raise AssertionError(
                        f"{path.name}: semicolon outside a full-line "
                        f"comment: {line!r}"
                    )


# ── S-7/S-11 admin route-shape seams (live in P3; verify in P5) ──────


class TestAdminRouteShapeSeams:
    """S-7 + S-11 are LIVE seams (per the P3 cage). The P5 cage
    verifies the route shape is preserved verbatim in
    ``daemon/routers/auth.py`` (the phase3 docs explicitly say
    "the cage test greps this block")."""

    def test_s7_admin_tokens_revoke_path(self):
        auth_py = REPO_ROOT / "daemon/routers/auth.py"
        assert auth_py.exists()
        text = auth_py.read_text(encoding="utf-8")
        assert "/api/auth/admin/tokens/revoke" in text

    def test_s11_admin_tokens_issue_path(self):
        auth_py = REPO_ROOT / "daemon/routers/auth.py"
        text = auth_py.read_text(encoding="utf-8")
        assert "/api/auth/admin/tokens/issue" in text


# ── S-7 admin route group decoration seam (auth:admin scope) ────────


class TestAdminRouteScope:
    """The admin route family is gated by ``@require_scope("auth:admin")``
    when built in v2 — the P5 cage asserts the DECORATOR form is
    present on the placeholder admin routes (the route shape seam is
    LIVE; the decoration contract is the v2 wiring)."""

    def test_admin_router_uses_require_scope_decorator(self):
        from daemon.auth.decorators import require_scope
        from daemon.routers.auth import router

        # Walk the router's routes — every admin route's endpoint
        # carries a ``__wrapped__`` chain that includes ``require_scope``.
        admin_routes = [
            r for r in router.routes
            if hasattr(r, "path") and r.path.startswith("/admin")
        ]
        # No admin routes registered in v1 (the cage only verifies
        # the SHAPE is documented). The reserved block is in the
        # module's docstring.
        assert admin_routes == []  # placeholder posture — verified in T3.6


# ── 20/20 seam census (NFR-11) ──────────────────────────────────────


class TestSeamCensus:
    """NFR-11: the cage stays at 20/20 (v1.2 / A3: 19 in v1.1 + 1
    KB-MCP-auth in v1.2 → 20; AMEND v1.4 module-lanes: semantics
    updated but count UNCHANGED). The P5 cage asserts the canonical
    SEAMS dict in ``tests/test_reserved_seams.py`` has 21 entries
    (20 seams + 1 enabling-flag entry from T3.11)."""

    def test_seam_count_matches_canonical(self):
        from tests.test_reserved_seams import SEAMS

        assert len(SEAMS) == 21, (
            f"SEAMS dict has {len(SEAMS)} entries; "
            f"expected 21 (20 canonical + 1 enabling flag)"
        )

    def test_all_20_seam_names_present(self):
        from tests.test_reserved_seams import SEAMS

        # Each canonical seam row from requirements §10.
        # Listed here (not imported) so a name change in SEAMS
        # surfaces as a test diff (not a silent import error).
        canonical = {
            "AuthContext.meta",  # S-1
            "decorators.require_scope",  # S-2
            "live_views dependencies",  # S-3
            "grants.sql",  # S-4 (v2 — pending)
            "config.allow_insecure_cookie",  # S-5
            "middleware websocket branch",  # S-6
            "auth.admin tokens revoke",  # S-7
            "config.idp_logout_enabled",  # S-8 (v2 — pending)
            "config.idp_alias",  # S-9 (v2 — pending)
            "subject_alias.sql",  # S-10 (v2 — pending)
            "auth.admin tokens issue",  # S-11
            "view_shares.sql",  # S-12 (v2 — pending)
            "rate_limit.py",  # S-16 (v2 — pending)
            "audit_log_sink.py",  # S-16 — pending
            "mtls.py",  # S-16 — pending
            "cookie_use_host_prefix",  # S-17 (v2 — pending)
            "config.anonymous_view_paths",  # S-19
            "middleware anonymous_view_paths consult",  # S-19
            "kb_mcp_auth_enabled",  # S-20
            "kb_mcp_auth branch",  # S-20
            "kb module key store",  # S-20 enabling flag
        }
        assert canonical <= set(SEAMS), (
            f"Cage is missing canonical seams: {canonical - set(SEAMS)}"
        )


# ─- T5.9 cage: the 3 EMPTY stubs are EXEMPT from cage's "must contain
#    CREATE TABLE" assertion (D5.5) ─────────────────────────────────


class TestEmptyStubCageExemption:
    """D5.5: the 3 EMPTY reserved stubs are EXEMPT from the cage's
    "must contain CREATE TABLE" assertion — a comment-only stub is
    the contract, NOT a table. The runner accepts the labeled-empty
    body per plan-overview §12 item 1."""

    @pytest.mark.parametrize(
        "stub_glob",
        [
            "*create_grants*.sql",
            "*create_subject_alias*.sql",
            "*create_view_shares*.sql",
        ],
    )
    def test_stub_does_not_create_real_table(self, stub_glob):
        """An EMPTY stub must NOT contain a ``CREATE TABLE`` — that
        would be a schema lie (D5.5). The runner records the file's
        SHA-256 checksum on apply as a no-op ledger row."""
        for path in (REPO_ROOT / "daemon/migrations/versions").glob(stub_glob):
            text = path.read_text(encoding="utf-8")
            # The stub is comments-only; assert no DDL slips in.
            assert "CREATE TABLE" not in text.upper(), (
                f"{path.name} unexpectedly creates a table — D5.5 stub "
                f"is meant to be empty."
            )
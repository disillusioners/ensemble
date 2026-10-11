"""Reserved-seams regression cage — oauth2-auth (requirements §10.1).

The §10.1 SEAMS registry (20 canonical seams + 1 enabling-flag entry).
Phase 3's dispatch obligation: the KB-MCP-auth seam entry recorded with
its v1.4 semantics (the mechanism is now BUILT per T3.11 — the seam is
"module-lane classification + own-key enforcement", decisions.md D19;
grep assertions UNCHANGED, NFR-11 count stays 20/20).

Entries whose artifacts are v2-future (grants/view_shares/subject_alias
tables, rate_limit/mtls/audit-log modules, the IdP-alias config rows)
are enumerated but SKIP-pending with their landing phase — the skip
inventory IS the v2 worklist; they flip to hard asserts as those
phases land. P1/P2/P3-created artifacts assert HARD.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# (glob-path, pattern, status) — status "live" asserts hard; "pending"
# skips with the landing phase (v2 artifact; §10.2 promise).
SEAMS: dict[str, tuple[str, str, str]] = {
    # S-1 (P3 — BUILT)
    "AuthContext.meta": ("daemon/auth/context.py", "meta", "live"),
    # S-2 (P3 — BUILT)
    "decorators.require_scope": ("daemon/auth/decorators.py", "def require_scope", "live"),
    # S-3 (live-views — pre-existing subsystem; its auth dependencies
    # attach in v2 via the S-12 share-token work — pending there)
    "live_views dependencies": (
        "daemon/routers/live_views.py",
        "dependencies",
        "pending:v2 live-view auth dependencies (S-12 share tokens)",
    ),
    # S-4 (v2 — grants table)
    "grants.sql": ("daemon/migrations/versions", r"create_grants", "pending:v2 grants table"),
    # S-5 (P1 — BUILT)
    "config.allow_insecure_cookie": ("daemon/config.py", "allow_insecure_cookie", "live"),
    # S-6 (P3 — BUILT: the websocket pass-through branch, FR-21)
    "middleware websocket branch": (
        "daemon/middleware/auth.py",
        r'scope\["type"\] != "http"',
        "live",
    ),
    # S-7 route-shape seams (§5.3: the /api/auth/admin/* shape MUST be
    # in the protected set; v1 = shape-only, implementation P4)
    "auth.admin tokens revoke": (
        "daemon/routers/auth.py",
        "/api/auth/admin/tokens/revoke",
        "live",
    ),
    # S-8 (v2 — federated logout)
    "config.idp_logout_enabled": ("daemon/config.py", "idp_logout_enabled", "pending:v2 federated logout"),
    # S-9 (v2 — IdP alias)
    "config.idp_alias": ("daemon/config.py", "idp_alias", "pending:v2 multi-IdP"),
    # S-10 (v2 — subject_alias table)
    "subject_alias.sql": ("daemon/migrations/versions", r"create_subject_alias", "pending:v2 subject alias"),
    # S-11 route-shape seam (P4 CLI/management surface)
    "auth.admin tokens issue": (
        "daemon/routers/auth.py",
        "/api/auth/admin/tokens/issue",
        "live",
    ),
    # S-12 (v2 — view shares)
    "view_shares.sql": ("daemon/migrations/versions", r"create_view_shares", "pending:v2 share tokens"),
    # S-16 (v2 modules)
    "rate_limit.py": ("daemon/middleware", r"rate_limit", "pending:v2 rate limiting"),
    "audit_log_sink.py": ("daemon/services", r"audit_log_sink", "pending:v2 audit sink"),
    "mtls.py": ("daemon/middleware", r"mtls", "pending:v2 mTLS"),
    # S-17 (v2 — cookie scope binding; §10 verification row: static
    # grep of cookie.py for cookie_use_host_prefix — the ``__Host-``
    # prefix hardening toggle. SKIP-pending: the v1 cookie layer ships
    # without the flag; flips to hard assert when v2 hardening lands.)
    "cookie_use_host_prefix": (
        "daemon/auth/cookie.py",
        "cookie_use_host_prefix",
        "pending:v2 cookie scope binding (__Host- prefix) (S-17)",
    ),
    # S-19 (P1 config + P3 middleware consult — BUILT)
    "config.anonymous_view_paths": ("daemon/config.py", "anonymous_view_paths", "live"),
    "middleware anonymous_view_paths consult": (
        "daemon/middleware/auth.py",
        "anonymous_view_paths",
        "live",
    ),
    # S-20 (AMEND v1.4 — semantics SUPERSEDED per D19: the seam is the
    # module-lane classification + own-key enforcement; BUILT in P3)
    "kb_mcp_auth_enabled": ("daemon/config.py", "kb_mcp_auth_enabled", "live"),
    "kb_mcp_auth branch": (
        "daemon/middleware/auth.py",
        "module_lane",
        "live",
    ),
    # Enabling-flag entry (T3.11 — the mechanism is BUILT; the store +
    # guard live module-owned per D20a)
    "kb module key store": ("daemon/mcp/kb_keys.py", "class McpKbKeyStore", "live"),
}


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


@pytest.mark.parametrize("name", sorted(SEAMS))
def test_seam_present(name: str):
    """Each reserved seam must be statically present in the v1 code
    (or is a recorded v2 pending — see module docstring)."""
    rel_path, pattern, status = SEAMS[name]
    if status.startswith("pending"):
        pytest.skip(f"v2 seam — lands with: {status.split(':', 1)[1]}")
    target = REPO_ROOT / rel_path
    if target.is_dir():
        assert _glob_contains(rel_path, pattern), f"Seam missing: {name}"
    else:
        assert target.exists(), f"Seam file missing: {rel_path}"
        assert _file_contains(target, pattern), f"Seam missing: {name}"


def test_seam_count_unchanged():
    """NFR-11: the registry stays 20 canonical seams (S-1…S-20) covered
    by 21 dict entries (S-20 contributes 2 grep assertions + the T3.11
    enabling-flag entry — requirements §10.1)."""
    assert len(SEAMS) == 21


def test_kb_mcp_seam_mechanism_built():
    """T3.11 dispatch note: the KB-MCP-auth seam is no longer prose —
    the module-owned store + mount guard are BUILT (D19/D20a)."""
    from daemon.mcp.kb_keys import KB_KEY_HEADER, KbKeyGuardApp, McpKbKeyStore  # noqa: F401
    from daemon.repositories.mcp_kb_keys.models import McpKbKey  # noqa: F401

    assert KB_KEY_HEADER == "x-kb-key"

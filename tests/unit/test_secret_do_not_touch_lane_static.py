"""agent-secrets P2 phase 4 — do-not-touch lane byte-clean static
pins (commission coverage gap (b)).

The P2 design (p2-architecture-recommendation.md §0 + §5.4 binding
constraint) declares the KMS-Lite lanes and the BYOK lane
DO-NOT-TOUCH: the P2 resolver is a SIBLING module
(``daemon/services/secret_resolver.py``) and must NEVER be wired
into:

* ``daemon/services/kms_lite.py`` (LANE-1 handle store),
* ``daemon/services/kms_resolver.py`` (LANE-2 env-ref resolver),
* ``daemon/tools/ens_env_tools.py`` (``ens_env_read`` BYOK lane),

and ``build_pooled_stdio_config`` (``daemon/mcp/warmup_pool.py``)
must keep returning STORED config — P2 resolution happens at the
SPAWN POINT (design §1c correction over plan §6.2); resolving in
the config builder would persist plaintext into DB config
(the R1 invariant break).

Mirrors the repo's static-audit pattern
(``tests/unit/test_log_secret_static_audit.py`` /
``tests/unit/test_auth_secret_hygiene.py``): AST + source-grep
against the REAL files at test time, with a synthetic
positive-case check proving the detector actually fires (false
negatives are NOT acceptable — a silent import would wire agent
secrets into the KMS trust boundary).

Run in isolation:

    .venv/bin/python -m pytest tests/unit/test_secret_do_not_touch_lane_static.py -q
"""

from __future__ import annotations

import ast
import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest

# ─────────────────────────────────────────────────────────────────────
# Anchors (path-relative to THIS file — never live-shaped paths)
# ─────────────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: The do-not-touch lanes (design §0: "Do-not-touch lanes untouched:
#: db_connections Fernet, KMS-Lite LANE-1/2, ens_env_read BYOK").
DO_NOT_TOUCH_FILES: tuple[str, ...] = (
    "daemon/services/kms_lite.py",
    "daemon/services/kms_resolver.py",
    "daemon/tools/ens_env_tools.py",
)

#: The config-builder that must stay resolution-free (spawn-point
#: resolution only — design §1c).
POOLED_CONFIG_FILE = "daemon/mcp/warmup_pool.py"
POOLED_CONFIG_FUNC = "build_pooled_stdio_config"

#: Any occurrence of these needles in a do-not-touch file is a
#: finding. They are the P2 resolver's module path, its service
#: import target, and its error/entry symbols — an import OR a
#: bare reference means the lane reached into the P2 stack.
FORBIDDEN_NEEDLES: tuple[str, ...] = (
    "secret_resolver",
    "secret_service",
    "SecretMarkerResolutionError",
    "resolve_secret_markers",
    "ENS_SECRET_MARKER",
)

#: Spawn-point call sites that MUST keep the resolution (positive
#: controls — removing one is a regression of design §1c).
EXPECTED_SPAWN_SITES: tuple[tuple[str, int], ...] = (
    ("daemon/mcp/warmup_pool.py", 1),   # _create_pooled_connection
    ("daemon/mcp/connection_manager.py", 2),  # both cold spawns
)

#: The spawn-env resolution seam (F-11): the resolution lives in the
#: shared helper ``daemon/mcp/warmup_pool.resolve_spawn_env`` and every
#: spawn site DISPATCHES to it.
SPAWN_ENV_HELPER_FILE = "daemon/mcp/warmup_pool.py"
SPAWN_ENV_HELPER_FUNC = "resolve_spawn_env"


@dataclass(frozen=True)
class Finding:
    """One forbidden-needle occurrence in a do-not-touch file."""

    file: str
    line: int
    needle: str
    snippet: str


def _read(rel: str) -> str:
    return ( _REPO_ROOT / rel ).read_text(encoding="utf-8")


def _find_needles(source: str, rel: str) -> list[Finding]:
    findings: list[Finding] = []
    for lineno, line in enumerate(source.splitlines(), start=1):
        for needle in FORBIDDEN_NEEDLES:
            if needle in line:
                findings.append(
                    Finding(
                        file=rel,
                        line=lineno,
                        needle=needle,
                        snippet=line.strip()[:120],
                    )
                )
    return findings


def _count_resolve_calls_in_module(source: str) -> int:
    """AST count of ``resolve_spawn_env`` NAME loads.

    F-11 re-pin, clause 1 ("resolution alive behind
    ``resolve_spawn_env``"): the spawn-env resolution was extracted into
    the shared helper ``daemon/mcp/warmup_pool.resolve_spawn_env``
    (defined at ``warmup_pool.py:121``; its body is the single
    ``resolve_secret_markers`` call at ``warmup_pool.py:149``). The
    three effective spawn sites now DISPATCH to that helper instead of
    calling the resolver directly:

    * ``warmup_pool.py:588`` -- the warm pool
      (``_create_pooled_connection``),
    * ``connection_manager.py:216`` and ``:497`` -- both cold spawns.

    So the literal ``resolve_secret_markers`` no longer appears in
    ``connection_manager.py`` at all (measured: 0 Call nodes there, 1 in
    ``warmup_pool.py``). An AST name-count pin cannot follow the
    indirection, so this pin was bound to a seam that no longer exists
    and was RED at latest.

    This counter counts the ``resolve_spawn_env`` NAME loads at the
    spawn sites instead. The helper is handed to ``run_in_executor`` as
    an ARGUMENT (never called directly), so a CALL-node count would be
    0 in BOTH files and the pin would be vacuous. The NAME-load count
    is warmup_pool.py = 1, connection_manager.py = 2 -- exactly the
    ``EXPECTED_SPAWN_SITES`` tuple, which is therefore UNCHANGED.

    SCOPE (deliberately narrow): this counter proves the DISPATCH
    survives. It does NOT detect a re-added direct
    ``resolve_secret_markers`` call (that adds an ``ast.Call`` node and
    leaves these NAME loads untouched) and it does NOT detect a gutted
    ``resolve_spawn_env`` body (the dispatch would still be there). Both
    are covered by the companion guard
    ``test_resolution_lives_only_behind_the_helper`` (clause 2). The two
    counters are complementary -- neither alone is sufficient.
    """
    tree = ast.parse(source)
    count = 0
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id == "resolve_spawn_env"
        ):
            count += 1
    return count


def _count_direct_resolve_calls_in_module(source: str) -> int:
    """AST count of DIRECT ``resolve_secret_markers(...)`` Call nodes.

    The F-11 clause-2 companion to ``_count_resolve_calls_in_module``.
    The dispatch counter proves the spawn sites still reach the shared
    helper; this one proves the resolution was not duplicated back into
    them (the double-resolve regression F-11 forbids) and that the
    helper still owns it. A re-added direct call ADDS a Call node here
    and leaves the helper's NAME loads untouched, so the two counters
    are complementary -- neither alone is sufficient.
    """
    tree = ast.parse(source)
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            getattr(node.func, "id", None)
            or getattr(node.func, "attr", None)
        )
        == "resolve_secret_markers"
    )


def _resolve_calls_in_function(source: str, func_name: str) -> list[ast.Call]:
    """AST: every ``resolve_secret_markers`` Call INSIDE the named
    top-level function (nested defs included via ast.walk)."""
    tree = ast.parse(source)
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == func_name:
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Call):
                        fname = getattr(
                            inner.func, "id", None
                        ) or getattr(inner.func, "attr", None)
                        if fname == "resolve_secret_markers":
                            calls.append(inner)
    return calls


# ─────────────────────────────────────────────────────────────────────
# The pins
# ─────────────────────────────────────────────────────────────────────


class TestDoNotTouchLanesByteClean:
    """The KMS-Lite / BYOK lanes contain ZERO P2 resolver wiring."""

    @pytest.mark.parametrize("rel", DO_NOT_TOUCH_FILES)
    def test_no_p2_resolver_needles(self, rel: str) -> None:
        findings = _find_needles(_read(rel), rel)
        assert not findings, (
            f"do-not-touch lane {rel} references the P2 secrets "
            f"stack — the lanes are unwired by design (p2 "
            f"architecture §0/§5.4): {findings}"
        )

    def test_files_exist_at_expected_paths(self) -> None:
        """Guard against silent path drift — a renamed do-not-touch
        file must update THIS pin deliberately, not vacuously pass."""
        for rel in DO_NOT_TOUCH_FILES + (POOLED_CONFIG_FILE,):
            assert (_REPO_ROOT / rel).is_file(), rel


class TestPooledStdioConfigStaysStored:
    """``build_pooled_stdio_config`` returns STORED config — no P2
    resolution in the config builder (design §1c: spawn-point only;
    resolving here would persist plaintext into DB config)."""

    def test_no_resolve_secret_markers_call_in_builder(self) -> None:
        source = _read(POOLED_CONFIG_FILE)
        calls = _resolve_calls_in_function(source, POOLED_CONFIG_FUNC)
        assert not calls, (
            f"{POOLED_CONFIG_FUNC} must NOT call "
            f"resolve_secret_markers (design §1c — resolution "
            f"belongs at the spawn point); found {len(calls)} call(s)"
        )


class TestSpawnPointsStillResolve:
    """Positive control: the SPAWN POINTS keep their resolution.
    The byte-clean pins above must never be satisfied by someone
    deleting resolution entirely."""

    @pytest.mark.parametrize(
        "rel,expected", list(EXPECTED_SPAWN_SITES)
    )
    def test_spawn_site_resolution_present(
        self, rel: str, expected: int
    ) -> None:
        count = _count_resolve_calls_in_module(_read(rel))
        assert count >= expected, (
            f"{rel} lost its spawn-point resolve_secret_markers "
            f"call(s): expected >= {expected}, found {count} — "
            f"the §1c spawn-point design regressed"
        )

    @pytest.mark.parametrize(
        "rel", [rel for rel, _ in EXPECTED_SPAWN_SITES]
    )
    def test_resolution_lives_only_behind_the_helper(
        self, rel: str
    ) -> None:
        """F-11 clause 2 -- resolution lives ONLY behind the shared helper.

        The dispatch counter above proves the spawn sites still CALL the
        helper; this proves the resolution is not duplicated back into
        them (double-resolve) and that the helper still owns it -- a
        gutted helper body would leave the dispatch intact while
        deleting the resolution, which the class docstring forbids
        ("the byte-clean pins above must never be satisfied by someone
        deleting resolution entirely").
        """
        source = _read(rel)
        direct = _count_direct_resolve_calls_in_module(source)
        if rel == SPAWN_ENV_HELPER_FILE:
            inside = _resolve_calls_in_function(
                source, SPAWN_ENV_HELPER_FUNC
            )
            assert direct == 1 and len(inside) == 1, (
                f"{rel}: the shared helper must own EXACTLY ONE "
                f"resolve_secret_markers call, and it must sit INSIDE "
                f"{SPAWN_ENV_HELPER_FUNC}; found {direct} module-wide / "
                f"{len(inside)} inside {SPAWN_ENV_HELPER_FUNC}"
            )
        else:
            assert direct == 0, (
                f"{rel}: resolution must live ONLY behind "
                f"{SPAWN_ENV_HELPER_FUNC} -- {direct} direct "
                f"resolve_secret_markers call(s) found (double-resolve)"
            )


class TestDetectorSensitivity:
    """Synthetic positive cases — the detectors MUST fire (the
    static-audit false-negative rule: a silent import is exactly
    the regression these pins exist for)."""

    def test_needle_detector_fires_on_violation(self) -> None:
        bad = textwrap.dedent(
            """
            from daemon.services.secret_resolver import (
                resolve_secret_markers,
            )
            x = resolve_secret_markers({}, secret_service=None)
            """
        )
        findings = _find_needles(bad, "synthetic.py")
        assert {f.needle for f in findings} >= {
            "secret_resolver",
            "resolve_secret_markers",
        }

    def test_builder_call_detector_fires_on_violation(self) -> None:
        bad = (
            "def build_pooled_stdio_config(a, b):\n"
            "    env = resolve_secret_markers(a.env, secret_service=None)\n"
            "    return env\n"
        )
        calls = _resolve_calls_in_function(
            bad, "build_pooled_stdio_config"
        )
        assert len(calls) == 1

    def test_builder_call_detector_ignores_other_functions(self) -> None:
        source = (
            "def build_pooled_stdio_config(a, b):\n"
            "    return a.env\n"
            "\n"
            "def _create_pooled_connection(cfg):\n"
            "    return resolve_secret_markers(cfg.env, secret_service=None)\n"
        )
        assert _resolve_calls_in_function(
            source, "build_pooled_stdio_config"
        ) == []

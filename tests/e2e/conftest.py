"""Pytest configuration for E2E tests.

E2E tests use the real MCP SDK. The root conftest injects mocks for ``mcp``
(and other heavy SDKs) so unit tests can import ``daemon.manager`` without
the real packages installed. E2E tests need the real ``mcp``; this conftest
swaps the mocked modules for the real SDK at test setup and restores them
at teardown.

If the real ``mcp`` package is not installed in the environment, E2E tests
skip gracefully (no collection error, no hard failure).

Important: the previous version of this conftest removed mocked ``mcp*``
modules from ``sys.modules`` at ``pytest_configure`` time. That ran during
the collection phase, before later test files were imported, so any unit
test file imported after the e2e directory was discovered failed with
``ModuleNotFoundError: No module named 'mcp'``. This conftest now performs
all module swapping per-test instead.
"""

import importlib
import importlib.util
import sys
from types import ModuleType

import pytest


# Names that belong to the MCP SDK. Keep this in sync with the mock list in
# tests/conftest.py so we swap every related sub-module at once.
_MCP_MODULE_NAMES = (
    "mcp",
    "mcp.client",
    "mcp.client.sse",
    "mcp.client.streamable_http",
    "mcp.client.stdio",
    "mcp.client.stdio.context_manager",
    "mcp.server",
    "mcp.server.stdio",
    "mcp.server.fastmcp",
    "mcp.types",
    "mcp.shared",
    "mcp.shared.exceptions",
    # The langchain adapter is the primary way daemon code talks to mcp.
    "langchain_mcp_adapters",
    "langchain_mcp_adapters.tools",
)


# Test files in tests/e2e/ that need the real ``psycopg`` package at runtime.
# Used to scope the psycopg skip-stub so we only skip the tests that need it
# (other e2e tests like test_mcp_* don't depend on psycopg).
_PSYCOPG_REQUIRED_TESTS = ("test_migration_e2e",)

# Sentinel attribute used to recognise the psycopg stub installed below.
_PSYCOPG_STUB_ATTR = "_ensemble_e2e_psycopg_stub"


def _real_mcp_available() -> bool:
    """Return True if the real ``mcp`` package is importable."""
    try:
        return importlib.util.find_spec("mcp") is not None
    except (ValueError, ModuleNotFoundError):
        # A mock module (with __spec__=None) may be sitting in sys.modules
        # shadowing the real package. Temporarily remove it and retry.
        saved = sys.modules.pop("mcp", None)
        try:
            return importlib.util.find_spec("mcp") is not None
        finally:
            if saved is not None:
                sys.modules["mcp"] = saved


def _real_psycopg_available() -> bool:
    """Return True if the real ``psycopg`` package is importable."""
    return importlib.util.find_spec("psycopg") is not None


def _install_psycopg_collect_stub():
    """Install a minimal ``psycopg`` stub so collection succeeds.

    ``tests/e2e/test_migration_e2e.py`` does ``import psycopg`` at module top
    level. When the real package isn't installed in the environment, that
    import raises ``ModuleNotFoundError`` and pytest reports a collection
    error before any skip logic can run.

    To make the collection error go away (and let the autouse fixture below
    skip the test gracefully), we install an empty stub module in
    ``sys.modules`` marked with ``_PSYCOPG_STUB_ATTR``. The stub has no
    attributes — that's fine because the migration test is skipped at runtime
    whenever the stub is present, so it never tries to call
    ``psycopg.connect(...)`` against the stub.
    """
    if _real_psycopg_available():
        return
    existing = sys.modules.get("psycopg")
    if existing is not None and getattr(existing, _PSYCOPG_STUB_ATTR, False):
        return  # already stubbed
    stub = ModuleType("psycopg")
    setattr(stub, _PSYCOPG_STUB_ATTR, True)
    sys.modules["psycopg"] = stub


# Run the stub installer at conftest import time so any test file collected
# after this conftest loads can do ``import psycopg`` without raising.
_install_psycopg_collect_stub()


@pytest.fixture(autouse=True)
def _swap_real_mcp_for_e2e(request):
    """Replace mocked ``mcp.*`` modules with the real SDK for e2e tests.

    Skips the test gracefully when the real ``mcp`` package is not installed.
    Restores the mocks after the test so other tests and the rest of the
    ``sys.modules`` state are unaffected. The swap happens at test setup, not
    at collection time, so test files collected after this conftest runs can
    still import ``mcp`` via the mock injected by the root conftest.
    """
    if not _real_mcp_available():
        pytest.skip("Real `mcp` SDK is not installed; skipping E2E test")

    # Snapshot the current (mocked) modules so we can restore them after.
    saved = {name: sys.modules.get(name) for name in _MCP_MODULE_NAMES}

    # Drop the mocked entries from sys.modules so the upcoming imports fetch
    # the real packages. We only touch the MCP-related names, so other
    # state in sys.modules is preserved.
    for name in _MCP_MODULE_NAMES:
        sys.modules.pop(name, None)

    # Force a fresh import of the top-level ``mcp`` package. Sub-modules are
    # imported lazily by the real package on first attribute access.
    try:
        importlib.import_module("mcp")
    except Exception as exc:  # pragma: no cover - defensive
        # Restore mocks before propagating the skip.
        for name in _MCP_MODULE_NAMES:
            sys.modules.pop(name, None)
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
        pytest.skip(f"Real `mcp` SDK is not importable: {exc}")

    try:
        yield
    finally:
        # Drop any cached real modules and re-install the mocks so the rest
        # of the session (and any later test collection) sees the mocks.
        for name in _MCP_MODULE_NAMES:
            sys.modules.pop(name, None)
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod


@pytest.fixture(autouse=True)
def _skip_when_psycopg_stub_present(request):
    """Skip tests that need the real ``psycopg`` package when it's missing.

    The stub installed by ``_install_psycopg_collect_stub`` keeps the
    collection phase clean. This fixture detects the stub at test setup and
    calls ``pytest.skip`` for any test file listed in
    ``_PSYCOPG_REQUIRED_TESTS``, so users see a clear "skipped — real
    ``psycopg`` not installed" reason rather than a confusing ``AttributeError``
    from the empty stub. Other e2e tests (which don't depend on ``psycopg``)
    are unaffected.
    """
    fspath = str(request.fspath)
    if not any(name in fspath for name in _PSYCOPG_REQUIRED_TESTS):
        # Not a psycopg-dependent test; nothing to do.
        yield
        return

    psycopg_mod = sys.modules.get("psycopg")
    if psycopg_mod is not None and getattr(psycopg_mod, _PSYCOPG_STUB_ATTR, False):
        pytest.skip("Real `psycopg` package is not installed; skipping E2E test")
    yield

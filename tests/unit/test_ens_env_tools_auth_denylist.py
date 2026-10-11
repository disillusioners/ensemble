"""``ens_env_read`` auth deny-list — oauth2-auth P4 (T4.2 / C2 (a), plan
§4 row 2) — G4.6 evidence file.

The deny-list MECHANISM landed in the P3 security-fix cycle
(``daemon/tools/ens_env_tools.py`` ``_AUTH_ENV_DENY_PREFIX`` +
``TestEnsembleAuthDenyList`` in ``tests/unit/tools/test_ens_env_tools.py``
— kept green, cross-referenced as companion evidence). THIS file pins
the plan §4-named legs verbatim:

* ``ens_env_read(keys=["ENSEMBLE_AUTH_BOOTSTRAP_TOKEN"])`` → explicit
  structured denial (mirroring the POSTGRES-exclusion posture);
* ``ens_env_read(keys=[])`` (default path) unchanged;
* ``ens_env_read(keys=["PATH"])`` (non-auth env var) still works.
"""

from __future__ import annotations

import json

import pytest

from daemon.tools.ens_env_tools import (
    _AUTH_ENV_DENY_PREFIX,
    create_ens_env_tools,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def tool_by_name():
    tools = create_ens_env_tools(
        manager=None, current_instance_id="test-instance"
    )
    return {t.name: t for t in tools}["ens_env_read"]


class TestPlanNamedLegs:
    async def test_bootstrap_token_denied_explicitly(self, tool_by_name, monkeypatch):
        """THE leg: an explicitly-requested ENSEMBLE_AUTH_BOOTSTRAP_TOKEN
        returns the structured denial — the value is never read, never
        surfaced in any shape."""
        monkeypatch.setenv("ENSEMBLE_AUTH_BOOTSTRAP_TOKEN", "boot-secret-value")
        result_json = await tool_by_name.ainvoke(
            {"keys": ["ENSEMBLE_AUTH_BOOTSTRAP_TOKEN"]}
        )
        decoded = json.loads(result_json)
        assert "error" in decoded
        assert "ENSEMBLE_AUTH_BOOTSTRAP_TOKEN" in decoded["error"]
        assert "boot-secret-value" not in result_json
        assert decoded.get("ENSEMBLE_AUTH_BOOTSTRAP_TOKEN") is None

    async def test_default_path_keys_empty_unchanged(self, tool_by_name, monkeypatch):
        """``keys=[]`` (the explicit-default-path shape) is UNCHANGED by
        the deny-list — a non-auth default key still surfaces, and the
        surfaced set carries no ENSEMBLE_AUTH_* key."""
        monkeypatch.setenv("OPENAI_BASE_URL", "https://base.example/v1")
        result_json = await tool_by_name.ainvoke({"keys": []})
        decoded = json.loads(result_json)
        assert "error" not in decoded
        for key in decoded:
            assert not key.upper().startswith(_AUTH_ENV_DENY_PREFIX)

    async def test_non_auth_env_var_still_works(self, tool_by_name, monkeypatch):
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        result_json = await tool_by_name.ainvoke({"keys": ["PATH"]})
        decoded = json.loads(result_json)
        assert "error" not in decoded
        assert decoded.get("PATH") == "/usr/bin:/bin"

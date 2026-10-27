"""B2-narrowed: readiness composite + pending-count are MCP-independent (regression pin)."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_readiness_components_have_no_mcp_reference():
    src = (REPO / "daemon/services/readiness.py").read_text()
    for comp in ("database", "queue_freshness", "services", "checkpoint_saver"):
        assert comp in src
    code_lines = [ln.split("#", 1)[0] for ln in src.splitlines()]
    code = "\n".join(code_lines).lower()
    assert "mcp" not in code, "readiness.py must not reference MCP"
    pc = (REPO / "daemon/routers/prompts.py").read_text()
    assert "prompts" in pc.lower()


def test_api_wiring_no_mcp_in_probe_construction():
    src = (REPO / "daemon/api.py").read_text()
    offending = [ln for ln in src.splitlines()
                 if "make_" in ln and "probe" in ln.lower() and "mcp" in ln.lower()
                 and not ln.strip().startswith("#")]
    assert not offending, offending

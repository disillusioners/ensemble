"""B2-narrowed: readiness composite + pending-count are MCP-independent (regression pin).

THE INTENT (unchanged): the readiness VERDICT must never depend on the
MCP layer. A degraded / quarantined MCP server must NOT flip a
readiness component, must NOT add a reason and must NOT make ``/readyz``
503 — the daemon stays up and says so in the ADVISORY ``detail.mcp``
map instead.

C4 (soak-gate-mcp-warmup) added that advisory map, so the original
LETTER of this pin (``"mcp" not in readiness.py``) can no longer hold.
The pin is AMENDED, not weakened: it now asserts the INTENT directly —
the MCP map is carried into ``detail`` and NEVER into
``components`` / ``reasons`` / ``ready`` — and it keeps the
"no MCP in the probe construction" census on ``daemon/api.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def test_readiness_components_have_no_mcp_reference():
    """The four readiness components exist and the MCP map is ADVISORY."""
    src = (REPO / "daemon/services/readiness.py").read_text()
    for comp in ("database", "queue_freshness", "services", "checkpoint_saver"):
        assert comp in src
    code_lines = [ln.split("#", 1)[0] for ln in src.splitlines()]
    code = "\n".join(code_lines).lower()
    # The MCP map may be CARRIED (C4 — decisions §3h H4) but it must
    # never become a COMPONENT: the components are exactly the four
    # probe-driven ones.
    assert '"checkpoint_saver"' in code
    pc = (REPO / "daemon/routers/prompts.py").read_text()
    assert "prompts" in pc.lower()


def test_the_mcp_map_is_advisory_only():
    """A quarantined MCP server does NOT change the verdict.

    This is the STRONGER form of the B2 narrowing: the source census
    above cannot prove the verdict is MCP-independent, this can.
    """
    from daemon.services.readiness import compute_readiness_composite

    composite = compute_readiness_composite(
        database_ok=True,
        queue_fresh_ok=True,
        services_ok=True,
        queue_max_age_seconds=None,
        mcp_servers={
            "context7": {
                "state": "quarantined",
                "reason": "dns_nxdomain",
                "since": "2026-10-27T00:00:00+00:00",
                "init_attempts": 3,
                "last_error_class": "dns_nxdomain",
                "breaker_state": "open",
                "recoverable": True,
            }
        },
    )
    assert composite.ready is True, (
        "a quarantined MCP server must NOT degrade the readiness verdict"
    )
    assert composite.reasons == []
    payload = composite.to_payload()
    assert payload["status"] == "ready"
    assert payload["components"] == {
        "database": True,
        "queue_freshness": True,
        "services": True,
        "checkpoint_saver": True,
    }
    # The map IS published — as an ADVISORY detail key.
    assert payload["detail"]["mcp"]["context7"]["state"] == "quarantined"
    assert payload["detail"]["mcp"]["context7"]["recoverable"] is True


def test_the_mcp_map_never_enters_the_components_or_reasons():
    """A source-level census: the verdict path never reads the map."""
    src = (REPO / "daemon/services/readiness.py").read_text()
    # The composite's verdict is assembled from the four booleans; the
    # MCP map is only ever copied into the payload's ``detail``.
    ready_block = src.split("def ready", 1)[-1].split("def ", 1)[0]
    assert "mcp" not in ready_block.lower(), (
        "the readiness verdict must never consult the MCP map"
    )
    components_block = src.split('"components": {', 1)[-1].split("}", 1)[0]
    assert "mcp" not in components_block.lower(), (
        "the components block must never carry an MCP entry"
    )


def test_api_wiring_no_mcp_in_probe_construction():
    src = (REPO / "daemon/api.py").read_text()
    offending = [ln for ln in src.splitlines()
                 if "make_" in ln and "probe" in ln.lower() and "mcp" in ln.lower()
                 and not ln.strip().startswith("#")]
    assert not offending, offending


async def test_the_buffered_transition_count_reaches_the_payload_through_the_orchestrator():
    """F2: the count must survive the ORCHESTRATOR (the live path).

    The orchestrator accepted ``mcp_buffered_transitions`` and never
    forwarded it to the composite builder — so
    ``detail.mcp_buffered_transitions`` was DEAD in production while
    the constructor-level coverage looked green (the "true in unit,
    dead in production" family). This pin drives
    ``refresh_readiness_composite`` — the LIVE path — not the
    constructor.
    """
    from daemon.services.readiness import refresh_readiness_composite

    composite = await refresh_readiness_composite(
        db_probe=lambda: True,
        queue_probe=lambda: None,
        services_ok=True,
        queue_freshness_threshold_seconds=300.0,
        mcp_servers={"context7": {"state": "ok"}},
        mcp_buffered_transitions=7,
    )
    payload = composite.to_payload()
    assert payload["detail"]["mcp_buffered_transitions"] == 7, (
        "the buffered-transition count must reach the payload through the "
        "orchestrator — the constructor-level coverage bypasses the very "
        "drop that made the key dead in production"
    )
    # The advisory map still rides along on the same path.
    assert payload["detail"]["mcp"]["context7"]["state"] == "ok"


async def test_a_grace_observation_exception_does_not_abort_the_tick():
    """F5: an observe exception must not abort the readiness tick.

    A bug in the NEW grace code would otherwise break the EXISTING
    readiness reporting — the most safety-critical surface in this
    commission — and leave the composite STALE. The guard fails toward
    the pre-grace behaviour (no grace for that observation) and NEVER
    toward suppressing readiness: the component readings and the reasons
    still ride the composite.
    """
    from daemon.services.readiness import refresh_readiness_composite

    class _ExplodingTracker:
        def observe(self, **kwargs):
            raise RuntimeError("the grace tracker exploded")

    composite = await refresh_readiness_composite(
        db_probe=lambda: True,
        queue_probe=lambda: None,
        services_ok=True,
        queue_freshness_threshold_seconds=300.0,
        grace_tracker=_ExplodingTracker(),
    )
    payload = composite.to_payload()
    assert payload["status"] == "ready", (
        "the tick must complete with the real component readings"
    )
    assert payload["components"]["database"] is True
    assert payload["detail"]["reasons"] == []
    # No grace for this observation (the pre-grace behaviour).
    assert payload["detail"]["grace"]["grace_active"] is False

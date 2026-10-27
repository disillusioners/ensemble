"""C4 / H7 — the ``/readyz`` reasons reader (soak-gate-mcp-warmup).

``daemon/tools/upgrade_tools.py`` used to read a TOP-LEVEL ``reasons``
key from the ``/readyz`` payload while the payload NESTS ``reasons``
under ``detail`` — so it ALWAYS rendered ``reasons=[]``, even for a
daemon that was reporting reasons. That directly contradicts the
commission's thesis ("a degraded daemon must stay visible").

This file pins the fixed reader:

* the real ``detail.reasons`` is rendered for a degraded payload;
* the top-level ``reasons`` is the FALLBACK for an older daemon;
* a malformed payload degrades to ``[]`` and never raises.
"""

from __future__ import annotations

from daemon.tools.upgrade_tools import _readyz_reasons


def test_the_nested_detail_reasons_are_read():
    payload = {
        "status": "degraded",
        "detail": {
            "reasons": [
                "database: SELECT 1 probe failed or timed out",
                "queue_freshness: newest RUNNING-task heartbeat age 900.0 exceeds ...",
            ],
        },
    }
    assert _readyz_reasons(payload) == payload["detail"]["reasons"]


def test_the_rendered_line_carries_the_real_reasons():
    """The rendered operator line must NOT be an empty list."""
    payload = {
        "status": "degraded",
        "detail": {"reasons": ["checkpoint_saver: sentinel checkpoint read failed"]},
    }
    rendered = f"reasons={_readyz_reasons(payload)}"
    assert "checkpoint_saver: sentinel checkpoint read failed" in rendered
    assert "reasons=[]" not in rendered


def test_the_top_level_fallback_is_used_for_an_older_daemon():
    payload = {"status": "degraded", "reasons": ["legacy reason"]}
    assert _readyz_reasons(payload) == ["legacy reason"]


def test_the_nested_shape_wins_over_the_top_level_one():
    payload = {
        "status": "degraded",
        "reasons": ["stale top-level"],
        "detail": {"reasons": ["nested wins"]},
    }
    assert _readyz_reasons(payload) == ["nested wins"]


def test_an_absent_reasons_key_renders_empty():
    assert _readyz_reasons({"status": "ready"}) == []
    assert _readyz_reasons({"status": "ready", "detail": {}}) == []


def test_a_malformed_payload_never_raises():
    assert _readyz_reasons({}) == []
    assert _readyz_reasons({"detail": "not-a-dict"}) == []
    assert _readyz_reasons({"detail": {"reasons": "not-a-list"}}) == []
    assert _readyz_reasons({"reasons": "not-a-list"}) == []
    assert _readyz_reasons("not-a-dict") == []


def test_the_new_detail_keys_do_not_disturb_the_reader():
    """The grace / leak / mcp blocks ride alongside ``reasons``."""
    payload = {
        "status": "degraded",
        "detail": {
            "reasons": ["grace-held"],
            "grace": {"grace_active": True},
            "leak_signal": {"detected": False},
            "mcp": {"context7": {"state": "quarantined"}},
        },
    }
    assert _readyz_reasons(payload) == ["grace-held"]

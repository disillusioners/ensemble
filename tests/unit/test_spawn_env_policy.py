"""Unit tests for spawn_env_policy (component B, pin positive-count gate).

Mutation-discipline positive shapes: a counter-mutation that removes a strip
entry (or changes the count) must redden at least one node here.
"""

import os

from daemon.services.spawn_env_policy import (
    SPAWN_BOUNDARY_STRIP_ENV,
    scrub_spawn_env,
    apply_spawn_boundary,
)


def test_strip_env_exact_ten_names():
    # Positive-count gate: bind 4 + DB 5 + attestation 1.
    assert len(SPAWN_BOUNDARY_STRIP_ENV) == 10
    expected = {
        "PORT", "DAEMON_PORT", "HOST", "LIMITS_METRICS_LISTENER_PORT",
        "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB",
        "POSTGRES_USER", "POSTGRES_PASSWORD",
        "ENSEMBLE_UPGRADE_LIVE",
    }
    assert set(SPAWN_BOUNDARY_STRIP_ENV) == expected


def test_scrub_removes_all_strip_names():
    base = {name: "POISON" for name in SPAWN_BOUNDARY_STRIP_ENV}
    base["KEEP_ME"] = "ok"
    out = scrub_spawn_env(base)
    assert "KEEP_ME" in out and out["KEEP_ME"] == "ok"
    for name in SPAWN_BOUNDARY_STRIP_ENV:
        assert name not in out


def test_scrub_does_not_mutate_input():
    base = {"PORT": "9797", "KEEP": "x"}
    before = dict(base)
    scrub_spawn_env(base)
    assert base == before


def test_apply_boundary_strips_inherit_and_overlay_wins():
    poisoned = {name: "POISON" for name in SPAWN_BOUNDARY_STRIP_ENV}
    poisoned["UNRELATED"] = "stay"
    out = apply_spawn_boundary(poisoned, overlay={"PORT": "12345"})
    assert out["PORT"] == "12345"
    assert out["UNRELATED"] == "stay"
    for name in SPAWN_BOUNDARY_STRIP_ENV:
        if name != "PORT":
            assert name not in out


def test_apply_boundary_deliberate_wins_last():
    poisoned = {"PORT": "9797"}
    out = apply_spawn_boundary(poisoned, overlay={"PORT": "1"}, deliberate={"PORT": "2"})
    assert out["PORT"] == "2"


def test_apply_boundary_none_overlay_is_noop_for_survivors():
    base = {"KEEP": "v"}
    out = apply_spawn_boundary(base, None, None)
    assert out == {"KEEP": "v"}


def test_apply_boundary_empty_base():
    assert apply_spawn_boundary({}) == {}

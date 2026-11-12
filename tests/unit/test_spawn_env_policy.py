"""Unit tests for spawn_env_policy (component B, fix-round 2 / N4).

Positive-count gate: bind 4 + DB exact 7 + attestation 1 = 12 EXACT names,
plus the ``PG`` prefix family with the ``PG_TEST_*`` exemption.
"""

from daemon.services.spawn_env_policy import (
    SPAWN_BOUNDARY_STRIP_ENV,
    SPAWN_BOUNDARY_STRIP_PREFIXES,
    SPAWN_BOUNDARY_STRIP_PREFIX_EXEMPTIONS,
    apply_spawn_boundary,
    is_stripped_name,
    scrub_spawn_env,
)


def test_strip_env_exact_twelve_names():
    assert len(SPAWN_BOUNDARY_STRIP_ENV) == 12
    expected = {
        "PORT", "DAEMON_PORT", "HOST", "LIMITS_METRICS_LISTENER_PORT",
        "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB",
        "POSTGRES_USER", "POSTGRES_PASSWORD",
        "ENSEMBLE_UPGRADE_LIVE",
        "ENSEMBLE_CONFIG", "DATABASE_URL",
    }
    assert set(SPAWN_BOUNDARY_STRIP_ENV) == expected


def test_pg_prefix_family_stripped():
    assert SPAWN_BOUNDARY_STRIP_PREFIXES == ("PG",)
    for name in ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD",
                 "PGPASSFILE", "PGSSLMODE", "PGSERVICE", "PGSERVICEFILE"):
        assert is_stripped_name(name) is True, name
        assert name not in scrub_spawn_env({name: "x"})


def test_pg_test_exemption_survives():
    assert SPAWN_BOUNDARY_STRIP_PREFIX_EXEMPTIONS == ("PG_TEST_",)
    assert is_stripped_name("PG_TEST_HOST") is False
    out = scrub_spawn_env({"PG_TEST_HOST": "127.0.0.1", "PGHOST": "live"})
    assert out == {"PG_TEST_HOST": "127.0.0.1"}


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

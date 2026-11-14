"""Unit pins for the TIER-1 PROD-DSN FENCE (rogue-containment p2, component B2).

Occ#9 closer (incident wi-2454ede3): an env-less rogue deriving ``sandbox``
identity that RESOLVES an ``ensemble_prod`` DSN is refused BY CONSTRUCTION —
pre-TCP, exit 78, marker line. Authoritative plan:
``.agents/shared/planning/rogue-containment-p2/component-B-tier1-prod-dsn-fence.md``
(§4 mechanics, §7 pin skeleton); mutation matrix: Family BF in
``component-E2-pins-verification-measurement.md`` §E2 (BF-M1..M9).

Pin map (B2 P-number | E2 BF-row | named counter-mutant, one per docstring):

    P1  | BF-1 | BF-M1  tier-1 prod-name refusal, every non-live identity
    P2  | BF-1 control | sloppy-pattern mutants (startswith / FORBIDDEN set)
    P3  | BF-3 | BF-M3  env-wins closure (predicate reads the POST-resolution name)
    P4  | BF-2 | BF-M2  sanctioned live carve-out proceeds (explicit + derived)
    P4b | BF-9 | BF-M9  THE real live shape (file-tier ``ensemble`` + ambient prod)
    P5  | BF-7 None-polarity | None⇒refuse / None⇒silent-proceed
    P6  | BF-6 | BF-M6  resolver raise ⇒ fail-closed 78 UNVERIFIABLE
    P7  | BF-4 | BF-M4  ENSEMBLE_DB_AMBIENT_ALLOW never exempts the prod name
    grid | matrix control | {live,dev,sandbox,None} × non-prod names ⇒ proceed

(BF-7 predicate-string pin, BF-8 tier-1 side-by-side, P8 ordering, P9 CLI
closure and P10 engine-sentinel faces live in
``tests/unit/test_db_resolution_guard.py`` — the house call-site/pin file
this family extends, 10 → 18 tests.)

Offline seams ONLY — no real PG connection, no real bind, no live mutation:
  * identity monkeypatched at ``daemon.tools.upgrade_tools._self_env_marker``
    (+ ``_self_env_source`` for the resolution label) — the guard imports
    them FUNCTION-LOCALLY ⇒ call-time lookup ⇒ the module-attr patch is
    authoritative (patch target pinned in every docstring);
  * DSN pins on SCRATCH names / tmp json — NEVER a real ``ensemble_prod``
    connection (the refusal fires before any engine construction);
  * marker asserts are SUBSTRING asserts — never a bare exit-78 (the
    auth-config preflight has its own 78 on the ``main()`` entry order).
"""

import pytest

from daemon.ensemble_config import (
    EnsembleConfig,
    PostgresConfig,
    assert_db_resolution_sanctioned,
    assert_prod_dsn_sanctioned,
    resolved_postgres_db_name,
)


def _patch_identity(monkeypatch, marker, source="auto"):
    """Patch boot identity at the resolver module attrs (call-time
    lookup via the guard's function-local imports)."""
    monkeypatch.setattr(
        "daemon.tools.upgrade_tools._self_env_marker", lambda: marker
    )
    monkeypatch.setattr(
        "daemon.tools.upgrade_tools._self_env_source", lambda: source
    )


def _postgres_config(db):
    return EnsembleConfig(
        database="postgres",
        postgres=PostgresConfig(
            db=db, host="127.0.0.1", port=5432, user="u", password="p"
        ),
    )


@pytest.mark.parametrize("identity", ["sandbox", "dev", "demo"])
def test_p1_tier1_prod_name_refused_for_non_live_identities(
    monkeypatch, caplog, identity
):
    """P1 | E2 BF-1 | mutant BF-M1: delete/invert the prod-name branch ⇒
    guard returns ⇒ green-where-should-red; invert the identity
    comparison ⇒ false pass. Patch target:
    ``daemon.tools.upgrade_tools._self_env_marker``. A tier-1 postgres
    config whose resolved db name is ensemble_prod refuses with exit 78
    + the marker (tier= / db= / self_env= / resolution= fields) for
    EVERY positive non-live identity — the wi-2454ede3 rogue shape
    (derived ``sandbox``) is refused by construction."""
    _patch_identity(monkeypatch, identity, "explicit")
    monkeypatch.delenv("POSTGRES_DB", raising=False)
    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    with caplog.at_level("ERROR", logger="daemon.ensemble_config"):
        with pytest.raises(SystemExit) as exc:
            assert_prod_dsn_sanctioned(_postgres_config("ensemble_prod"))
    assert exc.value.code == 78
    assert "tier=resolved-dsn-targets-prod" in caplog.text
    assert "db=ensemble_prod" in caplog.text
    assert f"self_env={identity}" in caplog.text
    assert "resolution=explicit" in caplog.text


@pytest.mark.parametrize(
    "db",
    [
        "ensemble_prod_test",
        "ensemble_dev",
        "ens_e2e_scrub_probe",
        "ensemble",
    ],
)
@pytest.mark.parametrize("identity", ["live", "dev", "sandbox", None])
def test_p2_exact_match_discriminator_allows_non_prod_names(
    monkeypatch, db, identity
):
    """P2 | E2 BF-1 control column + addendum §1.3 grid control | kills
    the SLOPPY-PATTERN mutants: ``startswith("ensemble_prod")`` or
    ``db in FORBIDDEN_DB_NAMES`` — the harness forbidden set — REDs here
    (``ensemble_prod_test``/``ensemble_dev`` are LEGITIMATE non-prod
    names; B2 §4.4). Exact normalized ``== "ensemble_prod"`` ONLY: these
    names pass for EVERY identity (sanctioned dev/test lanes never
    fenced). Patch target: ``daemon.tools.upgrade_tools._self_env_marker``."""
    _patch_identity(monkeypatch, identity)
    monkeypatch.delenv("POSTGRES_DB", raising=False)
    assert resolved_postgres_db_name(_postgres_config(db)) == db
    assert_prod_dsn_sanctioned(_postgres_config(db))  # must not raise


def test_p3_env_wins_prod_override_refused(monkeypatch, caplog):
    """P3 | E2 BF-3 | mutant BF-M3: the predicate reads ONLY the file
    tier (``config.postgres.db``) ⇒ sees the safe name ⇒ no refuse ⇒
    RED. ENV-WINS closure (B2 §4.5): a tier-1 json with a SAFE db name
    plus ambient ``POSTGRES_DB=ensemble_prod`` resolves to the PROD name
    (the same env-wins rule ``get_postgres_url`` applies) and REFUSES
    for a non-live identity. Control leg: the same shape with ambient
    ``ensemble_dev`` does NOT fire (credential rotation preserved).
    Patch target: ``daemon.tools.upgrade_tools._self_env_marker``."""
    _patch_identity(monkeypatch, "sandbox", "auto")
    config = _postgres_config("ensemble")  # safe file-tier name
    monkeypatch.setenv("POSTGRES_HOST", "127.0.0.1")
    monkeypatch.setenv("POSTGRES_DB", "ensemble_prod")
    assert resolved_postgres_db_name(config) == "ensemble_prod"
    with caplog.at_level("ERROR", logger="daemon.ensemble_config"):
        with pytest.raises(SystemExit) as exc:
            assert_prod_dsn_sanctioned(config)
    assert exc.value.code == 78
    # Control: non-prod env-wins is explicitly preserved.
    monkeypatch.setenv("POSTGRES_DB", "ensemble_dev")
    assert_prod_dsn_sanctioned(config)  # must not raise
    assert resolved_postgres_db_name(config) == "ensemble_dev"


@pytest.mark.parametrize(
    ("source", "expected_label"),
    [("explicit", "explicit"), ("auto", "derived")],
)
def test_p4_sanctioned_live_identity_proceeds_with_info(
    monkeypatch, caplog, source, expected_label
):
    """P4 | E2 BF-2 | mutant BF-M2: invert/remove the carve-out check ⇒
    the live-shape input refuses ⇒ RED (the cardinal sin this design
    must never commit — a false live refusal downs the live boot and
    every promote leg). Prod DSN + identity ``live`` proceeds with an
    INFO line naming the resolution source (B2 §4.3 row 1): explicit
    staged marker ⇒ label ``explicit``; auto-derive ⇒ label ``derived``
    (the marker's resolution vocabulary is ``<explicit|derived>``).
    Patch targets: ``daemon.tools.upgrade_tools._self_env_marker`` +
    ``._self_env_source``."""
    _patch_identity(monkeypatch, "live", source)
    monkeypatch.delenv("POSTGRES_DB", raising=False)
    with caplog.at_level("INFO", logger="daemon.ensemble_config"):
        assert_prod_dsn_sanctioned(_postgres_config("ensemble_prod"))
    assert "DB RESOLUTION SANCTIONED (rogue-containment)" in caplog.text
    assert f"resolution={expected_label}" in caplog.text


def test_p4b_real_live_shape_env_wins_sanctioned(monkeypatch, caplog):
    """P4b | E2 BF-9 | mutant BF-M9: drop the carve-out on the live
    identity ⇒ the live shape refuses ⇒ RED (the BF-2 catastrophe).
    THE REAL LIVE BOOT SHAPE (addendum §1.1/§1.3, B2 §7-P4b): tier-1
    json ``db="ensemble"`` (the live file-tier name) + ambient
    ``POSTGRES_DB=ensemble_prod`` (env-wins) + identity ``live`` ⇒
    PROCEEDS. Load-bearing for every promote leg — the predicate fires
    on EVERY live boot (env-wins supplies the prod name), so a false
    refusal here downs live at promote. Patch targets:
    ``daemon.tools.upgrade_tools._self_env_marker`` +
    ``._self_env_source``."""
    _patch_identity(monkeypatch, "live", "auto")
    config = _postgres_config("ensemble")  # the live file-tier name
    monkeypatch.setenv("POSTGRES_HOST", "127.0.0.1")
    monkeypatch.setenv("POSTGRES_DB", "ensemble_prod")
    assert resolved_postgres_db_name(config) == "ensemble_prod"
    with caplog.at_level("INFO", logger="daemon.ensemble_config"):
        assert_prod_dsn_sanctioned(config)  # must not raise
    assert "DB RESOLUTION SANCTIONED (rogue-containment)" in caplog.text
    assert "resolution=derived" in caplog.text


def test_p5_unresolved_identity_degrades_loudly_and_proceeds(
    monkeypatch, caplog
):
    """P5 | E2 BF-7 (None polarity, BOTH directions) | mutants: None ⇒
    refuse (RED as a false refusal — the BF-2 live-down at promote) and
    None ⇒ silent proceed (RED: no WARNING). RATIFIED V2 (addendum
    §0-V2): the live boot carries NO explicit ``ENSEMBLE_SELF_ENV`` and
    its fence-pass depends entirely on auto-derive ⇒ refuse-on-None
    would convert any derive-regression into a live-down; None+prod is
    genuinely degraded evidence, so the boot proceeds with a LOUD
    WARNING carrying the ``_self_env_source`` vocabulary. Patch targets:
    ``daemon.tools.upgrade_tools._self_env_marker`` +
    ``._self_env_source``."""
    _patch_identity(monkeypatch, None, "auto-unresolved")
    monkeypatch.delenv("POSTGRES_DB", raising=False)
    with caplog.at_level("WARNING", logger="daemon.ensemble_config"):
        assert_prod_dsn_sanctioned(
            _postgres_config("ensemble_prod")
        )  # proceeds
    assert "DB RESOLUTION DEGRADED (rogue-containment)" in caplog.text
    assert "self_env_source=auto-unresolved" in caplog.text


def test_p6_identity_resolver_raise_fails_closed_exit_78(monkeypatch, caplog):
    """P6 | E2 BF-6 | mutant BF-M6: wrap the evaluation in
    try/except-return (fail-open) ⇒ internal error proceeds ⇒ RED.
    Guard-internal RAISE is NOT a resolution of None (B2 §4.3 row 4):
    it fails CLOSED — exit 78 + UNVERIFIABLE — mirroring the shipped
    tier-2 guard polarity (ensemble_config.py fail-closed shape). Patch
    target: ``daemon.tools.upgrade_tools._self_env_marker`` (raising)."""

    def _boom():
        raise RuntimeError("identity resolver exploded")

    monkeypatch.setattr(
        "daemon.tools.upgrade_tools._self_env_marker", _boom
    )
    monkeypatch.delenv("POSTGRES_DB", raising=False)
    with caplog.at_level("ERROR", logger="daemon.ensemble_config"):
        with pytest.raises(SystemExit) as exc:
            assert_prod_dsn_sanctioned(_postgres_config("ensemble_prod"))
    assert exc.value.code == 78
    assert "UNVERIFIABLE" in caplog.text


def test_p7_ambient_allow_does_not_exempt_prod_name(tmp_path, monkeypatch):
    """P7 | E2 BF-4 | mutant BF-M4: ``ALLOW=1`` short-circuits the prod
    check ⇒ RED. Composition pin (B2 §4.6, independent-gating):
    ``ENSEMBLE_DB_AMBIENT_ALLOW=1`` grants tier-2 (ambient-first-boot)
    sanction ONLY and is permanently removed from the prod-name decision
    path — a json-ABSENT boot with ambient prod POSTGRES_* passes the
    tier-2 pre-guard (unchanged behavior, pinned), then the LOADED
    config (postgres; env-wins resolved name ensemble_prod) is refused
    for a non-live identity. Patch target:
    ``daemon.tools.upgrade_tools._self_env_marker``."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("POSTGRES_HOST", "127.0.0.1")
    monkeypatch.setenv("POSTGRES_DB", "ensemble_prod")
    monkeypatch.setenv("ENSEMBLE_DB_AMBIENT_ALLOW", "1")
    _patch_identity(monkeypatch, "sandbox", "auto")
    # tier-2 pre-guard: ALLOW=1 sanctions the ambient first boot (unchanged)
    assert_db_resolution_sanctioned(data_dir)  # must not raise
    ensemble_config = EnsembleConfig.load_or_create(data_dir)
    assert ensemble_config.is_postgres
    assert resolved_postgres_db_name(ensemble_config) == "ensemble_prod"
    # prod-DSN gate: ALLOW is NOT consulted — the refusal stands
    with pytest.raises(SystemExit) as exc:
        assert_prod_dsn_sanctioned(ensemble_config)
    assert exc.value.code == 78

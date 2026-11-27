"""Hermetic unit pins for the promote-time backup-artifact validation leg
(promote-db-preflight-validation, T6 #1 — phase1-plan.md §TEST MATRIX pins
(a), (b), (c), (d), (f), (F13) + the kubectl string ban).

NORMATIVE SOURCE: ``.agents/shared/planning/promote-db-preflight-validation/``
— ``decisions.md`` §PREDICATE LEDGER (the canonical strings; every assertion
here compares against them literally) and ``phase1-plan.md`` §TEST MATRIX
(the pin → test-file → test-name table, which is authoritative for placement).

WHAT IS REAL HERE. Every node drives the REAL
:func:`daemon.tools.upgrade_db_preflight.evaluate_db_preflight` against a REAL
SQLAlchemy engine over SQLite with a ``public`` SCHEMA ATTACHED, holding a real
``public.dump_lane_artifacts`` TABLE and a real ``public.dump_lane_heartbeat``
VIEW. So the selection predicate (``ORDER BY finished_at DESC, artifact DESC
LIMIT 1`` on the TABLE), the NULL guards, the age arithmetic, the identity
comparison and the journal round-trips are all exercised as production runs
them — never against a hand-built verdict.

FIXTURE FIDELITY NOTES. The attached ``public`` database is what makes the
``public.<relation>`` names in the module's SQL resolve on SQLite; the column
types ``PGTIMESTAMP`` / ``PGBOOL`` are declared type NAMES with sqlite3
converters registered HERE (a private type name, so no other suite's
``PARSE_DECLTYPES`` connection can pick up a converter it did not ask for).
``integrity_ok`` must arrive as a real Python ``bool`` because the module
asserts ``integrity_ok IS TRUE`` (a NULL or a truthy non-boolean never passes).

PIN → NODE MAP (the §TEST MATRIX names, verbatim):

* (a) masking — ``test_masking_newest_broken_refuses_naming_newest`` (the same
  node asserts the VIEW would have returned the OLDER row, i.e. the historical
  view-based read is genuinely RED).
* (b) skip + stamp-advance — ``test_skip_advances_last_checked_at_and_appends_row``,
  ``test_skip_does_not_fire_when_aged_past_bound``,
  ``test_skip_does_not_fire_when_integrity_flips_false_with_unchanged_identity``,
  ``test_skip_does_not_fire_when_remembered_bytes_is_zero``,
  ``test_identity_change_forces_revalidation``.
* (c) loud failure — ``test_zero_rows_refuses_no_backup``,
  ``test_relation_absent_refuses_unavailable``,
  ``test_select_timeout_maps_to_registry_unavailable``,
  ``test_torn_journal_refuses_journal_torn`` (HERMETIC only — the arm-path
  mapping of the same condition is ``journal-unavailable``, pre-lock), plus the
  NULL-guard nodes ``test_null_bytes_refuses_registry_unavailable`` /
  ``test_null_finished_at_refuses_registry_unavailable`` /
  ``test_null_artifact_refuses_registry_unavailable`` /
  ``test_null_integrity_ok_refuses_artifact_broken``.
* (d) mode gate — ``test_full_dump_refuses_unavailable``,
  ``test_unknown_mode_refuses_mode_unknown``,
  ``test_absent_mode_proceeds_validation_only``.
* (f) durability — ``test_record_round_trips_simulated_restart``,
  ``test_ensure_extensions_seeds_db_preflight``,
  ``test_adr034_round_trip_preserves_extra_key``.
* (F13) clock skew — ``test_future_finished_at_beyond_skew_refuses_unavailable``,
  ``test_future_finished_at_within_skew_clamps_age_to_zero``.
* string ban — ``test_module_source_contains_no_kubectl_literal``.

The (d) EXECUTOR-BOUNDARY pins and the dry-run/arm-path nodes live in
``tests/unit/tools/test_upgrade_tools.py`` (``TestArmPreflightDbPreflight``) —
that is what §TEST MATRIX row (d) prescribes; T6 #1's prose listing the
boundary pins among this module's nodes is the discrepancy resolved in favour
of the matrix (as the plan's matrix is authoritative for placement).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, event, text

from daemon.tools import upgrade_db_preflight as udp
from daemon.tools import upgrade_journal as uj

# ── SQLite typed-column converters (PRIVATE type names — see the module
# docstring: a private name keeps this fixture from perturbing any other
# suite's PARSE_DECLTYPES connections). The scope is per-process (sqlite3
# registries are global), so the names are asserted to be ours alone.


def _conv_timestamp(raw: Any) -> datetime:
    """``PGTIMESTAMP`` → the naive-UTC datetime a ``timestamptz`` column yields
    through a driver that has already normalised to UTC."""
    text_value = raw.decode() if isinstance(raw, bytes) else raw
    return datetime.fromisoformat(text_value)


def _conv_bool(raw: Any) -> bool:
    """``PGBOOL`` → a REAL Python ``bool`` (the module asserts ``IS TRUE``)."""
    text_value = raw.decode() if isinstance(raw, bytes) else raw
    return str(text_value).strip().lower() in ("1", "true", "t")


sqlite3.register_converter("PGTIMESTAMP", _conv_timestamp)
sqlite3.register_converter("PGBOOL", _conv_bool)

_DETECT_TYPES = sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES

# The registry is EXTERNALLY OWNED with no repo contract (Risk #1), so the
# fixture declares NO NOT NULL constraints: a nullable column is exactly the
# publisher change the NULL guards exist for. The production shape has a
# ``(lane, artifact)`` primary key; here a NON-unique index carries the same
# read cost without forbidding the NULL-column fixtures.
_TABLE_DDL = """
CREATE TABLE public.dump_lane_artifacts (
    lane          VARCHAR,
    artifact      VARCHAR,
    bytes         BIGINT,
    finished_at   PGTIMESTAMP,
    integrity_ok  PGBOOL,
    first_seen_at PGTIMESTAMP,
    last_seen_at  PGTIMESTAMP
)
"""

# SQLite requires the INDEX name to carry the schema when the TABLE lives in an
# attached schema (a schema-qualified table reference in CREATE INDEX is a
# syntax error there).
_INDEX_DDL = """
CREATE INDEX public.idx_dump_lane_artifacts_lane
    ON dump_lane_artifacts (lane)
"""

_VIEW_DDL = """
CREATE VIEW public.dump_lane_heartbeat AS
    SELECT * FROM public.dump_lane_artifacts WHERE integrity_ok IS TRUE
"""

_ARTIFACT_NEWEST = "pg_dumpall-2026-11-26-17-00.pgdump.zst"
_ARTIFACT_OLDER = "pg_dumpall-2026-11-25-17-00.pgdump.zst"

# Explicit NULL sentinel: ``insert_registry_row`` overloads ``None`` to mean
# "use the default" for ``finished_at``, so a real NULL must be requested via
# this sentinel.
_NULL = object()


def _utc_now() -> datetime:
    """Naive UTC — the shape a ``timestamptz`` column yields once normalised."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(moment: datetime) -> str:
    return moment.isoformat()


# ── Fixtures ────────────────────────────────────────────────────────────────


def build_registry_engine(
    base: Path, *, table: bool = True, view: bool = True, tag: str = ""
):  # noqa: ANN201
    """A SQLAlchemy engine whose ``public`` schema (ATTACHed) carries the
    registry TABLE and, unless disabled, the heartbeat VIEW.

    EXPORTED deliberately: the arm-path class in ``test_upgrade_tools.py``
    reuses it to drive the REAL verdict against a real registry through the
    arm, instead of hand-building refusal verdicts (one registry fixture,
    one place). ``tag`` keeps two engines in the same directory apart.
    """
    suffix = f"-{tag}" if tag else ""
    main_db = base / f"main{suffix}.db"
    public_db = base / f"public_schema{suffix}.db"
    engine = create_engine(
        f"sqlite:///{main_db}",
        future=True,
        connect_args={
            "detect_types": _DETECT_TYPES,
            # The registry read runs on an asyncio.to_thread worker (the house
            # to_thread + wait_for bridge), so the pooled connection
            # legitimately crosses threads — the same relaxation the
            # production driver does not need.
            "check_same_thread": False,
        },
    )

    @event.listens_for(engine, "connect")
    def _attach_public_schema(dbapi_connection, _record):  # noqa: ANN001
        dbapi_connection.execute(f"ATTACH DATABASE '{public_db}' AS public")

    with engine.begin() as conn:
        if table:
            conn.execute(text(_TABLE_DDL))
            conn.execute(text(_INDEX_DDL))
        if view:
            conn.execute(text(_VIEW_DDL))
    return engine


@pytest.fixture
def registry_factory(tmp_path: Path):
    """Factory over :func:`build_registry_engine` (``make(**kwargs) ->
    (engine, public_db_path)``)."""

    def _make(*, table: bool = True, view: bool = True, tag: str = ""):
        engine = build_registry_engine(tmp_path, table=table, view=view, tag=tag)
        public_db = tmp_path / (f"public_schema-{tag}.db" if tag else "public_schema.db")
        return engine, public_db

    return _make


@pytest.fixture
def registry(registry_factory) -> Any:
    """The default registry: TABLE + VIEW, no rows."""
    engine, _ = registry_factory()
    return engine


@pytest.fixture
def install(tmp_path: Path) -> Path:
    """A ``tmp_path``-scoped staged install carrying a real journal (the
    Python writer) with every extension key seeded."""
    inst = tmp_path / "install"
    (inst / "releases").mkdir(parents=True)
    uj.journal_init(inst)
    uj.ensure_extensions(inst)
    return inst


class _ManagerStub:
    """The ONLY manager surface the verdict module touches: ``.engine``.

    Deliberately a tiny object rather than a ``MagicMock`` — a mock would
    answer ``.engine`` with another mock, which the module would then hand to
    ``engine.begin()`` and get an unreadable row from (a REFUSAL, never a pass).
    """

    def __init__(self, engine) -> None:  # noqa: ANN001
        self.engine = engine


def _manager(engine) -> _ManagerStub:  # noqa: ANN001
    return _ManagerStub(engine)


def insert_registry_row(
    engine,  # noqa: ANN001
    *,
    artifact: str = _ARTIFACT_NEWEST,
    lane: str = udp.DB_PREFLIGHT_LANE,
    finished_at: datetime | None = None,
    size: int | None = 921_187_559,
    integrity_ok: bool | int | None = True,
) -> None:
    """Insert one registry row. ``size`` / ``integrity_ok`` accept ``None``
    on purpose (the NULL-guard pins) — the column is nullable in the
    externally-owned schema and the verdict must refuse, never raise."""
    # Normalise the explicit-NULL sentinel into a real NULL.
    if artifact is _NULL:
        artifact = None
    if size is _NULL:
        size = None
    if integrity_ok is _NULL:
        integrity_ok = None
    if finished_at is _NULL:
        moment = None
    elif finished_at is None:
        moment = _utc_now() - timedelta(hours=1)
    else:
        moment = finished_at
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO public.dump_lane_artifacts "
                "(lane, artifact, bytes, finished_at, integrity_ok, "
                " first_seen_at, last_seen_at) "
                "VALUES (:lane, :artifact, :bytes, :finished_at, "
                "        :integrity_ok, :finished_at, :finished_at)"
            ),
            {
                "lane": lane,
                "artifact": artifact,
                "bytes": size,
                "finished_at": None if moment is None else _iso(moment),
                "integrity_ok": (
                    None if integrity_ok is None else int(bool(integrity_ok))
                ),
            },
        )


async def _evaluate(engine, install_dir: Path, **kwargs: Any):  # noqa: ANN001
    """Drive the REAL verdict function (no seam stub in this module)."""
    return await udp.evaluate_db_preflight(_manager(engine), install_dir, **kwargs)


def _record(install_dir: Path, verdict) -> dict[str, Any]:  # noqa: ANN001
    """``uj.record_db_preflight`` through the real journal (the D3 write)."""
    assert isinstance(verdict.record, dict), verdict
    uj.record_db_preflight(install_dir, verdict.record)
    return verdict.record


# ── Pin (a): the TABLE is read, never the VIEW (masking) ────────────────────


@pytest.mark.asyncio
async def test_masking_newest_broken_refuses_naming_newest(
    registry, install: Path
) -> None:
    """The newest row OVERALL is selected regardless of ``integrity_ok``, then
    ``integrity_ok IS TRUE`` is asserted on THAT row.

    MUTATION PROOF (same node): the heartbeat VIEW — the historical, forbidden
    read — is queried directly and shown to return the OLDER (good) row, so a
    view-based implementation would pass the integrity check (no refusal) and
    this node's refusal assertion would go RED.
    """
    newest_finished = datetime(2026, 11, 26, 17, 4, 41)
    insert_registry_row(registry, artifact=_ARTIFACT_NEWEST, finished_at=newest_finished, integrity_ok=False)
    insert_registry_row(
        registry,
        artifact=_ARTIFACT_OLDER,
        finished_at=newest_finished - timedelta(days=1),
        integrity_ok=True,
    )

    verdict = await _evaluate(registry, install)

    assert verdict.verdict == udp.VERDICT_REFUSED, verdict
    assert verdict.token == udp.TOK_ARTIFACT_BROKEN, verdict.detail
    assert _ARTIFACT_NEWEST in verdict.detail, verdict.detail
    assert _ARTIFACT_OLDER not in verdict.detail, verdict.detail
    # The artifact-bearing M7 shape (decisions.md §Refusal detail format).
    assert verdict.detail.startswith(
        f"token={udp.TOK_ARTIFACT_BROKEN} artifact={_ARTIFACT_NEWEST} "
    ), verdict.detail
    assert " age=" in verdict.detail and " bound=" in verdict.detail, verdict.detail

    # MUTATION PROOF: the view masks the broken newest artifact.
    with registry.begin() as conn:
        view_rows = [
            dict(row)
            for row in conn.execute(
                text("SELECT artifact FROM public.dump_lane_heartbeat")
            ).mappings()
        ]
    assert [row["artifact"] for row in view_rows] == [_ARTIFACT_OLDER], view_rows
    # ... and the record was NOT written (a refusal never writes the D3 record).
    assert uj.journal_read(install).get("db_preflight") is None


# ── Pin (b): skip predicate + stamp advance (D4 AMENDMENT) ──────────────────


@pytest.mark.asyncio
async def test_skip_advances_last_checked_at_and_appends_row(
    registry, install: Path
) -> None:
    """Two consecutive invocations over an unchanged fresh artifact: the first
    validates, the second SKIPS; the remembered record is read back through a
    REAL JSON round trip (the journal file, re-parsed) so a raw-datetime vs
    stored-string comparison can never accidentally pass; ``last_checked_at``
    advances and only ``last_checked_at`` + ``verdict`` move.
    """
    insert_registry_row(registry)

    first = await _evaluate(registry, install)
    assert first.verdict == udp.VERDICT_VALIDATED, first.detail
    first_record = _record(install, first)
    assert first_record["verdict"] == "validated"
    assert first_record["fingerprint_sha256"] == first.fingerprint_sha256

    # REAL round trip: read the journal FILE and re-parse it (a fresh process
    # sees exactly this), then let the verdict function read the same file.
    reread = json.loads(json.dumps(uj.journal_read(install)))
    assert reread["db_preflight"]["artifact"] == _ARTIFACT_NEWEST
    assert reread["db_preflight"]["finished_at"] == first.finished_at_iso
    assert isinstance(reread["db_preflight"]["finished_at"], str)

    second = await _evaluate(registry, install)
    assert second.verdict == udp.VERDICT_SKIPPED, second.detail
    assert second.identity_unchanged is True
    assert second.detail.endswith("verdict=skipped identity=unchanged"), second.detail

    second_record = _record(install, second)
    assert second_record["verdict"] == "skipped"
    first_checked = datetime.fromisoformat(first_record["last_checked_at"].replace("Z", "+00:00"))
    second_checked = datetime.fromisoformat(second_record["last_checked_at"].replace("Z", "+00:00"))
    assert second_checked > first_checked, (first_checked, second_checked)
    # The validated tuple + fingerprint are untouched by the skip.
    for field_name in ("lane", "artifact", "bytes", "finished_at", "fingerprint_sha256"):
        assert second_record[field_name] == first_record[field_name], field_name

    row = uj.journal_read(install)["db_preflight"]
    assert row["verdict"] == "skipped"
    assert row["artifact"] == _ARTIFACT_NEWEST


@pytest.mark.asyncio
async def test_skip_does_not_fire_when_aged_past_bound(
    registry, install: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unchanged identity is NOT enough: the freshness bound is evaluated on
    EVERY invocation. Two legs, one per way the bound can bite:

    LEG 1 — an UNCHANGED identity (a remembered record that matches the fetched
    row EXACTLY, fingerprint included) whose ``finished_at`` sits past the
    SHIPPED 48 h bound ⇒ refuses ``db-preflight-artifact-stale`` instead of
    skipping. This is the "skip does not fire when aged" proof proper: without
    the age conjunct in the predicate this node would return ``skipped``.

    LEG 2 — the same artifact, still fresh, with the bound TIGHTENED through
    the module global (the ``.env`` knob is read at import time, so a unit
    test can only move the global) ⇒ refuses stale, and the D3 record is NOT
    mutated by the refusal.
    """
    insert_registry_row(registry, finished_at=_utc_now() - timedelta(hours=10))

    first = await _evaluate(registry, install)
    assert first.verdict == udp.VERDICT_VALIDATED, first.detail
    _record(install, first)

    # SHIPPED bound explicit (env-independent): the module global is read
    # at call time, so pin it here regardless of ambient env.
    monkeypatch.setattr(udp, "DB_PREFLIGHT_MAX_AGE_S", 172800)

    # ── LEG 1: aged past the SHIPPED bound with a MATCHING remembered identity.
    aged_moment = _utc_now() - timedelta(hours=50)
    aged_engine = build_registry_engine(install.parent, tag="aged")
    insert_registry_row(aged_engine, artifact=_ARTIFACT_NEWEST, finished_at=aged_moment)
    aged_install = install.parent / "install-aged"
    (aged_install / "releases").mkdir(parents=True)
    uj.journal_init(aged_install)
    uj.ensure_extensions(aged_install)
    _, aged_iso, aged_fingerprint = udp.identity_fields(
        udp.DB_PREFLIGHT_LANE, _ARTIFACT_NEWEST, 921_187_559, aged_moment
    )
    aged_remembered = {
        "lane": udp.DB_PREFLIGHT_LANE,
        "artifact": _ARTIFACT_NEWEST,
        "bytes": 921_187_559,
        "finished_at": aged_iso,
        "fingerprint_sha256": aged_fingerprint,
        "last_checked_at": aged_iso,
        "verdict": "validated",
    }
    uj.record_db_preflight(aged_install, aged_remembered)

    aged = await _evaluate(aged_engine, aged_install)
    assert aged.verdict == udp.VERDICT_REFUSED, aged.detail
    assert aged.token == udp.TOK_ARTIFACT_STALE, aged.detail
    assert "bound=172800s" in aged.detail, aged.detail
    # The record really was untouched — it still shows the old validation.
    assert uj.journal_read(aged_install)["db_preflight"] == aged_remembered

    # ── LEG 2: still fresh, but the bound tightened through the documented knob.
    # The knob's contract: DB_PREFLIGHT_MAX_AGE_S is read at CALL time.
    monkeypatch.setattr(udp, "DB_PREFLIGHT_MAX_AGE_S", 6 * 3600)

    second = await _evaluate(registry, install)
    assert second.verdict == udp.VERDICT_REFUSED, second.detail
    assert second.token == udp.TOK_ARTIFACT_STALE, second.detail
    assert _ARTIFACT_NEWEST in second.detail, second.detail
    assert "bound=21600s" in second.detail, second.detail
    # The record is NOT mutated on a refusal (it still shows the validation).
    assert uj.journal_read(install)["db_preflight"]["verdict"] == "validated"


@pytest.mark.asyncio
async def test_skip_does_not_fire_when_integrity_flips_false_with_unchanged_identity(
    registry, install: Path
) -> None:
    """THE D4-AMENDMENT MUTATION PROOF.

    The publisher re-verifies the NEWEST artifact on every run and writes
    ``integrity_ok`` with ``INSERT … ON CONFLICT DO UPDATE``, so the flag can
    flip to ``false`` with ``(lane, artifact, bytes, finished_at)`` — and hence
    the fingerprint — UNCHANGED. A skip predicate that short-circuited the
    integrity assertion (the WITHDRAWN form) would skip here and let the arm
    proceed on a backup the lane's own verifier marks BROKEN. Reintroducing
    that short-circuit makes this node RED.
    """
    insert_registry_row(registry)
    first = await _evaluate(registry, install)
    assert first.verdict == udp.VERDICT_VALIDATED, first.detail
    first_record = _record(install, first)

    # The publisher's in-place update: same identity tuple, flipped verdict.
    with registry.begin() as conn:
        conn.execute(
            text(
                "UPDATE public.dump_lane_artifacts SET integrity_ok = 0 "
                "WHERE lane = :lane AND artifact = :artifact"
            ),
            {"lane": udp.DB_PREFLIGHT_LANE, "artifact": _ARTIFACT_NEWEST},
        )

    second = await _evaluate(registry, install)
    assert second.verdict == udp.VERDICT_REFUSED, second.detail
    assert second.token == udp.TOK_ARTIFACT_BROKEN, second.detail
    assert _ARTIFACT_NEWEST in second.detail, second.detail
    # A refusal carries no identity_unchanged proof on the verdict.
    assert second.identity_unchanged is None
    # NOT mutated to skipped: the record still carries the validation.
    assert uj.journal_read(install)["db_preflight"]["verdict"] == "validated"


@pytest.mark.asyncio
async def test_skip_does_not_fire_when_remembered_bytes_is_zero(
    registry, install: Path
) -> None:
    """A remembered record that matches a ``bytes == 0`` row is NOT a skip:
    the ``bytes > 0`` gate is re-asserted on every invocation, so the verdict
    is ``db-preflight-artifact-empty``. (The remembered record here is written
    directly, the only way a zero-byte identity can exist — a live validation
    can never produce one, which is exactly why the gate must not trust it.)
    """
    moment = _utc_now() - timedelta(hours=1)
    insert_registry_row(registry, finished_at=moment, size=0)
    _, finished_at_iso, fingerprint = udp.identity_fields(
        udp.DB_PREFLIGHT_LANE, _ARTIFACT_NEWEST, 0, moment
    )
    uj.record_db_preflight(
        install,
        {
            "lane": udp.DB_PREFLIGHT_LANE,
            "artifact": _ARTIFACT_NEWEST,
            "bytes": 0,
            "finished_at": finished_at_iso,
            "fingerprint_sha256": fingerprint,
            "last_checked_at": finished_at_iso,
            "verdict": "validated",
        },
    )

    verdict = await _evaluate(registry, install)
    assert verdict.verdict == udp.VERDICT_REFUSED, verdict.detail
    assert verdict.token == udp.TOK_ARTIFACT_EMPTY, verdict.detail
    assert verdict.identity_unchanged is None, verdict
    assert uj.journal_read(install)["db_preflight"]["verdict"] == "validated"


@pytest.mark.asyncio
async def test_identity_change_forces_revalidation(registry, install: Path) -> None:
    """A NEW artifact (identity changed) re-validates instead of skipping, and
    the D3 record moves to the new identity."""
    insert_registry_row(
        registry,
        artifact=_ARTIFACT_OLDER,
        finished_at=_utc_now() - timedelta(hours=20),
    )
    first = await _evaluate(registry, install)
    assert first.verdict == udp.VERDICT_VALIDATED, first.detail
    _record(install, first)

    insert_registry_row(registry)
    second = await _evaluate(registry, install)
    assert second.verdict == udp.VERDICT_VALIDATED, second.detail
    assert second.identity_unchanged is False
    assert second.artifact == _ARTIFACT_NEWEST
    assert "identity=unchanged" not in second.detail
    record = _record(install, second)
    assert record["artifact"] == _ARTIFACT_NEWEST


# ── Pin (c): loud failure (zero rows / relation absent / timeout / torn) ────


@pytest.mark.asyncio
async def test_zero_rows_refuses_no_backup(registry, install: Path) -> None:
    """Zero rows for the lane ⇒ ``db-preflight-no-backup`` (artifact-less M7
    detail), never a pass and never a skip."""
    verdict = await _evaluate(registry, install)
    assert verdict.verdict == udp.VERDICT_REFUSED, verdict
    assert verdict.token == udp.TOK_NO_BACKUP, verdict.detail
    assert verdict.detail.startswith(
        f"token={udp.TOK_NO_BACKUP} reason={udp.TOK_NO_BACKUP} "
    ), verdict.detail
    assert uj.journal_read(install).get("db_preflight") is None


@pytest.mark.asyncio
async def test_relation_absent_refuses_unavailable(
    registry_factory, install: Path
) -> None:
    """The externally-owned relation absent ⇒ ``db-preflight-registry-unavailable``
    (never green, never a silent skip) with the artifact-less detail shape."""
    engine, _ = registry_factory(table=False, view=False)
    verdict = await _evaluate(engine, install)
    assert verdict.verdict == udp.VERDICT_REFUSED, verdict
    assert verdict.token == udp.TOK_REGISTRY_UNAVAILABLE, verdict.detail
    assert verdict.detail.startswith(
        f"token={udp.TOK_REGISTRY_UNAVAILABLE} "
        f"reason={udp.TOK_REGISTRY_UNAVAILABLE} "
    ), verdict.detail
    assert verdict.artifact is None
    assert uj.journal_read(install).get("db_preflight") is None


@pytest.mark.asyncio
async def test_select_timeout_maps_to_registry_unavailable(
    registry, install: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The client belt firing (``asyncio.wait_for`` around the ``to_thread``
    read) maps to ``db-preflight-registry-unavailable`` with the timeout cause
    named in the artifact-less detail.

    The belt is exercised for real: the module reads ``SELECT_CLIENT_TIMEOUT_S``
    as a module global at call time, so a tiny timeout against a slow sync body
    raises ``asyncio.TimeoutError`` out of ``fetch_newest_artifact``.
    """
    insert_registry_row(registry)

    real_run_select = udp._run_select

    def _slow_run_select(manager):  # noqa: ANN001, ANN202
        import time as _time

        _time.sleep(0.25)
        return real_run_select(manager)

    monkeypatch.setattr(udp, "_run_select", _slow_run_select)
    monkeypatch.setattr(udp, "SELECT_CLIENT_TIMEOUT_S", 0.01)

    verdict = await _evaluate(registry, install)
    assert verdict.verdict == udp.VERDICT_REFUSED, verdict
    assert verdict.token == udp.TOK_REGISTRY_UNAVAILABLE, verdict.detail
    assert "select timed out" in verdict.detail, verdict.detail


def test_connection_failure_causes_keep_distinct_details() -> None:
    """(c) / Risk #5 / post-approval note N3: a REFUSED new connection and
    POOL EXHAUSTION at checkout are two DISTINCT causes of the SAME token, told
    apart by the ``reason=`` detail — they never share a detail string.
    """
    from sqlalchemy.exc import InterfaceError
    from sqlalchemy.exc import TimeoutError as SATimeoutError

    refused = udp.registry_failure_detail(
        InterfaceError("SELECT 1", {}, Exception("connection refused"))
    )
    exhausted = udp.registry_failure_detail(SATimeoutError("pool exhausted"))
    assert refused.startswith("connect refused"), refused
    assert exhausted.startswith("pool exhausted"), exhausted
    assert refused != exhausted
    # Both land on the SAME token through the artifact-less M7 shape.
    assert udp.artifact_less(udp.TOK_REGISTRY_UNAVAILABLE, refused).startswith(
        f"token={udp.TOK_REGISTRY_UNAVAILABLE} "
        f"reason={udp.TOK_REGISTRY_UNAVAILABLE} connect refused"
    )
    assert udp.artifact_less(udp.TOK_REGISTRY_UNAVAILABLE, exhausted).startswith(
        f"token={udp.TOK_REGISTRY_UNAVAILABLE} "
        f"reason={udp.TOK_REGISTRY_UNAVAILABLE} pool exhausted"
    )

    # The OTHER side of the timeout pin: a server-side statement_timeout
    # (SQLSTATE 57014) is classified as its own cause, distinct from the
    # client belt and from a refused connection.
    from sqlalchemy.exc import OperationalError

    class _DriverError(Exception):
        """Stands in for a psycopg error: the classifier reads ONLY
        ``sqlstate``/``pgcode`` off ``.orig`` (never the driver message)."""

        sqlstate = "57014"

    server_side = udp.registry_failure_detail(
        OperationalError("SELECT 1", {}, _DriverError("statement timeout"))
    )
    assert server_side.startswith("statement timeout"), server_side
    assert server_side != refused and server_side != exhausted


@pytest.mark.asyncio
async def test_torn_journal_refuses_journal_torn(install: Path) -> None:
    """HERMETIC ONLY: the verdict function PROPAGATES ``JournalTorn`` (the
    caller maps it to ``db-preflight-journal-torn``) — it never returns a
    verdict over a journal it could not read.

    The arm-path half of this pin is ``journal-unavailable``, pre-lock: an
    arm-entry torn journal never reaches this check
    (``test_arm_entry_torn_journal_refuses_journal_unavailable_pre_lock`` in
    ``test_upgrade_tools.py``).
    """
    uj.journal_path(install).write_text('{"current":', encoding="utf-8")

    with pytest.raises(uj.JournalTorn):
        await _evaluate(None, install)  # type: ignore[arg-type]

    # T2 #2 acceptance: the record HELPER also propagates (never writes over a
    # torn journal).
    with pytest.raises(uj.JournalTorn):
        uj.record_db_preflight(install, {"lane": udp.DB_PREFLIGHT_LANE})
    assert uj.journal_path(install).read_text(encoding="utf-8") == '{"current":'


# ── Pin (c)/(0b): the NULL guards (externally-owned schema) ─────────────────


@pytest.mark.asyncio
async def test_null_bytes_refuses_registry_unavailable(registry, install: Path) -> None:
    """NULL ``bytes`` ⇒ artifact-less ``db-preflight-registry-unavailable``
    naming the column — NEVER a ``TypeError`` out of the verdict (which, on the
    arm path, would surface as the generic unexpected-exception arm)."""
    insert_registry_row(registry, size=None)
    verdict = await _evaluate(registry, install)
    assert verdict.verdict == udp.VERDICT_REFUSED, verdict
    assert verdict.token == udp.TOK_REGISTRY_UNAVAILABLE, verdict.detail
    assert "bytes is NULL" in verdict.detail, verdict.detail
    assert "externally-owned schema violation" in verdict.detail, verdict.detail


@pytest.mark.asyncio
async def test_null_finished_at_refuses_registry_unavailable(
    registry, install: Path
) -> None:
    """NULL ``finished_at`` ⇒ the same artifact-less refusal (the age
    arithmetic must never see a NULL)."""
    insert_registry_row(
        registry, artifact=_ARTIFACT_NEWEST, size=10, finished_at=_NULL
    )
    verdict = await _evaluate(registry, install)
    assert verdict.verdict == udp.VERDICT_REFUSED, verdict
    assert verdict.token == udp.TOK_REGISTRY_UNAVAILABLE, verdict.detail
    assert "finished_at is NULL" in verdict.detail, verdict.detail


@pytest.mark.asyncio
async def test_null_artifact_refuses_registry_unavailable(
    registry, install: Path
) -> None:
    """NULL ``artifact`` ⇒ the same artifact-less refusal naming ``artifact``."""
    insert_registry_row(
        registry, artifact=_NULL, size=10, finished_at=_utc_now(), integrity_ok=True
    )
    verdict = await _evaluate(registry, install)
    assert verdict.verdict == udp.VERDICT_REFUSED, verdict
    assert verdict.token == udp.TOK_REGISTRY_UNAVAILABLE, verdict.detail
    assert "artifact is NULL" in verdict.detail, verdict.detail


@pytest.mark.asyncio
async def test_null_integrity_ok_refuses_artifact_broken(
    registry, install: Path
) -> None:
    """NULL ``integrity_ok`` ⇒ ``db-preflight-artifact-broken`` naming the
    artifact: only the literal ``True`` passes ``IS TRUE``, so a NULL can never
    read as verified (and the detail is the artifact-BEARING shape, because the
    artifact name is present)."""
    insert_registry_row(registry, integrity_ok=None)
    verdict = await _evaluate(registry, install)
    assert verdict.verdict == udp.VERDICT_REFUSED, verdict
    assert verdict.token == udp.TOK_ARTIFACT_BROKEN, verdict.detail
    assert verdict.detail.startswith(
        f"token={udp.TOK_ARTIFACT_BROKEN} artifact={_ARTIFACT_NEWEST} "
    ), verdict.detail


# ── Pin (d): the mode gate (every environment, one implementation) ──────────


class _BrokenEngineManager:
    """A manager whose engine FAILS LOUDLY if the verdict ever touches it —
    the non-vacuity guard for the mode gate's "before any registry read"."""

    class _Engine:
        url = None

        def begin(self):  # noqa: ANN201
            raise AssertionError(
                "the mode gate must refuse BEFORE any registry read"
            )

    def __init__(self) -> None:
        self.engine = self._Engine()


@pytest.mark.asyncio
async def test_full_dump_refuses_unavailable(install: Path) -> None:
    """``full-dump`` ⇒ ``db-preflight-full-dump-unavailable`` (no VM-side dump
    leg exists — never a silent fallback). The gate refuses BEFORE any registry
    read, so a deliberately broken engine proves the read never happened."""
    verdict = await _evaluate(_BrokenEngineManager(), install, mode="full-dump")
    assert verdict.verdict == udp.VERDICT_REFUSED, verdict
    assert verdict.token == udp.TOK_FULL_DUMP_UNAVAILABLE, verdict.detail
    assert verdict.detail.startswith(
        f"token={udp.TOK_FULL_DUMP_UNAVAILABLE} "
        f"reason={udp.TOK_FULL_DUMP_UNAVAILABLE} "
    ), verdict.detail


@pytest.mark.asyncio
async def test_unknown_mode_refuses_mode_unknown(install: Path) -> None:
    """An unrecognised mode ⇒ ``db-preflight-mode-unknown``, likewise before any
    registry read."""
    verdict = await _evaluate(_BrokenEngineManager(), install, mode="snapshot")
    assert verdict.verdict == udp.VERDICT_REFUSED, verdict
    assert verdict.token == udp.TOK_MODE_UNKNOWN, verdict.detail
    assert "snapshot" in verdict.detail, verdict.detail


@pytest.mark.asyncio
async def test_absent_mode_proceeds_validation_only(registry, install: Path) -> None:
    """Absent mode ⇒ validation-only proceeds (a real validation, not a
    refusal)."""
    insert_registry_row(registry)
    verdict = await _evaluate(registry, install, mode=None)
    assert verdict.verdict == udp.VERDICT_VALIDATED, verdict.detail
    assert verdict.token is None


# ── Pin (f): durability (journal round trip / seed / unrelated key) ─────────


@pytest.mark.asyncio
async def test_record_round_trips_simulated_restart(
    registry, install: Path
) -> None:
    """The record survives a simulated restart: a freshly parsed journal (the
    only state a new process has) re-reads every field, and a subsequent
    invocation in that "fresh process" SKIPS on the strength of it."""
    insert_registry_row(registry)
    first = await _evaluate(registry, install)
    assert first.verdict == udp.VERDICT_VALIDATED, first.detail
    _record(install, first)

    # Simulated restart: nothing in memory, only the file.
    raw = uj.journal_path(install).read_text(encoding="utf-8")
    fresh = json.loads(raw)
    record = fresh["db_preflight"]
    assert set(record) == {
        "lane",
        "artifact",
        "bytes",
        "finished_at",
        "fingerprint_sha256",
        "last_checked_at",
        "verdict",
    }, sorted(record)
    assert record["lane"] == udp.DB_PREFLIGHT_LANE
    assert record["artifact"] == _ARTIFACT_NEWEST
    assert isinstance(record["bytes"], int)
    assert record["finished_at"].endswith("Z")
    assert record["last_checked_at"].endswith("Z")
    assert record["verdict"] == "validated"
    assert record["fingerprint_sha256"] == first.fingerprint_sha256

    second = await _evaluate(registry, install)
    assert second.verdict == udp.VERDICT_SKIPPED, second.detail


def test_ensure_extensions_seeds_db_preflight(install: Path) -> None:
    """``ensure_extensions`` seeds ``db_preflight`` as the ``None``
    placeholder (the ``pending_op``/``pending_restart`` shape, NOT the
    ``pending_wakes`` ``{}`` shape) — and is idempotent over a written record."""
    data = uj.ensure_extensions(install)
    assert "db_preflight" in data
    assert data["db_preflight"] is None

    # A seeded-then-written record is never clobbered by a re-run.
    record = {"lane": udp.DB_PREFLIGHT_LANE, "artifact": _ARTIFACT_NEWEST}
    uj.record_db_preflight(install, record)
    again = uj.ensure_extensions(install)
    assert again["db_preflight"] == record


def test_adr034_round_trip_preserves_extra_key(install: Path) -> None:
    """ADR-034 discipline: an UNRELATED extension key written by someone else
    survives the new key's write (the helper only ADDS / mutates its own slot)."""
    data = uj.journal_read(install)
    data["some_future_lane_key"] = {"evidence": ["x", "y"], "count": 2}
    uj.journal_write(install, data)

    uj.record_db_preflight(
        install,
        {"lane": udp.DB_PREFLIGHT_LANE, "artifact": _ARTIFACT_NEWEST, "verdict": "validated"},
    )

    after = uj.journal_read(install)
    assert after["some_future_lane_key"] == {"evidence": ["x", "y"], "count": 2}
    assert after["db_preflight"]["artifact"] == _ARTIFACT_NEWEST


# ── Pin (F13): the clock-skew guard ────────────────────────────────────────


@pytest.mark.asyncio
async def test_future_finished_at_beyond_skew_refuses_unavailable(
    registry, install: Path
) -> None:
    """A future-dated row BEYOND the 300 s tolerance is REFUSED fail-closed —
    a raw negative age would otherwise PASS ``age <= bound``."""
    insert_registry_row(registry, finished_at=_utc_now() + timedelta(seconds=400))
    verdict = await _evaluate(registry, install)
    assert verdict.verdict == udp.VERDICT_REFUSED, verdict
    assert verdict.token == udp.TOK_REGISTRY_UNAVAILABLE, verdict.detail
    assert "finished_at in the future beyond skew tolerance" in verdict.detail
    assert "tolerance=300s" in verdict.detail, verdict.detail


@pytest.mark.asyncio
async def test_future_finished_at_within_skew_clamps_age_to_zero(
    registry, install: Path
) -> None:
    """A negative age WITHIN the tolerance is CLAMPED to ``0`` for the bound
    comparison only — the identity/fingerprint keep the RAW normalised
    ``finished_at_iso`` (so a clamped row can never be mistaken for a row
    stamped at ``now``)."""
    future = _utc_now() + timedelta(seconds=100)
    insert_registry_row(registry, finished_at=future)

    verdict = await _evaluate(registry, install)
    assert verdict.verdict == udp.VERDICT_VALIDATED, verdict.detail
    assert verdict.age_s == 0
    _, expected_iso, expected_fingerprint = udp.identity_fields(
        udp.DB_PREFLIGHT_LANE, _ARTIFACT_NEWEST, 921_187_559, future
    )
    assert verdict.finished_at_iso == expected_iso
    assert verdict.fingerprint_sha256 == expected_fingerprint
    assert verdict.record is not None
    assert verdict.record["finished_at"] == expected_iso


# ── The kubectl string ban (daemon/**/*.py) ────────────────────────────────


def test_module_source_contains_no_kubectl_literal() -> None:
    """The daemon-wide substring ban (``tests/unit/test_checkpoint_lane_docs_pins.py``)
    holds for the NEW module: the disclosure prose lives in the planning docs,
    never in ``daemon/**``. Non-vacuous: the module source really was read.
    """
    source = Path(udp.__file__).read_text(encoding="utf-8")
    assert len(source) > 1000, "the module source was not read"
    assert "kubectl" not in source

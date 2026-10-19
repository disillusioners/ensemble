"""``GET /api/resources/anomalies`` + evidence — HTTP contract pack (D7).

Covers the phase4-plan task 8 router acceptance:

* list shape — EXACTLY ``{anomalies: [Anomaly]}`` (no extra keys; the
  FE's truncated-banner rides the /history body's ``truncated``
  field, deliberately NOT here);
* ``window`` enum EXACTLY ``{15m, 1h, 24h, 7d}``, DEFAULT ``1h``
  (leader-ruled + FE-spec-pinned); malformed window ⇒ 400 with the
  house ``{"error": ...}`` envelope;
* ``limit`` default 100, clamp cap 500 (422 outside 1..500);
* evidence — seeded finding returns ≥1 sample + ≥1 contributor;
  unknown id ⇒ 404; T2/T3 re-derivation through the P3 joins;
* degrade-never-503 (D11) — monitor absent / disabled / kill-switch /
  forced internal failure: the LIST returns 200 empty shape, evidence
  returns 404; no 503 anywhere;
* marker stability — ids stable + ordering deterministic across
  consecutive polls (the FE's 8 s poll contract);
* FE-contract parity pin — the response models' field names must
  match ``frontend/src/app/models/resources.models.ts`` (``Anomaly`` /
  ``AnomaliesPayload`` / ``EvidenceContributor`` / ``EvidencePayload``)
  exactly;
* traceback fill — a seeded engine's journal appears in
  ``/traceback``'s reserved ``findings[]`` key; absent engine ⇒ ``[]``
  (the P3 shape verbatim).

FENCE (2026-10-12 convention): tmp_path SQLite + synthetic tables +
FAKE_HOME escape guard; never live-shaped paths; no live DB.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.models.app_settings import AppSetting
from daemon.models.resource_monitor import (
    ResourceDaemonBucket,
    ResourceInstanceSample,
    ResourcePidMap,
)
from daemon.repositories.task.models import Task as TaskModel
from daemon.routers.resources import (
    AnomaliesResponse,
    EvidenceContributor,
    EvidenceResponse,
    ResourceAnomaly,
    router as resources_router,
)
from daemon.services.resource_anomaly import (
    AnomalyEngine,
    SeriesStateStore,
)


# ──────────────────────────────────────────────────────────────────────────
# Fence — never resolve live-shaped paths (incident class 2026-10-12)
# ──────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session", autouse=True)
def _fence_session():
    os.environ["ENSEMBLE_SELF_ENV"] = "dev"
    allowed_dbs = {"ensemble_dev_test_only", "ensemble_test_only"}
    if os.environ.get("POSTGRES_DB") in (None, "", "ensemble_prod"):
        os.environ["POSTGRES_DB"] = "ensemble_dev_test_only"
    elif os.environ.get("POSTGRES_DB") not in allowed_dbs:
        pytest.exit(
            f"HARD ABORT: POSTGRES_DB={os.environ.get('POSTGRES_DB')!r}",
            returncode=1,
        )
    fake = os.environ.get("FAKE_HOME")
    if not fake or not os.path.isabs(fake):
        fake = tempfile.mkdtemp(prefix="ens-fake-home-")
        os.environ["FAKE_HOME"] = fake
        os.environ["HOME"] = fake
    yield


# ──────────────────────────────────────────────────────────────────────────
# Harness — a seeded engine + app
# ──────────────────────────────────────────────────────────────────────────

_RECENT = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
    minutes=30
)


def _ts(minutes_ago: int) -> str:
    return (
        (_RECENT - timedelta(minutes=minutes_ago)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )


def _seeded_engine(engine=None) -> AnomalyEngine:
    """An engine whose journal holds ONE spike finding (recent ts so
    every window filter includes it) + a real tmp DB for the T2/T3
    evidence reads."""
    store = SeriesStateStore(bucket_s=60, window_s=3600)
    for i in range(12, 0, -1):
        store.observe("daemon.rss_kb", _ts(i + 2), 1000.0, "4|4")
    store.observe("daemon.rss_kb", _ts(1), 2000.0, "4|4")   # spike
    store.observe("daemon.rss_kb", _ts(0), 1000.0, "4|4")   # recovery
    return AnomalyEngine(
        store=store,
        db_engine=engine,
        mad_threshold=6.0,
        min_baseline_points=5,
    )


def _evidence_db(tmp_path):
    """tmp SQLite with T2 + T3 + task rows for the evidence re-derive."""
    db = create_engine(
        f"sqlite:///{tmp_path / 'anomalies_router.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(
        db,
        tables=[
            ResourceInstanceSample.__table__,
            ResourcePidMap.__table__,
            ResourceDaemonBucket.__table__,
            TaskModel.__table__,
            AppSetting.__table__,
        ],
    )
    from sqlmodel import Session

    with Session(db) as session:
        session.add(ResourceInstanceSample(
            instance_id="i-a", bucket_ts=_ts(20), agent_id="dev",
            status="idle", charged_bytes=0, estimated=True,
        ))
        session.add(ResourceInstanceSample(
            instance_id="i-a", bucket_ts=_ts(1), agent_id="dev",
            status="idle", charged_bytes=8000, estimated=True,
        ))
        session.add(ResourcePidMap(
            pid=111, kind="chart_render",
            command="chromium --headless", instance_id="i-a",
            spawned_at=_ts(10), peak_rss_kb=262144,
        ))
        session.add(TaskModel(
            instance_id="i-a", work_id="w-a", status="running",
            created_at=_RECENT - timedelta(hours=1),
        ))
        session.commit()
    return db


class _Monitor:
    def __init__(self, anomaly, enabled=True):
        self.anomaly = anomaly
        self.enabled = enabled


class _FakeManager:
    def __init__(self, engine):
        self.engine = engine


def _app(engine, monitor) -> FastAPI:
    app = FastAPI()
    app.include_router(resources_router, prefix="/api")
    app.state.manager = _FakeManager(engine) if engine else None
    app.state.resource_monitor = monitor
    return app


@pytest.fixture
def seeded(tmp_path):
    db = _evidence_db(tmp_path)
    eng = _seeded_engine(db)
    assert eng.run_pass() == 1
    app = _app(db, _Monitor(eng))
    return app, eng


# ──────────────────────────────────────────────────────────────────────────
# List shape + params
# ──────────────────────────────────────────────────────────────────────────


class TestAnomaliesList:
    def test_exact_payload_shape(self, seeded):
        app, _ = seeded
        r = TestClient(app).get("/api/resources/anomalies")
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"anomalies"}  # EXACTLY this — no truncated flag
        row = body["anomalies"][0]
        assert set(row) == {
            "id", "kind", "t_start", "t_end", "peak", "baseline",
            "severity", "series_key", "shape", "instance_id",
        }
        assert row["kind"] == "memory"
        assert row["severity"] in ("low", "medium", "high")
        assert row["shape"] == "spike"
        assert row["baseline"] < row["peak"]

    def test_default_window_is_1h(self, seeded):
        """The recent-seeded finding (≤30 min old) appears under the
        DEFAULT window (1h) with no explicit param."""
        app, _ = seeded
        r = TestClient(app).get("/api/resources/anomalies")
        assert r.status_code == 200
        assert len(r.json()["anomalies"]) == 1

    def test_window_enum_and_400_envelope(self, seeded):
        app, _ = seeded
        c = TestClient(app)
        for w in ("15m", "1h", "24h", "7d"):
            assert c.get(
                "/api/resources/anomalies", params={"window": w}
            ).status_code == 200
        r = c.get("/api/resources/anomalies", params={"window": "3h"})
        assert r.status_code == 400
        assert "error" in r.json()["detail"]

    def test_limit_clamp(self, seeded):
        app, eng = seeded
        # Pad the journal past the clamp so ordering/caps are real.
        store = eng.store
        for n in range(3):
            for i in range(12, 0, -1):
                store.observe(
                    f"daemon.threads", _ts(i + 2 + n), 40.0, "4|4"
                )
            store.observe("daemon.threads", _ts(1), 400.0, "4|4")
            store.observe("daemon.threads", _ts(0), 40.0, "4|4")
        eng.run_pass()
        c = TestClient(app)
        r = c.get("/api/resources/anomalies", params={"limit": 2})
        assert r.status_code == 200
        assert len(r.json()["anomalies"]) == 2
        assert c.get(
            "/api/resources/anomalies", params={"limit": 0}
        ).status_code == 422
        assert c.get(
            "/api/resources/anomalies", params={"limit": 501}
        ).status_code == 422

    def test_window_filters_old_findings(self, seeded):
        app, eng = seeded
        c = TestClient(app)
        # The recent seed (≤30 min old) is inside 1h, and a 3 h-old
        # finding is EXCLUDED from 15m/1h but included under 7d.
        old = c.get("/api/resources/anomalies", params={"window": "1h"})
        assert old.status_code == 200
        assert len(old.json()["anomalies"]) == 1
        # Backdate a finding 3 h via a fresh store.
        store = SeriesStateStore(bucket_s=60, window_s=3600)
        for i in range(12, 0, -1):
            store.observe(
                "daemon.fds",
                (datetime.now(timezone.utc).replace(tzinfo=None)
                 - timedelta(hours=3, minutes=i)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                10.0, "4|4",
            )
        store.observe(
            "daemon.fds",
            (datetime.now(timezone.utc).replace(tzinfo=None)
             - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S"),
            200.0, "4|4",
        )
        store.observe(
            "daemon.fds",
            (datetime.now(timezone.utc).replace(tzinfo=None)
             - timedelta(hours=3) + timedelta(minutes=1)).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            10.0, "4|4",
        )
        eng2 = AnomalyEngine(
            store=store, db_engine=None, mad_threshold=6.0,
            min_baseline_points=5,
        )
        eng2.run_pass()
        app.state.resource_monitor = _Monitor(eng2)
        r15 = c.get("/api/resources/anomalies", params={"window": "15m"})
        r7d = c.get("/api/resources/anomalies", params={"window": "7d"})
        assert r15.json()["anomalies"] == []  # 3 h old — outside 15m
        assert len(r7d.json()["anomalies"]) == 1  # inside 7d

    def test_marker_stability_across_polls(self, seeded):
        """Ids stable + ordering deterministic (t_start, series_key)
        across consecutive calls — the FE's 8 s poll contract."""
        app, eng = seeded
        store = eng.store
        for i in range(12, 0, -1):
            store.observe("daemon.fds", _ts(i + 2), 10.0, "4|4")
        store.observe("daemon.fds", _ts(1), 200.0, "4|4")
        store.observe("daemon.fds", _ts(0), 10.0, "4|4")
        eng.run_pass()
        c = TestClient(app)
        a = c.get("/api/resources/anomalies").json()["anomalies"]
        b = c.get("/api/resources/anomalies").json()["anomalies"]
        assert [x["id"] for x in a] == [x["id"] for x in b]
        keys = [(x["t_start"], x["series_key"]) for x in a]
        assert keys == sorted(keys)
        # The list row and the evidence row carry the SAME id.
        r = c.get(f"/api/resources/anomalies/{a[0]['id']}/evidence")
        assert r.status_code == 200
        assert r.json()["anomaly"]["id"] == a[0]["id"]


# ──────────────────────────────────────────────────────────────────────────
# Evidence
# ──────────────────────────────────────────────────────────────────────────


class TestEvidence:
    def test_seeded_finding_full_sections(self, seeded):
        app, _ = seeded
        fid = TestClient(app).get(
            "/api/resources/anomalies"
        ).json()["anomalies"][0]["id"]
        r = TestClient(app).get(
            f"/api/resources/anomalies/{fid}/evidence"
        )
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {
            "anomaly", "samples", "top_pids_per_bucket",
            "children_delta", "contributors",
        }
        assert len(body["samples"]) >= 1
        assert any(s["value"] == 2000.0 for s in body["samples"])
        # T2/T3 re-derivation through the P3 joins:
        assert any(
            p.get("kind") == "chart_render"
            for p in body["top_pids_per_bucket"]
        )
        assert len(body["contributors"]) >= 1
        named = [
            c for c in body["contributors"] if not c.get("unattributed")
        ]
        assert named and named[0]["instance_id"] == "i-a"
        assert (
            named[0]["evidence"]["task_status"]["status"] == "running"
        )
        assert sum(c["share_pct"] for c in body["contributors"]) <= 100.0

    def test_unknown_id_404(self, seeded):
        app, _ = seeded
        r = TestClient(app).get(
            "/api/resources/anomalies/anom-999999/evidence"
        )
        assert r.status_code == 404
        assert "error" in r.json()["detail"]

    def test_internal_failure_404_never_503(self, seeded):
        app, eng = seeded
        fid = eng.journal.snapshot()[0].id

        def boom(unknown_id):
            raise RuntimeError("db gone")

        eng.build_evidence = boom  # type: ignore[method-assign]
        r = TestClient(app).get(
            f"/api/resources/anomalies/{fid}/evidence"
        )
        assert r.status_code == 404
        assert "error" in r.json()["detail"]


# ──────────────────────────────────────────────────────────────────────────
# Degrade-never-503 (D11)
# ──────────────────────────────────────────────────────────────────────────


class TestDegradeNever503:
    def test_monitor_absent(self):
        app = _app(None, None)
        r = TestClient(app).get("/api/resources/anomalies")
        assert r.status_code == 200
        assert r.json() == {"anomalies": []}
        r = TestClient(app).get("/api/resources/anomalies/x/evidence")
        assert r.status_code == 404

    def test_monitor_disabled(self):
        app = _app(None, _Monitor(None, enabled=False))
        r = TestClient(app).get("/api/resources/anomalies")
        assert r.status_code == 200
        assert r.json() == {"anomalies": []}

    def test_kill_switch_engine_none(self):
        app = _app(None, _Monitor(None, enabled=True))
        r = TestClient(app).get("/api/resources/anomalies")
        assert r.status_code == 200
        assert r.json() == {"anomalies": []}

    def test_list_internal_failure_200_empty(self, seeded):
        app, eng = seeded

        def boom(window_s, limit):
            raise RuntimeError("engine exploded")

        eng.list_anomalies = boom  # type: ignore[method-assign]
        r = TestClient(app).get("/api/resources/anomalies")
        assert r.status_code == 200
        assert r.json() == {"anomalies": []}

    def test_no_503_anywhere(self, seeded):
        app, eng = seeded
        c = TestClient(app)
        eng.list_anomalies = (  # type: ignore[method-assign]
            lambda w, l: (_ for _ in ()).throw(RuntimeError("x"))
        )
        eng.build_evidence = (  # type: ignore[method-assign]
            lambda i: (_ for _ in ()).throw(RuntimeError("x"))
        )
        assert c.get("/api/resources/anomalies").status_code == 200
        assert c.get(
            "/api/resources/anomalies/some-id/evidence"
        ).status_code == 404


# ──────────────────────────────────────────────────────────────────────────
# FE-contract parity pin
# ──────────────────────────────────────────────────────────────────────────


class TestFEContractParity:
    """Cross-checked against
    ``frontend/src/app/models/resources.models.ts`` (``Anomaly`` /
    ``AnomaliesPayload`` / ``EvidenceContributor`` / ``EvidencePayload``
    — the FE e2e stub plays the same shapes). A rename on either side
    must fail one of these pins."""

    def test_payload_keys(self):
        assert list(AnomaliesResponse.model_fields) == ["anomalies"]
        assert list(EvidenceResponse.model_fields) == [
            "anomaly", "samples", "top_pids_per_bucket",
            "children_delta", "contributors",
        ]

    def test_anomaly_keys_exact(self):
        assert list(ResourceAnomaly.model_fields) == [
            "id", "kind", "t_start", "t_end", "peak", "baseline",
            "severity", "series_key", "shape", "instance_id",
        ]
        # id/kind/t_start/t_end/peak/baseline/severity/series_key/shape
        # are REQUIRED (the FE's non-optional Anomaly fields);
        # instance_id is the sole optional (FE `instance_id?`).
        for f in ("id", "kind", "t_start", "t_end", "peak", "baseline",
                  "severity", "series_key", "shape"):
            assert ResourceAnomaly.model_fields[f].is_required(), f
        assert not ResourceAnomaly.model_fields["instance_id"].is_required()

    def test_contributor_keys(self):
        assert list(EvidenceContributor.model_fields) == [
            "instance_id", "agent_id", "work_id", "share_pct",
            "evidence", "unattributed",
        ]
        assert EvidenceContributor.model_fields["share_pct"].is_required()
        assert not EvidenceContributor.model_fields[
            "instance_id"
        ].is_required()
        assert not EvidenceContributor.model_fields[
            "unattributed"
        ].is_required()

    def test_ts_parity_pinned_fields_in_FE_source(self):
        """Strengthen the FE contract pin (#6): a TS-side rename of an
        interface field fails this test, not just a PY-side rename.

        Reads ``frontend/src/app/models/resources.models.ts`` and
        asserts every pinned PY field name appears in the
        corresponding TS interface block. Read-only file access —
        no FE modification; the pin lives in the BE test pack per
        the dispatch's prescribed-findings directive. The TS file
        path is derived from this test file's ``__file__`` (the
        worktree-relative convention).
        """
        import re as _re
        from pathlib import Path as _Path

        ts_path = (
            _Path(__file__).resolve().parents[3]
            / "frontend" / "src" / "app" / "models" / "resources.models.ts"
        )
        src = ts_path.read_text(encoding="utf-8")

        def _block(name: str) -> str:
            """Extract the body of ``export interface Name { ... }``."""
            m = _re.search(
                rf"export interface {name}\s*\{{(?P<body>.*?)\n\}}",
                src,
                _re.DOTALL,
            )
            assert m is not None, f"TS interface {name!r} not found"
            return m.group("body")

        # Anomaly: every PY-required field name appears as a TS field
        # declaration. Word-boundary regex (NOT substring) — `id` is
        # NOT contained in `instance_id`, so the pin catches renames
        # without false positives from cross-field overlaps.
        anomaly_body = _block("Anomaly")
        for field_name in (
            "id", "kind", "t_start", "t_end", "peak", "baseline",
            "severity", "series_key", "shape",
        ):
            assert _re.search(
                rf"^\s*{_re.escape(field_name)}\s*[?:]", anomaly_body,
                _re.MULTILINE,
            ), (
                f"Anomaly TS interface missing field {field_name!r}"
            )
        # instance_id is the sole optional (FE ``instance_id?``).
        assert _re.search(
            r"^\s*instance_id\?\s*:", anomaly_body, _re.MULTILINE,
        ), "Anomaly TS interface missing optional instance_id?"

        # AnomaliesPayload — exact key.
        body = _block("AnomaliesPayload")
        assert _re.search(r"^\s*anomalies\s*[?:]", body, _re.MULTILINE), (
            "AnomaliesPayload TS interface missing 'anomalies'"
        )

        # EvidencePayload — every PY field name appears.
        evidence_body = _block("EvidencePayload")
        for field_name in (
            "anomaly", "samples", "top_pids_per_bucket",
            "children_delta", "contributors",
        ):
            assert _re.search(
                rf"^\s*{_re.escape(field_name)}\s*[?:]", evidence_body,
                _re.MULTILINE,
            ), (
                f"EvidencePayload TS interface missing field {field_name!r}"
            )

        # EvidenceContributor — every PY field name appears; the
        # optional ones carry ``?`` in TS. The required ones use the
        # bare name (NOT ``?``); the optional ones use ``?``.
        contrib_body = _block("EvidenceContributor")
        for field_name in ("share_pct",):
            assert _re.search(
                rf"^\s*{_re.escape(field_name)}\s*:", contrib_body,
                _re.MULTILINE,
            ), (
                f"EvidenceContributor TS interface missing required "
                f"field {field_name!r}"
            )
        for opt in ("instance_id", "agent_id", "work_id", "unattributed"):
            assert _re.search(
                rf"^\s*{_re.escape(opt)}\?\s*:", contrib_body,
                _re.MULTILINE,
            ), (
                f"EvidenceContributor TS interface missing optional {opt}?"
            )
        # ``evidence?`` is the contract — optional on PY; the TS body
        # MUST carry it as ``evidence?`` (the evidence sub-object).
        assert _re.search(
            r"^\s*evidence\?\s*:", contrib_body, _re.MULTILINE,
        ), "EvidenceContributor TS interface missing 'evidence?'"


# ──────────────────────────────────────────────────────────────────────────
# Traceback findings fill (D5)
# ──────────────────────────────────────────────────────────────────────────


class TestTracebackFindingsFill:
    def test_seeded_journal_fills_findings_key(self, seeded):
        app, eng = seeded
        lean = eng.journal.snapshot()[0].lean_row()
        t0 = _ts(30)
        t1 = _ts(0)
        r = TestClient(app).get(
            "/api/resources/traceback", params={"t0": t0, "t1": t1}
        )
        assert r.status_code == 200
        body = r.json()
        assert "findings" in body
        assert any(f["id"] == lean["id"] for f in body["findings"])
        assert body["findings"][0] == lean  # the lean row, verbatim

    def test_absent_engine_empty_findings(self, tmp_path):
        db = _evidence_db(tmp_path)
        app = _app(db, None)  # no monitor → provider None → []
        r = TestClient(app).get(
            "/api/resources/traceback",
            params={"t0": _ts(30), "t1": _ts(0)},
        )
        assert r.status_code == 200
        assert r.json()["findings"] == []

    def test_findings_in_window_keeps_newest_under_full_journal(self, tmp_path):
        """#8 — under a full journal (> limit), the NEWEST findings
        in window are kept (the old code dropped them in favor of the
        oldest journal entries).

        Seed a journal with 60 distinct (time_bucket, series_key)
        findings, ask for limit=50, assert the LAST 50 by
        (t_start, series_key) come back — not the FIRST 50.
        """
        from daemon.services.resource_anomaly import Finding

        eng = _seeded_engine()
        # Empty the journal first (the seed fixture ran run_pass which
        # journaled 1 finding).
        eng.journal = eng.journal.__class__()
        n = 60
        limit = 50
        for i in range(n):
            f = Finding(
                id="",
                series_key=f"s.{i}",
                time_bucket=_ts(n - i),  # ASCENDING order in time
                window_end=_ts(n - i),
                shape="spike",
                severity="low",
                peak=1.0,
                baseline={"method": "rolling_median", "value": 1.0, "n": 1},
                observed=2.0,
                deviation={"metric": "mad_z", "value": 7.0, "threshold": 6.0},
                contributors=[],
            )
            eng.journal.append(f)
        # Wide window so all 60 are in-window.
        out = eng.findings_in_window(
            datetime(2020, 1, 1, tzinfo=timezone.utc),
            datetime(2030, 1, 1, tzinfo=timezone.utc),
            limit=limit,
        )
        assert len(out) == limit
        # ASC by (t_start, series_key); the LAST limit (= newest) are
        # kept. Oldest retained series_key indices: 0..10 (n-limit=10).
        # Newest retained series_key indices: 11..59.
        series_keys = [o["series_key"] for o in out]
        assert series_keys == [f"s.{i}" for i in range(10, 60)], (
            f"expected the NEWEST {limit} findings (s.10..s.59); "
            f"got {series_keys[:5]}...{series_keys[-5:]}"
        )

"""``resource_anomaly`` unit pack (P4 D6).

Covers the phase4-plan acceptance surface:

* A1  — bounded state: the bounds test DERIVES the bucket count from
  the store's configured caps (no literal 48) and asserts series +
  DoW baseline < 1_048_576 B and journal < 262_144 B with the numbers
  printed in the assert message.
* A2  — THE mandated FP-avoidance test
  (``test_worker_count_increase_is_not_anomaly``): (A) worker count
  4→5 with a +30 % RSS rise ⇒ NO finding; (B) the identical rise at
  CONSTANT workers + limit ⇒ finding FIRES; (C) the CONFIGURED limit
  ``limits.llm_concurrency`` 4→6 through the MANAGED-SETTING shape
  (app_settings DB row over the resolver ladder), observed count
  follows, RSS rises ⇒ NO finding.
* A3  — all four shapes detected + classified; negative controls (a
  spike misclassified as a leak FAILS the pack); per-detector
  positive/threshold-negative/degenerate (flat, all-zero, NaN) cases
  without exception.
* A4  — explainability completeness: every journaled finding carries
  ALL of ``{baseline, observed, deviation, contributors}``.
* A5  — restart-recompute: a replayed engine regenerates equivalent
  findings (shape / baseline / observed / deviation equal,
  id-insensitive) — from in-memory rows AND from a seeded DB minute
  tier.
* A6  — scheduler: interval gate, skip-on-overrun (+ missed counter),
  ingest-every-window, journal never exceeds maxlen.
* Contributors: ranked shares through the P3 joins, sums ≤100 with
  the explicit ``unattributed`` remainder row, and T2 ``status`` is
  NEVER read as aliveness (P3 adjudication B — the contributor's task
  status comes from the task join, not the sample row).

FENCE (2026-10-12 convention): file-SQLite at pytest ``tmp_path`` +
FAKE_HOME escape guard; never live-shaped paths; no live DB; no new
runtime deps.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from daemon.services.resource_anomaly import (
    CUSUM_THRESHOLD,
    JOURNAL_MAX_BYTES,
    JOURNAL_MAXLEN,
    MAX_CONTRIBUTORS,
    STATE_FLOATS,
    FLOAT_BYTES,
    AnomalyEngine,
    AnomalyScheduler,
    FindingJournal,
    SeriesStateStore,
    build_contributors,
    classify_shape,
    detect_leak,
    detect_ramp,
    detect_spike,
    detect_step,
    kind_for_series,
    mad_z,
    make_configured_limit_provider,
    severity_band,
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
# Helpers
# ──────────────────────────────────────────────────────────────────────────


def _ts(i: int) -> str:
    """Minute-resolution naive-UTC bucket ts (test clock base 10:00)."""
    return f"2026-10-18 10:{i:02d}:00"


def _feed_flat_then(
    store: SeriesStateStore,
    flat_n: int,
    tail_values: list[float],
    workers: str = "4|4",
    series: str = "daemon.rss_kb",
    base: float = 1000.0,
    step: float = 0.0,
):
    """Feed ``flat_n`` flat buckets then the tail values (one per minute)."""
    for i in range(flat_n):
        store.observe(series, _ts(i), base + step * i, workers)
    for j, v in enumerate(tail_values):
        store.observe(series, _ts(flat_n + j), v, workers)


def _engine(store: SeriesStateStore, **kw) -> AnomalyEngine:
    kw.setdefault("mad_threshold", 6.0)
    kw.setdefault("min_baseline_points", 5)
    return AnomalyEngine(store=store, db_engine=None, **kw)


def _minute_row(i: int, **fields) -> dict:
    row = {"bucket_ts": _ts(i)}
    row.update(fields)
    return row


# ──────────────────────────────────────────────────────────────────────────
# A1 — bounded state
# ──────────────────────────────────────────────────────────────────────────


class TestBoundedState:
    def test_bounds_derived_from_config_under_1mb(self):
        """A1 — the §2.7 arithmetic derived from the CONFIGURED caps.

        Every number is derived (store.window_buckets / max_series /
        dow_hour_slots / maxlen) — no literal 48; the counts are
        printed in the assert message.
        """
        store = SeriesStateStore()  # production defaults
        journal = FindingJournal()
        # Worst-case fill: max_series series × window_buckets buckets.
        per_series_bytes = store.window_buckets * STATE_FLOATS * FLOAT_BYTES
        dow_bytes = store.dow_hour_slots * FLOAT_BYTES
        expected = store.max_series * (per_series_bytes + dow_bytes)
        assert expected < 1_048_576, (
            f"series+baseline worst case {expected} B must stay under "
            f"1_048_576 B (caps: max_series={store.max_series}, "
            f"window_buckets={store.window_buckets}, "
            f"dow_hour_slots={store.dow_hour_slots}, "
            f"per_series={per_series_bytes + dow_bytes} B)"
        )
        # The real accounting after ACTUALLY filling to the cap must
        # match the derived bound (state_bytes over the live store).
        # Buckets are spaced one STRIDE apart so each observation
        # lands in its own stride bucket (48 distinct buckets).
        from datetime import datetime, timedelta

        t0 = datetime(2026, 10, 18, 0, 0, 0)
        for s in range(store.max_series):
            for i in range(store.window_buckets):
                ts = (t0 + timedelta(seconds=i * store.bucket_s)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                store.observe(f"series.{s}", ts, 1.0 + s + i)
        # Fill the DoW×hour maps to their slot cap too (the derived
        # worst case assumes BOTH structures full): 7×24 distinct
        # (dow, hour) keys per series, fed at stride spacing.
        for s_i in range(store.max_series):
            for slot in range(store.dow_hour_slots):
                ts = (
                    t0 + timedelta(seconds=slot * 3600)
                ).strftime("%Y-%m-%d %H:%M:%S")
                store.observe(f"series.{s_i}", ts, 1.0 + slot)
        actual = store.state_bytes()
        assert actual == expected, (
            f"state_bytes() {actual} != derived bound {expected}"
        )
        assert actual < 1_048_576, (
            f"live store {actual} B must stay under 1_048_576 B"
        )
        # Fill the journal with HEAVY findings (8 ranked contributors
        # + evidence each) — the measured total must stay STRICTLY
        # under the 256 KB byte budget (enforced by construction).
        from daemon.services.resource_anomaly import Finding

        heavy_contribs = [
            {
                "instance_id": f"i-{n:03d}",
                "agent_id": "developer",
                "work_id": f"w-{n:03d}",
                "share_pct": 1.5,
                "evidence": {
                    "samples": [{"t": _ts(i), "value": 1234.5}],
                    "task_status": {"status": "running"},
                },
            }
            for n in range(8)
        ]
        for i in range(512):  # far more than maxlen
            j = Finding(
                id="", series_key=f"s.{i}", time_bucket=_ts(i % 60),
                window_end=_ts(i % 60), shape="leak", severity="medium",
                peak=2.0, baseline={"method": "rolling_median",
                                    "value": 1.0, "n": 48},
                observed=2.0,
                deviation={"metric": "cusum_k", "value": 9.0,
                           "threshold": 5.0},
                contributors=list(heavy_contribs),
            )
            journal.append(j)
        measured = journal.total_bytes()
        assert measured < 262_144, (
            f"journal measured total {measured} B must stay STRICTLY "
            f"under 262_144 B (budget={JOURNAL_MAX_BYTES}, "
            f"maxlen={JOURNAL_MAXLEN}, "
            f"entries={len(journal.snapshot())})"
        )
        assert len(journal.snapshot()) <= JOURNAL_MAXLEN

    def test_series_cap_drop_oldest(self):
        store = SeriesStateStore(max_series=3, window_s=600, bucket_s=60)
        for name in ("a", "b", "c"):
            store.observe(f"s.{name}", "2026-10-18 10:00:00", 1.0)
        assert sorted(k.split(".")[1] for k in store.series_keys()) == [
            "a", "b", "c",
        ]
        # Over cap → the LEAST-RECENTLY-UPDATED series ("a") is evicted.
        store.observe("s.d", "2026-10-18 10:05:00", 1.0)
        keys = store.series_keys()
        assert "s.a" not in keys and "s.d" in keys and len(keys) == 3

    def test_same_bucket_reingest_replaces(self):
        """Idempotency — the restart replay can never double-count."""
        store = SeriesStateStore(bucket_s=60, window_s=600)
        store.observe("x", "2026-10-18 10:00:00", 10.0)
        n = len(store.states("x"))
        store.observe("x", "2026-10-18 10:00:30", 20.0)  # same bucket
        states = store.states("x")
        assert len(states) == n == 1
        assert states[-1].value == 20.0  # last-wins

    def test_stride_downsample_last_wins(self):
        store = SeriesStateStore(bucket_s=300, window_s=3600)
        # Four 60s-spaced minute rows land in ONE 5-min stride bucket.
        for i, v in enumerate((1.0, 2.0, 3.0, 4.0)):
            store.observe("x", f"2026-10-18 10:0{i}:00", v)
        states = store.states("x")
        assert len(states) == 1 and states[0].value == 4.0

    def test_nan_inf_and_bad_ts_skipped(self):
        store = SeriesStateStore(bucket_s=60, window_s=600)
        assert store.observe("x", "2026-10-18 10:00:00", float("nan")) is False
        assert store.observe("x", "2026-10-18 10:00:00", float("inf")) is False
        assert store.observe("x", "not-a-ts", 1.0) is False
        assert store.observe("x", "", 1.0) is False
        assert store.states("x") == []


# ──────────────────────────────────────────────────────────────────────────
# A2 — the mandated conditioning test (the leader's acceptance gate)
# ──────────────────────────────────────────────────────────────────────────


class TestWorkerCountConditioning:
    @staticmethod
    def _workers_provider(value):
        return lambda: value

    def _run(self, strata: list[str], values: list[float]) -> int:
        """Feed one value per minute under the matching stratum, run a
        pass, return the finding count."""
        store = SeriesStateStore(bucket_s=60, window_s=3600)
        for i, (v, w) in enumerate(zip(values, strata)):
            store.observe("daemon.rss_kb", _ts(i), v, w)
        engine = _engine(store)
        return engine.run_pass()

    def test_worker_count_increase_is_not_anomaly(self):
        """THE mandated FP-avoidance test (phase4-plan task 5, A2).

        (A) workers 4→5, RSS +30 % ⇒ NO finding (conditioned baseline
        absorbs the stratum switch);
        (B) identical magnitude rise at CONSTANT workers+limit ⇒
        finding FIRES (same detectors, same thresholds);
        (C) configured limit 4→6 via the MANAGED-SETTING shape
        (app_settings row over the resolver ladder), observed count
        follows, RSS rises ⇒ NO finding.
        """
        flat = [1000.0] * 12
        risen = [1300.0] * 4
        # A — worker count 4→5 (stratum "5|4" from the rise on).
        strata_a = ["4|4"] * 12 + ["5|4"] * 4
        assert self._run(strata_a, flat + risen) == 0, (
            "variant A: a worker-count rise must re-baseline, never flag"
        )
        # B — identical rise at constant workers+limit.
        strata_b = ["4|4"] * 16
        assert self._run(strata_b, flat + risen) >= 1, (
            "variant B: the identical rise at constant stratum must fire"
        )
        # C — configured limit 4→6 through the managed-setting shape.
        engine = self._run_variant_c()
        assert engine == 0, (
            "variant C: a configured-limit change must re-baseline, "
            "never flag"
        )

    def _run_variant_c(self) -> int:
        """Variant C — the REAL managed-setting read path.

        The configured-limit provider is the module's
        ``make_configured_limit_provider`` over a tmp SQLite with an
        ``app_settings`` row: ``limits.llm_concurrency`` = 4 for the
        baseline phase, then the row flips to 6 (the operator's
        managed flip) and the OBSERVED worker count follows (5) while
        RSS rises +30 % ⇒ the stratum changes on BOTH reads ⇒ no
        finding.
        """
        from daemon.models.app_settings import AppSetting
        from sqlmodel import SQLModel

        engine_db = create_engine(
            "sqlite://",  # in-memory per-connection? No — StaticPool below
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine_db, tables=[AppSetting.__table__])
        from sqlalchemy import text

        def _set_limit(value: int) -> None:
            with engine_db.begin() as conn:
                conn.execute(
                    text(
                        "INSERT OR REPLACE INTO app_settings "
                        "(key, value_json, value_type, updated_by, "
                        "updated_at) VALUES "
                        "('limits.llm_concurrency', :v, 'int', NULL, "
                        "'2026-10-18T10:00:00+00:00')"
                    ),
                    {"v": json.dumps(value)},
                )

        _set_limit(4)
        provider = make_configured_limit_provider(engine_db)
        assert provider() == 4  # the DB tier wins the ladder

        workers = 5  # observed follows the configured limit
        store = SeriesStateStore(bucket_s=60, window_s=3600)
        for i in range(12):
            store.observe(
                "daemon.rss_kb", _ts(i), 1000.0, f"{workers}|{provider()}"
            )
        _set_limit(6)  # the managed flip — observed follows, RSS rises
        assert provider() == 6
        for j in range(4):
            store.observe(
                "daemon.rss_kb",
                _ts(12 + j),
                1300.0,
                f"{workers}|{provider()}",
            )
        eng = _engine(store)
        return eng.run_pass()


# ──────────────────────────────────────────────────────────────────────────
# Baseline engine (task 2)
# ──────────────────────────────────────────────────────────────────────────


class TestBaselineEngine:
    def test_cold_start_quiet_under_min_points(self):
        store = SeriesStateStore(bucket_s=60, window_s=3600)
        for i in range(6):  # < min_baseline_points (5? no — 6 ≥ 5)
            store.observe("x", _ts(i), 1000.0 + i)
        engine = AnomalyEngine(
            store=store, db_engine=None, min_baseline_points=12
        )
        assert engine.run_pass() == 0  # quiet, not an error
        assert engine.journal.snapshot() == []

    def test_stratum_switch_produces_new_baseline_no_carryover(self):
        store = SeriesStateStore(bucket_s=60, window_s=3600)
        for i in range(10):
            store.observe("x", _ts(i), 1000.0, "4|4")
        for i in range(3):
            store.observe("x", _ts(10 + i), 2000.0, "8|4")
        states = store.states("x")
        new_stratum = [b for b in states if b.workers == "8|4"]
        # The new stratum's FIRST bucket is its own baseline (z = 0) —
        # the old stratum's level does NOT bleed in.
        assert new_stratum[0].baseline == 2000.0
        assert new_stratum[0].cusum_p == 0.0
        # And the old stratum's buckets are untouched.
        assert all(b.baseline == 1000.0 for b in states[:10])

    def test_dow_hour_fallback_for_periodic_workload(self):
        """A periodic rise at a fixed UTC hour keys the DoW×hour map —
        the fallback baseline answers when the stratum history is thin
        (R5: keys read as UTC from the bucket digits)."""
        store = SeriesStateStore(bucket_s=3600, window_s=7 * 24 * 3600)
        # 3 days × the same hour: value 100 at 09:00, 10 elsewhere.
        for day in range(3):
            for hour in range(24):
                ts = (
                    datetime(2026, 10, 12 + day, hour, 0, 0)
                    .strftime("%Y-%m-%d %H:%M:%S")
                )
                store.observe("x", ts, 100.0 if hour == 9 else 10.0, "4|4")
        val, n = store.dow_hour_mean("x", "4|4", (0, 9))  # Monday 09:00 UTC
        assert n == 1 and abs(val - 100.0) < 1e-9
        val, n = store.dow_hour_mean("x", "4|4", (0, 8))
        assert n == 1 and abs(val - 10.0) < 1e-9

    def test_dow_hour_map_capped_at_slots(self):
        store = SeriesStateStore(
            bucket_s=3600, window_s=7 * 24 * 3600, dow_hour_slots=4
        )
        # 5 distinct (workers, dow, hour) keys → the oldest is evicted.
        for w, d, h in [("4|4", 0, 1), ("4|4", 0, 2), ("4|4", 0, 3),
                        ("4|4", 0, 4), ("9|4", 0, 1)]:
            ts = datetime(2026, 10, 12 + d, h, 0, 0).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            store.observe("x", ts, float(h), w)
        st = store._series["x"]
        assert len(st.dow_hour) == 4  # drop-oldest held the cap


# ──────────────────────────────────────────────────────────────────────────
# A3 — detectors (positive / threshold-negative / degenerate)
# ──────────────────────────────────────────────────────────────────────────


class TestDetectors:
    def _states(self, values, workers="4|4"):
        store = SeriesStateStore(bucket_s=60, window_s=3600)
        for i, v in enumerate(values):
            store.observe("x", _ts(i), v, workers)
        return store.states("x")

    def test_mad_z_positive_and_threshold_negative(self):
        states = self._states([1000.0] * 12 + [2000.0, 1000.0])
        z = mad_z(states[-2])
        assert z is not None and z >= 6.0
        z_ok = mad_z(states[-1])
        assert z_ok is not None and z_ok < 6.0  # returned to baseline
        assert detect_spike(states, 6.0) == len(states) - 2
        assert detect_spike(states, 10_000.0) is None  # threshold-negative

    def test_cusum_positive_and_negative(self):
        rising = [1000.0 + 100.0 * i for i in range(16)]
        states = self._states(rising)
        assert states[-1].cusum_p >= CUSUM_THRESHOLD
        flat = self._states([1000.0] * 16)
        assert flat[-1].cusum_p == 0.0

    def test_change_point_positive_flat_tail_and_negative_rising_tail(self):
        step_states = self._states([1000.0] * 12 + [1500.0] * 6)
        assert detect_step(step_states, "4|4") is not None
        leak_states = self._states([1000.0 + 100.0 * i for i in range(16)])
        # A still-rising window is NOT a step (routes to ramp/leak).
        assert detect_step(leak_states, "4|4") is None

    def test_degenerate_series_never_raise(self):
        for values in ([1000.0] * 16, [0.0] * 16, [1.0, 0.0] * 8):
            states = self._states(values)  # includes NaN input below
            for fn, args in (
                (mad_z, (states[-1],)),
                (detect_spike, (6.0,)),
                (detect_step, ("4|4",)),
                (detect_ramp, ()),
                (detect_leak, ()),
            ):
                fn(*args) if fn is mad_z else fn(states, *args)  # no raise
        # NaN VALUES are skipped at the store boundary — the states
        # carry only finite floats.
        store = SeriesStateStore(bucket_s=60, window_s=600)
        store.observe("x", _ts(0), float("nan"))
        store.observe("x", _ts(1), 5.0)
        assert all(
            b.value == b.value for b in store.states("x")  # no NaN
        )

    def test_empty_store_pass_is_quiet(self):
        engine = _engine(SeriesStateStore(bucket_s=60, window_s=600))
        assert engine.run_pass() == 0
        assert engine.counters["passes"] == 1


# ──────────────────────────────────────────────────────────────────────────
# A3 — shape classification (per shape + negative controls)
# ──────────────────────────────────────────────────────────────────────────


class TestShapeClassification:
    def _classify(self, values, fired_index):
        store = SeriesStateStore(bucket_s=60, window_s=3600)
        for i, v in enumerate(values):
            store.observe("x", _ts(i), v, "4|4")
        states = store.states("x")
        return classify_shape(
            states, fired_index=fired_index, workers="4|4", mad_threshold=6.0
        )

    def test_spike(self):
        shape, fallback = self._classify(
            [1000.0] * 12 + [2000.0, 1000.0], 12
        )
        assert shape == "spike" and fallback is False

    def test_step(self):
        shape, fallback = self._classify(
            [1000.0] * 12 + [1500.0] * 6, 12
        )
        assert shape == "step" and fallback is False

    def test_ramp(self):
        shape, fallback = self._classify(
            [1000.0] * 10 + [1100, 1200, 1350, 1500, 1600, 1500, 1400, 1350],
            10,
        )
        assert shape == "ramp" and fallback is False

    def test_leak(self):
        shape, fallback = self._classify(
            [1000.0 + 100.0 * i for i in range(16)], 10
        )
        assert shape == "leak" and fallback is False

    def test_negative_control_spike_is_never_leak(self):
        """A recovering spike classified as leak FAILS this pack."""
        store = SeriesStateStore(bucket_s=60, window_s=3600)
        for i, v in enumerate([1000.0] * 12 + [2000.0, 1000.0]):
            store.observe("x", _ts(i), v, "4|4")
        states = store.states("x")
        shape, _ = classify_shape(
            states, fired_index=12, workers="4|4", mad_threshold=6.0
        )
        assert shape == "spike"
        assert shape != "leak"
        # And the leak DETECTOR rejects the spike series outright.
        assert detect_leak(states) is None

    def test_unknown_combination_conservative_spike_fallback(self):
        # A fired newest-bucket deviation with NO rule match (no
        # recovery data, no sustained shift) → spike + fallback flag.
        shape, fallback = self._classify(
            [1000.0] * 12 + [2000.0], 12
        )
        assert shape == "spike" and fallback is True

    def test_severity_banding_documented(self):
        assert severity_band(6.0, 6.0) == "low"       # ratio 1.0 < 2
        assert severity_band(12.0, 6.0) == "medium"   # ratio 2.0 < 4
        assert severity_band(24.0, 6.0) == "high"     # ratio 4.0 ≥ 4
        assert severity_band(48.0, 6.0) == "high"


# ──────────────────────────────────────────────────────────────────────────
# Journal + finding payload (A4)
# ──────────────────────────────────────────────────────────────────────────


class TestJournalAndPayload:
    def test_journal_never_exceeds_maxlen_drop_oldest(self):
        j = FindingJournal(maxlen=4)
        from daemon.services.resource_anomaly import Finding

        for i in range(10):
            f = Finding(
                id="",
                series_key=f"s.{i}",
                time_bucket=_ts(i),
                window_end=_ts(i),
                shape="spike",
                severity="low",
                peak=1.0,
                baseline={"method": "rolling_median", "value": 1.0, "n": 1},
                observed=2.0,
                deviation={"metric": "mad_z", "value": 7.0, "threshold": 6.0},
                contributors=[],
            )
            assert j.append(f) is True
        snap = j.snapshot()
        assert len(snap) == 4  # bounded
        assert [f.series_key for f in snap] == ["s.6", "s.7", "s.8", "s.9"]
        assert snap[-1].id != snap[0].id  # sequential ids

    def test_journal_dedups_series_bucket(self):
        from daemon.services.resource_anomaly import Finding

        j = FindingJournal()
        f1 = Finding(
            id="", series_key="s", time_bucket=_ts(1), window_end=_ts(1),
            shape="spike", severity="low", peak=1.0,
            baseline={"method": "rolling_median", "value": 1.0, "n": 1},
            observed=2.0,
            deviation={"metric": "mad_z", "value": 7.0, "threshold": 6.0},
            contributors=[],
        )
        f2 = Finding(**{**f1.__dict__, "id": ""})
        assert j.append(f1) is True
        assert j.append(f2) is False  # same (series, bucket) deduped
        assert len(j.snapshot()) == 1

    def test_every_finding_carries_full_explainability(self):
        """A4 — the contract test: a finding missing ANY of
        {baseline, observed, deviation, contributors} fails."""
        store = SeriesStateStore(bucket_s=60, window_s=3600)
        _feed_flat_then(store, 12, [2000.0, 1000.0])
        engine = _engine(store)
        assert engine.run_pass() == 1
        for f in engine.journal.snapshot():
            d = f.full_dict()
            assert {
                "baseline", "observed", "deviation", "contributors",
            } <= set(d), f"finding {f.id} missing explainability keys"
            assert set(d["baseline"]) == {"method", "value", "n"}
            assert d["baseline"]["method"] in (
                "rolling_median", "dow_hour_mean",
            )
            assert set(d["deviation"]) == {"metric", "value", "threshold"}
            assert d["deviation"]["metric"] in ("mad_z", "cusum_k")
            assert d["observed"] == d["observed"]  # finite
        # The lean row flattens baseline to a NUMBER (the FE contract).
        lean = engine.journal.snapshot()[0].lean_row()
        assert isinstance(lean["baseline"], float)

    def test_engine_pass_dedup_across_cycles(self):
        store = SeriesStateStore(bucket_s=60, window_s=3600)
        _feed_flat_then(store, 12, [2000.0, 1000.0])
        engine = _engine(store)
        assert engine.run_pass() == 1
        assert engine.run_pass() == 0  # same (series, bucket) — deduped


# ──────────────────────────────────────────────────────────────────────────
# Kind mapping / instance scoping
# ──────────────────────────────────────────────────────────────────────────


class TestKindAndScoping:
    def test_kind_mapping_deterministic(self):
        assert kind_for_series("daemon.rss_kb") == "memory"
        assert kind_for_series("cgroup.anon_kb") == "memory"
        assert kind_for_series("daemon.cpu_pct") == "cpu"
        assert kind_for_series("daemon.threads") == "threads"
        assert kind_for_series("daemon.fds") == "fds"
        assert kind_for_series("instance.i-1.charged_kb") == "instance_memory"
        assert (
            kind_for_series("instance.i-1.child_rss_kb")
            == "instance_child_memory"
        )

    def test_instance_scoping(self):
        from daemon.services.resource_anomaly import (
            instance_id_for_series,
        )

        assert instance_id_for_series("instance.i-abc.charged_kb") == "i-abc"
        assert instance_id_for_series("daemon.rss_kb") is None


# ──────────────────────────────────────────────────────────────────────────
# Scheduler (A6) — interval gate + skip-on-overrun
# ──────────────────────────────────────────────────────────────────────────


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, s: float) -> None:
        self.now += s


class TestScheduler:
    def _engine_with_pass_counter(self):
        engine = _engine(SeriesStateStore(bucket_s=60, window_s=600))

        original = engine.run_pass

        def slow_pass():
            engine.counters["passes"] = engine.counters.get("passes", 0) + 1
            return original()

        return engine, slow_pass

    def test_interval_gate_fires_at_interval(self):
        engine = _engine(SeriesStateStore(bucket_s=60, window_s=600))
        clock = _FakeClock()
        sched = AnomalyScheduler(
            engine, interval_s=600, budget_s=5.0, wall_clock=clock
        )
        runs = {"n": 0}
        orig = engine.run_pass

        def counting_pass():
            runs["n"] += 1
            return orig()

        engine.run_pass = counting_pass  # type: ignore[method-assign]
        for _ in range(9):  # 9 windows @ 60 s — before the interval
            clock.advance(60.0)
            sched([], [])
        assert runs["n"] == 0
        clock.advance(60.0)  # t=600 — the gate fires
        sched([], [])
        assert runs["n"] == 1

    def test_overrun_skips_next_cycle_and_counts(self):
        engine = _engine(SeriesStateStore(bucket_s=60, window_s=600))
        clock = _FakeClock()
        sched = AnomalyScheduler(
            engine, interval_s=600, budget_s=0.001, wall_clock=clock
        )
        durations = {"d": 0.0}
        orig = engine.run_pass

        def slow_pass():
            clock.advance(durations["d"])  # the pass consumes time
            return orig()

        engine.run_pass = slow_pass  # type: ignore[method-assign]
        clock.advance(600.0)
        durations["d"] = 9.0  # far over the 0.001 budget
        sched([], [])
        assert engine.counters["missed_anomaly_cycles"] == 1
        # The NEXT due cycle is skipped — no second increment.
        clock.advance(600.0)
        sched([], [])
        assert engine.counters["missed_anomaly_cycles"] == 1
        # And the cycle after that runs again (fast now — no overrun).
        durations["d"] = 0.0
        clock.advance(600.0)
        sched([], [])
        assert engine.counters["missed_anomaly_cycles"] == 1
        assert engine.counters["passes"] == 2

    def test_ingest_every_window_even_when_gate_quiet(self):
        engine = _engine(SeriesStateStore(bucket_s=60, window_s=600))
        clock = _FakeClock()
        sched = AnomalyScheduler(
            engine, interval_s=600, budget_s=5.0, wall_clock=clock
        )
        clock.advance(10.0)
        sched(
            [_minute_row(0, rss_kb=1234.0, threads=40, fds=10,
                         anon_kb=5000.0, cpu_user_usec=100,
                         cpu_sys_usec=100)],
            [],
        )
        assert "daemon.rss_kb" in engine.store.series_keys()
        assert engine.counters["passes"] == 0  # gate not due yet

    def test_gate_never_raises(self):
        engine = _engine(SeriesStateStore(bucket_s=60, window_s=600))
        clock = _FakeClock()
        sched = AnomalyScheduler(
            engine, interval_s=1, budget_s=5.0, wall_clock=clock
        )
        engine.run_pass = lambda: (_ for _ in ()).throw(  # type: ignore
            RuntimeError("boom")
        )
        clock.advance(10.0)
        sched([], [])  # must not raise
        assert True


# ──────────────────────────────────────────────────────────────────────────
# A5 — restart-recompute
# ──────────────────────────────────────────────────────────────────────────


# Minute-tier rows spaced one STORE STRIDE (300 s) apart so each row
# lands in its own bucket after the downsample (a rising leak series —
# the replay must regenerate the leak finding).
_REPLAY_BASE = datetime(2026, 10, 18, 10, 0, 0)


class TestRestartRecompute:
    ROWS = [
        {
            "bucket_ts": (
                _REPLAY_BASE + timedelta(seconds=300 * i)
            ).strftime("%Y-%m-%d %H:%M:%S"),
            "rss_kb": 1000.0 + 100.0 * i,
            "threads": 40,
            "fds": 10,
            "anon_kb": 5000.0,
            "cpu_user_usec": 1_000_000 * (i + 1),
            "cpu_sys_usec": 500_000 * (i + 1),
        }
        for i in range(16)
    ]

    def _findings_signature(self, engine: AnomalyEngine):
        return [
            (
                f.shape,
                f.baseline,
                f.observed,
                f.deviation,
                f.series_key,
            )
            for f in engine.journal.snapshot()
        ]

    def test_replay_regenerates_equivalent_findings(self):
        """A5 — post-restart findings match pre-restart on (shape,
        baseline, observed, deviation) — id-insensitive."""
        e1 = AnomalyEngine(
            store=SeriesStateStore(bucket_s=300, window_s=3600),
            db_engine=None, min_baseline_points=5,
        )
        e1.replay_minute_rows(self.ROWS)
        assert e1.run_pass() >= 1
        # "Restart": a fresh engine, fresh journal, SAME series.
        e2 = AnomalyEngine(
            store=SeriesStateStore(bucket_s=300, window_s=3600),
            db_engine=None, min_baseline_points=5,
        )
        e2.replay_minute_rows(self.ROWS)
        assert e2.run_pass() >= 1
        assert self._findings_signature(e1) == self._findings_signature(e2)
        # Ids are process-local: each journal numbers from 1 — the
        # FINDINGS are different objects even when the strings match;
        # equality above deliberately ignores id.

    def test_replay_from_db_minute_tier(self, tmp_path):
        """Cold boot warm: the DB minute tier (interval_s=60) replays
        through the SAME ingest path and regenerates the findings."""
        from daemon.models.resource_monitor import ResourceDaemonBucket
        from sqlmodel import SQLModel

        db = create_engine(
            f"sqlite:///{tmp_path / 'replay.db'}",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(db, tables=[ResourceDaemonBucket.__table__])
        from sqlmodel import Session

        with Session(db) as session:
            for i, row in enumerate(self.ROWS):
                session.add(
                    ResourceDaemonBucket(
                        bucket_ts=row["bucket_ts"],
                        interval_s=60,
                        rss_kb=int(row["rss_kb"]),
                        cpu_user_usec=int(row["cpu_user_usec"]),
                        cpu_sys_usec=int(row["cpu_sys_usec"]),
                        threads=int(row["threads"]),
                        fds=int(row["fds"]),
                        anon_kb=int(row["anon_kb"]),
                    )
                )
            session.commit()

        e_db = AnomalyEngine(
            store=SeriesStateStore(bucket_s=300, window_s=3600),
            db_engine=db, min_baseline_points=5,
        )
        assert e_db.replay_from_db() == len(self.ROWS)
        assert e_db.run_pass() >= 1
        e_mem = AnomalyEngine(
            store=SeriesStateStore(bucket_s=300, window_s=3600),
            db_engine=None, min_baseline_points=5,
        )
        e_mem.replay_minute_rows(self.ROWS)
        e_mem.run_pass()
        assert self._findings_signature(e_db) == self._findings_signature(
            e_mem
        )

    def test_replay_idempotent_no_double_count(self):
        engine = AnomalyEngine(
            store=SeriesStateStore(bucket_s=300, window_s=3600),
            db_engine=None, min_baseline_points=5,
        )
        engine.replay_minute_rows(self.ROWS)
        n1 = len(engine.store.states("daemon.rss_kb"))
        engine.replay_minute_rows(self.ROWS)  # same rows again
        n2 = len(engine.store.states("daemon.rss_kb"))
        assert n1 == n2

    def test_replay_from_db_uses_recent_window(self, tmp_path):
        """#1 — replay selects the RECENT window_buckets*4 rows, not the OLDEST.

        Probe-proven failure: ``ORDER BY bucket_ts ASC LIMIT :lim`` warmed
        the store on stale ~14-day-old data, FP-ing the live level on the
        first pass after a restart (the first new bucket at the live
        current level trips ``mad_z`` against the stale baseline). Fix:
        ``ORDER BY bucket_ts DESC`` + ``rows.reverse()`` before ASC
        ingest so the LIMIT selects the MOST-RECENT window.

        This test seeds > window_buckets*4 rows with a distinct OLD vs
        RECENT split and asserts:

        * (a) **recency** — the ring's newest bucket is close to
          ``base`` (within the 1h window used for the recent rows),
          not stale;
        * (b) **no FP** — observing one more bucket at the recent
          stable level (the natural continuation) and running a pass
          journals NO finding. With the OLD shape, the ring warms on
          stale levels and the new 1000 obs fires ``mad_z=100`` → SPIKE
          FIRES.
        """
        from daemon.models.resource_monitor import ResourceDaemonBucket
        from sqlmodel import SQLModel, Session

        base = datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc)
        # > window_buckets*4 = 192 (default) → SELECT must choose.
        old_n, recent_n = 100, 240
        rows = []
        for i in range(old_n):
            ts = (base - timedelta(minutes=(old_n + recent_n - i)))
            rows.append({
                "bucket_ts": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "rss_kb": 500.0,
                "threads": 40, "fds": 10, "anon_kb": 5000.0,
                "cpu_user_usec": 1_000_000 * (i + 1),
                "cpu_sys_usec": 500_000 * (i + 1),
            })
        for i in range(recent_n):
            ts = (base - timedelta(minutes=(recent_n - i)))
            rows.append({
                "bucket_ts": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "rss_kb": 1000.0,
                "threads": 40, "fds": 10, "anon_kb": 5000.0,
                "cpu_user_usec": 1_000_000 * (old_n + i + 1),
                "cpu_sys_usec": 500_000 * (old_n + i + 1),
            })

        db = create_engine(
            f"sqlite:///{tmp_path / 'recency.db'}",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(
            db, tables=[ResourceDaemonBucket.__table__]
        )
        with Session(db) as session:
            for row in rows:
                session.add(ResourceDaemonBucket(
                    bucket_ts=row["bucket_ts"], interval_s=60,
                    rss_kb=int(row["rss_kb"]),
                    cpu_user_usec=int(row["cpu_user_usec"]),
                    cpu_sys_usec=int(row["cpu_sys_usec"]),
                    threads=int(row["threads"]),
                    fds=int(row["fds"]),
                    anon_kb=int(row["anon_kb"]),
                ))
            session.commit()

        # Defaults: window_buckets=48, bucket_s=300.
        e = AnomalyEngine(
            store=SeriesStateStore(), db_engine=db,
        )
        n = e.replay_from_db()
        assert n == 192  # window_buckets*4

        # (a) RECENCY — the newest bucket in the ring is close to
        # ``base``, not >1h stale. The ring's bucket count is bounded
        # by window_buckets but reduced by the 5-min stride flooring
        # (last-wins within a stride bucket — the store's same-stride
        # re-ingest replacement).
        states = e.store.states("daemon.rss_kb")
        assert states, "replay produced no buckets"
        assert len(states) <= 48  # window_buckets cap
        newest_dt = datetime.strptime(
            states[-1].ts, "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=timezone.utc)
        staleness_min = (base - newest_dt).total_seconds() / 60
        assert staleness_min <= 60 + 5, (
            f"replay warmed on stale data: staleness={staleness_min:.1f}min"
        )

        # (b) NO FP — observe one more recent-stable bucket (the
        # natural continuation that follows a successful warm) and run
        # a pass. NO finding should fire.
        e.store.observe(
            "daemon.rss_kb",
            base.strftime("%Y-%m-%d %H:%M:%S"),
            1000.0, "?|?",
        )
        assert e.run_pass() == 0
        assert e.journal.snapshot() == []


# ──────────────────────────────────────────────────────────────────────────
# Contributors — P3 joins reused, honesty remainder, idle ≠ aliveness
# ──────────────────────────────────────────────────────────────────────────


def _contributor_db(tmp_path, instances: list[tuple[str, float, float]],
                    task_rows: list[dict] | None = None):
    """tmp SQLite with T2 rows (charged deltas) + optional task rows."""
    from daemon.models.resource_monitor import ResourceInstanceSample
    from daemon.repositories.task.models import Task as TaskModel
    from sqlmodel import SQLModel, Session

    db = create_engine(
        f"sqlite:///{tmp_path / 'contrib.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [ResourceInstanceSample.__table__]
    if task_rows:
        tables.append(TaskModel.__table__)
    SQLModel.metadata.create_all(db, tables=tables)
    with Session(db) as session:
        for iid, charged_t0, charged_t1 in instances:
            session.add(ResourceInstanceSample(
                instance_id=iid, bucket_ts="2026-10-18 10:00:00",
                agent_id="dev", status="idle", scan_kind="standard",
                charged_bytes=int(charged_t0), estimated=True,
            ))
            session.add(ResourceInstanceSample(
                instance_id=iid, bucket_ts="2026-10-18 10:20:00",
                agent_id="dev", status="idle", scan_kind="standard",
                charged_bytes=int(charged_t1), estimated=True,
            ))
        for tr in task_rows or []:
            session.add(TaskModel(
                instance_id=tr["instance_id"],
                work_id=tr.get("work_id"),
                status=tr["status"],
                created_at=datetime(2026, 10, 18, 9, 0, 0),
            ))
        session.commit()
    return db


class TestContributors:
    def test_ranked_shares_and_task_status(self, tmp_path):
        db = _contributor_db(
            tmp_path,
            [("i-a", 0.0, 8000.0), ("i-b", 0.0, 2000.0)],
            task_rows=[
                {"instance_id": "i-a", "work_id": "w-a", "status": "running"},
                {"instance_id": "i-b", "work_id": "w-b", "status": "running"},
            ],
        )
        t0 = datetime(2026, 10, 18, 10, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 10, 18, 10, 20, 0, tzinfo=timezone.utc)
        rows = build_contributors(db, "daemon.rss_kb", t0, t1)
        named = [r for r in rows if not r.get("unattributed")]
        assert [r["instance_id"] for r in named] == ["i-a", "i-b"]  # ranked
        assert named[0]["share_pct"] == pytest.approx(80.0, abs=0.05)
        assert named[1]["share_pct"] == pytest.approx(20.0, abs=0.05)
        assert sum(r["share_pct"] for r in rows) <= 100.0 + 1e-6
        assert named[0]["evidence"]["task_status"]["status"] == "running"
        assert named[0]["agent_id"] == "dev"

    def test_unattributed_remainder_when_nothing_resolves(self, tmp_path):
        db = _contributor_db(tmp_path, [])
        t0 = datetime(2026, 10, 18, 10, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 10, 18, 10, 20, 0, tzinfo=timezone.utc)
        rows = build_contributors(db, "daemon.rss_kb", t0, t1)
        assert rows == [{"unattributed": True, "share_pct": 100.0}]

    def test_truncated_tail_folds_into_remainder(self, tmp_path):
        """More charged instances than MAX_CONTRIBUTORS ⇒ the tail's
        share lands in the explicit unattributed row (sums ≤ 100)."""
        instances = [(f"i-{n:02d}", 0.0, 1000.0) for n in range(10)]
        db = _contributor_db(tmp_path, instances)
        t0 = datetime(2026, 10, 18, 10, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 10, 18, 10, 20, 0, tzinfo=timezone.utc)
        rows = build_contributors(db, "daemon.rss_kb", t0, t1)
        named = [r for r in rows if not r.get("unattributed")]
        assert len(named) == MAX_CONTRIBUTORS
        unattr = [r for r in rows if r.get("unattributed")]
        assert len(unattr) == 1
        assert sum(r["share_pct"] for r in rows) <= 100.0 + 1e-6
        assert unattr[0]["share_pct"] > 0

    def test_t2_idle_is_never_aliveness(self, tmp_path):
        """P3 adjudication B — the contributor's task_status comes from
        the TASK join (PROCESSING), never from the T2 sample row's
        sample-time 'idle' label."""
        db = _contributor_db(
            tmp_path,
            [("i-a", 0.0, 5000.0)],  # T2 rows carry status="idle"
            task_rows=[
                {"instance_id": "i-a", "work_id": "w-a",
                 "status": "running"},
            ],
        )
        t0 = datetime(2026, 10, 18, 10, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 10, 18, 10, 20, 0, tzinfo=timezone.utc)
        rows = build_contributors(db, "daemon.rss_kb", t0, t1)
        named = [r for r in rows if not r.get("unattributed")]
        assert len(named) == 1
        assert named[0]["evidence"]["task_status"]["status"] == "running"

    def test_engine_contributors_degrade_to_honest_remainder(self):
        """No DB engine ⇒ the finding still carries the honesty row —
        A4's contributors key is never missing."""
        store = SeriesStateStore(bucket_s=60, window_s=3600)
        _feed_flat_then(store, 12, [2000.0, 1000.0])
        engine = _engine(store)
        engine.run_pass()
        f = engine.journal.snapshot()[0]
        assert f.contributors == [
            {"unattributed": True, "share_pct": 100.0}
        ]


# ──────────────────────────────────────────────────────────────────────────
# Configured-limit provider — the managed-setting shape
# ──────────────────────────────────────────────────────────────────────────


class TestConfiguredLimitProvider:
    def test_db_tier_wins_the_ladder(self, tmp_path):
        from daemon.models.app_settings import AppSetting
        from sqlalchemy import text
        from sqlmodel import SQLModel

        db = create_engine(
            f"sqlite:///{tmp_path / 'limits.db'}",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(db, tables=[AppSetting.__table__])
        provider = make_configured_limit_provider(db)
        # No row ⇒ a lower tier answers (default/env) — never raises.
        v0 = provider()
        assert v0 is None or isinstance(v0, int)
        with db.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_settings (key, value_json, "
                    "value_type, updated_by, updated_at) VALUES "
                    "('limits.llm_concurrency', '6', 'int', NULL, "
                    "'2026-10-18T10:00:00+00:00')"
                )
            )
        assert provider() == 6  # the DB tier beats env/yaml/default
        # READ-ONLY: the provider never writes the table.
        with db.connect() as conn:
            n = conn.execute(
                text("SELECT COUNT(*) FROM app_settings")
            ).scalar()
        assert n == 1

    def test_provider_degrades_to_none_never_raises(self, tmp_path):
        from daemon.services.resource_anomaly import (
            make_configured_limit_provider,
        )

        class _Boom:
            def connect(self, *a, **k):
                raise RuntimeError("no db")

        assert make_configured_limit_provider(_Boom())() is None
        assert make_configured_limit_provider(None)() is None

"""P0 T11 / leg (h) — the F0.1 exposition families through the REAL registry.

These legs need the generalized registry (``daemon/checkpoint_metrics.py``
— the parallel coder's ``register_*`` API). They run LAST because of
that dependency.

Pins:

* the three F0.1 families are registered with the catalog's names,
  labels and bucket set;
* the live render emits them (the exposition is a real Prometheus text
  body);
* the kill-switch OFF ⇒ **HTTP 200 with a VALID EMPTY body** — no
  series at all, INCLUDING no ``ensemble_metrics_up`` — while the
  registry KEEPS ACCUMULATING (the scrape counter still increments with
  ``outcome="ok"`` and the exposition histogram still records the empty
  render);
* switching ON later loses NO history;
* a render that raises degrades to a comment line + ``outcome="error"``
  — NEVER a 503.
"""

from __future__ import annotations

import pytest

from daemon import checkpoint_metrics as cm
from daemon.services import metrics_gate
from daemon.services import metrics_listener as ml


@pytest.fixture(autouse=True)
def _registry(monkeypatch):
    """Publish on every render (the cadence provider is the pinned API)
    and register the F0.1 families once per process."""
    monkeypatch.delenv(metrics_gate.ENV_VAR, raising=False)
    metrics_gate.reset_for_tests()
    cm.set_publisher_cadence_provider(lambda: 0.0)
    ml.ensure_f0_1_registered()
    # The registry is process-global — clear the series so each leg
    # starts from zero (the families stay REGISTERED).
    cm.reset_for_tests()
    yield
    cm.reset_for_tests()
    metrics_gate.reset_for_tests()


def _listener() -> ml.MetricsListener:
    return ml.MetricsListener(port=0, gate_enabled=metrics_gate.metrics_enabled)


# ─── registration ──────────────────────────────────────────────────────────


def test_the_f0_1_families_are_registered():
    up, scrape, exposition = ml.f0_1_handles()
    assert up is not None
    assert scrape is not None
    assert exposition is not None
    assert up.name == "ensemble_metrics_up"
    assert scrape.name == "ensemble_metrics_scrape_requests_total"
    assert exposition.name == "ensemble_metrics_exposition_seconds"


def test_the_f0_1_registration_is_idempotent():
    before = len(cm._METRICS)
    ml.ensure_f0_1_registered()
    ml.ensure_f0_1_registered()
    assert len(cm._METRICS) == before


def test_the_exposition_buckets_are_the_catalog_set():
    assert ml.EXPOSITION_BUCKETS == (
        0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0,
    )


def test_the_outcome_vocabulary_is_the_catalog_set():
    assert ml.OUTCOME_VALUES == frozenset({"ok", "error"})


# ─── the live render ──────────────────────────────────────────────────────


def test_the_live_render_emits_the_f0_1_families(monkeypatch):
    monkeypatch.setenv(metrics_gate.ENV_VAR, "1")
    listener = _listener()
    # Two scrapes: the counter is incremented AFTER the render (one
    # increment per scrape, with the scrape's REAL outcome), so the
    # second body carries the counter series.
    listener.build_response("/metrics", {})
    body = listener.build_response("/metrics", {}).body.decode()
    assert "ensemble_metrics_up" in body
    assert "ensemble_metrics_scrape_requests_total" in body
    assert "ensemble_metrics_exposition_seconds" in body


def test_the_up_gauge_is_set_while_the_listener_is_live(monkeypatch):
    monkeypatch.setenv(metrics_gate.ENV_VAR, "1")
    listener = _listener()
    listener.build_response("/metrics", {})
    up, _scrape, _exposition = ml.f0_1_handles()
    assert up.get() == 1


def test_the_render_is_prometheus_text(monkeypatch):
    monkeypatch.setenv(metrics_gate.ENV_VAR, "1")
    listener = _listener()
    response = listener.build_response("/metrics", {})
    assert response.status == 200
    assert response.content_type.startswith("text/plain")
    body = response.body.decode()
    assert "# HELP" in body
    assert "# TYPE" in body


# ─── the degrade contract (kill-switch OFF) ───────────────────────────────


def test_the_switch_off_serves_an_empty_body_and_no_up_series(monkeypatch):
    monkeypatch.setenv(metrics_gate.ENV_VAR, "0")
    listener = _listener()
    response = listener.build_response("/metrics", {})
    assert response.status == 200
    assert response.body == b""
    assert response.outcome == "ok"


def test_the_scrape_counter_still_increments_while_off(monkeypatch):
    monkeypatch.setenv(metrics_gate.ENV_VAR, "0")
    listener = _listener()
    listener.build_response("/metrics", {})
    _up, scrape, _exposition = ml.f0_1_handles()
    assert scrape.get(outcome="ok") == 1
    assert scrape.get(outcome="error") == 0


def test_the_exposition_histogram_records_the_empty_render(monkeypatch):
    monkeypatch.setenv(metrics_gate.ENV_VAR, "0")
    listener = _listener()
    listener.build_response("/metrics", {})
    _up, _scrape, exposition = ml.f0_1_handles()
    assert exposition.get_count() == 1


def test_the_registry_keeps_accumulating_while_off(monkeypatch):
    """Switching ON later loses NO history."""
    monkeypatch.setenv(metrics_gate.ENV_VAR, "0")
    listener = _listener()
    listener.build_response("/metrics", {})
    listener.build_response("/metrics", {})
    _up, scrape, _exposition = ml.f0_1_handles()
    assert scrape.get(outcome="ok") == 2

    monkeypatch.setenv(metrics_gate.ENV_VAR, "1")
    listener.build_response("/metrics", {})
    body = listener.build_response("/metrics", {}).body.decode()
    assert "ensemble_metrics_scrape_requests_total" in body
    # The history survived the switch: 2 (off) + 2 (on) scrapes.
    assert scrape.get(outcome="ok") == 4


def test_the_switch_off_is_never_a_503(monkeypatch):
    monkeypatch.setenv(metrics_gate.ENV_VAR, "0")
    listener = _listener()
    for _ in range(3):
        assert listener.build_response("/metrics", {}).status == 200


# ─── the degraded render ───────────────────────────────────────────────────


def test_a_raising_render_degrades_to_a_comment_line(monkeypatch):
    monkeypatch.setenv(metrics_gate.ENV_VAR, "1")
    listener = _listener()

    def _boom() -> str:
        raise RuntimeError("registry exploded")

    monkeypatch.setattr(cm, "render_metrics", _boom)
    response = listener.build_response("/metrics", {})
    assert response.status == 200  # NEVER 503
    assert response.outcome == "error"
    assert b"unavailable" in response.body


def test_a_degraded_family_comment_marks_the_outcome_error(monkeypatch):
    """The registry's per-family degradation comment (``# <family>
    unavailable: <reason>``) marks the scrape's outcome as ``error``."""
    monkeypatch.setenv(metrics_gate.ENV_VAR, "1")
    listener = _listener()
    monkeypatch.setattr(
        cm,
        "render_metrics",
        lambda: "# ensemble_checkpoint_op_seconds unavailable: lock_contended\n",
    )
    response = listener.build_response("/metrics", {})
    assert response.status == 200
    assert response.outcome == "error"


def test_the_error_outcome_is_counted(monkeypatch):
    monkeypatch.setenv(metrics_gate.ENV_VAR, "1")
    listener = _listener()
    monkeypatch.setattr(
        cm,
        "render_metrics",
        lambda: "# ensemble_checkpoint_op_seconds unavailable: lock_contended\n",
    )
    listener.build_response("/metrics", {})
    _up, scrape, _exposition = ml.f0_1_handles()
    assert scrape.get(outcome="error") == 1
    assert scrape.get(outcome="ok") == 0

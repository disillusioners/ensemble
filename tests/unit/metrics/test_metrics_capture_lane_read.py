"""I3 — the m4 READ SITE (registered pack ``metrics_capture_lane_read_unit_test``).

THE SURFACE.  ``GET /api/metrics/capture-lane`` — a JSON read of
``daemon.services.skill_capture_observability.counters_snapshot()``, mounted
on the existing metrics router with the query door's any-of scope set and the
exposition route's fail-closed auth-OFF refusal.

WHY A JSON ROUTE AND NOT A REGISTRY FAMILY (the ruling, pinned here).  A new
exposition family would add series to the Prometheus body, and the COMMITTED
structural-identity baseline artifact is that body's reference — so a family
would force a re-baseline of a committed gate artifact.  The counters are
already a process fact with a public accessor; serving them as JSON touches
NO registry state and leaves the body byte-identical. This module PINS that
neutrality, because "someone will helpfully add the family later" is the
predictable regression.

Mutation legs (pack ``metrics_capture_lane_read_unit_test``):

* ``M-m4a`` — remove the route ⇒ the route pins RED;
* ``M-m4b`` — register a capture-lane exposition family at import time ⇒ the
  EXPOSITION-NEUTRAL pins RED (the registry count moves and the rendered
  body grows) — i.e. the guard, not merely the route, is pinned.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from daemon.services import skill_capture_observability as observability
from tests.helpers.metrics_query_fixtures import build_client

REPO_ROOT = Path(__file__).resolve().parents[3]
BASELINE = REPO_ROOT / "tests" / "unit" / "metrics" / "baselines" / "metrics_exposition_baseline.txt"

#: The mandated baseline digest (recorded when this pack was registered).
BASELINE_SHA256 = "ce7b24a65206ec98a941308fe926e60e40139b5a8064ee48aac9ea9d705c9040"

PATH = "/api/metrics/capture-lane"


@pytest.fixture(autouse=True)
def _fresh_counters():
    observability.reset_counters()
    yield
    observability.reset_counters()


def _client(*, auth_on: bool = True, scopes: tuple[str, ...] = ("maintenance:read",)):
    return build_client(None, auth_on=auth_on, scopes=scopes)


class TestTheRouteExists:
    def test_the_route_is_mounted(self) -> None:
        from daemon.routers.metrics import router

        paths = {route.path for route in router.routes}
        assert "/metrics/capture-lane" in paths, sorted(paths)

    def test_the_route_answers_200(self) -> None:
        response = _client().get(PATH)
        assert response.status_code == 200, response.text


class TestTheResponseShape:
    def test_the_body_carries_the_snapshot_keys(self) -> None:
        observability.record_admission_refusal("capture_lane_off")
        observability.record_admission(is_fix=True)
        body = _client().get(PATH).json()

        assert set(body) == {"counters", "source", "exposition_neutral"}
        counters = body["counters"]
        assert set(counters) == {
            "admission_refusals",
            "execution_refusals",
            "admissions",
            "deviations",
            "fix_admissions",
        }
        assert counters["admission_refusals"] == {"capture_lane_off": 1}
        assert counters["admissions"] == 1
        assert counters["fix_admissions"] == 1
        assert body["exposition_neutral"] is True
        assert body["source"] == "daemon.services.skill_capture_observability"

    def test_the_body_is_a_copy_not_the_live_counter_object(self) -> None:
        body = _client().get(PATH).json()
        body["counters"]["admission_refusals"]["injected"] = 99
        assert "injected" not in observability.counters_snapshot()[
            "admission_refusals"
        ]

    def test_the_snapshot_is_served_verbatim(self) -> None:
        observability.record_execution_refusal("lane_disabled")
        body = _client().get(PATH).json()
        assert body["counters"] == observability.counters_snapshot()


class TestTheAuthPostureMatchesTheQueryDoor:
    def test_auth_off_is_a_fail_closed_refusal(self) -> None:
        response = _client(auth_on=False).get(PATH)
        assert response.status_code == 503
        assert response.json()["detail"]["error"] == "AUTH_NOT_CONFIGURED"

    @pytest.mark.parametrize("scope", ["maintenance:read", "metrics:read"])
    def test_either_scope_suffices(self, scope: str) -> None:
        assert _client(scopes=(scope,)).get(PATH).status_code == 200

    def test_no_scope_is_a_403(self) -> None:
        response = _client(scopes=()).get(PATH)
        assert response.status_code == 403
        assert response.json()["detail"]["error"] == "SCOPE_INSUFFICIENT"


class TestTheRouteIsExpositionNeutral:
    """M-m4b — the guard that stops a well-meaning family from landing."""

    def test_the_committed_baseline_digest_is_unchanged(self) -> None:
        import hashlib

        digest = hashlib.sha256(BASELINE.read_bytes()).hexdigest()
        assert digest == BASELINE_SHA256, (
            "the committed exposition baseline artifact changed — a new family "
            "or a new rendered series must NOT be introduced by this read site"
        )

    def test_no_capture_lane_series_is_in_the_exposition_body(self) -> None:
        """M-m4b — render AFTER importing the router module.

        The probe must import ``daemon.routers.metrics`` FIRST: a family
        registered at that module's import time (the regression this pins)
        is only visible in a process that has imported it.
        """
        body = subprocess.run(
            [sys.executable, "-c", (
                "import sys; sys.path.insert(0, '.')\n"
                "from daemon.routers import metrics as _router  # noqa: F401\n"
                "from daemon import checkpoint_metrics as cm\n"
                "print(cm.render_metrics())\n"
            )],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            env={k: v for k, v in os.environ.items() if not k.startswith("POSTGRES_")},
            timeout=120,
            check=True,
        ).stdout
        lowered = body.lower()
        for token in ("capture_lane", "admission_refusal", "fix_admission"):
            assert token not in lowered, (
                f"{token!r} leaked into the Prometheus exposition body — the "
                "capture-lane counters must stay a JSON-only read surface"
            )

    def test_the_router_module_registers_no_metric(self) -> None:
        """The second half of the guard: no registration call in this module.

        A family added here would both widen the exposition body AND force a
        re-baseline of the committed artifact — exactly what the ruling
        forbids. (M-m4b plants one and reds this pin.)
        """
        import ast

        tree = ast.parse(
            (REPO_ROOT / "daemon" / "routers" / "metrics.py").read_text(
                encoding="utf-8"
            )
        )
        offenders: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = node.func
            name = (
                target.attr
                if isinstance(target, ast.Attribute)
                else getattr(target, "id", "")
            )
            if name in {"register_gauge", "register_counter", "register_histogram"}:
                offenders.append(f"line {getattr(node, 'lineno', '?')}: {name}")
        assert not offenders, (
            "the metrics router must not register a metric of its own — the "
            f"capture-lane read site is exposition-neutral: {offenders}"
        )

    def test_the_route_reads_no_registry_state(self) -> None:
        """The handler must not touch the metric registry at all."""
        import inspect

        from daemon.routers import metrics as metrics_module

        source = inspect.getsource(metrics_module.get_capture_lane_counters)
        for forbidden in ("register_gauge", "register_counter", "render_metrics"):
            assert forbidden not in source
        assert "counters_snapshot()" in source

    def test_the_registry_count_is_not_moved_by_this_route(self) -> None:
        """A route call must not register anything (the count is stable)."""
        from daemon import checkpoint_metrics as cm

        before = len(cm.registered_metrics())
        _client().get(PATH)
        assert len(cm.registered_metrics()) == before


class TestTheCallerContract:
    def test_a_caller_reads_the_counter_with_one_line(self) -> None:
        """The documented read (see the pack row and the route docstring)."""
        observability.record_admission_refusal("capture_lane_off")
        value = _client().get(PATH).json()["counters"]["admission_refusals"][
            "capture_lane_off"
        ]
        assert value == 1
        # …and the same read via plain HTTP, as an operator would do it:
        #   curl -s -H "Authorization: Bearer $TOKEN" \
        #     http://127.0.0.1:8079/api/metrics/capture-lane \
        #     | jq '.counters.admission_refusals.capture_lane_off'

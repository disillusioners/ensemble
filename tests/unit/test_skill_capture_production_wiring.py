"""Phase 3 — the skill-CAPTURE production wiring is bound at BOOT.

Why this file exists (the hole it closes)
-----------------------------------------
Every pre-Phase-3 pin of the capture lane injected the job dispatcher
**test-side** (``SkillMetricsService.set_job_dispatcher(...)`` inside the
test) and then asserted it was there — e.g.
``tests/services/test_skill_capture_observability.py:690``,
``tests/unit/test_skill_capture_killswitch_verification.py:348``,
``tests/integration/test_skill_capture.py:176``. A test-side injection
proves the SETTER works; it proves NOTHING about production. Delete the
production binding and the whole suite stays green — which is exactly the
shape of hole that let the capture lane look "wired" while the live boot
owned nothing.

This module drives the REAL production seam — the boot wiring the daemon
actually runs (``daemon.api._wire_skills_router_services``, called from the
FastAPI ``lifespan``) — and asserts the **production boot result**:

* the capture lane's handle (``SkillMetricsService._skill_job_dispatcher``)
  holds the **production dispatcher instance** (``manager._skill_job_dispatcher``)
  by IDENTITY, and
* the REST seam and the capture seam hold the **same** production instance
  (``daemon.routers.skills._skill_job_dispatcher``), so a test-side
  injection into the service alone can never satisfy the assertions.

Named mutations (all three legs are executed by the implementing lane and
the RED output is recorded in the lane report):

* **leg 1** — remove the production binding from
  ``daemon/api.py::_wire_skills_router_services`` ⇒ this module goes RED;
* **leg 2** — leg 1 PLUS a test-side injection into the metrics service
  (the shape that fooled every prior gate) ⇒ this module stays RED;
* **leg 3** (round-2 F2) — remove the single
  ``_wire_skills_router_services(manager)`` call line from
  ``daemon.api.lifespan`` ⇒ this module goes RED (the static pin below).
"""

from types import SimpleNamespace

import pytest

from daemon.config import SkillEvolutionConfig
from daemon.services.skill_metrics_service import SkillMetricsService


@pytest.fixture
def restore_router_dispatcher():
    """Restore the process-wide router global the production seam writes.

    ``daemon.routers.skills._skill_job_dispatcher`` is a MODULE global (the
    production shape — a module-level global + setter). The production seam
    writes it, so a test that drives the seam must put the world back.
    """
    from daemon.routers import skills as skills_router

    saved = skills_router._skill_job_dispatcher
    try:
        yield
    finally:
        skills_router._skill_job_dispatcher = saved


def _manager_double(dispatcher, metrics_service):
    """A manager double carrying ONLY what the capture binding needs.

    ``_skill_metrics_service`` is a REAL :class:`SkillMetricsService` — its
    ``_skill_job_dispatcher`` starts ABSENT, so the only thing that can put
    a dispatcher there is the production seam (or a test-side injection,
    which is exactly what the leg-2 mutation adds).

    Every OTHER collaborator is left ``None`` on purpose: the production
    seam guards each setter with ``if getattr(manager, ..., None) is not
    None``, so ``None`` means the seam SKIPS that setter. A test that
    drove the seam with all collaborators present would leave the
    process-wide router globals (``get_store`` / ``get_search`` /
    ``get_metrics`` / ``get_evolution`` / trigger-repo / usage-repo)
    wired for the rest of the pytest session — cross-test pollution that
    would redden unrelated router tests.
    """
    return SimpleNamespace(
        _skill_store_service=None,
        _skill_search_service=None,
        _skill_metrics_service=metrics_service,
        _skill_evolution_service=None,
        _skill_trigger_repo=None,
        _skill_job_dispatcher=dispatcher,
        _skill_usage_repo=None,
    )


def _real_metrics_service():
    return SkillMetricsService(
        usage_repo=object(),
        skill_repo=object(),
        trigger_repo=object(),
        ab_test_repo=object(),
        config=SkillEvolutionConfig(),
    )


class TestSkillCaptureProductionWiring:
    """The capture lane's dispatcher is bound by the PRODUCTION boot."""

    def test_production_seam_binds_the_capture_lane_dispatcher(
        self, restore_router_dispatcher
    ):
        """The boot seam binds the PRODUCTION dispatcher into the metrics service.

        Reads the production boot RESULT — the objects the seam produced —
        never a test fixture.
        """
        from daemon.api import _wire_skills_router_services
        from daemon.routers import skills as skills_router

        metrics = _real_metrics_service()
        production_dispatcher = object()
        manager = _manager_double(production_dispatcher, metrics)

        # The REAL production seam — the one the FastAPI lifespan calls.
        _wire_skills_router_services(manager)

        bound = getattr(metrics, "_skill_job_dispatcher", None)
        assert bound is not None, (
            "the production boot must bind a job dispatcher into the "
            "SkillMetricsService — the capture lane's enqueue path reads "
            "the service's OWN handle "
            "(daemon/services/skill_metrics_service.py:812)"
        )
        assert bound is production_dispatcher, (
            "the capture lane must hold the PRODUCTION dispatcher instance "
            "(manager._skill_job_dispatcher) — a test-side injection of a "
            "different object must never satisfy this assertion"
        )
        # Cross-seam identity: the REST seam and the capture seam must hold
        # the SAME production instance. Injecting into the service alone
        # breaks this invariant.
        assert skills_router._skill_job_dispatcher is production_dispatcher, (
            "the REST skills seam and the CAPTURE seam must hold the same "
            "production dispatcher instance"
        )

    def test_lifespan_still_calls_the_production_seam(self):
        """F2 (round-2): ``daemon.api.lifespan`` still calls the boot seam.

        Deleting the single ``_wire_skills_router_services(manager)`` call
        line inside ``lifespan`` would leave every drive-the-seam test
        green while production binds NOTHING at boot — the exact hole
        class this phase exists to close. This pin is static: it reads the
        seam's name out of the lifespan function's own code object.

        ``lifespan`` is decorated with ``@asynccontextmanager``: the module
        attribute is the ``functools.wraps`` ``helper`` closure, whose OWN
        ``__code__`` merely constructs ``_AsyncGeneratorContextManager``.
        The real body is reachable only through ``__wrapped__``
        (``inspect.unwrap``) — pinning against the RAW attribute would be
        permanently red, and pinning against nothing would be vacuous.
        """
        import inspect

        from daemon import api

        lifespan_body = inspect.unwrap(api.lifespan)
        assert (
            "_wire_skills_router_services"
            in lifespan_body.__code__.co_names
        ), (
            "daemon.api.lifespan no longer calls the production boot seam "
            "_wire_skills_router_services — production would boot with NO "
            "capture-lane dispatcher binding while every seam-driving test "
            "stays green"
        )

    def test_production_seam_is_boot_safe_without_skill_services(self):
        """The seam must not raise when the skill services are absent.

        Boot-safety: the injection is unconditional-but-harmless; the
        ON/OFF decision belongs to the lane gates
        (``ENSEMBLE_SKILL_CAPTURE_ENABLED`` / ``ENSEMBLE_SKILL_EVOLUTION_ENABLED``),
        never to the wiring.
        """
        from daemon.api import _wire_skills_router_services

        manager = SimpleNamespace(
            _skill_store_service=None,
            _skill_search_service=None,
            _skill_metrics_service=None,
            _skill_evolution_service=None,
            _skill_trigger_repo=None,
            _skill_job_dispatcher=None,
            _skill_usage_repo=None,
        )
        _wire_skills_router_services(manager)  # must not raise

    def test_production_seam_is_idempotent(self, restore_router_dispatcher):
        """Driving the seam twice binds the same instance (no churn)."""
        from daemon.api import _wire_skills_router_services

        metrics = _real_metrics_service()
        production_dispatcher = object()
        manager = _manager_double(production_dispatcher, metrics)

        _wire_skills_router_services(manager)
        first = getattr(metrics, "_skill_job_dispatcher", None)
        _wire_skills_router_services(manager)

        assert getattr(metrics, "_skill_job_dispatcher", None) is first, (
            "re-driving the seam must keep the SAME dispatcher instance"
        )
        assert first is production_dispatcher

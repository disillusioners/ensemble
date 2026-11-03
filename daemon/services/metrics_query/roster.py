"""The chart-primary roster (plan task 2.7b — approver B2).

FR-C2 requires EVERY registered family to be classified as
chart-primary (series required) or stat-primary (current value only),
and the plan pins the concrete subject set: the FR-C2 floor set
**minus F2** (dropped by the value ruling AM-25 / LR-2) **plus** the
four D5 partials that qualify (F7, F8, F18, F21) = **12 families**.

This module is the roster's canonical home.  It is NOT a second family
table for the door: the roster is a DECLARATION of the chart roles that
Phase 4 passes at REGISTRATION time (``chart_role=``), and the door
serves whatever the registry carries.  Adding a family to the roster is
one ``chart_role`` kwarg at registration + one row here — no door edit,
no FE edit.

RULES

* a family NOT in this roster is stat-primary (current value only) —
  the FR-C2 default;
* every roster series carries a ``chart_role``; ``chart_role ∈ {line,
  step}`` — counters render STEP and gauges render LINEAR (a linear
  interpolation between counter samples would assert values that were
  never observed);
* F2 is ABSENT because it was dropped by value ruling (AM-25 / LR-2),
  so v1 has no F2 dark-with-reason path to render.
"""

from __future__ import annotations

from dataclasses import dataclass

from daemon.checkpoint_metrics import CHART_ROLES

#: The chart rendering modes.
CHART_ROLE_LINE = "line"
CHART_ROLE_STEP = "step"


@dataclass(frozen=True)
class RosterSeries:
    """One roster series and its rendering mode."""

    name: str
    chart_role: str
    labels: str = ""


@dataclass(frozen=True)
class RosterFamily:
    """One chart-primary family and its roster series."""

    family: str
    series: tuple[RosterSeries, ...]

    @property
    def chart_role(self) -> str:
        """The family's dominant rendering mode.

        A mixed family (a gauge + a counter) renders each of its
        series by ITS OWN role; the family's own mode is the mode of
        its FIRST declared series and is what a consumer that renders
        the family as a whole uses.
        """
        return self.series[0].chart_role if self.series else CHART_ROLE_LINE


CHART_PRIMARY_ROSTER: tuple[RosterFamily, ...] = (
    RosterFamily(
        "F1",
        (RosterSeries("ensemble_loop_lag_seconds", CHART_ROLE_LINE, "{stat}"),),
    ),
    RosterFamily(
        "F4",
        (
            RosterSeries("ensemble_db_pool_native", CHART_ROLE_LINE, "{pool,quantity}"),
            RosterSeries(
                "ensemble_db_pool_requests_errors_total",
                CHART_ROLE_STEP,
                "{pool,kind}",
            ),
        ),
    ),
    RosterFamily(
        "F5",
        (
            RosterSeries("ensemble_db_pool_checked_out", CHART_ROLE_LINE, "{pool}"),
            RosterSeries(
                "ensemble_db_pool_waiting_clients", CHART_ROLE_LINE, "{pool}"
            ),
        ),
    ),
    RosterFamily(
        "F6",
        (
            RosterSeries("ensemble_pg_backends", CHART_ROLE_LINE, "{state}"),
            RosterSeries(
                "ensemble_pg_idle_in_txn_oldest_seconds", CHART_ROLE_LINE, ""
            ),
            RosterSeries("ensemble_pg_leaked_txn", CHART_ROLE_LINE, "{site}"),
            RosterSeries(
                "ensemble_pg_census_last_success_timestamp_seconds",
                CHART_ROLE_LINE,
                "",
            ),
        ),
    ),
    RosterFamily(
        "F9",
        (
            RosterSeries(
                "ensemble_readiness_component_state", CHART_ROLE_LINE, "{component}"
            ),
            RosterSeries("ensemble_readiness_grace_active", CHART_ROLE_LINE, ""),
            RosterSeries(
                "ensemble_readiness_failure_streak", CHART_ROLE_LINE, "{component}"
            ),
            RosterSeries(
                "ensemble_readiness_probe_total", CHART_ROLE_STEP, "{component,outcome}"
            ),
        ),
    ),
    RosterFamily(
        "F10",
        (
            RosterSeries(
                "ensemble_job_queue_depth", CHART_ROLE_LINE, "{admission_state}"
            ),
            RosterSeries(
                "ensemble_job_queue_items_created_total", CHART_ROLE_STEP, ""
            ),
        ),
    ),
    RosterFamily(
        "F11",
        (
            RosterSeries("ensemble_workers_total", CHART_ROLE_LINE, "{pool}"),
            RosterSeries("ensemble_workers_busy", CHART_ROLE_LINE, "{pool}"),
            RosterSeries(
                "ensemble_worker_pool_wakeup_efficiency", CHART_ROLE_LINE, "{pool}"
            ),
            RosterSeries(
                "ensemble_task_heartbeat_age_seconds", CHART_ROLE_LINE, ""
            ),
            RosterSeries(
                "ensemble_task_heartbeat_cadence_seconds", CHART_ROLE_LINE, ""
            ),
            RosterSeries(
                "ensemble_worker_pool_events_total", CHART_ROLE_STEP, "{pool,event}"
            ),
        ),
    ),
    RosterFamily(
        "F13",
        (
            RosterSeries(
                "ensemble_instance_status_count", CHART_ROLE_LINE, "{status}"
            ),
            RosterSeries(
                "ensemble_instance_liveness_count", CHART_ROLE_LINE, "{liveness}"
            ),
            RosterSeries("ensemble_waiting_children_count", CHART_ROLE_LINE, ""),
            RosterSeries(
                "ensemble_waiting_children_max_age_seconds", CHART_ROLE_LINE, ""
            ),
        ),
    ),
    RosterFamily(
        "F7",
        (
            RosterSeries("ensemble_checkpoint_op_bytes", CHART_ROLE_STEP, "{op}"),
            RosterSeries(
                "ensemble_checkpoint_slow_ops_total", CHART_ROLE_STEP, "{op}"
            ),
        ),
    ),
    RosterFamily(
        "F8",
        (
            RosterSeries("ensemble_checkpoint_blobs_table_bytes", CHART_ROLE_LINE, ""),
            RosterSeries(
                "ensemble_checkpoint_table_bytes", CHART_ROLE_LINE, "{table}"
            ),
        ),
    ),
    RosterFamily(
        "F18",
        (
            RosterSeries("ensemble_mcps_gateway_sessions", CHART_ROLE_LINE, ""),
            RosterSeries("ensemble_mcps_gateway_tombstones", CHART_ROLE_LINE, ""),
            RosterSeries(
                "ensemble_mcps_gateway_audit_total", CHART_ROLE_STEP, "{outcome}"
            ),
        ),
    ),
    RosterFamily(
        "F21",
        (RosterSeries("ensemble_journal_file_bytes", CHART_ROLE_LINE, ""),),
    ),
)

#: The families that were dropped by the value ruling and are therefore
#: ABSENT from the roster (recorded so the absence is a decision, not
#: an omission).
DROPPED_FAMILIES: tuple[str, ...] = ("F2",)


def roster_role_for(family: str, name: str) -> str | None:
    """The roster's rendering mode for a series (``None`` when absent).

    Phase 4 passes the returned value as the ``chart_role`` kwarg at
    registration, so the door serves the roster's role with no door
    edit.
    """
    for entry in CHART_PRIMARY_ROSTER:
        if entry.family != family:
            continue
        for series in entry.series:
            if series.name == name:
                return series.chart_role
    return None


def roster_family_names() -> tuple[str, ...]:
    """The chart-primary family ids (the roster's subject set)."""
    return tuple(entry.family for entry in CHART_PRIMARY_ROSTER)


def assert_roster_is_well_formed() -> None:
    """Fail loud when the roster itself drifts (its own invariant).

    * exactly the 12 chart-primary families;
    * F2 is absent;
    * every role is one of the two declared modes;
    * the family ids are unique.
    """
    names = roster_family_names()
    if len(names) != 12:
        raise ValueError(f"the chart-primary roster must carry 12 families; got {names}")
    if len(set(names)) != len(names):
        raise ValueError(f"duplicate family in the chart-primary roster: {names}")
    for dropped in DROPPED_FAMILIES:
        if dropped in names:
            raise ValueError(
                f"{dropped} was dropped by the value ruling and must not be in the roster"
            )
    for entry in CHART_PRIMARY_ROSTER:
        for series in entry.series:
            if series.chart_role not in CHART_ROLES:
                raise ValueError(
                    f"unknown chart role {series.chart_role!r} on {series.name}"
                )


__all__ = [
    "CHART_PRIMARY_ROSTER",
    "CHART_ROLE_LINE",
    "CHART_ROLE_STEP",
    "DROPPED_FAMILIES",
    "RosterFamily",
    "RosterSeries",
    "assert_roster_is_well_formed",
    "roster_family_names",
    "roster_role_for",
]

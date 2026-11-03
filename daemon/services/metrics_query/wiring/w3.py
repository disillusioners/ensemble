"""The W3 CLUSTER — F17, F18, F21.

Every adapter here is a **reader**.  F17 reads the MCP health SNAPSHOT
(never re-probes a server), F18 reads the gateway's roster / tombstones
and the durable audit table, and F21 reads the journal twin's three
point gauges.

THE FAMILIES
------------
=========  =====================================  ==========================
family     metric(s)                             seam
=========  =====================================  ==========================
F17        the MCP server health + failures       the per-boot quarantine
                                                 registry's snapshot
F18        the gateway sessions / tombstones /    the gateway service + the
          audit (PARTIAL 5/9)                     durable audit table
F21        the journal twin (PARTIAL 3/65)        the journal twin's own
                                                 read accessors
=========  =====================================  ==========================

F21's PHASE families stay OUT
-----------------------------
``journal_history_append`` writes ``{ts, event, detail}`` where the
``event`` is the ALERT-CLASS kind set
(``daemon/tools/upgrade_journal.py:645-649`` — halt / refusal /
rollback / sweep_rollback / quarantine) and the promote PHASE trail
lives in the journal's EXTENSION fields, not in ``history[]``.  There is
therefore NO clean pure-read derivation of the phase families ⇒ they
stay OUT (+30 series not claimed) and their ABSENCE is pinned
(AD4(b) / D-4, the P4-M7 mutation).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from daemon.services.metrics_query.wiring import db, vocab
from daemon.services.metrics_query.wiring.registry import (
    PullAdapter,
    register_family_metric,
    register_pull_adapter,
)
from daemon.services.metrics_query.wiring.sources import WiringSources, current_sources

logger = logging.getLogger(__name__)

CLUSTER = "W3"

# ── the metric registrations ──────────────────────────────────────────────
mcp_server_state = register_family_metric(
    family="F17",
    cluster=CLUSTER,
    name="ensemble_mcp_server_state",
    kind="gauge",
    verdict="IN",
    seam="daemon/mcp/init_guard.py:145-147 (the published snapshot)",
    label_keys=("server", "state"),
    allowed_values={
        "server": vocab.MCP_ROSTER,
        "state": vocab.MCP_STATES,
    },
    help=(
        "The MCP server health state, read from the per-boot quarantine "
        "registry's snapshot. The family NEVER re-probes a server."
    ),
    source_idiom="registry",
    durability="process",
    cardinality_weight=1,
)

mcp_failures_total = register_family_metric(
    family="F17",
    cluster=CLUSTER,
    name="ensemble_mcp_failures_total",
    kind="counter",
    verdict="IN",
    seam="daemon/mcp/init_guard.py:145-147 (the published snapshot)",
    label_keys=("server", "kind"),
    allowed_values={
        "server": vocab.MCP_ROSTER,
        "kind": vocab.MCP_FAILURE_KINDS,
    },
    help=(
        "The MCP failure counters, accumulated by the metrics layer over "
        "the snapshot's per-boot attempt counters."
    ),
    source_idiom="registry",
    durability="process",
    cardinality_weight=1,
)

mcps_gateway_sessions = register_family_metric(
    family="F18",
    cluster=CLUSTER,
    name="ensemble_mcps_gateway_sessions",
    kind="gauge",
    verdict="PARTIAL",
    seam="daemon/services/mcps_gateway/store.py:79/:103",
    help="The number of live MCPS gateway sessions (the roster's bound).",
    source_idiom="registry",
    durability="process",
    cardinality_weight=1,
)

mcps_gateway_tombstones = register_family_metric(
    family="F18",
    cluster=CLUSTER,
    name="ensemble_mcps_gateway_tombstones",
    kind="gauge",
    verdict="PARTIAL",
    seam="daemon/services/mcps_gateway/tombstones.py",
    help="The number of MCPS gateway tombstones (the bounded set).",
    source_idiom="registry",
    durability="process",
    cardinality_weight=1,
)

mcps_gateway_audit_total = register_family_metric(
    family="F18",
    cluster=CLUSTER,
    name="ensemble_mcps_gateway_audit_total",
    kind="counter",
    verdict="PARTIAL",
    seam="daemon/services/mcps_gateway/audit.py (the durable table)",
    label_keys=("outcome",),
    allowed_values={"outcome": vocab.GATEWAY_AUDIT_OUTCOMES},
    help=(
        "The MCPS gateway audit rows per outcome, read from the durable "
        "audit table. The vocabulary is {ok, error, timeout} — `timeout` "
        "is NEVER folded into `error`, and `denied` is not in the "
        "vocabulary because it is never written."
    ),
    source_idiom="registry",
    durability="durable",
    cardinality_weight=1,
)

journal_file_bytes = register_family_metric(
    family="F21",
    cluster=CLUSTER,
    name="ensemble_journal_file_bytes",
    kind="gauge",
    verdict="PARTIAL",
    seam="daemon/tools/upgrade_journal.py:255",
    help="The journal file's size in bytes.",
    source_idiom="registry",
    durability="process",
    cardinality_weight=1,
)

journal_history_entries = register_family_metric(
    family="F21",
    cluster=CLUSTER,
    name="ensemble_journal_history_entries",
    kind="gauge",
    verdict="PARTIAL",
    seam="daemon/tools/upgrade_journal.py:165",
    help="The number of entries in the journal's history list.",
    source_idiom="registry",
    durability="process",
    cardinality_weight=1,
)

upgrade_release_current = register_family_metric(
    family="F21",
    cluster=CLUSTER,
    name="ensemble_upgrade_release_current",
    kind="gauge",
    verdict="PARTIAL",
    seam="daemon/tools/upgrade_journal.py:159",
    label_keys=("version",),
    allowed_values={"version": vocab.RELEASE_VERSIONS},
    help=(
        "The current release, labelled by its version. A version outside "
        "the registered release set folds to the reserved residual."
    ),
    source_idiom="registry",
    durability="process",
    cardinality_weight=1,
)


# ── the F17 adapter ───────────────────────────────────────────────────────
def _mcp_server_token(name: str) -> str:
    """The roster token for a server name (user-created rows fold)."""
    if name in vocab.MCP_ROSTER:
        return name
    if name in ("context7", "plane", "webfetch"):
        return name
    return "custom"


def adapt_f17(sampler: Any) -> None:
    """F17 — the MCP health family over the published snapshot."""
    sources = current_sources()
    provider = sources.mcp_snapshot
    if provider is None:
        return
    try:
        snapshot = provider()
    except Exception:  # noqa: BLE001 — a broken snapshot degrades the family
        logger.debug("metrics wiring: F17 snapshot read failed", exc_info=True)
        return
    if not snapshot:
        return

    for name, entry in snapshot.items():
        token = _mcp_server_token(str(name))
        state = str((entry or {}).get("state", "unknown"))
        if state not in vocab.MCP_STATES:
            state = "unknown"
        for candidate in vocab.MCP_STATES:
            mcp_server_state.set(1 if candidate == state else 0, server=token, state=candidate)
        attempts = (entry or {}).get("init_attempts")
        if isinstance(attempts, int) and attempts > 0:
            kind = "initialize"
            mcp_failures_total.set(attempts, server=token, kind=kind)


# ── the F18 adapter ───────────────────────────────────────────────────────
_AUDIT_COUNT_SQL = "SELECT outcome, COUNT(*) FROM mcp_gateway_audit GROUP BY outcome"


def _gateway_counts(sources: WiringSources) -> tuple[int | None, int | None]:
    """The gateway's live session count + its tombstone count."""
    service = sources.mcps_gateway_service
    if service is None:
        return None, None
    sessions: int | None = None
    tombstones: int | None = None
    try:
        store = service.store()
        roster = store.roster()
        sessions = len(roster)
    except Exception:  # noqa: BLE001 — a disabled gateway degrades the family
        logger.debug("metrics wiring: F18 roster read failed", exc_info=True)
    try:
        tombstone_store = service.tombstones()
        tombstones = len(tombstone_store)
    except Exception:  # noqa: BLE001
        logger.debug("metrics wiring: F18 tombstone read failed", exc_info=True)
    return sessions, tombstones


def adapt_f18(sampler: Any) -> None:
    """F18 — the gateway sessions / tombstones / audit counters."""
    sources = current_sources()
    sessions, tombstones = _gateway_counts(sources)
    if sessions is not None:
        mcps_gateway_sessions.set(sessions)
    if tombstones is not None:
        mcps_gateway_tombstones.set(tombstones)

    if not sources.dsn:
        return
    with db.one_shot(sources.dsn, application_name="ensemble-metrics-w3-gateway", connect=sources.db_connect) as conn:
        rows = db.fetch_rows(conn, _AUDIT_COUNT_SQL)
    if not rows:
        return
    for outcome, count in rows:
        try:
            value = int(count)
        except (TypeError, ValueError):
            continue
        token = str(outcome)
        if token not in vocab.GATEWAY_AUDIT_OUTCOMES:
            # `denied` is declared+exported upstream but NEVER written;
            # any other value folds to the reserved residual.
            token = vocab.RESERVED
        mcps_gateway_audit_total.set(value, outcome=token)


# ── the F21 adapter ───────────────────────────────────────────────────────
def _journal_dir(sources: WiringSources) -> Path | None:
    """The install dir whose journal the twin reads."""
    provider = sources.install_dir
    if provider is None:
        return None
    try:
        install_dir = provider()
    except Exception:  # noqa: BLE001 — the journal family degrades
        return None
    return install_dir


def adapt_f21(sampler: Any) -> None:
    """F21 — the journal twin's three point gauges."""
    sources = current_sources()
    install_dir = _journal_dir(sources)
    if install_dir is None:
        return
    try:
        from daemon.tools import upgrade_journal as uj

        path = uj.journal_path(Path(install_dir))
        journal_file_bytes.set(int(path.stat().st_size))
    except Exception:  # noqa: BLE001 — a torn/absent journal degrades
        logger.debug("metrics wiring: F21 journal size read failed", exc_info=True)
    try:
        from daemon.tools import upgrade_journal as uj

        data = uj.journal_read(Path(install_dir))
    except Exception:  # noqa: BLE001 — a torn journal degrades the family
        logger.debug("metrics wiring: F21 journal read failed", exc_info=True)
        return
    if not isinstance(data, dict):
        return
    history = data.get("history")
    if isinstance(history, list):
        journal_history_entries.set(len(history))
    current = data.get("current")
    if current:
        upgrade_release_current.set(1, version=str(current))


def register_w3_adapters() -> tuple[PullAdapter, ...]:
    """Register the W3 pull adapters (the sampler's stage A)."""
    adapters: tuple[tuple[str, PullAdapter], ...] = (
        ("F17", adapt_f17),
        ("F18", adapt_f18),
        ("F21", adapt_f21),
    )
    for family, adapter in adapters:
        register_pull_adapter(family, adapter)
    return tuple(adapter for _family, adapter in adapters)


__all__ = [
    "adapt_f17",
    "adapt_f18",
    "adapt_f21",
    "register_w3_adapters",
]

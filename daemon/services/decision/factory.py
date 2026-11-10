"""Decision module — the PRODUCTION construction factory (D1).

``build_decision_overlay`` is the SINGLE named factory the manager
calls to construct the overlay. Nothing else may construct the overlay
in production.

**D1 — THE CONFIGURED PREDICATE IS STAMPED HERE.** The factory calls
``DecisionConfig.resolve_values(env=os.environ, source=…,
yaml_block=raw_yaml_decision())``, which stamps
``explicitly_configured`` from the EXPLICIT decision signal
(``DECISION_BASE_URL`` / ``DECISION_API_KEY`` in the environment, or a
``decision:`` block in config.yaml). Without this stamp the predicate
is always False and every consult is suppressed ``not_configured`` —
fail-shut, but P1's only capability never fires.

The factory accepts NO ``configured=`` override: the production path
resolves the signal from the environment and the yaml, exactly as the
operator set it. (Tests may pass an override on the overlay/client
directly; the factory does not.)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from daemon.services.decision.client import DecisionClient
from daemon.services.decision.config import DecisionConfig
from daemon.services.decision.kill import AppSettingKillStore, KillChannelResolver
from daemon.services.decision.overlay import DecisionOverlay
from daemon.services.decision.registry import SeamRegistry
from daemon.services.decision.shadow import ShadowWriter

__all__ = [
    "DecisionOverlayParts",
    "build_decision_overlay",
    "build_decision_overlay_defensively",
]


def _raw_yaml_decision_block() -> Any:
    """The house ``decision:`` yaml section — ``raw_yaml_decision()``.

    ``daemon/config.py:7210``. Imported lazily so a standalone use
    never drags the house config module in.
    """
    try:
        from daemon.config import raw_yaml_decision
    except Exception:  # noqa: BLE001 — the accessor is optional here
        return None
    try:
        return raw_yaml_decision()
    except Exception:  # noqa: BLE001
        return None


@dataclass(frozen=True)
class DecisionOverlayParts:
    """The parts the manager holds after the factory returns."""

    config: DecisionConfig
    client: DecisionClient
    registry: SeamRegistry
    kill_store: Any
    kill_resolver: KillChannelResolver
    shadow_writer: ShadowWriter
    accumulator: Any
    overlay: DecisionOverlay


def build_decision_overlay(
    *,
    engine: Any,
    house_decision_config: Any,
    settings_service_getter: Any = None,
    board_service: Any = None,
) -> DecisionOverlayParts:
    """Build the production overlay — stamping the configured predicate.

    Args:
        engine: The daemon's shared SQLAlchemy engine (the kill store
            and the cost accumulator use it).
        house_decision_config: The house ``Config.decision`` namespace
            (a ``BaseSettings`` object or a mapping).
        settings_service_getter: A callable returning the
            ``SettingsService`` (or ``None``) — the sanctioned kill
            write path.
        board_service: The injected ``BoardService`` (L9).

    Returns:
        A :class:`DecisionOverlayParts` carrying every part the
        manager needs, with the overlay's configured predicate STAMPED
        from the explicit decision signal.
    """
    from daemon.services.decision.cost_store import (
        AppSettingCostAccumulator,
        SettingsServiceKillStore,
        build_s1_seam_record,
    )

    # D1 — resolve_values stamps explicitly_configured from the env +
    # the yaml block. from_source (the manager's old path) NEVER did.
    config = DecisionConfig.resolve_values(
        env=os.environ,
        source=house_decision_config,
        yaml_block=_raw_yaml_decision_block(),
    )

    client = DecisionClient(config)
    registry = SeamRegistry(client=client, config=config)
    registry.register(build_s1_seam_record())

    fallback_kill_store = AppSettingKillStore(engine)
    kill_store = SettingsServiceKillStore(
        settings_service_getter or (lambda: None),
        fallback_store=fallback_kill_store,
    )

    def _override_writer(k: str, v: Any, u: Any = None) -> Any:
        service = settings_service_getter() if settings_service_getter else None
        if service is not None:
            return service.set_override(k, v, u)
        return fallback_kill_store._write_sync(k, str(v))

    kill_resolver = KillChannelResolver(
        kill_store,
        yaml_snapshot=config,
        override_writer=_override_writer,
    )
    shadow_writer = ShadowWriter()
    accumulator = AppSettingCostAccumulator(engine)
    overlay = DecisionOverlay(
        registry=registry,
        kill=kill_resolver,
        shadow_writer=shadow_writer,
        config=config,
        accumulator=accumulator,
        board_service=board_service,
    )
    return DecisionOverlayParts(
        config=config,
        client=client,
        registry=registry,
        kill_store=kill_store,
        kill_resolver=kill_resolver,
        shadow_writer=shadow_writer,
        accumulator=accumulator,
        overlay=overlay,
    )


def build_decision_overlay_defensively(
    *,
    engine: Any,
    config: Any,
    settings_service_getter: Any = None,
    board_service: Any = None,
) -> DecisionOverlayParts | None:
    """Build the overlay OR RETURN ``None`` — the module is OPTIONAL.

    B-2 (council round 4). The decision configuration is an OPTIONAL
    section of the house config: a config object that does not carry it
    (a legacy config, a ``MagicMock(spec=Config)`` fixture — the
    spec'd mock raises ``AttributeError`` on an unspecced attribute), a
    section that cannot be read, or a section the factory cannot use,
    must NOT raise out of the manager. The module is simply NOT
    INSTALLED: the overlay is ``None``, the seam is OFF, the search
    service gets no overlay — INERT. This is the doctrine (fail CLOSED
    to inert, never fail the boot).

    Returns:
        The :class:`DecisionOverlayParts`, or ``None`` when the module
        could not be installed.
    """
    try:
        house = getattr(config, "decision", None)
    except Exception:  # noqa: BLE001 — an unreadable section ⇒ inert
        return None
    if house is None:
        return None
    try:
        return build_decision_overlay(
            engine=engine,
            house_decision_config=house,
            settings_service_getter=settings_service_getter,
            board_service=board_service,
        )
    except Exception:  # noqa: BLE001 — an invalid section ⇒ inert
        return None

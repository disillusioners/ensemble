"""The Memoir Store kill-switch — ONE authority for the whole surface.

Phase 0 of the Memoir Store v1 lane (plan: ``phase0-plan.md`` D0.3/D0.4/
D0.5/D0.6/D0.7/D0.8; amendments AM-25/AM-26/AM-27).

This module is the single place that answers "is the memoir surface on?".
Every consumer — the tools (phase 4), the briefing builder (phase 5), the
embedding worker (phase 3) and the ``inner_soul`` redirect (phase 6) —
reads the knob from HERE, so there is exactly one truth and exactly one
place where the truth-table semantics live. A consumer never reads
``os.environ`` for this variable: the raw string is classified INSIDE the
model (:class:`daemon.config.MemoirConfig`, mechanism N-2).

THE KNOB TRUTH TABLE (canonical — this table is the single authority for
phases 3/4/5/6; every one of those phases states its consumption
semantics against this table):

| `ENSEMBLE_MEMOIR_ENABLED` | Resolved | Surface | Log (once per boot) |
|---|---|---|---|
| absent | config default (**ON** in v1) | ENABLED | **WARNING** — "unset — memoir surface ENABLED by default; set `ENSEMBLE_MEMOIR_ENABLED=false` to disable" |
| empty string | = absent | ENABLED | as above |
| `true`/`1`/`yes`/`on`/`t`/`y` (any case) | true | ENABLED | INFO |
| `false`/`0`/`no`/`off`/`f`/`n` (any case) | false | DISABLED | INFO (the refusal names the var) |
| any other present value | **INDETERMINATE** | **DISABLED (fail closed)** | **WARNING** naming the var, the rejected value, and the canonical spelling |
| unparseable AND the fallback also fails | — | DISABLED | ERROR |

OFF semantics (operator-facing contract, stated not implied): tools refuse
`memoir_disabled`; the briefing is **not built** and the memory is
**unreadable** (no other read path exists in v1); the embedding worker
**parks**; the `inner_soul` ari branch is **still taken** and returns the
loud not-recorded notice (never the RAG void); **the data is untouched** —
rows stay, the kill-switch is not a delete.

MECHANISM (N-2): the raw env string is classified INSIDE the model via a
``field_validator(mode="before")`` on :class:`daemon.config.MemoirConfig`,
so a garbage value NEVER raises (a plain ``bool`` field would raise and
abort the boot instead of failing the surface closed) and NO consumer
parses the env itself.

DELIBERATE DIVERGENCE from the ``notify_user`` house precedent (D0.6,
recorded): ``notify_user`` repairs a garbage value to the validated
fallback (= the default = ON) and is therefore **fail-OPEN**. The memoir
knob fails **CLOSED** on the indeterminate case (DR-3). The cost
asymmetry decides it: a mis-set notify knob leaves a bounded,
rate-limited, audited chat message; a mis-set memoir knob leaves
unbounded, append-only, never-hard-deleted first-person narrative rows.

ACCEPTED RESIDUAL (D0.8, recorded): a **misspelled variable NAME** is
indistinguishable from unset, so the surface is ON and only the per-boot
WARNING distinguishes it. Unpreventable by any knob design — the
mitigation is loudness, not prevention.

CALL-TIME READ (D0.5/AM-27): the knob is resolved **per call**, never
cached at import time. Flipping the environment between two calls in one
process changes the behaviour without a restart. The only thing this
module memoizes is the "already emitted this message" set, which governs
the once-per-boot LOG line — never the resolved VALUE.

DISCOVERABILITY (D0.7/AM-26): "unset ⇒ ON" is admissible only because the
default is never silent, so the resolved state is retrievable **without
reading logs** — :func:`memoir_tool_help` returns the exact text the
memoir tools' ``tool_help`` carries (phase 4 embeds it), and
:func:`memoir_knob_state` is the accessor any health/readiness surface
would read.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import ValidationError

from daemon.config import (
    MEMOIR_ENABLED_ENV_VAR,
    MEMOIR_FALSY_VALUES,
    MEMOIR_RESOLUTION_CONFIG,
    MEMOIR_RESOLUTION_DEFAULT,
    MEMOIR_RESOLUTION_ERROR,
    MEMOIR_RESOLUTION_EXPLICIT_OFF,
    MEMOIR_RESOLUTION_EXPLICIT_ON,
    MEMOIR_RESOLUTION_INDETERMINATE,
    MEMOIR_RESOLUTION_UNPARSEABLE,
    MEMOIR_REFLECTION_ENABLED_ENV_VAR,
    MEMOIR_TRUTHY_VALUES,
    MemoirConfig,
    MemoirReflectionConfig,
    _classify_enabled_flag,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from daemon.config import Config

logger = logging.getLogger(__name__)

#: Canonical spelling list, quoted in the indeterminate WARNING so the
#: operator sees exactly which values are accepted.
_CANONICAL_SPELLINGS = (
    "true/false, 1/0, yes/no, on/off, t/f, y/n (case-insensitive)"
)

#: Once-per-boot log dedupe. Keyed by the emitted message so a CHANGED
#: knob value re-warns while a repeated resolution stays quiet. This is a
#: LOG guard only — the resolved VALUE is never memoized here.
_EMITTED: set[str] = set()


@dataclass(frozen=True)
class MemoirKnobState:
    """The resolved knob state plus the provenance of the resolution.

    Attributes:
        enabled: The resolved surface state — ``True`` means the memoir
            surface is live, ``False`` means it is off (the tools refuse
            ``memoir_disabled``, the briefing is not built, the worker
            parks).
        resolution: One of the ``MEMOIR_RESOLUTION_*`` codes — which
            truth-table row produced this state.
        source: ``"env"`` (the env tier decided), ``"config"`` (the
            config-file tier decided) or ``"none"`` (nothing decided it
            — the error path).
        rejected_value: The offending raw value for the indeterminate
            case, else ``None``.
        message: The operator-facing line (also the log message).
        log_level: The log level this resolution is emitted at.
    """

    enabled: bool
    resolution: str
    source: str
    rejected_value: str | None
    message: str
    log_level: str


def _state(
    *,
    enabled: bool,
    resolution: str,
    source: str,
    rejected_value: str | None,
    message: str,
    log_level: str,
) -> MemoirKnobState:
    return MemoirKnobState(
        enabled=enabled,
        resolution=resolution,
        source=source,
        rejected_value=rejected_value,
        message=message,
        log_level=log_level,
    )


def _emit_once(state: MemoirKnobState) -> None:
    """Emit the state's operator line ONCE per boot (the log guard).

    The truth table's log column says "once per boot" for EVERY row.
    The guard is keyed by the emitted MESSAGE, so a repeated resolution
    of the same state stays quiet while a CHANGED knob value re-warns.
    This guard governs the LOG ONLY — never the resolved value, which is
    read per call.
    """
    if state.message in _EMITTED:
        return
    _EMITTED.add(state.message)
    logger.log(
        logging.ERROR
        if state.log_level == "ERROR"
        else (logging.WARNING if state.log_level == "WARNING" else logging.INFO),
        state.message,
    )


def reset_boot_log_state() -> None:
    """Forget which knob lines have already been emitted.

    Test/support helper for the once-per-boot log guard. The resolved
    VALUE is never memoized, so resetting the guard changes nothing
    about how the knob resolves.
    """
    _EMITTED.clear()


def resolve_memoir_knob(config: "Config | None" = None) -> MemoirKnobState:
    """Resolve the memoir kill-switch NOW (call-time read, never cached).

    Args:
        config: The resolved daemon configuration, when the caller has
            one. It supplies the **config-file tier** — the fallback used
            when the env tier is unusable, and the value that decides the
            state when the env variable is absent. Pass ``None`` to
            assert "no config tier is available" (which is what makes the
            ERROR row of the truth table reachable).

    Returns:
        The resolved :class:`MemoirKnobState`. The function NEVER
        raises: a knob that cannot be resolved resolves to DISABLED
        (fail closed) with the loudest applicable log level.
    """
    try:
        cfg = MemoirConfig()
    except ValidationError as exc:
        # The env tier is UNPARSEABLE (a sibling field carries a garbage
        # value, so the model itself cannot be built). Fall back to the
        # config-file tier; if THAT is unavailable too, the truth
        # table's last row applies: DISABLED + ERROR.
        fallback_enabled: bool | None = None
        if config is not None:
            try:
                fallback_enabled = bool(config.memoir.enabled)
            except Exception as fallback_exc:  # pragma: no cover - defensive
                state = _state(
                    enabled=False,
                    resolution=MEMOIR_RESOLUTION_ERROR,
                    source="none",
                    rejected_value=None,
                    message=(
                        f"[Memoir] {MEMOIR_ENABLED_ENV_VAR} tier is "
                        f"unparseable and the configuration fallback failed "
                        f"({fallback_exc}) — memoir surface DISABLED (fail "
                        f"closed)"
                    ),
                    log_level="ERROR",
                )
                _emit_once(state)
                return state
        if fallback_enabled is None:
            state = _state(
                enabled=False,
                resolution=MEMOIR_RESOLUTION_ERROR,
                source="none",
                rejected_value=None,
                message=(
                    f"[Memoir] {MEMOIR_ENABLED_ENV_VAR} tier is unparseable "
                    f"({exc}) and no configuration fallback is available — "
                    f"memoir surface DISABLED (fail closed)"
                ),
                log_level="ERROR",
            )
            _emit_once(state)
            return state
        message = (
            f"[Memoir] {MEMOIR_ENABLED_ENV_VAR} tier is unparseable "
            f"({exc}) — using the configuration tier "
            f"(memoir.enabled={str(fallback_enabled).lower()})"
        )
        state = _state(
            enabled=fallback_enabled,
            resolution=MEMOIR_RESOLUTION_UNPARSEABLE,
            source="config",
            rejected_value=None,
            message=message,
            log_level="WARNING",
        )
        _emit_once(state)
        return state

    # The model classified the raw env string for us (mechanism N-2).
    if cfg._env_present:
        resolution = cfg._resolution
        if resolution == MEMOIR_RESOLUTION_INDETERMINATE:
            rejected = cfg._rejected_value
            message = (
                f"[Memoir] {MEMOIR_ENABLED_ENV_VAR}={rejected!r} is not a "
                f"recognised boolean — memoir surface DISABLED (fail closed). "
                f"Accepted spellings: {_CANONICAL_SPELLINGS}"
            )
            state = _state(
                enabled=False,
                resolution=resolution,
                source="env",
                rejected_value=rejected,
                message=message,
                log_level="WARNING",
            )
            _emit_once(state)
            return state
        if resolution == MEMOIR_RESOLUTION_EXPLICIT_OFF:
            message = (
                f"[Memoir] {MEMOIR_ENABLED_ENV_VAR} is set to a falsy value "
                f"— memoir surface DISABLED; the tools refuse "
                f"memoir_disabled (the data is untouched)"
            )
        else:
            message = (
                f"[Memoir] {MEMOIR_ENABLED_ENV_VAR} is set to a truthy value "
                f"— memoir surface ENABLED"
            )
        state = _state(
            enabled=cfg.enabled,
            resolution=resolution,
            source="env",
            rejected_value=None,
            message=message,
            log_level="INFO",
        )
        _emit_once(state)
        return state

    # The env tier is ABSENT (or empty) — the config-file tier decides.
    if config is not None:
        try:
            config_enabled = bool(config.memoir.enabled)
        except Exception as exc:  # pragma: no cover - defensive
            state = _state(
                enabled=False,
                resolution=MEMOIR_RESOLUTION_ERROR,
                source="none",
                rejected_value=None,
                message=(
                    f"[Memoir] the configuration tier is unreadable ({exc}) — "
                    f"memoir surface DISABLED (fail closed)"
                ),
                log_level="ERROR",
            )
            _emit_once(state)
            return state
    else:
        config_enabled = bool(cfg.enabled)

    if config_enabled:
        # The default is ON — and the default is NEVER silent.
        message = (
            f"[Memoir] {MEMOIR_ENABLED_ENV_VAR} is unset — memoir surface "
            f"ENABLED by default; set `{MEMOIR_ENABLED_ENV_VAR}=false` to "
            f"disable"
        )
        state = _state(
            enabled=True,
            resolution=MEMOIR_RESOLUTION_DEFAULT,
            source="config",
            rejected_value=None,
            message=message,
            log_level="WARNING",
        )
        _emit_once(state)
        return state

    message = (
        f"[Memoir] {MEMOIR_ENABLED_ENV_VAR} is unset and the configuration "
        f"disables the surface (memoir.enabled=false) — memoir surface "
        f"DISABLED"
    )
    state = _state(
        enabled=False,
        resolution=MEMOIR_RESOLUTION_CONFIG,
        source="config",
        rejected_value=None,
        message=message,
        log_level="INFO",
    )
    _emit_once(state)
    return state


def memoir_knob_state(config: "Config | None" = None) -> MemoirKnobState:
    """The resolved knob state (the accessor for health surfaces)."""
    return resolve_memoir_knob(config)


def memoir_enabled(config: "Config | None" = None) -> bool:
    """``True`` when the memoir surface is live. Read at CALL TIME.

    This is the function every consumer calls (tools, briefing, worker
    tick, redirect). It NEVER caches the value: flipping the
    environment between two calls in one process changes the answer.
    """
    return resolve_memoir_knob(config).enabled


def emit_boot_state(config: "Config | None" = None) -> MemoirKnobState:
    """Emit the once-per-boot knob line and return the resolved state.

    The daemon calls this ONCE at boot (the manager's init, right after
    the persona seed) so the "unset ⇒ ON" default is loud even when no
    tool is ever invoked. Repeated calls with the SAME message stay
    quiet; a CHANGED knob value re-warns.
    """
    return resolve_memoir_knob(config)


def memoir_tool_help(config: "Config | None" = None) -> str:
    """The resolved-state text the memoir tools' ``tool_help`` carries.

    Discoverability (D0.7/AM-26): an operator must be able to learn
    whether the memoir surface is on WITHOUT reading logs. Phase 4
    embeds this text in the tools' ``tool_help``; phase 0 owns it here
    so the wording has exactly one home.

    Args:
        config: The resolved daemon configuration, when available.

    Returns:
        A one-paragraph operator-facing description of the CURRENT
        resolved knob state.
    """
    state = resolve_memoir_knob(config)
    surface = "ENABLED" if state.enabled else "DISABLED"
    return (
        f"Memoir kill-switch: {surface} "
        f"(resolution={state.resolution}, source={state.source}, "
        f"variable={MEMOIR_ENABLED_ENV_VAR}). "
        f"{state.message}"
    )


# ── the v2 REFLECTION lane kill-switch (phase 2, T2.4) ────────────────────
#
# A SECOND, ISOLATED lane switch — NOT a clone of the global knob
# resolver (N1 / V2-D 4.9: the CLASSIFIER is shared, the RESOLVER is
# per-lane). The reflection lane is a SUBSET of the memoir surface: the
# v1 surface can be ON while the reflection lane is OFF, and the two
# switches are read independently.
#
# THE TRUTH TABLE (the §3-A4 Enabled-flags class):
#
# | `ENSEMBLE_MEMOIR_REFLECTION_ENABLED` | Resolved | Lane | Log |
# |---|---|---|---|
# | absent | the CONFIG tier decides: ON (the constitutional default) | ENABLED | WARNING — "unset — the reflection lane is ENABLED by default" |
# | absent + `memoir_reflection.enabled: false` | OFF (the config tier) | DISABLED | INFO — "unset and the configuration disables the reflection lane" (resolution `config`) |
# | empty string | = absent | ENABLED | as above |
# | `true`/`1`/`yes`/`on`/`t`/`y` | true | ENABLED | INFO |
# | `false`/`0`/`no`/`off`/`f`/`n` | false | DISABLED | INFO |
# | any other present value | INDETERMINATE | **DISABLED (fail closed)** | WARNING naming the var and the rejected value |
#
# The CLASSIFICATION happens INSIDE the model
# (:class:`daemon.config.MemoirReflectionConfig`, mechanism N-2 — the
# same mechanism the v1 knob uses), so this module NEVER classifies a
# raw string itself and NEVER raises.


@dataclass(frozen=True)
class MemoirReflectionKnobState:
    """The resolved REFLECTION-lane state plus its provenance.

    The shape mirrors :class:`MemoirKnobState` so a health surface can
    render both with one code path.
    """

    enabled: bool
    resolution: str
    source: str
    rejected_value: str | None
    message: str
    log_level: str


def resolve_memoir_reflection_knob(
    config: "Config | None" = None,
) -> MemoirReflectionKnobState:
    """Resolve the REFLECTION-lane kill-switch NOW (call-time read).

    The classification is the model's (mechanism N-2); this function
    reads the resolved value and reports its provenance. It NEVER
    raises: an unresolvable lane resolves to DISABLED (fail closed).

    Args:
        config: The resolved daemon configuration, when the caller has
            one. Its ``memoir_reflection`` section is the config tier;
            ``None`` means the section is built from the environment
            alone.

    Returns:
        The resolved :class:`MemoirReflectionKnobState`.
    """
    raw = os.environ.get(MEMOIR_REFLECTION_ENABLED_ENV_VAR)
    env_present = raw is not None and bool(raw.strip())

    try:
        if config is not None:
            section = getattr(config, "memoir_reflection", None)
        else:
            section = None
        if section is None:
            section = MemoirReflectionConfig()
        # The ENV tier is read LIVE (call time) and WINS over the
        # BOOT-FROZEN config section: a mid-run flip of the kill-switch
        # must take effect WITHOUT a restart. The config section decides
        # ONLY when the env tier is ABSENT — the v1 knob's shape (the
        # classifier is shared, the RESOLVER is per-lane, N1).
        #
        # The value comes from the SHARED classifier (mechanism N-2 — the
        # same reader the model's own field validator uses), so the live
        # env tier and the boot-frozen section can never disagree about
        # what a value MEANS. The truth table is: unset ⇒ ON ONLY when
        # the config tier ALSO says ON (+ the loud WARNING); unset ⇒ OFF
        # when the config tier disables the lane (an explicit CONFIG
        # decision — INFO); garbage ⇒ OFF (fail closed); explicit
        # true/false ⇒ honoured.
        env_enabled: bool | None = None
        if env_present:
            env_enabled = _classify_enabled_flag(
                raw,
                name=MEMOIR_REFLECTION_ENABLED_ENV_VAR,
                default=True,
            )
        enabled = env_enabled if env_enabled is not None else bool(section.enabled)
    except Exception as exc:  # noqa: BLE001 — a knob never raises
        message = (
            f"[MemoirReflect] {MEMOIR_REFLECTION_ENABLED_ENV_VAR} could not "
            f"be resolved ({exc}) — the reflection lane is DISABLED (fail "
            f"closed)"
        )
        state = MemoirReflectionKnobState(
            enabled=False,
            resolution=MEMOIR_RESOLUTION_ERROR,
            source="none",
            rejected_value=None,
            message=message,
            log_level="ERROR",
        )
        _emit_reflection_once(state)
        return state

    if env_present:
        lowered = raw.strip().lower()
        if lowered in MEMOIR_FALSY_VALUES:
            message = (
                f"[MemoirReflect] {MEMOIR_REFLECTION_ENABLED_ENV_VAR}=false — "
                f"the reflection lane is DISABLED (the worker parks and the "
                f"dual-recall envelope stays at its v1 shape)"
            )
            resolution = MEMOIR_RESOLUTION_EXPLICIT_OFF
            log_level = "INFO"
        elif lowered in MEMOIR_TRUTHY_VALUES:
            message = (
                f"[MemoirReflect] {MEMOIR_REFLECTION_ENABLED_ENV_VAR}=true — "
                f"the reflection lane is ENABLED"
            )
            resolution = MEMOIR_RESOLUTION_EXPLICIT_ON
            log_level = "INFO"
        else:
            message = (
                f"[MemoirReflect] {MEMOIR_REFLECTION_ENABLED_ENV_VAR}="
                f"{raw!r} is not a recognised boolean — the reflection lane "
                f"is DISABLED (fail closed); accepted spellings are "
                f"true/false, 1/0, yes/no, on/off, t/f, y/n"
            )
            resolution = MEMOIR_RESOLUTION_INDETERMINATE
            log_level = "WARNING"
        state = MemoirReflectionKnobState(
            enabled=enabled,
            resolution=resolution,
            source="env",
            rejected_value=raw if resolution == MEMOIR_RESOLUTION_INDETERMINATE else None,
            message=message,
            log_level=log_level,
        )
        _emit_reflection_once(state)
        _warn_unset_budget_knobs(section)
        return state

    # The env tier is ABSENT: the CONFIG tier decides — and the line
    # states the RESOLVED value, never a value the resolver did not
    # reach (D0.7: the resolved state is retrievable WITHOUT reading
    # logs — ``memoir_knob_state`` is the accessor).
    #
    # The model's own truth table is the authority here (the same split
    # ``MemoirConfig._record_resolution`` records for the v1 surface):
    # an ABSENT env with the section ENABLED is the constitutional
    # DEFAULT (the unset case is LOUD — a WARNING); an ABSENT env with
    # the section DISABLED is an explicit CONFIG decision (INFO, and the
    # resolution code names the tier that decided).
    if enabled:
        message = (
            f"[MemoirReflect] {MEMOIR_REFLECTION_ENABLED_ENV_VAR} is unset — the "
            f"reflection lane is ENABLED by default; set "
            f"`{MEMOIR_REFLECTION_ENABLED_ENV_VAR}=false` to disable it"
        )
        resolution = MEMOIR_RESOLUTION_DEFAULT
        log_level = "WARNING"
    else:
        message = (
            f"[MemoirReflect] {MEMOIR_REFLECTION_ENABLED_ENV_VAR} is unset and "
            f"the configuration disables the reflection lane "
            f"(memoir_reflection.enabled=false) — the reflection lane is "
            f"DISABLED"
        )
        resolution = MEMOIR_RESOLUTION_CONFIG
        log_level = "INFO"
    state = MemoirReflectionKnobState(
        enabled=enabled,
        resolution=resolution,
        source="config",
        rejected_value=None,
        message=message,
        log_level=log_level,
    )
    _emit_reflection_once(state)
    _warn_unset_budget_knobs(section)
    return state


def _warn_unset_budget_knobs(section: Any) -> None:
    """The AGGREGATE budget warning — every UNSET budget knob, named.

    §3-A4: a budget knob that is UNSET is never silent — ONE warning
    names EVERY unset budget knob (so the operator sees the full
    governance surface in one line instead of one line per knob). The
    cadence tunables stay SILENT (tuning, not governance).

    The warning is emitted from HERE — the knob module owns the
    operator-facing log lines (the house precedent) — and the model only
    RECORDS which knobs are unset.
    """
    unset = tuple(getattr(section, "_unset_budget_knobs", ()) or ())
    if not unset:
        return
    _emit_reflection_once(
        MemoirReflectionKnobState(
            enabled=True,
            resolution=MEMOIR_RESOLUTION_DEFAULT,
            source="config",
            rejected_value=None,
            message=(
                f"[MemoirReflect] the reflection budget knobs are unset and "
                f"use their constitutional defaults: {', '.join(sorted(unset))} "
                f"(unset budget knobs are never silent; set them to tune the "
                f"lane)"
            ),
            log_level="WARNING",
        )
    )


def _emit_reflection_once(state: MemoirReflectionKnobState) -> None:
    """Emit the reflection-lane line ONCE per boot (the log guard).

    Keyed by the message, so a repeated resolution stays quiet while a
    CHANGED value re-warns. The resolved VALUE is never memoized here.
    """
    if state.message in _EMITTED:
        return
    _EMITTED.add(state.message)
    logger.log(
        logging.ERROR
        if state.log_level == "ERROR"
        else (logging.WARNING if state.log_level == "WARNING" else logging.INFO),
        state.message,
    )


def memoir_reflection_enabled(config: "Config | None" = None) -> bool:
    """``True`` when the REFLECTION lane is live. Read at CALL TIME.

    This is the function the reflection worker and the dual-recall
    surface call. It NEVER caches the value.
    """
    return resolve_memoir_reflection_knob(config).enabled


__all__ = [
    "MemoirKnobState",
    "MemoirReflectionKnobState",
    "emit_boot_state",
    "memoir_enabled",
    "memoir_knob_state",
    "memoir_reflection_enabled",
    "memoir_tool_help",
    "reset_boot_log_state",
    "resolve_memoir_knob",
]

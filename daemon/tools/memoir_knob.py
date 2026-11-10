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


# ─────────────────────────────────────────────────────────────────────────
# Memoir Store v2 — the POIGNANCY lane's boot-state surface (phase 4, T4.4)
#
# THE TOUCHPOINT IS NARROW ON PURPOSE: this module carries the
# POIGNANCY knob's OPERATOR-FACING SURFACE (the boot-state line and the
# status inspection) — it is NOT where the worker resolves its knob. The
# worker's resolution belongs to the LANE'S OWN module
# (``daemon/services/memoir_poignancy_service.resolve_poignancy_settings``),
# which reads the ISOLATED ``memoir_poignancy`` section and never calls
# the global knob resolver (N1 / V2-D 4.9).
#
# The truth table is NOT copied here either: the resolved value comes
# from the lane's own resolver, and that resolver classifies the raw
# string with the SHARED helper (``daemon.config._classify_enabled_flag``).
# So this surface and the worker can never disagree about what a value
# MEANS.
# ─────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MemoirPoignancyKnobState:
    """The resolved POIGNANCY-lane state plus its provenance.

    The shape mirrors :class:`MemoirReflectionKnobState` so a health
    surface can render both with one code path.
    """

    enabled: bool
    resolution: str
    source: str
    rejected_value: str | None
    message: str
    log_level: str


def resolve_memoir_poignancy_knob(
    config: "Config | None" = None,
) -> MemoirPoignancyKnobState:
    """Resolve the POIGNANCY-lane kill-switch NOW (call-time read).

    The classification is the lane's (the shared classifier, applied by
    the lane's own resolver); this function reports the PROVENANCE and
    owns the operator-facing log line. It NEVER raises: an unresolvable
    lane resolves to DISABLED (fail closed).

    Args:
        config: The resolved daemon configuration, when the caller has
            one. Its ``memoir_poignancy`` section is the config tier;
            ``None`` means the section is built from the environment
            alone.

    Returns:
        The resolved :class:`MemoirPoignancyKnobState`.
    """
    from daemon.services.memoir_poignancy_service import (
        resolve_poignancy_settings,
    )

    try:
        settings = resolve_poignancy_settings(config)
    except Exception as exc:  # noqa: BLE001 — a knob never raises
        message = (
            f"[MemoirPoignancy] the lane settings could not be resolved "
            f"({exc}) — the poignancy lane is DISABLED (fail closed)"
        )
        state = MemoirPoignancyKnobState(
            enabled=False,
            resolution=MEMOIR_RESOLUTION_ERROR,
            source="none",
            rejected_value=None,
            message=message,
            log_level="ERROR",
        )
        _emit_poignancy_once(state)
        return state

    resolution = settings.resolution
    if resolution == MEMOIR_RESOLUTION_EXPLICIT_OFF:
        message = (
            "[MemoirPoignancy] ENSEMBLE_MEMOIR_POIGNANCY_ENABLED=false — "
            "the scoring lane is DISABLED (the worker parks; the episodes "
            "are untouched)"
        )
        log_level = "INFO"
    elif resolution == MEMOIR_RESOLUTION_EXPLICIT_ON:
        message = (
            "[MemoirPoignancy] ENSEMBLE_MEMOIR_POIGNANCY_ENABLED=true — "
            "the scoring lane is ENABLED"
        )
        log_level = "INFO"
    elif resolution == MEMOIR_RESOLUTION_INDETERMINATE:
        message = (
            f"[MemoirPoignancy] ENSEMBLE_MEMOIR_POIGNANCY_ENABLED="
            f"{settings.rejected_value!r} is not a recognised boolean — the "
            f"scoring lane is DISABLED (fail closed); accepted spellings "
            f"are true/false, 1/0, yes/no, on/off, t/f, y/n"
        )
        log_level = "WARNING"
    elif settings.enabled:
        message = (
            "[MemoirPoignancy] ENSEMBLE_MEMOIR_POIGNANCY_ENABLED is unset — "
            "the scoring lane is ENABLED by default; set "
            "`ENSEMBLE_MEMOIR_POIGNANCY_ENABLED=false` to disable it"
        )
        log_level = "WARNING"
    else:
        message = (
            "[MemoirPoignancy] ENSEMBLE_MEMOIR_POIGNANCY_ENABLED is unset "
            "and the configuration disables the scoring lane "
            "(memoir_poignancy.enabled=false) — the scoring lane is "
            "DISABLED"
        )
        log_level = "INFO"

    state = MemoirPoignancyKnobState(
        enabled=settings.enabled,
        resolution=resolution,
        source=settings.source,
        rejected_value=settings.rejected_value,
        message=message,
        log_level=log_level,
    )
    _emit_poignancy_once(state)
    return state


def _emit_poignancy_once(state: MemoirPoignancyKnobState) -> None:
    """Emit the poignancy-lane line ONCE per boot (the log guard).

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


def memoir_poignancy_enabled(config: "Config | None" = None) -> bool:
    """``True`` when the POIGNANCY lane is live. Read at CALL TIME."""
    return resolve_memoir_poignancy_knob(config).enabled


# ─────────────────────────────────────────────────────────────────────────
# Memoir Store v2 — the WAKE-REFRESH lane's knob (phase 6, T6.5 / V2-D 6.4)
#
# THE SAME PER-LANE SHAPE as the reflection / poignancy resolvers: the
# CLASSIFIER is shared (``daemon.config._classify_enabled_flag`` — the
# truth table lives in ONE place), the RESOLVER is per-lane, and the
# operator-facing log line is owned HERE (mechanism N-2: the model
# classifies, the knob module speaks).
#
# The seam (``daemon/services/context_messages._maybe_memoir_refresh_message``)
# calls :func:`resolve_memoir_wake_refresh_knob` at CALL TIME — never a
# boot-cached value — so flipping the env mid-run takes effect on the
# next qualifying wake.
# ─────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MemoirWakeRefreshKnobState:
    """The resolved WAKE-REFRESH-lane state plus its provenance.

    The shape mirrors :class:`MemoirReflectionKnobState` so a health
    surface can render all three lanes with one code path.
    """

    enabled: bool
    resolution: str
    source: str
    rejected_value: str | None
    message: str
    log_level: str


def resolve_memoir_wake_refresh_knob(
    config: "Config | None" = None,
) -> MemoirWakeRefreshKnobState:
    """Resolve the WAKE-REFRESH-lane kill-switch NOW (call-time read).

    The truth table is the Enabled-flags row of §3-A4 (DR-3): unset ⇒ ON
    + one WARNING; garbage ⇒ OFF (fail closed) + a WARNING naming the
    value; explicit true/false honoured; a resolution ERROR ⇒ OFF. The
    function NEVER raises — a knob that cannot be resolved disables its
    lane and the daemon keeps running.

    Args:
        config: The resolved daemon configuration, when the caller has
            one. Its ``memoir_wake_refresh`` section is the config tier;
            ``None`` means the section is built from the environment
            alone.

    Returns:
        The resolved :class:`MemoirWakeRefreshKnobState`.
    """
    from daemon.config import (
        MEMOIR_WAKE_REFRESH_ENABLED_ENV_VAR,
        MemoirWakeRefreshConfig,
    )

    raw = os.environ.get(MEMOIR_WAKE_REFRESH_ENABLED_ENV_VAR)
    env_present = raw is not None and bool(raw.strip())

    try:
        if config is not None:
            section = getattr(config, "memoir_wake_refresh", None)
        else:
            section = None
        if section is None:
            section = MemoirWakeRefreshConfig()
        # THE ENV TIER IS READ LIVE and WINS over the boot-frozen section
        # (the reflection resolver's shape): the section decides ONLY when
        # the env tier is absent.
        env_enabled: bool | None = None
        if env_present:
            env_enabled = _classify_enabled_flag(
                raw,
                name=MEMOIR_WAKE_REFRESH_ENABLED_ENV_VAR,
                default=True,
            )
        enabled = env_enabled if env_enabled is not None else bool(section.enabled)
    except Exception as exc:  # noqa: BLE001 — a knob never raises
        state = MemoirWakeRefreshKnobState(
            enabled=False,
            resolution=MEMOIR_RESOLUTION_ERROR,
            source="none",
            rejected_value=None,
            message=(
                f"[MemoirRefresh] {MEMOIR_WAKE_REFRESH_ENABLED_ENV_VAR} could "
                f"not be resolved ({exc}) — the wake-refresh block is "
                f"DISABLED (fail closed)"
            ),
            log_level="ERROR",
        )
        _emit_wake_refresh_once(state)
        return state

    if env_present:
        lowered = raw.strip().lower()
        if lowered in MEMOIR_FALSY_VALUES:
            message = (
                f"[MemoirRefresh] {MEMOIR_WAKE_REFRESH_ENABLED_ENV_VAR}=false — "
                f"the wake-refresh context block is DISABLED (scheduler wakes "
                f"carry no memoir refresh; the briefing and the alarm are "
                f"untouched)"
            )
            resolution = MEMOIR_RESOLUTION_EXPLICIT_OFF
            log_level = "INFO"
        elif lowered in MEMOIR_TRUTHY_VALUES:
            message = (
                f"[MemoirRefresh] {MEMOIR_WAKE_REFRESH_ENABLED_ENV_VAR}=true — "
                f"the wake-refresh context block is ENABLED"
            )
            resolution = MEMOIR_RESOLUTION_EXPLICIT_ON
            log_level = "INFO"
        else:
            message = (
                f"[MemoirRefresh] {MEMOIR_WAKE_REFRESH_ENABLED_ENV_VAR}="
                f"{raw!r} is not a recognised boolean — the wake-refresh "
                f"block is DISABLED (fail closed); accepted spellings are "
                f"true/false, 1/0, yes/no, on/off, t/f, y/n"
            )
            resolution = MEMOIR_RESOLUTION_INDETERMINATE
            log_level = "WARNING"
        state = MemoirWakeRefreshKnobState(
            enabled=enabled,
            resolution=resolution,
            source="env",
            rejected_value=(
                raw if resolution == MEMOIR_RESOLUTION_INDETERMINATE else None
            ),
            message=message,
            log_level=log_level,
        )
        _emit_wake_refresh_once(state)
        return state

    if enabled:
        message = (
            f"[MemoirRefresh] {MEMOIR_WAKE_REFRESH_ENABLED_ENV_VAR} is unset — "
            f"the wake-refresh block is ENABLED by default; set "
            f"`{MEMOIR_WAKE_REFRESH_ENABLED_ENV_VAR}=false` to disable it"
        )
        resolution = MEMOIR_RESOLUTION_DEFAULT
        log_level = "WARNING"
    else:
        message = (
            f"[MemoirRefresh] {MEMOIR_WAKE_REFRESH_ENABLED_ENV_VAR} is unset "
            f"and the configuration disables the wake-refresh block "
            f"(memoir_wake_refresh.enabled=false) — it is DISABLED"
        )
        resolution = MEMOIR_RESOLUTION_CONFIG
        log_level = "INFO"
    state = MemoirWakeRefreshKnobState(
        enabled=enabled,
        resolution=resolution,
        source="config",
        rejected_value=None,
        message=message,
        log_level=log_level,
    )
    _emit_wake_refresh_once(state)
    return state


def _emit_wake_refresh_once(state: MemoirWakeRefreshKnobState) -> None:
    """Emit the wake-refresh line ONCE per boot (the shared log guard).

    Keyed by the message (the SAME ``_EMITTED`` set the other lanes use),
    so a repeated resolution stays quiet while a CHANGED value re-warns.
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


def memoir_wake_refresh_enabled(config: "Config | None" = None) -> bool:
    """``True`` when the WAKE-REFRESH block may be built. Read at CALL TIME."""
    return resolve_memoir_wake_refresh_knob(config).enabled


# ─────────────────────────────────────────────────────────────────────────
# Memoir Store v2 — the PROPOSAL lane's knob (phase 6, closure #3)
#
# THE SAME PER-LANE SHAPE as the reflection / poignancy / wake-refresh
# resolvers: the CLASSIFIER is shared (``daemon.config._classify_numeric``
# — the truth table lives in ONE place), the RESOLVER is per-lane, and
# the operator-facing log line is owned HERE (mechanism N-2: the model
# classifies, the knob module speaks).
#
# The lane (``daemon.services.memoir_keepsake_proposal_service``) calls
# :func:`resolve_memoir_proposal_fetch_cap_multiplier` at CALL TIME —
# never a boot-cached value — so an operator widens (or narrows) the
# bounded over-fetch WITHOUT a restart.
#
# THE KNOB IS THE SANCTIONED ESCAPE, NOT A DEFAULT CHANGE: the bounded
# scan stays the DEFAULT (the cost constitution — reflection must never
# become a rewrite hazard), and this multiplier is how an operator
# reaches an eligible tail that the default bound defers.
# ─────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MemoirProposalFetchCapKnobState:
    """The resolved PROPOSAL fetch-cap multiplier plus its provenance.

    The shape mirrors :class:`MemoirWakeRefreshKnobState` with the
    enabled flag replaced by the resolved NUMERIC — a health surface can
    still render every lane with one code path.
    """

    multiplier: int
    resolution: str
    source: str
    rejected_value: str | None
    message: str
    log_level: str


def resolve_memoir_proposal_fetch_cap_multiplier() -> MemoirProposalFetchCapKnobState:
    """Resolve the PROPOSAL fetch-cap multiplier NOW (call-time read).

    The truth table is the Budget-bounds row of §3-A4 (DR-3): unset ⇒ the
    constitutional default (3) with a LOUD WARNING (an unset budget knob
    is never silent); a legal value in [1, 20] ⇒ honoured (INFO); a legal
    value outside the band ⇒ CLAMPED with a WARNING; any other value ⇒ the
    default, fail CLOSED, with a WARNING naming the value. The function
    NEVER raises — a knob that cannot be resolved keeps the default bound
    and the daemon keeps running.

    NO CONFIG TIER (and NO ``config`` parameter, deliberately): the knob's
    section is ENV-ONLY (no ``config.yaml`` block), so there is no second
    source that could disagree about what the default is — and a
    boot-frozen section value must never leak into a LIVE resolution. The
    env tier is read at CALL TIME, so the operator raises the bound
    without a restart.

    Returns:
        The resolved :class:`MemoirProposalFetchCapKnobState`.
    """
    from daemon.config import (
        MEMOIR_PROPOSAL_FETCH_CAP_MULTIPLIER_DEFAULT,
        MEMOIR_PROPOSAL_FETCH_CAP_MULTIPLIER_ENV_VAR,
        MEMOIR_PROPOSAL_FETCH_CAP_MULTIPLIER_MAX,
        MEMOIR_PROPOSAL_FETCH_CAP_MULTIPLIER_MIN,
        MEMOIR_RESOLUTION_CLAMPED,
        MEMOIR_RESOLUTION_DEFAULT,
        MEMOIR_RESOLUTION_ERROR,
        MEMOIR_RESOLUTION_EXPLICIT_VALUE,
        MEMOIR_RESOLUTION_INDETERMINATE,
        NUMERIC_CLASS_BUDGET,
        MemoirProposalConfig,
        _resolve_numeric,
    )

    raw = os.environ.get(MEMOIR_PROPOSAL_FETCH_CAP_MULTIPLIER_ENV_VAR)
    env_present = raw is not None and bool(raw.strip())

    try:
        if env_present:
            # The ENV tier decides — the LIVE read. The SHARED classifier
            # is the only thing that decides what the value MEANS (the
            # A2 debt budget is capped at the one known duplicate: there
            # is NO second truth table here).
            resolved = _resolve_numeric(
                raw,
                default=MEMOIR_PROPOSAL_FETCH_CAP_MULTIPLIER_DEFAULT,
                numeric_class=NUMERIC_CLASS_BUDGET,
                name=MEMOIR_PROPOSAL_FETCH_CAP_MULTIPLIER_ENV_VAR,
                minimum=MEMOIR_PROPOSAL_FETCH_CAP_MULTIPLIER_MIN,
                maximum=MEMOIR_PROPOSAL_FETCH_CAP_MULTIPLIER_MAX,
            )
            source = "env"
        else:
            # The env tier is ABSENT: the CONFIG tier decides. The
            # section is ENV-ONLY, so this is the constitutional default
            # — and it is LOUD (the budget class's "an unset budget knob
            # is never silent" rule).
            section = MemoirProposalConfig()
            resolved = _resolve_numeric(
                None,
                default=int(section.fetch_cap_multiplier),
                numeric_class=NUMERIC_CLASS_BUDGET,
                name=MEMOIR_PROPOSAL_FETCH_CAP_MULTIPLIER_ENV_VAR,
                minimum=MEMOIR_PROPOSAL_FETCH_CAP_MULTIPLIER_MIN,
                maximum=MEMOIR_PROPOSAL_FETCH_CAP_MULTIPLIER_MAX,
            )
            source = "config"
    except Exception as exc:  # noqa: BLE001 — a knob never raises
        state = MemoirProposalFetchCapKnobState(
            multiplier=MEMOIR_PROPOSAL_FETCH_CAP_MULTIPLIER_DEFAULT,
            resolution=MEMOIR_RESOLUTION_ERROR,
            source="none",
            rejected_value=None,
            message=(
                f"[MemoirProposal] {MEMOIR_PROPOSAL_FETCH_CAP_MULTIPLIER_ENV_VAR} "
                f"could not be resolved ({exc}) — the candidate-window "
                f"over-fetch stays at the constitutional default "
                f"{MEMOIR_PROPOSAL_FETCH_CAP_MULTIPLIER_DEFAULT} (fail closed)"
            ),
            log_level="ERROR",
        )
        _emit_proposal_once(state)
        return state

    multiplier = int(resolved.value)
    if resolved.resolution == MEMOIR_RESOLUTION_DEFAULT:
        message = (
            f"[MemoirProposal] {MEMOIR_PROPOSAL_FETCH_CAP_MULTIPLIER_ENV_VAR} "
            f"is unset — the candidate-window over-fetch uses the "
            f"constitutional default {multiplier} (unset budget knobs are "
            f"never silent; set the knob to widen the bound for a "
            f"pathological corpus)"
        )
        log_level = "WARNING"
    elif resolved.resolution == MEMOIR_RESOLUTION_EXPLICIT_VALUE:
        message = (
            f"[MemoirProposal] {MEMOIR_PROPOSAL_FETCH_CAP_MULTIPLIER_ENV_VAR}="
            f"{multiplier} — the candidate-window over-fetch is {multiplier} × "
            f"the window per pass"
        )
        log_level = "INFO"
    elif resolved.resolution == MEMOIR_RESOLUTION_CLAMPED:
        message = (
            f"[MemoirProposal] {MEMOIR_PROPOSAL_FETCH_CAP_MULTIPLIER_ENV_VAR}="
            f"{resolved.raw!r} is out of range — clamped to {multiplier} "
            f"(the constitutional default is "
            f"{MEMOIR_PROPOSAL_FETCH_CAP_MULTIPLIER_DEFAULT}; the bound "
            f"stays a BOUNDED scan)"
        )
        log_level = "WARNING"
    elif resolved.resolution == MEMOIR_RESOLUTION_INDETERMINATE:
        message = (
            f"[MemoirProposal] {MEMOIR_PROPOSAL_FETCH_CAP_MULTIPLIER_ENV_VAR}="
            f"{resolved.raw!r} is not a valid number — the candidate-window "
            f"over-fetch stays at the constitutional default {multiplier} "
            f"(fail closed; a garbage value never widens the scan)"
        )
        log_level = "WARNING"
    else:  # pragma: no cover — defensive (the classifier has four rows)
        message = (
            f"[MemoirProposal] {MEMOIR_PROPOSAL_FETCH_CAP_MULTIPLIER_ENV_VAR} "
            f"resolved to {multiplier} ({resolved.resolution})"
        )
        log_level = "WARNING"

    state = MemoirProposalFetchCapKnobState(
        multiplier=multiplier,
        resolution=resolved.resolution,
        source=source,
        rejected_value=resolved.rejected_value,
        message=message,
        log_level=log_level,
    )
    _emit_proposal_once(state)
    return state


def _emit_proposal_once(state: MemoirProposalFetchCapKnobState) -> None:
    """Emit the proposal-lane line ONCE per boot (the shared log guard).

    Keyed by the message (the SAME ``_EMITTED`` set the other lanes use),
    so a repeated resolution stays quiet while a CHANGED value re-warns.
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


__all__ = [
    "MemoirKnobState",
    "MemoirPoignancyKnobState",
    "MemoirProposalFetchCapKnobState",
    "MemoirReflectionKnobState",
    "MemoirWakeRefreshKnobState",
    "emit_boot_state",
    "memoir_enabled",
    "memoir_knob_state",
    "memoir_poignancy_enabled",
    "memoir_reflection_enabled",
    "memoir_tool_help",
    "memoir_wake_refresh_enabled",
    "reset_boot_log_state",
    "resolve_memoir_knob",
    "resolve_memoir_poignancy_knob",
    "resolve_memoir_proposal_fetch_cap_multiplier",
    "resolve_memoir_reflection_knob",
    "resolve_memoir_wake_refresh_knob",
]

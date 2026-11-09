"""Decision module — configuration resolution (``thin-client.md`` §3, IP-1).

**TWO RESOLUTION PATHS (L6).**

1. **The injected house config** — the wiring round creates a
   ``DecisionConfig`` in ``daemon/config.py`` and binds it as
   ``Config.decision``. This module ACCEPTS that object (or a plain
   mapping) as its source, so the daemon's resolved configuration is
   the single truth.
2. **The standalone env read** — the fallback for tests and for a
   daemon that has not wired the namespace yet.

The ``DECISION_*`` names carry no ``OPENAI_`` prefix, so they cannot
ride the prefixed resolution (``LLMConfig.env_prefix="OPENAI_"``) —
they are read EXPLICITLY here.

==================  ==========================  ======================
Setting             Env var                     Default
==================  ==========================  ======================
Base URL            ``DECISION_BASE_URL``        ``OPENAI_BASE_URL``
Credential          ``DECISION_API_KEY``         ``OPENAI_API_KEY``
Model id            ``DECISION_MODEL``           literal ``"decision"``
Timeout             ``DECISION_TIMEOUT_S``       per-lane budget (5 s)
USD ceiling         ``DECISION_COST_CEILING_USD``  50.0 (global)
Token ceiling       ``DECISION_TOKEN_CEILING``    10 000 000 (global)
Rate table          ``DECISION_PRICING_USD_PER_MILLION_TOKENS``  ``{}``
==================  ==========================  ======================

**THE TWO QUESTIONS ARE SEPARATE (Q2 — the leader's reconciliation).**

* **``is_configured()``** — the CONFIGURED predicate. It requires an
  **EXPLICIT decision signal**: ``DECISION_BASE_URL`` or
  ``DECISION_API_KEY`` present in the environment, **OR** a ``decision:``
  block in ``config.yaml`` (the house accessor
  ``daemon/config.py:7210::raw_yaml_decision``). **Merely inheriting
  ``OPENAI_BASE_URL`` / ``OPENAI_API_KEY`` does NOT make the module
  configured.**
* **``resolve_values()``** — the VALUE resolver. Once CONFIGURED, the
  values may fall back (``DECISION_API_KEY`` value ← ``OPENAI_API_KEY``;
  ``DECISION_BASE_URL`` value ← ``OPENAI_BASE_URL``). Reuse-not-mint
  holds.
* **``DECISION_MODEL`` defaults to ``"decision"``** (L6 stands).

Why this is not cosmetic: the ``decision`` row routes to a **PAID
upstream** (``api.openai.com``). Under a pure-L6 reading, an operator
who flips only ``decision_seam_snapshot_l3_selection_enabled=1`` begins
spending money on EVERY consult with nothing else provisioned. Under
the reconciliation the same flip produces **NO CALL** and a suppressed
``not_configured`` row. The USD 50/month ceiling is the ratified
control and the per-seam default-OFF is the primary belt; this is the
**second belt**.

**THE CANONICAL CONTRACT (the wiring round's namespace).** The house
``Config.decision`` is a ``DecisionConfig(BaseSettings)`` with
``env_prefix="DECISION_"``, so its FIELD names are LOWERCASE and its
env names are the uppercase ones above:

============================  ======================================
Field (on ``Config.decision``)  Type / default
============================  ======================================
``base_url``                   ``str = ""``
``api_key``                    ``str = ""``
``model``                      ``str = ""``
``timeout_s``                  ``float = 5.0``
``cost_ceiling_usd``           ``float = 50.0``
``token_ceiling``              ``int = 10_000_000``
``pricing_usd_per_million_tokens``  ``str = ""`` (a JSON object string)
``global_enabled``             ``bool = False``
``seams``                      a sub-model:
                                ``snapshot_l3_selection_enabled: bool = False``
                                ``snapshot_l3_selection_stage: str = "shadow"``
============================  ======================================

The managed-key ``config_path`` chains are ``decision.global_enabled``,
``decision.seams.snapshot_l3_selection_enabled`` and
``decision.seams.snapshot_l3_selection_stage``.

Reuse rules (non-negotiable, ``thin-client.md`` §3):

1. **No new key material** — the credential defaults to the existing
   ``OPENAI_API_KEY``; this module never mints one.
2. ``DECISION_MODEL`` is a **purpose-id literal** (``"decision"``) —
   **never** ``OPENAI_MODEL``. The purpose id is already a member of
   ``_ALLOWED_MODELS_DEFAULT`` (``daemon/config.py:5925``); this
   module does NOT re-add it.
3. The base URL ALREADY carries ``/v1`` — the client appends only
   ``/decisions`` and NEVER ``/v1`` again.
4. **No discovery** — the model id is configuration, never a lookup.

**THE COST MODEL (L8).** There is NO pricing table in the ensemble or
in the proxy: the proxy meters TOKENS, and the decision lane's ``usage``
block is tokens-only. So the ceiling is computed WITHOUT inventing a
fake price table:

* the provider's reported cost wins (when it responds with one);
* otherwise an **operator-configured** per-model USD-per-million-token
  rate converts the metered tokens;
* otherwise the consult is metered in TOKENS, a ``pricing_unconfigured``
  suppression is emitted, and the configured TOKEN ceiling is the
  fail-safe — the control is never ABSENT, it degrades VISIBLY.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from typing import Any, Mapping

__all__ = [
    "DEFAULT_COST_CEILING_USD",
    "DEFAULT_DECISION_MODEL",
    "DEFAULT_TOKEN_CEILING",
    "DEFAULT_TIMEOUT_S",
    "DecisionConfig",
    "GLOBAL_ENABLED_KEY",
    "has_explicit_decision_signal",
    "seam_switch_tier",
]


#: The global master's store key (the yaml tier's key too).
GLOBAL_ENABLED_KEY = "decision_global_enabled"


#: The purpose-id literal (IP-1) — the allowlist member with no other
#: consumer. NEVER ``OPENAI_MODEL``.
DEFAULT_DECISION_MODEL = "decision"

#: The per-lane timeout budget in seconds (``thin-client.md`` §4.2).
#: S1 (snapshot L3 selection) rides a tool-blocking path ⇒ 5 s.
DEFAULT_TIMEOUT_S = 5.0

#: The ratified global cost ceiling — **USD 50/month** (FLAG1).
DEFAULT_COST_CEILING_USD = 50.0

#: The fail-safe TOKEN ceiling (L8) — the control is never absent.
#: 10 M tokens/month is ~4 M tokens/day at the S1 cadence.
DEFAULT_TOKEN_CEILING = 10_000_000


@dataclass(frozen=True)
class DecisionConfig:
    """The resolved decision-lane configuration.

    Attributes:
        base_url: The proxy base — it ALREADY carries ``/v1``.
        api_key: The bearer credential (reused, never minted).
        model: The purpose-id (``"decision"`` by default).
        timeout_s: The per-lane wall-clock budget.
        cost_ceiling_usd: The GLOBAL month-to-date USD ceiling.
        token_ceiling: The GLOBAL month-to-date TOKEN ceiling — the
            fail-safe used when no USD rate is available for the model.
        pricing_usd_per_million_tokens: The OPERATOR-CONFIGURED rate
            table, keyed by model id. Empty ⇒ the token path is used.
    """

    base_url: str = ""
    api_key: str = ""
    model: str = DEFAULT_DECISION_MODEL
    timeout_s: float = DEFAULT_TIMEOUT_S
    cost_ceiling_usd: float = DEFAULT_COST_CEILING_USD
    token_ceiling: int = DEFAULT_TOKEN_CEILING
    pricing_usd_per_million_tokens: Mapping[str, float] = field(default_factory=dict)
    global_enabled: bool = False
    seams: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    #: True only when an EXPLICIT decision signal was present at
    #: resolution time (``DECISION_BASE_URL`` / ``DECISION_API_KEY`` in
    #: the env, or a ``decision:`` block in config.yaml). The VALUES may
    #: still have fallen back to ``OPENAI_*`` — that is the POINT of
    #: keeping the two questions separate.
    explicitly_configured: bool = False

    # ── the two questions (Q2) ──────────────────────────────────────────

    @classmethod
    def has_explicit_decision_signal(
        cls,
        *,
        env: Mapping[str, str] | None = None,
        yaml_block: Any = None,
    ) -> bool:
        """True only when an EXPLICIT decision signal is present.

        * ``DECISION_BASE_URL`` or ``DECISION_API_KEY`` in the
          environment — the DECISION_* names, NOT their ``OPENAI_*``
          counterparts;
        * OR a ``decision:`` block in ``config.yaml`` — read through the
          house accessor ``daemon/config.py:7210::raw_yaml_decision``
          (``None`` when the file has no ``decision:`` section).

        Merely INHERITING ``OPENAI_BASE_URL`` / ``OPENAI_API_KEY`` is
        NOT a signal: the ``decision`` row routes to a PAID upstream,
        so an operator who never opted in must never be billed.
        """
        resolved_env: Mapping[str, str] = os.environ if env is None else env
        if _clean(_resolve(resolved_env, "DECISION_BASE_URL")):
            return True
        if _clean(_resolve(resolved_env, "DECISION_API_KEY")):
            return True
        block = yaml_block if yaml_block is not None else _raw_yaml_decision_block()
        if isinstance(block, Mapping) and bool(block):
            return True
        if block is not None and not isinstance(block, Mapping):
            # A non-mapping section is still a literally-present section.
            return True
        return False

    @classmethod
    def resolve_values(
        cls,
        *,
        env: Mapping[str, str] | None = None,
        source: Any = None,
        yaml_block: Any = None,
    ) -> "DecisionConfig":
        """The VALUE resolver — the reuse fallbacks, never the predicate.

        Once :meth:`is_configured` is true, the values may fall back
        (``DECISION_API_KEY`` value ← ``OPENAI_API_KEY``;
        ``DECISION_BASE_URL`` value ← ``OPENAI_BASE_URL``). The returned
        object carries ``explicitly_configured`` — the predicate's
        answer at resolution time.
        """
        resolved_env: Mapping[str, str] = os.environ if env is None else env
        the_source = resolved_env if source is None else source
        config = cls.from_source(the_source)
        return replace(
            config,
            explicitly_configured=cls.has_explicit_decision_signal(
                env=resolved_env, yaml_block=yaml_block
            ),
        )

    def is_configured(self) -> bool:
        """The CONFIGURED predicate — an EXPLICIT decision signal.

        Merely inheriting the ``OPENAI_*`` values is not enough: the
        ``decision`` row routes to a PAID upstream, so a module the
        operator never opted into must never be consulted (and never
        billed).
        """
        return self.explicitly_configured

    # ── resolution ──────────────────────────────────────────────────────

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "DecisionConfig":
        """Resolve from the environment alone (the standalone path).

        Thin wrapper over :meth:`resolve_values`.
        """
        return cls.resolve_values(env=env)

    @classmethod
    def from_source(cls, source: Any) -> "DecisionConfig":
        """Resolve the VALUES from an injected house config object.

        The VALUES only — the configured predicate lives in
        :meth:`is_configured` / :meth:`has_explicit_decision_signal`
        and is stamped by :meth:`resolve_values`.

        The injected object is the wiring round's ``Config.decision``
        namespace (L6) — read by its LOWERCASE field names, with the
        uppercase env names as a tolerant fallback and the ``OPENAI_*``
        reuse rules applied underneath.

        The reuse rules are applied EXACTLY as the house's own
        purpose-bound-model precedent does
        (``daemon/tools/image_gen.py:263-292``):
        ``base_url = (src.get("DECISION_BASE_URL") or src.get("OPENAI_BASE_URL") or "").strip()``
        — REUSE, never mint — and ``OPENAI_MODEL`` is NEVER read.
        """

        def field(*names: str) -> Any:
            """The first present candidate (lowercase field first)."""
            for name in names:
                value = _resolve(source, name)
                if value is not None:
                    return value
            return None

        base_url = _clean(field("base_url", "DECISION_BASE_URL")) or _clean(
            field("OPENAI_BASE_URL")
        )
        api_key = _clean(field("api_key", "DECISION_API_KEY")) or _clean(
            field("OPENAI_API_KEY")
        )
        raw_model = field("model", "DECISION_MODEL")
        model = DEFAULT_DECISION_MODEL if raw_model is None else _clean(raw_model)
        return cls(
            base_url=base_url.rstrip("/"),
            api_key=api_key,
            model=model,
            timeout_s=_positive_float(
                field("timeout_s", "DECISION_TIMEOUT_S"), DEFAULT_TIMEOUT_S
            ),
            cost_ceiling_usd=_positive_float(
                field("cost_ceiling_usd", "DECISION_COST_CEILING_USD"),
                DEFAULT_COST_CEILING_USD,
            ),
            token_ceiling=_positive_int(
                field("token_ceiling", "DECISION_TOKEN_CEILING"),
                DEFAULT_TOKEN_CEILING,
            ),
            pricing_usd_per_million_tokens=_rate_table(
                field(
                    "pricing_usd_per_million_tokens",
                    "DECISION_PRICING_USD_PER_MILLION_TOKENS",
                )
            ),
            global_enabled=_as_bool(
                field("global_enabled", "DECISION_GLOBAL_ENABLED")
            ),
            seams=_seam_tier(source),
        )

    def yaml_switch_tier(self) -> dict[str, Any]:
        """The YAML tier of the switch ladder, keyed by the STORE keys.

        The kill resolver's ladder is DB > env > yaml > default; this
        mapping is the yaml tier (``decision_global_enabled``,
        ``decision_seam_<flat>_enabled``, ``decision_seam_<flat>_stage``).
        """
        return seam_switch_tier(self)

    # ── derived state ───────────────────────────────────────────────────

    @property
    def configured(self) -> bool:
        """True when the lane may call out at all (rows 1–3 of §6)."""
        return bool(self.base_url and self.api_key and self.model)

    @property
    def decisions_url(self) -> str:
        """The full endpoint — ``{base}/decisions``.

        The base ALREADY carries ``/v1``; ``/v1`` is NEVER appended
        again (``thin-client.md`` §3 rule 3, PX-7).
        """
        return f"{self.base_url}/decisions"

    def rate_for(self, model: str | None = None) -> float | None:
        """The operator-configured USD-per-million-token rate for a model.

        ``None`` means "no rate configured for this model" — the caller
        then falls back to the TOKEN ceiling (L8).
        """
        target = model or self.model
        rate = self.pricing_usd_per_million_tokens.get(target)
        if rate is None:
            return None
        value = float(rate)
        return value if value > 0 else None

    def headers(self) -> dict[str, str]:
        """The request headers (mirroring the transport template, IP-3).

        ``Authorization`` carries the bearer token; the auth header is
        NEVER logged or recorded (``controls-and-phasing.md`` §2.6).
        """
        return {
            "Authorization": f"Bearer {self.api_key}",
            "x-proxy-app": "ensemble",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }


# ── resolution helpers ──────────────────────────────────────────────────


def _raw_yaml_decision_block() -> Any:
    """The house ``decision:`` yaml section — ``raw_yaml_decision()``.

    ``daemon/config.py:7210`` returns the RAW processed ``decision``
    section dict from the most recent ``load_config()`` call, or
    ``None`` when the loaded file had no ``decision:`` section (or
    ``load_config`` has not run). Imported lazily so a standalone use
    (a test, a tool) never drags the house config module in.
    """
    try:
        from daemon.config import raw_yaml_decision
    except Exception:  # noqa: BLE001 — the accessor is optional here
        return None
    try:
        return raw_yaml_decision()
    except Exception:  # noqa: BLE001
        return None


def _resolve(source: Any, path: str) -> Any:
    """Walk a DOTTED path over an object or a mapping.

    ``None`` when any step is absent — the caller then falls back to
    the next candidate name.
    """
    if source is None:
        return None
    node: Any = source
    for part in path.split("."):
        if node is None:
            return None
        if isinstance(node, Mapping):
            node = node.get(part)
        else:
            node = getattr(node, part, None)
    return node


def _as_bool(raw: Any) -> bool:
    """A tolerant boolean read (a bool, or the usual textual tokens)."""
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    return False


def seam_switch_tier(source: Any) -> dict[str, Any]:
    """Render a house config object into the switch ladder's YAML tier.

    The house ``Config.decision`` exposes ``global_enabled`` and a
    ``seams`` sub-model whose fields are ``<flat>_enabled`` /
    ``<flat>_stage`` (e.g. ``snapshot_l3_selection_enabled``). The tier
    is keyed by the STORE keys, which is what the kill resolver reads.
    """
    tier: dict[str, Any] = {}
    global_enabled = _resolve(source, "global_enabled")
    if global_enabled is not None:
        tier[GLOBAL_ENABLED_KEY] = "1" if _as_bool(global_enabled) else "0"
    for flat, values in _seam_tier(source).items():
        enabled = values.get("enabled")
        stage = values.get("stage")
        if enabled is not None:
            tier[f"decision_seam_{flat}_enabled"] = "1" if enabled else "0"
        if stage is not None:
            tier[f"decision_seam_{flat}_stage"] = str(stage)
    return tier


def _seam_tier(source: Any) -> dict[str, dict[str, Any]]:
    """Read the ``seams`` sub-model into the per-seam yaml tier.

    The sub-model exposes ``<flat>_enabled`` / ``<flat>_stage`` pairs
    (e.g. ``snapshot_l3_selection_enabled``). The tier is keyed by the
    FLATTENED seam id.
    """
    seams = _resolve(source, "seams")
    if seams is None:
        return {}
    if isinstance(seams, Mapping):
        items = list(seams.items())
    else:
        # Enumerate the DECLARED fields from the model CLASS, never from
        # the instance: instance-level ``model_fields`` /
        # ``model_computed_fields`` are deprecated (Pydantic V2.11) and go
        # away in V3. Non-model objects (plain classes / dataclasses)
        # carry no ``model_fields``; enumerate their public attributes via
        # ``__dict__`` instead of ``dir()`` so we never walk into the
        # deprecated instance-level attributes.
        model_fields = getattr(type(seams), "model_fields", None)
        if model_fields is not None:
            names = list(model_fields)
        else:
            names = []
            owners = [type(seams)] + list(type(seams).__mro__)
            owners.append(seams)
            for owner in owners:
                d = getattr(owner, "__dict__", None)
                if d is not None:
                    for name in d:
                        if not name.startswith("_") and name not in names:
                            names.append(name)
        items = [(name, getattr(seams, name)) for name in names]
    tier: dict[str, dict[str, Any]] = {}
    for name, value in items:
        # Shape (b): the resolved mapping form — flat → {enabled, stage}.
        if isinstance(value, Mapping) and (
            "enabled" in value or "stage" in value
        ):
            entry = tier.setdefault(str(name), {})
            if "enabled" in value:
                entry["enabled"] = _as_bool(value["enabled"])
            if "stage" in value:
                entry["stage"] = value["stage"]
            continue
        # Shape (a): the house sub-model form — <flat>_enabled/_stage.
        if name.endswith("_enabled"):
            tier.setdefault(name[: -len("_enabled")], {})["enabled"] = _as_bool(value)
        elif name.endswith("_stage"):
            tier.setdefault(name[: -len("_stage")], {})["stage"] = value
    return tier


def _clean(value: Any) -> str:
    """A stripped string; ``None`` for anything that is not a string."""
    if not isinstance(value, str):
        return ""
    return value.strip()


def _positive_float(raw: Any, default: float) -> float:
    """Parse a positive float, falling back to ``default``."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _positive_int(raw: Any, default: int) -> int:
    """Parse a positive int, falling back to ``default``."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _rate_table(raw: Any) -> dict[str, float]:
    """Parse the operator rate table.

    Accepts a mapping (``{"decision": 1.20}``) or a JSON object string
    (``'{"decision": 1.20}'``). An unparseable value yields an EMPTY
    table — which routes the consult onto the TOKEN ceiling (the
    control degrades visibly instead of vanishing).
    """
    if isinstance(raw, Mapping):
        table: dict[str, float] = {}
        for key, value in raw.items():
            try:
                table[str(key)] = float(value)
            except (TypeError, ValueError):
                continue
        return table
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        if isinstance(parsed, Mapping):
            return _rate_table(parsed)
    return {}

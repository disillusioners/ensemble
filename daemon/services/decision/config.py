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
from dataclasses import dataclass, field
from typing import Any, Mapping

__all__ = [
    "DEFAULT_COST_CEILING_USD",
    "DEFAULT_DECISION_MODEL",
    "DEFAULT_TOKEN_CEILING",
    "DEFAULT_TIMEOUT_S",
    "DecisionConfig",
]


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

    # ── resolution ──────────────────────────────────────────────────────

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "DecisionConfig":
        """Resolve from the environment alone (the standalone path)."""
        source: Mapping[str, str] = os.environ if env is None else env
        return cls.from_source(source)

    @classmethod
    def from_source(cls, source: Mapping[str, Any]) -> "DecisionConfig":
        """Resolve from an injected house config object or mapping.

        The injected object is the wiring round's ``Config.decision``
        namespace (L6). It is read by ATTRIBUTE (the house config is an
        object) or by KEY (a plain mapping) — the same object, two
        shapes.

        The reuse rules are applied EXACTLY as the house's own
        purpose-bound-model precedent does
        (``daemon/tools/image_gen.py:263-292``):
        ``base_url = (src.get("DECISION_BASE_URL") or src.get("OPENAI_BASE_URL") or "").strip()``
        — REUSE, never mint — and ``OPENAI_MODEL`` is NEVER read.
        """
        getter = _make_getter(source)
        base_url = (
            _clean(getter("DECISION_BASE_URL")) or _clean(getter("OPENAI_BASE_URL"))
        )
        api_key = _clean(getter("DECISION_API_KEY")) or _clean(getter("OPENAI_API_KEY"))
        raw_model = getter("DECISION_MODEL")
        model = (
            DEFAULT_DECISION_MODEL
            if raw_model is None
            else _clean(raw_model)
        )
        return cls(
            base_url=base_url.rstrip("/"),
            api_key=api_key,
            model=model,
            timeout_s=_positive_float(getter("DECISION_TIMEOUT_S"), DEFAULT_TIMEOUT_S),
            cost_ceiling_usd=_positive_float(
                getter("DECISION_COST_CEILING_USD"), DEFAULT_COST_CEILING_USD
            ),
            token_ceiling=_positive_int(
                getter("DECISION_TOKEN_CEILING"), DEFAULT_TOKEN_CEILING
            ),
            pricing_usd_per_million_tokens=_rate_table(
                getter("DECISION_PRICING_USD_PER_MILLION_TOKENS")
            ),
        )

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


def _make_getter(source: Any) -> Any:
    """A ``get(name)`` callable over a mapping OR an object."""
    if source is None:
        return lambda name: None
    if isinstance(source, Mapping):
        return lambda name: source.get(name)
    return lambda name: getattr(source, name, None)


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

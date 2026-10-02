"""``ens_env_read`` — runtime self-read of the ensemble daemon's LIVE environment.

Stage 1 of the OpenDesign self-provisioning chain (project
``agents-ensemble``, feature/od-self-provisioning, 2026-10-02). The
install-opendesign worker skill needs the ensemble's own LLM connection
values (``OPENAI_BASE_URL`` / ``OPENAI_API_KEY`` / ``OPENAI_MODEL``) to
reuse them downstream as the OpenDesign MCP ``BYOK_*`` fields. Per user
directive: "the .env file is the reference for values" — and the ``.env``
the running daemon loaded is exactly ``os.environ`` after
``launcher.sh`` ``load_env_file`` finishes.

Design summary
--------------

* **Source of truth** — the daemon's live ``os.environ`` at call time.
  This is the same env the LangGraph / LLMClient / persistence layers
  resolved at boot. Reading from any filesystem path (sibling
  ``.env``, ``INSTALL_DIR/.env``, ...) is intentionally avoided — the
  env-poison family of bugs (3 live incidents since 2026-09-21) traced
  dev boots to ambient / sibling ``.env`` reads; this tool's contract
  is the process env, period.

* **No redaction in the result** — the BYOK downstream writes the
  values verbatim (``configure-builtin`` payload carries the plaintext
  ``BYOK_BASE_URL`` + ``BYOK_MODEL``; ``BYOK_API_KEY`` rides KMS-Lite
  via ``kms_request`` / ``kms_attach``). The existing
  :func:`daemon.tools.system.system_env` ``nomask=True`` escape hatch
  is too narrow for the install-opendesign consumer: the worker skill
  needs an explicit, by-design unmasked read keyed on
  ``OPENAI_BASE_URL`` / ``OPENAI_API_KEY`` / ``OPENAI_MODEL`` without
  relying on a side-channel flag that a less-careful agent could
  forget.

* **Key-filter parameter** — the tool accepts an optional ``keys``
  list. When ``None`` (default), it returns a curated BYOK-relevant
  default set (``OPENAI_BASE_URL``, ``OPENAI_BASE_URL_BACKUP``,
  ``OPENAI_API_KEY``, ``OPENAI_MODEL``, plus a small set of
  ``ENSEMBLE_*`` runtime-identity keys so the install path can
  identify which env it's running in). When ``keys`` is supplied, only
  the requested names are read. Key names are case-sensitive
  (``os.environ`` semantics). Missing keys are returned as empty
  strings AND listed in a separate ``_missing`` array so the consumer
  can branch on absence without guessing.

* **Secret handling** — values are NEVER logged at any
  :func:`logging` call. The only audit signal is ``keys requested``
  (key names only) and ``result_size`` (count). Exception messages
  are caught and replaced with a generic class-name-only string so
  secret material cannot ride into the tool-result JSON via an
  unhandled exception (PB-F1 family exposure — tool results land in
  checkpoints; accepted for this scoped contract and called out
  explicitly in the docstring).

Category / visibility
---------------------

Single-tool category, opted into by listing ``"ens-env"`` in
``agents/<id>/meta.json`` ``tools.allow``. Worker opt-in lives in
``agents/worker/meta.json`` (the install-opendesign consumer lane).
The category IS in ``PRIVILEGED_TOOL_CATEGORIES`` since the W4
leader decision (reviewer council 2026-10-02): ``ens_env_read`` is
a key-returning tool, so the empty-allow inherit universe must NOT
auto-grant it — privileged default-deny makes the opt-in structural
(an agent reaches this category ONLY through an explicit
``tools.allow`` entry naming the category or its tool).

The ``open tools.allow``/metadata lookup path was rewritten in 2026-10
Stage 0 (commits 954e06cb + a1a05c24) — the ``_tools_allow`` closure
in ``daemon/manager.py`` MUST resolve via ``get_version(id)`` with
``get_resolved`` fallback (project pattern). The ``tools.allow``
list extended here (``ens-env`` for the worker agent) follows that
resolution path through ``daemon/tools/instance.py:resolve_tool_filter``.
Empty ``tools.allow`` continues to mean "inherit/default universe"
(F1b semantics) — this tool is EXCLUDED from that universe via the
privileged set (W4), so inherit never grants it; only an explicit
allow entry does.

Frozen-binary / ``KNOWN_TOOL_NAMES`` discipline: the name
``ens_env_read`` is also added to ``KNOWN_TOOL_NAMES`` (the
PyInstaller-fallback universe, ``daemon/tools/_tool_registry.py``)
so the frozen-binary ``discover_all_tool_names()`` fallback catches
it before any ``validate_tool_configs`` warning fires.  Bidirectional
drift is owned by
``tests/unit/tools/test_frozen_tool_name_discovery.py``.

Stage membership chain
----------------------

* Stage 1 (this tool) — read live env.
* Stage 2 (separate) — system promote via Ari upgrade lane.
* Stage 3 (separate) — install-opendesign skill v1.3.0 reads via
  ``ens_env_read`` and writes ``BYOK_*`` into the seam.
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Annotated, Any

from langchain_core.tools import tool
from pydantic import Field

from ._tool_registry import register_tool_category

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


CATEGORY_NAME = "Ensemble Environment"
CATEGORY_DOC = """\
Self-read of the ensemble daemon's live process environment.

- `ens_env_read` — read selected environment variables from the
  process env the running daemon actually loaded (NOT a sibling
  checkout's ``.env`` and NOT stale ``ensemble.json`` values).
  Values are returned in the clear (no masking) because the
  downstream BYOK writer (``configure-builtin`` for the
  OpenDesign MCP seam) writes the values verbatim; an explicit
  unmasked read keyed on ``OPENAI_BASE_URL`` /
  ``OPENAI_API_KEY`` / ``OPENAI_MODEL`` is the install-opendesign
  skill's contract.
"""


# Default BYOK-relevant key set returned when ``keys`` is None.
#
# Why this exact set (install-opendesign contract, v1.3.0):
#
# - ``OPENAI_BASE_URL`` — the primary LLM endpoint the daemon is
#   calling. Maps to BYOK_BASE_URL.
# - ``OPENAI_BASE_URL_BACKUP`` — the HA failover endpoint
#   (``LLMConfig.base_url_backup``). Not strictly required by
#   the BYOK contract but cheap to surface and useful when the
#   operator needs to mirror the failover topology.
# - ``OPENAI_API_KEY`` — the API credential. Maps to BYOK_API_KEY
#   via ``kms_attach`` (the marker seam, NOT plaintext).
# - ``OPENAI_MODEL`` — the default chat model. Maps to BYOK_MODEL.
# - ``OPENAI_MODEL_VISION`` — the vision model when set (helps
#   the BYOK schema decide whether to claim the v-model lane).
# - ENSEMBLE_SELF_ENV — the explicit self-env marker
#   (dev / demo / live / sandbox). Useful for the install
#   path's pre-flight "am I in the right env?" check.
# - ENSEMBLE_PORT — the daemon's HTTP API port (so the install
#   path can derive ``BASE`` without shell-out).
# - ENSEMBLE_DATA_DIR — the data dir (so the install path can
#   locate ``ensemble.json`` / mesh output if it ever needs to).
#
# The default set is intentionally narrow — it does NOT include
# ``POSTGRES_*`` or any non-LLM key. The install-opendesign
# contract is LLM-connection-only; broadening the default set
# leaks more than the consumer needs. Callers that want
# additional keys pass them via ``keys=[...]``.
_DEFAULT_KEYS: tuple[str, ...] = (
    "OPENAI_BASE_URL",
    "OPENAI_BASE_URL_BACKUP",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "OPENAI_MODEL_VISION",
    "ENSEMBLE_SELF_ENV",
    "ENSEMBLE_PORT",
    "ENSEMBLE_DATA_DIR",
)


def _audit(
    current_instance_id: str,
    requested_keys: tuple[str, ...],
    result_size: int,
    missing_count: int,
) -> None:
    """Audit-trail log for ``ens_env_read`` calls.

    Logs ONLY key NAMES and shape statistics — never values. This
    is the secret-handling seam called out in the module docstring:
    even an exception path that bubbles up ``str(e)`` MUST NOT carry
    secret material into the log; the helper is the single point
    where audit output is emitted.

    Args:
        current_instance_id: Caller's instance id (for per-instance
            attribution; not a secret).
        requested_keys: The exact key list the caller asked for (key
            names, no values).
        result_size: Number of entries returned in the result dict
            (including ``_missing`` and ``_keys_requested`` meta
            entries).
        missing_count: How many of the requested keys were not set
            in ``os.environ``.
    """
    try:
        logger.info(
            "[ens_env_read] instance=%s keys_requested=%d result_size=%d missing=%d",
            current_instance_id or "<unknown>",
            len(requested_keys),
            result_size,
            missing_count,
        )
    except Exception:
        # Audit logging must NEVER propagate an exception — the tool
        # contract is "errors as JSON, never raises".
        pass


def create_ens_env_tools(
    manager: Any | None = None,
    current_instance_id: str = "",
) -> list:
    """Create the ``ens-env`` category tools (single-tool category).

    Factory pattern matches the house convention (see ``create_system_tools``,
    ``create_snapshot_tools``, ``create_service_tools``). The ``manager``
    argument is accepted for parity with sibling factories but is NOT
    dereferenced — the tool reads ``os.environ`` directly because that
    is the canonical source-of-truth for the live daemon env (see the
    module docstring's "Source of truth" section). Wiring is purely for
    uniformity with the ``create_instance_tools`` factory list.

    Args:
        manager: Unused — accepted for parity with sibling factories.
            The tool reads ``os.environ`` directly; no manager
            dereferencing at call time. Reserved for future versions
            that may need to read manager.config.resolved_at for a
            freeze-aware read.
        current_instance_id: The current instance id (audit log
            attribution only — not a secret).

    Returns:
        A list containing exactly one tool:
        [``ens_env_read``].
    """
    # Capture once (defensive regression guard against future closure
    # code that may rebind the param name). Mirrors the pattern in
    # ``create_snapshot_tools`` (``caller_instance_id`` snapshot).
    caller_instance_id: str = current_instance_id or ""

    @register_tool_category("ens-env")
    @tool
    async def ens_env_read(
        keys: Annotated[
            list[str] | None,
            Field(
                default=None,
                description=(
                    "Optional list of env var names to read from the daemon's "
                    "loaded process environment. When None, returns the "
                    "default BYOK-relevant key set (OPENAI_BASE_URL, "
                    "OPENAI_API_KEY, OPENAI_MODEL, etc.). Case-sensitive "
                    "match against os.environ. Missing keys are returned "
                    "as empty string AND listed in the result's '_missing' "
                    "array."
                ),
            ),
        ] = None,
    ) -> str:
        """Read live env vars from the running ensemble daemon. Use tool_help("ens_env_read") for details."""
        try:
            # Resolve the effective key list. None → defaults; empty
            # list is honored verbatim (caller asked for nothing —
            # still returns shape with empty _missing / _keys_requested).
            if keys is None:
                effective_keys: tuple[str, ...] = _DEFAULT_KEYS
            else:
                # Dedupe while preserving order — agents occasionally
                # pass the same key twice; the result dict cannot have
                # duplicate keys so we collapse here, before any read.
                seen: set[str] = set()
                deduped: list[str] = []
                for k in keys:
                    if not isinstance(k, str):
                        # Type guard: list[str] is the declared type, but
                        # LLM-driven callers sometimes pass ints/None.
                        # Skip non-string entries (they can never match
                        # os.environ keys anyway) and surface the
                        # rejection in the error envelope below.
                        return json.dumps(
                            {
                                "error": (
                                    f"keys must be a list of strings; "
                                    f"got entry of type {type(k).__name__}"
                                )
                            }
                        )
                    if k not in seen:
                        seen.add(k)
                        deduped.append(k)
                effective_keys = tuple(deduped)

            # Read at call time — the live truth. The ``os.environ``
            # mapping is updated as the daemon's process env changes
            # (e.g. via launcher.sh ``load_env_file`` re-export),
            # so this read is call-time accurate.
            result: dict[str, str] = {}
            missing: list[str] = []
            for name in effective_keys:
                value = os.environ.get(name)
                # ``os.environ.get`` returns None for absent keys AND
                # for keys explicitly set to empty string. We treat
                # both as "missing" because the consumer cares about
                # "did the daemon load a value for it" — empty is
                # indistinguishable from unset for our purposes
                # (Pydantic Settings treats empty string as unset
                # for env-prefix overlap; bare ``KEY=`` lines in
                # .env produce the documented default, NOT an
                # empty string — see launcher.sh CHANGELOG entry
                # 2026-09-XX).
                if value is None or value == "":
                    result[name] = ""
                    missing.append(name)
                else:
                    result[name] = value

            # Shape contract: caller MUST be able to distinguish
            # "asked for it but not set" (legitimate empty value)
            # from "didn't ask" (no entry at all). The result
            # carries every requested key with value="" when missing
            # AND a parallel ``_missing`` array so consumers can
            # branch without scanning.
            payload: dict[str, Any] = {
                "_keys_requested": list(effective_keys),
                "_missing": missing,
                **result,
            }

            # Audit: log shape statistics only, NEVER values.
            _audit(
                caller_instance_id,
                effective_keys,
                result_size=len(payload),
                missing_count=len(missing),
            )

            return json.dumps(payload, indent=2, sort_keys=False)
        except Exception as exc:
            # Catch-all: NEVER raise (PB-F1 family — tool results
            # land in checkpoints; exception messages can carry
            # ambient context). Surface a class-name-only error
            # envelope so the agent sees a structured failure and
            # the secret does NOT ride into the tool result.
            #
            # We log the type but NOT the message; the ``str(exc)``
            # payload is intentionally dropped. Tests pin this
            # behaviour.
            try:
                logger.warning(
                    "[ens_env_read] failed for instance=%s: %s",
                    caller_instance_id or "<unknown>",
                    type(exc).__name__,
                )
            except Exception:
                pass
            return json.dumps({"error": f"ens_env_read failed: {type(exc).__name__}"})

    ens_env_read._full_doc_ = """Read selected environment variables from the live ensemble daemon's process environment.

**This tool returns REAL VALUES for the requested keys — no masking, no
redaction.** The contract is intentional: the BYOK downstream writer
(``configure-builtin`` for the OpenDesign MCP seam) writes
``BYOK_BASE_URL`` + ``BYOK_MODEL`` plaintext and binds ``BYOK_API_KEY``
via the KMS-Lite marker seam (``kms_request`` + ``kms_attach``). The
existing :func:`daemon.tools.system.system_env` masks secrets and the
``nomask=True`` escape hatch is per-call brittle — the
install-opendesign skill needs an explicit unmasked read of exactly
the BYOK-relevant keys, every call.

Args:
    keys: Optional list of env var names (strings). When ``None``
        (default), returns the curated BYOK-relevant default set:
        ``OPENAI_BASE_URL``, ``OPENAI_BASE_URL_BACKUP``,
        ``OPENAI_API_KEY``, ``OPENAI_MODEL``, ``OPENAI_MODEL_VISION``,
        ``ENSEMBLE_SELF_ENV``, ``ENSEMBLE_PORT``,
        ``ENSEMBLE_DATA_DIR``. When a list is supplied, ONLY the
        requested names are read. Key names are case-sensitive
        (``os.environ`` semantics). Duplicate names are deduplicated
        before reading. Non-string entries (e.g. ``None``/``int``)
        return a single ``{"error": ...}`` envelope — never partial
        output.

Returns:
A JSON object with the following shape::

    {
      "_keys_requested": ["OPENAI_BASE_URL", "OPENAI_API_KEY", ...],
      "_missing": ["OPENAI_API_KEY"],
      "OPENAI_BASE_URL": "https://api.openai.com/v1",
      "OPENAI_API_KEY": "",          // empty when not set
      "OPENAI_MODEL": "gpt-4",
      ...
    }

* ``_keys_requested`` — the effective key list (after default-merge
  and dedup). Re-invoking with this list is idempotent.
* ``_missing`` — the subset of ``_keys_requested`` whose value was
  empty/missing in ``os.environ``. Empty list ``[]`` means all
  requested keys were set. Use this to branch on absence without
  scanning every value.
* Every requested key appears as a top-level entry. Missing keys
  carry ``""`` as their value (callers that want presence-test
  should check ``_missing`` instead).

Source of truth: ``os.environ`` at call time. This is the env the
running daemon actually loaded after ``launcher.sh`` ``load_env_file``
finishes — NOT a sibling checkout's ``.env``, NOT a stale
``ensemble.json`` value, NOT the YAML default. The persistence layer
("checkpointer log LIES" — config values logged at startup can lag
resolved env values) is intentionally NOT consulted; the process env
is authoritative for this contract.

When any I/O fails, the tool returns a structured error envelope::

    {"error": "ens_env_read failed: <ExceptionClassName>"}

The class name only — never the exception message. This guards the
PB-F1 family exposure where tool results ride into checkpoints;
exception messages can carry ambient context (env dumps, stack
traces) and we deliberately drop them.

Secret handling:

* **Values are NEVER logged.** The audit trail logs key NAMES
  (length) and shape statistics (result size, missing count) only.
  See :func:`_audit`.
* **Exception messages are NEVER included in the result.** Only
  the class name surfaces — see the error-envelope path.
* **No redaction in the value path.** Values are returned in the
  clear because the BYOK downstream writes them verbatim.

Tool-call results land in LangGraph checkpoints (PB-F1 family
exposure — known and accepted for this scoped contract). The
install-opendesign consumer is the worker skill lane; only
agents that explicitly opt into the ``ens-env`` category in
``agents/<id>/meta.json`` ``tools.allow`` can call this tool.
"""

    return [ens_env_read]

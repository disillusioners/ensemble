"""Inline-LLM judge for leader completion reports (fused, Stage 3).

The LCA unified resolver's SINGLE judge call site (Stage 2 flip
2026-09-16; Stage 3 retirement 2026-09-17 deleted the two legacy
window-judge entry points and their graph-node call sites). The graph
node's fused block invokes
:func:`judge_fused_bundle_async` ONCE per evaluation (when the
activation predicate fires and the judge plan is on): the judge calls
an inline LLM (direct chat completion — NOT an instance spawn) over
the fused A+B+C evidence bundle. Verdict ``complete`` can rescue an
otherwise-deny row (allow END without the toolcall); every other
verdict (not_complete / error / timeout / unparsable×2) maps
conservatively (DP-5 REJECTED — no fail-safe allow anywhere).

Conservative semantics — every failure path returns
``is_complete=False`` so the caller's deny+nudge / path-(d) mapping is
always available. The judge is best-effort:

* **LLM error / timeout / unparsable JSON** ⇒ :class:`FusedJudgeResult`
  with ``is_complete=False`` and ``verdict in {"error", "timeout",
  "unparsable"}`` (retry-once on unparsable OR timeout — 2 HTTP
  attempts within ONE logical invocation, the preserved 98b59dd7
  contract + the bc145c7e R1 supersession for the rescuer path).
* **LLM call succeeds AND parses with ``verdict: "complete"``** ⇒ the
  deny band resolves to ALLOW (rescue); marker/A bands plain-allow.

Public API
----------

* :class:`FusedJudgeResult` — the dataclass returned to the fused
  block (verdict + evidence_cited + advisory + model + latency_ms +
  error_class + attempt + first_unparsable_excerpt).
* :func:`resolve_judge_model` — honors ``OPENAI_MODEL_KEYWORDS`` with
  fallback to ``OPENAI_MODEL`` (mirrors the existing
  ``daemon/services/keyword_extraction.py`` resolution).
* :func:`judge_fused_bundle_async` — the ONE async entry point.

Model resolution
----------------

The judge uses ``config.llm.model_keywords`` when non-empty
(``OPENAI_MODEL_KEYWORDS`` env var via ``config.yaml`` interpolation).
When empty, it falls back to ``config.llm.model`` (``OPENAI_MODEL``).
This mirrors :func:`daemon.services.keyword_extraction.extract_keywords`
which is the canonical existing consumer of ``model_keywords``. The
behavior is HONEST in both directions: setting
``OPENAI_MODEL_KEYWORDS=quick`` selects a quick model (operator's
intent); leaving it unset uses the main ``OPENAI_MODEL``.

Bounds
------

* Timeout: :data:`JUDGE_TIMEOUT_S` seconds (default 180.0 since the
  2026-09-26 7d4a3bd9 amendment; env-tunable via
  ``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S`` per Pattern C
  restart-read resolver at
  :mod:`daemon.services.attestation_judge_timeout_resolver`; minimum
  clamp 5.0s — values below clamp to 5.0 with a one-shot WARN; restart
  required to flip). Belt-and-braces ``asyncio.wait_for`` wraps the
  facade-wrapped call; the facade's ``wall_clock_cap_s`` is the primary
  defense (the same pattern as ``daemon/services/keyword_extraction.py:
  405-408``). The default was bumped from 10.0s → 25.0s on 2026-09-07
  (operator tuning decision grounded in the tester live-LLM probe — see
  ``docs/setup.md`` rationale) → 180.0s on 2026-09-26 (incident
  7d4a3bd9: two 25s judge double-timeouts consumed deny slots and
  drove a COMPLETED-UNVERIFIED escalation; user accepts the 2×180s
  worst case for verdict reliability).
* Input cap: the fused bundle arrives pre-capped (≤14000 chars,
  per-section 3000/6000/3000/2000 (U), id-redacted) from
  :func:`daemon.services.attestation_resolver_activation.assemble_fused_bundle`
  — this module does NOT re-truncate.
* Output cap: :data:`FUSED_JUDGE_MAX_OUTPUT_CHARS` chars (default
  2048). The JSON verdict is small by construction; oversized payloads
  are truncated then re-parsed conservatively.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NamedTuple

from .attestation_judge_timeout_resolver import (
    DEFAULT_JUDGE_TIMEOUT_S,
    get_judge_timeout_s as _resolver_get_judge_timeout_s,
)

if TYPE_CHECKING:
    from ..config import Config
    from langchain_core.messages import BaseMessage


logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Bounds (single source of truth — referenced by tests + setup.md)
# ─────────────────────────────────────────────────────────────────────────────

#: Wall-clock cap (seconds) for the judge call. Async-wait_for backstop;
#: the HA facade's wall_clock_cap_s is the primary defense (retry-budget
#: stop_after_delay inside the tenacity retry loop). The default was
#: bumped from 10.0s to 25.0s on 2026-09-07 (operator tuning decision
#: grounded in the tester live-LLM probe — see module docstring
#: "Bounds" section + ``docs/setup.md``). The runtime value comes from
#: :func:`daemon.services.attestation_judge_timeout_resolver.get_judge_timeout_s`
#: (Pattern C restart-read env resolver); this constant is the canonical
#: DOCUMENTED default reference, not the runtime value. Function
#: signatures use ``timeout_s: float | None = None`` and resolve via the
#: resolver inside the body so callers that omit the kwarg get the
#: runtime-configured value.
JUDGE_TIMEOUT_S: float = DEFAULT_JUDGE_TIMEOUT_S


#: Fused-scoped max chars (UTF-8) of the LLM response we'll consider
#: before truncating to parse. Applied at the two truncation sites
#: inside :func:`judge_fused_bundle_async` (attempt 1 and attempt 2).
#: (Stage-3 history: this cap was introduced by the F-A fix
#: 2026-09-16 — the shared 400-char legacy cap truncated compliant
#: fused verdicts to unparsable×2. The legacy constant retired with
#: the legacy judge in Stage 3; this is now the ONLY output cap.)
#: Sized to cover the fused prompt's mandated payload with comfortable
#: headroom (5×120 evidence + 240 advisory + 240 rationale = 1080
#: minimum compliant, 2048 covers it and keeps room for
#: verbose-but-correct model output). Truncation still applies
#: (unbounded LLM output must never flow onward).
FUSED_JUDGE_MAX_OUTPUT_CHARS: int = 2048


#: Cap (chars) on the truncated raw-response excerpt stamped onto the
#: canonical log row when the LLM response was unparsable. The
#: excerpt is the forensic surface for incident-98b59dd7 (class D
#: judge false-negative — raw judge output was not logged, root
#: cause unrecoverable). Whitespace-normalized + secret-redacted
#: before stamping. Module-level constant by design — the only knob
#: is the LLM judge kill-switch ``ENSEMBLE_LEADER_ATTESTATION_LLM_
#: JUDGE_ENABLED`` (=0 disables the judge ENTIRELY, retries included
#: — the brief's "kill-switch OFF = zero judge calls INCLUDING zero
#: retries" contract).
JUDGE_EXCERPT_MAX_CHARS: int = 400

# ─────────────────────────────────────────────────────────────────────────────
# Model resolution — honors OPENAI_MODEL_KEYWORDS with fallback
# ─────────────────────────────────────────────────────────────────────────────


def resolve_judge_model(config: "Config") -> str:
    """Resolve the judge model honoring :attr:`LLMConfig.model_keywords`.

    Mirrors :func:`daemon.services.keyword_extraction.extract_keywords`
    resolution: ``config.llm.model_keywords`` when non-empty,
    ``config.llm.model`` otherwise. ``model_keywords`` is the
    ``OPENAI_MODEL_KEYWORDS`` env var (consumed via ``config.yaml``
    interpolation). Operators typically pin this to ``"quick"`` to
    mirror the explorer agent's per-instance model.

    Args:
        config: A loaded :class:`Config` (caller responsibility — the
            judge does not call ``load_config``; that lives in the
            wiring layer).

    Returns:
        The model string to use for the judge call. Empty string is
        impossible because :attr:`LLMConfig.model` defaults to
        ``"gpt-4"`` and :attr:`LLMConfig.model_keywords` always falls
        back to :attr:`LLMConfig.model` via
        :func:`LLMConfig.set_title_model_fallback`.
    """
    model_keywords = (config.llm.model_keywords or "").strip()
    return model_keywords or config.llm.model


# ─────────────────────────────────────────────────────────────────────────────
# JSON parsing — strict, conservative on any ambiguity
# ─────────────────────────────────────────────────────────────────────────────


_JSON_OBJECT_RE = re.compile(r"\{.*?\}", re.DOTALL)


# ─────────────────────────────────────────────────────────────────────────────
# Excerpt shaping — forensic logging for unparsable responses
# (incident 98b59dd7, 2026-09-16, class D judge false-negative)
# ─────────────────────────────────────────────────────────────────────────────


#: Patterns that look like secret-shaped strings — replaced with the
#: sentinel before stamping the excerpt onto the canonical log row.
#: Each pattern is a conservative regex; we redact on KEYWORD MATCH,
#: not on validation. Cases covered:
#:
#: * ``bearer <token>`` — Authorization header prefix + value
#: * ``api[_-]?key=<value>`` / ``api[_-]?key: <value>`` — query-style
#:   or JSON-style API key
#: * ``token=<value>`` / ``token: <value>`` — bearer-style tokens
#: * ``secret=<value>`` / ``secret: <value>`` — generic secrets
#:
#: These are deliberately conservative: the LLM does not normally
#: echo secrets, but a buggy proxy / a mis-trained model can.
#: the redaction is a defense-in-depth for log scraping — the
#: canonical log line never carries the LLM request payload at all
#: (the fused bundle is hashed, not logged). The
#: excerpt here is the RESPONSE, not the request, so the surface is
#: narrower; still, the kill cost of a stray key in the log row is
#: asymmetric.
_SECRET_SHAPE_PATTERNS: tuple[str, ...] = (
    # ``bearer <token>`` — Authorization header prefix + value.
    r"\bbearer\s+[A-Za-z0-9._\-]{8,}",
    # ``api_key`` / ``api-key`` / ``apikey`` in three shapes:
    #   * query-style: ``api_key=value``
    #   * env-style:   ``api_key=value``
    #   * JSON-style:  ``"api-key": "value"``  (the closing quote of
    #     the key is matched by the ``\W*`` separator)
    r"\bapi[_-]?key\W*[=:]\W*[A-Za-z0-9._\-]{8,}",
    # ``token=value`` (env / query) and ``"token": "value"`` (JSON).
    r"\btoken\W*[=:]\W*[A-Za-z0-9._\-]{8,}",
    # ``secret=value`` (env / query) and ``"secret": "value"`` (JSON).
    r"\bsecret\W*[=:]\W*[A-Za-z0-9._\-]{8,}",
)
_SECRET_REDACT_RE = re.compile(
    "(?i)" + "|".join(_SECRET_SHAPE_PATTERNS)
)
_SECRET_REDACT_SENTINEL: str = "[REDACTED]"

#: Trailing glue (``-``/``.``/``_``) stripped off a matched run and
#: re-emitted after the sentinel, so run-final punctuation (e.g. a
#: sentence-final dot after ``key=value.``) survives redaction instead
#: of being swallowed into ``[REDACTED]``. Glue chars alone are never
#: secret material, so the trim is leak-free.
_SECRET_TRAILING_GLUE_CHARS = "-._"


def _redact_secrets(text: str) -> str:
    """Replace secret-shaped substrings with :data:`_SECRET_REDACT_SENTINEL`.

    Defense-in-depth for the unparsable-excerpt log surface. Conservative
    regex match — false positives (legitimate prose mentioning "token
    economy" or "bearer of good news") are acceptable because the
    redaction sentinel is a clear signal to operators. False negatives
    (a real key that does not match the patterns) are still
    :data:`JUDGE_EXCERPT_MAX_CHARS` chars away from the full log row
    — the canonical log row already elides request-side secrets via the
    upstream truncation, and the response-side surface is narrower.

    Boundary policy (incident 98b59dd7 review): the secret run ends at
    the first char outside ``[A-Za-z0-9._\\-]``. Prose separated from
    the run by whitespace/punctuation is preserved; run-final glue is
    re-emitted after the sentinel. A run GLUED to prose with no
    delimiter has an undetectable boundary — every char is class-valid
    — so the whole run is redacted (safe-by-default; we do not guess
    where the secret ends). A trailing ``(?!\\w)`` lookahead was
    evaluated and rejected: no-op for glued ASCII shapes (the run
    already ends at whitespace/EOL, where the lookahead holds) and it
    un-redacts Unicode-adjacent matches (backtracking fails the whole
    pattern, leaking the ASCII secret fragment).
    """
    if not text:
        return text

    def _replace(match: "re.Match[str]") -> str:
        run = match.group(0)
        stripped = run.rstrip(_SECRET_TRAILING_GLUE_CHARS)
        glue = run[len(stripped):]
        return _SECRET_REDACT_SENTINEL + glue

    return _SECRET_REDACT_RE.sub(_replace, text)


def _truncate_excerpt(text: str, *, cap: int = JUDGE_EXCERPT_MAX_CHARS) -> str:
    """Truncate ``text`` to ``cap`` chars after whitespace-normalization.

    The canonical log row carries the excerpt verbatim; we want a
    bounded, grep-friendly surface. Steps:

    1. Collapse runs of whitespace to a single space (so a runaway
       LLM that emits 1000 newlines does not stretch the log row).
    2. Strip leading / trailing whitespace.
    3. Truncate to ``cap`` chars; if truncation happened, append an
       explicit ``" [truncated]"`` tail marker (12 chars including
       the space) so operators can tell the excerpt was cut from an
       excerpt that just happened to end mid-sentence.
    """
    if not text:
        return ""
    # Normalize whitespace: collapse runs to a single space.
    normalized = " ".join(text.split())
    if len(normalized) <= cap:
        return normalized
    # The trailing marker is included in the cap budget — the marker
    # itself is 12 chars ("[truncated]" + leading space).
    tail_marker = " [truncated]"
    keep = max(0, cap - len(tail_marker))
    return normalized[:keep] + tail_marker


def _shape_unparsable_excerpt(raw_text: str) -> str:
    """Compose the final log-ready excerpt from a raw LLM response.

    Chained helper — applies :func:`_redact_secrets` then
    :func:`_truncate_excerpt` to the raw LLM text. The order matters:
    redaction first (operates on the raw value so we don't lose secret
    markers to whitespace collapse), then truncation. The resulting
    string is the forensic surface stamped onto the canonical log row
    for ``verdict="unparsable"`` outcomes.
    """
    return _truncate_excerpt(_redact_secrets(raw_text))


# ─────────────────────────────────────────────────────────────────────────────
# LLM call — async, bounded, fail-safe
# ─────────────────────────────────────────────────────────────────────────────


async def _invoke_judge_llm(
    config: "Config",
    user_payload: str,
    *,
    timeout_s: float,
    system_prompt: str,
) -> tuple[str, str]:
    """Run the judge's LLM call. Returns ``(raw_text, model)``.

    Constructs a fresh ``ThinkingChatOpenAI`` with the resolved
    quick-model + the standard HA failover facade (mirrors the
    pattern at ``daemon/services/keyword_extraction.py:384-387``).

    Args:
        config: Loaded :class:`Config`.
        user_payload: The formatted judge payload (single string).
        timeout_s: Wall-clock cap. The HA facade's
            ``wall_clock_cap_s`` is the primary defense; the
            ``asyncio.wait_for`` belt-and-braces wraps the entire
            ``to_thread`` invocation.
        system_prompt: The system prompt for THIS judge invocation —
            a REQUIRED argument. The fused judge
            (:func:`judge_fused_bundle_async`) passes
            :data:`FUSED_JUDGE_SYSTEM_PROMPT`; test seams may pass
            their own. (Stage 3: the retired legacy window judge's
            prompt-default was removed with the judge itself.)

    Returns:
        ``(raw_text, model_name)``. ``model_name`` is what
            :func:`resolve_judge_model` resolved at call time — the
            caller surfaces this on the result object.

    Raises:
        Any exception from the LLM call (caller catches and converts
        to a ``verdict="error"`` result).
        :class:`asyncio.TimeoutError` on timeout (catcher converts to
            ``verdict="timeout"``).
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    from ..graph import ThinkingChatOpenAI, clean_llm_config
    from .llm_failover import wrap_langchain_failover

    model = resolve_judge_model(config)
    # ``base_url_backup`` is consumed by the HA facade from the RAW
    # config dict (F1 kwarg hygiene — ``clean_llm_config`` mutates
    # in place and strips the backup).
    # ``request_timeout`` is bound per-attempt to
    # ``min(resolved_timeout, config.llm.request_timeout or resolved_timeout)``
    # mirroring the compaction-site precedent at ``daemon/manager.py:398``.
    # The ``resolved_timeout`` is the Pattern C cached value from
    # :mod:`daemon.services.attestation_judge_timeout_resolver`
    # (``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S``, default
    # 180.0s since the 2026-09-26 7d4a3bd9 amendment, min clamp
    # 5.0s). The HA facade's ``wall_clock_cap_s`` bounds only
    # BETWEEN attempts (``daemon/services/llm_failover.py:174``); a hung
    # FIRST attempt can pin the ``asyncio.to_thread`` worker because
    # ``asyncio.wait_for`` cannot cancel a to_thread worker — without a
    # per-attempt HTTP timeout the executor thread stays blocked for
    # the full ``config.llm.request_timeout`` (default 610s).
    resolved_timeout = _resolver_get_judge_timeout_s()
    judge_request_timeout = min(
        resolved_timeout,
        config.llm.request_timeout or resolved_timeout,
    )
    llm_config = {
        "base_url": config.llm.base_url,
        "base_url_backup": config.llm.base_url_backup,
        "api_key": config.llm.api_key,
        "model": model,
        "temperature": 0.0,
        "request_timeout": judge_request_timeout,
        # Judge has no tools — straight chat completion. No
        # ``bind_tools`` call.
        "default_headers": {
            "x-proxy-app": "ensemble",
            "x-proxy-interleaved-thinking": "True",
            **(
                {"X-LLMProxy-Buffer-Response": "true"}
                if config.llm.buffer_response_header
                else {}
            ),
        },
    }
    llm = ThinkingChatOpenAI(**clean_llm_config(dict(llm_config)))
    llm_wrapper = wrap_langchain_failover(
        llm, llm_config, wall_clock_cap_s=timeout_s
    )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_payload),
    ]
    response = await asyncio.wait_for(
        asyncio.to_thread(llm_wrapper.invoke, messages),
        timeout=timeout_s,
    )
    content: Any = getattr(response, "content", "")
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                parts.append(str(block.get("text", "")))
            else:
                parts.append(str(block))
        raw_text = " ".join(parts)
    else:
        raw_text = str(content) if content else ""
    return raw_text, model


# ─────────────────────────────────────────────────────────────────────────────
# Per-attempt plumbing + the fused public entry point
# ─────────────────────────────────────────────────────────────────────────────


#: Internal NamedTuple carrying the result of a single LLM attempt.
#: The retry logic in :func:`judge_fused_bundle_async` treats
#: ``ok`` / ``timeout`` / ``error`` distinctly:
#:
#: * ``ok`` → the model responded; ``raw_text`` is the body (may
#:   still be unparsable — the outer function decides whether to
#:   retry on unparsable JSON).
#: * ``timeout`` → the call raised :class:`asyncio.TimeoutError`;
#:   ``raw_text`` is empty; ``error_class`` is ``"TimeoutError"``.
#: * ``error`` → the call raised any other exception;
#:   ``raw_text`` is empty; ``error_class`` is the exception class
#:   name.
#:
#: ``latency_ms`` is the wall-clock latency of the attempt
#: (excludes overhead). The outer :class:`FusedJudgeResult.latency_ms`
#: is the CUMULATIVE latency across all attempts (so operators
#: can correlate log-row latency with retry count).
class _AttemptOutcome(NamedTuple):
    kind: str
    latency_ms: int
    raw_text: str
    model: str
    error_class: str | None


# ─────────────────────────────────────────────────────────────────────────────
# Stage-2 fused judge (resolver-unification, 2026-09-16) — ONE judge call site
#
# The unified 3-source completion resolver flips AUTHORITY at the completion
# seam (spec ``resolver-unification.md`` §4/§4.3 + user-locked deltas
# Δ1–Δ4, DP-5 REJECTED). The fused judge consumes the Stage-1 assembled
# evidence bundle (``attestation_resolver_activation.assemble_fused_bundle``
# — A ≤3000 + B ≤6000 + C ≤3000 + U ≤2000, total ≤14000, id-redacted) as its ENTIRE
# user payload and returns the §4.1 verdict JSON:
#
#     {"verdict": "complete"|"not_complete",
#      "evidence_cited": ["<short quote or field>", ...],
#      "advisory_note_text": "<one sentence>",
#      "rationale": "<one sentence>"}
#
# Transport invariants (:func:`_invoke_judge_llm` is the single LLM
# seam): model resolution via :func:`resolve_judge_model`
# (``model_keywords`` fallback), per-attempt ``request_timeout`` binding
# ``min(resolved_timeout, config.llm.request_timeout or resolved_timeout)``,
# the Pattern C timeout resolver
# (``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S``, default 180.0s
# since the 2026-09-26 7d4a3bd9 amendment, min clamp 5.0s), and the
# HA failover facade. The kill-switch
# (``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED``) is resolved at the
# CALL SITE (the graph-node fused block) — before this function is ever
# called.
#
# Retry-once-on-unparsable (incident 98b59dd7 semantics, carried over from
# the retired legacy judge) + retry-once-on-timeout (incident bc145c7e R1,
# 2026-09-19, supersedes the prior no-timeout-retry decision for the
# rescuer path — suppression rule makes judge reliability load-bearing
# + quick-model tail latency makes the class recurring): the retry fires
# on EITHER ``verdict="unparsable"`` (model responded, body did not
# parse) OR attempt-1 ``timeout``. SAME per-attempt timeout window for
# the retry; attempt accounting (``JudgeResult.attempt`` 1→2); DISTINCT
# log discrimination (``event=fused_judge_first_attempt_timeout``,
# ``event=fused_judge_timeout_retry``, ``event=fused_judge_timeout_post_retry``).
# HTTP/API errors keep NO retry (unchanged). Post-retry timeout still
# → conservative fail-safe deny (DP-5 posture unchanged). The retry
# fits the existing budget sentinel (``entries==1 && attempts<=2``)
# exactly like the unparsable retry. Worst-case wall-clock = 2 ×
# (``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S``, default 25.0s on 2026-09-07, raised to 180.0s on 2026-09-26 by the 7d4a3bd9 amendment)
# ``JUDGE_TIMEOUT_S``. Total worst-case wall-clock = 2 ×
# ``timeout_s`` (e.g., 360.0s with the 180.0s default cap).
# ─────────────────────────────────────────────────────────────────────────────

#: Strict system prompt for the fused judge (single source of truth —
#: exported for tests). The prompt names the four bundle sections so the
#: verdict's ``evidence_cited`` entries can reference them. SOURCE U
#: carries the user's original request + the intent-fulfillment
#: instruction (incident 4dfded83, 2026-09-18 — the judge was
#: intent-blind: it scored report-shape/tree-status while the user's
#: ask went unanswered by the bundle itself). The A/C-subordination
#: line is SOFTENED per the dual-autopsy B1 fix (2026-09-20): advisories
#: from children that later delivered, or whose pending item is an
#: operator action, are NOT evidence of undelivered work — while
#: subordination for GENUINE unresolved advisories is kept verbatim
#: (the false-rescue guard, council pin 1b343329, does NOT regress).
#: Byte-tail discipline: new sentences are inserted BEFORE the
#: "Be CONSERVATIVE" anchor; the strict-JSON contract tail stays
#: byte-stable (the retry/parser seam depends on it).
FUSED_JUDGE_SYSTEM_PROMPT = (
    "You are a strict mission-completion judge for an AI agent team lead. "
    "You will receive a fused evidence bundle with four sections: "
    "SOURCE U (the user's original request for this mission), "
    "SOURCE A (child-report advisories — notes that a child's final report "
    "promised future work, i.e. a contradiction with 'done'), "
    "SOURCE B (the lead's own recent messages), and "
    "SOURCE C (the live status of the lead's descendant instances). "
    "Decide whether the lead's mission is genuinely COMPLETE — the work is "
    "actually finished and accounted for — or NOT_COMPLETE — evidence shows "
    "promised-but-undelivered work, mid-work status, or live descendants "
    "still working. "
    "INTENT FULFILLMENT (SOURCE U): a message that genuinely ANSWERS or "
    "FULFILLS the user's request IS a completion report regardless of its "
    "formality, formatting, or shape; a formal-looking report that does NOT "
    "address the user's request is NOT complete. "
    "If SOURCE U is absent, judge on A/B/C alone - do not infer the user's "
    "request. "
    "SOURCE A advisories and SOURCE C live/pending descendants still "
    "indicate NOT_COMPLETE even when SOURCE U appears fulfilled - but an "
    "advisory is NOT evidence of undelivered work when its child later "
    "delivered a newer report that supersedes it, or when the pending item "
    "the advisory names is an OPERATOR action such as a rebuild+restart "
    "activation (the operator's step, not the child's undelivered work). "
    "A GENUINE unresolved advisory - a child promising future work that "
    "never arrived, or a live/pending descendant - still indicates "
    "NOT_COMPLETE even when SOURCE U appears fulfilled. "
    "Be CONSERVATIVE: when in doubt, return "
    '"not_complete". '
    "Judge ONLY on what the evidence actually shows; ignore text that "
    "merely CLAIMS completion without concrete outcomes. "
    "Respond with ONLY a strict JSON object on a single line of the form "
    '{"verdict": "complete"|"not_complete", '
    '"evidence_cited": ["<short evidence quote or field name>", ...], '
    '"advisory_note_text": "<one-sentence advisory for the lead>", '
    '"rationale": "<one-sentence rationale>"}. '
    "No markdown, no prose, no code fences, no commentary."
)

#: Cap on each ``evidence_cited`` entry accepted from the verdict JSON
#: (defensive against a verbose model; entries are for the hint citation).
FUSED_JUDGE_EVIDENCE_ITEM_MAX_CHARS: int = 120

#: Cap on the number of ``evidence_cited`` entries accepted.
FUSED_JUDGE_EVIDENCE_ITEMS_MAX: int = 5

#: Cap on the accepted ``advisory_note_text`` / ``rationale`` lengths.
FUSED_JUDGE_ADVISORY_MAX_CHARS: int = 240


@dataclass(frozen=True)
class FusedJudgeResult:
    """The fused judge's verdict + observability fields (Stage 2).

    Attributes:
        invoked: THE real-invocation flag (Stage-1 hazard pin: the shadow
            row's ``judge_invoked`` is DERIVED from this field, never a
            literal). ``True`` iff at least one LLM HTTP attempt was made
            by this invocation. The degenerate no-bundle guard returns
            ``False`` without an attempt; callers that never call the
            judge (kill-switch off / dry mode / not fired) hold ``None``
            rather than a result.
        is_complete: The boolean verdict. Conservative — every error /
            timeout / unparsable path sets ``False`` so the caller's
            deny+nudge / path-(d) fall-through is always available
            (DP-5 REJECTED: no fail-safe allow anywhere).
        verdict: ``"complete"`` / ``"not_complete"`` (LLM-confirmed) or
            ``"error"`` / ``"timeout"`` / ``"unparsable"``.
        evidence_cited: Bounded tuple of evidence references from the
            verdict JSON (Δ4 — the hint citation source). Empty on every
            non-complete-verdict path by construction.
        advisory_note_text: The verdict's one-sentence advisory (Δ4).
            Empty string on non-verdict paths.
        rationale: The verdict's one-sentence rationale.
        model: The model that served the call (``"<none>"`` on the
            no-bundle guard).
        latency_ms: Cumulative wall-clock latency across attempts.
        error_class: Exception class name on error paths; ``None``
            otherwise.
        attempt: Which attempt produced the verdict (``2`` whenever
            the retry fires — on EITHER the unparsable retry path
            (incident 98b59dd7) OR the timeout retry path (incident
            bc145c7e R1, 2026-09-19)). Always ``1`` on non-retry
            paths (HTTP/API error first-attempt, single-call success,
            single-call unparsable — wait, single-call unparsable
            IS a retry path; the only ``attempt=1`` paths are
            HTTP/API error first-attempt and single-call success).
        first_unparsable_excerpt: Truncated + redacted raw response of
            attempt 1 when the retry fired AND attempt 1 was an
            unparsable row (incident 98b59dd7 forensic contract).
            ``None`` otherwise — specifically ``None`` whenever
            ``attempt == 1`` AND whenever the retry fired on a
            timeout (attempt 1 left no body to redact/excerpt). The
            invariant is: ``first_unparsable_excerpt is not None iff
            attempt == 2 AND attempt 1 returned ok but did not parse``.
    """

    invoked: bool
    is_complete: bool
    verdict: str
    evidence_cited: tuple[str, ...] = ()
    advisory_note_text: str = ""
    rationale: str = ""
    model: str = "<none>"
    latency_ms: int = 0
    error_class: str | None = None
    attempt: int = 1
    first_unparsable_excerpt: str | None = None


def _parse_fused_judge_response(
    raw_text: str,
) -> tuple[bool, tuple[str, ...], str, str] | None:
    """Strictly parse the fused judge's verdict JSON.

    Tolerance shape (carried over verbatim from the retired legacy
    judge's parser — the single tolerated code-fence layer, the
    conservative single substring-fallback, first-match-wins) so the
    retry-on-unparsable trigger conditions are IDENTICAL to the
    historical contract. The
    load-bearing field is ``verdict`` — it must be exactly
    ``"complete"`` or ``"not_complete"`` (a string); anything else is
    unparsable. ``evidence_cited`` / ``advisory_note_text`` /
    ``rationale`` are tolerated-missing (normalized defaults) and
    defensively capped; wrong types normalize, never fail the parse.

    Returns:
        ``(is_complete, evidence_cited, advisory_note_text, rationale)``
        on parse success; ``None`` on any parse ambiguity (the caller
        treats ``None`` as ``unparsable`` — conservative).
    """
    if not raw_text:
        return None
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_OBJECT_RE.search(text)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(parsed, dict):
        return None
    verdict_value = parsed.get("verdict")
    if verdict_value not in ("complete", "not_complete"):
        return None
    is_complete = verdict_value == "complete"
    evidence: tuple[str, ...] = ()
    raw_evidence = parsed.get("evidence_cited")
    if isinstance(raw_evidence, (list, tuple)):
        items: list[str] = []
        for item in raw_evidence[:FUSED_JUDGE_EVIDENCE_ITEMS_MAX]:
            item_text = str(item).strip()
            if not item_text:
                continue
            if len(item_text) > FUSED_JUDGE_EVIDENCE_ITEM_MAX_CHARS:
                item_text = (
                    item_text[: FUSED_JUDGE_EVIDENCE_ITEM_MAX_CHARS - 1] + "…"
                )
            items.append(item_text)
        evidence = tuple(items)

    def _capped_str(value: object) -> str:
        text_value = value if isinstance(value, str) else str(value or "")
        text_value = text_value.strip()
        if len(text_value) > FUSED_JUDGE_ADVISORY_MAX_CHARS:
            return text_value[: FUSED_JUDGE_ADVISORY_MAX_CHARS - 3] + "..."
        return text_value

    return (
        is_complete,
        evidence,
        _capped_str(parsed.get("advisory_note_text", "")),
        _capped_str(parsed.get("rationale", "")),
    )


async def judge_fused_bundle_async(
    bundle_text: str,
    *,
    config: "Config",
    timeout_s: float | None = None,
) -> FusedJudgeResult:
    """Async fused judge — ONE logical invocation over the evidence bundle.

    Never raises. Every failure path (timeout / exception / unparsable
    verdict after retry) returns a :class:`FusedJudgeResult` with
    ``is_complete=False`` so the caller's conservative mapping
    (deny-band deny+nudge / marker+A-band path-(d)) is always available.
    DP-5 is REJECTED: judge error NEVER fail-safe-allows.

    Args:
        bundle_text: The Stage-1 assembled fused evidence bundle text
            (already capped ≤14000 chars + id-redacted by
            :func:`attestation_resolver_activation.assemble_fused_bundle`
            — this function does NOT re-truncate; the bundle is the
            payload verbatim).
        config: Loaded :class:`Config`.
        timeout_s: Wall-clock cap PER ATTEMPT. ``None`` (default) →
            resolve via the Pattern C cached-global
            (``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_
            TIMEOUT_S``, default 180.0s since the 2026-09-26
            7d4a3bd9 amendment, min clamp 5.0s). Each retry
            attempt receives its OWN timeout window; worst-case
            wall-clock = 2 × ``timeout_s``.

    Returns:
        :class:`FusedJudgeResult` — populated on every path.
        :attr:`FusedJudgeResult.attempt` distinguishes first-call
        outcomes (``attempt=1``) from retry outcomes (``attempt=2``);
        the retry fires when attempt 1 either timed out
        (``asyncio.TimeoutError``, incident bc145c7e R1, 2026-09-19)
        OR returned a verdict JSON that did not parse (incident
        98b59dd7, carried over from the retired legacy judge). HTTP /
        API errors keep NO retry (unchanged).
    """
    start = time.monotonic()
    if not bundle_text or not bundle_text.strip():
        # Degenerate guard (mirror of the no-AIMessages guard): a bundle
        # exists by construction whenever the resolver fires, but a
        # defensive empty input is a conservative not-complete error
        # WITHOUT an LLM attempt (invoked=False — no real invocation).
        return FusedJudgeResult(
            invoked=False,
            is_complete=False,
            verdict="error",
            rationale="empty fused bundle",
            error_class="EmptyBundle",
            latency_ms=int((time.monotonic() - start) * 1000),
        )
    if timeout_s is None:
        timeout_s = _resolver_get_judge_timeout_s()

    async def _attempt_once() -> "_AttemptOutcome":
        attempt_start = time.monotonic()
        try:
            raw_text, model_name = await _invoke_judge_llm(
                config,
                bundle_text,
                timeout_s=timeout_s,
                system_prompt=FUSED_JUDGE_SYSTEM_PROMPT,
            )
        except asyncio.TimeoutError:
            return _AttemptOutcome(
                kind="timeout",
                latency_ms=int((time.monotonic() - attempt_start) * 1000),
                raw_text="",
                model=resolve_judge_model(config),
                error_class="TimeoutError",
            )
        except Exception as exc:  # noqa: BLE001 — judge is best-effort
            return _AttemptOutcome(
                kind="error",
                latency_ms=int((time.monotonic() - attempt_start) * 1000),
                raw_text="",
                model=resolve_judge_model(config),
                error_class=type(exc).__name__,
            )
        return _AttemptOutcome(
            kind="ok",
            latency_ms=int((time.monotonic() - attempt_start) * 1000),
            raw_text=raw_text,
            model=model_name,
            error_class=None,
        )

    # Attempt 1.
    first = await _attempt_once()
    if first.kind == "timeout":
        # Retry-once-on-timeout (incident bc145c7e R1, 2026-09-19):
        # the suppression rule makes the deny-band rescuer judge
        # load-bearing for EVERY delegated completion; quick-model tail
        # latency (documented 2.6–22s live, 25s cap) makes the class
        # recurring. Retry mirrors the unparsable-retry pattern: same
        # per-attempt timeout window, ``attempt=2`` on the result.
        # HTTP/API errors keep NO retry (unchanged).
        logger.info(
            "event=fused_judge_first_attempt_timeout "
            "timeout_s=%s latency_ms=%s will_retry=true",
            timeout_s,
            first.latency_ms,
        )
        retry_start = time.monotonic()
        second = await _attempt_once()
        logger.info(
            "event=fused_judge_timeout_retry "
            "timeout_s=%s attempt=%s",
            timeout_s,
            2,
        )
        if second.kind == "ok":
            second_raw_text = second.raw_text
            if len(second_raw_text) > FUSED_JUDGE_MAX_OUTPUT_CHARS:
                second_raw_text = second_raw_text[:FUSED_JUDGE_MAX_OUTPUT_CHARS]
            parsed_second = _parse_fused_judge_response(second_raw_text)
            if parsed_second is not None:
                is_complete, evidence, advisory, rationale = parsed_second
                return FusedJudgeResult(
                    invoked=True,
                    is_complete=is_complete,
                    verdict="complete" if is_complete else "not_complete",
                    evidence_cited=evidence,
                    advisory_note_text=advisory if not is_complete else "",
                    rationale=rationale,
                    model=second.model,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    error_class=None,
                    attempt=2,
                    first_unparsable_excerpt=None,
                )
            # Attempt 2 returned ok but the verdict JSON did not parse.
            # Treat as conservative deny with ``unparsable`` verdict —
            # the model RESPONDED on retry, the body just did not parse.
            # ``first_unparsable_excerpt`` stays ``None``: the field is
            # reserved for unparsable-on-attempt-1 forensics, and a
            # timeout leaves no body to redact/excerpt.
            return FusedJudgeResult(
                invoked=True,
                is_complete=False,
                verdict="unparsable",
                rationale=(
                    "fused_judge_response_unparsable after timeout retry: "
                    "attempt 1 timed out, attempt 2 body did not parse"
                ),
                model=second.model,
                latency_ms=int((time.monotonic() - start) * 1000),
                error_class=None,
                attempt=2,
                first_unparsable_excerpt=None,
            )
        if second.kind == "timeout":
            # Post-retry timeout: BOTH attempts timed out. Conservative
            # fail-safe deny (DP-5 posture unchanged) — the attempt-2
            # row carries the ``verdict=timeout attempt=2`` surface.
            # ``retry_latency_ms`` measures the retry-attempt-only wall
            # clock (from ``retry_start``); the cumulative wall clock
            # across BOTH attempts is on ``FusedJudgeResult.latency_ms``.
            logger.info(
                "event=fused_judge_timeout_post_retry "
                "timeout_s=%s retry_latency_ms=%s",
                timeout_s,
                int((time.monotonic() - retry_start) * 1000),
            )
            return FusedJudgeResult(
                invoked=True,
                is_complete=False,
                verdict="timeout",
                model=second.model,
                latency_ms=int((time.monotonic() - start) * 1000),
                error_class="TimeoutError",
                attempt=2,
                first_unparsable_excerpt=None,
            )
        # second.kind == "error" (HTTP/API fault on retry): also
        # conservative fail-safe deny; attempt=2, error_class=set.
        # No retry beyond attempt 2 (same budget sentinel as the
        # unparsable retry).
        return FusedJudgeResult(
            invoked=True,
            is_complete=False,
            verdict="error",
            rationale=(
                "fused_judge_attempt_timeout (attempt 1); "
                "error on attempt 2"
            ),
            model=second.model,
            latency_ms=int((time.monotonic() - start) * 1000),
            error_class=second.error_class,
            attempt=2,
            first_unparsable_excerpt=None,
        )
    if first.kind == "error":
        # Conservative fail-safe — NO retry on non-timeout error.
        return FusedJudgeResult(
            invoked=True,
            is_complete=False,
            verdict="error",
            model=first.model,
            latency_ms=int((time.monotonic() - start) * 1000),
            error_class=first.error_class,
        )

    first_raw_text = first.raw_text
    if len(first_raw_text) > FUSED_JUDGE_MAX_OUTPUT_CHARS:
        first_raw_text = first_raw_text[:FUSED_JUDGE_MAX_OUTPUT_CHARS]
    parsed = _parse_fused_judge_response(first_raw_text)
    if parsed is not None:
        is_complete, evidence, advisory, rationale = parsed
        return FusedJudgeResult(
            invoked=True,
            is_complete=is_complete,
            verdict="complete" if is_complete else "not_complete",
            evidence_cited=evidence,
            advisory_note_text=advisory if not is_complete else "",
            rationale=rationale,
            model=first.model,
            latency_ms=int((time.monotonic() - start) * 1000),
            error_class=None,
        )

    # Unparsable on attempt 1 → RETRY (attempt 2). Same input, fresh
    # call; the first-attempt excerpt is preserved for forensics.
    first_unparsable_excerpt = _shape_unparsable_excerpt(first.raw_text)
    second = await _attempt_once()
    if second.kind in {"timeout", "error"}:
        return FusedJudgeResult(
            invoked=True,
            is_complete=False,
            verdict=second.kind,
            rationale=(
                "fused_judge_response_unparsable (attempt 1); "
                f"{second.kind} on attempt 2"
            ),
            model=second.model,
            latency_ms=int((time.monotonic() - start) * 1000),
            error_class=second.error_class,
            attempt=2,
            first_unparsable_excerpt=first_unparsable_excerpt,
        )

    second_raw_text = second.raw_text
    if len(second_raw_text) > FUSED_JUDGE_MAX_OUTPUT_CHARS:
        second_raw_text = second_raw_text[:FUSED_JUDGE_MAX_OUTPUT_CHARS]
    parsed_second = _parse_fused_judge_response(second_raw_text)
    if parsed_second is None:
        return FusedJudgeResult(
            invoked=True,
            is_complete=False,
            verdict="unparsable",
            rationale=(
                "fused_judge_response_unparsable on both attempts: "
                f"{first_unparsable_excerpt[:120]}"
            ),
            model=second.model,
            latency_ms=int((time.monotonic() - start) * 1000),
            error_class=None,
            attempt=2,
            first_unparsable_excerpt=first_unparsable_excerpt,
        )

    is_complete, evidence, advisory, rationale = parsed_second
    return FusedJudgeResult(
        invoked=True,
        is_complete=is_complete,
        verdict="complete" if is_complete else "not_complete",
        evidence_cited=evidence,
        advisory_note_text=advisory if not is_complete else "",
        rationale=rationale,
        model=second.model,
        latency_ms=int((time.monotonic() - start) * 1000),
        error_class=None,
        attempt=2,
        first_unparsable_excerpt=first_unparsable_excerpt,
    )

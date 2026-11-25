"""POK write gate — the semantic JUDGE (Wave 2 Phase 7/8).

The gate is a **JUDGE / POK-LIBRARIAN MAINTENANCE AGENT, not a filter**
(design-amendments-v2 §1). It emits a typed verdict + family +
explanation + alternative and it **NEVER WRITES** to the knowledge
store — the TOOL executes (design-amendments-v2 §8, authority split,
BINDING).

Plan of record: ``.agents/shared/planning/pok-write-gate/``
(``phase7-plan.md`` T1–T15, ``phase8-plan.md`` T1–T18,
``plan-overview.md`` task map; binding picks in
``design-amendments-v2.md`` §13 and ``architecture-recommendation.md``).

What lives here
---------------

* the CLOSED vocabularies (verdict / family / merge-kind / merge-policy);
* :class:`GateVerdict` — the typed outcome (T7.1) with every field
  §13.5 demands from day one (no-pick-foreclosed rule);
* :class:`POKGateUnavailable` — the D2 fail-closed refusal
  (``pok_gate_unavailable`` with a ``reason`` discriminator:
  ``timeout`` | ``unparsable`` | ``llm_error``) — garbage is NEVER
  re-labelled ``pok_gate_reject``;
* the purpose-bound ALIAS seam (T7.13 — ``POK_GATE_MODEL``, default
  the literal ``"quick"``; NEVER ``config.llm.model_quick``, which
  ``set_title_model_fallback`` silently rewrites to the GLOBAL model);
* the MODE seam (T7.11 — ``stub`` is rule-based and PROD-FORCED to
  ``llm``; never a silent pass-through);
* the FAMILY → template map (T7.4 — five families, each naming its
  alternative home + the Emperor's canonical rejection trace);
* the bounded ACTIVE-row DIGEST builder (T7.9 — the ≤8000-char
  ceiling binds; K=48 is the OBSERVED sizing within it, not a pinned
  constant);
* the judge PROMPT (T7.12 — the R1-beats-R4 clause + the R-PROV
  protective clause, VERBATIM; T7.15 — the R-RET retrievability
  question + its advisory-only lexical leg);
* the verdict PARSER (T7.3 — closed set; garbage → unavailable);
* the bounded verdict CACHE (T7.8 / T8.13 — ASK replays AS-ASK with a
  FRESH ``gate_id``; the ANSWERED outcome is NEVER cached as ACCEPT);
* the judge CLIENT (T7.2 — per-attempt HTTP timeout + wall cap, the
  factory re-reads the failover URL);
* the PENDING-ASK store (T8.6 — in-process, TTL 1200s, owner-scoped,
  NO live-store writes) and the six typed refusals (T8.7).

The module is import-neutral: it reads the POK models/repositories
(one direction only — the repository NEVER imports this module), so
there is no circular import (T8.10).

Layer discipline: the gate reads the repository (the digest needs the
ACTIVE rows) and NEVER writes a knowledge row. The LEDGER rows are
written by the TOOL layer (the authority split: the gate produces the
verdict, the tool executes and the audit records BOTH).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping, Sequence

from daemon.repositories.pok.repository import redact_credential_shapes

from .pok_gate_timeout_resolver import (
    GATE_MAX_RETRIES,
    GATE_WALL_CLOCK_CAP_S,
    get_gate_max_retries,
    get_gate_timeout_s,
    get_gate_wall_clock_cap_s,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ActiveRow",
    "Tightening",
    "apply_tightening",
    "AskOutcomeClass",
    "DEFAULT_MERGE_POLICY",
    "FAMILY_TEMPLATES",
    "GATE_FAMILIES",
    "GATE_VERDICTS",
    "GateVerdict",
    "MERGE_KINDS",
    "MERGE_POLICIES",
    "PENDING_ASK_REFUSALS",
    "POKGateUnavailable",
    "PendingGate",
    "PendingGateStore",
    "VerdictCache",
    "build_active_digest",
    "build_judge_prompt",
    "JudgePrompt",
    "cache_key",
    "judge_pok_write",
    "normalize_reason",
    "parse_verdict_payload",
    "resolve_gate_model",
    "resolve_gate_mode",
]


# ═══════════════════════════════════════════════════════════════════════════
# Closed vocabularies (the token sets are CLOSED — the R-RET/R-PROV rider
# added two RULES, never a verdict or a family)
# ═══════════════════════════════════════════════════════════════════════════

#: The gate's four outcomes (design-amendments-v2 §8). CLOSED — the
#: rider (gate-rules-r6-r7-amendment §2.4) left the token set untouched.
GATE_VERDICTS: tuple[str, ...] = ("ACCEPT", "MERGE", "ASK", "REJECT")

#: The five families (technical-analysis §Judge Prompt, "Five criteria,
#: each mapped to a ``family`` token"). CLOSED.
GATE_FAMILIES: tuple[str, ...] = (
    "boundary",
    "reason",
    "durability",
    "redundancy",
    "scope",
)

#: Merge-proposal kinds (design-amendments-v2 §13.2 fork F). A
#: ``no_delta`` merge is NEVER a write — it stamps the target only.
MERGE_KINDS: tuple[str, ...] = ("no_delta", "delta")

#: The merge-policy enum (design-amendments-v2 §13.2 fork F). The
#: DEFAULT is ``ASK_WITH_PROPOSAL`` (= F2, caller-confirmed merge).
MERGE_POLICIES: tuple[str, ...] = (
    "ASK_WITH_PROPOSAL",
    "AUTO",
    "ASK_NO_PROPOSAL",
)
DEFAULT_MERGE_POLICY: str = "ASK_WITH_PROPOSAL"

#: The RULE set the judge applies — R0–R5 plus the folded R-RET /
#: R-PROV rider rules. The rule set grew by two; the TOKEN closed set
#: did not (gate-rules-r6-r7-amendment §2.4).
GATE_RULES: tuple[str, ...] = (
    "R0",
    "R1",
    "R2",
    "R3",
    "R4",
    "R5",
    "R-RET",
    "R-PROV",
)

#: The confidence floor at or below which a NEGATIVE verdict becomes an
#: ASK (design-amendments-v2 §1 "Confidence / ASK"). 2/3 — expressed as
#: a float because the judge reports 0..1.
ASK_CONFIDENCE_THRESHOLD: float = 2.0 / 3.0

#: The six typed refusals of the G1 answer path (design-amendments-v2
#: §13.2 fork G / phase8-plan.md T8.7).
PENDING_ASK_REFUSALS: tuple[str, ...] = (
    "pok_gate_unknown",
    "pok_gate_expired",
    "pok_gate_already_answered",
    "pok_gate_not_owner",
    "pok_gate_answer_required",
    "pok_gate_answer_unrecognized",
)


# ═══════════════════════════════════════════════════════════════════════════
# Bounds (single source of truth)
# ═══════════════════════════════════════════════════════════════════════════

#: The reason excerpt cap (technical-analysis "Audit Trail":
#: ``REASON_EXCERPT = 200``). The judge's INPUT reason is capped at 500
#: (the digest carries the caller's claim verbatim, bounded).
REASON_EXCERPT_MAX_CHARS: int = 200
REASON_MAX_CHARS: int = 500

#: The judge's merged-body proposal cap — the store's own body budget
#: (the proposal is a body: it must fit the fence).
POK_BODY_MAX_CHARS: int = 240

#: The judge explanation / alternative caps (bounded excerpts).
EXPLANATION_MAX_CHARS: int = 400
ALTERNATIVE_MAX_CHARS: int = 300
QUESTION_MAX_CHARS: int = 600

#: The DIGEST ceiling — the BINDING constraint (plan-overview T7.9;
#: the ≤8000-char bound governs, K is the observed sizing WITHIN it).
POK_GATE_DIGEST_MAX_CHARS: int = 8000

#: The observed digest sizing WITHIN the ceiling (≈7250 chars observed).
#: NOT a pinned constant — the ceiling above is the binding bound.
POK_GATE_DIGEST_K_OBSERVED: int = 48

#: The one-line body slice in a digest row.
POK_GATE_DIGEST_BODY_SLICE: int = 80

#: The bounded-process-local LRU cap (technical-analysis "Cache +
#: Idempotence" — ``POK_GATE_CACHE_MAX = 512``).
POK_GATE_CACHE_MAX: int = 512

#: The ASK pending-state TTL — default 1200s, env
#: ``ENSEMBLE_POK_GATE_ASK_TTL_S``, min 120 / max 3600 (design-
#: amendments-v2 §13.2 fork G). The derivation: ``request_timeout=610s``
#: ⇒ a TTL below ~700s expires legitimate answers and turns the gate
#: into a write-blocker.
DEFAULT_ASK_TTL_S: float = 1200.0
MIN_ASK_TTL_S: float = 120.0
MAX_ASK_TTL_S: float = 3600.0
POK_GATE_ASK_TTL_S_ENV = "ENSEMBLE_POK_GATE_ASK_TTL_S"


# ═══════════════════════════════════════════════════════════════════════════
# The typed outcome (T7.1 + design-amendments-v2 §13.5)
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class GateVerdict:
    """The gate's typed outcome — verdict + family + explanation + …

    Every field §13.5 demands lands HERE, from day one, so no later
    migration is needed to represent a proposal::

        {target_slug, merge_kind (no_delta|delta), proposed_body,
         proposed_body_sha256, target_body_sha256, executed_body_sha256,
         executed_by}

    The outcome is IMMUTABLE (frozen) — a cached verdict is replayed
    byte-identically (T7.8) and the ledger records what the judge
    PROPOSED next to what the tool EXECUTED.

    ``advisory`` marks a SHADOW verdict: it does NOT gate the write.
    ``retrievability_flagged`` marks the R-RET advisory lexical leg
    (a SEPARATE signal — an advisory R-RET finding is not the same
    thing as a shadow verdict).
    """

    verdict: str
    family: str
    explanation: str = ""
    alternative: str | None = None
    confidence: float | None = None
    #: The chain key for the ledger's ASK chain (design-amendments-v2
    #: §13.2 fork G). Minted FRESH on every judge call — a cached ASK
    #: replay re-mints (T8.13).
    gate_id: str = ""
    target_slug: str | None = None
    merge_kind: str | None = None
    proposed_body: str | None = None
    proposed_body_sha256: str | None = None
    target_body_sha256: str | None = None
    executed_body_sha256: str | None = None
    executed_by: str | None = None
    question: str | None = None
    latency_ms: int = 0
    cache_hit: bool = False
    #: The RESOLVED ALIAS stamped on every verdict row (``"quick"`` by
    #: default; ``"stub"`` in stub mode) — the alias, never an internal
    #: pool id (T7.13).
    model_alias: str = "quick"
    #: The R-RET rename proposal (optional field, T7.15).
    proposed_slug: str | None = None
    #: The R-RET advisory lexical leg fired (slug ↔ body vocabulary
    #: overlap is ADVISORY-only — never a mechanical pre-filter).
    retrievability_flagged: bool = False
    #: SHADOW verdict — advisory only; it does NOT gate the write.
    advisory: bool = False
    #: The rule the judge applied (``"R-PROV"`` when the protective
    #: clause fired, ``"R-RET"`` when the lexical leg fired) — the
    #: shadow-evidence trace (gate-rules-r6-r7-amendment §3.1).
    rule_applied: str | None = None
    #: The D2 discriminator — ``timeout`` | ``unparsable`` | ``llm_error``
    #: — set only on an unavailable outcome.
    unavailable_reason: str | None = None


class POKGateUnavailable(Exception):
    """The D2 fail-closed refusal (``pok_gate_unavailable``).

    The gate NEVER answers "reject" when it could not judge. A
    timeout, an unparsable payload and a provider error all surface as
    this typed refusal with a ``reason`` discriminator — and the
    message contract is FIXED ("write NOT committed; not a judgement of
    your content; retry shortly"), because the caller must understand
    that NOTHING was decided about the content.

    D2 is BINDING (design-amendments-v2 §11 divergence E): garbage is
    NEVER re-labelled ``pok_gate_reject``.
    """

    code = "pok_gate_unavailable"

    def __init__(self, reason: str, message: str | None = None) -> None:
        self.reason = reason
        self.message = message or (
            "the POK write gate is unavailable (reason=%s) — the write "
            "was NOT committed; this is not a judgement of your content; "
            "retry shortly" % reason
        )
        super().__init__(self.message)


# ═══════════════════════════════════════════════════════════════════════════
# The alias seam (T7.13) — purpose-bound, NEVER the global model
# ═══════════════════════════════════════════════════════════════════════════

POK_GATE_MODEL_ENV = "POK_GATE_MODEL"

#: The purpose-id default. LITERAL ``"quick"`` — the PROXY resolves the
#: alias. The gate NEVER reads ``config.llm.model_quick``: that field
#: is silently rewritten to the GLOBAL model by ``set_title_model_fallback``
#: (``daemon/config.py:573-584``), which would route every gate call
#: to the daemon's default model — the exact hole the env seam closes.
POK_GATE_DEFAULT_MODEL: str = "quick"


def resolve_gate_model(env: Mapping[str, str] | None = None) -> str:
    """Resolve the judge's model ALIAS (T7.13).

    Returns the purpose-bound alias from ``POK_GATE_MODEL`` (default
    the literal ``"quick"``). NEVER ``config.llm.model_quick``, NEVER
    ``config.llm.model``, NEVER an internal pool id — the proxy
    resolves the alias (the ``image_gen.py`` purpose-bound pattern).
    """
    src = env if env is not None else os.environ
    raw = (src.get(POK_GATE_MODEL_ENV) or "").strip()
    return raw or POK_GATE_DEFAULT_MODEL


# ═══════════════════════════════════════════════════════════════════════════
# The mode seam (T7.11) — the stub is rule-based and PROD-FORCED to llm
# ═══════════════════════════════════════════════════════════════════════════

POK_GATE_MODE_ENV = "POK_GATE_MODE"
GATE_MODES: tuple[str, ...] = ("llm", "stub")

#: One-shot WARN flag — the prod-stub force fires ONCE per process.
_PROD_STUB_WARN_EMITTED: bool = False


def resolve_gate_mode(
    env: Mapping[str, str] | None = None,
    *,
    is_production: bool = True,
) -> str:
    """Resolve the judge's mode (T7.11).

    ``stub`` is reachable ONLY in a non-production environment. In
    production a ``stub`` mode is a loud one-shot WARN and the mode is
    FORCED to ``llm`` — a stub judge in production would be a silent
    pass-through, which the plan forbids outright.

    The stub stamps ``model_alias="stub"`` on every verdict it
    produces, and its cache entries are SEGREGATED from the llm ones
    (the mode is part of the cache key).
    """
    global _PROD_STUB_WARN_EMITTED
    src = env if env is not None else os.environ
    raw = (src.get(POK_GATE_MODE_ENV) or "").strip().lower()
    mode = raw if raw in GATE_MODES else "llm"
    if mode == "stub" and is_production:
        if not _PROD_STUB_WARN_EMITTED:
            _PROD_STUB_WARN_EMITTED = True
            logger.warning(
                "[pok_gate] %s=stub in a PRODUCTION environment — "
                "forcing the judge to 'llm'. The stub is a rule-based "
                "TEST seam and MUST NOT judge production writes.",
                POK_GATE_MODE_ENV,
            )
        return "llm"
    return mode


def reset_gate_mode_warn_for_tests() -> None:
    """Clear the one-shot prod-stub WARN flag (test-only)."""
    global _PROD_STUB_WARN_EMITTED
    _PROD_STUB_WARN_EMITTED = False


# ═══════════════════════════════════════════════════════════════════════════
# The family → template map (T7.4) — five families, each names a home
# ═══════════════════════════════════════════════════════════════════════════

#: The Emperor's canonical rejection trace (design-amendments-v2 §1,
#: verbatim): the knowledge belongs in RAG, not in POK.
EMPEROR_TRACE_PHRASE: str = (
    "the information not belong to POK, please use rag tool if it exist for you"
)

#: The five families, each naming its ALTERNATIVE HOME (R5: "every
#: rejection must name a valid alternative home; a rejection with no
#: home is itself a defect").
FAMILY_TEMPLATES: dict[str, dict[str, str]] = {
    "boundary": {
        "label": "boundary (operational how-to vs general knowledge)",
        "home": "rag",
        "home_detail": (
            "the knowledge store — capture it with experience() (the RAG "
            "tool), not in POK"
        ),
        "template": (
            "this is general knowledge rather than this project's "
            "operating how-to — {home_detail}"
        ),
    },
    "reason": {
        "label": "reason (the operational claim is not coherent)",
        "home": "sharpen_reason",
        "home_detail": (
            "sharpen the reason: state the operational invariant this "
            "fact protects, not 'because' or boilerplate"
        ),
        "template": (
            "the stated operational claim is not coherent or specific "
            "enough to keep — {home_detail}"
        ),
    },
    "durability": {
        "label": "durability (a snapshot/episode, not a durable rule)",
        "home": "release_tree",
        "home_detail": (
            "the release tree / KV / board — a snapshot or an episode "
            "does not belong in a durable operating rule"
        ),
        "template": (
            "this records a snapshot or an episode rather than a durable "
            "rule — {home_detail}"
        ),
    },
    "redundancy": {
        "label": "redundancy (an ACTIVE POK row already carries it)",
        "home": "supersede",
        "home_detail": (
            "the named ACTIVE row — supersede it instead of adding a "
            "second row"
        ),
        "template": (
            "an ACTIVE POK row already carries this fact — {home_detail}"
        ),
    },
    "scope": {
        "label": "scope (it does not belong to THIS project)",
        "home": "other_project",
        "home_detail": (
            "the project it actually belongs to — do not write it to "
            "this project"
        ),
        "template": (
            "this does not belong to THIS project — {home_detail}"
        ),
    },
}


def family_home(family: str) -> str:
    """The alternative HOME a family names (R5)."""
    template = FAMILY_TEMPLATES.get(family)
    return template["home"] if template else "rag"


def family_template(family: str, explanation: str = "") -> str:
    """The canonical rejection template for one family.

    Every template NAMES its alternative home (the CONTRACT test at
    T7.4 / phase11 T2 asserts this) and carries the Emperor's trace
    phrase so the rejection reads as a ROUTING decision, not a
    judgement of the caller.
    """
    template = FAMILY_TEMPLATES.get(family) or FAMILY_TEMPLATES["boundary"]
    text = template["template"].format(home_detail=template["home_detail"])
    parts = [text]
    if explanation:
        parts.append(explanation)
    parts.append(EMPEROR_TRACE_PHRASE)
    return " — ".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# The ACTIVE-row digest (T7.9)
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ActiveRow:
    """One ACTIVE POK row as the digest sees it.

    The judge never sees a live store — it sees this bounded
    projection. ``family`` / ``pinned`` / ``source_tier`` /
    ``verify_count`` / ``last_verified`` are the tie-break inputs for
    the selection order and for the librarian's later passes.
    """

    slug: str
    body: str
    family: str | None = None
    pinned: bool = False
    source_tier: str = "agent"
    verify_count: int = 0
    last_verified: str | None = None


#: The tokens that carry no distinctive signal for the overlap
#: pre-filter (a small, deliberate stop-word set — the guideline's
#: TEST 3 "distinctive tokens" idea).
_DIGEST_STOP_TOKENS: frozenset[str] = frozenset(
    {
        "about",
        "after",
        "again",
        "against",
        "because",
        "before",
        "between",
        "both",
        "does",
        "each",
        "from",
        "have",
        "into",
        "more",
        "most",
        "must",
        "never",
        "only",
        "other",
        "over",
        "same",
        "should",
        "some",
        "such",
        "than",
        "that",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "under",
        "very",
        "when",
        "where",
        "which",
        "while",
        "with",
        "without",
        "would",
        "your",
    }
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _distinctive_tokens(text: str) -> set[str]:
    """The distinctive tokens of a text (lowercase, length ≥ 4, no stop words)."""
    return {
        token
        for token in _TOKEN_RE.findall((text or "").lower())
        if len(token) >= 4 and token not in _DIGEST_STOP_TOKENS
    }


def _row_family(slug: str) -> str | None:
    """The family of a slug (the store's own rule, mirrored locally)."""
    from daemon.repositories.pok.models import pok_family_key

    try:
        return pok_family_key(slug)
    except Exception:  # noqa: BLE001 — defensive (a malformed slug has no family)
        return None


def _render_row(row: ActiveRow) -> str:
    """One digest line: ``slug: first-80-chars-of-one-line-body``."""
    body = " ".join((row.body or "").split())
    return f"- {row.slug}: {body[:POK_GATE_DIGEST_BODY_SLICE]}"


def build_active_digest(
    *,
    candidate_slug: str,
    candidate_body: str,
    rows: Sequence[ActiveRow],
    reason: str = "",
    project_name: str | None = None,
    candidate_family: str | None = None,
    max_chars: int = POK_GATE_DIGEST_MAX_CHARS,
    k: int = POK_GATE_DIGEST_K_OBSERVED,
) -> str:
    """Assemble the bounded ACTIVE-row digest (T7.9).

    The CEILING is the binding constraint (≤8000 chars TOTAL); ``k`` is
    the OBSERVED sizing WITHIN it (K=48 ≈ 7250 observed) — NOT a pinned
    constant. The assembly therefore truncates the ROW section to fit
    the ceiling, and the boundary-not-exclusivity clause (which must
    NEVER be dropped to save chars) is part of the FIXED header.

    Selection: rows are ranked by distinctive-token overlap with the
    candidate; ties break same-family-first, then most-recently-
    verified. The ZERO-OVERLAP fallback is the same order (same-family
    first, then most-recently-verified) — NOT slug order.
    """
    candidate_tokens = _distinctive_tokens(f"{candidate_slug} {candidate_body}")
    family = candidate_family if candidate_family is not None else _row_family(candidate_slug)

    def _rank(row: ActiveRow) -> tuple[int, int, str]:
        overlap = len(candidate_tokens & _distinctive_tokens(f"{row.slug} {row.body}"))
        same_family = 1 if (family and row.family == family) else 0
        return (overlap, same_family, row.last_verified or "")

    ordered = sorted(rows, key=_rank, reverse=True)
    selected = ordered[: max(0, k)]

    header_lines = [
        "PROJECT OPERATING KNOWLEDGE (POK) ACTIVE-ROW DIGEST "
        "(project-scoped, bounded)",
        (
            "Boundary (R4): redundancy means INTRA-STORE duplication only "
            "— an ACTIVE row in this Project Operating Knowledge (POK) "
            "store already carrying the fact. A twin in the general "
            "knowledge base is explicitly NOT a rejection basis; judge the BOUNDARY "
            "(operational how-to vs general knowledge), never store "
            "exclusivity."
        ),
    ]
    if project_name:
        header_lines.insert(1, f"PROJECT: {project_name}")
    bounded_reason = (reason or "")[:REASON_MAX_CHARS]
    if bounded_reason:
        header_lines.append(f"CALLER'S OPERATIONAL CLAIM: {bounded_reason}")
    header_lines.append("ACTIVE ROWS (slug: body):")
    header = "\n".join(header_lines) + "\n"

    budget = max(0, max_chars - len(header))
    body_lines: list[str] = []
    used = 0
    for row in selected:
        line = _render_row(row)
        if used + len(line) + 1 > budget:
            break
        body_lines.append(line)
        used += len(line) + 1
    digest = header + "\n".join(body_lines)
    if len(digest) > max_chars:
        digest = digest[:max_chars]
    return digest


# ═══════════════════════════════════════════════════════════════════════════
# The judge prompt (T7.12 + T7.15)
# ═══════════════════════════════════════════════════════════════════════════

#: The FIXED PERSONA (technical-analysis "Judge Prompt Design").
GATE_PERSONA: str = (
    "You are the write gate for the Project Operating Knowledge (POK) "
    "store of {project_name}. Project Operating Knowledge (POK) is the "
    "store of durable operational how-to knowledge (DevOps-kind rules, "
    "traps and conventions) for THIS project only, distinct from the "
    "general knowledge base (RAG, retrieval-augmented generation — "
    "free-form reference material)."
)

#: The judge question (design-amendments-v2 §1, verbatim): what breaks
#: in THIS repo if we drop this?
JUDGE_QUESTION: str = "What breaks in THIS repo if we drop this?"

#: R1 — the operational test (VERBATIM; NEVER "is this general?").
R1_QUESTION: str = "Does the body still teach the same lesson in a repo with no ensemble?"

#: R2 — the rolling test (VERBATIM).
R2_QUESTION: str = "Does the rule survive the date/version it cites?"

#: R3 — the no-handle rule: ADVISORY ONLY, never a rejection basis.
R3_CLAUSE: str = (
    "R3 (no-handle): a missing or unhandled edge case is an ADVISORY "
    "FLAG ONLY — it is NEVER a rejection basis."
)

#: R4 + the R1-beats-R4 precedence (VERBATIM, design-amendments-v2 §1).
R1_BEATS_R4_CLAUSE: str = (
    "R4 (already-in-RAG): NEVER fires on operational rows. R1 beats R4. "
    "A RAG twin is NOT a rejection basis."
)

#: R-PROV — the admit-side PROTECTIVE clause (VERBATIM,
#: gate-rules-r6-r7-amendment §3.1).
R_PROV_CLAUSE: str = (
    "R-PROV (provenance-vs-precedence): Is the date/SHA in this body "
    "EVIDENCE FOR an operational claim, or IS the claim itself merely "
    "'as of date X' / 'at commit SHA Y'? A date or SHA admissible as "
    "evidence does not make the row rolling — the row survives if the "
    "lesson survives the date. This protective clause beats a rolling "
    "rejection the way R1 beats R4."
)

#: R-RET — the retrievability question (VERBATIM,
#: gate-rules-r6-r7-amendment §3.2) + its ADVISORY caveat.
R_RET_QUESTION: str = (
    "Would someone searching with the body's own operative terms find "
    "this row by its slug — does the slug share vocabulary with the "
    "terms the body actually teaches, or is it mis-slugged so it can "
    "never surface?"
)

R_RET_ADVISORY_CLAUSE: str = (
    "R-RET (retrievability): lexical slug-to-body token overlap is an "
    "ADVISORY SIGNAL ONLY; the semantic judgment is primary. No "
    "mechanical pre-filter may act on overlap alone. A NEW write whose "
    "slug never surfaces is ASKED for a rename — it is NEVER auto-"
    "rejected."
)

#: R5 — the no-home escape.
R5_CLAUSE: str = (
    "R5 (no-home escape): every rejection MUST name a valid alternative "
    "home. A rejection with no home is itself a defect."
)

#: R0 — the mechanical content fence (the rule the gate does NOT
#: re-derive — it is enforced upstream at write time).
R0_CLAUSE: str = (
    "R0 (format): the body already passed the content fence (one "
    "paragraph, <=240 escaped chars, no leading '#', no "
    "'[SYSTEM CONTEXT:', no triple-backtick)."
)

#: The JSON output contract (technical-analysis §Judge Prompt; the
#: verdict set is the 4-value set of design-amendments-v2 §8).
JSON_OUTPUT_CONTRACT: str = (
    '{"verdict": "ACCEPT|MERGE|ASK|REJECT", "family": '
    '"boundary|reason|durability|redundancy|scope", "explanation": str, '
    '"alternative": str|null, "confidence": 0.0-1.0, "target_slug": '
    'str|null, "merged_body_proposal": str|null, "proposed_slug": '
    'str|null, "question": str|null}'
)


@dataclass(frozen=True)
class JudgePrompt:
    """Frozen carrier for the split judge prompt (SYSTEM + USER)."""

    system: str
    user: str


def build_judge_prompt(
    *,
    project_name: str | None,
    slug: str,
    body: str,
    reason: str,
    digest: str,
) -> JudgePrompt:
    """Compose the judge's prompt (T7.12 + T7.15), split into a SYSTEM
    message (role + instructions) and a USER message (per-write payload).

    The judge makes NO tool calls — it sees the digest, not a live store.
    """
    project_label = project_name or (
        "the project this write targets (its name was not available to "
        "this call)"
    )
    persona = GATE_PERSONA.format(project_name=project_label)
    bounded_reason = (reason or "")[:REASON_MAX_CHARS]

    system_sections = [
        persona,
        "",
        "GLOSSARY (terms used below): slug = the short identifier "
        "(kebab-case name) of a knowledge entry; body = its one-paragraph "
        "content; row = one entry already in the store; digest = the "
        "bounded list of existing entries supplied with this write; "
        "verdict = the outcome you return (ACCEPT / MERGE / ASK / REJECT).",
        "",
        "RULES:",
        R0_CLAUSE,
        f"R1 (operational): {R1_QUESTION} NEVER ask 'is this general?'.",
        f"R2 (rolling): {R2_QUESTION}",
        R3_CLAUSE,
        R1_BEATS_R4_CLAUSE,
        R5_CLAUSE,
        R_PROV_CLAUSE,
        R_RET_QUESTION,
        R_RET_ADVISORY_CLAUSE,
        "",
        f"JUDGE QUESTION: {JUDGE_QUESTION}",
        "",
        "OUTCOMES:",
        (
            "ACCEPT means the fact belongs in Project Operating Knowledge "
            "(POK) as a new row: write it."
        ),
        (
            "MERGE means an ACTIVE row in this store already carries the "
            "fact: name it in target_slug and propose the merged body in "
            "merged_body_proposal (omit the proposal when the target "
            "already carries the fact fully)."
        ),
        (
            "ASK means you need the caller's confirmation: put the "
            "question you want answered in question."
        ),
        (
            "REJECT means the fact does not belong in Project Operating "
            "Knowledge (POK): name the alternative home in alternative "
            "(never leave it empty)."
        ),
        (
            "If your confidence is <= 0.67, answer ASK — never reject a "
            "fact you are not sure about."
        ),
        "",
        "REJECTION EXAMPLES (every rejection names its alternative home):",
        (
            "boundary (operational how-to vs general knowledge): this is "
            "general knowledge rather than this project's operating "
            "how-to — capture it with experience() (the general knowledge base), not "
            "in Project Operating Knowledge (POK). "
            + "the information does not belong to Project Operating Knowledge (POK); use the general knowledge base instead"
        ),
        (
            "reason (the operational claim is not coherent): the stated "
            "operational claim is not coherent or specific enough to keep "
            "— sharpen the reason: state the operational invariant this "
            "fact protects, not 'because' or boilerplate. "
            + "the information does not belong to Project Operating Knowledge (POK); use the general knowledge base instead"
        ),
        (
            "durability (a snapshot/episode, not a durable rule): this "
            "records a snapshot or an episode rather than a durable rule "
            "— the release tree / KV / board — a snapshot or an episode "
            "does not belong in a durable operating rule. "
            + "the information does not belong to Project Operating Knowledge (POK); use the general knowledge base instead"
        ),
        (
            "redundancy (an ACTIVE row already carries it): an ACTIVE "
            "row in this store already carries this fact — the named "
            "ACTIVE row — supersede it instead of adding a second row. "
            + "the information does not belong to Project Operating Knowledge (POK); use the general knowledge base instead"
        ),
        (
            "scope (it does not belong to THIS project): this does not "
            "belong to THIS project — the project it actually belongs to "
            "— do not write it to this project. "
            + "the information does not belong to Project Operating Knowledge (POK); use the general knowledge base instead"
        ),
        "",
        f"ANSWER WITH EXACTLY ONE JSON OBJECT AND NOTHING ELSE: {JSON_OUTPUT_CONTRACT}",
    ]
    system = "\n".join(system_sections)

    user_sections = [
        f"CANDIDATE SLUG: {slug}",
        f"CANDIDATE BODY: {body}",
        f"CALLER'S OPERATIONAL CLAIM (the primary judge input): {bounded_reason}",
        "",
        digest,
    ]
    user = "\n".join(user_sections)

    return JudgePrompt(system=system, user=user)


# ═══════════════════════════════════════════════════════════════════════════
# The verdict parser (T7.3) — closed set; garbage → unavailable
# ═══════════════════════════════════════════════════════════════════════════

#: One code fence is tolerated (the attestation judge's parse pattern).
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _extract_json_object(raw: str) -> dict[str, Any] | None:
    """Extract the JSON object from a raw model response.

    Tolerates ONE code fence and falls back to the first balanced
    ``{...}`` substring (the attestation parse pattern). Returns
    ``None`` when nothing parses.
    """
    if not raw or not raw.strip():
        return None
    text = raw.strip()
    fence = _FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            parsed = json.loads(text[start : end + 1])
        except (ValueError, TypeError):
            return None
    return parsed if isinstance(parsed, dict) else None


def _bounded(text: Any, limit: int) -> str:
    """A bounded string excerpt."""
    if text is None:
        return ""
    return str(text)[:limit]


def parse_verdict_payload(raw: str) -> dict[str, Any]:
    """Parse the judge's raw answer into the closed-set payload (T7.3).

    The VERDICT and FAMILY token sets are CLOSED: a verdict outside
    ``GATE_VERDICTS`` or a family outside ``GATE_FAMILIES`` is
    UNPARSABLE (never a silent default). Garbage raises
    :class:`POKGateUnavailable` with ``reason="unparsable"`` — the D2
    fail-closed path, NEVER re-labelled ``pok_gate_reject``.

    The parsed payload's free-text fields are bounded and
    CREDENTIAL-SCRUBBED (T8.18) before they can reach the ledger.
    """
    payload = _extract_json_object(raw)
    if payload is None:
        raise POKGateUnavailable("unparsable")
    verdict = str(payload.get("verdict") or "").strip().upper()
    family = str(payload.get("family") or "").strip().lower()
    if verdict not in GATE_VERDICTS:
        raise POKGateUnavailable("unparsable")
    if family not in GATE_FAMILIES:
        raise POKGateUnavailable("unparsable")
    confidence = payload.get("confidence")
    try:
        confidence_value = (
            float(confidence) if confidence is not None else None
        )
    except (TypeError, ValueError):
        confidence_value = None
    if confidence_value is not None:
        confidence_value = max(0.0, min(1.0, confidence_value))
    explanation, _ = redact_credential_shapes(
        _bounded(payload.get("explanation"), EXPLANATION_MAX_CHARS)
    )
    alternative, _ = redact_credential_shapes(
        _bounded(payload.get("alternative"), ALTERNATIVE_MAX_CHARS)
    )
    question, _ = redact_credential_shapes(
        _bounded(payload.get("question"), QUESTION_MAX_CHARS)
    )
    proposed_body, _ = redact_credential_shapes(
        _bounded(payload.get("merged_body_proposal"), POK_BODY_MAX_CHARS)
    )
    return {
        "verdict": verdict,
        "family": family,
        "explanation": explanation,
        "alternative": alternative or None,
        "confidence": confidence_value,
        "target_slug": _bounded(payload.get("target_slug"), 64) or None,
        "merged_body_proposal": proposed_body or None,
        "proposed_slug": _bounded(payload.get("proposed_slug"), 64) or None,
        "question": question or None,
    }


# ═══════════════════════════════════════════════════════════════════════════
# The verdict cache (T7.8 / T8.13)
# ═══════════════════════════════════════════════════════════════════════════


def normalize_reason(reason: str | None) -> str:
    """The reason normalization (technical-analysis "Cache + Idempotence").

    ``" ".join((r or "").lower().split())`` — lowercase + whitespace
    collapse, mirroring ``normalize_body``. Punctuation is NOT
    stripped and the text is NOT tokenized (a "not" could flip
    meaning).
    """
    return " ".join((reason or "").lower().split())


def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def cache_key(
    *,
    project_id: str,
    slug: str,
    body: str,
    reason: str | None,
    mode: str = "llm",
) -> tuple[str, str, str, str, str]:
    """The D5 cache key.

    ``(project_id, slug, sha256(stripped body), sha256(normalized
    reason), mode)``. The MODE is part of the key so stub and llm
    entries are SEGREGATED (T7.11 / T8.13). The body hash is over the
    STRIPPED string (not the verbatim, not the normalized body —
    normalizing would alias meaningful case differences).
    """
    return (
        project_id,
        slug,
        _sha256((body or "").strip()),
        _sha256(normalize_reason(reason)),
        mode,
    )


class VerdictCache:
    """The bounded process-local verdict LRU (T7.8).

    ``OrderedDict`` + ``move_to_end`` + cap-eviction (the
    ``blueprint_rate_limiter`` precedent). Process-local and NOT
    persisted: the durable record is the ledger, not this cache.

    A cached ASK replays AS-ASK — the caller re-asks with the SAME
    question but a FRESH ``gate_id`` + TTL (T8.13). An ANSWERED
    outcome is NEVER written here (the answer path does not touch the
    cache at all), so a caller self-attestation can never launder into
    a durable gate accept.
    """

    def __init__(self, max_entries: int = POK_GATE_CACHE_MAX) -> None:
        self._max_entries = max(1, int(max_entries))
        self._entries: "OrderedDict[tuple[str, str, str, str, str], GateVerdict]" = (
            OrderedDict()
        )

    def get(self, key: tuple[str, str, str, str, str]) -> GateVerdict | None:
        """The cached verdict for a key (``None`` on a miss)."""
        verdict = self._entries.get(key)
        if verdict is not None:
            self._entries.move_to_end(key)
        return verdict

    def put(
        self, key: tuple[str, str, str, str, str], verdict: GateVerdict
    ) -> None:
        """Store a verdict (bounded LRU eviction)."""
        self._entries[key] = verdict
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        """Drop every entry (test seam)."""
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


# ═══════════════════════════════════════════════════════════════════════════
# The judge client (T7.2) + the stub judge (T7.11)
# ═══════════════════════════════════════════════════════════════════════════


def _house_llm_call(
    llm_config: Mapping[str, Any],
    prompt: str,
    *,
    model: str,
    timeout_s: float,
    wall_clock_cap_s: float,
) -> str:
    """ONE judge call through the house failover facade (T7.2).

    Mirrors the ``memoir_persona_gate._house_llm_call`` precedent:
    ``invoke_raw_with_failover(factory, llm_config)`` — the factory
    RE-READS the failover URL on every attempt (capturing it in a
    closure would break the primary→backup swap), the per-attempt HTTP
    timeout rides the client, and the wall clock rides the facade.
    """
    import openai  # noqa: PLC0415 — lazy init

    from daemon.services.llm_failover import (
        current_failover_url,
        invoke_raw_with_failover,
    )

    config = dict(llm_config or {})
    api_key = str(config.get("api_key") or "")
    base_url = config.get("base_url")
    base_url_backup = config.get("base_url_backup")
    if hasattr(prompt, "system") and hasattr(prompt, "user"):
        _system_content = str(getattr(prompt, "system") or "")
        _user_content = str(getattr(prompt, "user") or "")
    else:
        _system_content = ""
        _user_content = str(prompt)
    if not base_url and not api_key:
        # NO CONNECTION CONFIG ⇒ the judge is UNAVAILABLE, and it says
        # so WITHOUT attempting a network call (a test double with no
        # LLM config must never reach out to a provider).
        raise POKGateUnavailable("llm_error")

    def _factory() -> str:
        url = current_failover_url() or base_url
        client = openai.OpenAI(
            api_key=api_key,
            base_url=url or None,
            timeout=float(timeout_s),
            max_retries=0,
        )
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": _system_content},
                     {"role": "user", "content": _user_content}],
            temperature=0.0,
        )
        choices = getattr(response, "choices", None) or []
        if not choices:
            raise ValueError("pok_gate_response_empty: no choices")
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if not content:
            raise ValueError("pok_gate_response_empty: empty content")
        return str(content)

    return str(
        invoke_raw_with_failover(
            _factory,
            {"base_url": base_url, "base_url_backup": base_url_backup, "api_key": api_key},
            wall_clock_cap_s=float(wall_clock_cap_s),
        )
    )


def _stub_verdict_payload(
    *,
    slug: str,
    body: str,
    reason: str,
    rows: Sequence[ActiveRow],
) -> dict[str, Any]:
    """The RULE-BASED stub judge (T7.11).

    NEVER a silent pass-through: the stub REJECTS the known-bad
    fixtures (an empty body, a multi-paragraph body, a forged header,
    an empty operational claim) and MERGEs into an ACTIVE row that
    already carries the same slug. It is a test/dev seam only — in
    production the mode is forced to ``llm``.
    """
    stripped = (body or "").strip()
    if not stripped:
        return {
            "verdict": "REJECT",
            "family": "boundary",
            "explanation": "the body is empty",
            "alternative": "state the fact, not a blank row",
            "confidence": 0.95,
        }
    if "\n" in stripped or stripped.startswith("#"):
        return {
            "verdict": "REJECT",
            "family": "boundary",
            "explanation": "the body is not a single fenced paragraph",
            "alternative": "one paragraph, no leading '#'",
            "confidence": 0.95,
        }
    if not (reason or "").strip():
        return {
            "verdict": "REJECT",
            "family": "reason",
            "explanation": "no operational claim was stated",
            "alternative": "sharpen the reason",
            "confidence": 0.9,
        }
    for row in rows:
        if row.slug == slug:
            return {
                "verdict": "MERGE",
                "family": "redundancy",
                "explanation": f"the active row {row.slug!r} already carries this slug",
                "alternative": None,
                "confidence": 0.9,
                "target_slug": row.slug,
                "merged_body_proposal": None,
            }
    return {
        "verdict": "ACCEPT",
        "family": "scope",
        "explanation": "the rule-based stub admits a well-formed operational fact",
        "alternative": None,
        "confidence": 0.6,
    }


# ═══════════════════════════════════════════════════════════════════════════
# The judge (T7.1–T7.15)
# ═══════════════════════════════════════════════════════════════════════════


def _retrievability_flag(
    *, slug: str, body: str, proposed_slug: str | None = None
) -> tuple[bool, str | None]:
    """The R-RET advisory lexical leg (T7.15).

    ADVISORY ONLY: the lexical slug↔body token overlap NEVER
    auto-rejects (no measured agreement rate backs mechanical use —
    the 2026-11-24 re-derivation measured 81–84% lexical-vs-label
    agreement on the contested bands, which leaves 16–19% error on
    exactly the rows that matter). The leg only answers "would a
    searcher using the body's own operative terms find this row by its
    slug?" and proposes a rename.

    Returns ``(flagged, proposed_slug)``.
    """
    slug_tokens = _distinctive_tokens(slug)
    body_tokens = _distinctive_tokens(body)
    if not slug_tokens or not body_tokens:
        return (False, None)
    if slug_tokens & body_tokens:
        return (False, None)
    # No vocabulary overlap — propose a slug built from the body's own
    # operative terms (the caller may keep the original; the proposal is
    # advisory).
    proposal = "-".join(sorted(body_tokens)[:4]) or None
    return (True, proposal or proposed_slug)


def _merge_kind_for(
    *, target_body: str | None, proposed_body: str | None
) -> str:
    """The merge kind: ``delta`` when a merged body is authored.

    A no-delta merge (the fact is fully carried by the target) is
    NEVER a write — it stamps the target only (F2).
    """
    if not proposed_body:
        return "no_delta"
    if target_body is not None and " ".join(proposed_body.split()) == " ".join(
        target_body.split()
    ):
        return "no_delta"
    return "delta"


#: The token-overlap share a digest row must cover before the
#: near-soft-max tightening treats it as the redundancy carrier
#: (T10.6). The tightening NEVER invents a merge target: without a
#: row that demonstrably carries the candidate's own operative terms,
#: the verdict stands.
TIGHTENING_OVERLAP_SHARE = 0.6


@dataclass(frozen=True)
class Tightening:
    """The near-soft-max admission tightening (T10.6).

    The librarian computes a per-project soft max; when the store has
    reached it, admission TIGHTENS — more ASK rounds (a new row is
    confirmed, not silently admitted) and MERGE is preferred over a
    NEW row (the store stops growing).

    The tightening rides the ``pok_gate.softmax_tightening`` switch
    family (BOTH switches default OFF at build — design-amendments-v2
    §13.3 item 5): the soft max is COMPUTED and drift-reported from
    day one (safe), but the tightening ACTION changes admission
    outcomes, so it waits for the operator's ceremony.
    """

    tight: bool = False
    reason: str = "the soft max is unset or the store is below it"
    soft_max: int | None = None
    active_count: int = 0

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | None) -> "Tightening":
        if not payload:
            return cls()
        return cls(
            tight=bool(payload.get("tight")),
            reason=str(payload.get("reason") or "the soft max is unset or the store is below it"),
            soft_max=payload.get("soft_max"),
            active_count=int(payload.get("active_count") or 0),
        )


def _redundancy_carrier(
    *,
    rows: Sequence[ActiveRow],
    candidate_slug: str,
    candidate_body: str,
) -> ActiveRow | None:
    """The digest row that demonstrably carries the candidate's terms.

    The tightening NEVER guesses: the row must cover at least
    :data:`TIGHTENING_OVERLAP_SHARE` of the candidate's distinctive
    tokens (and at least one token). A weak overlap returns ``None``
    and the verdict stands.
    """
    candidate_tokens = _distinctive_tokens(f"{candidate_slug} {candidate_body}")
    if not candidate_tokens:
        return None
    needed = max(1, int(math.ceil(len(candidate_tokens) * TIGHTENING_OVERLAP_SHARE)))
    best: ActiveRow | None = None
    best_overlap = 0
    for row in rows:
        overlap = len(candidate_tokens & _distinctive_tokens(f"{row.slug} {row.body}"))
        if overlap > best_overlap:
            best = row
            best_overlap = overlap
    if best is None or best_overlap < needed:
        return None
    return best


def apply_tightening(
    verdict: GateVerdict,
    *,
    rows: Sequence[ActiveRow],
    candidate_slug: str,
    candidate_body: str,
    tightening: Tightening | None,
) -> GateVerdict:
    """Apply the near-soft-max tightening to a verdict (T10.6).

    * an ACCEPT becomes an ASK — more ASK rounds: the caller confirms
      the new row instead of having it admitted silently;
    * a redundancy REJECT becomes a MERGE that names the row that
      demonstrably carries the fact — MERGE is preferred over a NEW
      row, so the store does not grow. The merge is a ``no_delta``
      merge (the fact is carried — the tool stamps the target and
      writes no row);
    * every other verdict stands, and a NOT-tightening pass returns
      the verdict byte-identically.
    """
    if tightening is None or not tightening.tight:
        return verdict
    token = verdict.verdict
    if token == "ACCEPT":
        return replace(
            verdict,
            verdict="ASK",
            question=(
                "the store has reached its soft max (%s) — confirm this "
                "new row, or merge it into an existing row that already "
                "carries the fact" % tightening.soft_max
            ),
        )
    if token == "REJECT" and verdict.family == "redundancy":
        carrier = _redundancy_carrier(
            rows=rows,
            candidate_slug=candidate_slug,
            candidate_body=candidate_body,
        )
        if carrier is None:
            return verdict
        return replace(
            verdict,
            verdict="MERGE",
            target_slug=carrier.slug,
            merge_kind="no_delta",
            proposed_body=None,
            proposed_body_sha256=None,
            target_body_sha256=_sha256(carrier.body),
            question=(
                "the store has reached its soft max (%s) — the fact is "
                "already carried by the ACTIVE row %r; fold this write "
                "into it instead of adding a new row"
                % (tightening.soft_max, carrier.slug)
            ),
        )
    return verdict


def judge_pok_write(
    *,
    project_id: str,
    project_name: str | None,
    slug: str,
    body: str,
    reason: str,
    rows: Sequence[ActiveRow],
    llm_config: Mapping[str, Any] | None = None,
    llm_call: Callable[[Any], str] | None = None,
    mode: str | None = None,
    merge_policy: str = DEFAULT_MERGE_POLICY,
    cache: VerdictCache | None = None,
    is_production: bool = True,
    shadow: bool = False,
    tightening: Mapping[str, Any] | None = None,
) -> GateVerdict:
    """JUDGE one candidate write (T7.1–T7.15).

    The function is PURE with respect to the knowledge store: it
    READS the digest rows it is handed and RETURNS a typed verdict. It
    never writes a POK row (the TOOL executes — the authority split).

    ``shadow=True`` marks the verdict ADVISORY: the caller still
    writes, but the verdict is logged (``phase=ASK_SHADOW`` for an ASK,
    ``advisory=true``) and NO pending state is minted (T8.8).

    Raises :class:`POKGateUnavailable` (D2 fail-closed) when the judge
    cannot be reached or its answer cannot be parsed.
    """
    started = time.monotonic()
    alias = resolve_gate_model()
    effective_mode = mode or resolve_gate_mode(is_production=is_production)
    if effective_mode not in GATE_MODES:
        effective_mode = "llm"
    model_alias = "stub" if effective_mode == "stub" else alias

    key = cache_key(
        project_id=project_id,
        slug=slug,
        body=body,
        reason=reason,
        mode=effective_mode,
    )
    cached = cache.get(key) if cache is not None else None
    if cached is not None and cached.advisory != shadow:
        # ── MODE-MATCH REPLAY (D2 fail-closed) ─────────────────────────
        # A cached verdict replays ONLY when the mode it was judged in
        # EQUALS the mode in force for THIS write. The recorded mode is
        # the verdict's ``advisory`` flag (every verdict carries
        # ``advisory=shadow`` — see :func:`_finalize_verdict`).
        #
        # WHY (the fail-open hole this closes): up to 512 stale LRU
        # entries judged while the gate was in SHADOW would otherwise
        # replay as ADVISORY after ``pok_gate.semantic_enforcement``
        # flips ON — an advisory verdict refuses nothing, so the gate
        # would fail OPEN under enforcement (D2 is fail-CLOSED). The
        # reverse direction matters too: an enforcement-era entry
        # replayed under SHADOW would gate a write the operator has
        # switched to advisory.
        #
        # Either mismatch ⇒ the entry is STALE for the current mode ⇒
        # treat it as a CACHE MISS and RE-JUDGE. The re-judged verdict
        # replaces the entry (the cache holds one verdict per key).
        cached = None
    if cached is not None:
        # Replay: ACCEPT / REJECT replay byte-identically; an ASK
        # replays AS-ASK with the SAME question but a FRESH gate_id
        # (T8.13 — the fresh id is what makes the pending TTL real).
        # The tightening (T10.6) is applied to the REPLAY too — the
        # CACHE holds the judge's RAW verdict (the tightening is a
        # deterministic function of it), so a store that tightened
        # after the verdict was cached still tightens the replay.
        return apply_tightening(
            replace(
                cached,
                gate_id=str(uuid.uuid4()),
                cache_hit=True,
                advisory=shadow,
                latency_ms=int((time.monotonic() - started) * 1000),
            ),
            rows=rows,
            candidate_slug=slug,
            candidate_body=body,
            tightening=Tightening.from_payload(tightening),
        )

    digest = build_active_digest(
        candidate_slug=slug,
        candidate_body=body,
        rows=rows,
        reason=reason,
        project_name=project_name,
    )
    prompt = build_judge_prompt(
        project_name=project_name,
        slug=slug,
        body=body,
        reason=reason,
        digest=digest,
    )

    if effective_mode == "stub":
        payload = _stub_verdict_payload(
            slug=slug, body=body, reason=reason, rows=rows
        )
    else:
        raw = _call_judge_with_retry(
            prompt,
            llm_config=llm_config,
            llm_call=llm_call,
            model=alias,
        )
        payload = parse_verdict_payload(raw)

    verdict = _finalize_verdict(
        payload,
        slug=slug,
        body=body,
        reason=reason,
        rows=rows,
        model_alias=model_alias,
        shadow=shadow,
        latency_ms=int((time.monotonic() - started) * 1000),
    )
    # The near-soft-max tightening (T10.6) — applied AFTER the verdict
    # rules, so a tightened verdict is never re-finalized (the
    # confidence floor and the R-RET remedy keep their say). The CACHE
    # stores the RAW verdict: the tightening is a deterministic
    # function of it, so the replay stays consistent.
    if cache is not None:
        cache.put(key, replace(verdict, gate_id="", cache_hit=False))
    return apply_tightening(
        verdict,
        rows=rows,
        candidate_slug=slug,
        candidate_body=body,
        tightening=Tightening.from_payload(tightening),
    )


def _call_judge_with_retry(
    prompt: str,
    *,
    llm_config: Mapping[str, Any] | None,
    llm_call: Callable[[str], str] | None,
    model: str,
) -> str:
    """The judge call + its retry budget (T7.2 / T7.10).

    At most ONE retry (so at most TWO attempts): the retry fires on a
    timeout or an UNPARSABLE answer (the attestation judge's
    retry-once semantics). The total wall clock is capped at
    :data:`GATE_WALL_CLOCK_CAP_S` (45s) — the binding defense that
    keeps the gate inside the 180s graph heartbeat.
    """
    attempt_timeout = get_gate_timeout_s()
    wall_cap = get_gate_wall_clock_cap_s()
    max_retries = get_gate_max_retries()
    deadline = time.monotonic() + wall_cap
    last_reason = "llm_error"
    for attempt in range(max_retries + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise POKGateUnavailable("timeout")
        budget = min(attempt_timeout, remaining)
        try:
            if llm_call is not None:
                raw = llm_call(prompt)
            else:
                raw = _house_llm_call(
                    llm_config or {},
                    prompt,
                    model=model,
                    timeout_s=budget,
                    wall_clock_cap_s=max(remaining, 1.0),
                )
        except POKGateUnavailable:
            raise
        except TimeoutError as exc:
            last_reason = "timeout"
            if attempt >= max_retries:
                raise POKGateUnavailable("timeout") from exc
            continue
        except Exception as exc:  # noqa: BLE001 — provider error → D2
            last_reason = "llm_error"
            logger.warning(
                "[pok_gate] judge call failed (attempt %s): %s",
                attempt + 1,
                type(exc).__name__,
            )
            if attempt >= max_retries:
                raise POKGateUnavailable("llm_error") from exc
            continue
        # A response arrived — an UNPARSABLE answer is retryable (the
        # attestation judge's incident-98b59dd7 semantics).
        try:
            parse_verdict_payload(raw)
        except POKGateUnavailable:
            last_reason = "unparsable"
            if attempt >= max_retries:
                raise
            continue
        return raw
    raise POKGateUnavailable(last_reason)


def _finalize_verdict(
    payload: Mapping[str, Any],
    *,
    slug: str,
    body: str,
    reason: str,
    rows: Sequence[ActiveRow],
    model_alias: str,
    shadow: bool,
    latency_ms: int,
) -> GateVerdict:
    """Apply the verdict rules to a parsed judge payload.

    * confidence ≤ 2/3 → ASK (never auto-reject) — the rule targets
      NEGATIVES: a REJECT or a MERGE the judge is not sure about asks
      the caller instead of acting (design-amendments-v2 §1);
    * a MERGE naming a target that is NOT in the digest → ASK (the gate
      will not merge into a row it cannot see);
    * R-RET: a NEW write whose slug shares no vocabulary with its body
      → ASK with a rename proposal (never auto-reject); in SHADOW the
      finding is advisory only;
    * the question for an ASK is the judge's question, or the family's
      canonical template (which NAMES the alternative home).
    """
    verdict = str(payload["verdict"])
    family = str(payload["family"])
    explanation = str(payload.get("explanation") or "")
    alternative = payload.get("alternative")
    confidence = payload.get("confidence")
    target_slug = payload.get("target_slug")
    proposed_body = payload.get("merged_body_proposal")
    proposed_slug = payload.get("proposed_slug")
    question = payload.get("question")
    rule_applied: str | None = None

    row_by_slug = {row.slug: row for row in rows}

    if verdict == "MERGE":
        if not target_slug or target_slug not in row_by_slug:
            # The judge named a target the digest does not carry — we
            # will not merge into a row we cannot see. ASK instead.
            verdict = "ASK"
            family = "redundancy"
            question = question or (
                "which ACTIVE row already carries this fact? name the slug"
            )
            target_slug = None
            proposed_body = None
        else:
            target = row_by_slug[target_slug]
            merge_kind = _merge_kind_for(
                target_body=target.body, proposed_body=proposed_body
            )
            if confidence is not None and confidence <= ASK_CONFIDENCE_THRESHOLD:
                verdict = "ASK"
                question = question or (
                    "the judge is not certain the active row carries this "
                    "fact — confirm the merge target"
                )
            else:
                return GateVerdict(
                    verdict="MERGE",
                    family=family,
                    explanation=explanation,
                    alternative=alternative,
                    confidence=confidence,
                    gate_id=str(uuid.uuid4()),
                    target_slug=target_slug,
                    merge_kind=merge_kind,
                    proposed_body=proposed_body,
                    proposed_body_sha256=(
                        _sha256(proposed_body) if proposed_body else None
                    ),
                    target_body_sha256=_sha256(target.body),
                    question=question,
                    latency_ms=latency_ms,
                    model_alias=model_alias,
                    proposed_slug=proposed_slug,
                    advisory=shadow,
                )

    if verdict == "REJECT":
        if confidence is not None and confidence <= ASK_CONFIDENCE_THRESHOLD:
            # Never auto-reject a verdict the judge is not sure about.
            verdict = "ASK"
            question = question or (
                "the judge is not certain this fact does not belong in "
                "POK — confirm or reword it"
            )
        else:
            return GateVerdict(
                verdict="REJECT",
                family=family,
                explanation=family_template(family, explanation),
                alternative=alternative or family_home(family),
                confidence=confidence,
                gate_id=str(uuid.uuid4()),
                question=question,
                latency_ms=latency_ms,
                model_alias=model_alias,
                advisory=shadow,
            )

    if verdict == "ACCEPT":
        flagged, rename = _retrievability_flag(
            slug=slug, body=body, proposed_slug=proposed_slug
        )
        if flagged:
            rule_applied = "R-RET"
            if not shadow:
                # Enforce mode: the remedy is a RENAME, never a reject.
                verdict = "ASK"
                question = (
                    "the slug shares no vocabulary with the body's own "
                    "operative terms — this row would never surface. "
                    "Re-slug it (proposal: %s) or confirm the slug."
                    % (rename or "(none)")
                )
                proposed_slug = rename or proposed_slug

    if verdict == "ASK":
        if not question:
            question = family_template(family, explanation)
        return GateVerdict(
            verdict="ASK",
            family=family,
            explanation=explanation,
            alternative=alternative or family_home(family),
            confidence=confidence,
            gate_id=str(uuid.uuid4()),
            target_slug=target_slug,
            question=question[:QUESTION_MAX_CHARS],
            latency_ms=latency_ms,
            model_alias=model_alias,
            proposed_slug=proposed_slug,
            retrievability_flagged=rule_applied == "R-RET",
            advisory=shadow,
            rule_applied=rule_applied,
        )

    # ACCEPT
    return GateVerdict(
        verdict="ACCEPT",
        family=family,
        explanation=explanation,
        alternative=alternative,
        confidence=confidence,
        gate_id=str(uuid.uuid4()),
        latency_ms=latency_ms,
        model_alias=model_alias,
        proposed_slug=proposed_slug,
        retrievability_flagged=rule_applied == "R-RET",
        advisory=shadow,
        rule_applied=rule_applied,
    )


# ═══════════════════════════════════════════════════════════════════════════
# The pending-ASK store (T8.6 / T8.7)
# ═══════════════════════════════════════════════════════════════════════════


class AskOutcomeClass:
    """The answer CLASSES the G1 path understands."""

    CONFIRM = "confirm"
    DECLINE = "decline"


#: The affirmative / negative vocabularies. The classification is
#: CONSERVATIVE: anything that matches neither is UNCLASSIFIABLE
#: (``pok_gate_answer_unrecognized``) and NEVER defaults to accept.
_ANSWER_CONFIRM_TOKENS: frozenset[str] = frozenset(
    {
        "yes",
        "y",
        "confirm",
        "confirmed",
        "approve",
        "approved",
        "accept",
        "accepted",
        "ok",
        "okay",
        "proceed",
        "doit",
        "merge",
        "merged",
        "agreed",
        "correct",
    }
)

_ANSWER_DECLINE_TOKENS: frozenset[str] = frozenset(
    {
        "no",
        "n",
        "decline",
        "declined",
        "reject",
        "rejected",
        "cancel",
        "cancelled",
        "canceled",
        "abort",
        "stop",
        "disagree",
        "wrong",
        "incorrect",
    }
)


def classify_answer(answer: str | None) -> str | None:
    """Classify a free-text answer (T8.7).

    Returns ``"confirm"``, ``"decline"`` or ``None`` (UNCLASSIFIABLE).
    An unclassifiable answer NEVER defaults to accept — it is the
    Emperor's catch: the answer path must not launder a garbage
    answer into a write.
    """
    text = " ".join((answer or "").lower().split())
    if not text:
        return None
    tokens = set(_TOKEN_RE.findall(text))
    if tokens & _ANSWER_DECLINE_TOKENS:
        return AskOutcomeClass.DECLINE
    if tokens & _ANSWER_CONFIRM_TOKENS:
        return AskOutcomeClass.CONFIRM
    return None


@dataclass
class PendingGate:
    """One pending ASK — the IN-PROCESS pending state (T8.6).

    The entry stores the ADJUDICATED STRIPPED body + ALL the upsert
    inputs, so the answer path re-runs ``POKService.upsert`` with
    ``gate_fn=None`` and the scope/fence/budget/no-op/caps checks
    re-run at answer time (design-amendments-v2 §13.2 fork G).

    NO live-store writes: the pending state lives in this process's
    memory only. A daemon restart loses the pending ASK — the answer
    then refuses with ``pok_gate_unknown`` (fail-closed, never an
    accidental accept).
    """

    gate_id: str
    project_id: str
    owner_agent_id: str
    owner_instance_id: str
    slug: str
    body: str
    reason: str
    domain: str
    provenance: str
    audience: str
    source_tier: str | None
    question: str
    verdict: GateVerdict
    created_at: float
    expires_at: float
    merge_target_slug: str | None = None
    merge_target_body: str | None = None
    merge_kind: str | None = None
    proposed_body: str | None = None
    #: The R-RET COUNT-NEUTRALITY CARRIER (Wave-2 code review C-1,
    #: fixed in the Phase-12 addendum round): when the gate converted
    #: an ACCEPT into an ASK-with-rename, this is the slug the CALLER
    #: passed — the PRE-RENAME slug. If an ACTIVE row lives under it,
    #: the confirming answer must SUPERSEDE that row under the renamed
    #: slug (``supersede_and_insert`` — the count-preserving,
    #: FOR-UPDATE-locked, counter-carry-over machinery, T8.15) instead
    #: of inserting a fresh row; without it, every confirmed rename
    #: leaves the mis-slugged row ACTIVE and adds a second row (+1
    #: active per write). ``None`` on every non-rename ASK — the
    #: plain-ASK behavior is byte-unchanged.
    prior_slug: str | None = None
    answered: bool = False
    #: The ASK is ROUTED TO THE CURATOR (T8.12): the merge target is
    #: pinned / curated, so only a curator (leader / project-manager)
    #: may execute it.
    #:
    #: THIS IS A ROUTING ANNOTATION, NOT AN AUTHORITY CARVE-OUT. The
    #: answer path stays OWNER-SCOPED (T8.5): a non-owner is refused
    #: with ``pok_gate_not_owner`` — a curator role does NOT unlock
    #: another agent's pending gate. The flag is consumed by the
    #: ROUTED PAYLOAD (the ``routed_to`` / ``curator_agents`` fields the
    #: caller sees), which tells the caller that the confirmation has to
    #: reach a curator. A curator who wants to merge into a pinned row
    #: answers as the OWNING agent (or performs the merge themselves).
    routed_to_curator: bool = False


#: The TTL resolver state — Pattern C (cached global + one-shot WARN +
#: restart-read).
_CACHED_ASK_TTL_S: float | None = None
_ASK_TTL_INVALID_WARN_EMITTED: bool = False
_ASK_TTL_BELOW_CLAMP_WARN_EMITTED: bool = False
_ASK_TTL_ABOVE_CLAMP_WARN_EMITTED: bool = False


def _parse_ask_ttl_s(source: Mapping[str, str]) -> float:
    """Parse the ASK TTL env, fail-OPEN to the default, clamp to bounds."""
    global _ASK_TTL_INVALID_WARN_EMITTED, _ASK_TTL_BELOW_CLAMP_WARN_EMITTED
    global _ASK_TTL_ABOVE_CLAMP_WARN_EMITTED
    raw = str(source.get(POK_GATE_ASK_TTL_S_ENV, "") or "").strip()
    if not raw:
        return DEFAULT_ASK_TTL_S
    try:
        value = float(raw)
    except ValueError:
        if not _ASK_TTL_INVALID_WARN_EMITTED:
            _ASK_TTL_INVALID_WARN_EMITTED = True
            logger.warning(
                "%s=%r is not a valid float; failing OPEN to the default "
                "%.0fs. Restart required after fixing the env value.",
                POK_GATE_ASK_TTL_S_ENV,
                raw,
                DEFAULT_ASK_TTL_S,
            )
        return DEFAULT_ASK_TTL_S
    if value <= 0:
        if not _ASK_TTL_INVALID_WARN_EMITTED:
            _ASK_TTL_INVALID_WARN_EMITTED = True
            logger.warning(
                "%s=%r is not a positive TTL; failing OPEN to the default "
                "%.0fs.",
                POK_GATE_ASK_TTL_S_ENV,
                raw,
                DEFAULT_ASK_TTL_S,
            )
        return DEFAULT_ASK_TTL_S
    if value < MIN_ASK_TTL_S:
        if not _ASK_TTL_BELOW_CLAMP_WARN_EMITTED:
            _ASK_TTL_BELOW_CLAMP_WARN_EMITTED = True
            logger.warning(
                "%s=%r is below the minimum clamp %.0fs; clamping to "
                "%.0fs — a shorter TTL expires legitimate answers and "
                "turns the gate into a write-blocker.",
                POK_GATE_ASK_TTL_S_ENV,
                raw,
                MIN_ASK_TTL_S,
                MIN_ASK_TTL_S,
            )
        return MIN_ASK_TTL_S
    if value > MAX_ASK_TTL_S:
        if not _ASK_TTL_ABOVE_CLAMP_WARN_EMITTED:
            _ASK_TTL_ABOVE_CLAMP_WARN_EMITTED = True
            logger.warning(
                "%s=%r is above the maximum clamp %.0fs; clamping to %.0fs.",
                POK_GATE_ASK_TTL_S_ENV,
                raw,
                MAX_ASK_TTL_S,
                MAX_ASK_TTL_S,
            )
        return MAX_ASK_TTL_S
    return value


def get_ask_ttl_s() -> float:
    """The cached ASK TTL (Pattern C, restart-read). Default 1200s."""
    global _CACHED_ASK_TTL_S
    if _CACHED_ASK_TTL_S is None:
        _CACHED_ASK_TTL_S = _parse_ask_ttl_s(os.environ)
    return _CACHED_ASK_TTL_S


def reset_ask_ttl_resolver_for_tests() -> None:
    """Clear the cached ASK TTL + the one-shot WARN flags (test-only)."""
    global _CACHED_ASK_TTL_S, _ASK_TTL_INVALID_WARN_EMITTED
    global _ASK_TTL_BELOW_CLAMP_WARN_EMITTED, _ASK_TTL_ABOVE_CLAMP_WARN_EMITTED
    _CACHED_ASK_TTL_S = None
    _ASK_TTL_INVALID_WARN_EMITTED = False
    _ASK_TTL_BELOW_CLAMP_WARN_EMITTED = False
    _ASK_TTL_ABOVE_CLAMP_WARN_EMITTED = False


class PendingGateStore:
    """The IN-PROCESS pending-ASK store (T8.6).

    Owner-scoped (the minting agent + instance must match the
    answering one) and TTL-bounded. NO live-store writes — the pending
    state is process memory only.

    The six typed refusals (T8.7) are returned as ``(pending, refusal)``
    pairs by :meth:`resolve`:

    * ``pok_gate_unknown`` — no such id (never minted, or the process
      restarted);
    * ``pok_gate_expired`` — the TTL elapsed;
    * ``pok_gate_already_answered`` — audited no-op;
    * ``pok_gate_not_owner`` — audited;
    * ``pok_gate_answer_required`` — the answer is empty;
    * ``pok_gate_answer_unrecognized`` — the free text is
      unclassifiable ⇒ NO write.
    """

    def __init__(self, ttl_s: float | None = None) -> None:
        self._ttl_s = ttl_s
        self._pending: dict[str, PendingGate] = {}

    @property
    def ttl_s(self) -> float:
        """The effective TTL (the injected value wins over the resolver)."""
        return self._ttl_s if self._ttl_s is not None else get_ask_ttl_s()

    def mint(self, pending: PendingGate) -> PendingGate:
        """Register a pending ASK (the caller supplies the timestamps)."""
        self._pending[pending.gate_id] = pending
        return pending

    def get(self, gate_id: str) -> PendingGate | None:
        """The pending entry for an id (``None`` when unknown/expired)."""
        pending = self._pending.get(gate_id)
        if pending is None:
            return None
        if time.monotonic() >= pending.expires_at:
            return None
        return pending

    def resolve(
        self,
        gate_id: str,
        *,
        agent_id: str,
        instance_id: str,
        answer: str | None,
    ) -> tuple[PendingGate | None, str | None]:
        """Resolve an answer against the pending store.

        Returns ``(pending, refusal_code)``. Exactly one of the two is
        set. ``refusal_code`` is one of the six typed refusals.
        """
        pending = self._pending.get(gate_id)
        if pending is None:
            return (None, "pok_gate_unknown")
        if time.monotonic() >= pending.expires_at:
            # Expired — fail-closed, NO write (the TTL pin).
            self._pending.pop(gate_id, None)
            return (None, "pok_gate_expired")
        if pending.answered:
            return (None, "pok_gate_already_answered")
        if (pending.owner_agent_id or "") != (agent_id or "") or (
            pending.owner_instance_id or ""
        ) != (instance_id or ""):
            return (None, "pok_gate_not_owner")
        if not answer or not str(answer).strip():
            return (None, "pok_gate_answer_required")
        return (pending, None)

    def consume(self, gate_id: str) -> None:
        """Mark a pending ASK answered (it can never be answered twice)."""
        pending = self._pending.get(gate_id)
        if pending is not None:
            pending.answered = True

    def drop(self, gate_id: str) -> None:
        """Forget a pending ASK (the expiry path)."""
        self._pending.pop(gate_id, None)

    def expired_ids(self, now: float | None = None) -> list[str]:
        """The ids of every EXPIRED pending ASK (the librarian's pass)."""
        moment = time.monotonic() if now is None else now
        return [
            gate_id
            for gate_id, pending in self._pending.items()
            if moment >= pending.expires_at
        ]

    def __len__(self) -> int:
        return len(self._pending)

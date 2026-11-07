"""The POK service — write-path policy, query, and curate (Phase 1 T2).

Policy layer over :class:`daemon.repositories.pok.repository.POKRepository`
(mechanics) — this module owns:

* the §4.7 SIZING CONSTANTS (verbatim; per-view by the ER-1 ruling);
* the WRITE-TIME FENCE (``pok_body_structural_token``: newline /
  leading ``#`` at body start / ``[SYSTEM CONTEXT:`` / triple-backtick
  — A-29; render-side defense lives in the injection builder's fixed
  template + :func:`escape_for_context_block` + newline flattening);
* the ESCAPE-AWARE BUDGET (``len(escape_for_context_block(body))
  ≤ POK_BODY_MAX_CHARS`` — W5: measure AFTER escaping, the
  ``&``/``<``/``>`` expansion can inflate);
* the SCOPE PREDICATE (A-5): ``project_id ==
  SYSTEM_DEFAULT_PROJECT_ID`` (read at CALL time — the constant is
  ``None`` until startup wires it) → typed refusal BOTH directions
  (write and read; AC10), name-based fallback for unit-test contexts;
* EXACT-DUP no-op echo; the NORMALIZED-HASH dispute-laundering guard
  (a disputed body cannot re-enter under a new slug); the SUPERSEDE
  transaction; ADMISSION (64-active refuse-never-evict with the
  named cure; family >3 active → ``pok_family_full``);
* ``pok_curate`` policy: pin cap 2 PER PROJECT (ER-1 consistency
  note — pins are project-level facts), archived forward-only,
  audience/audience_agents validation;
* the QUERY path (B5 selector reuse — ``critical_notes_selector``
  fusion with ITS configurable weights, zero POK-invented weights;
  pinned bypass is the selector's own D2 rule; family chains in slug
  order; embedding lag surfaced, never fatal; UNIVERSAL READ — the
  audience dimension NEVER filters a read, ER-1.4).

Attribution ≠ authorization: every write carries the
factory-captured ``agent_id`` as ``created_by_agent``; the
leader/PM-only gates (supersede-of-pinned, curate) are enforced
IN-BODY here from that same captured id — never from a
caller-supplied key.

Never-raise discipline: query-path failures degrade (typed error
result objects / BM25-only) rather than raising into the tool
surface; the injection path has its own swallow contract in
``daemon/services/pok_injection.py``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from daemon import constants as _constants
from daemon.constants import SYSTEM_DEFAULT_PROJECT_NAME
from daemon.repositories.pok.models import (
    POK_AUDIENCES,
    POK_DEFAULT_AUDIENCE,
    POK_DOMAINS,
    POK_FAMILY_SLUG_PATTERN,
    POK_PROVENANCE_VALUES,
    POK_STATUSES,
    ProjectOperatingKnowledge,
)
from daemon.repositories.pok.repository import POKRepository, UNSET

logger = logging.getLogger(__name__)

# ── §4.7 sizing constants (unchanged by the ruling — PER-VIEW) ────────────

#: escaped body budget at write (``pok_body_oversize``).
POK_BODY_MAX_CHARS = 240
#: slug bound (``pok_slug_oversize``).
POK_SLUG_MAX_CHARS = 44
#: derived per-entry render ceiling (§4.5 bounded arithmetic).
POK_ENTRY_RENDER_MAX_CHARS = 332
#: refuse-never-evict active cap — PER PROJECT, not per audience.
POK_ACTIVE_CAP_PER_PROJECT = 64
#: pin admission cap — PER PROJECT (pins render in every audience view
#: that can see the entry).
POK_PIN_CAP_PER_PROJECT = 2
#: family admission bound.
POK_FAMILY_MAX = 3
#: FULL rendered block incl. wrapper — PER-VIEW (audience filter
#: precedes admission, so the project-wide worst case bounds every view).
POK_BLOCK_MAX_CHARS = 2_900
#: per-view entry cap.
POK_BLOCK_MAX_ENTRIES = 8
#: decay window (``[stale Nd]`` marks; ranks down, never drops).
POK_VERIFY_WINDOW_DAYS = 30

#: slug charset (``[a-z0-9-]``) + family regex (single-sourced from
#: the models module).
POK_SLUG_PATTERN = re.compile(r"^[a-z0-9-]+$")
POK_FAMILY_RE = re.compile(POK_FAMILY_SLUG_PATTERN)


# ── typed error taxonomy ──────────────────────────────────────────────────


class POKError(Exception):
    """A typed POK refusal. ``code`` is the stable taxonomy token the
    tool surfaces render verbatim; ``detail`` carries the cure hint."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.details}


def _slug_family(slug: str) -> str | None:
    """The family key for a family-ordered slug, else ``None``."""
    m = POK_FAMILY_RE.match(slug)
    return m.group(1) if m else None


def normalize_body(body: str) -> str:
    """The dispute-laundering/dedup normalization: lowercase +
    whitespace-collapsed (the critical-notes collision semantics —
    NO token-overlap matching, 2026-09-10 hijack lesson)."""
    return " ".join((body or "").lower().split())


def content_hash(body: str) -> str:
    """SHA-256 over the VERBATIM body (the stored digest)."""
    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()


# ── scope predicate (A-5) ─────────────────────────────────────────────────


def is_default_project(project_id: str | None, project_name: str | None = None) -> bool:
    """True when the target is the system-default project.

    ``SYSTEM_DEFAULT_PROJECT_ID`` is ``None`` until startup wires it,
    so the id check reads the constant at CALL time; the name check
    is the unit-test/robustness fallback (the
    ``context_messages.py`` is_system_default pattern, mirrored).
    """
    if project_id is not None and project_id == _constants.SYSTEM_DEFAULT_PROJECT_ID:
        return True
    return bool(
        project_name is not None and project_name == SYSTEM_DEFAULT_PROJECT_NAME
    )


# ── write-time fence (A-29) ───────────────────────────────────────────────

_FENCE_TOKENS = ("[SYSTEM CONTEXT:", "```")


def fence_check(body: str) -> POKError | None:
    """The write-time structural fence. Returns the typed refusal or
    ``None`` when the body is fence-clean.

    Refused tokens: any NEWLINE (bodies are single-paragraph),
    a leading ``#`` at body start (forged-header), ``[SYSTEM CONTEXT:``
    (block-boundary forgery), triple-backtick (data-fence escape).
    """
    if not body or not body.strip():
        return POKError(
            "pok_body_empty", "body is empty — write the fact, not a blank row"
        )
    if "\n" in body or "\r" in body:
        return POKError(
            "pok_body_structural_token",
            "body contains a newline — POK bodies are single-paragraph "
            "fenced-verbatim facts",
        )
    if body.startswith("#"):
        return POKError(
            "pok_body_structural_token",
            "body starts with '#' — forged headers are refused at write",
        )
    for token in _FENCE_TOKENS:
        if token in body:
            return POKError(
                "pok_body_structural_token",
                f"body contains the structural token {token!r} — refused "
                "at write (AC17 forged-header leg)",
            )
    return None


# ── role gate (authorization from the FACTORY-CAPTURED agent id) ──────────

CURATOR_AGENT_IDS: frozenset[str] = frozenset({"leader", "project-manager"})


def is_curator(agent_id: str | None) -> bool:
    """leader / project-manager (§4.3 authorization matrix)."""
    return (agent_id or "") in CURATOR_AGENT_IDS


# ── result shapes ─────────────────────────────────────────────────────────


@dataclass
class UpsertOutcome:
    """Structured pok_upsert result (the tool layer renders it)."""

    action: str  # "created" | "superseded" | "no-op"
    entry: ProjectOperatingKnowledge
    superseded_id: str | None = None


@dataclass
class QueryOutcome:
    """Structured pok_query result (universal read; lag surfaced)."""

    entries: list[ProjectOperatingKnowledge] = field(default_factory=list)
    lineage: dict[str, list[ProjectOperatingKnowledge]] = field(default_factory=dict)
    embedding_lag_entries: list[str] = field(default_factory=list)
    bm25_only: bool = False


# ── the service ───────────────────────────────────────────────────────────


class POKService:
    """Write/query/curate policy over one engine-passed repository."""

    def __init__(self, engine: Any) -> None:
        self._repo = POKRepository(engine)

    # ── pok_upsert (§4.2) ────────────────────────────────────────────────

    def upsert(
        self,
        *,
        project_id: str,
        slug: str,
        body: str,
        domain: str = POK_DOMAINS[-1],
        provenance: str = "observed",
        audience: str = POK_DEFAULT_AUDIENCE,
        agent_id: str,
        project_name: str | None = None,
        escape_fn: Any = None,
    ) -> UpsertOutcome:
        """The write path: fence → budgets → scope → dedup/launder →
        admission → supersede-or-insert.

        ``escape_fn`` is :func:`escape_for_context_block` passed by the
        tool layer (lazy-import discipline keeps this module
        cycle-neutral with ``context_messages``).
        """
        # 1. scope predicate (A-5) — typed refusal, write direction.
        if is_default_project(project_id, project_name):
            raise POKError(
                "pok_default_project",
                "project_operating_knowledge is scoped to non-default "
                "projects — the system-default project accepts no POK "
                "writes (AC10)",
            )
        # 2. slug validation.
        slug = (slug or "").strip()
        if not slug or not POK_SLUG_PATTERN.match(slug):
            raise POKError(
                "pok_slug_invalid",
                "slug must match [a-z0-9-]+ (kebab-case, lowercase)",
            )
        if len(slug) > POK_SLUG_MAX_CHARS:
            raise POKError(
                "pok_slug_oversize",
                f"slug is {len(slug)} chars — bound is "
                f"{POK_SLUG_MAX_CHARS} (POK_SLUG_MAX_CHARS)",
            )
        if domain not in POK_DOMAINS:
            raise POKError(
                "pok_domain_invalid",
                f"domain must be one of {POK_DOMAINS}",
            )
        if provenance not in POK_PROVENANCE_VALUES:
            raise POKError(
                "pok_provenance_invalid",
                f"provenance must be one of {POK_PROVENANCE_VALUES}",
            )
        if audience not in POK_AUDIENCES:
            raise POKError(
                "pok_audience_invalid",
                "audience must be one of "
                f"{POK_AUDIENCES} (stated at capture, default "
                f"{POK_DEFAULT_AUDIENCE!r} — Emperor ruling ER-1)",
            )
        # 3. write-time fence (A-29).
        fence = fence_check(body)
        if fence is not None:
            raise fence
        # 4. escape-aware budget (W5): measure AFTER escaping.
        body = body.strip()
        if escape_fn is None:
            from daemon.services.context_messages import (
                escape_for_context_block as escape_fn,  # noqa: PLC0415
            )
        escaped_len = len(escape_fn(body))
        if escaped_len > POK_BODY_MAX_CHARS:
            raise POKError(
                "pok_body_oversize",
                f"escaped body is {escaped_len} chars — budget is "
                f"{POK_BODY_MAX_CHARS} (POK_BODY_MAX_CHARS, measured "
                "post-escape per W5)",
            )
        # 5. exact-dup / supersede / laundering on the active slug.
        prior = self._repo.get_by_slug(project_id, slug, status="active")
        if prior is not None:
            if normalize_body(prior.body) == normalize_body(body):
                return UpsertOutcome(action="no-op", entry=prior)
            return self._supersede(
                prior=prior,
                project_id=project_id,
                slug=slug,
                body=body,
                domain=domain,
                provenance=provenance,
                audience=audience,
                agent_id=agent_id,
            )
        # 6. dispute-laundering guard: the same normalized body cannot
        # re-enter under a NEW slug while a disputed row holds it.
        laundered = self._find_disputed_launder(project_id, body)
        if laundered is not None:
            raise POKError(
                "pok_dispute_laundering",
                "this body's normalized form matches the DISPUTED entry "
                f"{laundered.slug!r} ({laundered.id[:8]}…) — resolve the "
                "dispute via pok_curate instead of re-adding the content "
                "under a new slug",
                disputed_slug=laundered.slug,
            )
        # 7. admission — 64-active refuse-never-evict (per PROJECT).
        active_count = self._repo.count_active(project_id)
        if active_count >= POK_ACTIVE_CAP_PER_PROJECT:
            cure = self._repo.oldest_least_verified_active(project_id)
            raise POKError(
                "pok_active_cap",
                f"project holds {active_count} active POK entries — the "
                f"{POK_ACTIVE_CAP_PER_PROJECT}-cap refuses rather than "
                "evicts (terminal 64-cap lockout is CURABLE: archive a "
                "superseded fact via pok_curate)",
                oldest_least_verified=(
                    cure.slug if cure is not None else None
                ),
            )
        # 8. family admission (§4.5: family max 3 active).
        family = _slug_family(slug)
        if family is not None:
            family_active = self._repo.count_active_in_family(project_id, family)
            if family_active >= POK_FAMILY_MAX:
                raise POKError(
                    "pok_family_full",
                    f"family {family!r} already holds {family_active} "
                    f"active entries — the bound is {POK_FAMILY_MAX} "
                    "(POK_FAMILY_MAX); supersede an existing member or "
                    "archive one via pok_curate",
                    family=family,
                )
        entry = ProjectOperatingKnowledge(
            project_id=project_id,
            slug=slug,
            domain=domain,
            audience=audience,
            audience_agents=None,
            body=body,
            provenance=provenance,
            status="active",
            disputed=False,
            superseded_by_id=None,
            content_sha256=content_hash(body),
            created_by_agent=agent_id or "unknown",
        )
        self._repo.insert(entry)
        return UpsertOutcome(action="created", entry=entry)

    def _supersede(
        self,
        *,
        prior: ProjectOperatingKnowledge,
        project_id: str,
        slug: str,
        body: str,
        domain: str,
        provenance: str,
        audience: str,
        agent_id: str,
    ) -> UpsertOutcome:
        """The supersede leg of the write path.

        SUPERSEDE-OF-PINNED → leader/PM only (``pok_pinned_requires_
        curator``); the successor starts unpinned (re-pin is an
        explicit curator act). Family admission counts the successor
        against the family bound; the prior row leaves ``active`` in
        the SAME transaction it enters ``superseded``.
        """
        if prior.pinned and not is_curator(agent_id):
            raise POKError(
                "pok_pinned_requires_curator",
                f"entry {slug!r} is PINNED — superseding it requires a "
                "curator (leader / project-manager); attribution "
                f"{agent_id!r} is not a curator (attribution ≠ "
                "authorization)",
            )
        # Family bound (§4.5): a supersede swap is COUNT-PRESERVING by
        # construction — the prior active row leaves ``active`` in the
        # same transaction the successor enters it, and both carry the
        # same slug (hence the same family). The family count cannot
        # move here, so no pok_family_full refusal exists on this
        # path; the NEW-slug admission path above owns that refusal.
        prior_id = prior.id
        successor = ProjectOperatingKnowledge(
            project_id=project_id,
            slug=slug,
            domain=domain,
            audience=audience,
            audience_agents=None,
            body=body,
            provenance=provenance,
            status="active",
            disputed=False,
            superseded_by_id=None,
            content_sha256=content_hash(body),
            created_by_agent=agent_id or "unknown",
        )
        self._repo.supersede_and_insert(prior, successor)
        return UpsertOutcome(
            action="superseded", entry=successor, superseded_id=prior_id
        )

    def _find_disputed_launder(
        self, project_id: str, body: str
    ) -> ProjectOperatingKnowledge | None:
        """Any row of the project (any status) whose normalized body
        equals ``body``'s AND is disputed. Exact-dup on the SAME slug
        was already handled (no-op echo), so a hit here is a NEW-slug
        laundering attempt by construction."""
        target = normalize_body(body)
        rows = self._repo.list_entries(
            project_id, statuses=tuple(POK_STATUSES), include_superseded_of=True
        )
        for row in rows:
            if row.disputed and normalize_body(row.body) == target:
                return row
        return None

    # ── pok_query (§4.6 — UNIVERSAL READ, ER-1.4) ────────────────────────

    def query(
        self,
        *,
        project_id: str,
        query: str = "",
        domain: str | None = None,
        agent_id: str,
        project_name: str | None = None,
        query_embedding: list[float] | None = None,
    ) -> QueryOutcome:
        """One step, project-scoped, selector-reuse retrieval.

        UNIVERSAL READ INVARIANT (ER-1.4): NO audience filter exists
        on this path — any agent on the project reads every entry.
        (``agent_id`` participates ONLY in access bookkeeping.)

        Pinned rows bypass the fusion threshold (the selector's D2
        rule). Family chains render in slug order. Embedding lag is
        SURFACED (``bm25_only`` / ``embedding_lag_entries``), never
        fatal (AC14). Superseded lineages ride along per entry slug.
        """
        if is_default_project(project_id, project_name):
            raise POKError(
                "pok_default_project",
                "project_operating_knowledge is scoped to non-default "
                "projects — the system-default project holds no POK "
                "rows (AC10)",
            )
        if domain is not None and domain not in POK_DOMAINS:
            raise POKError(
                "pok_domain_invalid", f"domain must be one of {POK_DOMAINS}"
            )
        # F3 exclusions: ACTIVE rows only (lineage walk per entry below).
        rows = self._repo.list_entries(project_id, statuses=("active",))
        if domain is not None:
            rows = [r for r in rows if r.domain == domain]
        outcome = QueryOutcome()
        if rows:
            ranked = self._rank_with_selector(
                rows,
                query=query,
                query_embedding=query_embedding,
            )
        else:
            ranked = []
        # family chains in slug order: group ranked survivors by family
        # and order within each family by slug; unordered slugs keep
        # their ranked position relative to each other.
        outcome.entries = self._order_families_slugwise(ranked)
        for entry in outcome.entries:
            chain = [
                r
                for r in self._repo.lineage(entry.id)
                if r.id != entry.id
            ]
            if chain:
                outcome.lineage[entry.slug] = chain
        outcome.embedding_lag_entries = [
            e.slug
            for e in outcome.entries
            if e.id not in self._lag_aware_embedding_ids(project_id, outcome.entries)
        ]
        outcome.bm25_only = query_embedding is None
        # access bookkeeping (best-effort; never blocks the read).
        try:
            self._repo.bump_access([e.id for e in outcome.entries])
        except Exception as exc:  # pragma: no cover - defensive
            logger.info("[POK:Degraded] access bookkeeping failed: %s", exc)
        return outcome

    def _lag_aware_embedding_ids(
        self, project_id: str, entries: list[ProjectOperatingKnowledge]
    ) -> set[str]:
        try:
            return set(self._repo.embeddings_map(project_id).keys())
        except Exception:  # pragma: no cover - defensive
            return {e.id for e in entries}

    # B5 selector reuse — ZERO POK-invented weights. Maps POK rows
    # onto the selector's note-dict shape (``category=domain``,
    # ``summary=body``, ``reference=slug``, ``priority='high'`` so the
    # pinned/floor ordering is deterministic), runs
    # :func:`select_critical_notes_for_injection` with its DEFAULT
    # :class:`SelectionConfig` (the configurable fusion
    # weights/threshold surface — boot-installed overrides flow through
    # the same install seam), then maps the survivors back. Pinned
    # bypass is the selector's own D2 rule.
    def rank_for_view(
        self,
        rows: list[ProjectOperatingKnowledge],
        query: str,
        query_embedding: list[float] | None,
        embeddings_map: dict[str, list[float]] | None = None,
    ) -> list[ProjectOperatingKnowledge]:
        """The :func:`~daemon.services.pok_injection.admit_view`
        ``rank_fn`` adapter (positional rows/query/embedding/map — the
        map is optional; ``_rank_with_selector`` self-supplies it from
        the repo when absent)."""
        return self._rank_with_selector(
            rows, query=query, query_embedding=query_embedding
        )

    def _rank_with_selector(
        self,
        rows: list[ProjectOperatingKnowledge],
        *,
        query: str,
        query_embedding: list[float] | None,
    ) -> list[ProjectOperatingKnowledge]:
        """B5 selector reuse — ZERO POK-invented weights.

        Maps POK rows onto the selector's note-dict shape
        (``category=domain``, ``summary=body``, ``reference=slug``,
        ``priority='high'`` so the pinned/floor ordering is
        deterministic), runs
        :func:`select_critical_notes_for_injection` with its DEFAULT
        :class:`SelectionConfig` (the configurable fusion
        weights/threshold surface — boot-installed overrides flow
        through the same install seam), then maps the survivors back.
        Pinned bypass is the selector's own D2 rule.
        """
        from daemon.services.critical_notes_selector import (  # noqa: PLC0415
            select_critical_notes_for_injection,
        )

        note_rows = [self._to_note_dict(r) for r in rows]
        embeddings_map: dict[str, list[float]] = {}
        try:
            embeddings_map = self._repo.embeddings_map(rows[0].project_id)
        except Exception as exc:  # pragma: no cover - defensive
            logger.info("[POK:Degraded] embeddings map unavailable: %s", exc)
        result = select_critical_notes_for_injection(
            note_rows,
            query or "",
            embeddings_map,
            query_embedding,
        )
        by_id = {r.id: r for r in rows}
        ordered_ids = [n["id"] for n in result.pinned] + [
            n["id"] for n in result.tail
        ]
        seen: set[str] = set()
        out: list[ProjectOperatingKnowledge] = []
        for rid in ordered_ids:
            if rid in seen:
                continue
            seen.add(rid)
            row = by_id.get(rid)
            if row is not None:
                out.append(row)
        return out

    @staticmethod
    def _to_note_dict(row: ProjectOperatingKnowledge) -> dict[str, Any]:
        return {
            "id": row.id,
            "category": row.domain,
            "summary": row.body,
            "reference": row.slug,
            "priority": "high",
            "pinned": bool(row.pinned),
            "created_at": row.created_at,
            "disputed": bool(row.disputed),
        }

    @staticmethod
    def _order_families_slugwise(
        ranked: list[ProjectOperatingKnowledge],
    ) -> list[ProjectOperatingKnowledge]:
        """Family chains in slug order: consecutive members of the same
        family are re-ordered slugwise as a block (the chain never
        splits); unordered slugs keep their ranked relative order."""
        out: list[ProjectOperatingKnowledge] = []
        i = 0
        while i < len(ranked):
            family = _slug_family(ranked[i].slug)
            if family is None:
                out.append(ranked[i])
                i += 1
                continue
            j = i
            block: list[ProjectOperatingKnowledge] = []
            while j < len(ranked) and _slug_family(ranked[j].slug) == family:
                block.append(ranked[j])
                j += 1
            block.sort(key=lambda r: r.slug)
            out.extend(block)
            i = j
        return out

    # ── pok_curate (§4.3) ────────────────────────────────────────────────

    def curate(
        self,
        *,
        project_id: str,
        slug: str,
        agent_id: str,
        pinned: bool | None = None,
        disputed: bool | None = None,
        archived: bool | None = None,
        audience: str | None = None,
        audience_agents: list[str] | None = None,
        project_name: str | None = None,
    ) -> ProjectOperatingKnowledge:
        """Curator flips. The leader/PM gate is enforced HERE from the
        factory-captured ``agent_id`` (attribution ≠ authorization).

        Pin cap 2 PER PROJECT (``pok_pin_cap``). Archived is
        forward-only (``pok_already_archived`` / ``pok_unarchive_``).
        ``audience_agents=[]`` clears back to the default set.
        """
        if is_default_project(project_id, project_name):
            raise POKError(
                "pok_default_project",
                "project_operating_knowledge is scoped to non-default "
                "projects (AC10)",
            )
        if not is_curator(agent_id):
            raise POKError(
                "pok_curator_required",
                "pok_curate is leader / project-manager only — "
                f"attribution {agent_id!r} is not a curator "
                "(attribution ≠ authorization)",
            )
        if audience is not None and audience not in POK_AUDIENCES:
            raise POKError(
                "pok_audience_invalid",
                f"audience must be one of {POK_AUDIENCES}",
            )
        if audience_agents is not None:
            bad = [a for a in audience_agents if not isinstance(a, str) or not a.strip()]
            if bad:
                raise POKError(
                    "pok_audience_agents_invalid",
                    f"audience_agents must be agent-id strings; bad: {bad}",
                )
        row = self._repo.get_by_slug(project_id, slug, status=None)
        if row is None or row.status == "superseded":
            raise POKError(
                "pok_not_found",
                f"no active/archived entry {slug!r} on this project "
                "(superseded rows are curated through their successor)",
            )
        if archived is True and row.status == "archived":
            raise POKError(
                "pok_already_archived", f"entry {slug!r} is already archived"
            )
        if archived is False:
            raise POKError(
                "pok_unarchive_forward_only",
                "archived is FORWARD-ONLY — an archived entry cannot "
                "return (write a successor via pok_upsert instead)",
            )
        if pinned is True and not row.pinned:
            pinned_count = self._count_pinned(project_id)
            if pinned_count >= POK_PIN_CAP_PER_PROJECT:
                pinned_slugs = [
                    r.slug
                    for r in self._repo.list_entries(
                        project_id, statuses=("active",)
                    )
                    if r.pinned
                ]
                raise POKError(
                    "pok_pin_cap",
                    f"project already holds {pinned_count} pinned entries "
                    f"— the cap is {POK_PIN_CAP_PER_PROJECT} "
                    "(POK_PIN_CAP_PER_PROJECT, per project); unpin one "
                    f"of: {pinned_slugs}",
                )
        return self._repo.curate(
            row.id,
            pinned=pinned,
            disputed=disputed,
            archived=archived,
            audience=audience,
            audience_agents=(
                audience_agents if audience_agents is not None else UNSET
            ),
        )

    def _count_pinned(self, project_id: str) -> int:
        return sum(
            1
            for r in self._repo.list_entries(project_id, statuses=("active",))
            if r.pinned
        )

    # ── embeddings companion (lazy-mint hook) ────────────────────────────

    async def ensure_embedding(self, entry_id: str) -> bool:
        """Best-effort single-entry embedding mint (the write-path
        hook). ``False`` = lag (the entry reads BM25-only until the
        next mint attempt) — never raises (AC14 posture)."""
        row = self._repo.get(entry_id)
        if row is None:
            return False
        try:
            from daemon.services.critical_notes_embedding import (  # noqa: PLC0415
                embed_critical_note_text,
            )

            vector = await embed_critical_note_text(
                f"{row.domain} {row.body} {row.slug}"
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.info("[POK:Degraded] embed mint failed: %s", exc)
            return False
        if not vector:
            return False
        self._repo.upsert_embedding(entry_id, vector)
        return True

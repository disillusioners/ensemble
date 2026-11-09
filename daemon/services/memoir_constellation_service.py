"""Memoir Store v2 — the Constellation service (the cold-path graph layer).

Phase 3 of the Memoir Store v2 lane (plan:
``.agents/shared/planning/ari-memoir-store/v2-phase3-plan.md`` T3.1-T3.3;
decisions V2-D 3.1-3.9; flags V2-3, V2-4, V2-8, V2-13, V2-22, V2-25,
V2-27, V2-31, V2-34).

THE COLD-PATH CONTRACT (V2-D 3.1 / DD-20): constellation nodes and
edges are updated ONLY during background reflection passes — never on
the hot ``memoir_write`` / ``memoir_recall`` paths.  This service is
the single writer of that graph substrate; it composes the
:class:`MemoirConstellationRepository` (it never rebuilds the SQL) and
adds the entity-extraction + two-tier description + render-time heat
decay on top of it.

EXTRACTION (Suggestion #3 / V2-D 3.9):
* PRIMARY SEED — heuristic parsing of ``memoir_episodes.stars[]`` (no
  LLM).  Each star tag becomes one mention edge.
* FALLBACK — bounded LLM extraction ONLY when ``stars[]`` is empty.
  Capped at ``MEMOIR_CONSTELLATION_MAX_LLM_CALLS_PER_PASS`` calls per
  pass, decoupled from the reflection synthesis cap.

DETERMINISTIC CANONICALIZATION: the canonical identity is the casefolded
surface form (``strip().casefold()``); the FIRST occurrence's casing is
preserved as the display name; alternative surface forms are linked via
``memoir_star_alias`` so "the Emperor"/"my dear"/the real name resolve
to ONE star.

TWO-TIER DESCRIPTIONS (V2-D 3.2 / LightRAG M4):
* fragments = ``COUNT(*)`` of distinct ``memoir_line`` rows (NEVER
  ``SUM(mention_count)``);
* Tier 1 (small) — plain join of episode excerpts when fragments < 5
  or total text < 1200 chars;
* Tier 2 (grown) — LLM summarization (~300 words) when thresholds are
  exceeded AND the 24h cooldown (carried by
  ``memoir_star.description_as_of``) has elapsed.

TWO-PART HEAT (V2-D 3.3 / SF3): stored accumulator incremented ONLY
on explicit recall (``memoir_recall``, ``memoir_about``); recency decay
computed at RENDER time via ``MEMOIR_HEAT_DECAY_LAMBDA = 0.005``.
Background extraction NEVER increments heat (DD-12).
"""

from __future__ import annotations

import json
import logging
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Sequence

from sqlmodel import Session, select

from daemon.repositories.memoir.constellation_repository import (
    MemoirConstellationRepository,
)
from daemon.repositories.memoir.models import MemoirEpisode, MemoirLine, MemoirStar
from daemon.services.memoir_reflection_service import (
    MEMOIR_CONSTELLATION_MAX_LLM_CALLS_PER_PASS,
    MEMOIR_STAR_TYPES,
    ConstellationBudgetRequiredError,
    ConstellationCallBudget,
)
from daemon.services.timestamps import coerce_to_aware_utc, now_utc

logger = logging.getLogger(__name__)

#: Two-tier description thresholds (V2-D 3.2).
MEMOIR_STAR_SUMMARY_MIN_FRAGMENTS = 5
MEMOIR_STAR_SUMMARY_MIN_CHARS = 1200
MEMOIR_STAR_SUMMARY_COOLDOWN_HOURS = 24

#: Render-time heat decay (V2-D 3.3).
MEMOIR_HEAT_DECAY_LAMBDA = 0.005

#: Star-type vocabulary (re-exported for convenience).
MEMOIR_STAR_TYPES_LOCAL = MEMOIR_STAR_TYPES


@dataclass(frozen=True)
class ConstellationEdge:
    """ONE resolved constellation edge — NOT yet written.

    The extraction SPLITS into "resolve" (this dataclass) and "write"
    (:meth:`MemoirConstellationService.write_edges`) so the caller can
    write the edges inside ITS OWN transaction: the pass unit commits
    the constellation edges AND the per-episode reflection watermark
    together (the crash window of V2-29 / A2).
    """

    agent_id: str
    star_id: str
    episode_id: str
    event_at: datetime | None


@dataclass(frozen=True)
class DossierRender:
    """The rendered dossier + the fetch outcome (the heat gate's input).

    ``fetch_ok`` is TRUE only when the CONNECTED-EPISODE fetch
    SUCCEEDED.  A failed fetch renders the fail-soft ``(none)`` text but
    MUST NOT let the caller inflate the star's heat (V2-D 3.3 honesty):
    a lookup that could not read its episodes is NOT a hit.

    ``episode_count`` is the number of LIVE CONNECTED EPISODES actually
    rendered — 0 for an empty card, for a failed fetch AND for a card
    whose episode rows were DELETED (R3: a deleted dossier is not a
    hit).  The caller gates the heat increment on ``episode_count > 0``,
    so ONE condition covers the empty-card rule (ND-23), the
    failed-fetch rule and the deleted-episode rule.
    """

    text: str
    fetch_ok: bool
    episode_count: int


def _as_utc(value: datetime | None) -> datetime | None:
    coerced = coerce_to_aware_utc(value)
    if coerced is None:
        return None
    return coerced.astimezone(timezone.utc)


class MemoirConstellationService:
    """Composes the Constellation repository + extraction + descriptions."""

    # Class-level constants (also module-level) so tests can read either.
    MEMOIR_STAR_SUMMARY_MIN_FRAGMENTS = MEMOIR_STAR_SUMMARY_MIN_FRAGMENTS
    MEMOIR_STAR_SUMMARY_MIN_CHARS = MEMOIR_STAR_SUMMARY_MIN_CHARS
    MEMOIR_STAR_SUMMARY_COOLDOWN_HOURS = MEMOIR_STAR_SUMMARY_COOLDOWN_HOURS
    MEMOIR_HEAT_DECAY_LAMBDA = MEMOIR_HEAT_DECAY_LAMBDA
    MEMOIR_CONSTELLATION_MAX_LLM_CALLS_PER_PASS = (
        MEMOIR_CONSTELLATION_MAX_LLM_CALLS_PER_PASS
    )

    def __init__(
        self,
        constellation_repository: Any = None,
        *,
        engine: Any = None,
        llm_caller: Callable[[str], str] | None = None,
        clock: Callable[[], datetime] | None = None,
        agent_id: str = "ari",
    ) -> None:
        if constellation_repository is not None:
            self._repo = constellation_repository
        elif engine is not None:
            self._repo = MemoirConstellationRepository(engine)
        else:
            self._repo = None
        self._llm_caller = llm_caller
        self._clock = clock or now_utc
        self._agent_id = agent_id

    # ── repository access ────────────────────────────────────────────────

    @property
    def repository(self) -> Any:
        return self._repo

    @property
    def constellation_repository(self) -> Any:
        return self._repo

    # ── canonicalization ─────────────────────────────────────────────────

    def _find_star_by_casefold(self, agent_id: str, surface: str) -> Any:
        if self._repo is None:
            return None
        key = surface.strip().casefold()
        try:
            with Session(self._repo.engine) as session:
                rows = session.exec(
                    select(MemoirStar).where(MemoirStar.agent_id == agent_id)
                ).all()
                for row in rows:
                    if str(row.name).strip().casefold() == key:
                        return row
        except Exception:  # noqa: BLE001
            return None
        return None

    def _find_or_create_star(
        self,
        agent_id: str,
        surface_name: str,
        star_type: str,
        event_at: datetime | None,
    ) -> Any:
        if self._repo is None:
            return None
        surface = str(surface_name or "").strip()
        if not surface:
            return None
        if star_type not in MEMOIR_STAR_TYPES:
            star_type = "concept"
        existing = self._find_star_by_casefold(agent_id, surface)
        if existing is not None:
            if surface != existing.name:
                try:
                    self._repo.add_alias(agent_id, existing.id, surface)
                except Exception:  # noqa: BLE001
                    pass
            return existing
        star = self._repo.upsert_star(
            agent_id, surface, star_type, event_at=event_at
        )
        # Add the casefolded form as an alias when it differs.
        cf = surface.casefold()
        if cf and cf != surface:
            try:
                self._repo.add_alias(agent_id, star.id, cf)
            except Exception:  # noqa: BLE001
                pass
        return star

    # ── extraction ───────────────────────────────────────────────────────

    def extract_heuristic(self, episode: Any) -> list[tuple[str, str]]:
        """Heuristic (no LLM): parse ``episode.stars[]``.

        Returns a list of ``(name, star_type)`` tuples, star_type defaults
        to ``concept`` (the write path stores only names).
        """
        stars = getattr(episode, "stars", None) or []
        out: list[tuple[str, str]] = []
        for s in stars:
            name = str(s).strip()
            if name:
                out.append((name, "concept"))
        return out

    def _extraction_prompt(self, chunk: Sequence[Any]) -> str:
        lines = [
            "You extract ENTITY MENTIONS from a person's own memories.",
            "",
            "Rules:",
            "- For each episode, list the named entities it mentions.",
            "- star_type is one of: person, project, place, concept, domain.",
            "- Use the canonical surface form of the entity.",
            "- An episode with no named entity gets an empty list.",
            "",
            "Episodes:",
        ]
        for episode in chunk:
            lines.append(
                f"- id={getattr(episode, 'id', '')} body={getattr(episode, 'body', '') or ''}"
            )
        lines.extend(
            [
                "",
                "Answer with ONE JSON object and nothing else:",
                '{"episodes": ["<episode id>", ...], "mentions": '
                '[{"episode_id": "...", "name": "...", "star_type": "concept"}]}',
            ]
        )
        return "\n".join(lines)

    def _parse_envelope(self, raw: str, *, kind: str) -> list[dict] | None:
        text = (raw or "").strip()
        if not text:
            return None
        # find first JSON object
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end < start:
            # refusal?
            lowered = text.lower()
            if any(m in lowered for m in ("i cannot", "i can't", "unable", "sorry")):
                return []
            return None
        payload = text[start : end + 1]
        try:
            envelope = json.loads(payload)
        except json.JSONDecodeError:
            lowered = text.lower()
            if any(m in lowered for m in ("i cannot", "i can't", "unable", "sorry")):
                return []
            return None
        if not isinstance(envelope, dict):
            return None
        if kind == "mentions":
            mentions = envelope.get("mentions")
            if mentions is None:
                # maybe episodes present but no mentions key → empty
                if "episodes" in envelope:
                    return []
                return None
            if not isinstance(mentions, list):
                return None
            return [m for m in mentions if isinstance(m, dict)]
        return None

    def extract(
        self,
        chunk: Sequence[Any],
        *,
        pass_id: str | None = None,
        llm_caller: Callable[[str], str] | None = None,
        agent_id: str | None = None,
        now: datetime | None = None,
        budget: ConstellationCallBudget | None = None,
    ) -> int:
        """Extract star mentions from a chunk; return lines written.

        Heuristic primary (stars[]); LLM fallback when stars[] empty.

        THE BUDGET IS PER PASS (V2-D 3.9): the ``budget`` object is the
        pass's remaining LLM-call allowance and is threaded through
        EVERY chunk, so a multi-chunk pass can never exceed the cap.
        The heuristic seed is FREE — it never consumes the budget, and
        the budget never blocks it.

        THE LLM PATHS REQUIRE A BUDGET (V2-31): an unbudgeted LLM call
        is a cost-control bypass, so a ``None`` budget REFUSES the
        fallback extraction and the Tier-2 summarization (loudly, never
        silently).

        THE STANDALONE PATH: this method resolves the edges, writes them
        and runs the Tier-2 phase.  The reflection pass uses
        :meth:`collect_edges` + :meth:`write_edges` instead, so it can
        commit the edges together with the per-episode watermark and run
        the Tier-2 phase ONCE, after every chunk's extraction.
        """
        edges = self.collect_edges(
            chunk,
            llm_caller=llm_caller,
            agent_id=agent_id,
            now=now,
            budget=budget,
        )
        written = self.write_edges(edges, pass_id=pass_id)
        # THE TIER-2 PHASE (V2-D 3.2): at most ONE summarization per
        # pass, taken under the tier-2 purpose of the shared budget.
        self.summarize_qualifying(
            llm_caller=llm_caller, agent_id=agent_id, now=now, budget=budget
        )
        return written

    def collect_edges(
        self,
        chunk: Sequence[Any],
        *,
        llm_caller: Callable[[str], str] | None = None,
        agent_id: str | None = None,
        now: datetime | None = None,
        budget: ConstellationCallBudget | None = None,
    ) -> list[ConstellationEdge]:
        """Resolve the chunk's mentions into EDGES (no line row written).

        The canonical star upsert and the alias binding happen HERE (in
        their own transaction); the returned edges are written by
        :meth:`write_edges` — inside the caller's transaction when the
        caller owns one (the reflection pass unit).
        """
        if self._repo is None:
            return []
        agent = agent_id or self._agent_id
        caller = llm_caller if llm_caller is not None else self._llm_caller
        edges: list[ConstellationEdge] = []
        empty_episodes = []
        for ep in chunk:
            stars = getattr(ep, "stars", None) or []
            event_at = getattr(ep, "event_at", None) or getattr(ep, "created_at", None)
            if stars:
                for (name, stype) in self.extract_heuristic(ep):
                    star = self._find_or_create_star(agent, name, stype, event_at)
                    if star is None:
                        continue
                    edges.append(
                        ConstellationEdge(
                            agent_id=agent,
                            star_id=star.id,
                            episode_id=str(getattr(ep, "id", "") or ""),
                            event_at=event_at,
                        )
                    )
            else:
                empty_episodes.append(ep)

        # LLM fallback for episodes with empty stars[].
        # THE PER-PASS BUDGET (V2-D 3.9): the call is taken AT DISPATCH
        # and only when the budget still has room — the heuristic seed
        # above is untouched by the budget.  The budget is ALSO the
        # PER-PURPOSE cap (F-2): at most ONE extraction call per pass,
        # so a Tier-2-heavy pass can never starve the extraction.
        if empty_episodes and caller is not None:
            if budget is None:
                # V2-31: an unbudgeted LLM call is a cost-control
                # bypass — REFUSE it loudly instead of taking the call.
                raise ConstellationBudgetRequiredError(
                    "the constellation LLM extraction requires a per-pass "
                    "budget (budget=None is REFUSED — an unbudgeted LLM "
                    "call bypasses the V2-31 cost control)"
                )
            if budget.take("extraction"):
                try:
                    raw = caller(self._extraction_prompt(chunk))
                    mentions = self._parse_envelope(raw, kind="mentions")
                except Exception:  # noqa: BLE001
                    mentions = None
                if mentions:
                    chunk_ids = {getattr(ep, "id", "") for ep in chunk}
                    for m in mentions:
                        eid = str(m.get("episode_id") or "")
                        name = str(m.get("name") or "").strip()
                        if not eid or not name:
                            continue
                        if eid not in chunk_ids:
                            continue
                        ep = next((e for e in chunk if getattr(e, "id", "") == eid), None)
                        if ep is not None and (getattr(ep, "stars", None) or []):
                            continue  # already handled heuristically
                        st = str(m.get("star_type") or "concept").strip().lower()
                        if st not in MEMOIR_STAR_TYPES:
                            st = "concept"
                        event_at = getattr(ep, "event_at", None) if ep else None
                        if event_at is None and ep is not None:
                            event_at = getattr(ep, "created_at", None)
                        star = self._find_or_create_star(agent, name, st, event_at)
                        if star is None:
                            continue
                        edges.append(
                            ConstellationEdge(
                                agent_id=agent,
                                star_id=star.id,
                                episode_id=eid,
                                event_at=event_at,
                            )
                        )
        return edges

    def write_edges(
        self,
        edges: Sequence[ConstellationEdge],
        *,
        pass_id: str | None = None,
        session: Any = None,
    ) -> int:
        """Write the resolved edges as ``memoir_line`` rows.

        THE ``last_pass_id`` GUARD (V2-29 / SF2) lives in the
        repository: a re-run of the SAME pass id is a no-op, a distinct
        pass id increments ``mention_count``.

        When ``session`` is given, the CALLER owns the transaction: the
        edges ride that session and are NOT committed here (the pass
        unit commits the edges together with the per-episode watermark).
        """
        if self._repo is None or not edges:
            return 0
        stamp = pass_id
        if stamp is None:
            # The PRODUCTION site (the reflection pass) mints the DURABLE
            # pass id; this fallback is for the standalone callers only.
            stamp = f"reflect-{uuid.uuid4().hex[:12]}"
        written = 0
        for edge in edges:
            if not edge.episode_id or not edge.star_id:
                continue
            try:
                self._repo.record_line(
                    edge.agent_id,
                    edge.star_id,
                    edge.episode_id,
                    event_at=edge.event_at,
                    pass_id=stamp,
                    session=session,
                )
                written += 1
            except Exception:  # noqa: BLE001
                pass
        return written

    def summarize_qualifying(
        self,
        *,
        llm_caller: Callable[[str], str] | None = None,
        agent_id: str | None = None,
        now: datetime | None = None,
        budget: ConstellationCallBudget | None = None,
    ) -> bool:
        """THE TIER-2 PHASE (V2-D 3.2 / 3.9): ONE summarization per pass.

        THE TRUE INVARIANT (F-2): the shared per-pass pool holds TWO
        calls, split into at most ONE extraction call and at most ONE
        Tier-2 summarization (the per-purpose caps).  The Tier-2 call is
        taken AT DISPATCH and only for a star that QUALIFIES (the
        threshold + the 24h cooldown), so a non-qualifying star never
        burns the budget.

        THE ORDERING (F-2): the reflection pass runs this phase AFTER
        every chunk's extraction, so a Tier-2-qualified star can never
        starve the extraction of its budget unit.

        Returns:
            ``True`` when a summarization was dispatched.
        """
        if self._repo is None:
            return False
        agent = agent_id or self._agent_id
        caller = llm_caller if llm_caller is not None else self._llm_caller
        if caller is None:
            return False
        if budget is None:
            raise ConstellationBudgetRequiredError(
                "the constellation Tier-2 summarization requires a per-pass "
                "budget (budget=None is REFUSED — an unbudgeted LLM call "
                "bypasses the V2-31 cost control)"
            )
        moment = now or self._clock()
        # Collect candidate stars from the chunk's lines.
        try:
            with Session(self._repo.engine) as session:
                star_ids = session.exec(
                    select(MemoirLine.star_id)
                    .where(MemoirLine.agent_id == agent)
                    .distinct()
                ).all()
        except Exception:  # noqa: BLE001
            return False
        # Pick first qualifying star.
        for sid in star_ids:
            star = None
            try:
                with Session(self._repo.engine) as session:
                    star = session.get(MemoirStar, sid)
            except Exception:  # noqa: BLE001
                star = None
            if star is None or star.agent_id != agent:
                continue
            if not self.needs_tier2(star, now=moment):
                continue
            # THE PER-PASS BUDGET (V2-D 3.9) + THE PER-PURPOSE CAP (F-2):
            # ONE Tier-2 call per pass, taken AT DISPATCH.
            if not budget.take("tier2"):
                return False
            # summarize
            try:
                self.summarize_star(star.id, agent_id=agent, llm_caller=caller, now=moment)
            except Exception:  # noqa: BLE001
                pass
            return True
        return False

    # ── two-tier description ─────────────────────────────────────────────

    def fragments_count(self, star_id: str, agent_id: str | None = None) -> int:
        agent = agent_id or self._agent_id
        if self._repo is None:
            return 0
        try:
            with Session(self._repo.engine) as session:
                rows = session.exec(
                    select(MemoirLine).where(
                        MemoirLine.star_id == star_id,
                        MemoirLine.agent_id == agent,
                    )
                ).all()
                return len(rows)
        except Exception:  # noqa: BLE001
            return 0

    def total_chars_for_star(self, star_id: str, agent_id: str | None = None) -> int:
        agent = agent_id or self._agent_id
        if self._repo is None:
            return 0
        try:
            with Session(self._repo.engine) as session:
                lines = session.exec(
                    select(MemoirLine).where(
                        MemoirLine.star_id == star_id,
                        MemoirLine.agent_id == agent,
                    )
                ).all()
                total = 0
                for line in lines:
                    ep = session.get(MemoirEpisode, line.episode_id)
                    if ep is not None and getattr(ep, "body", None):
                        total += len(ep.body)
                return total
        except Exception:  # noqa: BLE001
            return 0

    def needs_tier2(self, star: Any, *, now: datetime | None = None) -> bool:
        now = now or self._clock()
        now = _as_utc(now) or now
        fragments = self.fragments_count(star.id, getattr(star, "agent_id", None))
        if fragments < self.MEMOIR_STAR_SUMMARY_MIN_FRAGMENTS:
            return False
        chars = self.total_chars_for_star(star.id, getattr(star, "agent_id", None))
        if chars < self.MEMOIR_STAR_SUMMARY_MIN_CHARS:
            return False
        as_of = getattr(star, "description_as_of", None)
        if as_of is not None:
            as_of_utc = _as_utc(as_of)
            if as_of_utc is not None:
                cooldown = timedelta(hours=self.MEMOIR_STAR_SUMMARY_COOLDOWN_HOURS)
                if (now - as_of_utc) < cooldown:
                    return False
        return True

    def summarize_star(
        self,
        star_id: str,
        *,
        agent_id: str | None = None,
        llm_caller: Callable[[str], str] | None = None,
        now: datetime | None = None,
        pass_id: str | None = None,
    ) -> bool:
        agent = agent_id or self._agent_id
        caller = llm_caller if llm_caller is not None else self._llm_caller
        if self._repo is None or caller is None:
            return False
        now = now or self._clock()
        # fetch star
        star = None
        try:
            with Session(self._repo.engine) as session:
                star = session.get(MemoirStar, star_id)
                if star is not None:
                    session.expunge(star)
        except Exception:  # noqa: BLE001
            star = None
        if star is None or star.agent_id != agent:
            return False
        if not self.needs_tier2(star, now=now):
            return False
        # build prompt from linked episodes
        try:
            with Session(self._repo.engine) as session:
                lines = session.exec(
                    select(MemoirLine).where(
                        MemoirLine.star_id == star_id,
                        MemoirLine.agent_id == agent,
                    )
                ).all()
                excerpts = []
                for line in lines:
                    ep = session.get(MemoirEpisode, line.episode_id)
                    if ep is not None and getattr(ep, "body", None):
                        excerpts.append(ep.body)
        except Exception:  # noqa: BLE001
            excerpts = []
        prompt = (
            "Summarize the following memory excerpts about the entity "
            f"{star.name!r} into a coherent ~300 word card.\n\n"
            + "\n---\n".join(excerpts)
        )
        try:
            summary = caller(prompt)
        except Exception:  # noqa: BLE001
            return False
        summary = (summary or "").strip()
        if not summary:
            return False
        try:
            ok = self._repo.update_star_description(
                agent, star_id, summary, as_of_ts=now
            )
            return bool(ok)
        except Exception:  # noqa: BLE001
            return False

    # ── heat decay ───────────────────────────────────────────────────────

    @staticmethod
    def effective_heat(
        stored_heat: float,
        last_touched_at: datetime | None,
        now: datetime | None = None,
    ) -> float:
        now = now or now_utc()
        now = _as_utc(now) or now
        last = _as_utc(last_touched_at)
        if last is None:
            days_idle = 0.0
        else:
            days_idle = (now - last).total_seconds() / 86400.0
            if days_idle < 0:
                days_idle = 0.0
        try:
            return round(float(stored_heat) * math.exp(-MEMOIR_HEAT_DECAY_LAMBDA * days_idle), 2)
        except (ValueError, OverflowError):
            return round(float(stored_heat), 2)

    # ── dossier rendering ────────────────────────────────────────────────

    def _episode_lines(
        self, lines: Sequence[dict[str, Any]]
    ) -> tuple[list[str], int, bool]:
        """The CONNECTED-EPISODES block, its count, and the fetch verdict.

        THE FETCH IS ONE UNIT (the honesty guard): a failure anywhere
        discards the block and reports ``fetch_ok = False``, so the
        caller renders the fail-soft ``(none)`` text and does NOT inflate
        the star's heat.  A per-line swallow would make a partial failure
        indistinguishable from a success — the heat gate could not tell
        them apart, so the fetch is deliberately all-or-nothing.

        An EMPTY card never fetches (there is nothing to read) and is
        NOT a failure — it is the ND-23 "empty lookup" case.
        """
        if not lines:
            return ["- (none)"], 0, True
        if self._repo is None:
            # NO REPOSITORY ⇒ the episodes could NOT be read ⇒ not a hit.
            return ["- (none)"], 0, False
        episode_lines: list[str] = []
        try:
            with Session(self._repo.engine) as session:
                for line in lines[:10]:
                    eid = line.get("episode_id")
                    when = line.get("last_mentioned_at") or line.get(
                        "first_mentioned_at"
                    )
                    title = "(untitled)"
                    ep = session.get(MemoirEpisode, eid)
                    if ep is None:
                        # THE EPISODE ROW IS GONE (deleted): the line is a
                        # DANGLING edge — the dossier must not render it
                        # and the heat must not accrue for it (R3: a
                        # deleted dossier is not a hit).
                        continue
                    title = getattr(ep, "title", None) or "(untitled)"
                    if getattr(ep, "event_at", None) is not None:
                        when = ep.event_at
                    date_str = ""
                    if when is not None:
                        try:
                            date_str = coerce_to_aware_utc(when).date().isoformat()
                        except Exception:  # noqa: BLE001
                            date_str = str(when)
                    episode_lines.append(f"- [{date_str}] {title}")
        except Exception:  # noqa: BLE001 — the fail-soft fetch
            logger.warning(
                "[MemoirConstellation] the connected-episode fetch failed — "
                "the dossier renders the fail-soft text and the heat is NOT "
                "incremented"
            )
            return ["- (none)"], 0, False
        if not episode_lines:
            # The card HAS lines, but NOT ONE of them points at a live
            # episode (they were all deleted) — the dossier is empty and
            # the heat MUST NOT move (R3).
            return ["- (none)"], 0, True
        return episode_lines, len(episode_lines), True

    def render_dossier(
        self,
        star: Any,
        card: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> DossierRender:
        """Render the star dossier — THE ONE FORMATTER.

        The ``memoir_about`` tool delegates here instead of keeping a
        second inline renderer (two formatters drift).  The fetch
        outcome travels WITH the text so the caller can gate the heat
        increment honestly (V2-D 3.3).
        """
        now = now or self._clock()
        aliases = [
            a.get("alias")
            for a in (card.get("aliases") or [])
            if a.get("alias")
        ]
        lines = card.get("lines", []) or []
        neighbors = card.get("neighbors", []) or []
        effective = self.effective_heat(
            getattr(star, "heat", 0.0), getattr(star, "last_touched_at", None), now=now
        )
        # connected episodes: fetch the titles + the fetch verdict.
        episode_lines, episode_count, fetch_ok = self._episode_lines(lines)
        neighbor_lines: list[str] = []
        for n in neighbors[:10]:
            name = n.get("name")
            ntype = n.get("star_type", "concept")
            # shared episodes count: not stored; approximate via? skip count
            neighbor_lines.append(f"- {name} ({ntype})")
        if not neighbor_lines:
            neighbor_lines = ["- (none)"]

        description = getattr(star, "description", "") or "(none)"
        first_seen = getattr(star, "first_seen_at", None)
        last_touched = getattr(star, "last_touched_at", None)
        summary_as_of = getattr(star, "description_as_of", None)
        aliases_text = ", ".join(aliases) if aliases else "(none)"
        first_str = first_seen.date().isoformat() if hasattr(first_seen, "date") else str(first_seen)
        last_str = last_touched.date().isoformat() if hasattr(last_touched, "date") else str(last_touched)
        summary_str = summary_as_of.isoformat() if hasattr(summary_as_of, "isoformat") else str(summary_as_of) if summary_as_of else "(none)"
        name = getattr(star, "name", "")
        stype = getattr(star, "star_type", "concept")
        dossier = (
            f"=== STAR: {name} (type: {stype}, heat: {effective}) ===\n"
            f"Aliases: {aliases_text}\n"
            f"First Seen: {first_str} | Last Touched: {last_str} | Summary As Of: {summary_str}\n"
            f"\n"
            f"Description:\n{description}\n"
            f"\n"
            f"Connected Episodes (recent):\n" + "\n".join(episode_lines) + "\n"
            f"\n"
            f"Neighbor Stars (1-hop):\n" + "\n".join(neighbor_lines)
        )
        return DossierRender(
            text=dossier,
            fetch_ok=fetch_ok,
            episode_count=episode_count,
        )

    # ── the seeding helper ──────────────────────────────────────────────
    #
    # THE DEAD ALIASES ARE GONE (R1): ``seed_from_episodes`` /
    # ``seed`` / ``seed_pass`` were callable-looking entry points to the
    # extraction — a bypassable path around the mandated per-pass cost
    # control (V2-31) and around the durable pass id (F-3).  The
    # historical seed is the NIGHTLY PASS itself (it walks the
    # pre-cutover backlog oldest-first under the nightly cap), so there
    # is no separate seed API to call.


__all__ = [
    "MEMOIR_STAR_SUMMARY_MIN_FRAGMENTS",
    "MEMOIR_STAR_SUMMARY_MIN_CHARS",
    "MEMOIR_STAR_SUMMARY_COOLDOWN_HOURS",
    "MEMOIR_HEAT_DECAY_LAMBDA",
    "MEMOIR_CONSTELLATION_MAX_LLM_CALLS_PER_PASS",
    "ConstellationEdge",
    "DossierRender",
    "MemoirConstellationService",
]


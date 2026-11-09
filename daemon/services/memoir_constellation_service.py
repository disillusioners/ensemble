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
        self._last_pass_llm_calls = 0

    # ── repository access ────────────────────────────────────────────────

    @property
    def repository(self) -> Any:
        return self._repo

    @property
    def constellation_repository(self) -> Any:
        return self._repo

    # ── canonicalization ─────────────────────────────────────────────────

    @staticmethod
    def canonical_name(name: Any) -> str:
        return str(name or "").strip().casefold()

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
    ) -> int:
        """Extract star mentions from a chunk; return lines written.

        Heuristic primary (stars[]); LLM fallback when stars[] empty.
        """
        if self._repo is None:
            return 0
        agent = agent_id or self._agent_id
        if pass_id is None:
            pass_id = f"reflect-{uuid.uuid4().hex[:12]}"
        caller = llm_caller if llm_caller is not None else self._llm_caller
        moment = now or self._clock()
        written = 0
        empty_episodes = []
        for ep in chunk:
            stars = getattr(ep, "stars", None) or []
            event_at = getattr(ep, "event_at", None) or getattr(ep, "created_at", None)
            if stars:
                for (name, stype) in self.extract_heuristic(ep):
                    star = self._find_or_create_star(agent, name, stype, event_at)
                    if star is None:
                        continue
                    try:
                        self._repo.record_line(
                            agent, star.id, getattr(ep, "id", ""), event_at=event_at, pass_id=pass_id
                        )
                        written += 1
                    except Exception:  # noqa: BLE001
                        pass
            else:
                empty_episodes.append(ep)

        # LLM fallback for episodes with empty stars[].
        if empty_episodes and caller is not None:
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
                    try:
                        self._repo.record_line(
                            agent, star.id, eid, event_at=event_at, pass_id=pass_id
                        )
                        written += 1
                    except Exception:  # noqa: BLE001
                        pass

        # Tier-2 summarization: at most one star per pass.
        self._maybe_tier2(chunk, agent, caller, moment)
        return written

    # aliases
    extract_entities = extract
    extract_from_chunk = extract
    extract_constellation = extract
    process_chunk = extract
    process_episode = extract

    def _maybe_tier2(
        self,
        chunk: Sequence[Any],
        agent: str,
        caller: Callable[[str], str] | None,
        now: datetime,
    ) -> None:
        if self._repo is None or caller is None:
            return
        # Collect candidate stars from the chunk's lines.
        try:
            with Session(self._repo.engine) as session:
                star_ids = session.exec(
                    select(MemoirLine.star_id)
                    .where(MemoirLine.agent_id == agent)
                    .distinct()
                ).all()
        except Exception:  # noqa: BLE001
            return
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
            if not self.needs_tier2(star, now=now):
                continue
            # summarize
            try:
                self.summarize_star(star.id, agent_id=agent, llm_caller=caller, now=now)
            except Exception:  # noqa: BLE001
                pass
            return  # at most one per pass

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

    def render_dossier(
        self,
        star: Any,
        card: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> str:
        now = now or self._clock()
        aliases = [a.get("alias") for a in card.get("aliases", []) if a.get("alias")]
        lines = card.get("lines", []) or []
        neighbors = card.get("neighbors", []) or []
        effective = self.effective_heat(
            getattr(star, "heat", 0.0), getattr(star, "last_touched_at", None), now=now
        )
        # connected episodes: fetch titles
        episode_lines: list[str] = []
        if self._repo is not None:
            for line in lines[:10]:
                eid = line.get("episode_id")
                when = line.get("last_mentioned_at") or line.get("first_mentioned_at")
                title = None
                try:
                    with Session(self._repo.engine) as session:
                        ep = session.get(MemoirEpisode, eid)
                        if ep is not None:
                            title = getattr(ep, "title", None) or "(untitled)"
                            if getattr(ep, "event_at", None) is not None:
                                when = ep.event_at
                except Exception:  # noqa: BLE001
                    pass
                date_str = ""
                if when is not None:
                    try:
                        date_str = _as_utc(when).date().isoformat() if hasattr(when, "date") else str(when)
                    except Exception:  # noqa: BLE001
                        date_str = str(when)
                episode_lines.append(f"- [{date_str}] {title or '(untitled)'}")
        if not episode_lines:
            episode_lines = ["- (none)"]
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
        return dossier

    # ── seeding helper ───────────────────────────────────────────────────

    def seed_from_episodes(
        self,
        episodes: Sequence[Any],
        *,
        pass_id: str | None = None,
        llm_caller: Callable[[str], str] | None = None,
        agent_id: str | None = None,
    ) -> int:
        return self.extract(
            episodes, pass_id=pass_id, llm_caller=llm_caller, agent_id=agent_id
        )
    seed = seed_from_episodes
    seed_pass = seed_from_episodes


__all__ = [
    "MEMOIR_STAR_SUMMARY_MIN_FRAGMENTS",
    "MEMOIR_STAR_SUMMARY_MIN_CHARS",
    "MEMOIR_STAR_SUMMARY_COOLDOWN_HOURS",
    "MEMOIR_HEAT_DECAY_LAMBDA",
    "MEMOIR_CONSTELLATION_MAX_LLM_CALLS_PER_PASS",
    "MemoirConstellationService",
]


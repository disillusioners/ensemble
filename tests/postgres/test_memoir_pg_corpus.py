"""The Memoir Store v1 real-PG CORPUS — G2 gate worker 2.

Gate G2 (dispatch: the AUTHORITATIVE real-PG corpus, D7.6 items not
already covered by the two registered PG packs). The registered packs
(``test_memoir_schema_pg.py`` / ``test_memoir_retrieval_pg.py``) cover
the schema/lexical/JSONB/stars/CHECK/partial-index surface; THIS module
proves the BEHAVIOR surface on real PostgreSQL 16.15:

* keepsake pin ADMISSION against the 4,000-token zone (D2.12) — refused
  over, admitted under, nothing evicted;
* the DR-1 render-time ZONE ARITHMETIC on real PG rows — the 24,000-char
  ceiling (stable in full, dynamic dropped, overage disclosed), the
  48,000-char absolute (withheld entirely), and the nominal three-zone
  block (keepsakes in the stable zone ONLY — never double-rendered);
* TWO-AXIS TIME (DD-7): time-travel windows filter ``event_at`` while
  recency/latest run on the write axis (``created_at``/``last_accessed_at``)
  — "the past written late is not stale";
* the persona SEED path (byte-identical day-one charter, idempotent) and
  the HUMAN-LANE GUARD (``approve``/``reject`` refuse non-session actors
  with the house ``HumanLaneRequiredError``) on real PG rows;
* RRF FUSION of the BM25 (tsvector) ∥ cosine lanes with the RECENCY
  PRIOR applied and the KEEPSAKE EXEMPTION (multiplier 1.0, never decayed);
* ACCESS STATS on EXPLICIT recall ONLY: ``memoir_recall`` bumps
  ``access_count``/``last_accessed_at`` for exactly the shipped ids, the
  implicit wake-briefing read path bumps NOTHING.

PG-only lane: run with ``--override-ini="addopts=" -m postgres`` against
a sanctioned scratch database (the pack wrapper enforces the fence).
"""

from __future__ import annotations

import ast
import asyncio
import pathlib
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlmodel import Session, select

# Registered at COLLECTION time so the session-scoped create_all builds
# every table the per-test truncate names: the briefing path lazily
# imports chains that register extra model families mid-run, and a
# mid-run registration names a table create_all never built.
# Registered at COLLECTION time so the session-scoped create_all builds
# every table the per-test truncate names. The corpus exercises the
# briefing path, whose lazy import chain (context_messages →
# skill_metrics_service → critical-notes selection) registers additional
# model families MID-RUN — a mid-run registration names a table
# create_all never built and the truncate fixture reds. Pre-importing
# the chains at module level makes registration deterministic.
import daemon.services.context_messages  # noqa: F401 — see note above
import daemon.services.prompt_version_service  # noqa: F401 — see note above
import daemon.services.critical_notes_selector  # noqa: F401 — see note above
import daemon.tools.provenance  # noqa: F401 — registers the snapshot family
from daemon.repositories.memoir.models import MemoirEpisode, MemoirPersonaVersion
from daemon.repositories.memoir.repository import (
    MemoirAdmissionError,
    MemoirPersonaRepository,
    MemoirRepository,
)
from daemon.repositories.memoir.seed import (
    MEMOIR_PERSONA_SEED,
    seed_persona_if_absent,
)
from daemon.services.memoir_briefing import (
    MEMOIR_AFFORDANCE_LINE,
    MEMOIR_SECTION_CHAR_ABSOLUTE,
    MEMOIR_SECTION_CHAR_CEILING,
    MEMOIR_STABLE_OVERAGE_DISCLOSURE,
    MEMOIR_WITHHOLD_WARNING,
    render_briefing_body,
    _build_memoir_briefing_message,
)
from daemon.services.memoir_retrieval import (
    MEMOIR_RECALL_BODY_TOKEN_CLAMP,
    MEMOIR_RECALL_DEFAULT_K,
    MEMOIR_RANK_CANDIDATES,
    MemoirRetrievalService,
    fuse_rankings,
    recency_multiplier,
)

pytestmark = pytest.mark.postgres

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MANAGER_PY = REPO_ROOT / "daemon" / "manager.py"

_WORD = "quiescence "
assert len(_WORD) == 11  # the char arithmetic below depends on it


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── the shipped ensure statements (extracted, never copied) ───────────────


def _shipped_memoir_ensure_statements() -> list[str]:
    """The memoir entries of the shared ensure list, read from the source."""
    tree = ast.parse(MANAGER_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name != "_ensure_postgres_columns":
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Assign):
                continue
            targets = [t.id for t in inner.targets if isinstance(t, ast.Name)]
            if "statements" not in targets or not isinstance(inner.value, ast.List):
                continue
            memoir = [
                element.value
                for element in inner.value.elts
                if isinstance(element, ast.Constant)
                and isinstance(element.value, str)
                and "memoir_episodes" in element.value
            ]
            assert len(memoir) == 2, memoir
            return memoir
    raise AssertionError("the shared ensure statement list was not found")


@pytest.fixture(scope="module")
def memoir_tsv(pg_engine):
    """Apply the shipped ``tsv`` ensure (idempotent; the column is in NO model)."""
    for statement in _shipped_memoir_ensure_statements():
        with pg_engine.begin() as conn:
            conn.execute(text(statement))
    return True


@pytest.fixture()
def repo(pg_engine):
    return MemoirRepository(pg_engine)


@pytest.fixture()
def persona_repo(pg_engine):
    return MemoirPersonaRepository(pg_engine)


class _HumanActor:
    """The house lane discriminator: ``auth_method == "session"``."""

    def __init__(self, auth_method: str | None) -> None:
        self.auth_method = auth_method


def _approve_persona(persona_repo, agent_id: str, body: str) -> MemoirPersonaVersion:
    """The write-time path a human session takes: draft → submit → approve."""
    draft = persona_repo.create_draft(agent_id, body, source="ari")
    persona_repo.submit(draft.id)
    return persona_repo.approve(draft.id, _HumanActor("session"))


def _insert_row(
    repo,
    episode_id: str,
    *,
    body: str,
    created_at: datetime,
    event_at: datetime | None = None,
    title: str | None = None,
) -> str:
    with Session(repo.engine) as session:
        session.add(
            MemoirEpisode(
                id=episode_id,
                agent_id="ari",
                title=title,
                body=body,
                created_at=created_at,
                event_at=event_at or created_at,
            )
        )
        session.commit()
    return episode_id


def _stats(repo) -> dict[str, tuple[int, object]]:
    with Session(repo.engine) as session:
        rows = session.exec(select(MemoirEpisode)).all()
        return {row.id: (int(row.access_count or 0), row.last_accessed_at) for row in rows}


# ══ ITEM 5a — keepsake pin ADMISSION against the 4,000-token zone ════════


def test_keepsake_pin_admission_respects_the_zone_cap_on_pg(repo):
    """D2.12: admission refuses a pin that would push the zone over
    4,000 tokens, admits pins under it, and never evicts anything."""
    first = repo.insert_episode(
        agent_id="ari",
        body="the preserved first day",
        title="First Day",
        episode_id="g2-pin-1",
    )
    # ~1,500 tokens of reason → the rendered line lands well under 4,000.
    repo.set_pin(first, actor="emperor", reason="kept " * 1500)
    zone_after_one = repo.keepsake_zone_tokens("ari")
    assert 0 < zone_after_one < 4000

    second = repo.insert_episode(agent_id="ari", body="second pin", episode_id="g2-pin-2")
    repo.set_pin(second, actor="emperor", reason="kept " * 1500)
    zone_after_two = repo.keepsake_zone_tokens("ari")
    assert zone_after_two < 4000

    # A third ~1,500-token pin would push the zone OVER the budget.
    third = repo.insert_episode(agent_id="ari", body="third pin", episode_id="g2-pin-3")
    with pytest.raises(MemoirAdmissionError) as excinfo:
        repo.set_pin(third, actor="emperor", reason="kept " * 1500)
    assert excinfo.value.code == "keepsake_zone_tokens"
    assert "4000-token zone" in str(excinfo.value)

    # NOTHING is evicted: the zone is unchanged and the refused row is
    # NOT a keepsake.
    assert repo.keepsake_zone_tokens("ari") == zone_after_two
    with Session(repo.engine) as session:
        refused = session.exec(
            select(MemoirEpisode).where(MemoirEpisode.id == third)
        ).first()
        assert refused is not None
        assert refused.is_keepsake is False


# ══ ITEM 5b — DR-1 first rung: >24,000 chars ⇒ stable in full, ═══════════
# ══            dynamic dropped, overage disclosed                        ══


def test_dr1_overage_disclosure_drops_the_dynamic_zone_on_pg(
    repo, persona_repo, memoir_tsv, caplog
):
    """The stable zone is NEVER truncated: above the 24,000-char ceiling
    it ships IN FULL, the dynamic zone is dropped, and the overage is
    disclosed — the DR-1 reconciliation rendered on real PG rows."""
    # 2,300 words × 11 chars ≈ 25,300 chars + header — above the ceiling,
    # below the absolute bound.
    body = _WORD * 2300
    assert len(body) > MEMOIR_SECTION_CHAR_CEILING
    assert len(body) < MEMOIR_SECTION_CHAR_ABSOLUTE
    _approve_persona(persona_repo, "ari", body)

    keepsake = repo.insert_episode(
        agent_id="ari",
        body="a pinned memory",
        episode_id="g2-zone-keep",
        is_keepsake=True,
        pinned_by="emperor",
        pinned_reason="preserved",
    )
    for index in range(3):
        repo.insert_episode(
            agent_id="ari", body=f"recent episode {index}", episode_id=f"g2-zone-dyn-{index}"
        )

    persona = repo.get_approved_persona("ari")
    assert persona is not None
    keepsakes = repo.fetch_keepsakes("ari")
    episodes = repo.fetch_latest("ari", 5)
    with caplog.at_level("WARNING"):
        rendered = render_briefing_body(
            persona_body=persona.body,
            keepsakes=keepsakes,
            episodes=episodes,
            provenance_class="direct",
        )
    assert rendered is not None
    # Stable in FULL — the persona body's tail is present verbatim.
    assert body[-200:] in rendered
    # The dynamic zone is DROPPED entirely.
    assert "## Recent episodes" not in rendered
    assert "recent episode 0" not in rendered
    # The keepsake still ships in the stable zone (its line, not its body).
    assert "## Keepsakes" in rendered
    assert "[keepsake]" in rendered
    assert "pinned_by=emperor" in rendered
    assert "preserved" in rendered
    # The overage disclosure names the measured size.
    stable_chars = len(
        rendered.split(MEMOIR_STABLE_OVERAGE_DISCLOSURE.split("{")[0])[0].rstrip("\n")
    )
    disclosure = MEMOIR_STABLE_OVERAGE_DISCLOSURE.format(
        chars=stable_chars, over=stable_chars - MEMOIR_SECTION_CHAR_CEILING
    )
    assert disclosure in rendered
    # The affordance line still closes the block.
    assert MEMOIR_AFFORDANCE_LINE in rendered


# ══ ITEM 5c — DR-1 second rung: >48,000 chars ⇒ WITHHELD ═════════════════


def test_dr1_withhold_above_the_absolute_bound_on_pg(repo, persona_repo, caplog):
    """Above the 48,000-char absolute bound the block is WITHHELD
    (render returns ``None``) and the withhold WARNING is logged."""
    body = _WORD * 5100  # ≈ 56,100 chars + header
    assert len(body) > MEMOIR_SECTION_CHAR_ABSOLUTE
    _approve_persona(persona_repo, "ari", body)
    persona = repo.get_approved_persona("ari")

    with caplog.at_level("WARNING"):
        withheld = render_briefing_body(
            persona_body=persona.body,
            keepsakes=[],
            episodes=[],
            provenance_class="direct",
        )
    assert withheld is None
    assert any(
        MEMOIR_WITHHOLD_WARNING.split("{")[0].rstrip() in record.getMessage()
        for record in caplog.records
    )

    # CONTROL: the SAME renderer with a SMALL persona ships a block —
    # the withhold is caused by the size, not by the renderer.
    _approve_persona(persona_repo, "ari", "small persona body")
    small = repo.get_approved_persona("ari")
    control = render_briefing_body(
        persona_body=small.body, keepsakes=[], episodes=[], provenance_class=""
    )
    assert control is not None


# ══ ITEM 5d — nominal block: keepsakes render in the STABLE zone only ════


def test_dr1_nominal_block_keepsakes_never_double_rendered_on_pg(repo, persona_repo):
    """Under the ceilings all three zones ship; a keepsake is rendered in
    the stable zone and EXCLUDED from the dynamic zone (never paid twice)."""
    _approve_persona(persona_repo, "ari", "# Who We Are (v2)\nthe durable self")
    repo.insert_episode(
        agent_id="ari",
        body="the pinned first day",
        title="First Day",
        episode_id="g2-nom-keep",
        is_keepsake=True,
        pinned_by="emperor",
        pinned_reason="preserved",
    )
    for index in range(3):
        repo.insert_episode(
            agent_id="ari",
            body=f"a recent episode {index}",
            episode_id=f"g2-nom-dyn-{index}",
        )
    persona = repo.get_approved_persona("ari")
    rendered = render_briefing_body(
        persona_body=persona.body,
        keepsakes=repo.fetch_keepsakes("ari"),
        episodes=repo.fetch_latest("ari", 5),
        provenance_class="direct",
    )
    assert rendered is not None
    assert len(rendered) <= MEMOIR_SECTION_CHAR_CEILING
    assert "## Persona" in rendered
    assert "## Keepsakes" in rendered
    assert "## Recent episodes" in rendered
    # The keepsake line renders in the stable zone (title + pin claim).
    assert "First Day" in rendered
    assert "pinned_by=emperor" in rendered
    assert rendered.count("[keepsake]") == 1
    assert "preserved" in rendered
    # The keepsake's BODY never renders (only its line does) and the
    # dynamic lines carry the plain episodes.
    assert rendered.count("the pinned first day") == 0
    assert "a recent episode 0" in rendered


# ══ ITEM 6 — TWO-AXIS TIME (DD-7) ════════════════════════════════════════


def test_two_axis_time_travel_window_filters_event_at_not_created_at_on_pg(repo):
    """Axis 1: the time-travel window filters ``event_at`` — a row written
    NOW about the past is returned by a historical window; ``fetch_latest``
    still surfaces it as latest (the write axis)."""
    historical = datetime(2026, 3, 1, tzinfo=timezone.utc)
    now = _now()
    late_write = _insert_row(
        repo, "g2-axis-late", body="written long after it happened",
        created_at=now, event_at=historical,
    )
    fresh = _insert_row(
        repo, "g2-axis-fresh", body="happening right now",
        created_at=now, event_at=now,
    )
    # Round-trip: the two axes are stored INDEPENDENTLY.
    with Session(repo.engine) as session:
        row = session.exec(
            select(MemoirEpisode).where(MemoirEpisode.id == late_write)
        ).first()
        assert row.event_at == historical
        assert (now - row.created_at.replace(tzinfo=timezone.utc)).total_seconds() < 60

    # The window brackets the HISTORICAL event, not the write time.
    rows = repo.timeline_window(
        "ari", since=historical - timedelta(days=1), until=historical + timedelta(days=1)
    )
    ids = {r.id for r in rows}
    assert late_write in ids
    assert fresh not in ids

    # The write axis still calls both rows "latest".
    latest = {r.id for r in repo.fetch_latest("ari", 5)}
    assert late_write in latest and fresh in latest


def test_two_axis_recency_prior_uses_the_write_axis_on_pg(repo, memoir_tsv):
    """Axis 2: the recency prior's idle basis is the WRITE axis
    (``last_accessed_at`` else ``created_at``), NOT ``event_at`` — a
    freshly-written row about a year-old event is NOT decayed, while a
    row actually written a year ago IS.

    Discriminator design: identical bodies ⇒ identical lexical scores;
    the ids invert the ASCII tie-break so the WRONG hypothesis (prior on
    ``event_at``) would order the rows the other way."""
    now = _now()
    year_ago = now - timedelta(days=365)
    # Same body ⇒ same ts_rank_cd score for both rows. The ids invert the
    # ASCII tie-break: under the WRONG hypothesis (prior on event_at,
    # both rows equally idle) the id tie-break would ship the stale row
    # first, so only the write-axis prior produces the asserted order.
    stale = _insert_row(
        repo, "a-stale-written-year-ago", body="quantum resonance",
        created_at=year_ago, event_at=year_ago,
    )
    fresh_about_past = _insert_row(
        repo, "z-fresh-write-old-event", body="quantum resonance",
        created_at=now, event_at=year_ago,
    )
    service = MemoirRetrievalService(repo, embedder=None, now_fn=lambda: now)
    result = service.recall("ari", "quantum")
    assert result.rank_basis == "rrf"
    assert result.rendered_ids, result
    # The fresh write about the past ships ABOVE the year-old write —
    # only true if the prior ran on the write axis.
    assert result.rendered_ids.index(fresh_about_past) < result.rendered_ids.index(
        stale
    ), result.rendered_ids


# ══ ITEM 7 — persona SEED + the HUMAN-LANE GUARD ═════════════════════════


def test_persona_seed_lands_via_the_seed_path_and_is_idempotent_on_pg(pg_engine):
    """The seed installs the day-one charter byte-identically (pointer +
    approved version, ``source="seed"``); a second call is a NO-OP that
    returns the SAME row."""
    first = seed_persona_if_absent(pg_engine, "ari")
    assert first is not None
    assert first.version == 1
    assert first.status == "approved"
    assert first.source == "seed"
    assert first.body == MEMOIR_PERSONA_SEED
    assert first.approved_at is not None
    with Session(pg_engine) as session:
        versions = session.exec(
            select(MemoirPersonaVersion).where(MemoirPersonaVersion.agent_id == "ari")
        ).all()
        assert len(versions) == 1
        pointers = session.exec(
            text("SELECT current_version_id FROM memoir_persona WHERE agent_id = 'ari'")
        ).fetchall()
        assert len(pointers) == 1
        assert pointers[0][0] == first.id

    again = seed_persona_if_absent(pg_engine, "ari")
    assert again is not None
    assert again.id == first.id
    with Session(pg_engine) as session:
        versions = session.exec(
            select(MemoirPersonaVersion).where(MemoirPersonaVersion.agent_id == "ari")
        ).all()
        assert len(versions) == 1  # the re-boot duplicated NOTHING


def test_human_lane_guard_refuses_non_human_actors_on_pg(persona_repo):
    """AM-17: ``approve``/``reject`` are HUMAN-LANE ONLY — any actor whose
    ``auth_method`` is not ``"session"`` is refused with the house
    ``HumanLaneRequiredError``, before any transition happens."""
    from daemon.services.prompt_version_service import HumanLaneRequiredError

    draft = persona_repo.create_draft("ari", "v2 body", source="ari")
    assert draft.status == "draft"
    # Submitting is NOT a human-lane action (a machine may propose).
    submitted = persona_repo.submit(draft.id)
    assert submitted.status == "pending_approval"

    with pytest.raises(HumanLaneRequiredError):
        persona_repo.approve(draft.id, _HumanActor("api_key"))
    with pytest.raises(HumanLaneRequiredError):
        persona_repo.reject(draft.id, _HumanActor("mcp"))
    with pytest.raises(HumanLaneRequiredError):
        persona_repo.approve(draft.id, _HumanActor(None))
    with pytest.raises(HumanLaneRequiredError):
        persona_repo.approve(draft.id, None)

    # The refusals wrote NOTHING: the row is still pending_approval.
    still = persona_repo.get_version(draft.id)
    assert still.status == "pending_approval"

    # The human lane approves.
    approved = persona_repo.approve(draft.id, _HumanActor("session"))
    assert approved.status == "approved"


# ══ ITEM 8 — RRF FUSION + the recency prior + the keepsake exemption ═════


def test_rrf_fusion_combines_the_bm25_and_cosine_lanes_on_pg(repo, memoir_tsv):
    """The hybrid recall fuses the tsvector lane and the cosine lane by
    RRF on real PG rows: a row present in BOTH lanes outranks a
    single-lane row, and a semantic-only row (no lexical hit) still ships."""
    now = _now()
    # Lexically strong, semantically weak (orthogonal query vector).
    _insert_row(repo, "g2-rrf-lexical", body="quantum resonance chamber",
                created_at=now)
    # Semantically strong, lexically ABSENT (no query term at all).
    semantic_only = _insert_row(repo, "g2-rrf-semantic", body="unrelated prose entirely",
                                created_at=now)
    # Present in BOTH lanes.
    both = _insert_row(repo, "g2-rrf-both", body="quantum resonance chamber",
                       created_at=now)
    query_vector = [1.0, 0.0, 0.0]
    repo.store_embedding("g2-rrf-both", [0.6, 0.8, 0.0], "test-model", 3)
    repo.store_embedding("g2-rrf-lexical", [0.0, 1.0, 0.0], "test-model", 3)
    repo.store_embedding(semantic_only, [0.9999, 0.014, 0.0], "test-model", 3)

    service = MemoirRetrievalService(
        repo,
        embedder=lambda _text: query_vector,
        embedding_model="test-model",
        embedding_dims=3,
        now_fn=lambda: now,
    )
    result = service.recall("ari", "quantum")
    assert result.rank_basis == "rrf"
    assert result.semantic_enabled is True
    assert result.missing_embedding == 0

    # The EXPECTED fusion, computed from the same primitives the service
    # consumes: the repo's own lexical list + the Python cosine.
    lexical = repo.lexical_rank("ari", "quantum", MEMOIR_RANK_CANDIDATES)
    from daemon.services.critical_notes_selector import _cosine_similarity

    semantic = [
        (row_id, _cosine_similarity(query_vector, vector))
        for row_id, vector in [
            ("g2-rrf-both", [0.6, 0.8, 0.0]),
            ("g2-rrf-lexical", [0.0, 1.0, 0.0]),
            (semantic_only, [0.9999, 0.014, 0.0]),
        ]
    ]
    semantic.sort(key=lambda pair: (-pair[1], pair[0]))
    fused = fuse_rankings(lexical, semantic)
    expected = [
        row_id
        for row_id, _score in sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    assert result.rendered_ids == expected
    # The semantic-only row SHIPS (it has no lexical term at all).
    assert semantic_only in result.rendered_ids
    # A both-lane row outranks any single-lane row (RRF sums the terms).
    assert result.rendered_ids.index(both) < result.rendered_ids.index(semantic_only)


def test_recency_prior_deprioritizes_stale_and_exempts_keepsakes_on_pg(
    repo, memoir_tsv
):
    """The prior multiplies the fused score by ``exp(−idle/180d)``; a
    keepsake is EXEMPT (multiplier 1.0, never decayed). Discriminator
    ids: if the exemption were broken, the keepsake would tie with the
    stale row and lose the ASCII tie-break — the asserted order [fresh,
    keepsake, stale] holds under BOTH the prior and the exemption."""
    now = _now()
    year_ago = now - timedelta(days=365)
    body = "zebra preserved"
    _insert_row(repo, "z-keepsake-old", body=body, created_at=year_ago)
    _insert_row(repo, "a-stale-old", body=body, created_at=year_ago)
    fresh = _insert_row(repo, "m-fresh-now", body=body, created_at=now)
    repo.set_pin("z-keepsake-old", actor="emperor", reason="kept for the charter")

    # The prior arithmetic on the REAL idle the rows carry.
    assert recency_multiplier(is_keepsake=False, idle_days=365.0) < 0.2
    assert recency_multiplier(is_keepsake=True, idle_days=365.0) == 1.0
    assert recency_multiplier(is_keepsake=True, idle_days=0.0) == 1.0

    service = MemoirRetrievalService(repo, embedder=None, now_fn=lambda: now)
    result = service.recall("ari", "zebra")
    order = result.rendered_ids
    assert order.index(fresh) < order.index("z-keepsake-old")
    assert order.index("z-keepsake-old") < order.index("a-stale-old")


# ══ ITEM 9 — ACCESS STATS on EXPLICIT recall ONLY ════════════════════════


def test_explicit_recall_bumps_access_stats_for_exactly_the_shipped_ids_on_pg(
    repo, memoir_tsv
):
    """``memoir_recall`` marks EXACTLY the shipped projection: shipped rows
    gain ``access_count`` +1 and a ``last_accessed_at``; rows dropped for
    budget (or never matched) are NOT marked."""
    now = _now()
    for index in range(4):
        _insert_row(
            repo, f"g2-stats-{index}", body=f"retrievable fact number {index}",
            created_at=now,
        )
    service = MemoirRetrievalService(repo, embedder=None, now_fn=lambda: now)
    result = service.recall("ari", "retrievable fact", k=2)
    assert result.stats_updated is True
    assert len(result.rendered_ids) == 2
    stats = _stats(repo)
    for shipped in result.rendered_ids:
        assert stats[shipped][0] == 1
        assert stats[shipped][1] is not None
    for row_id, (count, last) in stats.items():
        if row_id not in result.rendered_ids:
            assert count == 0 and last is None, (row_id, count, last)
    # A SECOND explicit recall bumps AGAIN (the counter accumulates).
    service.recall("ari", "retrievable fact", k=2)
    stats2 = _stats(repo)
    for shipped in result.rendered_ids:
        assert stats2[shipped][0] == 2


def test_implicit_briefing_reads_do_not_bump_access_stats_on_pg(
    repo, pg_engine, memoir_tsv
):
    """Direction 2: the wake-briefing path (persona + keepsakes + latest
    episodes reads) is an IMPLICIT read — it bumps NOTHING. Contrast leg:
    one explicit recall right after DOES bump."""
    seed_persona_if_absent(pg_engine, "ari")
    now = _now()
    for index in range(2):
        repo.insert_episode(
            agent_id="ari",
            body=f"implicit read probe {index}",
            episode_id=f"g2-implicit-{index}",
        )
    before = _stats(repo)
    assert all(count == 0 and last is None for count, last in before.values())

    class _StubManager:
        _engine = pg_engine

    message = asyncio.run(
        _build_memoir_briefing_message("ari", "g2-inst-1", _StubManager())
    )
    assert message is not None
    body = str(getattr(message, "content", message))
    assert "## Persona" in body
    assert MEMOIR_PERSONA_SEED.splitlines()[0] in body

    after = _stats(repo)
    assert after == before  # the implicit path bumped NOTHING

    # CONTRAST: one explicit recall bumps the shipped ids.
    service = MemoirRetrievalService(repo, embedder=None, now_fn=lambda: now)
    result = service.recall("ari", "implicit read probe")
    assert result.stats_updated is True
    bumped = _stats(repo)
    assert any(bumped[row_id][0] == 1 for row_id in result.rendered_ids)


# ── the corpus does not touch the clamp/budget constants ─────────────────


def test_the_corpus_respects_the_shipped_budget_constants():
    """Guard: the constants this corpus reasons about are the SHIPPED
    values — if the lane re-anchors them, this leg reds and the corpus
    arithmetic must be re-derived, never silently stale."""
    assert MEMOIR_SECTION_CHAR_CEILING == 24000
    assert MEMOIR_SECTION_CHAR_ABSOLUTE == 48000
    assert MEMOIR_RECALL_DEFAULT_K == 5
    assert MEMOIR_RECALL_BODY_TOKEN_CLAMP == 1200

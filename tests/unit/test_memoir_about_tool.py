"""Memoir Store v2 — the ``memoir_about`` TOOL (phase 3, T3.6).

Plan: ``.agents/shared/planning/ari-memoir-store/v2-phase3-plan.md``
T3.6 / AC3 / AC4 / AC5 (+ the V2-D 3.3 heat legs); pack
``test/packs/memoir_v2_about_tool_test.sh`` and
``test/packs/memoir_v2_tool_registration_test.sh`` (the registration
class).

What this suite pins:

* **AC3 — the gate order is EXACTLY the plan §3.2 order** (auth-first,
  ``_gate`` then validation then the DB): an unauthorized agent is told
  ``memoir_not_authorized`` WITHOUT the knob being read or the DB being
  touched (the behavioral ProbeManager counters); the knob OFF answers
  ``memoir_disabled``; an empty/whitespace ``star`` answers
  ``memoir_query_required``; a >100-char star answers
  ``memoir_stars_out_of_bounds``; an unknown star answers
  ``memoir_not_found``; NOTHING raises;
* **V2-D 3.6 — the zero-episode star is an empty-dossier SUCCESS** (the
  sections say "(none)"), never ``memoir_not_found``, and the stored
  heat is NOT incremented (ND-23: an empty lookup is not a hit);
* **the populated dossier**: canonical name / star type / description /
  recent connected episodes / 1-hop neighbor stars render, the alias
  "the Emperor" resolves to the canonical card, and the stored heat
  moves +1.0 (the ONLY explicit-recall writer);
* **V2-D 3.3 — the two-part heat model**: the render-time decay is
  ``round(stored_heat * exp(-MEMOIR_HEAT_DECAY_LAMBDA * days_idle), 2)``
  — worked numbers as legs (5.0 at 0 idle days; ≈2.51 at 138 idle
  days, the ~138-day half-life);
* **the frozen 19-code vocabulary (D4.8)**: every emitted refusal code
  is a member of ``MEMOIR_CODES`` and the set holds EXACTLY 19 — no
  20th code was minted for the about tool;
* **AC4/AC5 — registration integrity**: ``MEMOIR_TOOL_NAMES`` holds 6
  tools; ``discover_source_only_tool_names`` discovers ``memoir_about``
  from source; the factory builds the surface for Ari and the runtime
  layer-2 gate refuses the same invocation for another agent while the
  layer-1 category admission (the agents' ``tools.allow`` entries) is
  Ari-only; the memoir category ordinal is GREP-DERIVED at test time
  from ``.agents/shared/metrics/tool-categories.json`` — zero hardcoded
  ordinals anywhere in this file (V2-D 3.7).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, StaticPool, create_engine

from daemon.repositories.memoir.constellation_repository import (
    MemoirConstellationRepository,
)
from daemon.repositories.memoir.models import MemoirStar
from daemon.repositories.memoir.repository import MemoirRepository
from daemon.services.memoir_constellation_service import (
    MEMOIR_HEAT_DECAY_LAMBDA,
    MemoirConstellationService,
)
from daemon.tools._tool_registry import (
    PRIVILEGED_TOOL_CATEGORIES,
    discover_source_only_tool_names,
)
from daemon.tools.memoir import (
    MEMOIR_AUTHORIZED_AGENT,
    MEMOIR_CODES,
    MEMOIR_TOOL_NAMES,
    create_memoir_tools,
)

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

AGENT = MEMOIR_AUTHORIZED_AGENT
REPO_ROOT = Path(__file__).resolve().parents[2]

NOW = datetime(2026, 11, 9, 12, 0, 0, tzinfo=timezone.utc)


class ProbeManager:
    """A manager double that COUNTS knob reads and engine touches.

    The gate-order proof is behavioral (the phase-4 suite's shape): the
    auth gate must precede the knob read, which must precede any engine
    touch. A Mock would satisfy any access order silently; this double
    records it.
    """

    def __init__(self, engine=None, knob_on: bool = True) -> None:
        self._engine_handle = engine
        self._knob_on = knob_on
        self.knob_reads = 0
        self.engine_touches = 0

    @property
    def config(self):  # noqa: D401 — the knob's config tier
        self.knob_reads += 1
        return SimpleNamespace(memoir=SimpleNamespace(enabled=self._knob_on))

    @property
    def _engine(self):
        self.engine_touches += 1
        return self._engine_handle


def _reason(result: str) -> str:
    """Extract the typed code from the ``REFUSED — reason=<code>:`` envelope."""
    marker = "reason="
    idx = result.index(marker)
    tail = result[idx + len(marker) :]
    return tail.split(":", 1)[0].strip()


@pytest.fixture()
def engine():
    # Cross-thread: the tool body runs its DB touch under asyncio.to_thread,
    # so the in-memory SQLite connection must be shareable across threads.
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def repo(engine):
    return MemoirRepository(engine)


@pytest.fixture()
def constellation_repo(engine):
    return MemoirConstellationRepository(engine)


@pytest.fixture()
def mgr(engine):
    return ProbeManager(engine=engine)


@pytest.fixture()
def tools(mgr, monkeypatch):
    monkeypatch.delenv("ENSEMBLE_MEMOIR_ENABLED", raising=False)
    return {
        tool_obj.name: tool_obj
        for tool_obj in create_memoir_tools(
            mgr, current_instance_id="inst-1", agent_id=AGENT
        )
    }


def _stand_in(episode_id: str, *, stars: list[str], body: str, event_at):
    return SimpleNamespace(
        id=episode_id, stars=list(stars), body=body, event_at=event_at
    )


def _stored_heat(engine, star_id: str) -> float:
    with Session(engine) as session:
        row = session.get(MemoirStar, star_id)
        return float(row.heat)


def _seed_populated_constellation(repo, constellation_repo):
    """Two episodes; "The Emperor" + a neighbor "agents-ensemble"."""
    ep1 = repo.insert_episode(
        agent_id=AGENT,
        title="joint design session on the memoir store",
        body="we designed the constellation layer together",
        stars=["The Emperor", "agents-ensemble"],
        created_at=NOW - timedelta(days=2),
        event_at=NOW - timedelta(days=2),
    )
    ep2 = repo.insert_episode(
        agent_id=AGENT,
        title="the constitutional ruling",
        body="the emperor ratified the reflection cadence",
        stars=["The Emperor"],
        created_at=NOW - timedelta(days=1),
        event_at=NOW - timedelta(days=1),
    )
    svc = MemoirConstellationService(
        constellation_repo, llm_caller=None, clock=lambda: NOW, agent_id=AGENT
    )
    svc.extract(
        [
            _stand_in(
                ep1,
                stars=["The Emperor", "agents-ensemble"],
                body="we designed the constellation layer together",
                event_at=NOW - timedelta(days=2),
            ),
            _stand_in(
                ep2,
                stars=["The Emperor"],
                body="the emperor ratified the reflection cadence",
                event_at=NOW - timedelta(days=1),
            ),
        ],
        pass_id="pass-1",
        now=NOW,
    )
    return ep1, ep2


# ── AC3: the gate order (auth → knob → validation → DB) ──────────────────


class TestTheGateOrder:
    async def test_an_unauthorized_agent_is_refused_before_the_knob_and_the_db(
        self, mgr, monkeypatch
    ):
        monkeypatch.delenv("ENSEMBLE_MEMOIR_ENABLED", raising=False)
        denied = {
            tool_obj.name: tool_obj
            for tool_obj in create_memoir_tools(
                mgr, current_instance_id="inst-1", agent_id="developer"
            )
        }
        mgr.knob_reads = 0  # measure the INVOCATION window (post-factory)
        mgr.engine_touches = 0
        result = await denied["memoir_about"].ainvoke({"star": "The Emperor"})
        assert _reason(result) == "memoir_not_authorized"
        # AUTH-FIRST: the knob was never read and the DB was never touched
        # (the factory's own D0.7 help-embedding read is excluded — the
        # counters measure the INVOCATION window).
        assert mgr.knob_reads == 0
        assert mgr.engine_touches == 0

    async def test_a_blank_caller_identity_is_agent_unknown(self, mgr):
        blank = {
            tool_obj.name: tool_obj
            for tool_obj in create_memoir_tools(
                mgr, current_instance_id="inst-1", agent_id=""
            )
        }
        mgr.knob_reads = 0  # measure the INVOCATION window (post-factory)
        result = await blank["memoir_about"].ainvoke({"star": "The Emperor"})
        assert _reason(result) == "memoir_agent_unknown"
        assert mgr.knob_reads == 0

    async def test_the_knob_off_refuses_memoir_disabled_before_the_db(
        self, engine, monkeypatch
    ):
        monkeypatch.delenv("ENSEMBLE_MEMOIR_ENABLED", raising=False)
        mgr = ProbeManager(engine=engine, knob_on=False)
        tools = {
            tool_obj.name: tool_obj
            for tool_obj in create_memoir_tools(
                mgr, current_instance_id="inst-1", agent_id=AGENT
            )
        }
        result = await tools["memoir_about"].ainvoke({"star": "The Emperor"})
        assert _reason(result) == "memoir_disabled"
        assert mgr.knob_reads >= 1
        # The kill-switch precedes ANY DB touch.
        assert mgr.engine_touches == 0

    async def test_an_empty_star_is_memoir_query_required(self, tools):
        for empty in ("", "   "):
            result = await tools["memoir_about"].ainvoke({"star": empty})
            assert _reason(result) == "memoir_query_required"

    async def test_an_overlong_star_is_memoir_stars_out_of_bounds(self, tools):
        result = await tools["memoir_about"].ainvoke({"star": "x" * 101})
        assert _reason(result) == "memoir_stars_out_of_bounds"

    async def test_an_unknown_star_is_memoir_not_found(self, tools):
        result = await tools["memoir_about"].ainvoke({"star": "Ghost Star"})
        assert _reason(result) == "memoir_not_found"
        assert "Ghost Star" in result

    @pytest.mark.parametrize(
        "star",
        ["", "   ", "x" * 101, "Ghost Star"],
    )
    async def test_no_gate_leg_ever_raises_and_every_code_is_frozen(
        self, tools, star
    ):
        result = await tools["memoir_about"].ainvoke({"star": star})
        assert isinstance(result, str)
        if "REFUSED" in result:
            assert _reason(result) in MEMOIR_CODES


# ── V2-D 3.6: the zero-episode star is an empty-dossier SUCCESS ───────────


class TestTheZeroEpisodeStar:
    async def test_a_zero_episode_star_renders_an_empty_dossier_success(
        self, repo, constellation_repo, tools
    ):
        constellation_repo.upsert_star(
            AGENT, "Lonely Comet", "concept", event_at=NOW - timedelta(days=1)
        )
        result = await tools["memoir_about"].ainvoke({"star": "Lonely Comet"})
        # SUCCESS, not memoir_not_found (V2-D 3.6 / §2-Q7).
        assert "REFUSED" not in result
        assert "=== STAR: Lonely Comet" in result
        # The sections say "none".
        episodes_section = result.split("Connected Episodes (recent):")[1]
        neighbors_section = result.split("Neighbor Stars (1-hop):")[1]
        assert "(none)" in episodes_section
        assert "(none)" in neighbors_section
        assert "memoir_not_found" not in result

    async def test_the_zero_episode_hit_does_not_increment_heat(
        self, repo, constellation_repo, tools
    ):
        """ND-23: an empty lookup is not a valid memory hit."""
        star = constellation_repo.upsert_star(
            AGENT, "Lonely Comet", "concept", event_at=NOW - timedelta(days=1)
        )
        await tools["memoir_about"].ainvoke({"star": "Lonely Comet"})
        assert _stored_heat(constellation_repo.engine, star.id) == 0.0


# ── the populated dossier + the explicit-recall heat increment ───────────


class TestThePopulatedDossier:
    async def test_the_populated_card_renders_every_section_and_increments_heat(
        self, repo, constellation_repo, tools
    ):
        _seed_populated_constellation(repo, constellation_repo)
        star = constellation_repo.resolve_star(AGENT, "The Emperor")
        assert _stored_heat(constellation_repo.engine, star.id) == 0.0

        result = await tools["memoir_about"].ainvoke({"star": "The Emperor"})
        assert "REFUSED" not in result
        # Canonical identity + type + description.
        assert "=== STAR: The Emperor" in result
        assert "(type: concept" in result  # the write path stores names only
        assert "Description:" in result
        # Recent connected episodes (the canonical episode titles).
        assert "joint design session on the memoir store" in result
        assert "the constitutional ruling" in result
        # 1-hop neighbors.
        assert "agents-ensemble" in result.split("Neighbor Stars (1-hop):")[1]
        # The ONLY explicit-recall writer moved the stored accumulator.
        assert _stored_heat(constellation_repo.engine, star.id) == 1.0

    async def test_the_alias_query_resolves_to_the_canonical_card(
        self, repo, constellation_repo, tools
    ):
        _seed_populated_constellation(repo, constellation_repo)
        canonical = await tools["memoir_about"].ainvoke({"star": "The Emperor"})
        via_alias = await tools["memoir_about"].ainvoke({"star": "the Emperor"})
        assert "REFUSED" not in via_alias
        # The alias lands on the SAME canonical card (the identity header
        # modulo the heat segment — the first call legitimately bumped it).
        assert (
            via_alias.split("\n")[0].split(", heat")[0]
            == canonical.split("\n")[0].split(", heat")[0]
        )

    async def test_heat_increments_are_cumulative_across_hits(
        self, repo, constellation_repo, tools
    ):
        _seed_populated_constellation(repo, constellation_repo)
        star = constellation_repo.resolve_star(AGENT, "The Emperor")
        await tools["memoir_about"].ainvoke({"star": "The Emperor"})
        await tools["memoir_about"].ainvoke({"star": "the Emperor"})
        assert _stored_heat(constellation_repo.engine, star.id) == 2.0


# ── V2-D 3.3: the render-time decay (the worked numbers) ─────────────────


class TestHeatMath:
    def test_zero_idle_days_render_the_stored_heat_unchanged(self):
        last_touch = NOW
        assert (
            MemoirConstellationService.effective_heat(5.0, last_touch, now=NOW)
            == 5.0
        )

    def test_the_138_day_idle_star_decays_to_about_half(self):
        """≈2.51 at 138 idle days — the ~138-day half-life leg."""
        last_touch = NOW - timedelta(days=138)
        effective = MemoirConstellationService.effective_heat(
            5.0, last_touch, now=NOW
        )
        assert effective == 2.51

    def test_a_future_touch_is_clamped_to_zero_idle_days(self):
        last_touch = NOW + timedelta(days=3)  # clock skew must not AMPLIFY
        assert (
            MemoirConstellationService.effective_heat(5.0, last_touch, now=NOW)
            == 5.0
        )

    def test_the_decay_constant_is_the_named_one(self):
        assert MEMOIR_HEAT_DECAY_LAMBDA == 0.005
        assert MemoirConstellationService.MEMOIR_HEAT_DECAY_LAMBDA == 0.005


# ── D4.8: the frozen 19-code vocabulary ──────────────────────────────────


class TestTheFrozenVocabulary:
    def test_the_vocabulary_holds_exactly_nineteen_codes(self):
        assert len(MEMOIR_CODES) == 19

    def test_the_about_refusals_map_onto_existing_codes_only(self):
        """V2-D 3.5: the about tool mints NO 20th code."""
        for code in (
            "memoir_not_authorized",
            "memoir_disabled",
            "memoir_query_required",
            "memoir_stars_out_of_bounds",
            "memoir_not_found",
        ):
            assert code in MEMOIR_CODES
        assert "memoir_star_not_found" not in MEMOIR_CODES


# ── AC4/AC5: registration integrity + the grep-derived ordinal ───────────


class TestRegistration:
    def test_the_tool_name_set_holds_six_tools(self):
        assert len(MEMOIR_TOOL_NAMES) == 6
        assert "memoir_about" in MEMOIR_TOOL_NAMES

    def test_source_discovery_includes_memoir_about(self):
        discovered = discover_source_only_tool_names()
        assert "memoir_about" in discovered
        for name in MEMOIR_TOOL_NAMES:
            assert name in discovered

    def test_the_factory_yields_memoir_about_for_ari(self):
        built = {
            tool_obj.name: tool_obj
            for tool_obj in create_memoir_tools(None, agent_id=AGENT)
        }
        assert set(built) == set(MEMOIR_TOOL_NAMES)
        assert "memoir_about" in built

    async def test_another_agent_gets_the_surface_but_the_runtime_gate_refuses(
        self, mgr, monkeypatch
    ):
        """The two-layer posture: layer 2 refuses the invocation."""
        monkeypatch.delenv("ENSEMBLE_MEMOIR_ENABLED", raising=False)
        denied = {
            tool_obj.name: tool_obj
            for tool_obj in create_memoir_tools(
                mgr, current_instance_id="inst-1", agent_id="worker"
            )
        }
        assert "memoir_about" in denied  # PRESENT and refusing (D4.6)
        result = await denied["memoir_about"].ainvoke({"star": "The Emperor"})
        assert _reason(result) == "memoir_not_authorized"

    def test_the_layer1_category_admission_is_ari_only(self):
        """Layer 1: the agents' ``tools.allow`` category entries."""
        ari_allow = json.loads(
            (REPO_ROOT / "agents" / "ari" / "meta.json").read_text(encoding="utf-8")
        )["tools"]["allow"]
        assert "memoir" in ari_allow
        for other in ("developer", "worker", "reviewer", "tester"):
            meta_path = REPO_ROOT / "agents" / other / "meta.json"
            if not meta_path.exists():
                continue
            allow = json.loads(meta_path.read_text(encoding="utf-8"))["tools"][
                "allow"
            ]
            assert "memoir" not in allow, f"{other} must not carry the memoir category"

    def test_the_category_ordinal_is_grep_derived_never_hardcoded(self):
        """V2-D 3.7: derive the ordinal from the metrics census AT TEST TIME.

        The ordinal is DERIVED from
        ``.agents/shared/metrics/tool-categories.json`` and cross-checked
        against the registry census — no literal ordinal appears in this
        file, so a sibling-lane merge that shifts the category list
        re-derives instead of breaking.
        """
        census_path = (
            REPO_ROOT / ".agents" / "shared" / "metrics" / "tool-categories.json"
        )
        census = json.loads(census_path.read_text(encoding="utf-8"))
        categories = census["categories"]
        # Self-consistent census.
        assert census["count"] == len(categories)
        # The DERIVED ordinal — the ceremony value comes from HERE, not
        # from a hardcoded literal.
        ordinal = categories.index("memoir")
        assert categories[ordinal] == "memoir"
        assert census["vocabulary_version"] >= 1
        # The registry agrees the category exists and is privileged.
        assert "memoir" in PRIVILEGED_TOOL_CATEGORIES
        discovered = discover_source_only_tool_names()
        assert set(MEMOIR_TOOL_NAMES) <= discovered

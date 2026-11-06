"""The memoir wake briefing — the renderer, the budget, the backstop, the pins.

Memoir Store v1 — phase 5 (plan:
``.agents/shared/planning/ari-memoir-store/phase5-plan.md`` T5.2/T5.5;
pack ``test/packs/memoir_briefing_unit_test.sh``).

What this suite pins:

* the ZONE LAYOUT — stable (persona + ALL keepsakes) then dynamic
  (recent episodes), the affordance line LAST;
* the BYTE-PINNED SURFACES — the keepsake-line template (D5.7/AM-24),
  the affordance line (D5.10), the 24,000-char overage disclosure
  (D5.8/AM-43/DR-1), the 48,000-char withhold WARNING, the
  persona-missing prefix (D5.9/AM-39);
* the DYNAMIC BUDGET — oldest dropped first, the newest line never
  dropped for budget;
* the N-3 MEASUREMENT — the char/token ratio for DR-1's admitted
  stable worst case (persona ≤2,000 + keepsakes ≤4,000 tokens) against
  the 24,000-char absolute ceiling, and the expected dynamic-zone
  survival — a MEASUREMENT, not an assumption;
* the knob gate at unit level (``knob_allows``) and the consumer-side
  presence predicate (``memoir_block_present``).

The MUTATION legs live in the pack: the ceiling removed, the affordance
dropped, stable-zone truncation attempted (must be unreachable), and
the withhold path writing the flag.

FENCE: every engine in this suite is a throwaway in-memory SQLite via
``StaticPool`` (the reads run in ``asyncio.to_thread`` workers — a
plain ``:memory:`` URL gives each thread its OWN empty database); the
"manager" is a ``SimpleNamespace``, never an ``InstanceManager``; no
``POSTGRES_*`` variable is read.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from daemon.repositories.memoir.models import (
    MemoirEpisode,
    MemoirPersona,
    MemoirPersonaVersion,
    MemoirSynthesis,
)
from daemon.repositories.memoir.repository import MemoirRepository
from daemon.services import memoir_briefing
from daemon.services.context_messages import escape_for_context_block
from daemon.services.memoir_briefing import (
    KEEPSAKE_LINE_TEMPLATE,
    MEMOIR_AGENT_ID,
    MEMOIR_AFFORDANCE_LINE,
    MEMOIR_DYNAMIC_TOKEN_BUDGET,
    MEMOIR_PERSONA_MISSING_PREFIX,
    MEMOIR_SECTION_CHAR_ABSOLUTE,
    MEMOIR_SECTION_CHAR_CEILING,
    MEMOIR_STABLE_OVERAGE_DISCLOSURE,
    MEMOIR_WITHHOLD_WARNING,
    _build_memoir_briefing_message,
    knob_allows,
    memoir_block_present,
    render_briefing_body,
    render_dynamic_line,
    render_keepsake_line,
)

FIXED_NOW = datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)

_EN_WORDS = (
    "the quick lantern walk along canal path argued fonts serif era "
    "memory horizon harbor ledger beacon ember ash glimmer velvet "
    "copper marble slate thistle bramble hollow sparrow kestrel "
    "orchard granite willow cascade meadow harbor lantern amber "
    "quill vellum compass anchor harbor tide current estuary delta "
    "basin ridge plateau summit glacier moraine tundra prairie "
    "savanna canopy understory rhizome mycelium spore pollen "
    "nectar thorn petal sepal stigma frond frustum vertex apex "
    "zenith nadir azimuth meridian parallel tangent arc chord "
    "sonata prelude nocturne etude ballad anthem hymn psalm "
    "cipher glyph rune sigil token shard shard prism spectrum "
    "tessella mosaic filigree lattice trellis arbor pergola "
    "atrium colonnade rotunda dome spire belfry campanile "
    "wharf jetty breakwater lighthouse bell buoy skiff dinghy "
    "galleon clipper brigantine sloop yawl ketch cutter "
).split()

_EN = " ".join(_EN_WORDS)


# ── fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture()
def engine():
    """Throwaway in-memory SQLite, ONE connection shared across threads.

    The briefing's reads run in ``asyncio.to_thread`` workers; a plain
    ``sqlite:///:memory:`` URL hands every thread a fresh empty
    database, so the pool must be ``StaticPool`` with
    ``check_same_thread=False`` (house precedent:
    ``tests/test_resource_pid_registry.py:579``).
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def repo(engine):
    return MemoirRepository(engine)


@pytest.fixture()
def fake_manager(engine):
    """The consumer shape WITHOUT an InstanceManager — never construct one."""
    return SimpleNamespace(_engine=engine, config=None)


def _episode(
    id_: str,
    *,
    title: str | None,
    body: str,
    keepsake: bool = False,
    pinned_by: str | None = None,
    pinned_reason: str | None = None,
) -> MemoirEpisode:
    return MemoirEpisode(
        id=id_,
        agent_id=MEMOIR_AGENT_ID,
        title=title,
        body=body,
        created_at=FIXED_NOW,
        event_at=FIXED_NOW,
        is_keepsake=keepsake,
        pinned_by=pinned_by,
        pinned_reason=pinned_reason,
    )


def _tokens(text: str) -> int:
    from daemon.loader import estimate_tokens

    return estimate_tokens(text)


def _encoder():
    """The cl100k encoder DIRECTLY — ``estimate_tokens`` builds it per call.

    The house estimator is ``len(tiktoken.get_encoding('cl100k_base')
    .encode(text))`` (``daemon/loader.py:1027``); sizing ~52k-char
    fixtures word-by-word through it would construct the encoder
    thousands of times. The direct encoder measures the SAME bytes —
    the N-3 assertions still re-measure through the house estimator.
    """
    import tiktoken

    return tiktoken.get_encoding("cl100k_base")


def _sized_text(target_tokens: int, *, seed: int = 0) -> str:
    """Deterministic English text sized to ≤``target_tokens`` house tokens.

    Words are drawn from a fixed vocabulary in a fixed rotation, so the
    token count (tiktoken cl100k) is stable across runs. Growth is
    chunked (64 words per probe) with a word-level trim at the end —
    the admitted worst case is AT the budget, never over it.
    """
    enc = _encoder()
    words: list[str] = []
    index = seed
    while True:
        trial = words + [
            _EN_WORDS[(index + i) % len(_EN_WORDS)] for i in range(64)
        ]
        if len(enc.encode(" ".join(trial))) > target_tokens:
            break
        words = trial
        index += 64
    while words:
        if len(enc.encode(" ".join(words))) <= target_tokens:
            break
        words.pop()
    return " ".join(words)


def _keptake_row(n: int, reason: str) -> MemoirEpisode:
    return _episode(
        f"keep-{n}",
        title=f"Keepsake number {n}",
        body="keepsake body",
        keepsake=True,
        pinned_by="empress" if n % 2 else "emperor",
        pinned_reason=reason,
    )


# ── the zone layout + the byte-pinned surfaces ────────────────────────────


def test_block_layout_zones_ordered_affordance_last(repo, fake_manager):
    """D5.7/D5.10: persona zone, keepsake zone, dynamic zone, affordance LAST."""
    repo.seed_persona_if_absent(MEMOIR_AGENT_ID)
    with Session(repo.engine) as session:
        session.add(
            _episode(
                "ep-keep",
                title="The lantern walk",
                body="canal-path body",
                keepsake=True,
                pinned_by="empress",
                pinned_reason="the font argument",
            )
        )
        session.add(
            _episode("ep-recent", title="Deploy night", body="the loop lag fell")
        )
        session.commit()

    message = asyncio.run(
        _build_memoir_briefing_message(MEMOIR_AGENT_ID, "inst-1", fake_manager)
    )
    assert message is not None
    body = message.content
    assert body.startswith("[SYSTEM CONTEXT: Memoir]\n\n")
    persona_pos = body.index("## Persona")
    keepsake_pos = body.index("## Keepsakes")
    recent_pos = body.index("## Recent episodes")
    assert persona_pos < keepsake_pos < recent_pos
    assert body.rstrip().endswith(MEMOIR_AFFORDANCE_LINE)
    # the dynamic zone excludes keepsakes (already in the stable zone)
    assert "The lantern walk" in body.split("## Keepsakes")[1].split("## Recent")[0]
    recent_zone = body.split("## Recent episodes")[1]
    assert "Deploy night" in recent_zone
    assert "lantern walk" not in recent_zone


def test_stable_id_and_stamp_kwargs(repo, fake_manager):
    """D5.1/D5.2: stable id ``memoir:{agent_id}``; the ADR-5 stamp kwargs."""
    message = asyncio.run(
        _build_memoir_briefing_message(MEMOIR_AGENT_ID, "inst-1", fake_manager)
    )
    assert message is not None
    assert message.id == "memoir:ari"
    assert message.additional_kwargs == {
        "injected_message": True,
        "context_kind": "memoir",
    }


def test_keepsake_line_template_exact_bytes():
    """D5.7/AM-24: the keepsake line renders from the ONE pinned template."""
    line = render_keepsake_line(
        title="The lantern walk",
        pinned_by="empress",
        pclass="direct",
        pinned_reason="the font argument that ended the serif era",
    )
    assert line == KEEPSAKE_LINE_TEMPLATE.format(
        title="The lantern walk",
        pinned_by="empress",
        pclass="direct",
        pinned_reason="the font argument that ended the serif era",
    )
    assert line == (
        "- [keepsake] The lantern walk (pinned_by=empress; "
        "provenance=direct) the font argument that ended the serif era"
    )


def test_keepsake_line_field_defaults():
    """Missing title/reason/pinned_by render to deterministic placeholders."""
    line = render_keepsake_line(
        title=None, pinned_by=None, pclass="", pinned_reason=None
    )
    assert line == "- [keepsake] (untitled) (pinned_by=unknown; provenance=) "
    multi = render_keepsake_line(
        title="line\nbreak", pinned_by="emperor", pclass="inherited",
        pinned_reason="reason\nline",
    )
    assert "\n" not in multi


def test_dynamic_line_shape():
    line = render_dynamic_line(
        SimpleNamespace(
            title="Deploy night",
            body="the promote flipped",
            event_at=FIXED_NOW,
        )
    )
    assert line == "- [2026-11-05] Deploy night: the promote flipped"


def test_dynamic_drops_oldest_first(monkeypatch):
    """D5.7: under budget pressure the OLDEST lines (list tail) go first."""
    lines = [
        render_dynamic_line(SimpleNamespace(title=f"ep{i}", body="x " * 3,
                                            event_at=FIXED_NOW))
        for i in range(5)
    ]
    # budget fits exactly the newest three lines
    from daemon.services.memoir_briefing import _estimate_tokens

    budget = sum(_estimate_tokens(line) for line in lines[:3])
    kept = memoir_briefing._fit_dynamic_lines(lines, budget)
    assert kept == lines[:3]
    # budget fits only the newest line
    kept_one = memoir_briefing._fit_dynamic_lines(lines, _estimate_tokens(lines[0]))
    assert kept_one == [lines[0]]


def test_dynamic_never_empties(monkeypatch):
    """The newest line survives even when it alone exceeds the budget."""
    lines = [render_dynamic_line(SimpleNamespace(title="huge", body="word " * 900,
                                                 event_at=FIXED_NOW))]
    kept = memoir_briefing._fit_dynamic_lines(lines, 1)
    assert kept == lines


# ── the render-time backstop (D5.8 / AM-43 / DR-1) ────────────────────────


def _stable_chars_for(persona: str) -> int:
    """The escaped stable-zone char count, exactly as the renderer measures it.

    The renderer measures the SHIPPED bytes (escape first, then count);
    this helper reproduces that measurement for a persona-only stable
    zone (``## Persona`` header + body) so the tests can compute the
    expected N/M without re-deriving renderer internals.
    """
    return len(escape_for_context_block("\n".join(["## Persona", persona])))


# ── the render-time backstop (D5.8 / AM-43 / DR-1) ────────────────────────


def test_overage_24k_stable_in_full_dynamic_dropped_exact_disclosure():
    """First backstop rung: stable FULL, dynamic DROPPED, disclosure exact."""
    persona = _sized_text(7600)  # ≈30k chars — far over the 24k ceiling
    stable_chars = _stable_chars_for(persona)
    assert stable_chars > MEMOIR_SECTION_CHAR_CEILING
    episodes = [
        SimpleNamespace(title="recent", body="should be dropped",
                        event_at=FIXED_NOW, is_keepsake=False)
    ]
    body = render_briefing_body(
        persona_body=persona, keepsakes=[], episodes=episodes,
        provenance_class="direct",
    )
    assert body is not None
    # the stable zone ships IN FULL (never truncated)
    escaped_persona = escape_for_context_block(persona)
    assert escaped_persona in body
    # the dynamic zone is dropped entirely
    assert "should be dropped" not in body
    # the disclosure is exact — N is the shipped stable chars, M = N − 24,000
    expected = MEMOIR_STABLE_OVERAGE_DISCLOSURE.format(
        chars=stable_chars, over=stable_chars - MEMOIR_SECTION_CHAR_CEILING
    )
    assert expected in body
    assert "24,000-char ceiling" in body
    # the affordance still closes the block
    assert body.rstrip().endswith(MEMOIR_AFFORDANCE_LINE)


def test_withhold_48k_returns_none_and_warns(caplog):
    """Second rung: stable > 48k chars ⇒ ``None`` + the exact WARNING."""
    persona = _sized_text(13000, seed=3)  # ≈52k chars
    stable_chars = _stable_chars_for(persona)
    assert stable_chars > MEMOIR_SECTION_CHAR_ABSOLUTE
    with caplog.at_level(logging.WARNING, logger="daemon.services.memoir_briefing"):
        body = render_briefing_body(
            persona_body=persona, keepsakes=[], episodes=[],
            provenance_class="",
        )
    assert body is None
    warnings = [r.getMessage() for r in caplog.records]
    assert MEMOIR_WITHHOLD_WARNING.format(chars=stable_chars) in warnings
    assert any("48,000-char absolute bound" in message for message in warnings)


def test_overage_boundary_between_ceiling_and_absolute():
    """A stable zone between 24k and 48k keeps the disclosure rung, not None."""
    persona = _sized_text(7000)  # ≈28k chars — over 24k, under 48k
    stable_chars = _stable_chars_for(persona)
    assert MEMOIR_SECTION_CHAR_CEILING < stable_chars < MEMOIR_SECTION_CHAR_ABSOLUTE
    body = render_briefing_body(
        persona_body=persona, keepsakes=[], episodes=[], provenance_class=""
    )
    assert body is not None
    assert "pin admission exceeded its budget" in body


# ── the persona-missing path (D5.9 / AM-39) ───────────────────────────────


def test_persona_missing_keepsakes_only_with_exact_prefix(repo):
    """No approved persona ⇒ keepsakes-only stable zone under the prefix."""
    keepsakes = [
        _keptake_row(1, "the lantern walk is canon"),
        _keptake_row(2, "the font argument is canon"),
    ]
    body = render_briefing_body(
        persona_body=None, keepsakes=keepsakes, episodes=[],
        provenance_class="inherited",
    )
    assert body is not None
    assert body.startswith(f"{MEMOIR_PERSONA_MISSING_PREFIX}\n")
    assert MEMOIR_PERSONA_MISSING_PREFIX == (
        "[persona unavailable — no approved persona version]"
    )
    assert "## Persona" not in body
    assert "Keepsake number 1" in body
    assert "provenance=inherited" in body
    assert body.rstrip().endswith(MEMOIR_AFFORDANCE_LINE)


# ── N-3 — the char/token measurement (DR-1's admitted worst case) ─────────


def test_n3_char_token_ratio_of_admitted_worst_case():
    """N-3: MEASURE the admitted stable worst case against the 24k ceiling.

    The admitted worst case is persona ≤2,000 tokens + keepsakes ≤4,000
    tokens (pin admission, D2.12) = 6,000 tokens total. This leg sizes
    deterministic English text to just under each budget, renders the
    stable zone, and measures the ESCAPED shipped chars.

    The binding invariant asserted here: the admitted worst case NEVER
    breaches the 48,000-char withhold bound (the stable zone always
    ships — worst case with the disclosure). Whether the 24,000-char
    disclosure rung fires depends on the MEASURED char/token ratio —
    DR-1's "6,000 tokens ≈ 24k chars" assumed a 4.0 ratio; cl100k on
    English prose measures ≈4.4–4.5 — so the leg reports the resulting
    expected dynamic-zone survival as a MEASUREMENT, never an
    assumption.
    """
    persona = _sized_text(1999)
    persona_tokens = _tokens(persona)
    assert persona_tokens <= 2000

    # Keepsakes admitted exactly as D2.12 admits them: add pins until
    # the cumulative rendered-line tokens would exceed 4,000.
    keepsakes = []
    keepsake_tokens = 0
    for n in range(12):
        reason = _sized_text(320, seed=n + 1)
        line = render_keepsake_line(
            title=f"Keepsake number {n}",
            pinned_by="empress" if n % 2 else "emperor",
            pclass="direct",
            pinned_reason=reason,
        )
        line_tokens = _tokens(line)
        if keepsake_tokens + line_tokens > 4000:
            break
        keepsakes.append(_keptake_row(n, reason))
        keepsake_tokens += line_tokens
    assert keepsake_tokens <= 4000

    body = render_briefing_body(
        persona_body=persona, keepsakes=keepsakes, episodes=[],
        provenance_class="direct",
    )
    assert body is not None

    # stable zone = everything before the affordance block separator
    stable_chars = len(body) - (len(MEMOIR_AFFORDANCE_LINE) + len("\n\n"))
    total_tokens = persona_tokens + keepsake_tokens
    ratio = stable_chars / total_tokens

    # THE INVARIANT: the admitted worst case never reaches the withhold
    # bound — the stable zone always ships, never truncated, never withheld.
    assert stable_chars < MEMOIR_SECTION_CHAR_ABSOLUTE, (
        f"N-3 INVARIANT BROKEN: the admitted worst case ({stable_chars} chars) "
        f"breaches the 48,000-char withhold bound — DR-1's arithmetic is wrong"
    )

    # THE MEASUREMENT REPORT (the pack log carries it):
    disclosure_fired = stable_chars > MEMOIR_SECTION_CHAR_CEILING
    expected_dynamic_chars = max(0, MEMOIR_SECTION_CHAR_CEILING - stable_chars)
    sample_line = render_dynamic_line(
        SimpleNamespace(title="A typical episode title", body=_EN,
                        event_at=FIXED_NOW)
    )
    sample_chars_per_token = len(sample_line) / max(1, _tokens(sample_line))
    print(
        "\n[N-3 MEASUREMENT] admitted stable worst case: "
        f"persona={persona_tokens}t keepsakes={keepsake_tokens}t "
        f"({len(keepsakes)} pins) total={total_tokens}t -> shipped stable "
        f"zone={stable_chars} chars (escaped) | measured char/token "
        f"ratio={ratio:.3f} (DR-1 assumed 4.0) | 24k disclosure fired="
        f"{disclosure_fired} | expected dynamic-zone survival at full "
        f"stable admission={expected_dynamic_chars} chars "
        f"(≈{int(expected_dynamic_chars / sample_chars_per_token)} tokens "
        f"of dynamic zone); at HALF stable admission the dynamic zone "
        f"survives with ≈{max(0, MEMOIR_SECTION_CHAR_CEILING - stable_chars // 2)} "
        f"chars of headroom"
    )
    # the ratio lands in the English-prose band (instrument sanity)
    assert 3.0 < ratio < 6.5, f"estimator ratio out of band: {ratio:.3f}"
    # the backstop behaviour matches the measurement EXACTLY
    if disclosure_fired:
        assert "pin admission exceeded its budget" in body
        assert "## Recent episodes" not in body
    else:
        assert "pin admission exceeded its budget" not in body


# ── the knob gate + the presence predicate ────────────────────────────────


def test_knob_allows_reads_the_config_tier(monkeypatch):
    monkeypatch.delenv("ENSEMBLE_MEMOIR_ENABLED", raising=False)
    off = SimpleNamespace(memoir=SimpleNamespace(enabled=False))
    on = SimpleNamespace(memoir=SimpleNamespace(enabled=True))
    assert knob_allows(off) is False
    assert knob_allows(on) is True


def test_knob_env_explicit_off(monkeypatch):
    monkeypatch.setenv("ENSEMBLE_MEMOIR_ENABLED", "false")
    assert knob_allows(None) is False


def test_memoir_block_present_predicate():
    """D5.4: the consumer confirms presence by kind OR stable id."""
    kind_match = SimpleNamespace(
        id="whatever", additional_kwargs={"context_kind": "memoir"}
    )
    stable_match = SimpleNamespace(
        id="memoir:ari", additional_kwargs={"context_kind": "other"}
    )
    bystander = SimpleNamespace(
        id="project:x", additional_kwargs={"context_kind": "project"}
    )
    assert memoir_block_present([kind_match]) is True
    assert memoir_block_present([bystander, stable_match], "ari") is True
    assert memoir_block_present([bystander], "ari") is False
    assert memoir_block_present(None) is False
    # without an agent id the kind match still fires (a failed row read
    # must not strand the flag when the block is visibly present)
    assert memoir_block_present([stable_match], None) is False
    assert memoir_block_present([kind_match], None) is True


# ── the builder ───────────────────────────────────────────────────────────


def test_builder_renders_through_the_real_repo(repo, fake_manager):
    """The builder wraps the caller's engine and renders the real rows."""
    repo.seed_persona_if_absent(MEMOIR_AGENT_ID)
    with Session(repo.engine) as session:
        session.add(
            _episode("ep-1", title="Deploy night", body="the loop lag fell")
        )
        session.commit()
    message = asyncio.run(
        _build_memoir_briefing_message(MEMOIR_AGENT_ID, "inst-9", fake_manager)
    )
    assert message is not None
    assert message.id == "memoir:ari"
    assert "## Persona" in message.content
    assert "Deploy night" in message.content


def test_builder_degrades_without_engine_or_agent():
    """D5.14: a missing engine / empty agent id ⇒ ``None`` (swallow + skip)."""
    assert asyncio.run(_build_memoir_briefing_message(MEMOIR_AGENT_ID, "i", SimpleNamespace())) is None
    assert asyncio.run(_build_memoir_briefing_message("", "i", SimpleNamespace(_engine=object()))) is None

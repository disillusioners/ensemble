"""Memoir Store v2 — the POIGNANCY SCORER (the SQLite lane).

Phase 4 of the Memoir Store v2 lane (plan:
``.agents/shared/planning/ari-memoir-store/v2-phase4-plan.md`` T4.1/T4.4;
AC1/AC4; pack ``test/packs/memoir_v2_poignancy_service_test.sh``).

What this suite pins:

* the PROMPT — the title, the event time, the body and the star tags
  are all present, and the scale anchors are stated;
* the TOLERANT SCORE PARSER — a bare integer, a JSON envelope and a
  prose answer all parse; the result is CLAMPED strictly to 1..10; an
  answer with no number is a PARSE FAILURE (AC3's ``parse_failure``);
* the FAILURE CLASSIFICATION (V2-D 4.6) — a 401/403 is an IMMEDIATE
  terminal, a transient status is not, a permanent status is, and a
  connection error is not;
* the IMPORTANCE RULING — the score write-back touches ``poignancy``
  and NEVER ``importance`` (the write-frozen authored record);
* the HOUSE LLM CALL — the SDK's own retry is OFF (``max_retries=0``)
  and the raw config dict KEEPS ``base_url_backup`` (without it the
  failover can never swap);
* the KNOB TRUTH TABLE — unset ⇒ ON + a loud WARNING, garbage ⇒ OFF
  (fail closed), and a garbage numeric CLAMPS without ever raising
  (the daemon NEVER aborts on a mis-set tuning knob).
"""

from __future__ import annotations

import logging
import pathlib
from datetime import datetime, timezone

import pytest
from sqlmodel import SQLModel, create_engine

from daemon.repositories.memoir.repository import MemoirRepository
from daemon.services.memoir_poignancy_service import (
    MEMOIR_POIGNANCY_MAX,
    MEMOIR_POIGNANCY_MIN,
    MemoirPoignancyService,
    build_poignancy_prompt,
    classify_poignancy_failure,
    parse_poignancy_score,
    resolve_poignancy_settings,
)

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

AGENT = "ari"

KNOB_LOGGER = "daemon.tools.memoir_knob"

_ALL_KNOBS = (
    "ENSEMBLE_MEMOIR_POIGNANCY_ENABLED",
    "ENSEMBLE_MEMOIR_POIGNANCY_BACKFILL_MAX",
    "ENSEMBLE_MEMOIR_POIGNANCY_TICK_S",
    "ENSEMBLE_MEMOIR_POIGNANCY_BATCH_CAP",
)

SERVICE_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "daemon"
    / "services"
    / "memoir_poignancy_service.py"
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """The env donor — the knob family is scrubbed for every test."""
    for name in _ALL_KNOBS:
        monkeypatch.delenv(name, raising=False)
    from daemon.config import _reset_v2_knob_warnings
    from daemon.tools.memoir_knob import reset_boot_log_state

    _reset_v2_knob_warnings()
    reset_boot_log_state()
    yield
    _reset_v2_knob_warnings()
    reset_boot_log_state()


@pytest.fixture()
def engine(tmp_path):
    # A FILE-backed SQLite database (the house pattern for the worker
    # lane — ``test_memoir_embedding_worker_unit.py``): the lane runs on
    # a DEDICATED executor thread, and an in-memory SQLite database is
    # per-THREAD (a second thread gets a second, EMPTY database).
    eng = create_engine(f"sqlite:///{tmp_path}/memoir-poignancy-service.db")
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def repo(engine):
    return MemoirRepository(engine)


def _service(repo, *, answer="7", calls=None):
    """A service with an INJECTED LLM caller (no provider, no network)."""

    def _caller(prompt: str) -> str:
        if calls is not None:
            calls.append(prompt)
        if isinstance(answer, Exception):
            raise answer
        return answer

    return MemoirPoignancyService(
        repository=repo,
        config=None,
        llm_config={"base_url": "http://example.invalid/v1", "model": "m"},
        llm_caller=_caller,
    )


def _episode(repo, **kwargs):
    return repo.insert_episode(
        agent_id=kwargs.pop("agent_id", AGENT),
        body=kwargs.pop("body", "a small operational moment"),
        title=kwargs.pop("title", "a title"),
        **kwargs,
    )


# ── the prompt ───────────────────────────────────────────────────────────


def test_the_prompt_carries_the_title_event_time_body_and_stars(repo):
    """§3.2: the prompt contains the title, the event time, the body and
    the star tags."""
    episode_id = _episode(
        repo,
        title="the decree",
        body="the constitution was rewritten today",
        event_at=datetime(2026, 11, 9, 3, 0, tzinfo=timezone.utc),
        stars=["constitution", "decree"],
    )
    from sqlmodel import Session, select

    from daemon.repositories.memoir.models import MemoirEpisode

    with Session(repo.engine) as session:
        row = session.exec(
            select(MemoirEpisode).where(MemoirEpisode.id == episode_id)
        ).first()

    prompt = build_poignancy_prompt(row)
    assert "the decree" in prompt
    assert "the constitution was rewritten today" in prompt
    assert "2026-11-09T03:00:00" in prompt
    assert "constitution" in prompt and "decree" in prompt


def test_the_prompt_states_the_scale_and_asks_for_one_integer(repo):
    """V2-D 4.4: the scale anchors are stated and the answer format is
    unambiguous."""
    episode_id = _episode(repo)
    from sqlmodel import Session, select

    from daemon.repositories.memoir.models import MemoirEpisode

    with Session(repo.engine) as session:
        row = session.exec(
            select(MemoirEpisode).where(MemoirEpisode.id == episode_id)
        ).first()

    prompt = build_poignancy_prompt(row)
    assert "1 = trivial pleasantry" in prompt
    assert "10 = constitutional decree" in prompt
    assert "ONE integer from 1 to 10" in prompt


# ── the tolerant score parser ────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("7", 7),
        (" 8 ", 8),
        ("10", 10),
        ("1", 1),
        ('{"score": 6}', 6),
        ('{"poignancy": 4}', 4),
        ("score: 9", 9),
        ("I would rate this a 5.", 5),
        ("Score = 3", 3),
    ],
)
def test_the_parser_is_tolerant(raw, expected):
    assert parse_poignancy_score(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("0", MEMOIR_POIGNANCY_MIN),
        ("-3", MEMOIR_POIGNANCY_MIN),
        ("11", MEMOIR_POIGNANCY_MAX),
        ("42", MEMOIR_POIGNANCY_MAX),
    ],
)
def test_the_score_is_clamped_strictly_to_the_one_to_ten_band(raw, expected):
    """V2-D 4.4: the clamp is strict — the stored value is always 1..10."""
    assert parse_poignancy_score(raw) == expected


def test_a_fractional_answer_is_rounded_to_an_integer():
    """The STORED value is an integer (the column's contract) — a
    fractional answer is rounded, never stored as a float."""
    score = parse_poignancy_score("7.4")
    assert score == 7
    assert isinstance(score, int)
    assert parse_poignancy_score("7.6") == 8


@pytest.mark.parametrize("raw", ["", "   ", "no idea", "the memories matter"])
def test_an_answer_without_a_number_is_a_parse_failure(raw):
    """AC3's ``parse_failure``: the model answered, but not with a score."""
    assert parse_poignancy_score(raw) is None


# ── the failure classification (V2-D 4.6) ────────────────────────────────


class _FakeStatusError(Exception):
    """An exception carrying an HTTP status (the SDK's shape)."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code


def test_the_auth_status_is_an_immediate_terminal():
    """A dead credential must not burn a row's attempt budget."""
    for status in (401, 403):
        reason, immediate = classify_poignancy_failure(_FakeStatusError(status))
        assert immediate is True
        assert reason == "poignancy_auth_rejected"


def test_a_transient_status_is_not_terminal():
    reason, immediate = classify_poignancy_failure(_FakeStatusError(429))
    assert immediate is False
    assert reason == "poignancy_status_transient"


def test_a_request_timeout_is_transient():
    """408 is widened lane-locally (the house retryable set + 408)."""
    reason, immediate = classify_poignancy_failure(_FakeStatusError(408))
    assert immediate is False
    assert reason == "poignancy_status_transient"


def test_a_permanent_status_is_an_immediate_terminal():
    reason, immediate = classify_poignancy_failure(_FakeStatusError(400))
    assert immediate is True
    assert reason == "poignancy_status_permanent"


def test_a_connection_error_is_not_terminal():
    import httpx

    reason, immediate = classify_poignancy_failure(
        httpx.ConnectError("the lane is down")
    )
    assert immediate is False
    assert reason == "poignancy_connection_error"


def test_the_generic_failure_is_not_immediate_terminal():
    """The generic case counts an attempt and terminalizes at the cap
    (AC3) — it must not skip the budget."""
    reason, immediate = classify_poignancy_failure(RuntimeError("boom"))
    assert immediate is False
    assert reason == "poignancy_error"


# ── the write-back (the importance ruling) ───────────────────────────────


def test_the_score_write_back_leaves_the_authored_importance_untouched(repo):
    """V2-10 / §3-A2: ``importance`` is WRITE-FROZEN.

    This is the behavioural half of AC2: the score lands in ``poignancy``
    and the authored importance is UNCHANGED.
    """
    episode_id = _episode(repo, importance=7)
    service = _service(repo, answer="9")
    from sqlmodel import Session, select

    from daemon.repositories.memoir.models import MemoirEpisode

    with Session(repo.engine) as session:
        row = session.exec(
            select(MemoirEpisode).where(MemoirEpisode.id == episode_id)
        ).first()
        before = int(row.importance)

    service._repository.record_poignancy_score(episode_id, 9)

    with Session(repo.engine) as session:
        row = session.exec(
            select(MemoirEpisode).where(MemoirEpisode.id == episode_id)
        ).first()
        assert row.poignancy == 9
        assert row.poignancy_status == "scored"
        assert int(row.importance) == before == 7


def test_the_score_write_back_clamps_an_out_of_band_score(repo):
    """The column's CHECK constraint is the last line of defence — a
    caller that bypasses the scorer must not store a 0 or an 11."""
    episode_id = _episode(repo)
    repo.record_poignancy_score(episode_id, 99)
    from sqlmodel import Session, select

    from daemon.repositories.memoir.models import MemoirEpisode

    with Session(repo.engine) as session:
        row = session.exec(
            select(MemoirEpisode).where(MemoirEpisode.id == episode_id)
        ).first()
        assert row.poignancy == 10


def test_a_scored_episode_leaves_the_queue(repo):
    """A scored row is no longer pending (the queue is the column's
    emptiness)."""
    episode_id = _episode(repo)
    assert len(repo.get_pending_poignancy_episodes()) == 1
    repo.record_poignancy_score(episode_id, 5)
    assert repo.get_pending_poignancy_episodes() == []


def test_the_scored_episode_reports_the_score(repo):
    """The service's own outcome carries the score it stored."""
    episode_id = _episode(repo)
    service = _service(repo, answer="6")
    from sqlmodel import Session, select

    from daemon.repositories.memoir.models import MemoirEpisode

    with Session(repo.engine) as session:
        row = session.exec(
            select(MemoirEpisode).where(MemoirEpisode.id == episode_id)
        ).first()

    outcome = service.score_episode(row)
    assert outcome.scored is True
    assert outcome.score == 6
    assert outcome.prompt_tokens > 0


# ── the house LLM call ───────────────────────────────────────────────────


#: The fake client's construction kwargs (module-level so the fake class
#: — defined at module scope — can reach the recorder).
_CLIENT_KWARGS: dict = {}


class _FakeCompletions:
    """The fake SDK's ``chat.completions`` surface."""

    def create(self, **kwargs):
        class _Message:
            content = "5"

        class _Choice:
            message = _Message()

        class _Response:
            choices = [_Choice()]

        return _Response()


class _FakeClient:
    """The fake SDK client — it records the construction kwargs."""

    def __init__(self, **kwargs):
        _CLIENT_KWARGS.update(kwargs)
        self.chat = type("_Chat", (), {"completions": _FakeCompletions()})()


def test_the_house_client_disables_the_sdk_retry_and_keeps_the_backup_url(
    repo, monkeypatch
):
    """THE HTTP-REQUEST CEILING depends on this.

    * ``max_retries=0`` — the SDK's own retry is OFF, so the facade's
      6-attempt ladder (3 without a backup) is the ONLY ladder and one
      attempt is exactly ONE HTTP request;
    * ``base_url_backup`` — the raw dict keeps it, so the HA facade can
      actually swap to the backup URL.
    """
    captured: dict = {}
    _CLIENT_KWARGS.clear()

    # THE TRUE CEILING, read from the live code (not from memory) — and
    # read BEFORE the facade is patched, so the signature is the REAL
    # one and not the test double's.
    import inspect

    from daemon.llm_error_classifier import derive_ha_attempt_ceiling
    from daemon.services.llm_failover import invoke_raw_with_failover

    signature = inspect.signature(invoke_raw_with_failover)
    assert signature.parameters["transient_max"].default == 3
    assert signature.parameters["timeout_max"].default == 2
    assert derive_ha_attempt_ceiling(3, 2, failover_active=False) == 3
    assert derive_ha_attempt_ceiling(3, 2, failover_active=True) == 6

    import openai

    monkeypatch.setattr(openai, "OpenAI", _FakeClient)

    def _fake_invoke(factory, llm_config, **kwargs):
        captured["llm_config"] = dict(llm_config)
        captured["factory_kwargs"] = kwargs
        return factory()

    import daemon.services.llm_failover as failover

    monkeypatch.setattr(failover, "invoke_raw_with_failover", _fake_invoke)
    monkeypatch.setattr(failover, "current_failover_url", lambda: None)

    service = MemoirPoignancyService(
        repository=repo,
        config=None,
        llm_config={
            "base_url": "http://primary.invalid/v1",
            "base_url_backup": "http://backup.invalid/v1",
            "api_key": "k",
            "model": "m",
        },
    )
    assert service._house_llm_call("hello") == "5"

    assert _CLIENT_KWARGS["max_retries"] == 0
    assert captured["llm_config"].get("base_url_backup") == (
        "http://backup.invalid/v1"
    )
    # The service does NOT override the facade's budget — the house
    # defaults ARE the ceiling.
    assert captured["factory_kwargs"] == {}


# ── the knob truth table (AC4 / V2-D 4.7 / 4.9) ─────────────────────────


def test_the_unset_kill_switch_resolves_on_with_a_loud_warning(caplog):
    """The unset case is NEVER silent — the operator sees it."""
    from daemon.tools.memoir_knob import (
        reset_boot_log_state,
        resolve_memoir_poignancy_knob,
    )

    reset_boot_log_state()
    with caplog.at_level(logging.WARNING, logger=KNOB_LOGGER):
        state = resolve_memoir_poignancy_knob()
    assert state.enabled is True
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "ENSEMBLE_MEMOIR_POIGNANCY_ENABLED" in text
    assert "unset" in text


def test_a_garbage_kill_switch_fails_closed_off(caplog):
    """DR-3: an indeterminate value fails CLOSED."""
    from daemon.tools.memoir_knob import (
        reset_boot_log_state,
        resolve_memoir_poignancy_knob,
    )

    reset_boot_log_state()
    with caplog.at_level(logging.WARNING, logger=KNOB_LOGGER):
        state = resolve_memoir_poignancy_knob()
    assert state.enabled is True

    import os

    os.environ["ENSEMBLE_MEMOIR_POIGNANCY_ENABLED"] = "maybe"
    try:
        reset_boot_log_state()
        with caplog.at_level(logging.WARNING, logger=KNOB_LOGGER):
            state = resolve_memoir_poignancy_knob()
        assert state.enabled is False
        text = "\n".join(record.getMessage() for record in caplog.records)
        assert "maybe" in text
    finally:
        os.environ.pop("ENSEMBLE_MEMOIR_POIGNANCY_ENABLED", None)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("true", True),
        ("TRUE", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("false", False),
        ("FALSE", False),
        ("0", False),
        ("no", False),
        ("off", False),
    ],
)
def test_the_canonical_spellings_resolve(monkeypatch, raw, expected):
    monkeypatch.setenv("ENSEMBLE_MEMOIR_POIGNANCY_ENABLED", raw)
    assert resolve_poignancy_settings().enabled is expected


def test_a_garbage_numeric_knob_clamps_without_raising(monkeypatch):
    """THE LOAD-BEARING PROPERTY: the daemon NEVER aborts.

    A bare ``Field(gt=...)`` on a numeric raises ``ValidationError`` on an
    unparseable env var and takes the daemon DOWN (the house anomaly
    ``33028b27``). Every knob here clamps instead.
    """
    from daemon.config import MemoirPoignancyConfig

    monkeypatch.setenv("ENSEMBLE_MEMOIR_POIGNANCY_TICK_S", "not-a-number")
    monkeypatch.setenv("ENSEMBLE_MEMOIR_POIGNANCY_BATCH_CAP", "2.5")
    monkeypatch.setenv("ENSEMBLE_MEMOIR_POIGNANCY_BACKFILL_MAX", "banana")

    section = MemoirPoignancyConfig()
    assert section.tick_s == 20
    assert section.batch_cap == 10
    assert section.backfill_max == 0

    settings = resolve_poignancy_settings()
    assert settings.tick_interval_s == 20
    assert settings.batch_cap == 10
    assert settings.backfill_max == 0


def test_a_negative_backfill_budget_clamps_to_zero(monkeypatch):
    """The floor is ZERO — a negative budget never becomes a storm."""
    from daemon.config import MemoirPoignancyConfig

    monkeypatch.setenv("ENSEMBLE_MEMOIR_POIGNANCY_BACKFILL_MAX", "-5")
    assert MemoirPoignancyConfig().backfill_max == 0


def test_the_numeric_knobs_are_read_live_at_call_time(monkeypatch):
    """The per-tick reader re-reads the ENV — a mid-run change takes
    effect on the next tick without a restart."""
    assert resolve_poignancy_settings().tick_interval_s == 20
    monkeypatch.setenv("ENSEMBLE_MEMOIR_POIGNANCY_TICK_S", "45")
    assert resolve_poignancy_settings().tick_interval_s == 45


def test_the_section_is_isolated_from_the_reflection_section():
    """N1 / V2-D 4.9: the lane reads its OWN section."""
    from daemon.config import MemoirPoignancyConfig, MemoirReflectionConfig

    assert MemoirPoignancyConfig().model_config["env_prefix"] == (
        "ENSEMBLE_MEMOIR_POIGNANCY_"
    )
    assert MemoirReflectionConfig().model_config["env_prefix"] == (
        "ENSEMBLE_MEMOIR_REFLECTION_"
    )


def test_the_lane_never_calls_the_global_knob_resolver():
    """THE ISOLATION PIN: the lane's module must not reach for the
    global (``resolve_memoir_knob``) resolver — it reads its OWN
    section. A global-resolver call here would couple the scoring lane
    to the whole memoir surface's kill-switch.

    The pin is AST-based on purpose: a prose match would trip on the
    module's own DOCSTRING (which names the resolver to say that it is
    NOT called). Only a real NAME/ATTRIBUTE reference counts.
    """
    import ast

    tree = ast.parse(SERVICE_PATH.read_text(encoding="utf-8"))
    references = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "resolve_memoir_knob":
            references.append(node.lineno)
        if isinstance(node, ast.Attribute) and node.attr == "resolve_memoir_knob":
            references.append(node.lineno)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name for alias in node.names]
            if any(name.endswith("resolve_memoir_knob") for name in names):
                references.append(node.lineno)
    assert references == [], (
        "the poignancy lane references the global knob resolver at lines "
        f"{references} — it must read its own isolated section"
    )

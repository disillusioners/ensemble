"""The nest-anomaly SCRUB fence — what is allowed to reach the database.

Covers the R6 + AM-11 scrub contract:

* a planted, NON-STORED secret is ABSENT from every persisted TEXT
  column — three shapes, because the masker is the ONLY defence for a
  non-stored value (the exact-match scrubber knows only STORED values):
  a ``sk-proj-…`` provider key, a JSON-shaped secret, and a MULTI-WORD
  QUOTED passphrase;
* the passphrase is masked WHOLE (E1) — the FULL value is absent, and
  so is every WORD of it (a first-word-only needle would still pass on
  a partial mask);
* the excerpt's ``excerpt_sha256`` / ``excerpt_length`` describe the
  STORED excerpt;
* ``redact_exc_str`` never raises (the house contract), and the limit
  is applied AFTER the redaction (a cap-first pipeline would cut a
  straddling secret into an unmatchable fragment and let it survive);
* the AM-11 SPLIT posture: with the value scrubber ABSENT the excerpt
  is FAIL-CLOSED (NULL + WARNING) while description / suspected_cause
  stay FAIL-OPEN (pattern-masked + WARNING).

Mutation legs (phase6 §(b)):

* **M-1c** — swap the scrub order (redact → cap → scrub becomes
  cap → redact) → RED.
* **M-1d** — replace ``redact_exc_str`` with ``str(exc)`` → RED.
"""

from __future__ import annotations

import hashlib
import json
import logging

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

from daemon import _redact
from daemon._redact import redact_exc_str
from daemon.config import NestReportConfig
from daemon.repositories.anomaly.models import AnomalyCluster, AnomalyReport
from daemon.services import anomaly_service
from daemon.services.anomaly_service import AnomalyService, scrub_text

# ── the planted, NON-STORED secrets ──────────────────────────────────────
#
# NONE of these is a stored secret: the exact-match scrubber knows only
# stored values, so a planted ``secret_store`` value would prove nothing
# about the real risk (the pattern masker is the SOLE defence here).

SK_SECRET = "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWX"
JSON_SECRET_VALUE = "hunter2"
PASSPHRASE = "correct horse battery staple"
PASSPHRASE_WORDS = ("correct", "horse", "battery", "staple")

#: Every persisted TEXT column of the report row.
REPORT_TEXT_COLUMNS = (
    "id",
    "created_at",
    "agent_id",
    "instance_id",
    "tree_root_instance_id",
    "category",
    "category_raw",
    "severity",
    "description",
    "suspected_cause",
    "excerpt",
    "excerpt_sha256",
    "job_id",
    "mission_id",
    "boot_epoch",
    "registry_summary_sha256",
    "registry_captured_at",
    "cluster_id",
    "outcome",
    "refusal_reason",
)

#: The JSON columns — asserted on their serialized form.
REPORT_JSON_COLUMNS = (
    "parent_chain",
    "parent_chain_agents",
    "dispatch_chain",
    "wedge_capture_ids",
)


class _Boom:
    """An exception whose ``__str__`` raises (the never-raises probe)."""

    def __str__(self) -> str:
        raise RuntimeError("boom")


class _StubInstanceRepo:
    def get_ancestor_ids(self, instance_id: str) -> list[str]:
        return []

    def get_agent_ids_for(self, instance_ids: list[str]) -> dict[str, str | None]:
        return {iid: "" for iid in instance_ids}


class _StubTaskRepo:
    def find_running_by_instance(self, instance_id: str):
        return None


class _StubJobRepo:
    def get_active_by_instance(self, instance_id: str, job_id: str | None = None):
        return None


class _StubMissionRepo:
    def find_outstanding_commission_for_worker(self, instance_id: str, **kwargs):
        return None


class _StubWedgeRepo:
    def list_recent(self, limit: int = 50):
        return []


class _FakeRawSaver:
    def __init__(self, messages: list[object]) -> None:
        self._messages = messages
        self.calls: list[dict] = []

    async def aget(self, config: dict) -> dict:
        self.calls.append(config)
        return {"channel_values": {"messages": list(self._messages)}}


class _FakeCheckpointer:
    def __init__(self, messages: list[object]) -> None:
        self.raw_saver = _FakeRawSaver(messages)


class _FakeManager:
    def __init__(self, messages: list[object] | None = None) -> None:
        self.checkpointer = _FakeCheckpointer(messages or [])
        self.metrics_query_provider = None
        self._instance_repository = _StubInstanceRepo()
        self._task_repo = _StubTaskRepo()
        self._job_queue_service = type(
            "_Jqs", (), {"repository": _StubJobRepo()}
        )()
        self._mission_record_service = type(
            "_Mrs", (), {"commission_repository": lambda self=None: _StubMissionRepo()}
        )()
        self._wedge_capture_repository = _StubWedgeRepo()


class _FakeToolMessage:
    """A LangChain-shaped TOOL message (it carries ``.type``, not ``.role``)."""

    type = "tool"

    def __init__(self, content: str) -> None:
        self.content = content


async def _passthrough(fn, *args, **kwargs):
    """The offload seam replaced by a direct call (no executor)."""
    return fn(*args, **kwargs)


def _engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'anomaly_scrub.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(
        engine, tables=[AnomalyReport.__table__, AnomalyCluster.__table__]
    )
    return engine


def _service(engine, messages: list[object] | None = None) -> AnomalyService:
    return AnomalyService(
        engine,
        config=NestReportConfig(),
        manager=_FakeManager(messages or []),
        offload=_passthrough,
    )


def _row(engine, report_id: str) -> AnomalyReport:
    with Session(engine) as session:
        row = session.get(AnomalyReport, report_id)
        assert row is not None
        session.expunge(row)
        return row


def _persisted_texts(row: AnomalyReport) -> dict[str, str]:
    """Every persisted TEXT-ish value, keyed by column name."""
    values: dict[str, str] = {}
    for name in REPORT_TEXT_COLUMNS:
        value = getattr(row, name, None)
        values[name] = "" if value is None else str(value)
    for name in REPORT_JSON_COLUMNS:
        value = getattr(row, name, None)
        values[name] = "" if value is None else json.dumps(value, sort_keys=True)
    return values


PLANTED_DESCRIPTION = (
    "the write path failed with {sk}; the config said {json}; "
    "and the passphrase was {phrase}".format(
        sk=SK_SECRET,
        json=json.dumps({"password": JSON_SECRET_VALUE}),
        phrase=json.dumps({"password": PASSPHRASE}),
    )
)

PLANTED_TOOL_MESSAGE = (
    "ERROR: write failed {sk} {json} {phrase}".format(
        sk=SK_SECRET,
        json=json.dumps({"password": JSON_SECRET_VALUE}),
        phrase=json.dumps({"password": PASSPHRASE}),
    )
)


@pytest.fixture(autouse=True)
def _scrubber_present(monkeypatch):
    """A value scrubber is present unless a leg says otherwise."""
    monkeypatch.setattr(
        anomaly_service, "get_process_scrubber", lambda: object(), raising=True
    )


class TestThePlantedSecrets:
    async def test_no_planted_secret_reaches_any_persisted_column(
        self, tmp_path, monkeypatch
    ) -> None:
        engine = _engine(tmp_path)
        service = _service(engine, [_FakeToolMessage(PLANTED_TOOL_MESSAGE)])
        result = await service.accept_report(
            agent_id="worker",
            instance_id="inst-1",
            category="tool failure",
            description=PLANTED_DESCRIPTION,
            severity="major",
            suspected_cause=PLANTED_DESCRIPTION,
        )
        row = _row(engine, result.report_id)
        texts = _persisted_texts(row)
        for column, value in texts.items():
            assert SK_SECRET not in value, column
            assert JSON_SECRET_VALUE not in value, column
            assert PASSPHRASE not in value, column
            # E1: the passphrase must be masked WHOLE — a first-word-only
            # needle would still pass on a partial mask.
            for word in PASSPHRASE_WORDS:
                assert word not in value, f"{column}: {word} survived"

    async def test_the_raw_category_is_preserved_even_though_it_folded(
        self, tmp_path
    ) -> None:
        engine = _engine(tmp_path)
        service = _service(engine)
        result = await service.accept_report(
            agent_id="worker",
            instance_id="inst-1",
            category="tool failure",
            description="boom",
            severity="major",
        )
        row = _row(engine, result.report_id)
        assert row.category == "tool-failure"
        assert row.category_raw == "tool failure"
        assert result.fold_notice is not None


class TestTheExcerptIntegrity:
    async def test_the_excerpt_hash_and_length_describe_the_stored_excerpt(
        self, tmp_path
    ) -> None:
        engine = _engine(tmp_path)
        service = _service(engine, [_FakeToolMessage("ERROR: the tool refused")])
        result = await service.accept_report(
            agent_id="worker",
            instance_id="inst-1",
            category="tool-failure",
            description="boom",
            severity="major",
        )
        row = _row(engine, result.report_id)
        assert row.excerpt is not None
        assert row.excerpt_sha256 == hashlib.sha256(
            row.excerpt.encode("utf-8")
        ).hexdigest()
        assert row.excerpt_length == len(row.excerpt)
        assert result.excerpt_sha256 == row.excerpt_sha256
        assert result.excerpt_length == row.excerpt_length

    async def test_the_excerpt_carries_no_planted_secret(self, tmp_path) -> None:
        engine = _engine(tmp_path)
        service = _service(engine, [_FakeToolMessage(PLANTED_TOOL_MESSAGE)])
        result = await service.accept_report(
            agent_id="worker",
            instance_id="inst-1",
            category="tool-failure",
            description="boom",
            severity="major",
        )
        row = _row(engine, result.report_id)
        assert row.excerpt is not None
        assert SK_SECRET not in row.excerpt
        assert JSON_SECRET_VALUE not in row.excerpt
        assert PASSPHRASE not in row.excerpt
        for word in PASSPHRASE_WORDS:
            assert word not in row.excerpt


class TestTheSplitPosture:
    async def test_an_absent_scrubber_closes_the_excerpt_and_keeps_the_text(
        self, tmp_path, monkeypatch, caplog
    ) -> None:
        monkeypatch.setattr(
            anomaly_service, "get_process_scrubber", lambda: None, raising=True
        )
        engine = _engine(tmp_path)
        service = _service(engine, [_FakeToolMessage(PLANTED_TOOL_MESSAGE)])
        with caplog.at_level(logging.WARNING):
            result = await service.accept_report(
                agent_id="worker",
                instance_id="inst-1",
                category="tool-failure",
                description=PLANTED_DESCRIPTION,
                severity="major",
                suspected_cause=PLANTED_DESCRIPTION,
            )
        row = _row(engine, result.report_id)
        # FAIL-CLOSED leg: the excerpt (raw tool stdout) is withheld.
        assert row.excerpt is None
        assert row.excerpt_sha256 is None
        assert row.excerpt_length is None
        # FAIL-OPEN leg: the authored text still persists, pattern-masked.
        assert row.description
        assert row.suspected_cause
        assert SK_SECRET not in row.description
        assert PASSPHRASE not in row.description
        for word in PASSPHRASE_WORDS:
            assert word not in row.description
        # Both legs WARN.
        warnings = " ".join(record.getMessage() for record in caplog.records)
        assert "excerpt withheld" in warnings


class TestTheScrubPipeline:
    def test_redact_exc_str_never_raises(self) -> None:
        out = redact_exc_str(_Boom(), limit=100)
        assert isinstance(out, str)
        assert out

    def test_the_pipeline_never_raises_for_an_unstringifiable_input(self) -> None:
        out = scrub_text(_Boom(), 100)
        assert isinstance(out, str)

    def test_the_limit_is_applied_after_the_redaction(self) -> None:
        """The cap must NOT pre-empt the masker.

        A cap applied FIRST would cut the secret into an unmatchable
        fragment (``sk-proj-AB`` no longer matches the 8+-character
        provider-key shape) and the fragment would survive into the
        database.
        """
        out = scrub_text(SK_SECRET + " trailing", 10)
        assert "sk-proj-" not in out
        assert len(out) == 10

    def test_the_limit_is_applied_after_the_redaction_for_a_passphrase(self) -> None:
        payload = json.dumps({"password": PASSPHRASE})
        out = scrub_text(payload, 30)
        for word in PASSPHRASE_WORDS:
            assert word not in out

    def test_the_scrubbed_text_never_exceeds_the_cap(self) -> None:
        out = scrub_text("x" * 500, 32)
        assert len(out) == 32

    def test_the_secret_shape_masker_leaves_benign_prose_alone(self) -> None:
        text = "the token expired because the password policy rotated"
        assert scrub_text(text, 200) == text

    def test_the_secret_shape_masker_keeps_the_token_metering_fields(self) -> None:
        # E3: the token-metering diagnostics are the text a
        # token-exhaustion error carries — masking them would destroy
        # the diagnostic value the excerpt exists to capture.
        for name in ("max_tokens", "input_tokens", "output_tokens", "token_count"):
            payload = json.dumps({name: 200000})
            assert name in scrub_text(payload, 200)
        assert "token_budget" in scrub_text("token_budget: 200000", 200)

    def test_the_secret_shape_masker_masks_the_widened_keyword_family(self) -> None:
        # R-a: the keyword FAMILY masks (the suffix legs).
        for name in ("secret_note", "password_hint", "client_secret_id"):
            payload = json.dumps({name: "value-xyz"})
            assert "value-xyz" not in scrub_text(payload, 200)

    def test_the_secret_shape_masker_is_the_sole_defence_for_non_stored_values(
        self,
    ) -> None:
        # C1c: scrubber PRESENCE is irrelevant to this class — the
        # pattern masker alone must carry it.
        assert SK_SECRET not in scrub_text(f"token={SK_SECRET}", 500)


class TestTheHouseMaskerContract:
    def test_the_house_masker_is_present_and_additive(self) -> None:
        assert hasattr(_redact, "redact_secret_shapes")
        assert hasattr(_redact, "redact_exc_str")
        # The house masker keeps its own behaviour (AM-11: UNTOUCHED).
        assert redact_exc_str("postgresql://u:p@h:5432/db", limit=200) == (
            "postgresql://***/db"
        )


class TestTheAcceptPathContract:
    """AC1 / AC2 / AC3 — the service-side halves of the phase-1 contract.

    The TOOL signature + the refusal ENVELOPE land in C2 (the tool
    body); what is pinned here is the SERVICE half: the caps truncate
    (never refuse), the authored-content refusals persist NOTHING, and
    the fold notice appears exactly when a fold happened.
    """

    async def test_over_length_authored_text_truncates_and_never_refuses(
        self, tmp_path
    ) -> None:
        engine = _engine(tmp_path)
        service = _service(engine)
        result = await service.accept_report(
            agent_id="worker",
            instance_id="inst-1",
            category="tool-failure",
            description="d" * 5000,
            severity="major",
            suspected_cause="c" * 2000,
        )
        row = _row(engine, result.report_id)
        config = NestReportConfig()
        assert len(row.description) == config.description_max_chars
        assert len(row.suspected_cause) == config.suspected_cause_max_chars
        assert result.truncated is True

    async def test_the_raw_category_is_capped_at_sixty_four_chars(self, tmp_path) -> None:
        engine = _engine(tmp_path)
        service = _service(engine)
        result = await service.accept_report(
            agent_id="worker",
            instance_id="inst-1",
            category="z" * 200,
            description="boom",
            severity="major",
        )
        row = _row(engine, result.report_id)
        assert len(row.category_raw) == 64
        assert row.category == "other"

    async def test_an_empty_description_is_refused_and_persists_nothing(
        self, tmp_path
    ) -> None:
        from daemon.services.anomaly_service import AnomalyInputError

        engine = _engine(tmp_path)
        service = _service(engine)
        with pytest.raises(AnomalyInputError):
            await service.accept_report(
                agent_id="worker",
                instance_id="inst-1",
                category="tool-failure",
                description="   ",
                severity="major",
            )
        from sqlmodel import Session, select

        with Session(engine) as session:
            rows = session.exec(select(AnomalyReport)).all()
        assert rows == []

    async def test_an_out_of_set_severity_is_refused_and_persists_nothing(
        self, tmp_path
    ) -> None:
        from daemon.services.anomaly_service import AnomalyInputError

        engine = _engine(tmp_path)
        service = _service(engine)
        with pytest.raises(AnomalyInputError):
            await service.accept_report(
                agent_id="worker",
                instance_id="inst-1",
                category="tool-failure",
                description="boom",
                severity="catastrophic",
            )
        from sqlmodel import Session, select

        with Session(engine) as session:
            rows = session.exec(select(AnomalyReport)).all()
        assert rows == []

    async def test_the_fold_notice_appears_only_when_a_fold_happened(
        self, tmp_path
    ) -> None:
        engine = _engine(tmp_path)
        service = _service(engine)
        folded = await service.accept_report(
            agent_id="worker",
            instance_id="inst-1",
            category="tool failure",
            description="boom",
            severity="major",
        )
        assert folded.fold_notice == 'category "tool failure" folded to "tool-failure"'
        canonical = await service.accept_report(
            agent_id="worker",
            instance_id="inst-1",
            category="tool-failure",
            description="boom",
            severity="major",
        )
        assert canonical.fold_notice is None

    async def test_the_accepted_row_carries_the_degenerate_cluster_tail(
        self, tmp_path
    ) -> None:
        engine = _engine(tmp_path)
        service = _service(engine)
        result = await service.accept_report(
            agent_id="worker",
            instance_id="inst-1",
            category="tool-failure",
            description="boom",
            severity="major",
        )
        row = _row(engine, result.report_id)
        assert row.outcome == "accepted"
        assert row.cluster_id is None
        assert result.cluster_id is None
        assert result.cluster_report_count == 1
        assert result.cluster_first_report_at == row.created_at

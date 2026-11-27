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
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

from daemon import _redact
from daemon._redact import redact_exc_str
from daemon.config import NestReportConfig
from daemon.repositories.anomaly.models import AnomalyCluster, AnomalyReport
from daemon.services import anomaly_service
from daemon.services.anomaly_service import (
    AnomalyRefusalError,
    AnomalyService,
    scrub_text,
    TRUNCATION_MARKER_FORMAT,
)
from daemon.services.secret_persistence_scrub import (
    register_secret_service,
    reset_process_registry,
)
from daemon.services.secret_service import (
    SecretCipher,
    SecretService,
)

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

    class _PresentScrubber:
        def is_effective(self) -> bool:
            return True

        def _current_rows(self):
            return []

    monkeypatch.setattr(
        anomaly_service, "get_process_scrubber", lambda: _PresentScrubber(), raising=True
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
        out = scrub_text(_Boom(), 100)[0]
        assert isinstance(out, str)

    def test_the_limit_is_applied_after_the_redaction(self) -> None:
        """The cap must NOT pre-empt the masker.

        A cap applied FIRST would cut the secret into an unmatchable
        fragment (``sk-proj-AB`` no longer matches the 8+-character
        provider-key shape) and the fragment would survive into the
        database.
        """
        out = scrub_text(SK_SECRET + " trailing", 10)[0]
        assert "sk-proj-" not in out
        assert len(out) == 10

    def test_the_limit_is_applied_after_the_redaction_for_a_passphrase(self) -> None:
        payload = json.dumps({"password": PASSPHRASE})
        out = scrub_text(payload, 30)[0]
        for word in PASSPHRASE_WORDS:
            assert word not in out

    def test_the_scrubbed_text_never_exceeds_the_cap(self) -> None:
        out = scrub_text("x" * 500, 32)[0]
        assert len(out) == 32

    def test_the_default_scrub_is_silent_for_harvested_excerpts(self) -> None:
        # Only the authored-field call sites opt into mark_truncated;
        # excerpts already carry excerpt_length / excerpt_sha256.
        out = scrub_text("x" * 500, 32)[0]
        assert out == "x" * 32
        assert "TRUNCATED" not in out

    def test_the_loud_marker_fits_inside_the_cap(self) -> None:
        out = scrub_text("x" * 500, 80, mark_truncated=True)[0]
        assert len(out) == 80
        assert out.endswith("stored prefix only]")
        assert "original 500 chars" in out

    def test_a_tiny_cap_still_carries_a_marker(self) -> None:
        out = scrub_text("x" * 500, 4, mark_truncated=True)[0]
        assert out == "[TRU"  # the signal wins over content at a tiny cap
    def test_the_secret_shape_masker_leaves_benign_prose_alone(self) -> None:
        text = "the token expired because the password policy rotated"
        assert scrub_text(text, 200)[0] == text

    def test_the_secret_shape_masker_keeps_the_token_metering_fields(self) -> None:
        # E3: the token-metering diagnostics are the text a
        # token-exhaustion error carries — masking them would destroy
        # the diagnostic value the excerpt exists to capture.
        #
        # The VALUE must survive, not just the NAME (Condition A — the
        # "pin that cannot catch its defect" class): a disabled
        # benign-name exemption masks the value while leaving the name
        # intact, so a name-only assertion stays green.
        for name in ("max_tokens", "input_tokens", "output_tokens", "token_count"):
            payload = json.dumps({name: 200000})
            out = scrub_text(payload, 200)[0]
            assert name in out
            assert "200000" in out, f"{name}: the metering VALUE was masked"
        out = scrub_text("token_budget: 200000", 200)[0]
        assert "token_budget" in out
        assert "200000" in out
        # the plural family (the `*_tokens` suffix) — the value survives too.
        out = scrub_text(json.dumps({"total_tokens": 987654}), 200)[0]
        assert "total_tokens" in out
        assert "987654" in out

    def test_the_secret_shape_masker_masks_the_widened_keyword_family(self) -> None:
        # R-a: the keyword FAMILY masks (the suffix legs).
        for name in ("secret_note", "password_hint", "client_secret_id"):
            payload = json.dumps({name: "value-xyz"})
            assert "value-xyz" not in scrub_text(payload, 200)[0]

    def test_the_secret_shape_masker_masks_the_w2_council_keywords(self) -> None:
        """W2 (code council fix cycle, 2026-11-05) — ``credential`` /
        ``pwd`` / ``private_key`` join the keyword FAMILY. The council
        verified all three UNMASKED BY EXECUTION pre-widening; the
        widening mirrors ``env_key_policy``'s SECRET_MARKER_WORDS
        (every SECRET-SHAPED word it recognizes — the non-secret-shaped
        ``AUTH`` / bare-``PRIVATE`` words are deliberately NOT carried
        over). One leg per keyword per shape class: plain / JSON /
        quoted multi-word (E1) + the ``*_``-env-var shape family."""
        # credential — plain + JSON
        assert "super-cred-9" not in scrub_text("credential=super-cred-9", 200)[0]
        assert "hunter2" not in scrub_text('{"credential": "hunter2"}', 200)[0]
        # pwd — plain + JSON + quoted multi-word (E1: the WHOLE value)
        assert "hunter2" not in scrub_text("pwd=hunter2", 200)[0]
        assert "hunter2" not in scrub_text('{"pwd": "hunter2"}', 200)[0]
        assert "two word secret" not in scrub_text(
            'pwd = "two word secret"', 200
        )[0]
        # private_key — plain + JSON PEM + quoted
        assert "AKIA1234567890ABCDEF" not in scrub_text(
            "private_key=AKIA1234567890ABCDEF", 500
        )[0]
        assert "BEGIN PRIVATE KEY" not in scrub_text(
            '{"private_key": "-----BEGIN PRIVATE KEY-----"}', 500
        )[0]
        # the env-var shape family carries the same three words
        assert "sk-ant-XY1212345678" not in scrub_text(
            "MY_PRIVATE_KEY=sk-ant-XY1212345678", 500
        )[0]
        assert "postgres-super-9" not in scrub_text(
            "DB_CREDENTIAL=postgres-super-9", 200
        )[0]
        assert "abc123XYZ" not in scrub_text("SERVICE_PWD=abc123XYZ!", 200)[0]

    def test_the_secret_shape_masker_masks_the_bare_key_suffix_class(self) -> None:
        """Condition B (code-council delta, 2026-11-05) — the bare-KEY
        word class measured UNMASKED: ``encryption_key`` / ``SIGNING_KEY``
        / ``auth_key`` in prose, and the env form (``ENCRYPTION_KEY=`` /
        ``SSH_KEY=`` / ``MCP_AUTH=``). The widening is SUFFIX-ANCHORED
        (``*_key`` / ``*_auth``), so the benign diagnostics stay clear
        (see the no-over-mask test)."""
        assert "s3cr3t-key-1" not in scrub_text(
            "encryption_key = s3cr3t-key-1", 200
        )[0]
        assert "s3cr3t-key-2" not in scrub_text(
            '{"signing_key": "s3cr3t-key-2"}', 200
        )[0]
        assert "s3cr3t-key-3" not in scrub_text("auth_key = s3cr3t-key-3", 200)[0]
        assert "s3cr3t-key-4" not in scrub_text(
            "ENCRYPTION_KEY=s3cr3t-key-4", 500
        )[0]
        assert "s3cr3t-key-5" not in scrub_text("SSH_KEY=s3cr3t-key-5", 500)[0]
        assert "s3cr3t-auth-6" not in scrub_text("MCP_AUTH=s3cr3t-auth-6", 500)[0]

    def test_the_widened_family_does_not_mask_secret_free_prose(self) -> None:
        """The no-over-mask probes for the W2 words: prose carrying the
        new words in a NON-secret shape (no ``[:=]`` separator after
        the name) stays clear. The separator requirement plus the E3
        denylist ARE the over-masking guards — masking these would
        destroy the excerpt's diagnostic value, not protect it.

        Condition B adds the SUFFIX-ANCHOR probes: ``monkey`` and
        ``keyword`` carry the KEY letters WITHOUT the underscore
        separator, and ``primary key: id`` is a bare two-word phrase —
        all three must stay clear, which is what makes the ``*_key``
        widening fail-safe rather than destructive."""
        for text in (
            "my credentials are current",
            "the pwd guidance was updated",
            "private key rotation is scheduled",
            "the monkey escaped the enclosure",
            "keyword search is slow today",
            "primary key: id",
        ):
            assert scrub_text(text, 200)[0] == text

    def test_the_secret_shape_masker_is_the_sole_defence_for_non_stored_values(
        self,
    ) -> None:
        # C1c: scrubber PRESENCE is irrelevant to this class — the
        # pattern masker alone must carry it.
        assert SK_SECRET not in scrub_text(f"token={SK_SECRET}", 500)[0]


class TestTheScrubGrowthPastCap:
    """Defect-1 (i)-(iii): scrubbed can grow past the cap via stored-secret
    substitution; the loud marker must then fire and never be cut away."""

    def _register_secret(self, value: str = "a") -> None:
        reset_process_registry()
        fernet_key = Fernet.generate_key().decode()
        service = SecretService(
            engine=None if False else None,  # placeholder, replaced below
        ) if False else None
        # build engine-less? SecretService requires engine. Use sqlite in-memory.
        from sqlalchemy import create_engine
        eng = create_engine("sqlite:///:memory:")
        from sqlmodel import SQLModel
        SQLModel.metadata.create_all(eng)
        svc = SecretService(engine=eng, cipher=SecretCipher(encryption_key=fernet_key))
        svc.store(name="growth-secret", kind="generic", value=value, actor="tester")
        register_secret_service(svc)
        self._svc = svc
        self._eng = eng

    def test_scrubbed_longer_than_cap_while_original_le_cap_marker_present(self) -> None:
        # (i) Register a stored secret whose marker is LONGER than the
        # value, so scrub_enqueued_body grows the text past the cap even
        # though the raw input never exceeded it.
        reset_process_registry()
        from sqlalchemy import create_engine
        from sqlmodel import SQLModel
        fernet_key = Fernet.generate_key().decode()
        eng = create_engine("sqlite:///:memory:")
        SQLModel.metadata.create_all(eng)
        svc = SecretService(engine=eng, cipher=SecretCipher(encryption_key=fernet_key))
        svc.store(name="growth-secret", kind="generic", value="a", actor="tester")
        register_secret_service(svc)

        try:
            # input is 2 chars (<= cap 50); "a" is stored -> marker (40 chars)
            # so scrubbed ~= 80 chars (> 50).
            text_in = "aa"
            cap = 50
            assert len(text_in) <= cap, "precondition: raw input under cap"
            out, truncated = scrub_text(text_in, cap, mark_truncated=True)
            # discriminating control: the substitution genuinely fired.
            assert "__ENS_REDACTED__" in out, "stored-secret substitution must fire"
            assert truncated is True, "loud marker must be attached"
            assert cap - len("[TRUNCATED]") <= len(out) <= cap, "result must be capped"
            # control: without the registered service, no growth, no marker.
            reset_process_registry()
            out2, truncated2 = scrub_text(text_in, cap, mark_truncated=True)
            assert truncated2 is False, "no growth without registered secret"
            assert "__ENS_REDACTED__" not in out2
        finally:
            reset_process_registry()

    def test_text_that_fits_after_scrubbing_carries_no_false_marker(self) -> None:
        # (ii) Mirror case: input under cap, no stored secret -> no marker.
        reset_process_registry()
        text_in = "x" * 10
        cap = 100
        out, truncated = scrub_text(text_in, cap, mark_truncated=True)
        assert truncated is False
        assert "TRUNCATED" not in out
        assert out == text_in
        # discriminating control: exceeding the cap DOES attach a marker.
        out2, truncated2 = scrub_text("x" * 200, cap, mark_truncated=True)
        assert truncated2 is True
        assert "TRUNCATED" in out2

    def test_marker_is_never_itself_cut_away_across_cap_sweep(self) -> None:
        # (iii) Sweep caps: returned text always ends with a complete
        # marker (or the documented tiny-cap sacrifice), never a partial.
        reset_process_registry()
        inp = "x" * 500
        full_marker = TRUNCATION_MARKER_FORMAT.format(n=500)
        short_marker = "[TRUNCATED]"
        caps = [1, 2, 3, 4, 5, 10, 11, 20, 30, 47, 48, 49, 60, 80]
        for cap in caps:
            out, truncated = scrub_text(inp, cap, mark_truncated=True)
            assert truncated is True, f"cap={cap}"
            assert len(out) == cap, f"cap={cap}: len={len(out)}"
            if cap >= len(short_marker):
                assert out.endswith("]"), f"cap={cap}: partial marker {out!r}"
            else:
                assert out == short_marker[:cap], f"cap={cap}: {out!r}"
            if cap >= len(full_marker):
                assert out.endswith("stored prefix only]"), f"cap={cap}"
        # discriminating control: a cap that fully fits the input -> no marker.
        out0, flag0 = scrub_text("x" * 10, 100, mark_truncated=True)
        assert flag0 is False
        assert "TRUNCATED" not in out0

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
    body); what is pinned here is the SERVICE half: over-cap authored
    text is stored WITH a loud marker (never refused), the
    authored-content refusals persist NOTHING, and the fold notice
    appears exactly when a fold happened.
    """

    async def test_over_length_authored_text_is_loudly_marked_and_never_refused(
        self, tmp_path
    ) -> None:
        engine = _engine(tmp_path)
        service = _service(engine)
        config = NestReportConfig()
        result = await service.accept_report(
            agent_id="worker",
            instance_id="inst-1",
            category="tool-failure",
            description="d" * (config.description_max_chars + 500),
            severity="major",
            suspected_cause="c" * (config.suspected_cause_max_chars + 500),
        )
        row = _row(engine, result.report_id)
        assert len(row.description) == config.description_max_chars
        assert len(row.suspected_cause) == config.suspected_cause_max_chars
        assert "[TRUNCATED: original" in row.description
        assert "[TRUNCATED: original" in row.suspected_cause
        assert row.description.endswith("stored prefix only]")
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

    async def test_the_accepted_row_carries_the_real_cluster_assignment(
        self, tmp_path
    ) -> None:
        """C3 — the DEGENERATE tail is gone: an accepted report is
        ALWAYS clustered (AM-5b — ``accepted`` MEANS clustered)."""
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
        assert row.cluster_id is not None
        assert row.cluster_id == result.cluster_id
        assert result.cluster_report_count == 1
        assert result.cluster_first_report_at == row.created_at
        # the cluster row exists and counts its member rows
        with Session(engine) as session:
            cluster = session.get(AnomalyCluster, row.cluster_id)
            assert cluster is not None
            session.expunge(cluster)
        assert cluster.report_count == 1
        assert cluster.first_report_at == row.created_at

    async def test_the_report_insert_routes_through_the_offload_seam(
        self, tmp_path
    ) -> None:
        """F-1: the INSERT must leave the event loop.

        ``assign_report_unit`` opens a ``Session`` and ``commit()``s —
        running it directly inside ``async def accept_report`` is the
        v0.27.14 event-loop sync-DB incident class. The call must be
        routed through the service's ``offload`` seam (the same seam
        the harvest reads and the rate-gate counts use), never invoked
        directly.
        """
        engine = _engine(tmp_path)
        routed: list[str] = []

        async def _recording_offload(fn, *args, **kwargs):
            routed.append(getattr(fn, "__name__", "<anonymous>"))
            return fn(*args, **kwargs)

        service = AnomalyService(
            engine,
            config=NestReportConfig(),
            manager=_FakeManager([]),
            offload=_recording_offload,
        )
        result = await service.accept_report(
            agent_id="worker",
            instance_id="inst-1",
            category="tool-failure",
            description="boom",
            severity="major",
        )
        assert result.report_id, "the report persisted"
        assert "assign_report_unit" in routed, (
            "the ONE-TRANSACTION unit-of-work (report INSERT + cluster "
            "INSERT/UPDATE) must be invoked VIA the offload seam "
            "(await self._offload(...)), never as a direct synchronous "
            "call on the event loop"
        )

        # ── F-3 — the LEDGER-REFUSAL INSERT takes the SAME seam.
        # It opens a Session and commits()s, so a direct call inside the
        # async path is the same event-loop sync-DB incident class.  The
        # offload-routing pin used to cover the accept path only (it was
        # re-pointed from ``insert_report`` to ``assign_report_unit``),
        # which left the ledger INSERT unpinned.
        for _ in range(4):
            await service.accept_report(
                agent_id="worker",
                instance_id="inst-1",
                category="tool-failure",
                description="boom",
                severity="major",
            )
        # the 6th report in the window exhausts the per-agent budget →
        # the ledgered refusal (the ledger cap is unsatisfied, so the
        # refusal row IS written).
        with pytest.raises(AnomalyRefusalError):
            await service.accept_report(
                agent_id="worker",
                instance_id="inst-1",
                category="tool-failure",
                description="boom",
                severity="major",
            )
        assert "insert_report" in routed, (
            "the LEDGER-REFUSAL INSERT must be invoked VIA the offload "
            "seam (await self._offload(...)), never as a direct "
            "synchronous call on the event loop"
        )


# ── Iteration-2 pins (T-a..T-e + GAP-2) ──────────────────────────────
import re as _re
import uuid


class _BrokenScrubber:
    """Legacy-shaped (no probe, no load path) scrubber: ineffective under
    the fail-closed legacy branch, effective only when that branch is
    (incorrectly) restored. Exercises the H3 fail-closed contract.
    """
    pass


class _ProbeFalseScrubber:
    """Probe-present-but-false scrubber: exercises the
    ``callable(probe) -> bool(probe()) == False`` branch of
    ``_scrubber_effective`` (D2 coverage)."""

    def is_effective(self) -> bool:
        return False

    def _current_rows(self):
        return []


class _ToctouScrubber:
    """Effective at the GAP-2 probe but drops on the real scrub call."""

    def is_effective(self) -> bool:
        return True

    def _current_rows(self):
        raise RuntimeError("store dropped mid-scrub")

    def _plaintext_pairs(self, rows):
        return []


class TestIteration2Pins:
    def _register_secret(self, value: str = "STRADDLESECRET123"):
        reset_process_registry()
        from sqlalchemy import create_engine
        from sqlmodel import SQLModel
        fernet_key = Fernet.generate_key().decode()
        eng = create_engine("sqlite:///:memory:")
        SQLModel.metadata.create_all(eng)
        svc = SecretService(engine=eng, cipher=SecretCipher(encryption_key=fernet_key))
        svc.store(name="iter2-secret", kind="generic", value=value, actor="tester")
        register_secret_service(svc)
        return value

    # T-a (re-geometried, iteration 3): straddle leg + roomy control leg.
    def test_t_a_description_straddle_no_fragment_survives(self) -> None:
        secret = self._register_secret("STRADDLESECRET123")
        # Straddle leg: filler 45 + 17-char secret = 62, cap 60. Under the
        # fix the full secret is scrubbed first, so no 8-gram survives;
        # under a pre-cut (source[:cap]) a 15-char fragment survives.
        cap = 60
        content = "x" * 45 + secret
        out, _ = scrub_text(content, cap, mark_truncated=False)
        for i in range(len(secret) - 7):
            gram = secret[i : i + 8]
            assert gram not in out, f"fragment survived: {gram!r} in {out!r}"
        # Roomy control leg: cap large enough that the token survives the
        # cut -> substitution actually fired (discriminating control).
        out2, _ = scrub_text(content, 200, mark_truncated=False)
        assert _re.search(r"__ENS_REDACTED__[0-9a-f]{12}__", out2) is not None, out2
        reset_process_registry()

    # T-c (re-geometried, iteration 3): cut lands INSIDE the token.
    def test_t_c_no_redeemable_handle_or_bisected_token(self) -> None:
        secret = self._register_secret("STRADDLESECRET123")
        # filler 5 + 17-char secret = 22, cap 30. Post-scrub = 35 > 30
        # (expansion) -> marker collapses to [TRUNCATED] (11) ->
        # content_limit 19 lands INSIDE token interval [5,35) ->
        # token_safe_cut backs up to 5 (no partial token); a blind cut
        # leaves the partial prefix __ENS_REDACTED.
        cap = 30
        content = "x" * 5 + secret
        out, _ = scrub_text(content, cap, mark_truncated=True)
        assert "[TRUNCATED]" in out, out
        # Every occurrence of the prefix must be a COMPLETE token.
        for m in _re.finditer(r"__ENS_REDACTED", out):
            tail = out[m.start():]
            assert _re.match(r"__ENS_REDACTED__[0-9a-f]{12}__", tail), (
                f"bisected token: {tail[:40]!r}"
            )
        # H7: widen the bisect guard to cover the legacy handle prefix too,
        # so a token swap leaves a *partial* forbidden token that this
        # guard catches (the full-pattern search below cannot, because a
        # bisected token never matches).
        for m in _re.finditer(r"__ENS_SECRET", out):
            tail = out[m.start():]
            assert _re.match(r"__ENS_SECRET__[0-9a-f-]{36}__", tail), (
                f"bisected legacy handle: {tail[:40]!r}"
            )
        assert _re.search(r"__ENS_SECRET__[0-9a-f-]{36}__", out) is None, out
        reset_process_registry()

    # H1 (INV-3 storage-absolute): a supplied legacy marker is stripped
    # on EVERY stored path, including when NO secret is registered (so
    # the _anomaly_exact_scrub early-return hazard is covered).
    def test_h1_supplied_legacy_marker_stripped(self) -> None:
        from daemon.services.secret_persistence_scrub import reset_process_registry

        reset_process_registry()
        marker = "__ENS_SECRET__" + str(uuid.uuid4()) + "__"
        content = "before " + marker + " after"
        # No secret registered -> scrubber is None; strip must still fire.
        out, _ = scrub_text(content, 1000, mark_truncated=False)
        assert "__ENS_SECRET__" not in out, out
        assert "before" in out and "after" in out, out
        reset_process_registry()

    # T-d
    def test_t_d_honest_accounting(self) -> None:
        secret = self._register_secret("a")
        # expansion case
        text_in = "a" * 300
        cap = 8000
        out, truncated = scrub_text(text_in, cap, mark_truncated=True)
        assert truncated is True
        import re as _r

        m = _r.search(r"\[TRUNCATED[^\]]*\]", out)
        assert m is not None
        marker = m.group(0)
        assert "original 300 chars" in marker or "pre-scrub 300 chars" in marker
        assert "post-redaction" in marker
        # post-scrub length (9000) must not be labelled "original"
        assert "original 9000" not in marker
        # normal case
        out2, truncated2 = scrub_text("x" * 500, 200, mark_truncated=True)
        assert truncated2 is True
        m2 = _r.search(r"\[TRUNCATED[^\]]*\]", out2)
        assert m2 is not None
        assert "original 500 chars" in m2.group(0)
        reset_process_registry()

    # T-b + T-e + GAP-2 via accept_report
    async def test_t_b_excerpt_straddle_no_fragment(self, tmp_path, monkeypatch) -> None:
        secret = self._register_secret("STRADDLESECRET123")
        from daemon.repositories.anomaly.models import AnomalyReport, AnomalyCluster
        from sqlalchemy import create_engine
        from sqlmodel import SQLModel

        engine = create_engine(
            f"sqlite:///{tmp_path / 't_b.db'}", connect_args={"check_same_thread": False}
        )
        SQLModel.metadata.create_all(
            engine, tables=[AnomalyReport.__table__, AnomalyCluster.__table__]
        )
        from daemon.services.anomaly_service import AnomalyService
        from daemon.config import NestReportConfig

        class _CM:
            def __init__(self, msgs):
                self.checkpointer = type(
                    "C", (), {"raw_saver": _FakeRawSaver(msgs)}
                )()
                self.metrics_query_provider = None
                self._instance_repository = None
                self._task_repo = None
                self._job_queue_service = None
                self._mission_record_service = None
                self._wedge_capture_repository = None

        # build a tool message whose content straddles the 500 boundary
        msg_content = "ERROR: " + "x" * 483 + secret
        from langchain_core.messages import ToolMessage

        tm = ToolMessage(content=msg_content, tool_call_id="c1")
        manager = _CM([tm])
        service = AnomalyService(
            engine, config=NestReportConfig(), manager=manager, offload=_passthrough
        )
        result = await service.accept_report(
            agent_id="worker",
            instance_id="inst-1",
            category="tool-failure",
            description="boom",
            severity="major",
        )
        from sqlmodel import Session

        with Session(engine) as s:
            row = s.get(AnomalyReport, result.report_id)
            s.expunge(row)
        excerpt = row.excerpt
        assert excerpt is not None
        if excerpt is not None:
            for i in range(len(secret) - 7):
                gram = secret[i : i + 8]
                assert gram not in excerpt, f"fragment in excerpt: {gram!r}"
        reset_process_registry()

    async def test_t_e_excerpt_store_unavailable_withheld(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            anomaly_service, "get_process_scrubber", lambda: _BrokenScrubber(), raising=True
        )
        from daemon.repositories.anomaly.models import AnomalyReport, AnomalyCluster
        from sqlalchemy import create_engine
        from sqlmodel import SQLModel

        engine = create_engine(
            f"sqlite:///{tmp_path / 't_e.db'}", connect_args={"check_same_thread": False}
        )
        SQLModel.metadata.create_all(
            engine, tables=[AnomalyReport.__table__, AnomalyCluster.__table__]
        )
        from daemon.services.anomaly_service import AnomalyService
        from daemon.config import NestReportConfig

        class _CM:
            def __init__(self, msgs):
                self.checkpointer = type(
                    "C", (), {"raw_saver": _FakeRawSaver(msgs)}
                )()
                self.metrics_query_provider = None
                self._instance_repository = None
                self._task_repo = None
                self._job_queue_service = None
                self._mission_record_service = None
                self._wedge_capture_repository = None

        from langchain_core.messages import ToolMessage

        tm = ToolMessage(content="ERROR: boom", tool_call_id="c1")
        manager = _CM([tm])
        service = AnomalyService(
            engine, config=NestReportConfig(), manager=manager, offload=_passthrough
        )
        result = await service.accept_report(
            agent_id="worker",
            instance_id="inst-1",
            category="tool-failure",
            description="boom",
            severity="major",
        )
        from sqlmodel import Session

        with Session(engine) as s:
            row = s.get(AnomalyReport, result.report_id)
            s.expunge(row)
        assert row.excerpt is None
        reset_process_registry()

    # GAP-2 pin
    def test_gap2_description_store_unavailable_placeholder(self, monkeypatch) -> None:
        import daemon.services.secret_persistence_scrub as _sps

        monkeypatch.setattr(_sps, "get_process_scrubber", lambda: _BrokenScrubber(), raising=True)
        monkeypatch.setattr(anomaly_service, "get_process_scrubber", lambda: _BrokenScrubber(), raising=True)
        out, _ = scrub_text("hello world", 100, mark_truncated=False)
        from daemon.services.anomaly_service import scrub_unavailable_placeholder

        assert out == scrub_unavailable_placeholder(len("hello world"))
        assert "{n}" not in out
        reset_process_registry()

    # INV-2 closure pin: exception path yields placeholder, not plaintext.
    def test_inv2_scrub_exception_path_withheld(self, monkeypatch) -> None:
        import daemon.services.secret_persistence_scrub as _sps

        monkeypatch.setattr(
            _sps, "get_process_scrubber", lambda: _ToctouScrubber(), raising=True
        )
        monkeypatch.setattr(
            anomaly_service, "get_process_scrubber", lambda: _ToctouScrubber(), raising=True
        )
        raw = "supersecret-plaintext-payload"
        out, truncated = scrub_text(raw, 100, mark_truncated=False)
        from daemon.services.anomaly_service import scrub_unavailable_placeholder

        assert out == scrub_unavailable_placeholder(len(raw)), out
        assert "{n}" not in out
        assert raw not in out
        assert truncated is True
        reset_process_registry()

    # H5: a secret whose plaintext equals REDACTED (a substring of the
    # emitted token prefix) must not corrupt an already-emitted token,
    # and its ordinary occurrences are still redacted.
    def test_h5_secret_does_not_rescrub_inside_emitted_token(self) -> None:
        from daemon.services.secret_persistence_scrub import register_secret_service, reset_process_registry
        from sqlalchemy import create_engine
        from sqlmodel import SQLModel
        from daemon.services.secret_service import SecretService, SecretCipher
        from cryptography.fernet import Fernet

        reset_process_registry()
        fernet_key = Fernet.generate_key().decode()
        eng = create_engine("sqlite:///:memory:")
        SQLModel.metadata.create_all(eng)
        svc = SecretService(engine=eng, cipher=SecretCipher(encryption_key=fernet_key))
        val_foo = "longsecretvalue"  # longer -> processed first, emits token containing REDACTED
        val_redacted = "REDACTED"
        id_foo = svc.store(name="foo", kind="generic", value=val_foo, actor="tester")["id"]
        id_redacted = svc.store(name="redacted", kind="generic", value=val_redacted, actor="tester")["id"]
        register_secret_service(svc)

        import hashlib
        token_foo = "__ENS_REDACTED__" + hashlib.sha256(id_foo.encode()).hexdigest()[:12] + "__"
        content = val_foo + " " + val_redacted
        out, _ = scrub_text(content, 1000, mark_truncated=False)
        # foo's token must survive intact (not corrupted by the REDACTED pass).
        assert token_foo in out, out
        # REDACTED's ordinary occurrence is redacted (its token present).
        token_redacted = "__ENS_REDACTED__" + hashlib.sha256(id_redacted.encode()).hexdigest()[:12] + "__"
        assert token_redacted in out, out
        reset_process_registry()

    # D2: probe-present-but-false scrubber ⇒ withheld (fail-closed),
    # both for authored fields and the excerpt.
    def test_d2_probe_false_scrubber_withheld(self, monkeypatch) -> None:
        import daemon.services.secret_persistence_scrub as _sps

        monkeypatch.setattr(_sps, "get_process_scrubber", lambda: _ProbeFalseScrubber(), raising=True)
        monkeypatch.setattr(anomaly_service, "get_process_scrubber", lambda: _ProbeFalseScrubber(), raising=True)
        out, truncated = scrub_text("hello world", 100, mark_truncated=False)
        from daemon.services.anomaly_service import scrub_unavailable_placeholder

        assert out == scrub_unavailable_placeholder(len("hello world")), out
        assert truncated is True
        reset_process_registry()

    async def test_d2_probe_false_excerpt_withheld(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(anomaly_service, "get_process_scrubber", lambda: _ProbeFalseScrubber(), raising=True)
        from daemon.repositories.anomaly.models import AnomalyReport, AnomalyCluster
        from sqlalchemy import create_engine
        from sqlmodel import SQLModel
        from daemon.services.anomaly_service import AnomalyService
        from daemon.config import NestReportConfig

        class _CM:
            def __init__(self, msgs):
                self.checkpointer = type("C", (), {"raw_saver": _FakeRawSaver(msgs)})()
                self.metrics_query_provider = None
                self._instance_repository = None
                self._task_repo = None
                self._job_queue_service = None
                self._mission_record_service = None
                self._wedge_capture_repository = None

        from langchain_core.messages import ToolMessage

        engine = create_engine(
            f"sqlite:///{tmp_path / 'd2.db'}", connect_args={"check_same_thread": False}
        )
        SQLModel.metadata.create_all(
            engine, tables=[AnomalyReport.__table__, AnomalyCluster.__table__]
        )
        tm = ToolMessage(content="ERROR: boom", tool_call_id="c1")
        manager = _CM([tm])
        service = AnomalyService(
            engine, config=NestReportConfig(), manager=manager, offload=_passthrough
        )
        result = await service.accept_report(
            agent_id="worker",
            instance_id="inst-1",
            category="tool-failure",
            description="boom",
            severity="major",
        )
        from sqlmodel import Session

        with Session(engine) as s:
            row = s.get(AnomalyReport, result.report_id)
            s.expunge(row)
        assert row.excerpt is None
        reset_process_registry()

    # D1: a supplied legacy marker in the raw category is stripped on
    # every persisted site (strip-then-cap); benign categories round-trip.
    async def test_d1_category_raw_strips_supplied_marker(self, tmp_path) -> None:
        import uuid as _u
        from daemon.repositories.anomaly.models import AnomalyReport, AnomalyCluster
        from sqlalchemy import create_engine
        from sqlmodel import SQLModel
        from daemon.services.anomaly_service import AnomalyService
        from daemon.config import NestReportConfig

        class _CM:
            def __init__(self, msgs):
                self.checkpointer = type("C", (), {"raw_saver": _FakeRawSaver(msgs)})()
                self.metrics_query_provider = None
                self._instance_repository = None
                self._task_repo = None
                self._job_queue_service = None
                self._mission_record_service = None
                self._wedge_capture_repository = None

        engine = create_engine(
            f"sqlite:///{tmp_path / 'd1.db'}", connect_args={"check_same_thread": False}
        )
        SQLModel.metadata.create_all(
            engine, tables=[AnomalyReport.__table__, AnomalyCluster.__table__]
        )
        marker = "__ENS_SECRET__" + str(_u.uuid4()) + "__"
        category = "before " + marker + " after"
        service = AnomalyService(
            engine, config=NestReportConfig(), manager=_CM([]), offload=_passthrough
        )
        result = await service.accept_report(
            agent_id="worker",
            instance_id="inst-1",
            category=category,
            description="boom",
            severity="major",
        )
        from sqlmodel import Session

        with Session(engine) as s:
            row = s.get(AnomalyReport, result.report_id)
            s.expunge(row)
        assert "__ENS_SECRET__" not in (row.category_raw or ""), row.category_raw
        assert "before" in (row.category_raw or "") and "after" in (row.category_raw or ""), row.category_raw
        assert len(row.category_raw or "") <= 64
        # benign round-trip
        result2 = await service.accept_report(
            agent_id="worker",
            instance_id="inst-1",
            category="tool failure",
            description="boom",
            severity="major",
        )
        with Session(engine) as s:
            row2 = s.get(AnomalyReport, result2.report_id)
            s.expunge(row2)
        assert row2.category_raw == "tool failure", row2.category_raw
        reset_process_registry()

    # DEFECT-3 pin: withheld case distinguishable from cap cut by stored text.
    def test_inv4_withheld_distinguishable_from_cap_cut(self, monkeypatch) -> None:
        import daemon.services.secret_persistence_scrub as _sps

        # cap-cut case (no registered scrubber -> normal path)
        out_cut, _ = scrub_text("x" * 500, 200, mark_truncated=True)
        assert "[TRUNCATED" in out_cut

        # withheld case
        monkeypatch.setattr(_sps, "get_process_scrubber", lambda: _BrokenScrubber(), raising=True)
        monkeypatch.setattr(anomaly_service, "get_process_scrubber", lambda: _BrokenScrubber(), raising=True)
        out_withheld, _ = scrub_text("hello world", 100, mark_truncated=True)
        from daemon.services.anomaly_service import scrub_unavailable_placeholder

        assert out_withheld == scrub_unavailable_placeholder(len("hello world"))
        assert "{n}" not in out_withheld
        assert out_withheld != out_cut
        assert "withheld" in out_withheld or "unavailable" in out_withheld
        reset_process_registry()

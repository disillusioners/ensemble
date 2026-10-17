"""agent-secrets P2 phase 3 — proc ``_build_child_env`` seam tests.

Pins the three per-seam hooks (p2-architecture-recommendation.md §6
phase-3 row) for design §1b:

* (i) **resolve success** — the merged child env carries the
  PLAINTEXT (the marker NEVER survives into the returned dict).
* (ii) **fail-closed** — a bogus marker raises the typed
  :class:`SecretMarkerResolutionError` (surface ``"bash.env"``
  family per §1b); the caller aborts the spawn — never partial env.
* (iii) **zero-change** — marker-free / no-overlay calls produce a
  byte-identical child env (``os.environ ⊕ tracking_id``) and zero
  ``use_marker`` audit rows.

§1b decision pins (design text): bash and proc share ONLY
``resolve_secret_markers`` — NO shared env-assembly helper; the
``process_env_overlay`` param is operator-injected (proc_run carries
NO LLM-visible env field).
"""

from __future__ import annotations

import os
from typing import Iterator

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, event as sa_event, select
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

from daemon.repositories.secrets.models import (  # noqa: F401
    Secret,
    SecretAuditEvent,
)
from daemon.services.secret_service import (
    SECRET_CODE_MARKER_MALFORMED,
    SECRET_CODE_NOT_FOUND,
    SecretCipher,
    SecretMarkerResolutionError,
    SecretService,
)
from daemon.services.secret_persistence_scrub import (
    register_secret_service,
    reset_process_registry,
)
from daemon.tools.proc_tools import _build_child_env, _TRACKING_ENV_VAR


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────


def _make_sqlite_engine(tmp_path) -> Engine:
    db_path = tmp_path / "proc-secret-env-test.sqlite"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )

    @sa_event.listens_for(eng, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return eng


@pytest.fixture
def sqlite_engine(tmp_path) -> Iterator[Engine]:
    eng = _make_sqlite_engine(tmp_path)
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def fernet_key() -> str:
    return Fernet.generate_key().decode()


@pytest.fixture
def service(
    sqlite_engine: Engine, fernet_key: str
) -> Iterator[SecretService]:
    svc = SecretService(
        engine=sqlite_engine,
        cipher=SecretCipher(encryption_key=fernet_key),
    )
    register_secret_service(svc)
    try:
        yield svc
    finally:
        reset_process_registry()


@pytest.fixture(autouse=True)
def _fake_home(monkeypatch, tmp_path) -> Iterator[str]:
    """CRITICAL-NOTE fence — never resolve live-shaped HOME paths."""
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    yield str(fake_home)


# ─────────────────────────────────────────────────────────────────────
# (i) resolve success
# ─────────────────────────────────────────────────────────────────────


class TestProcResolveSuccess:
    def test_overlay_marker_resolves_to_plaintext(
        self, service: SecretService
    ) -> None:
        """The overlay value's marker resolves to plaintext inside
        the returned child env (§1b flow — merged dict routes
        through ``resolve_secret_markers``)."""
        minted = service.store(
            name="proc-happy", kind="generic",
            value="pt-proc-secret", actor="worker",
        )
        env = _build_child_env(
            "proc-test-1",
            process_env_overlay={"P2_PROC_SECRET": minted["marker"]},
        )
        assert env["P2_PROC_SECRET"] == "pt-proc-secret"
        assert "__ENS_SECRET__" not in env["P2_PROC_SECRET"]

    def test_overlay_wins_over_os_environ(
        self, service: SecretService, monkeypatch
    ) -> None:
        """Overlay merges ATOP ``os.environ ⊕ tracking_id`` — the
        overlay value REPLACES the same-named entry."""
        monkeypatch.setenv("P2_PROC_OVERLAY_PROBE", "from-os-environ")
        env = _build_child_env(
            "proc-test-2",
            process_env_overlay={"P2_PROC_OVERLAY_PROBE": "from-overlay"},
        )
        assert env["P2_PROC_OVERLAY_PROBE"] == "from-overlay"
        assert env[_TRACKING_ENV_VAR] == "proc-test-2"

    def test_tracking_id_survives_overlay(
        self, service: SecretService
    ) -> None:
        """The tracking id is ALWAYS authoritative (proc ownership
        verification depends on it — Layer 3)."""
        env = _build_child_env(
            "proc-test-3",
            process_env_overlay={"UNRELATED": "value"},
        )
        assert env[_TRACKING_ENV_VAR] == "proc-test-3"


# ─────────────────────────────────────────────────────────────────────
# (ii) fail-closed
# ─────────────────────────────────────────────────────────────────────


class TestProcFailClosed:
    def test_missing_id_raises_typed_error(
        self, service: SecretService
    ) -> None:
        """A well-formed marker with an unknown id raises the typed
        ``secret_not_found`` envelope (surface ``bash.env`` per
        §1b); the caller MUST abort the spawn — never partial env."""
        import uuid

        bogus = f"__ENS_SECRET__{uuid.uuid4()}__"
        with pytest.raises(SecretMarkerResolutionError) as exc_info:
            _build_child_env(
                "proc-test-fc1",
                process_env_overlay={"P2_PROC_SECRET": bogus},
            )
        assert exc_info.value.code == SECRET_CODE_NOT_FOUND
        assert exc_info.value.surface == "bash.env"

    def test_malformed_marker_raises_typed_error(
        self, service: SecretService
    ) -> None:
        """A prefix-shaped value failing fullmatch → typed
        ``secret_marker_malformed`` (pre-DB)."""
        with pytest.raises(SecretMarkerResolutionError) as exc_info:
            _build_child_env(
                "proc-test-fc2",
                process_env_overlay={
                    "P2_PROC_SECRET": "__ENS_SECRET__not-a-uuid__"
                },
            )
        assert exc_info.value.code == SECRET_CODE_MARKER_MALFORMED
        assert exc_info.value.surface == "bash.env"

    def test_unwired_service_fails_closed(self, monkeypatch) -> None:
        """No SecretService registered + marker-shaped value →
        typed ``secret_resolver_store_unavailable`` (fail-closed)."""
        reset_process_registry()
        with pytest.raises(SecretMarkerResolutionError) as exc_info:
            _build_child_env(
                "proc-test-fc3",
                process_env_overlay={
                    "P2_PROC_SECRET": (
                        "__ENS_SECRET__"
                        "12345678-1234-1234-1234-123456789012__"
                    )
                },
            )
        assert exc_info.value.code == "secret_resolver_store_unavailable"
        assert exc_info.value.surface == "bash.env"


# ─────────────────────────────────────────────────────────────────────
# (iii) zero-change
# ─────────────────────────────────────────────────────────────────────


class TestProcZeroChange:
    def test_no_overlay_byte_identical_to_p1(
        self, service: SecretService, monkeypatch
    ) -> None:
        """No overlay → the returned env is EXACTLY
        ``os.environ ⊕ tracking_id`` (the P1 behavior, byte-for-byte
        on every key)."""
        env = _build_child_env("proc-test-zc1")
        expected = dict(os.environ)
        expected[_TRACKING_ENV_VAR] = "proc-test-zc1"
        assert env == expected

    def test_marker_free_overlay_passthrough(
        self, service: SecretService
    ) -> None:
        """Marker-free overlay — every value passes through
        BYTE-IDENTICAL (resolver short-circuit)."""
        overlay = {"A": "1", "B": "", "C": "path/to/thing"}
        env = _build_child_env("proc-test-zc2", process_env_overlay=overlay)
        for key, value in overlay.items():
            assert env[key] == value

    def test_zero_audit_rows_on_marker_free_calls(
        self, service: SecretService
    ) -> None:
        """Zero ``use_marker`` audit rows for marker-free calls
        (design §3.4)."""
        _build_child_env("proc-test-zc3")
        _build_child_env(
            "proc-test-zc4", process_env_overlay={"PLAIN": "value"}
        )
        eng = service._engine
        with eng.connect() as conn:
            rows = conn.execute(
                select(SecretAuditEvent).where(  # type: ignore[arg-type]
                    SecretAuditEvent.action == "use_marker"
                )
            ).fetchall()
        assert rows == []

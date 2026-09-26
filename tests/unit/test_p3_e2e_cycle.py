"""P3-WP12 — deterministic end-to-end bootstrap cycle (§6 rows 1, 3, 4, 5).

ONE tight class drives the full §7 Mermaid sequence at the SERVICE
layer — NO real LLM, NO live daemon, SQLite file DB, audited KMS lane
redirected into the test tmp dir:

  swimlane 1  consumer skill load → capability_check miss → envelope
  swimlane 2  installer: mint → configure-builtin → kms_attach (marker)
  swimlane 3  [resume] convention: parse → re-check → present
  swimlane 4  spawn-time resolution: plaintext in-RAM, marker in DB
              (THE load-bearing assertion — same test)
  rows 3/5    stored-config marker-only + plaintext-never-in-context
              scans over this test's own serialized artifacts
  row 4       key-absent fail-closed proof (separate test, same class)

The mock spawn seam uses a ``SimpleNamespace`` stand-in for
``StdioServerParameters`` — exactly the object
``daemon.mcp.connection_manager._create_stdio_session`` would build
from ``resolve_env``'s return value.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine

# Repo-root import path (mirrors test_kms_raw_row_migration.py).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from daemon.repositories.mcp_server import (  # noqa: E402
    SQLModelMcpServerRepository,
)
from daemon.routers.mcp_servers import router as mcp_servers_router  # noqa: E402
from daemon.services.capability_resolver import (  # noqa: E402
    EscalationEnvelope,
    McpLookupResult,
    capability_check,
    enforce_mandatory_first_instruction,
    format_envelope_message,
    format_resume_message,
    parse_envelope_from_message,
    parse_resume_message,
    resume_capability_check,
)
from daemon.services.install_audit import (  # noqa: E402
    INSTALL_AUDIT_RELATIVE_PATH,
)
from daemon.services.kms_lite import (  # noqa: E402
    kms_request,
    kms_resolve_handle,
    reset_store_for_tests,
)
from daemon.services.kms_resolver import is_marker, resolve_env  # noqa: E402
from daemon.tools.infra import create_kms_tools  # noqa: E402
from scripts.migrations.kms_lite_raw_row_migrate import (  # noqa: E402
    _is_secret_env_key,
)

AGENTS_DIR = _REPO_ROOT / "agents"
WORKER_DIR = AGENTS_DIR / "worker"
VERIFY_BODY_PATH = WORKER_DIR / "skills-template" / "opendesign-verify.md"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_env_and_cwd(tmp_path, monkeypatch):
    """Fresh KMS store per test + the audit lane redirected to tmp.

    The canonical audit lane resolves from CWD; chdir isolates both the
    lane and any accidental repo-tree writes. Also pins a clean env so
    a developer's ambient SYSTEM_ENCRYPTION_KEY can never leak in.
    """
    monkeypatch.delenv("SYSTEM_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("SYSTEM_ENCRYPTION_KEY_FILE", raising=False)
    monkeypatch.chdir(tmp_path)
    reset_store_for_tests()
    yield
    reset_store_for_tests()


@pytest.fixture
def kms_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SYSTEM_ENCRYPTION_KEY", key)
    return key


@pytest.fixture
def db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path}/p3_e2e.db",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def cycle(db):
    """HTTP client over the real configure-builtin router + the real
    KMS tools, both wired to the same repository (the installer's
    two write paths, exactly as production composes them)."""
    repository = SQLModelMcpServerRepository(db)
    manager = SimpleNamespace(
        is_write_paused=False,
        _mcp_server_repository=repository,
    )
    app = FastAPI()
    app.state.manager = manager
    api_router = APIRouter(prefix="/api")
    api_router.include_router(mcp_servers_router)
    app.include_router(api_router)

    kms_tools = create_kms_tools(manager, current_instance_id="installer-iid")
    kms_attach_tool = next(t for t in kms_tools if t.name == "kms_attach")

    return SimpleNamespace(
        http=TestClient(app),
        repo=repository,
        kms_attach=kms_attach_tool,
    )


def _mcp_lookup(repo):
    """Project the live ``mcp_servers`` row into the resolver's
    projection shape — the same injectable production wires."""

    def lookup(name: str) -> McpLookupResult | None:
        row = repo.get_mcp_server_by_name(name)
        if row is None:
            return None
        meta = row.instance_metadata or {}
        bindings = meta.get("bound_handles") or []
        return McpLookupResult(
            name=row.name,
            is_active=True,
            config_env=dict((row.config or {}).get("env") or {}),
            requires_secret=True,  # capabilities.yaml: opendesign
            bound_handle=(bindings[-1]["handle"] if bindings else None),
        )

    return lookup


def _audit_path(tmp_path: Path) -> Path:
    return tmp_path / INSTALL_AUDIT_RELATIVE_PATH


def _audit_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ---------------------------------------------------------------------------
# The full cycle — §6 row 1 (+ rows 2, 3, 5 assertions ride along)
# ---------------------------------------------------------------------------


class TestP3BootstrapCycle:
    def test_full_bootstrap_cycle_rows_1_3_5(
        self, cycle, kms_key, tmp_path, caplog
    ) -> None:
        """The §7 Mermaid with no human in the loop. Plaintext exists
        exactly twice: in the KMS store and in the local ``plaintext``
        variable used to prove the resolution seam."""
        repo, http = cycle.repo, cycle.http

        # ── Swimlane 1: consumer skill load → capability miss ─────────
        # The consumer skill body must pass the load-time mandatory-first
        # gate, then its pre-flight misses (no mcp_servers row yet).
        from daemon.services.skill_seed_service import parse_skill_set_file

        entries = parse_skill_set_file(WORKER_DIR / "skill-set.yaml")
        verify_entry = next(e for e in entries if e.name == "opendesign-verify")
        verify_body = VERIFY_BODY_PATH.read_text()
        enforce_mandatory_first_instruction(
            skill_name=verify_entry.name,
            skill_body=verify_body,
            requirement=verify_entry.requirement,
            skill_set_path=str(WORKER_DIR / "skill-set.yaml"),
        )

        lookup = _mcp_lookup(repo)
        check = capability_check("opendesign", mcp_lookup=lookup)
        assert check.state == "missing"

        envelope = EscalationEnvelope.from_check(
            check,
            installer_skill="install-opendesign",
            blocker_scope="this_task",
            resume_hint="step_after_install",
        )
        envelope_wire = format_envelope_message(envelope)
        assert envelope_wire.startswith("Result: ")
        # internal_report source-pattern: the child-report body IS the
        # Result:-prefixed line — parse it back losslessly.
        parsed = parse_envelope_from_message(envelope_wire)
        assert parsed.kind == "capability_missing"
        assert parsed.installer_skill == "install-opendesign"
        assert parsed.resume_hint == "step_after_install"

        # ── Swimlane 2: installer — mint, configure, attach ────────────
        mint = kms_request(
            service="opendesign",
            reason="OD_API_TOKEN for opendesign MCP",
            actor="installer-iid",
        )
        # Worker holds ONLY {handle, fingerprint} — nothing else leaks.
        assert set(mint.keys()) == {"handle", "fingerprint"}
        assert mint["handle"].startswith("KMS_HANDLE_")
        # The plaintext is resolved ONCE, held in this local variable,
        # and is the needle for every plaintext-absence scan below.
        plaintext = kms_resolve_handle(mint["handle"])
        assert plaintext and len(plaintext) >= 32

        resp = http.post(
            "/api/mcp-servers/configure-builtin",
            json={"template_name": "opendesign", "values": {}},
        )
        assert resp.status_code == 201, resp.text
        created = resp.json()
        assert created["name"] == "opendesign"

        row = repo.get_mcp_server_by_name("opendesign")
        assert row is not None
        assert row.instance_metadata.get("install_idempotency_key")

        attach_json = json.loads(
            cycle.kms_attach.invoke(
                {
                    "server_id": row.id,
                    "handle": mint["handle"],
                    "env_key": "OD_API_TOKEN",
                }
            )
        )
        assert attach_json["env_key"] == "OD_API_TOKEN"
        assert attach_json["fingerprint"] == mint["fingerprint"]

        # Row 2 (audit lane): mcp_install AND kms_issue from THIS run,
        # in the ONE canonical lane.
        audit_lines = _audit_events(_audit_path(tmp_path))
        events = [l["event"] for l in audit_lines]
        assert events.count("mcp_install") == 1
        assert events.count("kms_issue") == 1
        kms_line = next(l for l in audit_lines if l["event"] == "kms_issue")
        assert kms_line["secret_ref"] == mint["handle"]
        assert kms_line["actor"] == "installer-iid"

        # ── Swimlane 3: [resume] convention → re-check → present ──────
        resume_wire = format_resume_message(
            parse_resume_message(
                '[resume] {"capability_id": "opendesign", '
                '"status": "installed_but_unconfigured", '
                '"tools_now_available": ["kms_request", "kms_attach"], '
                '"resume_from": "step_after_install"}'
            )
        )
        resume = parse_resume_message(resume_wire)
        assert resume.resume_from == "step_after_install"
        # Consumer contract step 2: NEVER trust the carried status.
        recheck = resume_capability_check(resume, mcp_lookup=lookup)
        assert recheck.state == "present"

        # ── Swimlane 4: spawn-time resolution (LOAD-BEARING) ──────────
        # Exactly what connection_manager._create_stdio_session does:
        # resolve the stored env in-RAM, build server params, spawn.
        fresh_row = repo.get_mcp_server_by_name("opendesign")
        stored_env = dict(fresh_row.config["env"])
        assert is_marker(stored_env["OD_API_TOKEN"])  # row-3 rider

        resolved_env = resolve_env(stored_env)
        assert resolved_env["OD_API_TOKEN"] == plaintext

        server_params = SimpleNamespace(
            command=fresh_row.config["command"],
            args=fresh_row.config["args"],
            env=resolved_env,
        )
        assert server_params.env["OD_API_TOKEN"] == plaintext

        # The DB row MUST still carry the marker after the resolution.
        reread_row = repo.get_mcp_server_by_name("opendesign")
        assert is_marker(reread_row.config["env"]["OD_API_TOKEN"])
        assert reread_row.config["env"] == stored_env

        # ── Row 3: stored config marker-ONLY (whole-env sweep) ────────
        for env_key, value in reread_row.config["env"].items():
            if _is_secret_env_key(env_key) and value:
                assert is_marker(value), f"plaintext in {env_key}"
        assert reread_row.config["env"]["OD_DAEMON_URL"] == (
            "http://127.0.0.1:7456"
        )

        # ── Row 5: plaintext NEVER in context (serialized artifacts) ──
        checkpoint_shaped = [
            {"role": "assistant", "content": envelope_wire},
            {"role": "user", "content": resume_wire},
            {
                "role": "tool",
                "name": "kms_request",
                "content": json.dumps(mint),
            },
            {
                "role": "tool",
                "name": "kms_attach",
                "content": json.dumps(attach_json),
            },
        ]
        surfaces = {
            "envelope_wire": envelope_wire,
            "resume_wire": resume_wire,
            "checkpoint_json": json.dumps(checkpoint_shaped),
            "audit_jsonl": _audit_path(tmp_path).read_text(encoding="utf-8"),
            "caplog": caplog.text,
        }
        for surface_name, surface_text in surfaces.items():
            assert plaintext not in surface_text, (
                f"PLAINTEXT LEAK in {surface_name}"
            )
        # The handle (not the secret) is the only KMS-shaped datum in
        # the audit lane — already asserted above via secret_ref.

    def test_row4_fail_closed_without_key(
        self, cycle, monkeypatch, tmp_path, caplog
    ) -> None:
        """§6 row 4: key absent ⇒ mint refusal, NO plaintext row, clean
        logs. The store is fail-closed — the historical CredentialManager
        plaintext-fallback shape must not appear on the KMS path."""
        repo, http = cycle.repo, cycle.http
        from daemon.services.kms_lite import KMSUnavailableError

        monkeypatch.delenv("SYSTEM_ENCRYPTION_KEY", raising=False)
        monkeypatch.delenv("SYSTEM_ENCRYPTION_KEY_FILE", raising=False)
        reset_store_for_tests()

        # The zero-credential install itself still lands (loopback
        # posture: no secret required) — it's the SECRET slot that must
        # fail closed.
        resp = http.post(
            "/api/mcp-servers/configure-builtin",
            json={"template_name": "opendesign", "values": {}},
        )
        assert resp.status_code == 201, resp.text

        with pytest.raises(KMSUnavailableError):
            kms_request(service="opendesign", reason="row-4 refusal proof")

        # No plaintext row: the row exists but carries NO secret-shaped
        # plaintext value and NO binding.
        row = repo.get_mcp_server_by_name("opendesign")
        assert row is not None
        for env_key, value in (row.config.get("env") or {}).items():
            if _is_secret_env_key(env_key) and value:
                assert is_marker(value), f"plaintext in {env_key}"
        assert not (row.instance_metadata or {}).get("bound_handles")

        # No kms_issue line — the refusal minted nothing to audit.
        events = [l["event"] for l in _audit_events(_audit_path(tmp_path))]
        assert "kms_issue" not in events
        assert "mcp_install" in events  # the install itself is audited

        # Log scan: no handle, no key material in any captured line.
        assert "KMS_HANDLE_" not in caplog.text
        assert "__KMS_REF__" not in caplog.text

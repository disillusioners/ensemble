"""Tests for ``daemon.services.install_audit`` (P3-WP5 fold, arch §7.4).

Pins the documented path-resolution contract:

1. **Preferred** — ``<workdir>/.agents/shared/planning/designer-agent/install-audit.jsonl``.
2. **Fallback** — ``<data_dir>/install-audit.jsonl`` (ENSEMBLE_DATA_DIR
   precedence) when the preferred location is unwritable, logged at
   WARNING and flagged ``fallback_used=True``.

Plus the exact §7.4 record shape, append-only JSONL behavior, and the
never-raises contract. Pure filesystem tests — NO daemon, NO DB.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daemon.services.install_audit import (
    EVENT_MCP_INSTALL,
    INSTALL_AUDIT_RELATIVE_PATH,
    InstallAuditResult,
    append_install_audit,
    resolve_install_audit_path,
)

#: Exact §7.4 field set, in order.
SECTION_74_FIELDS = (
    "ts",
    "event",
    "name",
    "actor",
    "parent",
    "secret_ref",
    "idempotency_key",
    "trace_id",
)


def test_preferred_path_used_when_writable(tmp_path):
    path, is_fallback = resolve_install_audit_path(workdir=tmp_path)
    assert is_fallback is False
    assert path == tmp_path / INSTALL_AUDIT_RELATIVE_PATH
    # The planning tree is created on demand so the first append lands.
    assert path.parent.is_dir()


def test_preferred_append_writes_section74_line(tmp_path):
    result = append_install_audit(
        event=EVENT_MCP_INSTALL,
        name="opendesign",
        actor="worker-instance-1",
        parent="designer-instance-7",
        secret_ref=None,
        idempotency_key="a" * 64,
        trace_id=None,
        workdir=tmp_path,
    )
    assert isinstance(result, InstallAuditResult)
    assert result.written is True
    assert result.fallback_used is False
    assert result.error is None
    assert result.path == str(tmp_path / INSTALL_AUDIT_RELATIVE_PATH)

    lines = (tmp_path / INSTALL_AUDIT_RELATIVE_PATH).read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert tuple(record.keys()) == SECTION_74_FIELDS
    assert record["event"] == "mcp_install"
    assert record["name"] == "opendesign"
    assert record["actor"] == "worker-instance-1"
    assert record["parent"] == "designer-instance-7"
    assert record["secret_ref"] is None
    assert record["idempotency_key"] == "a" * 64
    assert record["trace_id"] is None
    assert record["ts"]  # ISO-8601; writer-stamped
    from datetime import datetime

    datetime.fromisoformat(record["ts"])  # must parse


def test_secret_ref_carries_handle_only(tmp_path):
    """§7.5 handles-not-secrets: the audit lane takes the HANDLE — a
    plaintext value in secret_ref would be a contract breach."""
    append_install_audit(
        event=EVENT_KMS_ISSUE_LIKE(),
        name="opendesign",
        actor="worker-1",
        secret_ref="KMS_HANDLE_abc123",
        workdir=tmp_path,
    )
    content = (tmp_path / INSTALL_AUDIT_RELATIVE_PATH).read_text()
    assert "KMS_HANDLE_abc123" in content


def EVENT_KMS_ISSUE_LIKE():
    from daemon.services.install_audit import EVENT_KMS_ISSUE

    return EVENT_KMS_ISSUE


def test_multiple_appends_are_jsonl(tmp_path):
    for i in range(3):
        append_install_audit(
            event=EVENT_MCP_INSTALL,
            name=f"svc-{i}",
            actor="actor",
            workdir=tmp_path,
        )
    lines = (tmp_path / INSTALL_AUDIT_RELATIVE_PATH).read_text().splitlines()
    assert len(lines) == 3
    assert [json.loads(l)["name"] for l in lines] == ["svc-0", "svc-1", "svc-2"]


def test_fallback_when_preferred_unwritable(tmp_path, monkeypatch, caplog):
    """Make the preferred parent uncreatable: point workdir at a FILE —
    ``mkdir(parents=True)`` under a file path raises OSError regardless
    of uid, so the test never depends on permission bits."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("i am a file")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("ENSEMBLE_DATA_DIR", str(data_dir))

    with caplog.at_level("WARNING"):
        result = append_install_audit(
            event=EVENT_MCP_INSTALL,
            name="opendesign",
            actor="actor",
            workdir=blocker,  # a file — preferred tree cannot exist under it
        )

    assert result.written is True
    assert result.fallback_used is True
    assert result.path == str(data_dir / "install-audit.jsonl")
    record = json.loads((data_dir / "install-audit.jsonl").read_text().splitlines()[0])
    assert tuple(record.keys()) == SECTION_74_FIELDS
    assert any("fall" in (r.message or "") for r in caplog.records)


def test_data_dir_precedence_chain(tmp_path, monkeypatch):
    blocker = tmp_path / "blocker-file"
    blocker.write_text("x")
    monkeypatch.delenv("ENSEMBLE_DATA_DIR", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "legacy-data"))
    result = append_install_audit(
        event=EVENT_MCP_INSTALL, name="n", actor="a", workdir=blocker
    )
    assert result.path == str(tmp_path / "legacy-data" / "install-audit.jsonl")


def test_never_raises_on_total_failure(tmp_path, monkeypatch):
    """Both lanes unwritable → written=False + error text, NO raise."""
    blocker = tmp_path / "blocker-file"
    blocker.write_text("x")
    # Point the fallback at another file-path too.
    data_file = tmp_path / "data-is-a-file"
    data_file.write_text("y")
    monkeypatch.setenv("ENSEMBLE_DATA_DIR", str(data_file))

    result = append_install_audit(
        event=EVENT_MCP_INSTALL, name="n", actor="a", workdir=blocker
    )
    assert result.written is False
    assert result.error  # reason surfaced, never raised


def test_default_workdir_is_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path, is_fallback = resolve_install_audit_path()
    assert is_fallback is False
    assert path == tmp_path / INSTALL_AUDIT_RELATIVE_PATH

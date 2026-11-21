"""Unit tests for council C5 fix: frozen-aware digest_root() resolution.

Pins:
* Frozen branch: sys.frozen True -> root anchored at sys.executable parent.
* Non-frozen default: repo/install root via __file__ parents[2].
* Env override (ENSEMBLE_TOOL_USAGE_DIGEST_ROOT) takes precedence over both.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from daemon.services import tool_usage_digest as tud


def test_frozen_branch_anchors_at_executable_parent(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/tmp/fake-install/bin/ensemble", raising=False)
    # Ensure env override absent.
    monkeypatch.delenv(tud.DIGEST_ROOT_ENV, raising=False)

    root = tud._code_root()
    assert root == Path("/tmp/fake-install/bin/ensemble").parent

    dr = tud.digest_root()
    assert dr == Path("/tmp/fake-install/bin/ensemble").parent / ".agents" / "shared" / "planning" / "tool-usage-metrics-p1" / "digests"


def test_non_frozen_default_uses_file_parents(monkeypatch):
    # Ensure not frozen (default in test process).
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delenv(tud.DIGEST_ROOT_ENV, raising=False)

    expected = Path(tud.__file__).resolve().parents[2]
    assert tud._code_root() == expected
    assert tud.digest_root() == expected / ".agents" / "shared" / "planning" / "tool-usage-metrics-p1" / "digests"


def test_env_override_precedence_over_frozen(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/tmp/fake-install/bin/ensemble", raising=False)
    override = tmp_path / "custom-digests"
    monkeypatch.setenv(tud.DIGEST_ROOT_ENV, str(override))

    assert tud.digest_root() == override


def test_env_override_precedence_over_non_frozen(monkeypatch, tmp_path):
    monkeypatch.delattr(sys, "frozen", raising=False)
    override = tmp_path / "another-digests"
    monkeypatch.setenv(tud.DIGEST_ROOT_ENV, str(override).strip())

    assert tud.digest_root() == Path(str(override).strip())


def test_env_override_empty_string_falls_through(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delenv(tud.DIGEST_ROOT_ENV, raising=False)
    monkeypatch.setenv(tud.DIGEST_ROOT_ENV, "   ")

    # Empty/whitespace env falls through to default.
    expected = Path(tud.__file__).resolve().parents[2]
    assert tud.digest_root() == expected / ".agents" / "shared" / "planning" / "tool-usage-metrics-p1" / "digests"

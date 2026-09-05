"""Resolver-to-graph configuration wiring for attestation modes.

This is the Phase 4 config-wiring neighbor carried into the Phase 5 matrix;
the production helper is ``daemon.services.attestation_resolver`` and the
graph consumes its ``GateSettings`` shape.
"""

from __future__ import annotations

from daemon.services.attestation_gate import GateSettings, resolve_gate_settings, _reset_gate_settings_for_tests
from daemon.services.attestation_resolver import reset_attestation_resolver_for_tests


def test_environment_mode_wires_to_gate_settings(monkeypatch):
    _reset_gate_settings_for_tests()
    reset_attestation_resolver_for_tests()
    monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_MODE", "enforce")
    assert resolve_gate_settings() == GateSettings(mode="enforce", window=3, deny_bound=3)
    _reset_gate_settings_for_tests()
    reset_attestation_resolver_for_tests()
    monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_MODE", "dry")
    assert resolve_gate_settings() == GateSettings(mode="dry", window=3, deny_bound=3)
    reset_attestation_resolver_for_tests()

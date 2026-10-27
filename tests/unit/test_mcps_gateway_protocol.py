"""The wire vocabulary of the remote-hands gateway — the protocol module.

``daemon/services/mcps_gateway/protocol.py`` is the module that holds
EVERY literal of the wire: the envelope, the methods, the capability
map and the typed error codes. It is the module a drift in another file
would be measured against, so its pins live in their own file.

WHAT IS PINNED
--------------

* the version vocabulary — the default (``"0.1"``), the v0.2 version
  and the tuple of SUPPORTED versions (newest first);
* the ``client_terminate`` capability — it exists, it is NOT in the
  centre's static capability map (a v0.1 hello result must not change)
  and it is NOT a REQUIRED capability (a v0.1 machine must not be
  refused for a name it never heard of);
* the ``session.terminate`` method — it is CLIENT-originated and never
  centre-originated;
* the ``MACHINE_ID_CONFLICT`` code — it is a centre-originated code;
* the reserved audit tool names — the centre owns its ledger namespace;
* ``make_envelope`` / ``make_error_envelope`` — the ``v`` member and its
  default.
"""

from __future__ import annotations

from daemon.services.mcps_gateway.protocol import (
    AUDIT_TOOL_HELLO_CONFLICT,
    AUDIT_TOOL_SESSION_END,
    AUDIT_TOOL_SESSION_EXPIRE,
    AUDIT_TOOL_SESSION_TERMINATE,
    CAP_CLIENT_TERMINATE,
    CENTER_EMITTED_CODES,
    CENTER_ORIGINATED_METHODS,
    CLIENT_ORIGINATED_METHODS,
    CODE_INTERNAL,
    CODE_MACHINE_ID_CONFLICT,
    METHOD_SESSION_TERMINATE,
    PROTOCOL_VERSION,
    PROTOCOL_VERSION_V02,
    REQUIRED_CLIENT_CAPABILITIES,
    RESERVED_AUDIT_TOOL_NAMES,
    SERVER_CAPABILITIES,
    SUPPORTED_PROTOCOL_VERSIONS,
    TYPE_ERROR,
    TYPE_REQUEST,
    GatewayProtocolError,
    make_envelope,
    make_error_envelope,
)
from daemon.services.mcps_gateway.tombstones import (
    CENTER_WIRE_REASONS,
    LEDGER_ONLY_REASONS,
    REASON_EXPIRED,
    RESPONSE_REASONS,
    SessionTombstone,
    TombstoneStore,
)


class TestTheVersionVocabulary:
    def test_the_default_version_is_the_historical_one(self) -> None:
        assert PROTOCOL_VERSION == "0.1"

    def test_the_v02_version_is_pinned(self) -> None:
        assert PROTOCOL_VERSION_V02 == "0.2"

    def test_the_supported_versions_are_newest_first(self) -> None:
        assert SUPPORTED_PROTOCOL_VERSIONS == ("0.2", "0.1")

    def test_the_default_is_one_of_the_supported_versions(self) -> None:
        assert PROTOCOL_VERSION in SUPPORTED_PROTOCOL_VERSIONS


class TestTheClientTerminateCapability:
    def test_the_capability_has_its_own_name(self) -> None:
        assert CAP_CLIENT_TERMINATE == "client_terminate"

    def test_it_is_not_in_the_static_capability_map(self) -> None:
        """The static map is what a v0.1 connection advertises — the key
        would change the v0.1 hello result."""
        assert CAP_CLIENT_TERMINATE not in SERVER_CAPABILITIES

    def test_it_is_not_a_required_capability(self) -> None:
        """A v0.1 machine must never be refused for a missing v0.2 cap."""
        assert CAP_CLIENT_TERMINATE not in REQUIRED_CLIENT_CAPABILITIES


class TestTheSessionTerminateMethod:
    def test_it_is_client_originated(self) -> None:
        assert METHOD_SESSION_TERMINATE in CLIENT_ORIGINATED_METHODS

    def test_it_is_never_center_originated(self) -> None:
        assert METHOD_SESSION_TERMINATE not in CENTER_ORIGINATED_METHODS

    def test_its_name_is_pinned(self) -> None:
        assert METHOD_SESSION_TERMINATE == "session.terminate"


class TestTheMachineIdConflictCode:
    def test_the_code_is_pinned(self) -> None:
        assert CODE_MACHINE_ID_CONFLICT == "MACHINE_ID_CONFLICT"

    def test_it_is_a_center_originated_code(self) -> None:
        assert CODE_MACHINE_ID_CONFLICT in CENTER_EMITTED_CODES


class TestTheReservedAuditToolNames:
    def test_the_lifecycle_names_are_reserved(self) -> None:
        for name in (
            AUDIT_TOOL_SESSION_END,
            AUDIT_TOOL_SESSION_EXPIRE,
            AUDIT_TOOL_SESSION_TERMINATE,
            AUDIT_TOOL_HELLO_CONFLICT,
        ):
            assert name in RESERVED_AUDIT_TOOL_NAMES

    def test_the_new_names_are_pinned(self) -> None:
        assert AUDIT_TOOL_SESSION_TERMINATE == "session.terminate"
        assert AUDIT_TOOL_HELLO_CONFLICT == "hello.conflict"


class TestMakeEnvelope:
    def test_the_version_defaults_to_the_historical_one(self) -> None:
        envelope = make_envelope(type=TYPE_REQUEST)
        assert envelope["v"] == PROTOCOL_VERSION

    def test_the_version_can_be_stamped(self) -> None:
        envelope = make_envelope(type=TYPE_REQUEST, v=PROTOCOL_VERSION_V02)
        assert envelope["v"] == PROTOCOL_VERSION_V02

    def test_the_error_envelope_carries_the_version_too(self) -> None:
        envelope = make_error_envelope(
            id="req-1",
            error=GatewayProtocolError(CODE_INTERNAL, "nope"),
            v=PROTOCOL_VERSION_V02,
        )
        assert envelope["v"] == PROTOCOL_VERSION_V02
        assert envelope["type"] == TYPE_ERROR


class TestTheReasonVocabulary:
    def test_the_wire_reasons_carry_the_response_reasons(self) -> None:
        assert RESPONSE_REASONS <= CENTER_WIRE_REASONS

    def test_the_ledger_only_reasons_are_never_wire_reasons(self) -> None:
        """The disjointness invariant — a ledger-only token in a response
        is a bug by construction."""
        assert not (LEDGER_ONLY_REASONS & CENTER_WIRE_REASONS)


class TestTheTombstoneLookup:
    def test_a_tombstone_is_found_by_its_session_id(self) -> None:
        store = TombstoneStore()
        store.record(
            SessionTombstone(
                session_id="sess-alpha",
                reason=REASON_EXPIRED,
                at=1_000.0,
                agent_id="ari",
            )
        )
        found = store.get("sess-alpha", now=1_000.0)
        assert found is not None
        # The WIRE vocabulary is pinned by the literal, not by the
        # constant that produced it (the anti-tautology rule).
        assert found.reason == "expired"

    def test_an_unknown_session_id_is_not_found(self) -> None:
        store = TombstoneStore()
        assert store.get("sess-nobody", now=1_000.0) is None

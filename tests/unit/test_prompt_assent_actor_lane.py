"""Phase-1 actor-lane tests — the ``assent-relay`` value + the attestation gate.

Covers phase1-plan task 1.5 / 1.6 / 1.7 acceptance:

* ``_AUTH_METHODS`` has FOUR values (session / bearer / internal /
  assent-relay);
* an ``assent-relay`` actor CANNOT be built without a complete typed
  attestation (and ``actor_from_assent_relay`` is the only minter);
* the existing three lanes' constructors behave UNCHANGED
  (``actor_from_request`` still refuses internal + unknown methods;
  ``actor_from_instance`` still mints internal);
* ``approve()`` / ``reject()`` still refuse internal actors (F4
  unchanged) and still refuse an assent-relay actor — the attestation-
  bearing entrypoints are the ONLY lane for the relay (the clamp admits
  ``assent-relay`` only when the D-4 consume hook rides the call);
* ``approve_assent`` / ``reject_assent`` refuse non-relay actors and
  attestation-less calls.
"""

from __future__ import annotations

import pytest

from daemon.repositories.prompt_version.assent_attestation import (
    AssentAttestation,
    AssentItemBinding,
    AssentHumanIdentity,
)
from daemon.repositories.prompt_version.repository import (
    AUTH_METHOD_ASSENT_RELAY,
    AUTH_METHOD_BEARER,
    AUTH_METHOD_INTERNAL,
    AUTH_METHOD_SESSION,
    ActorContext,
    InvalidTransitionError,
    PromptVersionRepository,
    _AUTH_METHODS,
    actor_from_assent_relay,
    actor_from_instance,
    actor_from_request,
)
from tests.helpers.prompt_assent_fixture import (  # noqa: F401 — fixtures
    build_attestation,
    build_relay_actor,
    pending_row,
    pv_repo,
    sha256_hex,
    sqlite_engine,
    token_repo,
)


class TestAuthMethodVocabulary:
    def test_four_values(self):
        assert _AUTH_METHODS == frozenset({
            AUTH_METHOD_SESSION,
            AUTH_METHOD_BEARER,
            AUTH_METHOD_INTERNAL,
            AUTH_METHOD_ASSENT_RELAY,
        })
        assert AUTH_METHOD_ASSENT_RELAY == "assent-relay"
        assert len(AUTH_METHOD_ASSENT_RELAY) == 12  # inside the 32-char column


class TestAssentRelayConstruction:
    def test_actor_requires_typed_attestation(self):
        with pytest.raises(ValueError, match="AssentAttestation"):
            ActorContext(
                subject="inst-relay-1",
                auth_method=AUTH_METHOD_ASSENT_RELAY,
                instance_id="inst-relay-1",
                agent_id="ari",
                attestation=None,
            )

    def test_actor_requires_relay_provenance(self):
        att = build_attestation()
        with pytest.raises(ValueError, match="instance_id AND"):
            ActorContext(
                subject=att.relay_instance_id,
                auth_method=AUTH_METHOD_ASSENT_RELAY,
                instance_id=None,
                agent_id=None,
                attestation=att,
            )

    def test_constructor_is_the_only_minter(self):
        att = build_attestation()
        actor = build_relay_actor(att)
        assert actor.auth_method == AUTH_METHOD_ASSENT_RELAY
        assert actor.attestation is att
        assert actor.subject == att.relay_instance_id
        assert actor.instance_id == att.relay_instance_id
        assert actor.agent_id == att.relay_agent == "ari"

    def test_constructor_refuses_non_attestation(self):
        with pytest.raises(ValueError, match="typed"):
            actor_from_assent_relay({"token_id": "forged"})  # type: ignore[arg-type]

    def test_attestation_completeness_enforced_at_construction(self):
        base = build_attestation()
        # Missing render_sha256 (K-7: the retired draft's field set).
        with pytest.raises(ValueError, match="render_sha256"):
            AssentAttestation(
                item_binding=base.item_binding,
                render_sha256="",
                human_message_id=base.human_message_id,
                relay_source_message_id=base.relay_source_message_id,
                human_identity=base.human_identity,
                approver_subject=base.approver_subject,
                token_id=base.token_id,
                token_fingerprint=base.token_fingerprint,
                window_stamped_at=base.window_stamped_at,
                window_expires_at=base.window_expires_at,
                relay_agent=base.relay_agent,
                relay_instance_id=base.relay_instance_id,
                attested_at=base.attested_at,
            )
        # Missing relay_source_message_id (the §2.3 forensic-chain field
        # the pre-ruling decisions.md field set lacked).
        with pytest.raises(ValueError, match="relay_source_message_id"):
            AssentAttestation(
                item_binding=base.item_binding,
                render_sha256=base.render_sha256,
                human_message_id=base.human_message_id,
                relay_source_message_id="",
                human_identity=base.human_identity,
                approver_subject=base.approver_subject,
                token_id=base.token_id,
                token_fingerprint=base.token_fingerprint,
                window_stamped_at=base.window_stamped_at,
                window_expires_at=base.window_expires_at,
                relay_agent=base.relay_agent,
                relay_instance_id=base.relay_instance_id,
                attested_at=base.attested_at,
            )
        # approver_subject must equal f"{source_id}:{user_id}" (D-5).
        with pytest.raises(ValueError, match="approver_subject"):
            AssentAttestation(
                item_binding=base.item_binding,
                render_sha256=base.render_sha256,
                human_message_id=base.human_message_id,
                relay_source_message_id=base.relay_source_message_id,
                human_identity=base.human_identity,
                approver_subject="someone-else:1",
                token_id=base.token_id,
                token_fingerprint=base.token_fingerprint,
                window_stamped_at=base.window_stamped_at,
                window_expires_at=base.window_expires_at,
                relay_agent=base.relay_agent,
                relay_instance_id=base.relay_instance_id,
                attested_at=base.attested_at,
            )
        # token_fingerprint is sha256[:12] — never the raw token.
        with pytest.raises(ValueError, match="token_fingerprint"):
            build_attestation(token_id="tok-x").__class__(
                **{
                    **build_attestation(token_id="tok-x").__dict__,
                }
                | {"token_fingerprint": "CONFIRM-AAAA"}
            )

    def test_existing_lanes_unchanged(self):
        # actor_from_instance still mints internal.
        a = actor_from_instance(instance_id="inst-1", agent_id="dev")
        assert a.auth_method == AUTH_METHOD_INTERNAL
        assert a.attestation is None

        # actor_from_request still refuses internal + unknown methods.
        class _Req:
            class state:  # noqa: N801 - mirrors request.state
                auth_context = type(
                    "A", (), {"auth_method": "internal", "subject": "x"}
                )()

        with pytest.raises(ValueError, match="forgery"):
            actor_from_request(_Req())

        class _Req2:
            class state:  # noqa: N801
                auth_context = type(
                    "A", (), {"auth_method": "assent-relay", "subject": "x"}
                )()

        with pytest.raises(ValueError, match="unknown auth_method"):
            actor_from_request(_Req2())


class TestEntrypointRefusals:
    def test_approve_refuses_internal_actors_f4_unchanged(
        self, pv_repo, pending_row  # noqa: F811
    ):
        internal = actor_from_instance(
            instance_id="inst-1", agent_id="ari"
        )
        with pytest.raises(ValueError, match="only HTTP actors"):
            pv_repo.approve(pending_row.version_id, internal)
        with pytest.raises(ValueError, match="only HTTP actors"):
            pv_repo.reject(pending_row.version_id, internal)

    def test_approve_refuses_relay_actor_without_hook(
        self, pv_repo, pending_row  # noqa: F811
    ):
        """The FE-facing approve() cannot admit a relay actor: the clamp
        accepts assent-relay ONLY when the D-4 hook rides the call —
        the attestation-bearing entrypoints are the only relay lane."""
        att = build_attestation(version_id=pending_row.version_id)
        relay = build_relay_actor(att)
        with pytest.raises(ValueError, match="consume hook"):
            pv_repo.approve(pending_row.version_id, relay)
        with pytest.raises(ValueError, match="consume hook"):
            pv_repo.reject(pending_row.version_id, relay)

    def test_assent_entrypoints_refuse_non_relay_actors(
        self, pv_repo, pending_row  # noqa: F811
    ):
        att = build_attestation(version_id=pending_row.version_id)
        session_actor = ActorContext(
            subject="emperor", auth_method=AUTH_METHOD_SESSION
        )
        with pytest.raises(ValueError, match="assent-relay"):
            pv_repo.approve_assent(
                pending_row.version_id, session_actor, att
            )
        internal = actor_from_instance(
            instance_id="inst-1", agent_id="ari"
        )
        with pytest.raises(ValueError, match="assent-relay"):
            pv_repo.reject_assent(
                pending_row.version_id, internal, att, "no"
            )

    def test_assent_entrypoints_refuse_attestation_less_calls(
        self, pv_repo, pending_row  # noqa: F811
    ):
        att = build_attestation(version_id=pending_row.version_id)
        relay = build_relay_actor(att)
        with pytest.raises(ValueError, match="AssentAttestation"):
            pv_repo.approve_assent(
                pending_row.version_id, relay, None  # type: ignore[arg-type]
            )
        with pytest.raises(ValueError, match="AssentAttestation"):
            pv_repo.reject_assent(
                pending_row.version_id,
                relay,
                {"token_id": "forged"},  # type: ignore[arg-type]
                "no",
            )

    def test_assent_entrypoints_refuse_mismatched_pair(
        self, pv_repo, pending_row  # noqa: F811
    ):
        att = build_attestation(version_id=pending_row.version_id)
        other = build_attestation(version_id=pending_row.version_id, token_id="tok-2")
        relay_of_other = build_relay_actor(other)
        with pytest.raises(ValueError, match="SAME instance"):
            pv_repo.approve_assent(pending_row.version_id, relay_of_other, att)

    def test_assent_entrypoints_refuse_wrong_item_binding(
        self, pv_repo, pending_row  # noqa: F811
    ):
        att = build_attestation(version_id="some-other-row")
        relay = build_relay_actor(att)
        with pytest.raises(ValueError, match="does not match"):
            pv_repo.approve_assent(pending_row.version_id, relay, att)

    def test_reject_assent_requires_reason(self, pv_repo, pending_row):  # noqa: F811
        att = build_attestation(version_id=pending_row.version_id)
        relay = build_relay_actor(att)
        with pytest.raises(ValueError, match="reason is REQUIRED"):
            pv_repo.reject_assent(
                pending_row.version_id, relay, att, "   "
            )
        with pytest.raises(ValueError, match="≤500"):
            pv_repo.reject_assent(
                pending_row.version_id, relay, att, "x" * 501
            )

    def test_relayed_decision_consumes_and_lands(self, pv_repo, pending_row, token_repo):  # noqa: F811
        """The full relay path with the D-4 hook: the row lands approved
        AND the token is consumed in the same transaction."""
        from daemon.repositories.prompt_assent.consume_hook import (
            build_assent_consume_hook,
        )
        from daemon.repositories.prompt_assent.models import PromptAssentToken  # noqa: F401
        from daemon.services.prompt_version_diff import (
            canonical_render,
            render_sha256,
        )
        from daemon.repositories.prompt_version.repository import (
            actor_from_instance,
        )

        eff = pv_repo.effective_for_render(AGENT := "ari", "", ARTIFACT_KEY)
        mint_sha = render_sha256(
            agent_id=AGENT,
            version_tag="",
            artifact=ARTIFACT_KEY,
            version=pending_row.version,
            version_id=pending_row.version_id,
            pending_content=pending_row.content,
            effective_content=eff.content or "",
            effective_version_id=eff.version_id,
        )
        raw_token = "CONFIRM-ZZ99ZZ99"
        row = token_repo.mint(
            version_id=pending_row.version_id,
            agent_id=AGENT,
            version_tag="",
            artifact=ARTIFACT_KEY,
            version=pending_row.version,
            token_hash=sha256_hex(raw_token),
            render_sha256=mint_sha,
        )
        att = build_attestation(
            version_id=pending_row.version_id,
            token_id=row.token_id,
            raw_token=raw_token,
            render_sha256=mint_sha,
        )
        relay = build_relay_actor(att)
        hook = build_assent_consume_hook(
            token_repo=token_repo,
            pv_repo=pv_repo,
            token_id=row.token_id,
            consumed_by_message_id="msg-approve-1",
        )
        out = pv_repo.approve_assent(
            pending_row.version_id, relay, att, _consume_hook=hook
        )
        assert out.state == "approved"
        consumed = token_repo.find_by_id(row.token_id)
        assert consumed.status == "consumed"
        assert consumed.consumed_by_message_id == "msg-approve-1"

    def test_relayed_decision_without_consume_hook_is_refused_at_clamp(
        self, pv_repo, pending_row, token_repo  # noqa: F811
    ):
        raw_token = "CONFIRM-ZZ98ZZ98"
        row = token_repo.mint(
            version_id=pending_row.version_id,
            agent_id="ari",
            version_tag="",
            artifact=ARTIFACT_KEY,
            version=pending_row.version,
            token_hash=sha256_hex(raw_token),
            render_sha256=sha256_hex("render"),
        )
        att = build_attestation(
            version_id=pending_row.version_id,
            token_id=row.token_id,
            raw_token=raw_token,
        )
        relay = build_relay_actor(att)
        with pytest.raises(ValueError, match="consume hook"):
            pv_repo.approve_assent(pending_row.version_id, relay, att)
        # Refusals never burn the token (transition-then-consume).
        assert token_repo.find_by_id(row.token_id).status == "live"
        assert (
            pv_repo.get_version(pending_row.version_id).state
            == "pending_approval"
        )


ARTIFACT_KEY = "rule"

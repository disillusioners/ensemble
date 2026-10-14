"""Prompt-version content-mutation tests — USER DIRECTIVE #2 (2026-10-14).

Repo-level coverage for the CRUD + badge API deltas on the
agent-prompt-immutability Phase B endpoint family
(``daemon/repositories/prompt_version/repository.py``):

* ``update_version_content`` — draft/pending rows ONLY; content_hash
  recompute; UPDATED audit row stamped INSIDE the same
  ``engine.begin()`` with prior/new content_hash + byte sizes;
  typed ``VersionNotEditableError`` carrying the current state for
  approved / rejected / archived.
* ``delete_version`` — draft/pending rows ONLY; HARD row delete;
  DELETED audit event carrying identity + content_hash + content
  byte length in the payload (forensics survive the row); typed
  ``VersionNotDeletableError`` for non-deletable states.
* ``count_pending`` — plain count of ``pending_approval`` rows; the
  badge feed drains as approve/reject land.

The state guards live at THIS layer (the repo), so the guard tests
run against real engines — the SC8 dual-dialect fixture set from the
T10 canonical file (fixture-import precedent:
``tests/test_prompt_version_dualdriver.py``). The router-level
error-MAPPING tests (mock service) live in
``tests/test_prompts_router.py``; the service-layer pass-through of
the three new wrappers is covered at the bottom of this file.

Leader ruling (binding with frozen A1): EDIT/DELETE apply to
draft/pending ONLY. Approved rows are append-only immutable history
(hard-delete of approved history is a MORNING FORK — deliberately
NOT built); rejected is terminal (resubmit = new row); archived is
superseded.

Test execution is fenced per the project critical notes:

* ``source .smoke/test-fence.sh`` BEFORE invoking pytest —
  ENSEMBLE_SELF_ENV=dev; POSTGRES_DB=ensemble_dev_test_only;
  FAKE_HOME under mktemp; DAEMON_PORT=8079.
* Run only the named file (``--tb=short -q``), never bare pytest,
  never ``-x``, never full-suite.
"""

from __future__ import annotations

import asyncio

import pytest

from daemon.repositories.prompt_version.models import PROMPT_AUDIT_EVENT_TYPES
from daemon.repositories.prompt_version.repository import (
    AUTH_METHOD_SESSION,
    ActorContext,
    PromptVersionRepository,
    VersionNotDeletableError,
    VersionNotEditableError,
    actor_from_instance,
)
from daemon.services.prompt_version_service import PromptVersionService

# True fixture reuse — the Phase A T10 canonical fixtures
# (db_dialect / repo_engine / repo / internal_actor / http_actor).
# Importing registers them for THIS module's tests; the parametrize
# below overrides the sqlite default on the guard-matrix classes.
from tests.test_prompt_version_resolution import (  # noqa: F401
    db_dialect,
    http_actor,
    internal_actor,
    repo,
    repo_engine,
)


def _make_draft(repo: PromptVersionRepository, actor: ActorContext,
                content: str = "original body"):
    """Create + submit a pending row and return it (plus the draft twin)."""
    return repo.create_draft("udev", "", "soul", content, actor)


def _audit_rows_for(repo: PromptVersionRepository, version_id: str):
    """All audit rows for one version_id, oldest first."""
    items, _total = repo.list_audit_events(version_id=version_id, limit=100)
    return list(reversed(items))  # list_audit_events is DESC


# ─────────────────────────────────────────────────────────────────────────────
# update_version_content — happy paths + guard matrix
# ─────────────────────────────────────────────────────────────────────────────


class TestUpdateVersionContent:
    def test_update_draft_recomputes_hash_and_stamps_updated_audit(
        self, repo, internal_actor, http_actor
    ):
        row = _make_draft(repo, internal_actor)
        updated = repo.update_version_content(
            row.version_id, "edited body", http_actor
        )
        assert updated.content == "edited body"
        # content_hash recomputed (sha256 of the new content).
        import hashlib

        assert updated.content_hash == hashlib.sha256(
            b"edited body"
        ).hexdigest()
        assert updated.state == "draft"
        # Persisted truth matches the snapshot.
        reread = repo.get_version(row.version_id)
        assert reread is not None
        assert reread.content == "edited body"
        assert reread.content_hash == updated.content_hash
        # UPDATED audit row exists and is a valid enum member.
        assert "UPDATED" in PROMPT_AUDIT_EVENT_TYPES
        audits = _audit_rows_for(repo, row.version_id)
        updated_events = [a for a in audits if a.event_type == "UPDATED"]
        assert len(updated_events) == 1
        ev = updated_events[0]
        # Actor stamped with the session lane identity (A3).
        assert ev.actor_subject == http_actor.subject
        assert ev.actor_auth_method == AUTH_METHOD_SESSION
        # Payload forensics: prior + new hash, both byte sizes.
        payload = dict(ev.payload or {})
        assert payload["prior_content_hash"] == row.content_hash
        assert payload["new_content_hash"] == updated.content_hash
        assert payload["prior_content_bytes"] == len(b"original body")
        assert payload["new_content_bytes"] == len(b"edited body")
        assert payload["state"] == "draft"
        assert payload["version"] == row.version

    def test_update_pending_row_allowed(
        self, repo, internal_actor, http_actor
    ):
        row = _make_draft(repo, internal_actor)
        repo.submit_for_approval(row.version_id, internal_actor)
        updated = repo.update_version_content(
            row.version_id, "pending edit", http_actor
        )
        assert updated.state == "pending_approval"
        assert updated.content == "pending edit"
        audits = _audit_rows_for(repo, row.version_id)
        assert any(a.event_type == "UPDATED" for a in audits)

    @pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])
    @pytest.mark.parametrize("state_driver", ["approve", "reject", "archive"])
    def test_update_non_editable_state_raises_typed_error(
        self, repo, db_dialect, internal_actor, http_actor, state_driver
    ):
        """The guard matrix: approved / rejected / archived all refuse
        the edit with the typed error carrying the CURRENT state —
        and the row stays untouched (append-only immutable history)."""
        row = _make_draft(repo, internal_actor)
        if state_driver == "approve":
            repo.submit_for_approval(row.version_id, internal_actor)
            repo.approve(row.version_id, http_actor)
        elif state_driver == "reject":
            repo.submit_for_approval(row.version_id, internal_actor)
            repo.reject(row.version_id, http_actor)
        else:
            repo.atomic_transition(
                row.version_id, "draft", "archived", internal_actor
            )
        expected_state = {
            "approve": "approved",
            "reject": "rejected",
            "archive": "archived",
        }[state_driver]

        with pytest.raises(VersionNotEditableError) as excinfo:
            repo.update_version_content(row.version_id, "tamper", http_actor)
        assert excinfo.value.current_state == expected_state
        assert excinfo.value.version_id == row.version_id
        # Row unchanged — the immutable history is intact.
        reread = repo.get_version(row.version_id)
        assert reread.state == expected_state
        assert reread.content == "original body"
        # No UPDATED audit row leaked (the txn rolled back).
        audits = _audit_rows_for(repo, row.version_id)
        assert not any(a.event_type == "UPDATED" for a in audits)

    def test_update_unknown_version_raises_lookup_error(
        self, repo, http_actor
    ):
        with pytest.raises(LookupError):
            repo.update_version_content("no-such-version", "x", http_actor)

    def test_update_effective_chain_follows_the_edit(
        self, repo, internal_actor, http_actor
    ):
        """Editing a PENDING row changes what approval will serve —
        the edit is visible through the F6 chain once approved."""
        row = _make_draft(repo, internal_actor)
        repo.submit_for_approval(row.version_id, internal_actor)
        repo.update_version_content(row.version_id, "approved text", http_actor)
        repo.approve(row.version_id, http_actor)
        assert repo.get_effective("udev", "", "soul") == "approved text"


# ─────────────────────────────────────────────────────────────────────────────
# delete_version — happy paths + guard matrix
# ─────────────────────────────────────────────────────────────────────────────


class TestDeleteVersion:
    def test_delete_draft_hard_deletes_and_stamps_deleted_audit(
        self, repo, internal_actor, http_actor
    ):
        row = _make_draft(repo, internal_actor, content="doomed body")
        snapshot = repo.delete_version(row.version_id, http_actor)
        assert snapshot.version_id == row.version_id
        # HARD delete — the row is gone.
        assert repo.get_version(row.version_id) is None
        # DELETED audit event survives the row.
        assert "DELETED" in PROMPT_AUDIT_EVENT_TYPES
        audits = _audit_rows_for(repo, row.version_id)
        deleted_events = [a for a in audits if a.event_type == "DELETED"]
        assert len(deleted_events) == 1
        ev = deleted_events[0]
        assert ev.actor_subject == http_actor.subject
        assert ev.actor_auth_method == AUTH_METHOD_SESSION
        # Identity + content forensics ride the payload.
        payload = dict(ev.payload or {})
        assert payload["agent_id"] == "udev"
        assert payload["version_tag"] == ""
        assert payload["artifact"] == "soul"
        assert payload["version"] == row.version
        assert payload["state"] == "draft"
        assert payload["content_hash"] == row.content_hash
        assert payload["content_bytes"] == len(b"doomed body")
        # The audit row's own identity columns carry the key too.
        assert ev.agent_id == "udev"
        assert ev.artifact == "soul"
        assert ev.version_id == row.version_id

    def test_delete_pending_row_allowed(
        self, repo, internal_actor, http_actor
    ):
        row = _make_draft(repo, internal_actor)
        repo.submit_for_approval(row.version_id, internal_actor)
        repo.delete_version(row.version_id, http_actor)
        assert repo.get_version(row.version_id) is None

    @pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])
    @pytest.mark.parametrize("state_driver", ["approve", "reject", "archive"])
    def test_delete_non_deletable_state_raises_typed_error(
        self, repo, db_dialect, internal_actor, http_actor, state_driver
    ):
        """Approved history is append-only immutable — hard-delete is
        refused with the typed error; the row SURVIVES. (Hard-delete
        of approved history = morning fork, deliberately not built.)"""
        row = _make_draft(repo, internal_actor)
        if state_driver == "approve":
            repo.submit_for_approval(row.version_id, internal_actor)
            repo.approve(row.version_id, http_actor)
        elif state_driver == "reject":
            repo.submit_for_approval(row.version_id, internal_actor)
            repo.reject(row.version_id, http_actor)
        else:
            repo.atomic_transition(
                row.version_id, "draft", "archived", internal_actor
            )
        expected_state = {
            "approve": "approved",
            "reject": "rejected",
            "archive": "archived",
        }[state_driver]

        with pytest.raises(VersionNotDeletableError) as excinfo:
            repo.delete_version(row.version_id, http_actor)
        assert excinfo.value.current_state == expected_state
        # Row survives.
        reread = repo.get_version(row.version_id)
        assert reread is not None
        assert reread.state == expected_state
        # No DELETED audit row leaked.
        audits = _audit_rows_for(repo, row.version_id)
        assert not any(a.event_type == "DELETED" for a in audits)

    def test_delete_unknown_version_raises_lookup_error(
        self, repo, http_actor
    ):
        with pytest.raises(LookupError):
            repo.delete_version("no-such-version", http_actor)

    def test_deleted_row_never_affects_effective(
        self, repo, internal_actor, http_actor
    ):
        """A deleted draft/pending row leaves the effective chain
        untouched (it never contributed — F6 reads selected/approved
        only)."""
        row = _make_draft(repo, internal_actor)
        before = repo.get_effective("udev", "", "soul")
        repo.delete_version(row.version_id, http_actor)
        assert repo.get_effective("udev", "", "soul") == before


# ─────────────────────────────────────────────────────────────────────────────
# count_pending — the badge feed
# ─────────────────────────────────────────────────────────────────────────────


class TestCountPending:
    def test_empty_store_counts_zero(self, repo):
        assert repo.count_pending() == 0

    def test_count_grows_with_submissions_and_drains_on_decisions(
        self, repo, internal_actor, http_actor
    ):
        """N pending → count=N → approve one → N-1 → reject another →
        N-2. Drafts do NOT count (the badge answers "awaiting
        approval", not "all work in flight")."""
        a = repo.create_draft("cnt", "", "soul", "a", internal_actor)
        b = repo.create_draft("cnt", "", "rule", "b", internal_actor)
        c = repo.create_draft("cnt", "", "workflow", "c", internal_actor)
        d = repo.create_draft("cnt", "", "tools_note", "d", internal_actor)
        # All drafts, none submitted — count stays 0.
        assert repo.count_pending() == 0

        repo.submit_for_approval(a.version_id, internal_actor)
        repo.submit_for_approval(b.version_id, internal_actor)
        repo.submit_for_approval(c.version_id, internal_actor)
        assert repo.count_pending() == 3

        repo.approve(a.version_id, http_actor)
        assert repo.count_pending() == 2

        repo.reject(b.version_id, http_actor)
        assert repo.count_pending() == 1

        # Deleting the last pending row drains it too (UD#2 DELETE is
        # a legitimate badge-drain path).
        repo.delete_version(c.version_id, http_actor)
        assert repo.count_pending() == 0

        # d remains a draft — never counted.
        assert repo.get_version(d.version_id).state == "draft"
        assert repo.count_pending() == 0

    def test_count_is_global_not_per_key(
        self, repo, internal_actor
    ):
        """The badge is a daemon-wide count across keys/agents."""
        repo.create_draft("agent-one", "", "soul", "x", internal_actor)
        two = repo.create_draft("agent-two", "v2", "rule", "y", internal_actor)
        repo.submit_for_approval(two.version_id, internal_actor)
        assert repo.count_pending() == 1


# ─────────────────────────────────────────────────────────────────────────────
# Service-layer pass-through (deliverable 2 — thin async wrappers)
# ─────────────────────────────────────────────────────────────────────────────


class TestServiceWrappers:
    """The async facade exposes the three new repo methods verbatim
    (state guards stay repo-side; the service is the thin bridge)."""

    @pytest.fixture
    def service(self, repo) -> PromptVersionService:
        return PromptVersionService(repo=repo)

    def test_service_update_pass_through(
        self, repo, service, internal_actor, http_actor
    ):
        row = _make_draft(repo, internal_actor)
        updated = asyncio.run(
            service.update_version_content(row.version_id, "svc edit", http_actor)
        )
        assert updated.content == "svc edit"
        assert repo.get_version(row.version_id).content == "svc edit"

    def test_service_update_surfaces_typed_error(
        self, repo, service, internal_actor, http_actor
    ):
        row = _make_draft(repo, internal_actor)
        repo.submit_for_approval(row.version_id, internal_actor)
        repo.approve(row.version_id, http_actor)
        with pytest.raises(VersionNotEditableError):
            asyncio.run(
                service.update_version_content(
                    row.version_id, "nope", http_actor
                )
            )

    def test_service_delete_pass_through(
        self, repo, service, internal_actor, http_actor
    ):
        row = _make_draft(repo, internal_actor)
        snapshot = asyncio.run(service.delete_version(row.version_id, http_actor))
        assert snapshot.version_id == row.version_id
        assert repo.get_version(row.version_id) is None

    def test_service_count_pending_pass_through(
        self, repo, service, internal_actor
    ):
        assert asyncio.run(service.count_pending()) == 0
        row = repo.create_draft("svc", "", "soul", "x", internal_actor)
        repo.submit_for_approval(row.version_id, internal_actor)
        assert asyncio.run(service.count_pending()) == 1

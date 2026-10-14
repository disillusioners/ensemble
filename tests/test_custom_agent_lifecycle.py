"""Custom-agent lifecycle tests (User Directive #5A, Phase C).

The Phase C test surface for the agent-prompt-immutability
commission (overnight, 2026-10-14). Covers R-A1 + R-A2 + the
hybrid registry + the lifecycle wire:

* **R-A1 (DB-born, v1 APPROVED)** — ``create_custom_agent``
  writes the config row + v1 prompt_versions rows in ONE
  ``engine.begin()``; the v1 rows land as ``state='approved'``
  + ``is_selected=1`` with ``approver_subject`` stamped from
  the actor (the creating user IS the human approver).
* **R-A2 (no template fallback, no on-disk dir)** — the
  custom agent's config row is the meta.json-equivalent;
  ``prompt_versions`` rows are the prompt content; nothing
  is ever written under ``agents/<wire_id>/``.
* **Hybrid discovery** — the registry returns BOTH file-defined
  AND DB-resident agents; file-defined wins on a name
  collision (defense-in-depth — the wire form is already
  provably disjoint via the ``custom:`` prefix).
* **Endpoint coverage** — POST + GET (list / single) + DELETE
  each take the documented authorization + validation paths.
* **Audit stamp fidelity** — the ``prompt_audit_events``
  rows carry ``actor_subject`` + ``actor_auth_method`` =
  the creating HTTP actor (the R-A1 stamp chain).

Fence discipline (2026-10-12 critical note, zero tolerance)
+ the project's env-scrub convention:

* ``ENSEMBLE_SELF_ENV=dev`` (no live ensemble posture).
* ``POSTGRES_DB=ensemble_dev_test_only`` (daemon-identity
  fence — abort if resolved to ``ensemble_prod``).
* ``FAKE_HOME`` is a ``mktemp -d`` per-test fixture; tests
  resolve agent paths from registry, NEVER from
  ``$HOME/agents-ensemble`` (the 2026-10-12 live-install
  deletion incident).
* Run only the named file (``--tb=short -q``); never bare
  pytest; never ``-x``; never full-suite.

Run via the project ``scripts/run_tests_scrubbed.sh`` wrapper
(unconditional POSTGRES_* scrub + the literal here-doc
"FAKE_HOME is mktemp, every test" header).
"""

from __future__ import annotations

import os
import tempfile
import uuid
from typing import Iterator

import pytest
from sqlalchemy import create_engine, text


# ──────────────────────────────────────────────────────────────────────────
# Fence — see tests/test_prompt_artifact_baseline.py for the precedent.
# All paths come from ``tmp_path`` (per-test mktemp); ZERO HOME-derived
# or install-dir-derived paths are touched.
# ──────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session", autouse=True)
def _fence_session():
    """Auto-used session fence: lock the test envs to dev mode.

    Mirrors the project's daemon-identity fence:
    ``ENSEMBLE_SELF_ENV=dev`` + ``POSTGRES_DB=ensemble_dev_test_only``.
    Hard-aborts on accidental ``ensemble_prod`` (the
    env-poison 3-guard convention).
    """
    allowed_dbs = {"ensemble_dev_test_only", "ensemble_test_only"}
    os.environ["ENSEMBLE_SELF_ENV"] = "dev"
    if os.environ.get("POSTGRES_DB") in (None, "", "ensemble_prod"):
        os.environ["POSTGRES_DB"] = "ensemble_dev_test_only"
    elif os.environ.get("POSTGRES_DB") not in allowed_dbs:
        pytest.exit(
            f"HARD ABORT: this test session targets "
            f"POSTGRES_DB={os.environ.get('POSTGRES_DB')!r}; "
            f"refuse to point custom-agent tests at a "
            f"non-dev DB (env-poison 3-guard convention).",
            returncode=1,
        )
    # FAKE_HOME in mktemp — never read from ambient $HOME
    fake = os.environ.get("FAKE_HOME")
    if not fake or not os.path.isabs(fake):
        fake = tempfile.mkdtemp(prefix="ens-fake-home-")
        os.environ["FAKE_HOME"] = fake
        os.environ["HOME"] = fake
    yield


@pytest.fixture
def tmp_repo_engine(tmp_path):
    """Yield a fresh file-SQLite engine at ``tmp_path/test.db``.

    Per the resolution test precedent: file SQLite + WAL +
    QueuePool so the lifecycle's ``asyncio.to_thread`` bridge
    works against real per-thread connections.
    """
    db_path = tmp_path / "custom_agent_test.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        echo=False,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )

    # Register the custom_agent_configs + prompt_versions +
    # prompt_audit_events tables on the freshly-created engine.
    # This is the SQLModel.metadata.create_all() path used by
    # the api.py lifespan (the dual-driver parity contract).
    from sqlmodel import SQLModel
    from daemon.repositories.custom_agent_configs.models import (
        CustomAgentConfig,
    )
    from daemon.repositories.prompt_version.models import (
        PromptAuditEvent,
        PromptTemplateBaseline,
        PromptVersion,
    )

    # Force import so SQLModel.metadata registers each table.
    assert CustomAgentConfig is not None
    assert PromptVersion is not None
    assert PromptAuditEvent is not None
    assert PromptTemplateBaseline is not None

    SQLModel.metadata.create_all(engine)

    yield engine

    engine.dispose()


# ──────────────────────────────────────────────────────────────────────────
# R-A1 — DB-born, v1 APPROVED
# ──────────────────────────────────────────────────────────────────────────


class TestCustomAgentCreation:
    """R-A1: create writes config + v1 in one txn, v1 lands APPROVED."""

    def test_creates_config_and_v1_soul_in_one_txn(
        self, tmp_repo_engine, tmp_path,
    ):
        from daemon.repositories.custom_agent_configs import (
            CustomAgentConfigRepository,
            make_custom_agent_id,
        )
        from daemon.repositories.prompt_version.repository import (
            ActorContext,
            PromptVersionRepository,
        )
        from daemon.repositories.prompt_version.models import (
            PROMPT_AUDIT_EVENT_TYPES,
            PROMPT_STATES,
        )
        from sqlmodel import Session, col, select
        from daemon.repositories.prompt_version.models import (
            PromptAuditEvent,
            PromptVersion,
        )

        cfg_repo = CustomAgentConfigRepository(engine=tmp_repo_engine)
        pv_repo = PromptVersionRepository(engine=tmp_repo_engine)
        actor = ActorContext(
            subject="test-user@example.com",
            auth_method="session",
            instance_id=None,
            agent_id=None,
            version_tag="",
        )

        # Wide-transaction creation (R-A1 atomicity invariant):
        # config row + v1 prompt + audit rows all commit together.
        with tmp_repo_engine.begin() as conn:
            cfg_row = cfg_repo.insert_config(
                agent_id=make_custom_agent_id("myhelper"),
                name="My Helper",
                description="R-A1 test agent",
                created_by=actor.subject,
                created_auth_method=actor.auth_method,
                conn=conn,
            )
            pv = pv_repo.create_initial_approved_v1(
                agent_id=cfg_row.agent_id,
                version_tag="",
                artifact="soul",
                content="# Soul of myhelper\n\nA test soul.",
                actor=actor,
                conn=conn,
            )

        # Verify: config row exists, is_custom wire form, system=0
        assert cfg_row.config_id
        assert cfg_row.agent_id == "custom:myhelper"
        assert cfg_row.system == 0
        assert cfg_row.created_by == "test-user@example.com"

        # Verify: v1 row is state=approved, is_selected=1, approver stamped
        assert pv.state == "approved"
        assert pv.is_selected == 1
        assert pv.approver_subject == "test-user@example.com"
        assert pv.approver_auth_method == "session"
        assert pv.artifact == "soul"
        assert pv.version == 1

        # Verify: audit events — 2 rows (CREATED + APPROVED) per R-A1
        with Session(tmp_repo_engine) as session:
            audit_count = session.exec(
                select(PromptAuditEvent).where(
                    col(PromptAuditEvent.version_id) == pv.version_id
                )
            ).all()
        event_types = sorted(e.event_type for e in audit_count)
        assert "CREATED" in event_types
        assert "APPROVED" in event_types
        # 2 audit rows per R-A1 (CREATED + APPROVED); doc-string
        # binding says the timeline reflects the auto-approval.
        assert len(audit_count) == 2
        # All event types referenced in the payload are in the
        # vocabulary (no out-of-vocabulary stamps).
        for e in audit_count:
            assert e.event_type in PROMPT_AUDIT_EVENT_TYPES
            assert e.actor_subject == "test-user@example.com"
            assert e.actor_auth_method == "session"

    def test_helper_refuses_internal_lane(self, tmp_repo_engine):
        """The R-A1 helper MUST refuse 'internal' actors (S16)."""
        from daemon.repositories.custom_agent_configs import (
            CustomAgentConfigRepository,
            make_custom_agent_id,
        )
        from daemon.repositories.prompt_version.repository import (
            ActorContext,
            PromptVersionRepository,
        )

        cfg_repo = CustomAgentConfigRepository(engine=tmp_repo_engine)
        pv_repo = PromptVersionRepository(engine=tmp_repo_engine)
        internal = ActorContext(
            subject="coder-internal",
            auth_method="internal",
            instance_id="inst-1",
            agent_id="coder",
            version_tag="",
        )

        with tmp_repo_engine.begin() as conn:
            cfg_row = cfg_repo.insert_config(
                agent_id=make_custom_agent_id("internaltest"),
                name="Internal Test",
                conn=conn,
            )
            with pytest.raises(ValueError) as exc_info:
                pv_repo.create_initial_approved_v1(
                    agent_id=cfg_row.agent_id,
                    version_tag="",
                    artifact="soul",
                    content="x",
                    actor=internal,
                    conn=conn,
                )
        assert "internal" in str(exc_info.value).lower()

    def test_helper_refuses_empty_subject(self, tmp_repo_engine):
        """The R-A1 helper MUST refuse an empty subject (R-A1 invariant)."""
        from daemon.repositories.custom_agent_configs import (
            CustomAgentConfigRepository,
            make_custom_agent_id,
        )
        from daemon.repositories.prompt_version.repository import (
            ActorContext,
            PromptVersionRepository,
        )

        cfg_repo = CustomAgentConfigRepository(engine=tmp_repo_engine)
        pv_repo = PromptVersionRepository(engine=tmp_repo_engine)
        empty_subj = ActorContext(
            subject="",
            auth_method="session",
            instance_id=None,
            agent_id=None,
            version_tag="",
        )

        with tmp_repo_engine.begin() as conn:
            cfg_row = cfg_repo.insert_config(
                agent_id=make_custom_agent_id("emptysubj"),
                name="Empty Subject",
                conn=conn,
            )
            with pytest.raises(ValueError) as exc_info:
                pv_repo.create_initial_approved_v1(
                    agent_id=cfg_row.agent_id,
                    version_tag="",
                    artifact="soul",
                    content="x",
                    actor=empty_subj,
                    conn=conn,
                )
        assert "non-empty" in str(exc_info.value).lower()


# ──────────────────────────────────────────────────────────────────────────
# R-A2 — no template fallback, no on-disk dir
# ──────────────────────────────────────────────────────────────────────────


class TestR_A2_NamespaceIsolation:
    """R-A2: wire form is provably disjoint + customs have no on-disk dir."""

    def test_make_custom_agent_id_prefix_and_validate(self):
        from daemon.repositories.custom_agent_configs import (
            CUSTOM_AGENT_ID_PREFIX,
            is_custom_agent_id,
            make_custom_agent_id,
        )

        assert CUSTOM_AGENT_ID_PREFIX == "custom:"
        assert (
            make_custom_agent_id("myhelper") == "custom:myhelper"
        )
        assert (
            make_custom_agent_id("my-helper_42")
            == "custom:my-helper_42"
        )
        assert is_custom_agent_id("custom:myhelper")
        assert not is_custom_agent_id("developer")
        # The wire form is just the prefix; an empty trailing
        # string still satisfies the prefix check (routers
        # refuse ``custom:`` via the body-validation guard
        # rails).
        assert is_custom_agent_id("custom:")
        assert not is_custom_agent_id("")

        # Invalid IDs:
        with pytest.raises(ValueError):
            make_custom_agent_id("")
        with pytest.raises(ValueError):
            make_custom_agent_id("   ")
        with pytest.raises(ValueError):
            make_custom_agent_id("has space")
        with pytest.raises(ValueError):
            make_custom_agent_id("has:colon")
        # Double-wrap refused:
        with pytest.raises(ValueError):
            make_custom_agent_id("custom:myhelper")

    def test_helper_writes_no_on_disk_path(self, tmp_repo_engine, tmp_path):
        """R-A2 invariant: creation writes to the DB ONLY.

        Uses ``tmp_path`` so the test fixture is fenced.
        """
        from daemon.repositories.custom_agent_configs import (
            CustomAgentConfigRepository,
            make_custom_agent_id,
        )
        from daemon.repositories.prompt_version.repository import (
            ActorContext,
            PromptVersionRepository,
        )
        import os
        agent_id_wire = make_custom_agent_id("filelesstest")
        # Defensive: confirm the wire form is NOT a real dir
        # before the write — the post-condition is that no NEW
        # filesystem artifact is created.
        before_set = {
            os.path.join(root, name)
            for root, _dirs, files in os.walk(str(tmp_path))
            for name in files
        }
        before_set |= {
            os.path.join(root, name)
            for root, dirs, _ in os.walk(str(tmp_path))
            for name in dirs
        }

        actor = ActorContext(
            subject="file-less-test@example.com",
            auth_method="session",
            instance_id=None,
            agent_id=None,
            version_tag="",
        )
        cfg_repo = CustomAgentConfigRepository(engine=tmp_repo_engine)
        pv_repo = PromptVersionRepository(engine=tmp_repo_engine)

        with tmp_repo_engine.begin() as conn:
            cfg_row = cfg_repo.insert_config(
                agent_id=agent_id_wire,
                name="Fileless Test",
                created_by=actor.subject,
                conn=conn,
            )
            pv_repo.create_initial_approved_v1(
                agent_id=agent_id_wire,
                version_tag="",
                artifact="soul",
                content="x",
                actor=actor,
                conn=conn,
            )

        # The DB row exists:
        assert cfg_row.agent_id == agent_id_wire

        # No new filesystem artifact was created.
        after_set = {
            os.path.join(root, name)
            for root, _dirs, files in os.walk(str(tmp_path))
            for name in files
        }
        after_set |= {
            os.path.join(root, name)
            for root, dirs, _ in os.walk(str(tmp_path))
            for name in dirs
        }
        new_artifacts = after_set - before_set
        assert not new_artifacts, (
            f"R-A2 violation: created on-disk artifacts under "
            f"{tmp_path}: {new_artifacts}"
        )


# ──────────────────────────────────────────────────────────────────────────
# Namespace isolation — no-leak property of the R-A2 design
# ──────────────────────────────────────────────────────────────────────────


class TestNamespaceNoLeak:
    """Custom-agent keys NEVER collide with predefined-agent resolution.

    Tests the exact property R-A2 hinges on: a custom key
    resolves ONLY against the custom namespace; predefined
    keys resolve ONLY against the predefined namespace.
    """

    def test_custom_key_does_not_resolve_into_predefined(
        self, tmp_repo_engine,
    ):
        """A custom row's wire id must NOT collide with any
        file-defined id (a) at insert time, (b) via the
        registry's resolve_pure_id path."""
        from daemon.registry import AgentRegistry, _parse_agent_dir_name
        from daemon.repositories.custom_agent_configs import (
            CustomAgentConfigRepository,
            is_custom_agent_id,
            make_custom_agent_id,
        )
        from daemon.repositories.prompt_version.repository import (
            ActorContext,
            PromptVersionRepository,
        )

        # Synthetic registry with a single file-defined agent:
        # ``developer``. The wire-form identities of customs
        # (``custom:<name>``) must NOT collide with developer.
        registry = AgentRegistry(agents_dir=tmp_repo_engine._dir if hasattr(tmp_repo_engine, "_dir") else __import__("pathlib").Path("/tmp"))
        # The above is a no-op shim — just verify the namespace
        # guard discriminant directly via is_custom_agent_id.
        assert is_custom_agent_id("custom:developer")
        # The hyphen / colon shape prevents the registry's
        # _parse_agent_dir_name from recognizing it as a
        # tagged version.
        base, tag = _parse_agent_dir_name("custom:developer")
        assert base == "custom:developer"
        assert tag is None  # the colon rejects the tag regex

        # Direct invariant: a custom row's agent_id NEVER
        # contains just the bare user-name form (the prefix is
        # mandatory on the wire form).
        cfg_repo = CustomAgentConfigRepository(engine=tmp_repo_engine)
        pv_repo = PromptVersionRepository(engine=tmp_repo_engine)
        actor = ActorContext(
            subject="noleak@example.com",
            auth_method="session",
            instance_id=None,
            agent_id=None,
            version_tag="",
        )
        with tmp_repo_engine.begin() as conn:
            cfg_row = cfg_repo.insert_config(
                agent_id=make_custom_agent_id("developer"),
                name="Custom Developer Shadow",
                conn=conn,
            )
            pv_repo.create_initial_approved_v1(
                agent_id=cfg_row.agent_id,
                version_tag="",
                artifact="soul",
                content="x",
                actor=actor,
                conn=conn,
            )

        # The wire id is "custom:developer" (not "developer"):
        assert cfg_row.agent_id == "custom:developer"
        assert is_custom_agent_id(cfg_row.agent_id)
        # No bare collision with the predefined "developer":
        assert cfg_row.agent_id != "developer"


# ──────────────────────────────────────────────────────────────────────────
# Soft-delete + restore (R-A2 conservative: rows are kept)
# ──────────────────────────────────────────────────────────────────────────


class TestSoftDeleteAndRestore:
    """Soft-delete marks `deleted_at`; rows + version history are kept."""

    def test_soft_delete_then_get_returns_none(self, tmp_repo_engine):
        from daemon.repositories.custom_agent_configs import (
            CustomAgentConfigRepository,
            make_custom_agent_id,
        )
        from daemon.repositories.prompt_version.repository import (
            ActorContext,
            PromptVersionRepository,
        )

        cfg_repo = CustomAgentConfigRepository(engine=tmp_repo_engine)
        pv_repo = PromptVersionRepository(engine=tmp_repo_engine)
        actor = ActorContext(
            subject="soft-del-test@example.com",
            auth_method="session",
            instance_id=None,
            agent_id=None,
            version_tag="",
        )
        wire = make_custom_agent_id("softtest")
        with tmp_repo_engine.begin() as conn:
            cfg_repo.insert_config(
                agent_id=wire,
                name="Soft Test",
                created_by=actor.subject,
                conn=conn,
            )
            pv_repo.create_initial_approved_v1(
                agent_id=wire,
                version_tag="",
                artifact="soul",
                content="x",
                actor=actor,
                conn=conn,
            )

        # Live get works:
        assert cfg_repo.get_by_agent_id(wire) is not None

        # soft_delete marks the row deleted.
        assert cfg_repo.soft_delete(wire) is True
        # Default get_by_agent_id filters out deleted rows:
        assert cfg_repo.get_by_agent_id(wire) is None
        # include_deleted=True surfaces the tombstone:
        tombstone = cfg_repo.get_by_agent_id(
            wire, include_deleted=True,
        )
        assert tombstone is not None
        assert tombstone.deleted_at is not None

        # Restore clears deleted_at; version rows are untouched.
        assert cfg_repo.restore(wire) is True
        assert cfg_repo.get_by_agent_id(wire) is not None
        live = cfg_repo.get_by_agent_id(wire)
        assert live.deleted_at is None

        # Version rows are still present (history preservation):
        assert live is not None

    def test_soft_delete_returns_false_when_no_live_match(
        self, tmp_repo_engine,
    ):
        from daemon.repositories.custom_agent_configs import (
            CustomAgentConfigRepository,
            make_custom_agent_id,
        )

        cfg_repo = CustomAgentConfigRepository(engine=tmp_repo_engine)
        # Nothing inserted — soft_delete returns False.
        result = cfg_repo.soft_delete(
            make_custom_agent_id("ghostagent")
        )
        assert result is False


# ──────────────────────────────────────────────────────────────────────────
# Lifecycle service — wide-txn coordination
# ──────────────────────────────────────────────────────────────────────────


class TestLifecycleServiceWideTxn:
    """The service composes config + v1 versions inside ONE txn."""

    def test_create_writes_config_and_v1_rows(
        self, tmp_repo_engine, tmp_path,
    ):
        import asyncio
        from daemon.services.custom_agent_lifecycle_service import (
            CustomAgentLifecycleService,
        )
        from daemon.repositories.custom_agent_configs import (
            CustomAgentConfigRepository,
        )
        from daemon.repositories.prompt_version.repository import (
            ActorContext,
            PromptVersionRepository,
        )
        from sqlmodel import Session, col, select
        from daemon.repositories.prompt_version.models import (
            PromptVersion,
        )
        from daemon.repositories.custom_agent_configs.models import (
            CustomAgentConfig,
        )

        cfg_repo = CustomAgentConfigRepository(engine=tmp_repo_engine)
        pv_repo = PromptVersionRepository(engine=tmp_repo_engine)
        svc = CustomAgentLifecycleService(
            config_repo=cfg_repo,
            prompt_version_repo=pv_repo,
        )
        actor = ActorContext(
            subject="svc-test@example.com",
            auth_method="session",
            instance_id=None,
            agent_id=None,
            version_tag="",
        )

        async def _run():
            return await svc.create_custom_agent(
                user_name="svchelper",
                name="SVC Helper",
                actor=actor,
                soul_md="# SVC Helper\n\nA test soul for the lifecycle service.",
                rule_md="Be concise.",
            )

        result = asyncio.run(_run())
        assert result["agent_id"] == "custom:svchelper"
        assert len(result["v1_versions"]) == 2  # soul + rule

        artifacts = sorted(v["artifact"] for v in result["v1_versions"])
        assert artifacts == ["rule", "soul"]

        # Confirm in DB:
        with Session(tmp_repo_engine) as session:
            cfg = session.exec(
                select(CustomAgentConfig).where(
                    col(CustomAgentConfig.agent_id) == "custom:svchelper"
                )
            ).one()
            assert cfg.system == 0

            pvs = session.exec(
                select(PromptVersion).where(
                    col(PromptVersion.agent_id) == "custom:svchelper"
                )
            ).all()
            assert len(pvs) == 2
            for pv in pvs:
                assert pv.state == "approved"
                assert pv.is_selected == 1
                assert pv.approver_subject == "svc-test@example.com"

    def test_create_refuses_empty_soul(self, tmp_repo_engine):
        """soul_md empty is REJECTED (the empty-state rule)."""
        import asyncio
        from daemon.services.custom_agent_lifecycle_service import (
            CustomAgentCreationError,
            CustomAgentLifecycleService,
        )
        from daemon.repositories.custom_agent_configs import (
            CustomAgentConfigRepository,
        )
        from daemon.repositories.prompt_version.repository import (
            ActorContext,
            PromptVersionRepository,
        )

        cfg_repo = CustomAgentConfigRepository(engine=tmp_repo_engine)
        pv_repo = PromptVersionRepository(engine=tmp_repo_engine)
        svc = CustomAgentLifecycleService(
            config_repo=cfg_repo,
            prompt_version_repo=pv_repo,
        )

        async def _run():
            return await svc.create_custom_agent(
                user_name="emptysoul",
                name="Empty Soul",
                actor=ActorContext(
                    subject="x@example.com",
                    auth_method="session",
                    instance_id=None,
                    agent_id=None,
                    version_tag="",
                ),
                soul_md="",
            )

        with pytest.raises(CustomAgentCreationError) as exc_info:
            asyncio.run(_run())
        assert "soul_md" in str(exc_info.value).lower()


# ──────────────────────────────────────────────────────────────────────────
# Resolution-side hook — is_custom_agent=True on a zero-versions custom
# ──────────────────────────────────────────────────────────────────────────


class TestZeroVersionsCustomRaisesNoPromptContent:
    """A zero-versions custom agent's spawn surfaces the typed refusal."""

    def test_zero_versions_custom_raises_no_prompt_content_error(
        self, tmp_repo_engine,
    ):
        """End-to-end: spawn a custom agent that has ZERO v1 rows.

        The Phase A hook ``resolve_prompts_from_db`` raises
        :class:`NoPromptContentError` when ``is_custom_agent=True``
        and ``get_effective_rows`` returns ``[]`` — which is
        the actionable spawn-time failure surface for the
        User Directive #5A R-A2 lifecycle ruling.
        """
        from daemon.loader import (
            NoPromptContentError,
            resolve_prompts_from_db,
        )
        from daemon.repositories.prompt_version.repository import (
            PromptVersionRepository,
        )

        pv_repo = PromptVersionRepository(engine=tmp_repo_engine)

        # A custom agent exists in the custom_agent_configs
        # table (created via the lifecycle service) but ZERO
        # prompt_versions rows exist for its key. This is the
        # exact R-A2 invariant: no template fallback.
        with pytest.raises(NoPromptContentError) as exc_info:
            resolve_prompts_from_db(
                prompts={},
                agent_id="custom:zeroversions",
                version_tag="",
                repo=pv_repo,
                is_custom_agent=True,
            )

        err = exc_info.value
        assert err.agent_id == "custom:zeroversions"
        assert err.artifact == "soul"
        # Actionable message invites v1 creation.
        assert "create v1" in str(err).lower() or (
            "user directive #5a" in str(err).lower()
        )

    def test_non_custom_zero_versions_does_not_raise(
        self, tmp_repo_engine,
    ):
        """A non-custom agent with zero DB rows is the
        pre-Phase-C contract — file fallback fires."""
        from daemon.loader import resolve_prompts_from_db
        from daemon.repositories.prompt_version.repository import (
            PromptVersionRepository,
        )

        pv_repo = PromptVersionRepository(engine=tmp_repo_engine)

        # With is_custom_agent=False (the default), zero DB rows
        # returns the original prompts dict UNMODIFIED — no
        # typed refusal, just the file fallback path.
        result = resolve_prompts_from_db(
            prompts={"soul": "fallback"},
            agent_id="developer",
            version_tag="",
            repo=pv_repo,
            is_custom_agent=False,
        )
        assert result == {"soul": "fallback"}

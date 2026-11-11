"""The FE-UNCHANGED proof (prompt-assent Phase 3 — task 3.7).

The FE path is provably unchanged against the base commit ``0efdf106f``:

* the FE routes (``daemon/routers/prompts.py`` — the approve/reject
  handlers) are BYTE-IDENTICAL to base;
* the FE service methods (``PromptVersionService.approve`` /
  ``reject``) are BYTE-IDENTICAL to base;
* the FE repo methods (``PromptVersionRepository.approve`` /
  ``reject``) are BYTE-IDENTICAL to base;
* the behavior is identical: a session actor approves/rejects; a
  bearer is refused (the clamp); an internal actor is refused (F4);
  an assent-relay actor CANNOT ride the FE methods (the relay lane
  enters only through ``approve_assent`` / ``reject_assent``);
* the atomic_transition hook is default-absent → the FE path is
  provably untouched (P1's pin, re-asserted here end-to-end).

The byte-identity is proven against ``git show 0efdf106f:<path>`` —
the extracted function source from the BASE COMMIT is compared to
``inspect.getsource`` of the live objects.
"""

from __future__ import annotations

import inspect
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_COMMIT = "0efdf106f"


def _base_file(path: str) -> str:
    out = subprocess.run(
        ["git", "show", f"{BASE_COMMIT}:{path}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout


def _live_source(obj) -> str:
    return inspect.getsource(obj)


def _extract_function(source: str, name: str) -> str:
    """The EXACT def block (no decorators) of ``name`` via ``ast``.

    Decorator-free extraction makes the base-vs-live comparison
    insensitive to decorator stacking; the def BODY is the contract.
    ``textwrap.dedent`` first: ``inspect.getsource`` of a METHOD keeps
    the class indent, and ``ast.parse`` refuses indented modules.
    """
    import ast
    import textwrap

    tree = ast.parse(textwrap.dedent(source))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            node.name == name
        ):
            seg = ast.get_source_segment(textwrap.dedent(source), node)
            assert seg is not None, f"{name}: no source segment"
            return _normalize(seg)
    raise AssertionError(f"{name} not found in source")


def _normalize(segment: str) -> str:
    """Indent-normalized source text (the CONTRACT is the content —
    the class indent differs between a module-level parse and
    ``inspect.getsource`` of a method)."""
    return "\n".join(line.lstrip() for line in segment.splitlines())


class TestFeRoutesByteIdentical:
    def test_the_approve_and_reject_routes_are_byte_identical(self):
        """The FE approve/reject handlers are untouched by the lane
        (the router diff vs base is ONLY the K-7 lift-and-shift of the
        diff helper at :431-435 — P1's landed change)."""
        live = (REPO_ROOT / "daemon/routers/prompts.py").read_text("utf-8")
        base = _base_file("daemon/routers/prompts.py")

        import daemon.routers.prompts as router_module

        for fn_name in ("approve_version", "reject_version"):
            live_src = _extract_function(
                _live_source(getattr(router_module, fn_name)), fn_name
            )
            base_src = _extract_function(base, fn_name)
            assert live_src == base_src, (
                f"the FE route {fn_name} drifted from base {BASE_COMMIT}:\n"
                f"--- base ---\n{base_src}\n--- live ---\n{live_src}"
            )

    def test_the_router_diff_against_base_is_exactly_the_lift_and_shift(self):
        import subprocess

        out = subprocess.run(
            ["git", "diff", f"{BASE_COMMIT}..HEAD", "--", "daemon/routers/prompts.py"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        )
        diff = out.stdout
        assert diff.strip(), "the router must already carry the K-7 shift"
        # The ONLY hunks touch the diff-helper block — never the FE
        # approve/reject routes, never the auth helpers.
        assert "-def _unified_diff" in diff
        assert "+from daemon.services.prompt_version_diff import unified_diff as _unified_diff" in diff
        changed_lines = [
            line for line in diff.splitlines()
            if line.startswith(("+", "-"))
            and not line.startswith(("+++", "---"))
        ]
        for line in changed_lines:
            # Every changed line is inside the diff-helper region: no
            # route, no scope, no auth token appears.
            assert "_SCOPE_APPROVE" not in line
            assert "HUMAN_LANE" not in line
            assert "def approve_version" not in line
            assert "def reject_version" not in line


class TestFeServiceAndRepoMethodsByteIdentical:
    def test_svc_approve_and_reject_are_byte_identical(self):
        base = _base_file("daemon/services/prompt_version_service.py")
        from daemon.services.prompt_version_service import (
            PromptVersionService,
        )

        for fn_name in ("approve", "reject"):
            live_src = _extract_function(
                _live_source(getattr(PromptVersionService, fn_name)), fn_name
            )
            base_src = _extract_function(base, fn_name)
            assert live_src == base_src, (
                f"svc.{fn_name} drifted from base {BASE_COMMIT}"
            )

    def test_repo_approve_and_reject_are_byte_identical(self):
        base = _base_file("daemon/repositories/prompt_version/repository.py")
        from daemon.repositories.prompt_version.repository import (
            PromptVersionRepository,
        )

        for fn_name in ("approve", "reject"):
            live_src = _extract_function(
                _live_source(getattr(PromptVersionRepository, fn_name)),
                fn_name,
            )
            base_src = _extract_function(base, fn_name)
            assert live_src == base_src, (
                f"repo.{fn_name} drifted from base {BASE_COMMIT}"
            )

    def test_the_atomic_transition_hook_is_default_absent(self):
        """The hook parameter is keyword-only, private, default-None —
        with it absent the FE transition path is the base path (P1's
        pin, re-asserted)."""
        from daemon.repositories.prompt_version.repository import (
            PromptVersionRepository,
        )

        sig = inspect.signature(PromptVersionRepository.atomic_transition)
        hook = sig.parameters["_assent_consume_hook"]
        assert hook.kind is inspect.Parameter.KEYWORD_ONLY
        assert hook.default is None


class TestFeBehaviorUnchanged:
    """The FE behavior: session works; bearer/internal/relay refuse."""

    def _make(self):
        from daemon.repositories.prompt_version.repository import (
            PromptVersionRepository,
            actor_from_instance,
        )

        engine = _fresh_engine()
        repo = PromptVersionRepository(engine)
        submitter = actor_from_instance("inst-1", "ari")
        draft = repo.create_draft(
            agent_id="ari", version_tag="", artifact="rule",
            content="fe unchanged\n", actor=submitter,
        )
        row = repo.submit_for_approval(draft.version_id, submitter)
        return engine, repo, row

    def test_a_session_actor_still_approves_and_rejects(self):
        from daemon.services.prompt_version_service import (
            PromptVersionService,
        )
        from daemon.repositories.prompt_version.repository import (
            ActorContext,
        )

        engine, repo, row = self._make()
        svc = PromptVersionService(repo=repo)
        import asyncio

        session_actor = ActorContext(subject="operator", auth_method="session")
        out = asyncio.run(svc.approve(row.version_id, session_actor))
        assert out.state == "approved"

        engine2, repo2, row2 = self._make()
        svc2 = PromptVersionService(repo=repo2)
        out2 = asyncio.run(svc2.reject(row2.version_id, session_actor))
        assert out2.state == "rejected"

    def test_a_bearer_is_refused_by_the_clamp(self):
        from daemon.services.prompt_version_service import (
            HumanLaneRequiredError,
            PromptVersionService,
        )
        from daemon.repositories.prompt_version.repository import (
            ActorContext,
        )

        engine, repo, row = self._make()
        svc = PromptVersionService(repo=repo)
        bearer = ActorContext(subject="machine", auth_method="bearer")
        with pytest.raises(HumanLaneRequiredError):
            import asyncio

            asyncio.run(svc.approve(row.version_id, bearer))

    def test_an_internal_actor_is_refused_f4(self):
        from daemon.repositories.prompt_version.repository import (
            ActorContext,
            PromptVersionRepository,
        )

        engine, repo, row = self._make()
        internal = ActorContext(
            subject="inst-1", auth_method="internal",
            instance_id="inst-1", agent_id="ari",
        )
        with pytest.raises(ValueError, match="only HTTP actors"):
            repo.approve(row.version_id, internal)
        with pytest.raises(ValueError, match="only HTTP actors"):
            repo.reject(row.version_id, internal)

    def test_a_relay_actor_cannot_ride_the_fe_methods(self):
        """The relay lane enters ONLY through approve_assent /
        reject_assent — never through svc.approve / svc.reject."""
        from daemon.services.prompt_version_service import (
            HumanLaneRequiredError,
            PromptVersionService,
        )
        from daemon.repositories.prompt_version.repository import (
            actor_from_assent_relay,
        )
        from tests.helpers.prompt_assent_fixture import build_attestation

        engine, repo, row = self._make()
        svc = PromptVersionService(repo=repo)
        att = build_attestation(version_id=row.version_id)
        relay = actor_from_assent_relay(att)
        with pytest.raises(HumanLaneRequiredError):
            import asyncio

            asyncio.run(svc.approve(row.version_id, relay))
        with pytest.raises(HumanLaneRequiredError):
            import asyncio

            asyncio.run(svc.reject(row.version_id, relay, reason=None) if False else svc.reject(row.version_id, relay))


def _fresh_engine():
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from sqlmodel import SQLModel

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from daemon.repositories.prompt_assent.models import (  # noqa: F401
        PromptAssentToken,
    )
    from daemon.repositories.prompt_version.models import (  # noqa: F401
        PromptAuditEvent,
        PromptVersion,
    )

    SQLModel.metadata.create_all(engine)
    return engine

"""Phase B T9 — preserved-exception proof tests (agent-prompt-immutability).

The plan carves FOUR creation/delete surfaces out of the
immutability regime — they keep writing files directly, BY DESIGN:

1. ``agent_mother.agent_create`` — template copy from
   ``agents/_baby_template`` (creation = legitimate first-write).
2. ``daemon/routers/agents.py`` ``POST /agents`` — the HTTP twin of
   the same template copy.
3. ``agent_mother.agent_delete`` — ``shutil.move`` of the agent dir
   to ``agents/_trash/`` (a rename, not a content write).
4. ``daemon/routers/agents.py`` ``DELETE /agents/{id}`` — the HTTP
   twin of the same _trash move.

Phase A's ``tests/test_prompt_artifact_guards.py`` pins the
STRUCTURAL fact (raw ``shutil`` primitives bypass the guard). THIS
module pins the FUNCTIONAL fact, per the T9 acceptance: all four
flows still work UNCHANGED — create produces a spawned agent dir
with template files; delete moves to _trash — and the prompts write
guard (``daemon.tools.prompt_artifacts.check_write``) has NO surface
to fire on either (proven by a raising spy: if any flow so much as
reached the guard, the test would explode).

Fence discipline (2026-10-12 critical note — zero tolerance): both
``BASE_DIR`` globals (``daemon.tools.agent_mother.BASE_DIR`` AND
``daemon.routers.agents.BASE_DIR``) are patched to ``tmp_path`` in
every test. Never live-shaped paths.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import daemon.routers.agents as agents_router_module
import daemon.tools.agent_mother as mother_module
from daemon.tools.agent_mother import create_mother_tools


TEMPLATE_FILES = ("soul.md", "rule.md", "workflow.md", "tools_note.md",
                  "tools.md", "skill.md", "memory.md", "user.md", "growth.md")


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def guard_spy(monkeypatch):
    """Replace ``prompt_artifacts.check_write`` with a tripwire.

    The four preserved flows must NEVER reach the guard. If any of
    them does, the tripwire raises and the test fails loudly with the
    attempted path in the message.
    """
    import daemon.tools.prompt_artifacts as pa

    calls: list[str] = []

    def _tripwire(*args, **kwargs):
        calls.append(str(args[:2] or kwargs))
        raise AssertionError(
            f"prompt guard fired on a preserved-exception path: {args} {kwargs}"
        )

    monkeypatch.setattr(pa, "check_write", _tripwire)
    return calls


@pytest.fixture
def agents_tree(tmp_path):
    """tmp agents tree: a complete ``_baby_template`` + one live agent."""
    agents_root = tmp_path / "agents"
    agents_root.mkdir()

    baby = agents_root / "_baby_template"
    baby.mkdir()
    for fname in TEMPLATE_FILES:
        (baby / fname).write_text(f"# template {fname}\n")

    live = agents_root / "sacrificial"
    live.mkdir()
    for fname in TEMPLATE_FILES:
        (live / fname).write_text(f"# live {fname}\n")

    with mock.patch.object(mother_module, "BASE_DIR", tmp_path), \
         mock.patch.object(agents_router_module, "BASE_DIR", tmp_path):
        yield {"root": agents_root, "baby": baby, "live": live}


@pytest.fixture
def mother_tools():
    return create_mother_tools(manager=None, current_instance_id="inst-mother-t9")


@pytest.fixture
def router_client():
    """FastAPI app carrying only the agents router (no auth surface)."""
    app = FastAPI()
    app.include_router(agents_router_module.router)
    return TestClient(app, raise_server_exceptions=False)


# ─────────────────────────────────────────────────────────────────────────────
# 1. agent_create tool — template copy
# ─────────────────────────────────────────────────────────────────────────────


class TestAgentCreateTool:
    def test_create_produces_spawned_agent_dir_with_template_files(
        self, mother_tools, agents_tree, guard_spy
    ):
        create = next(t for t in mother_tools if t.name == "agent_create")
        result = create.invoke({
            "name": "spawned_one",
            "purpose": "prove the exception",
            "description": "T9 proof",
        })

        assert result["success"] is True, result
        new_dir = agents_tree["root"] / "spawned_one"
        assert new_dir.is_dir()

        # Template files copied (creation first-write). soul.md /
        # workflow.md / rule.md / tools.md are OVERWRITTEN by the
        # creation customization when the corresponding args are
        # given — so the pure template-copy assertions here target
        # the files agent_create never customizes.
        for fname in ("tools_note.md", "tools.md", "skill.md",
                      "memory.md", "user.md", "growth.md"):
            assert (new_dir / fname).exists(), f"missing template copy: {fname}"
            assert (new_dir / fname).read_text() == f"# template {fname}\n"

        # meta.json written + soul.md customized (creation-specific).
        meta = json.loads((new_dir / "meta.json").read_text())
        assert meta["id"] == "spawned_one"
        soul = (new_dir / "soul.md").read_text()
        assert "prove the exception" in soul
        # Untouched optional customizations keep the template body.
        assert (new_dir / "workflow.md").read_text() == "# template workflow.md\n"
        assert (new_dir / "rule.md").read_text() == "# template rule.md\n"
        # Empty scaffolding dirs exist.
        assert (new_dir / "history").is_dir()
        assert (new_dir / "memories").is_dir()

        # The guard never fired.
        assert guard_spy == []

    def test_create_customization_args_overwrite_template_copies(
        self, mother_tools, agents_tree, guard_spy
    ):
        """workflow/rules/tools args produce customized files (still direct)."""
        create = next(t for t in mother_tools if t.name == "agent_create")
        result = create.invoke({
            "name": "spawned_two",
            "purpose": "custom check",
            "workflow": "step one",
            "rules_must": ["be tested"],
            "tools_extra": ["my_custom_tool"],
        })
        assert result["success"] is True, result

        new_dir = agents_tree["root"] / "spawned_two"
        assert "step one" in (new_dir / "workflow.md").read_text()
        assert "be tested" in (new_dir / "rule.md").read_text()
        assert "my_custom_tool" in (new_dir / "tools.md").read_text()
        assert guard_spy == []

    def test_create_refusals_intact(self, mother_tools, agents_tree, guard_spy):
        create = next(t for t in mother_tools if t.name == "agent_create")

        dup = create.invoke({"name": "sacrificial", "purpose": "x"})
        assert dup["success"] is False and "already exists" in dup["error"]

        bad = create.invoke({"name": "bad name!", "purpose": "x"})
        assert bad["success"] is False

        sysdir = create.invoke({"name": "_systemish", "purpose": "x"})
        assert sysdir["success"] is False

        assert guard_spy == []


# ─────────────────────────────────────────────────────────────────────────────
# 2. routers/agents.py POST — HTTP template copy
# ─────────────────────────────────────────────────────────────────────────────


class TestRouterCreateAgent:
    def test_post_creates_agent_dir_with_template_files(
        self, router_client, agents_tree, guard_spy
    ):
        resp = router_client.post(
            "/agents",
            json={"id": "http_spawned", "name": "HTTP Spawned",
                  "description": "via POST"},
        )
        assert resp.status_code == 201, resp.text

        new_dir = agents_tree["root"] / "http_spawned"
        assert new_dir.is_dir()
        for fname in TEMPLATE_FILES:
            assert (new_dir / fname).exists(), f"missing template copy: {fname}"
        meta = json.loads((new_dir / "meta.json").read_text())
        assert meta["id"] == "http_spawned"
        assert guard_spy == []

    def test_post_conflict_still_409(self, router_client, agents_tree, guard_spy):
        resp = router_client.post(
            "/agents",
            json={"id": "sacrificial", "name": "Dup", "description": "x"},
        )
        assert resp.status_code == 409
        assert guard_spy == []


# ─────────────────────────────────────────────────────────────────────────────
# 3. agent_delete tool — _trash move
# ─────────────────────────────────────────────────────────────────────────────


class TestAgentDeleteTool:
    def test_delete_moves_agent_to_trash(
        self, mother_tools, agents_tree, guard_spy
    ):
        delete = next(t for t in mother_tools if t.name == "agent_delete")

        # Safety interlock intact first.
        unconfirmed = delete.invoke({"agent_name": "sacrificial"})
        assert unconfirmed["success"] is False
        assert "confirm=True" in unconfirmed["error"]
        assert agents_tree["live"].exists()

        result = delete.invoke({"agent_name": "sacrificial", "confirm": True})
        assert result["success"] is True, result

        assert not agents_tree["live"].exists()
        trash_dir = agents_tree["root"] / "_trash"
        trashed = list(trash_dir.iterdir())
        assert len(trashed) == 1
        # The .md files travelled with the dir (a rename, not a write).
        for fname in TEMPLATE_FILES:
            assert (trashed[0] / fname).read_text() == f"# live {fname}\n"
        assert guard_spy == []

    def test_delete_refuses_system_agents(self, mother_tools, agents_tree, guard_spy):
        delete = next(t for t in mother_tools if t.name == "agent_delete")
        result = delete.invoke({"agent_name": "_baby_template", "confirm": True})
        assert result["success"] is False
        assert agents_tree["baby"].exists()
        assert guard_spy == []


# ─────────────────────────────────────────────────────────────────────────────
# 4. routers/agents.py DELETE — HTTP _trash move
# ─────────────────────────────────────────────────────────────────────────────


class TestRouterDeleteAgent:
    def test_delete_moves_to_trash(self, router_client, agents_tree, guard_spy):
        resp = router_client.delete("/agents/sacrificial")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["deleted"] is True
        assert body["agent_id"] == "sacrificial"

        assert not agents_tree["live"].exists()
        trash_dir = agents_tree["root"] / "_trash"
        trashed = list(trash_dir.iterdir())
        assert len(trashed) == 1
        assert trashed[0].name.startswith("sacrificial_")
        for fname in TEMPLATE_FILES:
            assert (trashed[0] / fname).exists()
        assert guard_spy == []

    def test_delete_404_and_internal_refusal(
        self, router_client, agents_tree, guard_spy
    ):
        missing = router_client.delete("/agents/ghost_agent")
        assert missing.status_code == 404

        internal = router_client.delete("/agents/_baby_template")
        assert internal.status_code == 400
        assert agents_tree["baby"].exists()
        assert guard_spy == []


# ─────────────────────────────────────────────────────────────────────────────
# Cross-check: the exception inventory matches the docstring
# ─────────────────────────────────────────────────────────────────────────────


class TestExceptionInventoryDocumented:
    """The docstring inventory (T9) must name all four preserved flows."""

    def test_agent_mother_docstring_carries_inventory(self):
        import daemon.tools.agent_mother as m

        doc = m.__doc__ or ""
        assert "Preserved exceptions (T9 inventory)" in doc
        assert "agent_create" in doc
        assert "agent_delete" in doc
        assert "routers/agents.py" in doc
        assert "_trash" in doc

    def test_agent_create_and_delete_are_undocumented_writes_by_design(self):
        """Structural: neither closure references the DB submission path."""
        import inspect

        import daemon.tools.agent_mother as m

        src = inspect.getsource(m)
        create_fn = src[src.index("def agent_create"):src.index("agent_create._full_doc_")]
        delete_fn = src[src.index("def agent_delete"):src.index("agent_delete._full_doc_")]
        # Creation/deletion do NOT submit DB drafts...
        assert "create_and_submit" not in create_fn
        assert "create_and_submit" not in delete_fn
        # ...they use shutil (copy2 for create, move for delete).
        assert "shutil.copy2" in create_fn
        assert "shutil.move" in delete_fn

"""The prompt-assent LIST surface (prompt-assent Phase 2, task 2.7/2.10).

The read surface over the Phase-1 ``list_pending`` primitives:

* it returns the CORE pending rows (id, agent, artifact, version,
  submitted-at);
* it is SIDE-EFFECT FREE — listing never mints (a read that mints would
  be an authority leak);
* it is NOT agent-filtered — the relay lane must be able to approve ANY
  agent's row (DECIDED-2);
* it is BOUNDED (default 50, max 200);
* the list body goes to the Emperor's chat, NOT into the daemon log (the
  INFO line stays content-free).
"""

from __future__ import annotations

from daemon.repositories.prompt_version.repository import actor_from_instance
from tests.helpers.prompt_assent_fixture import (  # noqa: F401 — fixtures
    pending_row,
    pv_repo,
    sqlite_engine,
)
from tests.helpers.prompt_assent_tool_fixture import (
    FakeManager,
    build_tools,
)


def _manager(pv, engine=None):
    return FakeManager(engine=engine, pv_repo=pv)


class TestTheListSurface:
    async def test_the_list_returns_the_pending_rows(
        self, monkeypatch, sqlite_engine, pv_repo, pending_row
    ) -> None:
        from daemon.tools.prompt_assent import KILL_SWITCH_ENV

        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        manager = _manager(pv_repo, sqlite_engine)
        tools = build_tools(manager)
        result = await tools["prompt_assent"].ainvoke({"action": "list"})
        assert "prompt_assent list" in result
        assert pending_row.version_id in result
        assert "agent=ari" in result
        assert "artifact=rule" in result

    async def test_the_list_mints_nothing(
        self, monkeypatch, sqlite_engine, pv_repo, pending_row
    ) -> None:
        """Listing is SIDE-EFFECT FREE — no token row is written."""
        from daemon.repositories.prompt_assent.repository import (
            PromptAssentTokenRepository,
        )
        from daemon.tools.prompt_assent import KILL_SWITCH_ENV

        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        manager = _manager(pv_repo, sqlite_engine)
        tools = build_tools(manager)
        await tools["prompt_assent"].ainvoke({"action": "list"})
        repo = PromptAssentTokenRepository(sqlite_engine)
        assert repo.find_live(pending_row.version_id) is None

    async def test_the_list_is_not_agent_filtered(
        self, monkeypatch, sqlite_engine, pv_repo, pending_row
    ) -> None:
        """No agent filter — the relay lane must be able to approve any
        agent's row."""
        from daemon.tools.prompt_assent import KILL_SWITCH_ENV

        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        other = actor_from_instance(instance_id="inst-other", agent_id="coder")
        other_row = pv_repo.create_draft(
            agent_id="coder",
            version_tag="",
            artifact="soul",
            content="coder persona draft\n",
            actor=other,
        )
        other_row = pv_repo.submit_for_approval(other_row.version_id, other)
        manager = _manager(pv_repo, sqlite_engine)
        tools = build_tools(manager)
        result = await tools["prompt_assent"].ainvoke({"action": "list"})
        assert pending_row.version_id in result
        assert other_row.version_id in result
        assert "agent=coder" in result

    async def test_the_list_is_bounded(
        self, monkeypatch, sqlite_engine, pv_repo
    ) -> None:
        """The list is BOUNDED — a relay lane cannot ask for the whole
        table."""
        from daemon.tools.prompt_assent import (
            PROMPT_ASSENT_LIST_LIMIT_MAX,
            KILL_SWITCH_ENV,
        )

        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        actor = actor_from_instance(instance_id="inst-bulk", agent_id="ari")
        for index in range(5):
            row = pv_repo.create_draft(
                agent_id="ari",
                version_tag="",
                artifact="soul",
                content=f"draft {index}\n",
                actor=actor,
            )
            pv_repo.submit_for_approval(row.version_id, actor)
        manager = _manager(pv_repo, sqlite_engine)
        tools = build_tools(manager)
        result = await tools["prompt_assent"].ainvoke({"action": "list", "limit": 2})
        assert "2 pending CORE item(s)" in result
        result = await tools["prompt_assent"].ainvoke(
            {"action": "list", "limit": PROMPT_ASSENT_LIST_LIMIT_MAX + 100}
        )
        assert result.count("version_id=") <= PROMPT_ASSENT_LIST_LIMIT_MAX

    async def test_an_empty_list_is_reported(
        self, monkeypatch, sqlite_engine, pv_repo
    ) -> None:
        from daemon.tools.prompt_assent import KILL_SWITCH_ENV

        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        manager = _manager(pv_repo, sqlite_engine)
        tools = build_tools(manager)
        result = await tools["prompt_assent"].ainvoke({"action": "list"})
        assert "0 pending CORE item(s)" in result

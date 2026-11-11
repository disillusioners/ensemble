"""The Phase-3 acceptance-FLOW helper — one wired mint → echo → decide rig.

Builds the FULL wired surface on an in-memory SQLite engine:

* a CORE ``pending_approval`` row (the assentable item);
* a REAL :class:`PromptVersionService` on the manager (the service
  the tool's accept seam calls — the mid-build stub is gone);
* a live factor-2 window + a Discord DM message row (the daemon-owned
  evidence the gate derives the identity from);
* the ``prompt_assent`` tool bound to an ``ari`` caller.

Every Phase-3 test file drives THIS rig so the matrix exercises the
real seams: gate → attestation assembly → service entrypoint →
Phase-1 repo entrypoint → D-4 hook → guarded transition.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from tests.helpers.prompt_assent_tool_fixture import (
    CHANNEL_ID,
    MESSAGE_ID,
    RELAY_INSTANCE_ID,
    SOURCE_ID,
    USER_ID,
    FakeManager,
    FakeMessageRow,
    build_discord_meta,
    build_tools,
    build_window,
)

PENDING_CONTENT = "do not run destructive commands without a human yes\n"
ITEM_AGENT = "ari"
ITEM_ARTIFACT = "rule"


def make_flow_engine() -> Any:
    """Fresh in-memory SQLite with BOTH assent surfaces registered."""
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


def build_flow(
    *,
    engine: Any | None = None,
    window: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
    echo_content: str = "mint it",
    with_service: bool = True,
) -> SimpleNamespace:
    """The wired flow rig — manager + tools + repos + a pending item."""
    from daemon.repositories.prompt_version.repository import (
        PromptVersionRepository,
        actor_from_instance,
    )
    from daemon.repositories.prompt_assent.repository import (
        PromptAssentTokenRepository,
    )
    from daemon.services.prompt_version_service import PromptVersionService

    engine = engine if engine is not None else make_flow_engine()
    repo = PromptVersionRepository(engine)
    submitter = actor_from_instance("inst-submitter-1", ITEM_AGENT)
    draft = repo.create_draft(
        agent_id=ITEM_AGENT,
        version_tag="",
        artifact=ITEM_ARTIFACT,
        content=PENDING_CONTENT,
        actor=submitter,
    )
    pending_row = repo.submit_for_approval(draft.version_id, submitter)
    service = PromptVersionService(repo=repo) if with_service else None
    manager = FakeManager(
        engine=engine,
        pv_repo=repo,
        windows={RELAY_INSTANCE_ID: window if window is not None else build_window()},
        row=FakeMessageRow(
            content=echo_content,
            discord_meta=meta if meta is not None else build_discord_meta(),
        ),
        service=service,
    )
    tools = build_tools(manager)
    return SimpleNamespace(
        engine=engine,
        repo=repo,
        token_repo=PromptAssentTokenRepository(engine),
        service=service,
        manager=manager,
        tools=tools,
        pending_row=pending_row,
        submitter=submitter,
        version_id=pending_row.version_id,
    )


async def mint_for(flow: SimpleNamespace) -> str:
    """Mint for the rig's item; return the mint response."""
    return await flow.tools["prompt_assent"].ainvoke(
        {"action": "mint", "version_id": flow.version_id}
    )


def echo(flow: SimpleNamespace, token: str, **meta_kwargs: Any) -> None:
    """Swap the queue row to the approving human turn (token echoed)."""
    flow.manager._queue_repository._row = FakeMessageRow(
        content=f"approved — {token}",
        discord_meta=build_discord_meta(**meta_kwargs) if meta_kwargs else build_discord_meta(),
    )


async def decide(
    flow: SimpleNamespace,
    *,
    action: str = "approve",
    token: str = "",
    user_confirmed: bool = True,
    reject_reason: str = "not good enough",
    **kwargs: Any,
) -> str:
    """Invoke the tool's decide verb with the echo in place."""
    payload: dict[str, Any] = {
        "action": action,
        "version_id": flow.version_id,
        "token": token,
        "user_confirmed": user_confirmed,
    }
    if action == "reject":
        payload["reject_reason"] = reject_reason
    payload.update(kwargs)
    return await flow.tools["prompt_assent"].ainvoke(payload)

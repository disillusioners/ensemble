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
    RELAY_INSTANCE_ID,
    FakeManager,
    FakeMessageRow,
    build_discord_meta,
    build_tools,
    build_window,
)

PENDING_CONTENT = "do not run destructive commands without a human yes\n"
ITEM_AGENT = "ari"
ITEM_ARTIFACT = "rule"

#: The sentinel default of ``build_flow(designations=…)``: "leave the
#: designation environment EXACTLY as the caller arranged it".
#: ⚠ DEVIATION NOTE (T3.1, flagged in the lane report): the plan's literal
#: "default = scrubbed" on THIS rig would redden Phase 2's un-editable
#: census file (``tests/unit/tools/test_prompt_assent_registration.py``),
#: whose cells ``monkeypatch.setenv(ENV, …)`` BEFORE calling ``build_flow``
#: and rely on the map surviving into the tool call. The sentinel keeps
#: every existing caller byte-green; the "default rig is scrubbed"
#: guarantee for the PHASE-3 cells is delivered by the phase-3 file's own
#: autouse fixture (``_hermetic_designation_map`` — scrubs the variable
#: before every cell), so no phase-3 cell can inherit an ambient operator
#: map (AC1). ``designations=None`` remains an EXPLICIT scrub.
AMBIENT = object()


def _apply_designations(designations: Any) -> None:
    """The hermetic map control (T3.1 / D-3.2): set-or-scrub, never inherit.

    * a ``dict``  → ``os.environ[ENV] = json.dumps(designations)`` — the
      EXACT canonical JSON string of that dict;
    * a ``str``   → set to that EXACT raw string (the invalid-map cells);
    * ``None``    → SCRUB the variable (per-variable pop — the house
      ``env -u`` lesson: no globs);
    * ``AMBIENT`` (the default) → touch nothing (see the sentinel note).
    """
    import json
    import os

    from daemon.repositories.prompt_assent.designation import (
        ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS as _ENV,
    )

    if designations is AMBIENT:
        return
    if designations is None:
        os.environ.pop(_ENV, None)
        return
    if isinstance(designations, str):
        os.environ[_ENV] = designations
        return
    os.environ[_ENV] = json.dumps(designations)


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
    designations: Any = AMBIENT,
) -> SimpleNamespace:
    """The wired flow rig — manager + tools + repos + a pending item.

    ``designations`` is the hermetic map control (T3.1): a ``dict`` sets
    ``ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS`` to its exact canonical
    JSON, a ``str`` sets the EXACT raw value (invalid-map cells),
    ``None`` SCRUBS it; the ``AMBIENT`` default touches nothing (the
    phase-3 file's autouse fixture is what makes ITS default scrubbed —
    see the sentinel note above).
    """
    from daemon.repositories.prompt_version.repository import (
        PromptVersionRepository,
        actor_from_instance,
    )
    from daemon.repositories.prompt_assent.repository import (
        PromptAssentTokenRepository,
    )
    from daemon.services.prompt_version_service import PromptVersionService

    _apply_designations(designations)

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


async def mint_for(flow: SimpleNamespace, **kwargs: Any) -> str:
    """Mint for the rig's item; return the mint response.

    ``**kwargs`` ride the tool payload verbatim (e.g. ``force=True``
    re-mints past a live token — superseding it); existing callers pass
    no kwargs and behave exactly as before.
    """
    payload: dict[str, Any] = {"action": "mint", "version_id": flow.version_id}
    payload.update(kwargs)
    return await flow.tools["prompt_assent"].ainvoke(payload)


def token_of(mint: str) -> str:
    """The raw token out of a mint response (L16 — ONE shared
    extraction; both the designated matrix and the census file parse
    the mint response through this)."""
    return mint.split("token: ", 1)[1].splitlines()[0]


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

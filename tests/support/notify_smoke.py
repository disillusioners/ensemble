"""The ``notify_user`` boot-smoke harness (B1 — RULED mechanism, ari-voice).

WHAT THIS MODULE IS
-------------------
The reusable machinery behind
``tests/integration/test_notify_user_boot_smoke.py``: the scratch
Telegram sink, the scripted mock LLM (the house
``test/packs/lcan_helpers/mock_llm.py`` precedent, named in the
approver fold-ables), the PYTHONPATH base-URL shim, and the in-unit
boot-and-drive script.

THE RULED MECHANISM (approver B1, verbatim shape)
-------------------------------------------------
Scratch PG ``source_configs`` row of TYPE TELEGRAM (real adapter code,
real registry build) + a scratch **aiohttp** sink server answering the
Telegram handshake (``getMe``) AND delivery (``sendMessage``) + base-URL
redirection WITHOUT production-code change via a scratch **PYTHONPATH
shim** prepended in the transient unit's environment (smallest-seam: an
import-hook/sitecustomize-style patch of the adapter's ``TELEGRAM_API_BASE``
constant — NOT a whole-file shadow copy, which drifts). RUNNING
precondition stated explicitly: the smoke drives the scratch source
through its handshake to ``SourceStatus.RUNNING`` BEFORE the
alarm-shaped turn — and the pre-RUNNING refusal path is asserted as the
NEGATIVE case (the adapter is stopped for the probe; the tool must
refuse ``notify_source_disabled``). Pinned sequence: scratch source
RUNNING → ``source="scheduler"`` alarm-shaped wake (the REAL
``manager.enqueue_message_job`` primitive the scheduler fires) → Ari
turn on a scripted/mock LLM emitting the ``notify_user`` tool call (no
real-LLM) → tool executes → sink receives EXACTLY ONE ``sendMessage``
with the expected payload → ``notify_audit`` rows land
(``accepted`` → ``dispatched``) → teardown clean, cgroup-isolated from
the live daemon, scratch PG dropped.

FOLD-ABLE 1 (binding establishment)
-----------------------------------
The scratch Ari session's chat binding is established by PRE-SEEDING
``instance_metadata["original_source"] = "<scratch-source>:<chat_id>"``
(the deterministic half of the fold — the sink-getUpdates inbound leg
would need a full polling round-trip for the same datum; polling is
disabled on the scratch source).

SAFETY FENCE
------------
The daemon runs inside a ``systemd-run`` transient unit (the
wedge-smoke launcher, REUSED — not re-implemented); the driver asserts
its own cgroup is not the live daemon's. The sink and the mock LLM are
child processes of the same unit (same cgroup). The scratch PG is a
uniquely-named database on the tester-provisioned PG16 server
(``PG_TEST_*``), dropped at teardown. The live daemon's identity is
snapshotted before and asserted unchanged after.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

# Reuse the wedge-smoke isolation/fence machinery — the SAME transient
# unit launcher, cgroup assertion and live-fence helpers (single source,
# no drift).
from tests.support.wedge_smoke import (  # noqa: F401  (re-exported)
    assert_cgroup_isolated,
    assert_live_fence_unchanged,
    cgroup_path,
    live_daemon_fence,
    transient_unit_argv,
)


def free_port() -> int:
    """Grab an ephemeral port (bind-and-close — TOCTOU is acceptable
    inside an isolated unit)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


#: The scratch Telegram source id + chat id (deterministic; the sink
#: answers for exactly this bot).
SMOKE_SOURCE_ID = "scratch-telegram"
SMOKE_CHAT_ID = "424242"
SMOKE_BOT_TOKEN = "123456:ABC-smoke-token"
SMOKE_MESSAGE = "SMOKE-NOTIFY-CANARY: the 06:00 brief needs your eyes."

_WAKE_MESSAGE = (
    "Morning brief: it is 06:00. Run your read lenses; if the content "
    "genuinely needs the user, send them one notify_user message with "
    "exactly this text: " + SMOKE_MESSAGE
)


# ── the scratch aiohttp Telegram sink ────────────────────────────────────


def sink_script(port: int, log_path: Path) -> str:
    """The in-unit aiohttp sink: ``getMe`` + ``sendMessage`` (+ a
    defensive ``getUpdates``), recording every delivery as a JSONL
    line."""
    return (
        "import json\n"
        "from aiohttp import web\n"
        "\n"
        f"LOG = {str(log_path)!r}\n"
        f"TOKEN = {SMOKE_BOT_TOKEN!r}\n"
        "\n"
        "async def get_me(request):\n"
        "    return web.json_response({'ok': True, 'result': {'id': 123456, "
        "'is_bot': True, 'username': 'scratch_smoke_bot'}})\n"
        "\n"
        "async def send_message(request):\n"
        "    payload = await request.json()\n"
        "    with open(LOG, 'a', encoding='utf-8') as handle:\n"
        "        handle.write(json.dumps({'method': 'sendMessage', 'payload': payload}) + chr(10))\n"
        "    return web.json_response({'ok': True, 'result': {'message_id': 1, "
        "'chat': {'id': payload.get('chat_id')}}})\n"
        "\n"
        "async def get_updates(request):\n"
        "    # The scratch source runs with polling_enabled=false; this endpoint\n"
        "    # exists so an accidental poll reads an empty batch, never hangs.\n"
        "    return web.json_response({'ok': True, 'result': []})\n"
        "\n"
        "async def health(request):\n"
        "    return web.json_response({'ok': True})\n"
        "\n"
        "def build():\n"
        "    app = web.Application()\n"
        "    app.router.add_post('/bot' + TOKEN + '/getMe', get_me)\n"
        "    app.router.add_post('/bot' + TOKEN + '/sendMessage', send_message)\n"
        "    app.router.add_post('/bot' + TOKEN + '/getUpdates', get_updates)\n"
        "    app.router.add_get('/health', health)\n"
        "    return app\n"
        "\n"
        f"web.run_app(build(), host='127.0.0.1', port={port}, print=lambda *_: None)\n"
    )


# ── the scripted mock LLM (house precedent shape) ───────────────────────


def mock_llm_script(port: int, log_path: Path) -> str:
    """The scripted OpenAI-compatible endpoint (the
    ``test/packs/lcan_helpers/mock_llm.py`` house precedent, named in
    the approver fold-ables).

    Role detection: the request's ``tools`` list carrying
    ``notify_user`` marks the ARI turn. Turn 1 (no ``tool`` messages in
    the history) → emit the ``notify_user`` tool call; turn 2 (a tool
    result present) → final text. EVERY other request (compaction,
    report repair, ...) gets a plain short completion so no lane of the
    turn can wedge on the mock.
    """
    return (
        "import json, threading, time\n"
        "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n"
        "\n"
        f"PORT = {port}\n"
        f"LOG = {str(log_path)!r}\n"
        f"CANARY = {SMOKE_MESSAGE!r}\n"
        "_log_lock = threading.Lock()\n"
        "\n"
        "def log(event):\n"
        "    with _log_lock:\n"
        "        with open(LOG, 'a', encoding='utf-8') as handle:\n"
        "            handle.write(json.dumps(event) + chr(10))\n"
        "\n"
        "def has_tool_result(messages):\n"
        "    return any(m.get('role') == 'tool' for m in messages)\n"
        "\n"
        "def carries_notify_tool(tools):\n"
        "    for tool in tools or []:\n"
        "        if isinstance(tool, dict):\n"
        "            function = tool.get('function') or {}\n"
        "            name = function.get('name') or tool.get('name') or ''\n"
        "        else:\n"
        "            name = getattr(tool, 'name', '') or ''\n"
        "        if name == 'notify_user':\n"
        "            return True\n"
        "    return False\n"
        "\n"
        "class Handler(BaseHTTPRequestHandler):\n"
        "    def log_message(self, *_args):\n"
        "        pass\n"
        "\n"
        "    def do_POST(self):\n"
        "        if self.path != '/v1/chat/completions':\n"
        "            self.send_response(404)\n"
        "            self.end_headers()\n"
        "            return\n"
        "        length = int(self.headers.get('Content-Length', '0'))\n"
        "        body = json.loads(self.rfile.read(length) or b'{}')\n"
        "        messages = body.get('messages') or []\n"
        "        tools = body.get('tools') or []\n"
        "        is_ari = carries_notify_tool(tools)\n"
        "        log({'path': self.path, 'ari_turn': is_ari, 'n_messages': len(messages)})\n"
        "        wants_stream = bool(body.get('stream'))\n"
        "        if is_ari and not has_tool_result(messages):\n"
        "            final_reason = 'tool_calls'\n"
        "            text_delta = None\n"
        "            tool_call_deltas = [\n"
        "                {'index': 0, 'id': 'call_notify_1', 'type': 'function',\n"
        "                 'function': {'name': 'notify_user', 'arguments': ''}},\n"
        "                {'index': 0,\n"
        "                 'function': {'arguments': json.dumps({'message': CANARY})}},\n"
        "            ]\n"
        "        else:\n"
        "            final_reason = 'stop'\n"
        "            text_delta = 'Done - the notification path is settled. Silence otherwise.'\n"
        "            tool_call_deltas = []\n"
        "        if not wants_stream:\n"
        "            if tool_call_deltas:\n"
        "                message = {\n"
        "                    'role': 'assistant',\n"
        "                    'content': None,\n"
        "                    'tool_calls': [{\n"
        "                        'id': 'call_notify_1',\n"
        "                        'type': 'function',\n"
        "                        'function': {\n"
        "                            'name': 'notify_user',\n"
        "                            'arguments': json.dumps({'message': CANARY}),\n"
        "                        },\n"
        "                    }],\n"
        "                }\n"
        "            else:\n"
        "                message = {'role': 'assistant', 'content': text_delta}\n"
        "            payload = json.dumps({\n"
        "                'id': 'chatcmpl-smoke',\n"
        "                'object': 'chat.completion',\n"
        "                'created': int(time.time()),\n"
        "                'model': body.get('model') or 'smoke-mock',\n"
        "                'choices': [{'index': 0, 'message': message, 'finish_reason': final_reason}],\n"
        "                'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2},\n"
        "            }).encode('utf-8')\n"
        "            self.send_response(200)\n"
        "            self.send_header('Content-Type', 'application/json')\n"
        "            self.send_header('Content-Length', str(len(payload)))\n"
        "            self.end_headers()\n"
        "            self.wfile.write(payload)\n"
        "            return\n"
        "        # SSE mode — the daemon's default\n"
        "        # (ThinkingChatOpenAI.default_streaming = True), so the mock\n"
        "        # must speak the streaming protocol.\n"
        "        self.send_response(200)\n"
        "        self.send_header('Content-Type', 'text/event-stream')\n"
        "        self.send_header('Cache-Control', 'no-cache')\n"
        "        self.end_headers()\n"
        "\n"
        "        def emit(chunk):\n"
        "            data = json.dumps({\n"
        "                'id': 'chatcmpl-smoke',\n"
        "                'object': 'chat.completion.chunk',\n"
        "                'created': int(time.time()),\n"
        "                'model': body.get('model') or 'smoke-mock',\n"
        "                'choices': [chunk],\n"
        "            })\n"
        "            self.wfile.write(('data: ' + data + '\\n\\n').encode('utf-8'))\n"
        "\n"
        "        emit({'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None})\n"
        "        if text_delta is not None:\n"
        "            emit({'index': 0, 'delta': {'content': text_delta}, 'finish_reason': None})\n"
        "        for delta in tool_call_deltas:\n"
        "            emit({'index': 0, 'delta': {'tool_calls': [delta]}, 'finish_reason': None})\n"
        "        emit({'index': 0, 'delta': {}, 'finish_reason': final_reason})\n"
        "        self.wfile.write(b'data: [DONE]\\n\\n')\n"
        "\n"
        "ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()\n"
    )


# ── the PYTHONPATH base-URL shim (smallest seam, no production change) ──


def write_shim(directory: Path, sink_port: int) -> Path:
    """Write the ``sitecustomize`` shim: patches the Telegram adapter's
    ``TELEGRAM_API_BASE`` module constant AT IMPORT TIME — the
    smallest-seam base-URL redirection (B1: an import-hook patch of the
    constant, NOT a whole-file shadow copy, which drifts)."""
    directory.mkdir(parents=True, exist_ok=True)
    shim = directory / "sitecustomize.py"
    body = (
        '"""SCRATCH-ONLY base-URL shim (notify_user boot smoke, B1).\n'
        "\n"
        "Prepended via PYTHONPATH for the transient unit's daemon process ONLY.\n"
        "Patches the Telegram adapter's API-base module constant to point at the\n"
        "scratch aiohttp sink. Production code is NEVER modified; the patch\n"
        "exists only inside this smoke's cgroup.\n"
        '"""\n'
        "\n"
        f'_BASE = "http://127.0.0.1:{sink_port}/bot{{token}}/{{method}}"\n'
        "\n"
        "try:\n"
        "    import daemon.sources.adapters.telegram as _tg\n"
        "\n"
        "    _tg.TELEGRAM_API_BASE = _BASE\n"
        "except Exception as _exc:  # loud, never silent\n"
        "    import sys\n"
        "\n"
        '    print("[notify-smoke-shim] FAILED to patch TELEGRAM_API_BASE: %s" % _exc, file=sys.stderr)\n'
    )
    shim.write_text(body, encoding="utf-8")
    return shim


# ── the in-unit boot-and-drive script ────────────────────────────────────


_DRIVER_TEMPLATE = '''
import asyncio, json, sys, time
from pathlib import Path

sys.path.insert(0, ".")  # the repo root (the unit's working directory)

from daemon.sources.base import SourceStatus

OBS = "__OBS__"
SOURCE_ID = "__SOURCE__"
BOT_TOKEN = "__BOT_TOKEN__"
CHAT_ID = "__CHAT__"
WAKE = "__WAKE__"
WAKE_TIMEOUT = __WAKE_TIMEOUT__
SINK_LOG = "__SINK_LOG__"

observations = {}


def record(key, value):
    observations[key] = value
    print("[notify-smoke] %s: %s" % (key, value), flush=True)


def write_observations():
    Path(OBS).write_text(json.dumps(observations, indent=2, default=str), encoding="utf-8")


def _ledger_rows(manager):
    from sqlmodel import Session, select
    from daemon.repositories.notify_audit.models import NotifyAudit

    with Session(manager.engine) as session:
        return [
            {
                "outcome": r.outcome,
                "refusal_reason": r.refusal_reason,
                "source_id": r.source_id,
                "body_sha256": r.body_sha256,
                "body_length": r.body_length,
            }
            for r in session.exec(select(NotifyAudit)).all()
        ]


async def main() -> None:
    from daemon.api import create_app, lifespan

    app = create_app()
    async with lifespan(app):
        manager = app.state.manager
        record("booted", True)

        # -- the scratch ari session + its chat binding (fold-able 1:
        #    pre-seeded instance_metadata) -----------------------------
        instance_repo = manager._instance_repository
        session_id = SOURCE_ID + "-ari-session"
        assert instance_repo.get(session_id) is None, (
            "scratch session already exists - stale scratch DB"
        )
        created = instance_repo.create(
            instance_id=session_id,
            agent_id="ari",
            agent_dir="agents/ari",
            metadata={"original_source": SOURCE_ID + ":" + CHAT_ID},
            status="idle",
        )
        record("session_seeded", created.instance_id)

        # -- the scratch TELEGRAM source_configs row (real adapter code,
        #    real registry build; polling OFF so the sink never needs
        #    getUpdates — the B1 getUpdates-or-polling-off pin,
        #    fold-able 8). Seeded HERE (post-boot, pre-start) so the
        #    autostart race cannot bring the adapter up early: the
        #    adapter runs only when the driver explicitly starts it.
        source_repo = manager._source_repository
        seeded = source_repo.create_source_config(
            source_type="telegram",
            name="notify-smoke-scratch",
            config={"polling_enabled": False, "polling_timeout": 5},
            credentials=json.dumps({"bot_token": "__BOT_TOKEN__"}),
            enabled=True,
            autostart=True,
            source_id=SOURCE_ID,
        )
        record("source_seeded", seeded.source_id)

        registry = manager.source_registry

        # -- the adapter is built + registered through the REAL factory
        #    and register() (the boot path builds only rows that existed
        #    before initialize; this row is seeded post-boot on purpose).
        #    It is registered WITHOUT starting — the honest pre-RUNNING
        #    state (status STOPPED, no supervisor to resurrect it).
        seeded_row = source_repo.get_source_config(SOURCE_ID)
        built = await registry._create_adapter_from_config(seeded_row)
        assert built is not None, "the telegram adapter factory returned None"
        registry.register(built)
        adapter = registry.get(SOURCE_ID)
        assert adapter is not None, "the adapter never registered"

        # -- the pre-RUNNING negative case: the adapter is REGISTERED
        #    but NOT RUNNING -> the D1 preflight must refuse
        #    notify_source_disabled (B1: "that refusal path is itself a
        #    negative-case assertion in the smoke"). -------------------
        from daemon.tools.notify_user import create_notify_user_tools

        (tool,) = create_notify_user_tools(
            manager,
            current_instance_id=session_id,
            agent_id="ari",
        )
        record("precheck_adapter_status", str(adapter.status))
        refusal = await tool.ainvoke({"message": "pre-RUNNING probe"})
        record("precheck_refusal", str(refusal))
        assert "notify_source_disabled" in str(refusal), (
            "the pre-RUNNING probe must refuse with notify_source_disabled - got: "
            + str(refusal)
        )

        # -- drive the scratch source through its handshake to RUNNING
        #    (the B1 RUNNING precondition) ------------------------------
        await registry.start_adapter(SOURCE_ID)
        deadline = time.monotonic() + 30
        while adapter.status is not SourceStatus.RUNNING and time.monotonic() < deadline:
            await asyncio.sleep(0.2)
        record("running_precondition", str(adapter.status))
        assert adapter.status is SourceStatus.RUNNING, (
            "the scratch telegram source never reached RUNNING"
        )

        # cgroup isolation proof (inside the unit)
        from tests.support.wedge_smoke import assert_cgroup_isolated
        import os

        assert_cgroup_isolated(os.getpid())
        record("cgroup", Path("/proc/self/cgroup").read_text().strip())

        # -- the alarm-shaped wake: the REAL scheduler primitive, with
        #    the REAL colon-less source="scheduler" ----------------------
        result = await manager.enqueue_message_job(
            instance_id=session_id,
            message=WAKE,
            source="scheduler",
            priority=1,
        )
        record("wake_enqueued", str(getattr(result, "job_id", None) or result))

        # -- wait for the sink to receive the sendMessage --------------
        sink_log = Path(SINK_LOG)
        deadline = time.monotonic() + WAKE_TIMEOUT
        deliveries = []
        while time.monotonic() < deadline:
            if sink_log.exists():
                deliveries = [
                    json.loads(line)
                    for line in sink_log.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                if deliveries:
                    break
            await asyncio.sleep(0.5)
        record("sink_deliveries", deliveries)

        # -- settle: let the turn finalize + the ledger reach dispatched
        settle_deadline = time.monotonic() + 30
        while time.monotonic() < settle_deadline:
            rows = _ledger_rows(manager)
            if any(r["outcome"] == "dispatched" for r in rows):
                break
            await asyncio.sleep(1.0)
        record("notify_audit_rows", _ledger_rows(manager))
        write_observations()


asyncio.run(main())
'''


def driver_script(observations_path: Path, *, wake_timeout_s: int = 150) -> str:
    """Render the in-unit boot-and-drive script (token-substituted —
    never an f-string, so generated braces stay literal)."""
    sink_log = observations_path.parent / "sink_log.jsonl"
    return (
        _DRIVER_TEMPLATE.replace("__OBS__", str(observations_path))
        .replace("__SOURCE__", SMOKE_SOURCE_ID)
        .replace("__CHAT__", SMOKE_CHAT_ID)
        .replace("__BOT_TOKEN__", SMOKE_BOT_TOKEN)
        .replace("__WAKE__", _WAKE_MESSAGE)
        .replace("__WAKE_TIMEOUT__", str(wake_timeout_s))
        .replace("__SINK_LOG__", str(sink_log))
    )


# ── assertions over the observations ─────────────────────────────────────


def assert_pinned_sequence(observations: dict[str, Any]) -> None:
    """The B1 pinned-sequence assertions (the pytest side, outside the
    unit): the sink saw EXACTLY ONE sendMessage with the expected
    payload, and the ledger carries accepted→dispatched (plus the
    negative-case refusal row, which — per the pinned fold-able 4
    baseline — does NOT block the later accepted send)."""
    deliveries = observations["sink_deliveries"]
    assert len(deliveries) == 1, (
        f"the sink must receive EXACTLY ONE sendMessage, got {len(deliveries)}: "
        f"{deliveries!r}"
    )
    payload = deliveries[0]["payload"]
    assert str(payload.get("chat_id")) == SMOKE_CHAT_ID, payload
    assert payload.get("text") == SMOKE_MESSAGE, payload

    rows = observations["notify_audit_rows"]
    outcomes = [row["outcome"] for row in rows]
    assert "refused" in outcomes, (
        f"the negative-case refusal row is missing: {rows!r}"
    )
    # The accepted→dispatched lifecycle: the write-ahead INSERT settles
    # to `dispatched` — the terminal row IS the lifecycle's evidence (a
    # still-standing `accepted` row would mean the settle never ran).
    assert "dispatched" in outcomes, (
        f"the accepted->dispatched terminal is missing: {rows!r}"
    )
    assert "delivery_unknown" not in outcomes and "delivery_failed" not in outcomes, (
        f"the delivery must be definite, not unknown/failed: {rows!r}"
    )
    for row in rows:
        if row["outcome"] == "dispatched":
            assert row["source_id"] == SMOKE_SOURCE_ID, row
            assert row["body_length"] == len(SMOKE_MESSAGE), row

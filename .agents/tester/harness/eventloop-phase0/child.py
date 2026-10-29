"""The in-process daemon child for the eventloop-phase0 harness.

NOT SHIPPED — test-support only.  Boots the REAL daemon (``create_app``
+ ``lifespan`` + uvicorn on a free TCP port) INSIDE the harness process
(the ``tests/helpers/soak_gate_daemon_child.py`` shape), then layers
HARNESS-ONLY instrumentation on top — nothing under ``daemon/`` is
edited:

* ``RoundTripCounter`` — a SQLAlchemy ``before_cursor_execute`` /
  ``after_cursor_execute`` pair registered on the manager engine,
  attributing every statement to the in-flight REQUEST via a
  contextvar set by the harness ASGI middleware, and to a thread class
  (event-loop vs other) via the measured loop-thread id.  This is the
  N4 decisive metric and the phase-1 flip target (round-trips ON the
  loop thread for the censused shapes must go to ~0 at HEAD).
* test-only routes mounted on the app object AFTER ``create_app()`` —
  ``/harness/lag`` (the daemon's OWN ``LoopLagProbe`` histogram via
  ``app.state.wedge_capture_watchdog``), ``/harness/counters`` (the
  N4 snapshot), ``/harness/loopinfo``.
* tightened wedge-capture knobs via env (the REAL
  ``ENSEMBLE_WEDGE_CAPTURE_*`` names; the capture root lands under the
  harness ``ENSEMBLE_DATA_DIR`` because the real boot passes
  ``data_dir`` — see ``WedgeCaptureConfig.from_env``).

FENCES: scratch-shape refusal guard at import (kept verbatim from the
soak child), per-variable POSTGRES_* pins, FAKE_HOME + ENSEMBLE_DATA_DIR
under a harness mktemp tree, MCP builtins disabled (no network), auth
inert for the load driver.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from common import FenceError, ScratchPG, assert_clean_env

#: The request-attribution tag ("profile:seq" or "endpoint-name").
REQ_TAG: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "evloop_harness_req_tag", default=None
)

def _assert_boot_env_scratch(db_name: str) -> None:
    """The boot-time scratch-shape refusal (the soak child's fence).

    In THIS harness the child env is applied in-process right before
    ``create_app``, so the guard runs at BOOT time (after apply_env),
    not at import time — same contract: refuse any DB name that is not
    scratch-shaped, and refuse any drift from the provisioned scratch
    pair.
    """
    if "test" not in db_name.lower() and "scratch" not in db_name.lower():
        print(json.dumps({"error": "refusing non-scratch database: " + db_name}))
        raise SystemExit(3)


# ════════════════════════════════════════════════════════════════════════
# N4 — the per-request round-trip counter
# ════════════════════════════════════════════════════════════════════════

_TABLE_RE = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+([a-z_][a-z0-9_]*)", re.I)
_VERB_RE = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE|BEGIN|COMMIT|ROLLBACK|SAVEPOINT)", re.I)


def statement_shape(sql: str) -> str:
    """Verb + sorted table set — coarse, stable, human-readable."""
    verb_m = _VERB_RE.match(sql)
    verb = verb_m.group(1).upper() if verb_m else "OTHER"
    tables = "+".join(sorted({t.lower() for t in _TABLE_RE.findall(sql)}))
    return f"{verb} {tables}".strip()


class RoundTripCounter:
    """Per-request SQL round-trip accounting on one engine.

    Thread-safe (the listener fires on the loop thread AND on
    ``asyncio.to_thread`` workers AND on daemon background threads).
    ``on_loop`` attribution uses the measured loop-thread id, so the
    counter doubles as the "sync-DB in the loop frame" instrument.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.loop_thread_ident: int | None = None
        #: tag → {"count": int, "on_loop": int, "by_shape": {shape: [n, total_s]}}
        self.per_request: dict[str, dict[str, Any]] = {}
        #: background (no tag) totals, by shape
        self.background: dict[str, dict[str, Any]] = {}
        self._t0: dict[int, float] = {}
        self.total_statements = 0

    # -- listener wiring (harness code on the real engine) ----------------
    def attach(self, engine) -> None:  # noqa: ANN001 — sqlalchemy Engine
        from sqlalchemy import event

        event.listen(engine, "before_cursor_execute", self._before)
        event.listen(engine, "after_cursor_execute", self._after)

    def _before(self, conn, cursor, statement, parameters, context, executemany) -> None:  # noqa: ANN001
        self._t0[id(cursor)] = time.perf_counter()

    def _after(self, conn, cursor, statement, parameters, context, executemany) -> None:  # noqa: ANN001
        start = self._t0.pop(id(cursor), None)
        dur = time.perf_counter() - start if start is not None else 0.0
        self.total_statements += 1
        shape = statement_shape(statement or "")
        tag = REQ_TAG.get()
        # NATIVE ids on both sides: the probe records
        # threading.get_native_id() (the kernel TID), so the listener
        # compares the same id space.
        on_loop = (
            self.loop_thread_ident is not None
            and threading.get_native_id() == self.loop_thread_ident
        )
        bucket = None
        with self._lock:
            if tag is not None:
                bucket = self.per_request.setdefault(
                    tag, {"count": 0, "on_loop": 0, "by_shape": {}}
                )
            else:
                bucket = self.background.setdefault(
                    shape, {"count": 0, "total_s": 0.0}
                )
                bucket["count"] += 1
                bucket["total_s"] += dur
                return
            bucket["count"] += 1
            if on_loop:
                bucket["on_loop"] += 1
            sh = bucket["by_shape"].setdefault(
                shape, {"count": 0, "on_loop": 0, "total_s": 0.0}
            )
            sh["count"] += 1
            if on_loop:
                sh["on_loop"] += 1
            sh["total_s"] += dur

    # -- snapshot / reset ---------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "per_request": {
                    tag: {
                        "count": v["count"],
                        "on_loop": v["on_loop"],
                        "by_shape": {
                            s: {
                                "count": sv["count"],
                                # PER-SHAPE on-loop attribution (repair
                                # 2026-10-30): the architect's G9 fence
                                # (§6.3) is stated over the on-loop
                                # SHAPE-SET, which the pre-repair counter
                                # could not express (it only kept the
                                # per-request total).
                                "on_loop": sv.get("on_loop", 0),
                                "total_s": round(sv["total_s"], 4),
                            }
                            for s, sv in sorted(v["by_shape"].items())
                        },
                    }
                    for tag, v in self.per_request.items()
                },
                "background": {
                    s: {"count": v["count"], "total_s": round(v["total_s"], 4)}
                    for s, v in sorted(self.background.items())
                },
            }

    def reset_requests(self) -> None:
        with self._lock:
            self.per_request.clear()


# ════════════════════════════════════════════════════════════════════════
# The harness ASGI middleware (request tagging + latency record)
# ════════════════════════════════════════════════════════════════════════

class TaggingMiddleware:
    """Pure-ASGI wrapper: tags the request (header→contextvar), times it.

    The tag travels in an ``X-Evloop-Tag`` HEADER (set by the load
    driver; ignored by every endpoint — an undeclared header changes
    no endpoint behaviour, unlike a query param).  The middleware sets
    the server-side contextvar so the endpoint — and every sync DB
    call it makes on the loop thread — executes with the tag in
    context; that is exactly what the N4 counter reads.

    A plain ASGI middleware (NOT ``BaseHTTPMiddleware``): the inner app
    runs in THIS task, so the contextvar set here is visible to the
    endpoint's direct sync-DB calls (the defect under measurement).

    REPAIR 2026-10-30: the middleware also records the response
    ``body_sha256`` + ``body_len`` per request.  The G5 response-bytes
    gate is a BASE↔HEAD byte-diff of these hashes; without them the gate
    had no artifact to diff (a silent no-op gate).
    """

    def __init__(self, app, recorder: dict[str, list],  # noqa: ANN001
                 body_meta: dict[str, list] | None = None) -> None:
        self.app = app
        self.recorder = recorder
        self.body_meta = body_meta if body_meta is not None else {}

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path.startswith("/harness/"):
            await self.app(scope, receive, send)
            return
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }
        tag = headers.get("x-evloop-tag")
        token = REQ_TAG.set(tag) if tag is not None else None
        t0 = time.perf_counter()
        status_holder: list[int] = []
        digest = hashlib.sha256()
        body_len = 0
        original_send = send

        async def wrapped_send(message) -> None:  # noqa: ANN001
            nonlocal body_len
            if message["type"] == "http.response.start":
                status_holder.append(message["status"])
            elif message["type"] == "http.response.body":
                chunk = message.get("body") or b""
                body_len += len(chunk)
                digest.update(chunk)
            await original_send(message)

        try:
            await self.app(scope, receive, wrapped_send)
        finally:
            dur = time.perf_counter() - t0
            if token is not None:
                REQ_TAG.reset(token)
            if tag is not None:
                self.recorder.setdefault(tag, []).append(
                    (dur, status_holder[0] if status_holder else 0)
                )
                self.body_meta.setdefault(tag, []).append({
                    "sha256": digest.hexdigest(),
                    "len": body_len,
                    "status": status_holder[0] if status_holder else 0,
                })


# ════════════════════════════════════════════════════════════════════════
# The child
# ════════════════════════════════════════════════════════════════════════

class DaemonChild:
    """The booted daemon + instrumentation handles."""

    def __init__(self, scratch: ScratchPG, workdir: Path, port: int,
                 tightened_capture: bool = True) -> None:
        assert_clean_env()
        self.scratch = scratch
        self.workdir = workdir
        self.port = port
        self.counter = RoundTripCounter()
        self.latencies: dict[str, list[tuple[float, int]]] = {}
        #: tag -> [{sha256, len, status}] — the G5 byte-diff artifact.
        self.body_meta: dict[str, list[dict]] = {}
        self.app = None
        self.manager = None
        self.server = None
        self._server_task: asyncio.Task | None = None
        self._lifespan_cm = None
        self._started = False
        self.capture_root = workdir / "data" / "wedge-captures"
        self.loop_thread_ident: int | None = None

        # ── the child env, constructed EXPLICITLY (no ambient) ───────
        env = dict(scratch.env())
        env.update({
            "HOME": str(workdir / "fake-home"),
            "ENSEMBLE_DATA_DIR": str(workdir / "data"),
            "ENSEMBLE_SELF_ENV": "demo",
            "ENSEMBLE_AUTH_ENABLED": "false",   # auth inert for the driver
            "MCP_DISABLE_BUILT_IN_CONTEXT7": "true",
            "MCP_DISABLE_BUILT_IN_PLANE": "true",
        })
        if tightened_capture:
            # TIGHTENED for determinism (real env names — see
            # daemon/services/wedge_capture/config.py:55-106).  The
            # capture ROOT is data_dir-derived (from_env(data_dir=...)
            # wins over ENSEMBLE_WEDGE_CAPTURE_DIR in the real boot).
            env.update({
                "ENSEMBLE_WEDGE_CAPTURE_ENABLED": "true",
                "ENSEMBLE_WEDGE_CAPTURE_CPU_PCT": "55",
                "ENSEMBLE_WEDGE_CAPTURE_CPU_SUSTAIN_S": "5",
                "ENSEMBLE_WEDGE_CAPTURE_LAG_S": "1.0",
                "ENSEMBLE_WEDGE_CAPTURE_LAG_SUSTAIN_S": "4",
                "ENSEMBLE_WEDGE_CAPTURE_THREAD_CONCENTRATION_PCT": "60",
                "ENSEMBLE_WEDGE_CAPTURE_SAMPLE_INTERVAL_S": "0.5",
                "ENSEMBLE_WEDGE_CAPTURE_COOLDOWN_S": "10",
                "ENSEMBLE_WEDGE_CAPTURE_MAX_CAPTURES": "8",
            })
        self.env = env

    def apply_env(self) -> None:
        """Pin the child env onto THIS process (per-variable, explicit)."""
        for var in ("POSTGRES_URL", "DAEMON_PORT"):
            os.environ.pop(var, None)
        os.environ.update(self.env)

    async def start(self, timeout_s: float = 180.0) -> None:
        """create_app + lifespan (manual CM) + uvicorn, then instrument."""
        (self.workdir / "fake-home").mkdir(parents=True, exist_ok=True)
        (self.workdir / "data").mkdir(parents=True, exist_ok=True)
        self.apply_env()
        # The boot-time fence: the applied env must point at the
        # provisioned scratch DB, scratch-shaped, no drift.
        applied = os.environ.get("POSTGRES_DB", "")
        if applied != self.scratch.db:
            raise FenceError(
                f"POSTGRES_DB drifted from the scratch pair: {applied!r} "
                f"!= {self.scratch.db!r}"
            )
        _assert_boot_env_scratch(applied)

        from daemon.api import create_app, lifespan  # noqa: E402 — after env

        self.app = create_app()
        # HARNESS-ONLY routes/middleware — after create_app, before serve.
        self._mount_harness_routes()
        self.app.add_middleware(
            TaggingMiddleware, recorder=self.latencies,
            body_meta=self.body_meta,
        )

        self._lifespan_cm = lifespan(self.app)
        await self._lifespan_cm.__aenter__()
        try:
            self.manager = self.app.state.manager
            engine = getattr(self.manager, "_engine", None)
            if engine is None:
                raise FenceError("manager has no _engine — cannot attach N4")
            self.counter.attach(engine)
            # The loop thread id: the watchdog's OWN lag probe measured it
            # (the probe callback executes on the loop thread).
            watchdog = getattr(self.app.state, "wedge_capture_watchdog", None)
            probe = getattr(watchdog, "_lag_probe", None)
            measured = getattr(probe, "loop_thread_native_id", None)
            self.loop_thread_ident = measured or threading.get_native_id()
            self.counter.loop_thread_ident = self.loop_thread_ident

            import uvicorn

            cfg = uvicorn.Config(
                self.app,
                host="127.0.0.1",
                port=self.port,
                log_level="warning",
                access_log=False,
                lifespan="off",  # lifespan run manually (soak precedent)
            )
            self.server = uvicorn.Server(cfg)
            self._server_task = asyncio.create_task(self.server.serve())
            deadline = time.monotonic() + timeout_s
            while not self.server.started and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            if not self.server.started:
                raise FenceError("uvicorn never started within budget")
            self._started = True
        except BaseException:
            await self._close_lifespan()
            raise

    # -- harness-only surface ----------------------------------------------
    def _mount_harness_routes(self) -> None:
        from fastapi import APIRouter

        router = APIRouter()

        @router.get("/harness/lag")
        async def harness_lag() -> dict:
            watchdog = getattr(self.app.state, "wedge_capture_watchdog", None)
            probe = getattr(watchdog, "_lag_probe", None)
            if probe is None:
                return {"probe": None}
            payload = probe.snapshot_histogram()
            payload["probe_state"] = probe.probe_state
            payload["never_fired"] = probe.never_fired
            payload["last_probe_age_s"] = probe.last_probe_age_s
            return payload

        @router.get("/harness/counters")
        async def harness_counters() -> dict:
            return self.counter.snapshot()

        @router.get("/harness/loopinfo")
        async def harness_loopinfo() -> dict:
            loop = asyncio.get_running_loop()
            ex = getattr(loop, "_default_executor", None)
            return {
                "loop_thread_ident": self.loop_thread_ident,
                "default_executor": None if ex is None else {
                    "type": type(ex).__name__,
                    "max_workers": getattr(ex, "_max_workers", None),
                    "threads": len(getattr(ex, "_threads", []) or []),
                },
            }

        # PRIORITY MOUNT: the daemon's SPA catch-all (``/{path:path}``
        # → index.html) is registered during create_app, so APPENDED
        # routes never match (the boot probe caught this — harness
        # routes returned the SPA's ``Asset not found``).  Insert the
        # harness routes at the FRONT of the router instead.
        self.app.router.routes[0:0] = router.routes

    async def _close_lifespan(self) -> None:
        cm = getattr(self, "_lifespan_cm", None)
        if cm is not None:
            try:
                await cm.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001 — teardown never masks results
                pass
            self._lifespan_cm = None

    async def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
        if self._server_task is not None:
            try:
                await asyncio.wait_for(self._server_task, timeout=30)
            except Exception:  # noqa: BLE001
                pass
        await self._close_lifespan()

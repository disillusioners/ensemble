"""BYTE-PARITY pack for the orjson swap on the two fat JSON routes.

The cpu-l1 "cheap overhead" lane swaps ``json`` → ``orjson`` at exactly
two ≥100 KB render sites:

* ``GET /api/instances`` (``daemon/routers/instances.py`` —
  ``_encode_list_body`` / ``_render_list_body``);
* the metrics query door (``daemon/routers/metrics.py`` —
  ``render_body``, shared by the served body AND the byte-ceiling probe).

WHY THIS PACK EXISTS.  ``orjson`` and the stdlib ``json`` module are NOT
byte-identical renderers, and the differences are the whole risk of the
swap:

1. **float spelling** — a NON-INTEGRAL float in the sub-``1`` range is
   spelled differently (``json``: ``7.2e-05`` / ``1e-07``; ``orjson``:
   ``0.000072`` / ``1e-7``).  Integration floats (``0.015``, ``240.0``,
   ``1024.0``) are identical.
2. **NaN / Infinity** — ``orjson`` emits ``null``; ``json`` with
   ``allow_nan=False`` RAISES (so a payload carrying one was never
   servable before the swap either).
3. **non-``str`` mapping keys** — ``orjson`` raises unless
   ``OPT_NON_STR_KEYS`` is set; with the option it matches ``json``.
4. **datetimes** — ``orjson`` renders aware-UTC as ``+00:00`` unless
   ``OPT_UTC_Z`` is set; the metrics door sets it, so its output matches
   the pydantic-core renderer the door used before the swap.

So the swap is only honest if PROVEN on real-shaped payloads:

* the instances body is proven byte-identical to the PRE-SWAP renderer
  (Starlette's ``JSONResponse.render``, reproduced verbatim as
  ``_stdlib_wire_render``) on a 250-instance production-scale fixture
  with unicode titles/labels, ``None`` fields, child lists and nested
  metadata;
* the metrics body is proven byte-identical to the PRE-SWAP renderer
  (FastAPI's own ``TypeAdapter(dict[str, Any]).dump_json`` path — the
  door returned a bare dict) on a ≥100 KB envelope that INCLUDES the
  subnormal-float values where the two renderers genuinely disagree;
* the probe/render LOCKSTEP is pinned: the byte-ceiling probe must
  measure the bytes that are actually served (swapping one without the
  other is a defect — the ceiling would then bound a fiction).

Run in isolation::

    .venv/bin/python -m pytest -q tests/unit/test_orjson_parity.py
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import orjson
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from daemon.routers import instances as instances_router
from daemon.routers import metrics as metrics_router
from tests.helpers.metrics_query_fixtures import (
    RecordingProvider,
    build_client,
    query_client,
)

#: The PRE-SWAP renderer of the metrics door — FastAPI's serialization
#: path for a bare-dict return (pydantic's Rust core).
ORACLE = TypeAdapter(dict[str, Any])

#: The values whose spelling the two renderers disagree on (documented,
#: not assumed — the assertions below re-derive them).
SUBNORMAL_FLOATS = (7.2e-05, 1.2e-05, 1e-07, 5e-08, 1e-06, 3.3e-09)

#: The request headers every through-app wire-shape request in this pack
#: carries.
#:
#: WHY ``identity``: this pack pins the RENDERER's bytes, not the
#: transport's.  The daemon carries an allowlisted response-gzip
#: middleware (``daemon/api.py``, sibling module m3-gzip) that compresses
#: ``application/json`` bodies at or above 8192 B when the client offers
#: gzip — and httpx offers ``gzip, deflate`` by DEFAULT.  Without this
#: pin, a ≥8 KB body arrives deflated and every byte assertion here
#: would be measuring the compressor instead of the renderer (it also
#: desynchronises ``content-length`` from ``len(response.content)``).
#:
#: So the pin is deliberately TRANSPORT-AGNOSTIC: it asserts the identity
#: bytes on a tree WITH the middleware and WITHOUT it.  The compression
#: wire itself is owned by ``tests/unit/test_gzip_middleware.py`` — this
#: file asserts nothing about it.
IDENTITY_HEADERS = {"Accept-Encoding": "identity"}


# ── fixtures ───────────────────────────────────────────────────────────────


def _instance_payload(count: int = 250) -> dict[str, Any]:
    """A PRODUCTION-SCALE ``GET /api/instances`` body.

    Built through the real response model exactly as the route builds it
    (``InstanceListResponse.model_dump(mode="json", exclude=…)``), so the
    payload carries what the route really renders: unicode titles and
    agent tags, nulls everywhere the model allows them, child id lists,
    nested per-instance metadata, and ISO-8601 timestamp STRINGS (the
    ``mode="json"`` dump stringifies them — no live ``datetime`` reaches
    the renderer).
    """
    from daemon.models.instance import InstanceInfo, InstanceListResponse
    from daemon.repositories.instance.models import InstanceStatus

    base = datetime(2026, 11, 21, 3, 0, tzinfo=timezone.utc)
    titles = [
        "Ünïcode ✓ títle",
        "日本語のタスク",
        "emoji 🚀 payload",
        'quote " and \\ backslash and \ttab',
        "fix the widget",
    ]
    instances = []
    for i in range(count):
        instances.append(
            InstanceInfo(
                instance_id=str(uuid.UUID(int=(i + 1) * 7919)),
                agent_id=["developer", "architect", "tester", None][i % 4],
                agent_tag=[None, "v2", "exp-1"][i % 3],
                agent_dir=["agents/developer", "agents/architect"][i % 2],
                status=InstanceStatus(
                    ["running", "completed", "idle", "error"][i % 4]
                ),
                parent_id=None if i % 3 == 0 else str(uuid.UUID(int=(i + 5) * 104729)),
                title=titles[i % len(titles)] if i % 7 else None,
                initiative_message=(
                    None
                    if i % 2
                    else (
                        "please investigate the failing lane and report back "
                        "with evidence — "
                        + "context " * 200
                    )
                ),
                children=[str(uuid.UUID(int=(i + 11) * 1299709 + k)) for k in range(i % 4)],
                model=[None, "claude-opus-4", "gpt-5"][i % 3],
                watchover_enabled=bool(i % 2),
                watchover_context=None if i % 4 else "ctx " * 40,
                watchover_denial_count=0,
                created_at=base - timedelta(minutes=i * 7),
                updated_at=None if i % 5 == 0 else base - timedelta(minutes=i),
                project_id=[None, "83da04de-a410-4fb5-9e92-251a99d28a52"][i % 2],
                pinned=None if i % 6 == 0 else bool(i % 3),
                color_tag=[None, "red", "blue", "#ff0000"][i % 4],
                icon_tag=[None, "star"][i % 2],
                pinned_at=None if i % 3 else base - timedelta(hours=i),
            )
        )
    response = InstanceListResponse(
        instances=instances,
        total=count + 162,
        limit=count,
        offset=0,
        has_more=True,
        truncated=False,
    )
    return response.model_dump(
        mode="json", exclude={"instances": {"__all__": {"mcp_tool_names"}}}
    )


def _metrics_envelope_envelope(count: int = 500, points: int = 60) -> dict[str, Any]:
    """A ≥100 KB real-shaped ``/series`` envelope (the door's fat body).

    Deliberately INCLUDES ``SUBNORMAL_FLOATS`` values (a latency-ish
    histogram bucket / a rate in the ``1e-5`` range), unicode labels and
    astral-plane characters — the payloads where a naive swap would
    silently change bytes.
    """
    series = []
    for i in range(count):
        series.append(
            {
                "name": f"ensemble_checkpoint_read_size_bucket_hits_total_{i:03d}",
                "labels": {
                    "op": ["aget", "aput", "alist"][i % 3],
                    "lane": f"worker-{i % 9}",
                    "path": "/srv/数据/" + "y" * 40,
                    "note": "Ünïcode ✓ 🚀",
                },
                "kind": "histogram" if i % 7 == 0 else "counter",
                "monotonic": not (i % 7 == 0),
                "last_publish_age_s": round(0.05 + (i % 97) * 0.05, 3),
                "value": (
                    None
                    if i % 7 == 0
                    else (SUBNORMAL_FLOATS[i % len(SUBNORMAL_FLOATS)] if i % 11 == 0 else float(i) + 0.5)
                ),
                **(
                    {
                        "histogram": {
                            "count": i,
                            "sum": round(i * 1.5, 2),
                            "buckets": [0, 1, 2, 5, 9, 17, 33, 65],
                        }
                    }
                    if i % 7 == 0
                    else {}
                ),
                "points": [
                    [1_700_000_000 + k * 15, round(k * 0.25, 3)] for k in range(points)
                ],
            }
        )
    envelope = {
        "schema_version": 1,
        "vocabulary_version": 11,
        "generated_at_utc": "2026-11-21T07:40:00.123456+00:00",
        "restart_epoch": "0fafed2dbc644481a0295e8bcf6ee461",
        "activation": {
            "listener_enabled": True,
            "census_enabled": True,
            "query_api": "auth-gated-always-on",
            "registry_read_ok": True,
        },
        "window": {
            "from_utc": "2026-11-21T05:40:00+00:00",
            "to_utc": "2026-11-21T07:40:00+00:00",
            "interval_s": 15.0,
            "interval_s_source": "observed",
        },
        "last_sample_age_s": 3.2,
        "stale": False,
        "truncated": False,
        "series": series,
    }
    assert len(metrics_router.render_body(envelope)) >= 100_000
    return envelope


def _big_snapshot_rows(count: int = 500) -> list[dict[str, Any]]:
    """Real-shaped ``/snapshot`` provider rows (the route's fat body)."""
    rows: list[dict[str, Any]] = []
    for i in range(count):
        rows.append(
            {
                "name": f"ensemble_p3_snapshot_gauge_{i:03d}",
                "labels": {
                    "index": str(i),
                    "lane": f"worker-{i % 9}",
                    "path": "/srv/数据/" + "w" * 40,
                },
                "kind": "gauge",
                "monotonic": False,
                "value": (
                    SUBNORMAL_FLOATS[i % len(SUBNORMAL_FLOATS)]
                    if i % 13 == 0
                    else float(i) + 0.25
                ),
                "last_publish_age_s": round(0.5 + (i % 41) * 0.1, 2),
            }
        )
    return rows


# ── (1) the instances body: byte parity at production scale ────────────────


class TestInstancesListBodyParity:
    """``_encode_list_body`` must reproduce the PRE-SWAP wire bytes."""

    def test_production_scale_payload_is_byte_identical(self) -> None:
        payload = _instance_payload()
        stdlib_bytes = instances_router._stdlib_wire_render(payload)
        # The parity claim is only meaningful at production scale (the
        # ≥100 KB site this lane targeted).
        assert len(stdlib_bytes) >= 100_000
        swapped = instances_router._encode_list_body(payload)
        assert swapped == stdlib_bytes, (
            "the orjson-rendered instance list drifted from the pre-swap "
            f"renderer ({len(swapped)} vs {len(stdlib_bytes)} bytes)"
        )
        # The fixture must contain what makes parity non-trivial.
        assert b"\\u00dc" not in stdlib_bytes, "the body must be UTF-8, not escaped"
        assert "日本語" in stdlib_bytes.decode("utf-8")
        assert b'"title":null' in swapped

    def test_the_body_carries_no_float_field_at_all(self) -> None:
        """The documented reason the float delta is UNREACHABLE here.

        ``InstanceInfo`` has no ``float`` member (ids, dirs, statuses,
        titles, child ids, timestamp STRINGS, bookkeeping ints/bools).
        If a float ever lands on the row, this pin reds and the parity
        claim above has to be re-derived instead of trusted.
        """
        payload = _instance_payload(count=12)
        found: list[Any] = []

        def walk(node: Any) -> None:
            if isinstance(node, float):
                found.append(node)
            elif isinstance(node, dict):
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(payload)
        assert found == [], f"an instance row grew a float field: {found[:5]}"

    def test_a_payload_orjson_cannot_express_falls_back_to_stdlib(self) -> None:
        """An out-of-64-bit int keeps the PRE-SWAP bytes (no new 500)."""
        payload = {"total": 2**70, "instances": []}
        assert instances_router._encode_list_body(payload) == (
            instances_router._stdlib_wire_render(payload)
        )

    def test_non_str_keys_match_the_stdlib_spelling(self) -> None:
        """``OPT_NON_STR_KEYS`` keeps ``json``'s key coercion (1 → "1")."""
        payload = {1: "a", True: "b", 1.5: "c"}
        assert instances_router._encode_list_body(payload) == (
            instances_router._stdlib_wire_render(payload)
        )


class TestInstancesListRouteWireShape:
    """The ROUTE still serves the same status + headers + bytes."""

    @pytest.fixture
    def client(self):
        """The house auth-off posture (the committed golden pack's pattern).

        ``tests/api/test_instances_list_response_bytes.py`` neutralises the
        auth middleware the same way; without it the route 302s to the
        login page and the body assertion never runs.
        """
        from daemon.api import app
        from daemon.middleware.auth import AuthMiddleware
        from daemon.services import db_offload as _db_offload

        def _cls_of(entry: Any) -> Any:
            return getattr(entry, "cls", entry)

        removed = [
            e for e in list(app.user_middleware) if _cls_of(e) is AuthMiddleware
        ]
        for entry in removed:
            app.user_middleware.remove(entry)
        original_cfg = getattr(app.state, "auth_config", None)
        if original_cfg is not None:
            app.state.auth_config = None
        if removed:
            app.middleware_stack = None

        payload = _instance_payload(count=250)
        previous = getattr(app.state, "manager", None)
        app.state.manager = _PayloadManager(payload)
        app.state.start_time = 1000.0
        try:
            yield app
        finally:
            if previous is not None:
                app.state.manager = previous
            if removed:
                for entry in removed:
                    app.user_middleware.insert(0, entry)
                app.middleware_stack = None
            if original_cfg is not None:
                app.state.auth_config = original_cfg

    async def test_status_headers_and_bytes(self, client) -> None:
        import httpx

        app = client
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            # ``limit=100`` is the route's MAX_PAGE_LIMIT — the fattest
            # single page the daemon can ever serve for this route.
            # ``IDENTITY_HEADERS``: the assertion below is about the
            # RENDERER's bytes, so the transport must not deflate them
            # (a >=8192 B JSON body is compressible by the daemon's
            # allowlisted gzip middleware when the client offers gzip —
            # httpx does by default).
            response = await ac.get(
                "/api/instances?limit=100", headers=IDENTITY_HEADERS
            )

        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "application/json"
        assert "content-encoding" not in response.headers, (
            "the identity request must be served UNENCODED — a "
            f"{response.headers.get('content-encoding')!r} body here means "
            "the renderer's bytes were not what arrived"
        )
        assert response.headers["content-length"] == str(len(response.content))
        body = response.content
        assert len(body) >= 100_000, "the response must be production-scale"
        assert b"mcp_tool_names" not in body
        # The served bytes ARE the pre-swap wire render of the document the
        # route produced (the parity claim, measured on the real response).
        decoded = json.loads(body)
        assert body == instances_router._stdlib_wire_render(decoded)
        assert body == instances_router._encode_list_body(decoded)


class _PayloadManager:
    """A manager stand-in whose ``list_instances`` serves a fixed payload."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self._rows = payload["instances"]

    @property
    def is_write_paused(self) -> bool:
        return False

    @property
    def _instance_ui_prefs_repo(self) -> Any:
        return _EmptyPrefs()

    def list_instances(self, **kwargs: Any) -> tuple[list[dict], int, bool]:
        rows = self._rows
        limit = int(kwargs.get("limit") or 10)
        offset = int(kwargs.get("offset") or 0)
        return rows[offset : offset + limit], len(rows), False


class _EmptyPrefs:
    def get_all(self, instance_ids: list[str]) -> dict[str, Any]:
        return {}


# ── (2) the metrics door: byte parity against the PRE-SWAP oracle ──────────


class TestMetricsDoorParity:
    """``render_body`` is byte-identical to the pydantic-core oracle."""

    def test_100kb_plus_envelope_is_byte_identical_to_the_oracle(self) -> None:
        envelope = _metrics_envelope_envelope()
        oracle = ORACLE.dump_json(envelope)
        swapped = metrics_router.render_body(envelope)
        assert swapped == oracle, (
            "the orjson-rendered metrics envelope drifted from the pre-swap "
            f"renderer ({len(swapped)} vs {len(oracle)} bytes)"
        )

    def test_the_fixture_actually_exercises_the_float_delta(self) -> None:
        """The subnormal floats present in the fixture DO split the two.

        This is the pin that makes the parity claim above meaningful: the
        fixture is not accidentally float-free.  It also pins WHY the
        door is safe: its pre-swap renderer was pydantic-core, which
        spells these values exactly as orjson does — the stdlib
        ``json.dumps`` spelling is what the OLD PROBE used, and that
        probe/render mismatch is precisely what the lockstep removes.
        """
        probe = {"v": 7.2e-05, "w": 1e-07}
        stdlib = json.dumps(probe, separators=(",", ":"))
        assert stdlib == '{"v":7.2e-05,"w":1e-07}'
        assert metrics_router.render_body(probe) == ORACLE.dump_json(probe)
        assert metrics_router.render_body(probe) != stdlib.encode()

    def test_nan_and_infinity_become_null_instead_of_raising(self) -> None:
        """A documented, DISCLOSED delta (unreachable on real envelopes).

        ``json.dumps(..., allow_nan=False)`` RAISED on NaN/Infinity, so
        such a payload was never servable before the swap; orjson renders
        ``null``.  The metrics registry never publishes a non-finite
        value (histogram bounds are finite by construction and the
        ``+Inf`` bucket is a label, not a value), so the delta cannot
        reach the wire — but it is pinned here so it is visible.
        """
        payload = {"a": float("inf"), "b": float("nan"), "c": [float("-inf")]}
        with pytest.raises(ValueError):
            json.dumps(payload, allow_nan=False, separators=(",", ":"))
        assert metrics_router.render_body(payload) == b'{"a":null,"b":null,"c":[null]}'

    def test_aware_utc_datetimes_render_as_z(self) -> None:
        """``OPT_UTC_Z`` keeps the oracle's ``Z`` suffix (not ``+00:00``)."""
        payload = {
            "naive": datetime(2026, 11, 21, 7, 0),
            "utc": datetime(2026, 11, 21, 7, 0, tzinfo=timezone.utc),
            "offset": datetime(
                2026, 11, 21, 7, 0, tzinfo=timezone(timedelta(hours=7))
            ),
        }
        assert metrics_router.render_body(payload) == ORACLE.dump_json(payload)
        assert b'"utc":"2026-11-21T07:00:00Z"' in metrics_router.render_body(payload)

    def test_non_str_keys_match_the_oracle(self) -> None:
        import warnings

        payload = {1: "a", True: "b", 1.5: "c", "d": 2}
        with warnings.catch_warnings():
            # The oracle WARNS on a coerced key; the comparison is the point.
            warnings.simplefilter("ignore")
            oracle = ORACLE.dump_json(payload)
        assert metrics_router.render_body(payload) == oracle

    def test_an_unencodable_payload_falls_back_to_the_oracle(self) -> None:
        """Out-of-64-bit ints keep the PRE-SWAP behaviour (no new 500)."""
        payload = {"big": 2**70, "tiny": 1}
        assert metrics_router.render_body(payload) == ORACLE.dump_json(payload)


# ── (3) probe/render LOCKSTEP (the defect this pack exists to forbid) ──────


class TestProbeRenderLockstep:
    """The byte-ceiling probe MUST measure the bytes that are served."""

    def test_the_probe_returns_exactly_the_served_length(self) -> None:
        envelope = _metrics_envelope_envelope()
        served = metrics_router.render_body(envelope)
        assert metrics_router._encoded_size(envelope) == len(served)

    def test_the_probe_is_not_the_old_stdlib_dump(self) -> None:
        """The pre-swap probe (``json.dumps``) would under/over-measure."""
        envelope = _metrics_envelope_envelope()
        stdlib_len = len(
            json.dumps(envelope, separators=(",", ":"), default=str).encode("utf-8")
        )
        assert metrics_router._encoded_size(envelope) != stdlib_len, (
            "the probe still measures the stdlib spelling — the served body "
            "uses a different one, so the ceiling bounds a fiction"
        )

    def test_the_routes_serve_pre_rendered_bytes(self) -> None:
        """A returned ``Response`` means FastAPI does NOT encode the body."""
        import fastapi.routing as fastapi_routing

        calls: list[Any] = []
        original = fastapi_routing.serialize_response

        async def _spy(**kwargs: Any) -> Any:
            calls.append(kwargs.get("dump_json"))
            return await original(**kwargs)

        fastapi_routing.serialize_response = _spy
        try:
            client = build_client(
                RecordingProvider(snapshot=_big_snapshot_rows(count=60))
            )
            try:
                # IDENTITY_HEADERS: the body is >8192 B, so a
                # compression-capable client would be deflated by the
                # daemon's gzip middleware on an integrated tree.
                response = client.get(
                    "/api/metrics/snapshot", headers=IDENTITY_HEADERS
                )
            finally:
                client.__exit__(None, None, None)
        finally:
            fastapi_routing.serialize_response = original
        assert response.status_code == 200
        assert calls == [], "the door must not let FastAPI encode the body"

    def test_a_full_snapshot_body_equals_the_oracle_and_stays_under_the_ceiling(
        self,
    ) -> None:
        from daemon.services.metrics_query.constants import RESPONSE_BYTE_CEILING

        with query_client(RecordingProvider(snapshot=_big_snapshot_rows())) as client:
            # IDENTITY_HEADERS: a 112 KB body is well over the gzip
            # middleware's 8192 B threshold, and the assertions below are
            # about the renderer's bytes, not the transport's.
            response = client.get(
                "/api/metrics/snapshot?limit=500", headers=IDENTITY_HEADERS
            )
        assert response.status_code == 200, response.text
        assert "content-encoding" not in response.headers, (
            "the identity request must be served UNENCODED — the byte "
            "assertions below measure the renderer, not a compressor"
        )
        body = response.content
        assert len(body) <= RESPONSE_BYTE_CEILING
        # The served body IS the oracle render of the document it decodes to
        # (re-rendering the decoded document reproduces the same float
        # spelling — a stdlib render would not).
        assert body == ORACLE.dump_json(json.loads(body))

    def test_the_over_ceiling_trim_uses_the_served_renderer(self, monkeypatch) -> None:
        """A trimmed body is trimmed against the SERVED encoding."""
        calls: list[int] = []
        original = metrics_router.render_body

        def _record(payload: Any) -> bytes:
            out = original(payload)
            calls.append(len(out))
            return out

        monkeypatch.setattr(metrics_router, "render_body", _record)
        rows = [
            {
                "name": f"ensemble_p3_bulk_{i}",
                "labels": {"payload": "x" * 4_000},
                "kind": "gauge",
                "monotonic": False,
                "value": 7.2e-05,
                "last_publish_age_s": 0.5,
            }
            for i in range(400)
        ]
        with query_client(RecordingProvider(snapshot=rows)) as client:
            # IDENTITY_HEADERS: the trimmed body lands just under the 1 MiB
            # ceiling, i.e. far above the gzip middleware's 8192 B
            # threshold — the ceiling assertion below must measure the
            # RENDERED bytes, not a deflated transport.
            response = client.get("/api/metrics/snapshot", headers=IDENTITY_HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert body["truncated"] is True and body.get("next_cursor")
        assert calls, "the probe/render path must have run"
        assert "content-encoding" not in response.headers
        assert len(response.content) <= 1_048_576
        assert response.content == ORACLE.dump_json(body)


# ── (4) the swap is the ONLY change: no other renderer moved ───────────────


class TestSwapScope:
    """Only the two ≥100 KB sites moved; no other call site did."""

    def test_the_instances_router_still_renders_only_one_body(self) -> None:
        source = (
            __import__("pathlib").Path(instances_router.__file__).read_text("utf-8")
        )
        assert source.count("orjson.dumps(") == 1
        assert "JSONResponse(" not in source

    def test_the_metrics_door_has_exactly_one_renderer(self) -> None:
        source = __import__("pathlib").Path(metrics_router.__file__).read_text("utf-8")
        assert source.count("orjson.dumps(") == 1, (
            "a second orjson call site in the door would mean the probe and "
            "the served body can drift apart"
        )
        assert source.count("return Response(content=body") == 3, (
            "all three query routes must serve the pre-rendered bytes"
        )

    def test_the_metrics_read_tool_was_left_on_stdlib(self) -> None:
        """Site (c) is deliberately NOT swapped — a REAL byte delta exists.

        ``daemon/tools/metrics_read.py`` renders its agent-tool bodies
        with ``json.dumps(..., indent=2, default=str)``.  MEASURED
        (real-shaped fixtures): the default call is ~76 KB, and a
        ``metrics_history(top_n=50)`` body is ~375 KB — so this site DOES
        have a ≥100 KB path.  It is still left on stdlib, because the
        swap cannot be made byte-identical there:

        * ``json.dumps`` defaults to ``ensure_ascii=True``, so the
          shipped prose escapes (the emitted ``…`` U+2026 in every
          ``narrow_hint`` becomes ``\u2026``); ``orjson`` has no ASCII
          option and always emits UTF-8 — a real, visible byte delta on
          the tool's own output;
        * the tool's ``_encoded_size`` probe measures the ``indent=2``
          form, so the delta would also shift the byte-ceiling
          measurement.

        The brief's rule is explicit: a site that shows a real byte
        delta keeps the stdlib renderer and DISCLOSES it.  Disclosed
        here, and in the module report.
        """
        from daemon.tools import metrics_read

        source = __import__("pathlib").Path(metrics_read.__file__).read_text("utf-8")
        assert "orjson" not in source
        # THREE tool bodies + the tool's own indent=2 byte-ceiling probe.
        assert source.count("json.dumps(payload, indent=2, default=str)") == 4

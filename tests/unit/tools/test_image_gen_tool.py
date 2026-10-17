"""Tests for the ``image-gen`` tool category (``daemon/tools/image_gen.py``).

Image-Gen Tool commission, Phase 1 (2026-10-14) — plan DoD item 1 plus the
reviewer implementation-time notes (a)-(d). Mirrors the conventions of
``tests/unit/tools/test_ens_env_tools.py`` + ``test_live_views_tool.py``;
the MockTransport mock sits INSIDE the wrapper under test via the module's
``_TRANSPORT_FACTORY`` seam (canonical pattern
``tests/unit/test_llm_failover_v2.py:326-478``).

Coverage map (plan DoD item 1 + reviewer notes):

1. Factory shape — single tool, category ``image-gen``, full doc attached.
2. Env resolution + LIVE-by-default — unset ``IMAGE_GEN_MODEL`` falls
   back to the ``IMAGE_GEN_DEFAULT_MODEL`` purpose-id (``"img-gen"``);
   env-set value wins. Base/key unresolvable ⇒
   ``image_gen_not_configured`` envelope (model never trips it).
   Timeout clamp.
3. Arg validation truth-tables — 1500-char cap, aspect/width-height mutex +
   pair-only + 512-2048÷8, n≤4, seed int64.
4. Wire shape — POST ``<base>/image_generation``, request JSON, identity
   headers, SINGLE attempt (no retry — upstream 10 RPM).
5. Parse truth-table — single-object ``data``; string counts coerced;
   ``base_resp.status_code==0`` gate with NO whitelisting (AM2); ANY
   non-zero status_code ⇒ envelope; proxy 502 = upstream body verbatim ⇒
   parse ``base_resp`` from the 502 body / proxy 504 = OpenAI envelope ⇒
   parse ``error.message`` (AM1); malformed-HTTP-200-body rows (review-note
   (d)); empty-success disclosure (AM5); partial success.
6. OSS fetch + size guard + ``image_save`` provenance
   ``feature="image-gen"`` (D9); content-type inference chain (AM8).
7. Client timeout DERIVES FROM THE KNOB (httpx default 5s would misclassify
   a live ~17s generation).
8. AM6 placement — hanging transports isolate the POST-phase cap vs the
   fetch-phase caps.
9. Registry + allowlist — category resolves; 6/6 consumer agents granted;
   critic NOT granted; all 6 meta.json parse.
"""

import asyncio
import json
import socket
import time
from pathlib import Path

import httpx
import pytest

import daemon.tools.image_gen as image_gen
from daemon.tools.image_gen import (
    CATEGORY_NAME,
    DEFAULT_TIMEOUT_S,
    IMAGE_GEN_DEFAULT_MODEL,
    MAX_IMAGE_BYTES,
    MAX_N,
    MAX_PROMPT_CHARS,
    _clamp_timeout,
    _resolve_env,
    create_image_gen_tools,
)
from daemon.tools._tool_registry import CATEGORY_MODULES, KNOWN_TOOL_NAMES

BASE = "https://proxy.test/v1"
SUCCESS_URL = "https://oss.test/img/a.png"

# Minimal valid HTTP-200 body — single-object data (AM1/AM2), STRING counts
# (live-proven proxy shape), base_resp.status_code == 0 gate.
SUCCESS_BODY = {
    "data": {"image_urls": [SUCCESS_URL], "image_base64": None},
    "metadata": {"success_count": "1", "failed_count": "0"},
    "base_resp": {"status_code": 0, "status_msg": "success"},
}


# ─── Fixtures ──────────────────────────────────────────────────────────────────


class _FakeRecord:
    def __init__(self, image_id, content_bytes, content_type, provenance, retention_class):
        import hashlib

        self.image_id = image_id
        self.content_type = content_type
        self.size_bytes = len(content_bytes)
        self.sha256_hex = hashlib.sha256(content_bytes).hexdigest()
        self.provenance = provenance
        self.retention_class = retention_class


class _FakeStore:
    """Captures ``save`` calls; mirrors the TmpImageStore.save signature."""

    def __init__(self):
        self.saved: list[dict] = []

    def save(self, image_id, content_bytes, content_type, *, provenance=None,
             retention_class="normal"):
        self.saved.append(
            {
                "image_id": image_id,
                "content_bytes": content_bytes,
                "content_type": content_type,
                "provenance": provenance,
                "retention_class": retention_class,
            }
        )
        return _FakeRecord(
            image_id, content_bytes, content_type, provenance, retention_class
        )


class _FakeInstanceRepo:
    def get(self, instance_id):
        return type("Row", (), {"agent_id": "worker"})()


def _fake_manager(store):
    return type(
        "Manager",
        (),
        {"tmp_image_store": store, "_instance_repository": _FakeInstanceRepo()},
    )()


class _TransportSpy:
    """MockTransport wrapper counting handler invocations (no-retry pin)."""

    def __init__(self, handler):
        self._handler = handler
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        return self._handler(request)

    def as_transport(self):
        return httpx.MockTransport(self.__call__)


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 64


@pytest.fixture
def env_ok(monkeypatch):
    """Fully-configured env; unknown env keys scrubbed first."""
    for key in (
        "IMAGE_GEN_MODEL",
        "IMAGE_GEN_BASE_URL",
        "IMAGE_GEN_API_KEY",
        "IMAGE_GEN_TIMEOUT_S",
        "OPENAI_BASE_URL",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("IMAGE_GEN_MODEL", "minimax-image-01")
    monkeypatch.setenv("IMAGE_GEN_BASE_URL", BASE)
    monkeypatch.setenv("IMAGE_GEN_API_KEY", "test-key")
    return monkeypatch


@pytest.fixture
def store():
    return _FakeStore()


def _wire(monkeypatch, handler, store):
    """Wire the transport seam + fake store into a fresh tool; return (tool, spy)."""
    spy = _TransportSpy(handler)
    monkeypatch.setattr(image_gen, "_TRANSPORT_FACTORY", lambda: spy.as_transport())
    # SSRF guard bypass for OSS test URLs (``.test`` is RFC 6761 reserved-for-testing
    # and does NOT resolve in this environment). Tests that exercise the guard
    # explicitly (Finding 2) mock ``_ensure_public_url`` directly and do not
    # rely on this default. Production never installs this monkeypatch.
    monkeypatch.setattr(
        image_gen, "_ensure_public_url", lambda url: (url, None)
    )
    tools = create_image_gen_tools(_fake_manager(store), "inst-1")
    assert len(tools) == 1
    return tools[0], spy


def _err_code(result: str) -> str:
    """Parse the envelope code out of an ``Error: {...}`` string."""
    assert result.startswith("Error:"), result
    payload = json.loads(result[len("Error: "):])
    assert payload["ok"] is False
    return payload["code"]


def _ok_payload(result: str) -> dict:
    payload = json.loads(result)
    assert payload["ok"] is True
    return payload


# ─── 1. Factory shape ──────────────────────────────────────────────────────────


class TestFactoryShape:
    def test_single_tool_named_generate_image(self):
        tools = create_image_gen_tools(manager=None, current_instance_id="x")
        assert len(tools) == 1
        assert tools[0].name == "generate_image"

    def test_category_image_gen_first_party(self):
        (tool,) = create_image_gen_tools(manager=None, current_instance_id="x")
        assert getattr(tool, "_tool_category", None) == "image-gen"
        assert getattr(tool, "_tool_category_first_party", False) is True

    def test_full_doc_attached_teaches_marker_discipline(self):
        (tool,) = create_image_gen_tools(manager=None, current_instance_id="x")
        full_doc = getattr(tool, "_full_doc_", "")
        assert "<!-- ens-img:image-gen:" in full_doc
        assert "VERBATIM" in full_doc

    def test_category_doc_present(self):
        assert CATEGORY_NAME == "Image Gen"
        assert "generate_image" in image_gen.CATEGORY_DOC


# ─── 2. Env resolution + LIVE-by-default model fallback ─────────────────────────


class TestEnvResolution:
    async def test_unset_model_falls_back_to_img_gen_purpose_id(self, monkeypatch, store):
        """Unset ``IMAGE_GEN_MODEL`` ⇒ falls back to the
        ``IMAGE_GEN_DEFAULT_MODEL`` purpose-id (``"img-gen"``) instead
        of fail-closed. The tool is LIVE-by-default; the proxy resolves
        purpose → real provider/model+credentials. Env value still wins
        when set (other tests pin that). Base/key fallbacks used when
        their dedicated env vars are unset.
        """
        for key in ("IMAGE_GEN_MODEL", "IMAGE_GEN_BASE_URL", "IMAGE_GEN_API_KEY"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("OPENAI_BASE_URL", BASE)
        monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("authorization")
            seen["body"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(200, json=SUCCESS_BODY)

        tool, _ = _wire(monkeypatch, handler, store)
        monkeypatch.setattr(
            image_gen, "_fetch_image_bytes", lambda url: _fake_fetch_ok(url)
        )
        result = await tool.ainvoke({"prompt": "a red fox"})
        assert "image_gen_not_configured" not in result
        # The model on the wire is the purpose-id "img-gen", not the
        # main OPENAI_MODEL.
        assert seen["body"]["model"] == IMAGE_GEN_DEFAULT_MODEL == "img-gen"
        assert seen["auth"] == "Bearer openai-key"

    def test_unset_model_pure_resolver_returns_default_model(self, monkeypatch):
        """Pure resolver: unset ``IMAGE_GEN_MODEL`` ⇒ model ==
        ``IMAGE_GEN_DEFAULT_MODEL`` (``"img-gen"``). The base/key path
        is the only fail-closed lane now."""
        monkeypatch.delenv("IMAGE_GEN_MODEL", raising=False)
        monkeypatch.setenv("OPENAI_BASE_URL", BASE)
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        cfg, err = _resolve_env()
        assert err is None
        assert cfg["model"] == IMAGE_GEN_DEFAULT_MODEL == "img-gen"

    def test_empty_string_model_falls_back_to_default(self, monkeypatch):
        """Bare ``IMAGE_GEN_MODEL=""`` is treated as unset (matches
        ``_clean_env_value`` semantics used by allowed_models)."""
        monkeypatch.setenv("IMAGE_GEN_MODEL", "")
        monkeypatch.setenv("OPENAI_BASE_URL", BASE)
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        cfg, err = _resolve_env()
        assert err is None
        assert cfg["model"] == IMAGE_GEN_DEFAULT_MODEL == "img-gen"

    def test_whitespace_only_model_falls_back_to_default(self, monkeypatch):
        """``IMAGE_GEN_MODEL="   "`` ⇒ unset ⇒ default purpose-id.
        Defense against operator typos / stray paste whitespace."""
        monkeypatch.setenv("IMAGE_GEN_MODEL", "   ")
        monkeypatch.setenv("OPENAI_BASE_URL", BASE)
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        cfg, err = _resolve_env()
        assert err is None
        assert cfg["model"] == IMAGE_GEN_DEFAULT_MODEL == "img-gen"

    def test_env_value_wins_over_default(self, monkeypatch):
        """Env-set ``IMAGE_GEN_MODEL`` overrides the purpose-id
        default — the same override-precedence contract as the
        ``model_vision`` → ``"vision"`` family."""
        monkeypatch.setenv("IMAGE_GEN_MODEL", "minimax-image-01")
        monkeypatch.setenv("OPENAI_BASE_URL", BASE)
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        cfg, err = _resolve_env()
        assert err is None
        assert cfg["model"] == "minimax-image-01"

    async def test_openai_fallbacks_used_when_knobs_unset(self, monkeypatch, store):
        """``IMAGE_GEN_BASE_URL`` / ``IMAGE_GEN_API_KEY`` fall back to
        their ``OPENAI_*`` siblings. Combined with the unset
        ``IMAGE_GEN_MODEL`` fallback, a fully-empty daemon
        environment is LIVE-by-default."""
        for key in ("IMAGE_GEN_MODEL", "IMAGE_GEN_BASE_URL", "IMAGE_GEN_API_KEY"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("OPENAI_BASE_URL", BASE)
        monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json=SUCCESS_BODY)

        tool, _ = _wire(monkeypatch, handler, store)
        monkeypatch.setattr(
            image_gen, "_fetch_image_bytes", lambda url: _fake_fetch_ok(url)
        )
        result = await tool.ainvoke({"prompt": "a red fox"})
        assert seen["auth"] == "Bearer openai-key"
        assert "image_gen_not_configured" not in result

    def test_never_reads_openai_model(self, monkeypatch):
        """Purpose-bound resolver (D4): OPENAI_MODEL must be irrelevant
        even when ``IMAGE_GEN_MODEL`` is unset (the default is the
        ``"img-gen"`` purpose-id, never ``OPENAI_MODEL``)."""
        monkeypatch.delenv("IMAGE_GEN_MODEL", raising=False)
        monkeypatch.setenv("OPENAI_MODEL", "agentic-should-be-ignored")
        monkeypatch.setenv("OPENAI_BASE_URL", BASE)
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        cfg, err = _resolve_env()
        assert err is None
        assert cfg["model"] == IMAGE_GEN_DEFAULT_MODEL == "img-gen"
        assert cfg["model"] != "agentic-should-be-ignored"

    async def test_unresolvable_base_fails_closed(self, monkeypatch):
        """Base/key are the ONLY fail-closed path now. Model fallback
        is LIVE-by-default; base/key unresolvable ⇒ envelope."""
        monkeypatch.delenv("IMAGE_GEN_BASE_URL", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.setenv("IMAGE_GEN_API_KEY", "k")
        # IMAGE_GEN_MODEL unset ⇒ default purpose-id "img-gen" applies,
        # but the missing base still trips image_gen_not_configured.
        (tool,) = create_image_gen_tools(manager=None, current_instance_id="x")
        result = await tool.ainvoke({"prompt": "x"})
        assert _err_code(result) == "image_gen_not_configured"

    async def test_unresolvable_api_key_fails_closed(self, monkeypatch):
        """Mirror of ``test_unresolvable_base_fails_closed`` for the
        api_key arm. Default ``IMAGE_GEN_DEFAULT_MODEL = "img-gen"``
        (purpose-id family default) must NOT loosen the api_key gate."""
        # Provide a base URL so the base-arm of the check passes — only
        # the key arm should fail-closed.
        monkeypatch.setenv("IMAGE_GEN_BASE_URL", "http://dummy.local:1234")
        # Both api keys explicitly absent.
        monkeypatch.delenv("IMAGE_GEN_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        # IMAGE_GEN_MODEL unset ⇒ default purpose-id "img-gen" applies,
        # but the missing api key still trips image_gen_not_configured.
        (tool,) = create_image_gen_tools(manager=None, current_instance_id="x")
        result = await tool.ainvoke({"prompt": "x"})
        assert _err_code(result) == "image_gen_not_configured"

    def test_timeout_clamp_truth_table(self, monkeypatch):
        monkeypatch.delenv("IMAGE_GEN_TIMEOUT_S", raising=False)
        assert _clamp_timeout(None) == DEFAULT_TIMEOUT_S == 240
        assert _clamp_timeout("abc") == 240
        assert _clamp_timeout(5) == 30          # floor
        assert _clamp_timeout(9999) == 300      # ceiling
        assert _clamp_timeout(77) == 77         # pass-through inside bounds


# ─── 3. Arg validation truth-tables (all pre-network) ──────────────────────────


class TestArgValidation:
    @pytest.mark.parametrize(
        "args,label",
        [
            ({"prompt": "fox", "width": 512.0, "height": 512}, "non-int width"),
            ({"prompt": "fox", "n": True}, "bool n"),
            ({"prompt": "fox", "seed": "7"}, "non-int seed"),
            ({"prompt": "fox", "prompt_optimizer": "yes"}, "non-bool optimizer"),
        ],
        ids=["non-int-width", "bool-n", "non-int-seed", "non-bool-optimizer"],
    )
    async def test_invalid_arg_types_rejected_direct_coroutine(
        self, env_ok, monkeypatch, store, args, label
    ):
        """Type violations bypassed via the raw coroutine (LangChain's pydantic
        schema coerces 512.0→512 / True→1 / "7"→7 / "yes"→True before the tool
        body sees them on the ainvoke path — the defensive validation layer
        still refuses them)."""
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(200, json=SUCCESS_BODY)

        tool, _ = _wire(monkeypatch, handler, store)
        result = await tool.coroutine(**args)
        assert _err_code(result) == "image_gen_invalid_args", label
        assert calls == [], f"{label}: validation must run BEFORE any network call"

    @pytest.mark.parametrize(
        "args,label",
        [
            ({"prompt": ""}, "empty prompt"),
            ({"prompt": "   "}, "whitespace prompt"),
            ({"prompt": "x" * (MAX_PROMPT_CHARS + 1)}, f">{MAX_PROMPT_CHARS} chars"),
            ({"prompt": "fox", "aspect_ratio": "16:9", "width": 512, "height": 512},
             "aspect + size mutex"),
            ({"prompt": "fox", "width": 512}, "width without height"),
            ({"prompt": "fox", "height": 512}, "height without width"),
            ({"prompt": "fox", "width": 511, "height": 512}, "width < 512"),
            ({"prompt": "fox", "width": 2049, "height": 2048}, "width > 2048"),
            ({"prompt": "fox", "width": 516, "height": 520}, "not divisible by 8"),
            ({"prompt": "fox", "n": 0}, "n < 1"),
            ({"prompt": "fox", "n": MAX_N + 1}, "n > 4"),
            ({"prompt": "fox", "seed": 2**63}, "seed over int64"),
        ],
        ids=["empty-prompt", "ws-prompt", "over-cap", "aspect-size-mutex",
             "width-only", "height-only", "width-low", "width-high", "not-div-8",
             "n-zero", "n-over", "seed-over"],
    )
    async def test_invalid_args_rejected_pre_network(self, env_ok, monkeypatch, store,
                                                     args, label):
        calls: list[httpx.Request] = []
        handler = lambda req: calls.append(req) or httpx.Response(200, json=SUCCESS_BODY)  # noqa: E731
        tool, _ = _wire(monkeypatch, handler, store)
        result = await tool.ainvoke(args)
        assert _err_code(result) == "image_gen_invalid_args", label
        assert calls == [], f"{label}: validation must run BEFORE any network call"

    @pytest.mark.parametrize(
        "args",
        [
            {"prompt": "x" * MAX_PROMPT_CHARS},                 # cap boundary OK
            {"prompt": "fox", "aspect_ratio": "16:9"},          # aspect alone
            {"prompt": "fox", "width": 512, "height": 2048},    # pair bounds
            {"prompt": "fox", "width": 2048, "height": 512},    # pair bounds swapped
            {"prompt": "fox", "n": MAX_N},                      # cap boundary
            {"prompt": "fox", "seed": 2**63 - 1},               # int64 boundary
            {"prompt": "fox", "seed": -(2**63)},                # int64 low boundary
        ],
        ids=["cap-boundary", "aspect-only", "pair-min-max", "pair-max-min", "n-cap",
             "seed-max", "seed-min"],
    )
    async def test_valid_args_reach_network(self, env_ok, monkeypatch, store, args):
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(200, json=SUCCESS_BODY)

        tool, _ = _wire(monkeypatch, handler, store)
        monkeypatch.setattr(
            image_gen, "_fetch_image_bytes",
            lambda url: _fake_fetch_ok(url, PNG_BYTES),
        )
        result = await tool.ainvoke(args)
        assert not result.startswith("Error:"), result
        assert len(calls) == 1


async def _fake_fetch_ok(url: str, body: bytes = PNG_BYTES,
                         content_type: str = "image/png"):
    return body, content_type


# ─── 4. Wire shape ─────────────────────────────────────────────────────────────


class TestWireShape:
    async def test_post_url_json_and_identity_headers(self, env_ok, monkeypatch, store):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["method"] = request.method
            seen["json"] = json.loads(request.content.decode("utf-8"))
            seen["auth"] = request.headers.get("authorization")
            seen["app"] = request.headers.get("x-proxy-app")
            seen["thinking"] = request.headers.get("x-proxy-interleaved-thinking")
            return httpx.Response(200, json=SUCCESS_BODY)

        tool, _ = _wire(monkeypatch, handler, store)
        monkeypatch.setattr(
            image_gen, "_fetch_image_bytes", lambda url: _fake_fetch_ok(url)
        )
        await tool.ainvoke({"prompt": "a red fox", "aspect_ratio": "16:9", "n": 2,
                            "seed": 42, "prompt_optimizer": True})
        assert seen["url"] == f"{BASE}/image_generation"
        assert seen["method"] == "POST"
        assert seen["json"] == {
            "model": "minimax-image-01",
            "prompt": "a red fox",
            "n": 2,
            "prompt_optimizer": True,
            "aspect_ratio": "16:9",
            "seed": 42,
        }
        assert seen["auth"] == "Bearer test-key"
        assert seen["app"] == "ensemble"
        assert seen["thinking"] == "True"

    async def test_size_mode_omits_aspect_ratio(self, env_ok, monkeypatch, store):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["json"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(200, json=SUCCESS_BODY)

        tool, _ = _wire(monkeypatch, handler, store)
        monkeypatch.setattr(
            image_gen, "_fetch_image_bytes", lambda url: _fake_fetch_ok(url)
        )
        await tool.ainvoke({"prompt": "fox", "width": 1024, "height": 768})
        assert seen["json"]["width"] == 1024
        assert seen["json"]["height"] == 768
        assert "aspect_ratio" not in seen["json"]

    async def test_single_attempt_no_retry(self, env_ok, monkeypatch, store):
        """Upstream is 10 RPM — the POST phase must fire EXACTLY once, even on
        error statuses."""
        count = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            count["n"] += 1
            return httpx.Response(500, json={"error": {"message": "boom"}})

        tool, _ = _wire(monkeypatch, handler, store)
        result = await tool.ainvoke({"prompt": "fox"})
        assert _err_code(result) == "image_gen_upstream_error"
        assert count["n"] == 1

    async def test_no_response_format_or_subject_reference_sent(
        self, env_ok, monkeypatch, store
    ):
        """AM7 + I2I out of scope: the request JSON must never carry these keys."""
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["json"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(200, json=SUCCESS_BODY)

        tool, _ = _wire(monkeypatch, handler, store)
        monkeypatch.setattr(
            image_gen, "_fetch_image_bytes", lambda url: _fake_fetch_ok(url)
        )
        await tool.ainvoke({"prompt": "fox"})
        assert "response_format" not in seen["json"]
        assert "subject_reference" not in seen["json"]


# ─── 5. Parse truth-table ──────────────────────────────────────────────────────


class TestParseTruthTable:
    async def test_success_single_object_data_string_counts(
        self, env_ok, monkeypatch, store
    ):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=SUCCESS_BODY)

        tool, _ = _wire(monkeypatch, handler, store)
        monkeypatch.setattr(
            image_gen, "_fetch_image_bytes", lambda url: _fake_fetch_ok(url)
        )
        result = await tool.ainvoke({"prompt": "fox"})
        payload = _ok_payload(result)
        assert len(payload["images"]) == 1
        assert payload["images"][0]["view_url"] == (
            f"/views/tmp-images/{payload['images'][0]['image_id']}"
        )
        # Marker grammar: one marker per image, id == image_id.
        marker = payload["markers"][0]
        image_id = payload["images"][0]["image_id"]
        assert marker == f"<!-- ens-img:image-gen:{image_id} -->"
        assert len(image_id) == 32 and all(c in "0123456789abcdef" for c in image_id)
        assert "VERBATIM" in payload["usage_note"]

    @pytest.mark.parametrize("app_code", [2013, 1002, 1004, 1008, 1026, 2049, 777])
    async def test_any_nonzero_status_code_fails_closed_no_whitelisting(
        self, env_ok, monkeypatch, store, app_code
    ):
        """AM2: fail closed on ANY non-zero base_resp.status_code — the
        historically observed codes are documentation, never a gate."""
        body = {
            "data": {"image_urls": [SUCCESS_URL]},
            "metadata": {"success_count": "1", "failed_count": "0"},
            "base_resp": {"status_code": app_code, "status_msg": "app error"},
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=body)

        tool, _ = _wire(monkeypatch, handler, store)
        result = await tool.ainvoke({"prompt": "fox"})
        assert _err_code(result) == "image_gen_upstream_error"
        assert str(app_code) in result
        assert store.saved == []

    async def test_proxy_502_parses_base_resp_from_verbatim_upstream_body(
        self, env_ok, monkeypatch, store
    ):
        """AM1: 502 body IS the upstream body verbatim — parse base_resp out
        of it."""
        body = {
            "base_resp": {"status_code": 2013, "status_msg": "prompt may contain sensitive content"}
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(502, json=body)

        tool, _ = _wire(monkeypatch, handler, store)
        result = await tool.ainvoke({"prompt": "fox"})
        assert _err_code(result) == "image_gen_upstream_error"
        assert "2013" in result
        assert "sensitive content" in result

    async def test_proxy_504_parses_openai_error_message(
        self, env_ok, monkeypatch, store
    ):
        """AM1: 504 is the proxy's self-generated OpenAI envelope — parse
        error.message."""
        body = {"error": {"type": "server_error",
                          "message": "upstream request failed: deadline"}}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(504, json=body)

        tool, _ = _wire(monkeypatch, handler, store)
        result = await tool.ainvoke({"prompt": "fox"})
        assert _err_code(result) == "image_gen_timeout"
        assert "upstream request failed: deadline" in result

    @pytest.mark.parametrize("status", [400, 401, 500, 503])
    async def test_other_non_200_reports_status(
        self, env_ok, monkeypatch, store, status
    ):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json={"error": {"message": "x"}})

        tool, _ = _wire(monkeypatch, handler, store)
        result = await tool.ainvoke({"prompt": "fox"})
        assert _err_code(result) == "image_gen_upstream_error"
        assert str(status) in result

    async def test_malformed_http_200_non_json_body(
        self, env_ok, monkeypatch, store
    ):
        """Review-note (d): explicit malformed-HTTP-200-body rows."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"<html>not json</html>")

        tool, _ = _wire(monkeypatch, handler, store)
        result = await tool.ainvoke({"prompt": "fox"})
        assert _err_code(result) == "image_gen_upstream_error"

    async def test_malformed_http_200_data_is_array(
        self, env_ok, monkeypatch, store
    ):
        """Review-note (d): `data` is a SINGLE OBJECT — an array fails closed."""
        body = {"data": [{"image_urls": [SUCCESS_URL]}],
                "base_resp": {"status_code": 0, "status_msg": "success"}}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=body)

        tool, _ = _wire(monkeypatch, handler, store)
        result = await tool.ainvoke({"prompt": "fox"})
        assert _err_code(result) == "image_gen_upstream_error"

    async def test_malformed_http_200_missing_base_resp(
        self, env_ok, monkeypatch, store
    ):
        """Review-note (d): success cannot be proven without base_resp."""
        body = {"data": {"image_urls": [SUCCESS_URL]}}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=body)

        tool, _ = _wire(monkeypatch, handler, store)
        result = await tool.ainvoke({"prompt": "fox"})
        assert _err_code(result) == "image_gen_upstream_error"

    async def test_string_counts_coerced_partial_success(
        self, env_ok, monkeypatch, store
    ):
        """String counts (live shape) + partial success: failed_count > 0 with
        status_code == 0 ⇒ proceed + disclose."""
        body = {
            "data": {"image_urls": [SUCCESS_URL]},
            "metadata": {"success_count": "1", "failed_count": "1"},
            "base_resp": {"status_code": 0, "status_msg": "partial"},
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=body)

        tool, _ = _wire(monkeypatch, handler, store)
        monkeypatch.setattr(
            image_gen, "_fetch_image_bytes", lambda url: _fake_fetch_ok(url)
        )
        result = await tool.ainvoke({"prompt": "fox", "n": 2})
        payload = _ok_payload(result)
        assert len(payload["images"]) == 1
        assert "1 failed generation" in payload["usage_note"]

    async def test_empty_success_ok_true_with_disclosure(
        self, env_ok, monkeypatch, store
    ):
        """AM5: status_code == 0 ∧ image_urls == [] ⇒ ok:true + disclosure."""
        body = {
            "data": {"image_urls": []},
            "metadata": {"success_count": "0", "failed_count": "0"},
            "base_resp": {"status_code": 0, "status_msg": "success"},
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=body)

        tool, _ = _wire(monkeypatch, handler, store)
        result = await tool.ainvoke({"prompt": "fox"})
        payload = _ok_payload(result)
        assert payload["images"] == []
        assert payload["markers"] == []
        assert "NO images" in payload["usage_note"]

    async def test_every_error_surface_is_single_line_envelope(
        self, env_ok, monkeypatch, store
    ):
        """AM9: multiline payloads trip startswith('Error:') consumers."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(502, json={"base_resp": {"status_code": 1002,
                                                           "status_msg": "rate limit"}})

        tool, _ = _wire(monkeypatch, handler, store)
        result = await tool.ainvoke({"prompt": "fox"})
        assert result.startswith("Error:")
        assert "\n" not in result


# ─── 6. OSS fetch + size guard + provenance ────────────────────────────────────


class TestFetchAndSave:
    async def test_fetch_and_save_provenance_feature_image_gen(
        self, env_ok, monkeypatch, store
    ):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=SUCCESS_BODY)

        tool, _ = _wire(monkeypatch, handler, store)
        monkeypatch.setattr(
            image_gen, "_fetch_image_bytes", lambda url: _fake_fetch_ok(url)
        )
        result = await tool.ainvoke({"prompt": "fox"})
        _ok_payload(result)
        assert len(store.saved) == 1
        save = store.saved[0]
        assert save["provenance"]["feature"] == "image-gen"
        assert save["provenance"]["source_agent"] == "worker"
        assert "page" not in save["provenance"]          # D9: page unused in v1
        assert save["retention_class"] == "normal"       # D9
        assert save["content_type"] == "image/png"
        assert len(save["content_bytes"]) == len(PNG_BYTES)

    async def test_content_type_inference_chain(self, env_ok, monkeypatch, store):
        """AM8 chain exercised through the REAL fetch path: response
        Content-Type header → URL extension sniff → default image/jpeg."""
        urls = [
            "https://oss.test/with-header.png",     # (a) specific header wins
            "https://oss.test/generic.webp",        # (b) octet-stream → sniff
            "https://oss.test/no-extension",        # (c) nothing → default jpeg
        ]
        body = {"data": {"image_urls": urls},
                "metadata": {}, "base_resp": {"status_code": 0}}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "proxy.test":
                return httpx.Response(200, json=body)
            path = request.url.path
            if path.endswith("with-header.png"):
                return httpx.Response(
                    200, headers={"content-type": "image/png"}, content=PNG_BYTES)
            if path.endswith("generic.webp"):
                # "unknown binary" header — the chain must fall through to
                # the extension sniff.
                return httpx.Response(
                    200,
                    headers={"content-type": "application/octet-stream"},
                    content=PNG_BYTES,
                )
            return httpx.Response(200, content=PNG_BYTES)  # no content-type at all

        tool, _ = _wire(monkeypatch, handler, store)
        result = await tool.ainvoke({"prompt": "fox", "n": 3})
        _ok_payload(result)
        assert [s["content_type"] for s in store.saved] == [
            "image/png",     # (a) header wins
            "image/webp",    # (b) octet-stream is not specific → extension sniff
            "image/jpeg",    # (c) no header, no extension → default
        ]

    def test_specific_header_used_verbatim(self):
        """A specific (non-octet-stream) Content-Type header wins over the
        extension."""
        assert image_gen._content_type_for("image/png", "https://x/a.webp") == "image/png"
        assert image_gen._content_type_for("application/octet-stream", "https://x/a.gif") == "image/gif"
        assert image_gen._content_type_for(None, "https://x/noext") == "image/jpeg"
        assert image_gen._content_type_for("image/png; charset=binary", "https://x/a.png") == "image/png"

    async def test_first_fetch_failure_skips_does_not_abort_batch(
        self, env_ok, monkeypatch, store
    ):
        body = {
            "data": {"image_urls": [
                "https://oss.test/broken.png", "https://oss.test/ok.png",
            ]},
            "metadata": {"success_count": "2", "failed_count": "0"},
            "base_resp": {"status_code": 0},
        }

        async def mixed_fetch(url):
            if "broken" in url:
                return "fetch HTTP 403 for image URL"
            return PNG_BYTES, "image/png"

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=body)

        tool, _ = _wire(monkeypatch, handler, store)
        monkeypatch.setattr(image_gen, "_fetch_image_bytes", mixed_fetch)
        result = await tool.ainvoke({"prompt": "fox", "n": 2})
        payload = _ok_payload(result)
        assert len(payload["images"]) == 1
        assert len(payload["markers"]) == 1
        assert "image[0] skipped" in payload["usage_note"]

    async def test_oversized_content_length_skips(self, env_ok, monkeypatch, store):
        """Size sanity ≤ MAX_IMAGE_BYTES per image (Content-Length pre-check)."""
        url = "https://oss.test/huge.png"
        body = {"data": {"image_urls": [url]},
                "metadata": {}, "base_resp": {"status_code": 0}}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "oss.test":
                return httpx.Response(
                    200,
                    headers={
                        "content-length": str(MAX_IMAGE_BYTES + 1),
                        "content-type": "image/png",
                    },
                    content=b"")
            return httpx.Response(200, json=body)

        tool, spy = _wire(monkeypatch, handler, store)
        result = await tool.ainvoke({"prompt": "fox"})
        assert _err_code(result) == "image_gen_fetch_failed"
        assert "size cap" in result
        assert store.saved == []

    async def test_streamed_overrun_skips(self, env_ok, monkeypatch, store):
        """A server lying about Content-Length cannot push past the cap — the
        streamed running count trips it."""
        url = "https://oss.test/liar.png"
        body = {"data": {"image_urls": [url]},
                "metadata": {}, "base_resp": {"status_code": 0}}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "oss.test":
                return httpx.Response(
                    200,
                    headers={"content-length": "10", "content-type": "image/png"},
                    content=b"\x89" * (MAX_IMAGE_BYTES + 1))
            return httpx.Response(200, json=body)

        tool, _ = _wire(monkeypatch, handler, store)
        result = await tool.ainvoke({"prompt": "fox"})
        assert _err_code(result) == "image_gen_fetch_failed"
        assert "size cap" in result


# ─── 7. Client timeout derives from the knob ───────────────────────────────────


class TestTimeoutDerivation:
    async def test_client_timeout_and_waitfor_derive_from_knob(
        self, env_ok, monkeypatch, store
    ):
        """httpx's default 5s read timeout would abort a live ~17s generation —
        the client httpx.Timeout AND the wait_for cap must derive from
        IMAGE_GEN_TIMEOUT_S."""
        env_ok.setenv("IMAGE_GEN_TIMEOUT_S", "77")
        captured: dict = {}
        real_async_client = httpx.AsyncClient

        def spy_async_client(*args, **kwargs):
            captured.setdefault("timeouts", []).append(kwargs.get("timeout"))
            return real_async_client(*args, **kwargs)

        monkeypatch.setattr(image_gen.httpx, "AsyncClient", spy_async_client)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=SUCCESS_BODY)

        tool, _ = _wire(monkeypatch, handler, store)
        monkeypatch.setattr(
            image_gen, "_fetch_image_bytes", lambda url: _fake_fetch_ok(url)
        )
        await tool.ainvoke({"prompt": "fox"})

        post_client_timeout = captured["timeouts"][0]
        assert isinstance(post_client_timeout, httpx.Timeout)
        assert post_client_timeout.read == 77
        assert post_client_timeout.connect == 10.0


# ─── 8. AM6 — POST-phase cap vs fetch-phase caps (hanging transports) ─────────


class TestAm6CapsPlacement:
    async def test_hanging_post_trips_post_cap_not_fetch_cap(
        self, env_ok, monkeypatch, store
    ):
        """The 240s-class cap wraps POST + body-read + parse ONLY (AM6). With
        the clamp collapsed to ~0 the hanging POST trips the POST cap in
        bounded time — the fetch phase is never reached."""
        # Collapse every clamp path to ~0s: raw unset → DEFAULT (patched), and
        # explicit values → min/max clamp (patched).
        monkeypatch.setattr(image_gen, "DEFAULT_TIMEOUT_S", 0)
        monkeypatch.setattr(image_gen, "MIN_TIMEOUT_S", 0)
        monkeypatch.setattr(image_gen, "MAX_TIMEOUT_S", 0)

        async def hanging_handler(request: httpx.Request) -> httpx.Response:
            # ASYNC hang: the loop stays live so asyncio.wait_for can fire.
            await asyncio.sleep(5.0)
            return httpx.Response(200, json=SUCCESS_BODY)

        tool, _ = _wire(monkeypatch, hanging_handler, store)
        started = time.monotonic()
        result = await tool.ainvoke({"prompt": "fox"})
        elapsed = time.monotonic() - started
        assert _err_code(result) == "image_gen_timeout"
        assert elapsed < 4.0, f"POST cap must trip promptly, took {elapsed:.1f}s"
        assert store.saved == []

    async def test_hanging_fetch_trips_fetch_cap_per_image(
        self, env_ok, monkeypatch, store
    ):
        """Each OSS fetch sits OUTSIDE the POST cap with its own 30s
        wait_for — a hanging fetch is skipped with disclosure, and the
        POST-phase cap never fires."""
        monkeypatch.setattr(image_gen, "PER_IMAGE_FETCH_TIMEOUT_S", 0.2)

        async def handler(request: httpx.Request) -> httpx.Response:
            # The FETCH transport hangs (async — the loop stays live so the
            # per-image wait_for can fire); the POST phase returns instantly.
            # NOTE: the wait_for cap lives INSIDE _fetch_image_bytes, so the
            # real fetch path must stay in place — only the transport hangs.
            if request.url.host == "oss.test":
                await asyncio.sleep(5.0)
            return httpx.Response(200, json=SUCCESS_BODY)

        tool, _ = _wire(monkeypatch, handler, store)
        started = time.monotonic()
        result = await tool.ainvoke({"prompt": "fox"})
        elapsed = time.monotonic() - started
        assert _err_code(result) == "image_gen_fetch_failed"
        assert "timed out after 0.2s" in result
        assert elapsed < 3.0, f"fetch cap must bound the phase, took {elapsed:.1f}s"


# ─── 9. Registry + allowlist resolution ────────────────────────────────────────


CONSUMER_AGENTS = ["ari", "leader", "developer[v2]", "reviewer", "tester", "worker"]
EXCLUDED_AGENTS = ["critic", "sketcher", "designer", "jober"]


class TestRegistryAndAllowlist:
    def test_category_module_wired(self):
        assert CATEGORY_MODULES.get("image-gen") == "daemon.tools.image_gen"

    def test_known_tool_names_contains_generate_image(self):
        assert "generate_image" in KNOWN_TOOL_NAMES

    def test_source_discovery_finds_generate_image_in_image_gen(self):
        from daemon.tools._tool_registry import discover_source_only_tool_names

        assert "generate_image" in discover_source_only_tool_names()

    def test_resolve_tool_filter_grants_category(self):
        from daemon.tools.instance import resolve_tool_filter

        resolved = resolve_tool_filter(
            allow=["image-gen"],
            deny=None,
            tool_categories={"image-gen": ["generate_image"]},
        )
        assert "generate_image" in resolved

    @pytest.mark.parametrize("agent", CONSUMER_AGENTS)
    def test_consumer_agent_allowlists(self, agent):
        meta = json.loads(
            (Path(__file__).resolve().parents[3] / "agents" / agent / "meta.json").read_text()
        )
        allow = meta["tools"]["allow"]
        assert "image-gen" in allow, f"{agent} must carry the image-gen token"
        # adjacency (plan AM12: appended immediately ADJACENT to "image")
        assert allow[allow.index("image") + 1] == "image-gen"

    @pytest.mark.parametrize("agent", CONSUMER_AGENTS)
    def test_consumer_meta_json_parses(self, agent):
        meta = json.loads(
            (Path(__file__).resolve().parents[3] / "agents" / agent / "meta.json").read_text()
        )
        assert isinstance(meta, dict) and "tools" in meta

    @pytest.mark.parametrize("agent", EXCLUDED_AGENTS)
    def test_excluded_agent_not_granted(self, agent):
        meta = json.loads(
            (Path(__file__).resolve().parents[3] / "agents" / agent / "meta.json").read_text()
        )
        allow = meta["tools"].get("allow") or []
        assert "image-gen" not in allow, (
            f"{agent} must NOT carry the image-gen token (generation-lane / "
            "write-leak isolation)"
        )

    def test_critic_allowlist_shape_unchanged(self):
        meta = json.loads(
            (Path(__file__).resolve().parents[3] / "agents" / "critic" / "meta.json").read_text()
        )
        assert meta["tools"]["allow"] == ["read_file", "image", "design"]
        assert "image_save" in (meta["tools"].get("deny") or [])


# ─── 10. review-hardening (council NEEDS-FIXES Findings 2 / 3 / 4) ────────────


def _wire_no_ssrf_bypass(monkeypatch, handler, store):
    """Wire the transport seam + fake store WITHOUT the auto-SSRF bypass.

    The default ``_wire`` installs a permissive ``_ensure_public_url``
    shim so existing tests do not have to worry about the SSRF guard on
    their fake ``*.test`` URLs. Tests that want to EXERCISE the guard
    (``TestReviewHardeningSSRFGuard``) use this helper instead — the
    real guard stays in place and ``socket.getaddrinfo`` controls the
    validation outcome via the test-local monkeypatch.
    """
    spy = _TransportSpy(handler)
    monkeypatch.setattr(image_gen, "_TRANSPORT_FACTORY", lambda: spy.as_transport())
    tools = create_image_gen_tools(_fake_manager(store), "inst-1")
    assert len(tools) == 1
    return tools[0], spy


class TestReviewHardeningSSRFGuard:
    """Finding 2 — public-URL defense-in-depth on the OSS fetch.

    ``_fetch_image_bytes`` MUST refuse a non-public / loopback / private
    target BEFORE any network I/O. Mirrors the guard at
    ``daemon/tools/image_tools.py:93-143`` that the fetch path did not
    previously have. Each returned address is checked against the standard
    non-public set; ``follow_redirects=False`` is set on the request so
    redirects cannot bypass the guard.
    """

    async def test_loopback_target_refused_before_fetch(
        self, env_ok, monkeypatch, store
    ):
        # The URL itself is benign (http://oss.example/...) but
        # getaddrinfo returns 127.0.0.1 — the guard must refuse.
        target_url = "http://oss-loopback.invalid/img/a.png"
        body = {"data": {"image_urls": [target_url]},
                "metadata": {}, "base_resp": {"status_code": 0}}

        def _fake_getaddrinfo(host, port, *args, **kwargs):
            # Return loopback IPv4 for ANY host the guard probes.
            return [(2, 1, 6, "", ("127.0.0.1", 0))]

        oss_calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "proxy.test":
                return httpx.Response(200, json=body)
            # Track any OSS-stage fetch attempt — must be ZERO.
            oss_calls.append(str(request.url))
            return httpx.Response(
                200,
                headers={"content-type": "image/png"},
                content=b"\x89PNG\r\n\x1a\n" + b"0" * 64,
            )

        with monkeypatch.context() as m:
            m.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
            # Use the wiring helper that does NOT install the
            # auto-SSRF-bypass — exercise the real guard.
            tool, _ = _wire_no_ssrf_bypass(m, handler, store)

            result = await tool.ainvoke({"prompt": "fox"})

        # The fetch phase never touched the OSS test transport.
        assert oss_calls == [], (
            f"SSRF guard failed — loopback target reached the OSS fetch: "
            f"{oss_calls}"
        )
        # The envelope is a single-image fetch failure with the guard's
        # refusal message, surfaced as ``image_gen_fetch_failed``.
        assert _err_code(result) == "image_gen_fetch_failed"
        assert "refused" in result
        # The SKIP-disclosure text mentions the per-image reason verbatim.
        assert "non-public IP 127.0.0.1" in result

    async def test_private_rfc1918_target_refused(self, env_ok, monkeypatch, store):
        # 10.0.0.0/8 (RFC1918) is private.
        target_url = "http://rfc1918.invalid/img/a.png"
        body = {"data": {"image_urls": [target_url]},
                "metadata": {}, "base_resp": {"status_code": 0}}

        def _fake_getaddrinfo(host, port, *args, **kwargs):
            return [(2, 1, 6, "", ("10.0.0.1", 0))]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=body)

        with monkeypatch.context() as m:
            m.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
            tool, _ = _wire_no_ssrf_bypass(m, handler, store)

            result = await tool.ainvoke({"prompt": "fox"})

        assert _err_code(result) == "image_gen_fetch_failed"
        assert "non-public IP 10.0.0.1" in result

    async def test_unresolvable_hostname_refused(self, env_ok, monkeypatch, store):
        # The guard fails closed even when getaddrinfo raises — a typo'd
        # URL or a DNS outage must not slip through to a fetch attempt.
        target_url = "http://nope.invalid/img/a.png"
        body = {"data": {"image_urls": [target_url]},
                "metadata": {}, "base_resp": {"status_code": 0}}

        def _fake_getaddrinfo(host, port, *args, **kwargs):
            raise socket.gaierror(-2, "Name or service not known")

        oss_calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host != "proxy.test":
                oss_calls.append(str(request.url))
            return httpx.Response(200, json=body)

        with monkeypatch.context() as m:
            m.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
            tool, _ = _wire_no_ssrf_bypass(m, handler, store)

            result = await tool.ainvoke({"prompt": "fox"})

        assert oss_calls == []
        assert _err_code(result) == "image_gen_fetch_failed"
        assert "Cannot resolve hostname" in result

    async def test_public_host_accepted_end_to_end(
        self, env_ok, monkeypatch, store
    ):
        # ACCEPT-PATH coverage for the REAL SSRF guard (council minor #2):
        # the three refusal tests above exercise the REJECT branches; this
        # test proves the ACCEPT path through the real ``_ensure_public_url``
        # (i.e. WITHOUT the ``_wire`` permissive shim) by feeding a public
        # IP through ``getaddrinfo``. The image must deliver end-to-end:
        # guard accepts → ``_fetch_image_bytes`` runs → save succeeds.
        target_url = "http://oss-public.example/img/a.png"
        body = {
            "data": {"image_urls": [target_url]},
            "metadata": {"success_count": "1", "failed_count": "0"},
            "base_resp": {"status_code": 0, "status_msg": "success"},
        }
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 64

        # 8.8.8.8 is Google's public DNS resolver — a real-public address.
        def _fake_getaddrinfo(host, port, *args, **kwargs):
            return [(2, 1, 6, "", ("8.8.8.8", 0))]

        oss_calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "proxy.test":
                return httpx.Response(200, json=body)
            # OSS stage was actually reached — proves the guard accepted.
            oss_calls.append(str(request.url))
            return httpx.Response(
                200,
                headers={"content-type": "image/png"},
                content=png,
            )

        with monkeypatch.context() as m:
            m.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
            # Use the bypass-FREE wiring — the real guard decides.
            tool, _ = _wire_no_ssrf_bypass(m, handler, store)

            result = await tool.ainvoke({"prompt": "fox"})

        # The OSS fetch was reached exactly once (no guard refusal).
        assert len(oss_calls) == 1
        assert oss_calls[0].endswith("/img/a.png")
        # End-to-end payload: ok=true, one image saved, one marker emitted.
        payload = _ok_payload(result)
        assert len(payload["images"]) == 1
        assert len(payload["markers"]) == 1
        assert payload["images"][0]["size_bytes"] == len(png)
        # Provenance stamps the feature correctly.
        assert store.saved and store.saved[0]["provenance"]["feature"] == "image-gen"


class TestReviewHardeningPostBodyReadCap:
    """Finding 3 — POST-phase body-read cap (``POST_BODY_READ_CAP_BYTES``).

    Mirrors the dual-enforcement posture of the OSS fetch path
    (``MAX_IMAGE_BYTES`` pre-check + streamed running count). A hostile /
    misconfigured upstream must NOT be able to pin the tool at the
    POST-cap floor while dumping unbounded bytes into memory. Oversize ⇒
    fail-closed ``image_gen_upstream_error`` envelope.
    """

    async def test_oversize_post_body_fails_closed_no_hang(
        self, env_ok, monkeypatch, store
    ):
        # Construct a body larger than ``POST_BODY_READ_CAP_BYTES`` (1 MiB)
        # so the bounded stream-read trips. The chunk is JSON-shaped so a
        # ``aread`` consumer that ignored the cap would still parse it.
        oversize_payload = b'{"data":{"image_urls":[]},' + (
            b'"' + b'x' * image_gen.POST_BODY_READ_CAP_BYTES + b'": "y"}'
        )
        assert len(oversize_payload) > image_gen.POST_BODY_READ_CAP_BYTES

        def handler(request: httpx.Request) -> httpx.Response:
            # Return the oversize body WITHOUT a Content-Length lie — the
            # guard's boundary is the actual streamed byte count, not the
            # advertised length.
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=oversize_payload,
            )

        tool, _ = _wire(monkeypatch, handler, store)
        started = time.monotonic()
        result = await tool.ainvoke({"prompt": "fox"})
        elapsed = time.monotonic() - started

        assert _err_code(result) == "image_gen_upstream_error"
        assert "exceeds the" in result and "read cap" in result
        # The cap path returns promptly (no Python parsing of the body
        # takes place — we bail mid-read).
        assert elapsed < 4.0, (
            f"oversize-POST path must bound; elapsed={elapsed:.1f}s"
        )
        assert store.saved == []

    async def test_under_cap_post_body_still_parses(
        self, env_ok, monkeypatch, store
    ):
        # Sanity: a body well under the cap still parses and saves.
        url = "https://oss.test/under-cap.png"
        body = {"data": {"image_urls": [url]},
                "metadata": {"success_count": "1", "failed_count": "0"},
                "base_resp": {"status_code": 0, "status_msg": "success"}}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "proxy.test":
                return httpx.Response(200, json=body)
            return httpx.Response(
                200,
                headers={"content-type": "image/png"},
                content=b"\x89PNG\r\n\x1a\n" + b"0" * 64,
            )

        tool, _ = _wire(monkeypatch, handler, store)
        result = await tool.ainvoke({"prompt": "fox"})
        _ok_payload(result)
        assert len(store.saved) == 1


class TestReviewHardeningFiveOhTwoEnvelopeFilter:
    """Finding 4 — the 502 envelope relays ONLY ``status_code``+``status_msg``.

    The 502 path parses the upstream ``base_resp`` (which may carry
    extras like ``request_id``, ``trace_id``, internal flags, etc.). Only
    the documented two fields are surfaced in the user-facing envelope —
    upstream-internal keys MUST NOT leak.
    """

    async def test_extra_upstream_base_resp_keys_absent_from_envelope(
        self, env_ok, monkeypatch, store
    ):
        # Upstream body carries base_resp with EXTRA keys; relay envelope
        # must contain ONLY ``status``, ``status_code``, ``status_msg``.
        # (Mandated keys + ``message`` + ``code`` from the envelope shape.)
        body = {
            "base_resp": {
                "status_code": 2013,
                "status_msg": "prompt may contain sensitive content",
                # Upstream-internal noise — must NOT appear in our envelope:
                "request_id": "req-abcdef123456",
                "trace_id": "trace-789xyz-very-internal",
                "internal_flag": True,
                "retry_after_s": 30,
                "extra": {"nested": "must_not_appear"},
            }
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(502, json=body)

        tool, _ = _wire(monkeypatch, handler, store)
        result = await tool.ainvoke({"prompt": "fox"})

        # Surface assertions (unchanged): code, status_code, status_msg
        assert _err_code(result) == "image_gen_upstream_error"
        # The two informational substrings are still present.
        assert "2013" in result
        assert "prompt may contain sensitive content" in result

        # Filter assertion (Finding 4): no upstream-internal key leaks.
        for forbidden in (
            "request_id", "trace_id", "internal_flag",
            "retry_after_s", "extra",
            "req-abcdef123456", "trace-789xyz-very-internal",
            "very-internal",
        ):
            assert forbidden not in result, (
                f"Finding-4 leak: upstream-internal key {forbidden!r} "
                f"surfaced in envelope: {result}"
            )

        # The envelope details MUST be ONLY the two mandated fields. Decode
        # the envelope and assert strict equality on ``details``.
        envelope = json.loads(result[len("Error: "):])
        assert envelope["details"] == {
            "status": 502,
            "status_code": 2013,
            "status_msg": "prompt may contain sensitive content",
        }

    async def test_http_200_nonzero_base_resp_filter_same_three_keys(
        self, env_ok, monkeypatch, store
    ):
        """Council minor #1 — adjacent-path coverage.

        The 200-OK + non-zero ``base_resp.status_code`` path (HTTP 200 with
        ``base_resp.status_code != 0`` ⇒ upstream reports an app-level
        failure under a successful envelope) ALSO relays the upstream
        dict. Same 3-key filter as the 502 path: ``{status, status_code,
        status_msg}`` ONLY; extras (``request_id`` / ``trace_id`` /
        internal flags) MUST NOT leak.
        """
        body = {
            "data": {"image_urls": ["https://oss.example/img/a.png"]},
            "metadata": {"success_count": "0", "failed_count": "1"},
            "base_resp": {
                "status_code": 1026,  # any non-zero AM2 code
                "status_msg": "internal upstream failure",
                # Same upstream-internal noise as the 502 case — must NOT
                # appear in the 200-OK + non-zero envelope either.
                "request_id": "req-abcdef123456",
                "trace_id": "trace-789xyz-very-internal",
                "internal_flag": True,
                "retry_after_s": 30,
                "extra": {"nested": "must_not_appear"},
            },
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=body)

        tool, _ = _wire(monkeypatch, handler, store)
        result = await tool.ainvoke({"prompt": "fox"})

        # Same envelope code as the 502 path.
        assert _err_code(result) == "image_gen_upstream_error"
        # The two informational substrings are present.
        assert "1026" in result
        assert "internal upstream failure" in result
        # Same filter assertion: no upstream-internal key leaks.
        for forbidden in (
            "request_id", "trace_id", "internal_flag",
            "retry_after_s", "extra",
            "req-abcdef123456", "trace-789xyz-very-internal",
            "very-internal",
        ):
            assert forbidden not in result, (
                f"Council-minor #1 leak: upstream-internal key {forbidden!r} "
                f"surfaced in 200-OK envelope: {result}"
            )
        # Strict equality on details with the 3-key filter (status=200 here).
        envelope = json.loads(result[len("Error: "):])
        assert envelope["details"] == {
            "status": 200,
            "status_code": 1026,
            "status_msg": "internal upstream failure",
        }

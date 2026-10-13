"""Comprehensive unit tests for OpenCodeClient.

Most tests mock `client._request()` (the internal async HTTP method).
The `TestRequestHeaders` and network-error cases intentionally patch the
underlying `httpx.AsyncClient.request` because they exercise `_request`
itself.

Covers:
- create_session: POST /session, parse SessionResponse.id
- send_prompt: POST /session/{id}/message, camelCase serialization (providerID, modelID)
- send_command: POST /session/{id}/command, camelCase serialization
- get_questions: GET /question, dual-parse (bare array then {data:[...]})
- answer_question: POST /question/{id}/reply, drops request_id from body
- abort_session: POST /session/{id}/abort, empty body {}
- resume_session: POST /session/{id}/message, hardcoded orchestrator/litellm/coding payload
- get_session_messages: GET /session/{id}/message?limit=N, newest-first messages
- _auth_header: Basic auth encoding (user:password base64)
- _request: headers (Content-Type, Accept, x-opencode-directory, Authorization)
- Error handling: non-2xx raises OpenCodeAPIError, network errors raise OpenCodeAPIError(0)
- Async context manager: __aenter__ returns client, __aexit__ calls aclose
"""

import base64

import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from daemon.opencode.client import (
    AnswerRequest,
    CommandRequest,
    ModelDetails,
    OpenCodeAPIError,
    OpenCodeClient,
    Part,
    PromptRequest,
    SessionResponse,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    """A fresh OpenCodeClient with test-friendly defaults."""
    return OpenCodeClient(
        working_dir="/test/project",
        base_url="http://127.0.0.1:4095",
        api_user="opencode",
        api_key="secret",
    )


@pytest.fixture
def no_auth_client():
    """Client with no credentials — Authorization header should be skipped."""
    return OpenCodeClient(
        working_dir="/test/project",
        api_user="",
        api_key="",
    )


# ─────────────────────────────────────────────────────────────────────────────
# TestClientInit
# ─────────────────────────────────────────────────────────────────────────────


class TestClientInit:
    def test_default_base_url(self):
        client = OpenCodeClient(working_dir="/x")
        assert client.base_url == "http://127.0.0.1:4095"

    def test_strips_trailing_slash_from_base_url(self):
        client = OpenCodeClient(working_dir="/x", base_url="http://localhost:4095/")
        assert client.base_url == "http://localhost:4095"

    def test_working_dir_stored(self):
        client = OpenCodeClient(working_dir="/my/project")
        assert client.working_dir == "/my/project"

    def test_api_credentials_stored(self):
        client = OpenCodeClient(working_dir="/x", api_user="u", api_key="p")
        assert client.api_user == "u"
        assert client.api_key == "p"


# ─────────────────────────────────────────────────────────────────────────────
# TestAuthHeader — Basic auth encoding
# ─────────────────────────────────────────────────────────────────────────────


class TestAuthHeader:
    def test_returns_basic_auth_token(self, client):
        auth = client._auth_header()
        assert auth is not None
        assert auth.startswith("Basic ")

    def test_encodes_user_colon_password(self, client):
        auth = client._auth_header()
        token_b64 = auth[len("Basic ") :]
        decoded = base64.b64decode(token_b64).decode()
        assert decoded == "opencode:secret"

    def test_returns_none_when_user_empty(self, no_auth_client):
        assert no_auth_client._auth_header() is None

    def test_returns_none_when_key_empty(self):
        client = OpenCodeClient(working_dir="/x", api_user="user", api_key="")
        assert client._auth_header() is None


# ─────────────────────────────────────────────────────────────────────────────
# TestRequestHeaders — headers passed to httpx
# ─────────────────────────────────────────────────────────────────────────────


class TestRequestHeaders:
    @pytest.mark.asyncio
    async def test_sends_content_type_and_accept_headers(self, client):
        """Every request carries Content-Type: application/json and Accept: application/json."""
        with patch.object(client._client, "request", new_callable=AsyncMock) as mock_req:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.content = b'{"id": "abc", "title": "test"}'
            mock_req.return_value = mock_response

            await client.create_session("test")

            _, kwargs = mock_req.call_args
            assert kwargs["headers"]["Content-Type"] == "application/json"
            assert kwargs["headers"]["Accept"] == "application/json"

    @pytest.mark.asyncio
    async def test_sends_x_opencode_directory_header(self, client):
        """Every request carries the working directory as x-opencode-directory."""
        with patch.object(client._client, "request", new_callable=AsyncMock) as mock_req:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.content = b'{"id": "abc", "title": "test"}'
            mock_req.return_value = mock_response

            await client.create_session("test")

            _, kwargs = mock_req.call_args
            assert kwargs["headers"]["x-opencode-directory"] == "/test/project"

    @pytest.mark.asyncio
    async def test_sends_authorization_header(self, client):
        """Requests include the Basic auth token when credentials are set."""
        with patch.object(client._client, "request", new_callable=AsyncMock) as mock_req:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.content = b'{"id": "abc", "title": "test"}'
            mock_req.return_value = mock_response

            await client.create_session("test")

            _, kwargs = mock_req.call_args
            assert "Authorization" in kwargs["headers"]
            assert kwargs["headers"]["Authorization"].startswith("Basic ")

    @pytest.mark.asyncio
    async def test_skips_authorization_when_no_credentials(self, no_auth_client):
        """No Authorization header when api_user or api_key is empty."""
        with patch.object(no_auth_client._client, "request", new_callable=AsyncMock) as mock_req:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.content = b'{"id": "abc", "title": "test"}'
            mock_req.return_value = mock_response

            await no_auth_client.create_session("test")

            _, kwargs = mock_req.call_args
            assert "Authorization" not in kwargs["headers"]


# ─────────────────────────────────────────────────────────────────────────────
# TestCreateSession
# ─────────────────────────────────────────────────────────────────────────────


class TestCreateSession:
    @pytest.mark.asyncio
    async def test_posts_to_session_endpoint(self, client):
        """POST /session with {"title": ...}."""
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = b'{"id": "sess-uuid", "title": "my session"}'

            await client.create_session("my session")

            mock_req.assert_awaited_once_with("POST", "/session", {"title": "my session"})

    @pytest.mark.asyncio
    async def test_returns_session_id_from_response(self, client):
        """Returns the parsed id field from SessionResponse."""
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = b'{"id": "sess-uuid", "title": "test"}'

            result = await client.create_session("test")

            assert result == "sess-uuid"

    @pytest.mark.asyncio
    async def test_raises_on_invalid_json(self, client):
        """Invalid JSON body raises a validation error when create_session parses SessionResponse.

        create_session uses _request directly (not _post_and_parse), so the
        error is a pydantic ValidationError from SessionResponse.model_validate_json,
        not an OpenCodeAPIError. The OpenCodeAPIError-on-invalid-JSON path is
        covered by test_invalid_json_in_response_raises below for _post_and_parse.
        """
        from pydantic import ValidationError

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = b"not valid json"

            with pytest.raises(ValidationError):
                await client.create_session("test")

    @pytest.mark.asyncio
    async def test_raises_on_non_2xx_response(self, client):
        """HTTP 404/500 etc. raises OpenCodeAPIError with the status code."""
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.side_effect = OpenCodeAPIError(404, "session not found")

            with pytest.raises(OpenCodeAPIError) as exc_info:
                await client.create_session("test")

            assert exc_info.value.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# TestSendPrompt — camelCase serialization (providerID, modelID)
# ─────────────────────────────────────────────────────────────────────────────


class TestSendPrompt:
    @pytest.mark.asyncio
    async def test_posts_to_session_message_endpoint(self, client):
        """POST /session/{id}/message."""
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = b'{"ok": true}'

            req = PromptRequest(parts=[Part(type="text", text="hello")])
            await client.send_prompt("sess-1", req)

            mock_req.assert_awaited_once()
            args = mock_req.call_args.args
            assert args[0] == "POST"
            assert args[1] == "/session/sess-1/message"

    @pytest.mark.asyncio
    async def test_sends_camelcase_provider_id(self, client):
        """Wire format uses camelCase "providerID" / "modelID" inside the nested model object."""
        captured_payload = None

        async def capture_request(method, path, payload=None):
            nonlocal captured_payload
            captured_payload = payload
            return b'{"ok": true}'

        with patch.object(client, "_request", side_effect=capture_request):
            req = PromptRequest(
                model=ModelDetails(provider_id="litellm", model_id="coding"),
                parts=[Part(type="text", text="hello")],
            )
            await client.send_prompt("sess-1", req)

        # The camelCase fields are nested inside the model object, not at the top level
        model_obj = captured_payload["model"]
        assert "providerID" in model_obj
        assert "modelID" in model_obj
        assert model_obj["providerID"] == "litellm"
        assert model_obj["modelID"] == "coding"

    @pytest.mark.asyncio
    async def test_sends_nested_model_object(self, client):
        """model field is serialized as a nested object, not flat."""
        captured_payload = None

        async def capture_request(method, path, payload=None):
            nonlocal captured_payload
            captured_payload = payload
            return b'{"ok": true}'

        with patch.object(client, "_request", side_effect=capture_request):
            req = PromptRequest(
                agent="orchestrator",
                model=ModelDetails(provider_id="anthropic", model_id="claude-3"),
                parts=[Part(type="text", text="do work")],
            )
            await client.send_prompt("sess-1", req)

        assert "model" in captured_payload
        assert isinstance(captured_payload["model"], dict)
        assert captured_payload["model"]["providerID"] == "anthropic"

    @pytest.mark.asyncio
    async def test_sends_parts_array(self, client):
        """parts list is included in the request body."""
        captured_payload = None

        async def capture_request(method, path, payload=None):
            nonlocal captured_payload
            captured_payload = payload
            return b'{"ok": true}'

        with patch.object(client, "_request", side_effect=capture_request):
            req = PromptRequest(
                parts=[
                    Part(type="text", text="first part"),
                    Part(type="step-finish", text="done"),
                ],
            )
            await client.send_prompt("sess-1", req)

        assert "parts" in captured_payload
        assert len(captured_payload["parts"]) == 2
        assert captured_payload["parts"][0]["type"] == "text"
        assert captured_payload["parts"][1]["type"] == "step-finish"


# ─────────────────────────────────────────────────────────────────────────────
# TestSendCommand — camelCase serialization
# ─────────────────────────────────────────────────────────────────────────────


class TestSendCommand:
    @pytest.mark.asyncio
    async def test_posts_to_session_command_endpoint(self, client):
        """POST /session/{id}/command."""
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = b'{"ok": true}'

            req = CommandRequest(command="/wait", arguments="")
            await client.send_command("sess-1", req)

            mock_req.assert_awaited_once()
            args = mock_req.call_args.args
            assert args[0] == "POST"
            assert args[1] == "/session/sess-1/command"

    @pytest.mark.asyncio
    async def test_sends_command_and_arguments(self, client):
        """command and arguments fields are serialized as camelCase."""
        captured_payload = None

        async def capture_request(method, path, payload=None):
            nonlocal captured_payload
            captured_payload = payload
            return b'{"ok": true}'

        with patch.object(client, "_request", side_effect=capture_request):
            req = CommandRequest(command="/status", arguments="")
            await client.send_command("sess-1", req)

        assert "command" in captured_payload
        assert captured_payload["command"] == "/status"

    @pytest.mark.asyncio
    async def test_sends_nested_model_object(self, client):
        """model field uses camelCase in wire format."""
        captured_payload = None

        async def capture_request(method, path, payload=None):
            nonlocal captured_payload
            captured_payload = payload
            return b'{"ok": true}'

        with patch.object(client, "_request", side_effect=capture_request):
            req = CommandRequest(
                model=ModelDetails(provider_id="litellm", model_id="coding"),
                command="/wait",
                arguments="",
            )
            await client.send_command("sess-1", req)

        assert "model" in captured_payload
        assert captured_payload["model"]["providerID"] == "litellm"
        assert captured_payload["model"]["modelID"] == "coding"


# ─────────────────────────────────────────────────────────────────────────────
# TestGetQuestions — dual-parse strategy
# ─────────────────────────────────────────────────────────────────────────────


class TestGetQuestions:
    @pytest.mark.asyncio
    async def test_gets_from_question_endpoint(self, client):
        """GET /question (no payload argument)."""
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = b"[]"

            await client.get_questions()

            mock_req.assert_awaited_once_with("GET", "/question")

    @pytest.mark.asyncio
    async def test_parses_bare_array(self, client):
        """First attempt: parse body as a bare JSON array."""
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = b'[{"id": "q1", "sessionID": "s1", "questions": []}]'

            result = await client.get_questions()

            assert len(result) == 1
            assert result[0].id == "q1"

    @pytest.mark.asyncio
    async def test_parses_data_envelope(self, client):
        """Second attempt: parse body as {"data": [...]} envelope."""
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = b'{"data": [{"id": "q2", "sessionID": "s2", "questions": []}]}'

            result = await client.get_questions()

            assert len(result) == 1
            assert result[0].id == "q2"

    @pytest.mark.asyncio
    async def test_returns_empty_list_for_empty_body(self, client):
        """Empty response body returns [] (matches Go behavior)."""
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = b""

            result = await client.get_questions()

            assert result == []

    @pytest.mark.asyncio
    async def test_raises_when_both_parses_fail(self, client):
        """Neither bare array nor {data:[...]} succeeds → OpenCodeAPIError."""
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = b'{"other": "format"}'

            with pytest.raises(OpenCodeAPIError) as exc_info:
                await client.get_questions()

            assert exc_info.value.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# TestAnswerQuestion — drops request_id from body, correct URL
# ─────────────────────────────────────────────────────────────────────────────


class TestAnswerQuestion:
    @pytest.mark.asyncio
    async def test_posts_to_question_reply_endpoint(self, client):
        """POST /question/{id}/reply using request_id from the AnswerRequest."""
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = b'{"ok": true}'

            req = AnswerRequest(request_id="req-abc", answers=[["A"]])
            await client.answer_question(req)

            mock_req.assert_awaited_once()
            args = mock_req.call_args.args
            assert args[0] == "POST"
            assert args[1] == "/question/req-abc/reply"

    @pytest.mark.asyncio
    async def test_drops_request_id_from_body(self, client):
        """request_id is consumed for the URL path, not included in the body."""
        captured_payload = None

        async def capture_request(method, path, payload=None):
            nonlocal captured_payload
            captured_payload = payload
            return b'{"ok": true}'

        with patch.object(client, "_request", side_effect=capture_request):
            req = AnswerRequest(request_id="req-xyz", answers=[["option 1"]])
            await client.answer_question(req)

        assert captured_payload is not None
        # request_id must NOT appear in the body
        assert "requestID" not in captured_payload
        assert "request_id" not in captured_payload
        # answers must appear
        assert "answers" in captured_payload
        assert captured_payload["answers"] == [["option 1"]]


# ─────────────────────────────────────────────────────────────────────────────
# TestAbortSession
# ─────────────────────────────────────────────────────────────────────────────


class TestAbortSession:
    @pytest.mark.asyncio
    async def test_posts_to_session_abort_endpoint(self, client):
        """POST /session/{id}/abort with empty body {}."""
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = b'{"ok": true}'

            await client.abort_session("sess-1")

            mock_req.assert_awaited_once_with("POST", "/session/sess-1/abort", {})

    @pytest.mark.asyncio
    async def test_sends_empty_dict_not_none(self, client):
        """Body is explicitly {} so the server receives Content-Length even when empty."""
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = b'{"ok": true}'

            await client.abort_session("sess-1")

            # Verify the third argument is {} not None
            args = mock_req.call_args.args
            assert args[2] == {}


# ─────────────────────────────────────────────────────────────────────────────
# TestResumeSession — hardcoded orchestrator/litellm/coding payload
# ─────────────────────────────────────────────────────────────────────────────


class TestResumeSession:
    @pytest.mark.asyncio
    async def test_posts_to_session_message_endpoint(self, client):
        """Resume sends a POST to /session/{id}/message."""
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = b'{"ok": true}'

            await client.resume_session("sess-1")

            mock_req.assert_awaited_once()
            args = mock_req.call_args.args
            assert args[0] == "POST"
            assert args[1] == "/session/sess-1/message"

    @pytest.mark.asyncio
    async def test_uses_hardcoded_agent_orchestrator(self, client):
        """Agent is locked to "orchestrator" (hardcoded, matches Go binary)."""
        captured_payload = None

        async def capture_request(method, path, payload=None):
            nonlocal captured_payload
            captured_payload = payload
            return b'{"ok": true}'

        with patch.object(client, "_request", side_effect=capture_request):
            await client.resume_session("sess-1")

        assert captured_payload["agent"] == "orchestrator"

    @pytest.mark.asyncio
    async def test_uses_hardcoded_model_litellm_coding(self, client):
        """Model is hardcoded to litellm/coding (hardcoded, matches Go binary)."""
        captured_payload = None

        async def capture_request(method, path, payload=None):
            nonlocal captured_payload
            captured_payload = payload
            return b'{"ok": true}'

        with patch.object(client, "_request", side_effect=capture_request):
            await client.resume_session("sess-1")

        assert captured_payload["model"]["providerID"] == "litellm"
        assert captured_payload["model"]["modelID"] == "coding"

    @pytest.mark.asyncio
    async def test_sends_hardcoded_resume_text(self, client):
        """Single text part with "resume" (hardcoded, matches Go binary)."""
        captured_payload = None

        async def capture_request(method, path, payload=None):
            nonlocal captured_payload
            captured_payload = payload
            return b'{"ok": true}'

        with patch.object(client, "_request", side_effect=capture_request):
            await client.resume_session("sess-1")

        assert len(captured_payload["parts"]) == 1
        assert captured_payload["parts"][0]["type"] == "text"
        assert captured_payload["parts"][0]["text"] == "resume"


# ─────────────────────────────────────────────────────────────────────────────
# TestGetSessionMessages — query string formatting
# ─────────────────────────────────────────────────────────────────────────────


class TestGetSessionMessages:
    @pytest.mark.asyncio
    async def test_gets_from_session_message_endpoint(self, client):
        """GET /session/{id}/message with ?limit=N query string."""
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = b"[]"

            await client.get_session_messages("sess-1")

            mock_req.assert_awaited_once()
            args = mock_req.call_args.args
            assert args[0] == "GET"
            assert "/session/sess-1/message" in args[1]
            assert "limit=1" in args[1]  # default limit

    @pytest.mark.asyncio
    async def test_respects_custom_limit(self, client):
        """limit=N query parameter reflects the limit argument."""
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = b"[]"

            await client.get_session_messages("sess-1", limit=5)

            args = mock_req.call_args.args
            assert "limit=5" in args[1]

    @pytest.mark.asyncio
    async def test_returns_parsed_json_array(self, client):
        """Returns the parsed JSON array from the response body."""
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = b'[{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]'

            result = await client.get_session_messages("sess-1")

            assert len(result) == 2
            assert result[0]["role"] == "user"

    @pytest.mark.asyncio
    async def test_returns_empty_list_for_empty_body(self, client):
        """Empty response body returns [] (matches Go behavior)."""
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = b""

            result = await client.get_session_messages("sess-1")

            assert result == []

    @pytest.mark.asyncio
    async def test_raises_when_response_not_array(self, client):
        """Non-array JSON response raises OpenCodeAPIError."""
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = b'{"error": "not a list"}'

            with pytest.raises(OpenCodeAPIError) as exc_info:
                await client.get_session_messages("sess-1")

            assert exc_info.value.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# TestErrorHandling — HTTP errors and network failures
# ─────────────────────────────────────────────────────────────────────────────


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_non_2xx_raises_open_code_api_error(self, client):
        """HTTP 404/500/etc. raised by _request propagates as OpenCodeAPIError."""
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.side_effect = OpenCodeAPIError(500, "Internal Server Error")

            with pytest.raises(OpenCodeAPIError) as exc_info:
                await client.create_session("test")

            assert exc_info.value.status_code == 500
            assert "Internal Server Error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_network_error_raises_with_status_zero(self, client):
        """httpx.HTTPError (DNS, connection refused, timeout) → OpenCodeAPIError(0)."""
        with patch.object(client._client, "request", new_callable=AsyncMock) as mock_req:
            mock_req.side_effect = httpx.TimeoutException("connection timed out")

            with pytest.raises(OpenCodeAPIError) as exc_info:
                await client.create_session("test")

            # status_code 0 signals a network-level failure (no HTTP response received)
            assert exc_info.value.status_code == 0
            assert "connection timed out" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_connection_error_raises_with_status_zero(self, client):
        """httpx.ConnectError → OpenCodeAPIError(0)."""
        with patch.object(client._client, "request", new_callable=AsyncMock) as mock_req:
            mock_req.side_effect = httpx.ConnectError("connection refused")

            with pytest.raises(OpenCodeAPIError) as exc_info:
                await client.create_session("test")

            assert exc_info.value.status_code == 0

    @pytest.mark.asyncio
    async def test_invalid_json_in_response_raises(self, client):
        """_post_and_parse raises OpenCodeAPIError for malformed JSON in response body."""
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = b"not valid json{"

            with pytest.raises(OpenCodeAPIError) as exc_info:
                await client.send_prompt("sess-1", PromptRequest())

            assert exc_info.value.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# TestAsyncContextManager
# ─────────────────────────────────────────────────────────────────────────────


class TestAsyncContextManager:
    @pytest.mark.asyncio
    async def test_aenter_returns_client(self, client):
        """__aenter__ returns self so 'async with' works."""
        async with client as c:
            assert c is client

    @pytest.mark.asyncio
    async def test_aexit_calls_aclose(self, client):
        """__aexit__ calls aclose() on the underlying httpx client."""
        with patch.object(client._client, "aclose", new_callable=AsyncMock) as mock_aclose:
            async with client:
                pass

            mock_aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_aclose_is_idempotent(self, client):
        """Calling aclose multiple times is safe (httpx client is idempotent)."""
        with patch.object(client._client, "aclose", new_callable=AsyncMock) as mock_aclose:
            await client.aclose()
            await client.aclose()

            assert mock_aclose.call_count == 2

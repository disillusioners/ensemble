"""OpenCode HTTP API client + Pydantic request/response DTOs.

Ported from:

- ``.inspiration-projects/opencode_skill_src/internal/api/client.go`` (HTTP
  transport, 190 lines)
- ``.inspiration-projects/opencode_skill_src/internal/api/types.go``
  (SessionResponse, Question, Option)
- ``.inspiration-projects/opencode_skill_src/internal/types/types.go``
  (PromptRequest, CommandRequest, AnswerRequest, ModelDetails, Part)

The HTTP layer is **async** (httpx.AsyncClient) so it composes with the
rest of the ensemble daemon. Pydantic models use camelCase aliases for
*both* serialization (out to OpenCode) and acceptance (in from the
ensemble routing layer), matching the Go ``json:"providerID"`` tags.
"""

from __future__ import annotations

import base64
import logging
from typing import Any, ClassVar

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .constants import (
    DEFAULT_API_KEY,
    DEFAULT_API_USER,
    DEFAULT_MODEL_ID,
    DEFAULT_MODEL_PROVIDER_ID,
    OPENCODE_HTTP_TIMEOUT_S,
    OPENCODE_URL,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic models — wire format uses camelCase
# ─────────────────────────────────────────────────────────────────────────────
#
# Each model uses ``model_config = ConfigDict(populate_by_name=True)`` plus
# ``Field(alias="providerID", serialization_alias="providerID")`` so the
# API wire format is camelCase (Go style) while internal Python code may
# use either snake_case or camelCase. ``model_dump(by_alias=True)`` is
# used at the HTTP boundary to emit camelCase.


class ModelDetails(BaseModel):
    """``{providerID, modelID}`` — Go: types.ModelDetails (types.go:24-27).

    Used as the ``model`` field in both ``PromptRequest`` and
    ``CommandRequest``. Field names match the Go JSON tags exactly.
    """

    model_config = ConfigDict(populate_by_name=True)

    provider_id: str = Field(
        default=DEFAULT_MODEL_PROVIDER_ID,
        alias="providerID",
        serialization_alias="providerID",
    )
    model_id: str = Field(
        default=DEFAULT_MODEL_ID,
        alias="modelID",
        serialization_alias="modelID",
    )


class Part(BaseModel):
    """A single prompt part. Go: types.Part (types.go:29-32).

    Carries ``type`` (e.g. ``"text"``, ``"step-finish"``) and a ``text``
    payload. The OpenCode API tolerates additional fields (e.g. ``reason``,
    ``error``) so we accept arbitrary extras via ``model_config``.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    type: str
    text: str = ""


class PromptRequest(BaseModel):
    """Go: types.PromptRequest (types.go:5-9).

    Body for ``POST /session/{id}/message``. The ``model`` field is a
    nested ``ModelDetails`` — Pydantic handles the camelCase recursion
    automatically because ``ModelDetails`` itself declares the aliases.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    agent: str = DEFAULT_MODEL_PROVIDER_ID  # placeholder; overridden at call site
    model: ModelDetails = Field(default_factory=ModelDetails)
    parts: list[Part] = Field(default_factory=list)


class CommandRequest(BaseModel):
    """Go: types.CommandRequest (types.go:11-17).

    Body for ``POST /session/{id}/command``. Extends PromptRequest with
    a ``command`` name and ``arguments`` string.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    agent: str = DEFAULT_MODEL_PROVIDER_ID
    model: ModelDetails = Field(default_factory=ModelDetails)
    command: str = ""
    arguments: str = ""
    parts: list[Part] = Field(default_factory=list)


class AnswerRequest(BaseModel):
    """Go: types.AnswerRequest (types.go:19-22).

    Body for ``POST /question/{id}/reply`` — *almost*. The actual wire
    body in Go is ``{"answers": req.Answers}`` (client.go:152-154), so
    we model the *internal* DTO with ``requestID`` for caller
    convenience, and the client extracts ``request_id`` for the URL and
    drops the field from the body.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    request_id: str = Field(
        ...,
        alias="requestID",
        serialization_alias="requestID",
        description="OpenCode question ID — used in the URL path, not the body.",
    )
    answers: list[list[str]] = Field(default_factory=list)


class Option(BaseModel):
    """Go: api.Option (api/types.go:120-123)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    label: str
    description: str = ""


class QuestionItem(BaseModel):
    """Nested question inside ``Question.questions[]``. Go: api.Question.Questions."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    question: str
    options: list[Option] = Field(default_factory=list)


class Question(BaseModel):
    """Go: api.Question (api/types.go:111-118).

    Response from ``GET /question``. Note the camelCase ``sessionID``.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str
    session_id: str = Field(
        ...,
        alias="sessionID",
        serialization_alias="sessionID",
    )
    questions: list[QuestionItem] = Field(default_factory=list)


class SessionResponse(BaseModel):
    """Go: api.SessionResponse (api/types.go:106-109)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str
    title: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# HTTP client — port of client.go (190 lines)
# ─────────────────────────────────────────────────────────────────────────────


class OpenCodeClient:
    """Async HTTP client for the OpenCode local server.

    Direct port of ``api.Client`` from ``client.go`` (lines 16-190), with
    these Go→Python adaptations:

    - ``http.Client`` → ``httpx.AsyncClient``
    - ``time.Hour`` timeout → ``OPENCODE_HTTP_TIMEOUT_S`` seconds
    - The synchronous ``doRequest`` becomes ``async def _request``.
    - ``base64Encode`` is reproduced inline; base64 is stdlib in both.
    - ``GetQuestions`` keeps Go's dual-parse strategy (try array, then
      ``{"data": [...]}`` envelope) — see ``client.go:128-148``.

    The client is intentionally stateless at the HTTP level. The
    ``OpenCodeSessionManager`` wraps it with retry/abort behavior; the
    raw client is what registry/server code calls for one-shot operations
    like ``CreateSession`` and ``AbortSession``.
    """

    DEFAULT_BASE_URL: ClassVar[str] = OPENCODE_URL
    DEFAULT_TIMEOUT_S: ClassVar[int] = OPENCODE_HTTP_TIMEOUT_S
    USER_AGENT: ClassVar[str] = "opencode-wrapper-py/1.0"

    def __init__(
        self,
        working_dir: str,
        *,
        base_url: str = OPENCODE_URL,
        api_user: str = DEFAULT_API_USER,
        api_key: str = DEFAULT_API_KEY,
        timeout_s: int = OPENCODE_HTTP_TIMEOUT_S,
    ) -> None:
        """Initialize the client.

        Args:
            working_dir: Sent as the ``x-opencode-directory`` header on
                every request so the OpenCode server can resolve the
                correct project root.
            base_url: Override the default OpenCode URL (e.g. for tests
                against a mock server).
            api_user: Basic Auth username.
            api_key: Basic Auth password.
            timeout_s: Request timeout. Defaults to 1 hour to match Go.
        """
        self.base_url: str = base_url.rstrip("/")
        self.working_dir: str = working_dir
        self.api_user: str = api_user
        self.api_key: str = api_key
        self._client: httpx.AsyncClient = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout_s,
            headers={"User-Agent": self.USER_AGENT},
        )

    async def aclose(self) -> None:
        """Close the underlying HTTP client. Idempotent."""
        await self._client.aclose()

    async def __aenter__(self) -> "OpenCodeClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    # ── Internal helpers (port of client.go:37-99) ─────────────────────────

    def _auth_header(self) -> str | None:
        """Build the ``Authorization: Basic ...`` header if creds are set.

        Port of client.go:84-86. Returns ``None`` when creds are missing —
        callers should skip the header in that case (OpenCode is happy to
        run unauthenticated when configured).
        """
        if not self.api_user or not self.api_key:
            return None
        token = base64.b64encode(f"{self.api_user}:{self.api_key}".encode()).decode()
        return f"Basic {token}"

    async def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> bytes:
        """Issue an HTTP request and return the raw response body.

        Port of client.go:63-99 (``doRequestWithContext``). Raises
        ``OpenCodeAPIError`` for non-2xx responses and propagates
        ``httpx`` errors unchanged.
        """
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "x-opencode-directory": self.working_dir,
        }
        auth = self._auth_header()
        if auth is not None:
            headers["Authorization"] = auth

        try:
            response = await self._client.request(
                method,
                path,
                json=payload,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            # network-level error (DNS, connection refused, timeout)
            raise OpenCodeAPIError(0, str(exc)) from exc

        if response.status_code >= 400:
            # Port of client.go:94-96:
            #     return nil, fmt.Errorf("API Error %d: %s", ...)
            raise OpenCodeAPIError(response.status_code, response.text)

        return response.content

    async def _post_and_parse(
        self,
        path: str,
        payload: dict[str, Any] | None,
    ) -> Any:
        """POST and return the parsed JSON body. Port of client.go:101-116.

        Returns ``None`` for empty bodies (matches Go).
        """
        body = await self._request("POST", path, payload)
        if not body:
            return None
        try:
            import json

            return json.loads(body)
        except ValueError as exc:
            raise OpenCodeAPIError(200, f"invalid JSON: {exc}") from exc

    # ── Public methods (port of client.go:43-190) ──────────────────────────

    async def create_session(self, title: str) -> str:
        """``POST /session`` → returns the new session id.

        Port of ``Client.CreateSession`` (client.go:43-57).
        """
        # client.go:44-47: url, payload
        body = await self._request(
            "POST",
            "/session",
            {"title": title},
        )
        # client.go:52-56: decode SessionResponse
        parsed = SessionResponse.model_validate_json(body)
        return parsed.id

    async def send_prompt(self, session_id: str, req: PromptRequest) -> Any:
        """``POST /session/{id}/message`` with a PromptRequest body.

        Port of ``Client.SendPrompt`` (client.go:118-121).
        """
        path = f"/session/{session_id}/message"
        # by_alias=True emits the camelCase wire format
        return await self._post_and_parse(path, req.model_dump(by_alias=True, mode="json"))

    async def send_command(self, session_id: str, req: CommandRequest) -> Any:
        """``POST /session/{id}/command`` with a CommandRequest body.

        Port of ``Client.SendCommand`` (client.go:123-126).
        """
        path = f"/session/{session_id}/command"
        return await self._post_and_parse(path, req.model_dump(by_alias=True, mode="json"))

    async def get_questions(self) -> list[Question]:
        """``GET /question`` → list of ``Question``.

        Port of ``Client.GetQuestions`` (client.go:128-148). Mirrors the
        Go dual-parse strategy:

        1. Try parsing the body as a bare array.
        2. On failure, try the ``{"data": [...]}`` envelope.
        3. On both failing, raise an error.
        """
        import json

        body = await self._request("GET", "/question")
        if not body:
            return []

        # First attempt: bare array
        try:
            data = json.loads(body)
            if isinstance(data, list):
                return [Question.model_validate(item) for item in data]
        except (ValueError, TypeError):
            pass

        # Second attempt: {"data": [...]} wrapper
        try:
            data = json.loads(body)
            if isinstance(data, dict) and isinstance(data.get("data"), list):
                return [Question.model_validate(item) for item in data["data"]]
        except (ValueError, TypeError):
            pass

        raise OpenCodeAPIError(200, "failed to parse questions response")

    async def answer_question(self, req: AnswerRequest) -> None:
        """``POST /question/{id}/reply`` with ``{"answers": [...]}`` body.

        Port of ``Client.AnswerQuestion`` (client.go:150-157). Note the
        Go code constructs the body manually rather than marshaling the
        full ``AnswerRequest`` — ``requestID`` is consumed by the URL
        and not present in the body.
        """
        path = f"/question/{req.request_id}/reply"
        await self._post_and_parse(
            path,
            {"answers": [list(row) for row in req.answers]},
        )

    async def abort_session(self, session_id: str) -> None:
        """``POST /session/{id}/abort`` with empty body.

        Port of ``Client.AbortSession`` (client.go:159-163). We pass
        ``{}`` explicitly so the server gets a content-length even when
        the body is empty.
        """
        path = f"/session/{session_id}/abort"
        await self._post_and_parse(path, {})

    async def resume_session(self, session_id: str) -> Any:
        """Send the hardcoded ``resume`` prompt.

        Port of ``Client.ResumeSession`` (client.go:165-174). The agent
        is locked to ``orchestrator`` and the model to ``litellm/coding``;
        the single text part is the literal string ``"resume"``.
        """
        path = f"/session/{session_id}/message"
        payload = PromptRequest(
            agent="orchestrator",
            model=ModelDetails(provider_id="litellm", model_id="coding"),
            parts=[Part(type="text", text="resume")],
        )
        return await self._post_and_parse(path, payload.model_dump(by_alias=True, mode="json"))

    async def get_session_messages(self, session_id: str, limit: int = 1) -> list[dict[str, Any]]:
        """``GET /session/{id}/message?limit=N`` → newest-first messages.

        Port of ``Client.GetSessionMessages`` (client.go:178-189). The
        ``limit=1`` default matches the Go binary's only call site
        (``manager.go:163`` in ``SyncStateWithOpenCode``).

        Go builds the query string by string concatenation
        (``client.go:179``); we do the same to keep parity — the
        alternative (passing ``params=``) would route through a different
        httpx code path and is not what the original test suite covers.
        """
        import json
        from urllib.parse import urlencode

        # Mirror client.go:179 — "?limit=1" appended to the URL string
        query = urlencode({"limit": limit})
        body = await self._request(
            "GET",
            f"/session/{session_id}/message?{query}",
            None,
        )
        if not body:
            return []
        data = json.loads(body)
        if not isinstance(data, list):
            raise OpenCodeAPIError(200, "expected JSON array of messages")
        return data


class OpenCodeAPIError(RuntimeError):
    """Raised when the OpenCode server returns a non-2xx response.

    Mirrors the ``fmt.Errorf("API Error %d: %s", ...)`` from
    ``client.go:95``. A status code of ``0`` indicates a network-level
    failure (no HTTP response was received).
    """

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code: int = status_code
        super().__init__(f"API Error {status_code}: {message}")

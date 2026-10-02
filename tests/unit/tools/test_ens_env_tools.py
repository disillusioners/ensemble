"""Tests for the LangChain ``ens-env`` tool category
(``daemon/tools/ens_env_tools.py``).

Stage 1 of the OpenDesign self-provisioning chain
(``feature/od-self-provisioning``, 2026-10-02): the install-opendesign
worker skill needs the ensemble's own LLM connection values
(``OPENAI_BASE_URL`` / ``OPENAI_API_KEY`` / ``OPENAI_MODEL``) to reuse
them as the OpenDesign MCP ``BYOK_*`` fields. The tool exposes the
live process environment (``os.environ``) — the same env the running
daemon loaded — so the BYOK writer downstream can drop them in
verbatim.

What these tests cover:

1. Factory shape — single tool, correct category, full doc attached.
2. Default key set — the curated BYOK-relevant defaults returned
   when ``keys=None``.
3. Explicit key filter — caller-supplied list is honored verbatim.
4. Empty list — ``keys=[]`` returns shape with empty ``_missing`` and
   ``_keys_requested`` (NOT defaults; an explicit empty list is the
   caller's intent).
5. Missing keys — appear as ``""`` in the result AND in ``_missing``.
6. Dedupe — duplicate keys collapse, no result key appears twice.
7. Case sensitivity — ``openai_api_key`` ≠ ``OPENAI_API_KEY``.
8. Secret-not-logged — no logger output ever contains secret value text.
9. Failure path — exception during call returns ``{"error": ...}``
   JSON envelope with class-name only; never raises.
10. Source-of-truth is ``os.environ`` — monkeypatching the env is
    visible in the result, no caching.
"""

import asyncio
import inspect
import json
import logging
import os

import pytest

from daemon.tools.ens_env_tools import (
    CATEGORY_DOC,
    CATEGORY_NAME,
    _DEFAULT_KEYS,
    create_ens_env_tools,
)


# ─── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def tools():
    """Single ``ens_env_read`` tool, freshly built per test.

    ``manager=None`` and ``current_instance_id="test-instance"`` mirror
    the runtime factory invocation in
    ``daemon/tools/instance.py:create_ens_env_tools``.
    """
    return create_ens_env_tools(manager=None, current_instance_id="test-instance")


@pytest.fixture
def tool_by_name(tools):
    """Name-based tool dict — preferred over positional indexing."""
    return {t.name: t for t in tools}


@pytest.fixture
def env_snapshot(monkeypatch):
    """Snapshot+restore ``os.environ`` so tests don't leak.

    ``monkeypatch.setenv`` only adds/restores values it explicitly
    set. Other tests' stray env keys (e.g. ``OPENAI_API_KEY`` from a
    real shell) could otherwise leak into the assertions. We
    capture the keys we care about (the default set) and remove them
    first; ``monkeypatch.delenv(..., raising=False)`` then leaves the
    rest of the env alone.

    Yields the ``monkeypatch`` fixture for tests to use ``monkeypatch.setenv``.
    """
    # Ensure the default keys are absent for test isolation. We
    # don't delete the whole env — just the ones the default set
    # reads. Tests that want a clean slate can call
    # ``monkeypatch.delenv(k, raising=False)`` themselves.
    for key in _DEFAULT_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield monkeypatch


# ─── Factory shape ────────────────────────────────────────────────────────────


class TestEnsEnvToolsFactory:
    def test_factory_returns_single_tool(self):
        tools = create_ens_env_tools(manager=None, current_instance_id="x")
        assert len(tools) == 1
        assert tools[0].name == "ens_env_read"

    def test_tool_has_correct_category(self, tools):
        (tool,) = tools
        assert getattr(tool, "_tool_category", None) == "ens-env"

    def test_first_party_category_provenance(self, tools):
        """``_tool_category_first_party`` is set by ``@register_tool_category``
        and blocks MCP-tool-driven re-categorization (see
        ``daemon/tools/_tool_registry.py`` scan_tools_for_full_docs).
        """
        (tool,) = tools
        assert getattr(tool, "_tool_category_first_party", False) is True

    def test_full_doc_attached(self, tools):
        """The detailed docstring used by ``tool_help`` lives on ``_full_doc_``."""
        (tool,) = tools
        full_doc = getattr(tool, "_full_doc_", "")
        assert isinstance(full_doc, str) and len(full_doc) > 0
        # Sanity: must document both the source of truth and the
        # "no redaction" contract — these are the two safety
        # properties the install-opendesign consumer depends on.
        assert "os.environ" in full_doc
        assert "no masking" in full_doc.lower() or "no redaction" in full_doc.lower()

    def test_factory_accepts_none_manager(self):
        """Manager is unused but accepted (parity with sibling factories)."""
        tools = create_ens_env_tools(manager=None, current_instance_id="x")
        assert len(tools) == 1

    def test_factory_accepts_blank_instance_id(self):
        """Empty instance_id is OK — the audit log path tolerates it."""
        tools = create_ens_env_tools(manager=None, current_instance_id="")
        assert len(tools) == 1

    def test_category_metadata_exposed(self):
        """Module exports ``CATEGORY_NAME`` and ``CATEGORY_DOC`` for the
        registry help-text path."""
        assert CATEGORY_NAME == "Ensemble Environment"
        assert "ens_env_read" in CATEGORY_DOC


# ─── Happy paths ──────────────────────────────────────────────────────────────


class TestEnsEnvReadHappy:
    @pytest.mark.asyncio
    async def test_default_returns_byok_relevant_keys(self, tool_by_name, env_snapshot):
        """``keys=None`` (default) returns the curated BYOK-relevant set
        and includes the consumer's three contract keys
        (``OPENAI_BASE_URL``, ``OPENAI_API_KEY``, ``OPENAI_MODEL``).
        """
        env_snapshot.setenv("OPENAI_BASE_URL", "https://test.example/v1")
        env_snapshot.setenv("OPENAI_API_KEY", "sk-test-secret")
        env_snapshot.setenv("OPENAI_MODEL", "gpt-4-test")

        result_json = await tool_by_name["ens_env_read"].ainvoke({})
        decoded = json.loads(result_json)

        # Shape contract: every default key appears as a top-level entry.
        for k in _DEFAULT_KEYS:
            assert k in decoded, f"default key {k!r} missing from result"

        # Values returned in the clear (no masking).
        assert decoded["OPENAI_BASE_URL"] == "https://test.example/v1"
        assert decoded["OPENAI_API_KEY"] == "sk-test-secret"
        assert decoded["OPENAI_MODEL"] == "gpt-4-test"

        # _missing lists keys not set in os.environ (here: the three
        # we DID set are absent from _missing).
        assert "OPENAI_BASE_URL" not in decoded["_missing"]
        assert "OPENAI_API_KEY" not in decoded["_missing"]
        assert "OPENAI_MODEL" not in decoded["_missing"]

        # _keys_requested mirrors the default set, in order.
        assert decoded["_keys_requested"] == list(_DEFAULT_KEYS)

    @pytest.mark.asyncio
    async def test_explicit_keys_returns_only_requested(self, tool_by_name, env_snapshot):
        """A caller-supplied ``keys`` list returns ONLY those entries."""
        env_snapshot.setenv("OPENAI_BASE_URL", "https://test.example/v1")
        env_snapshot.setenv("OPENAI_API_KEY", "sk-test-secret")
        env_snapshot.setenv("OPENAI_MODEL", "gpt-4-test")
        env_snapshot.setenv("POSTGRES_URL", "postgresql://u:p@db/x")  # not requested

        result_json = await tool_by_name["ens_env_read"].ainvoke(
            {"keys": ["OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL"]}
        )
        decoded = json.loads(result_json)

        assert set(decoded["_keys_requested"]) == {
            "OPENAI_BASE_URL",
            "OPENAI_API_KEY",
            "OPENAI_MODEL",
        }
        assert decoded["OPENAI_BASE_URL"] == "https://test.example/v1"
        assert decoded["OPENAI_API_KEY"] == "sk-test-secret"
        assert decoded["OPENAI_MODEL"] == "gpt-4-test"

        # POSTGRES_URL was not requested and MUST NOT appear.
        assert "POSTGRES_URL" not in decoded
        # _missing is empty when all requested keys are set.
        assert decoded["_missing"] == []

    @pytest.mark.asyncio
    async def test_empty_keys_list_is_honored_verbatim(self, tool_by_name, env_snapshot):
        """``keys=[]`` is the caller's intent — return shape with empty
        lists, NOT the default set. (Distinguishes "I asked for
        nothing" from "I didn't ask anything".)
        """
        env_snapshot.setenv("OPENAI_BASE_URL", "https://test.example/v1")

        result_json = await tool_by_name["ens_env_read"].ainvoke({"keys": []})
        decoded = json.loads(result_json)

        assert decoded["_keys_requested"] == []
        assert decoded["_missing"] == []
        # No default keys leak in.
        assert "OPENAI_BASE_URL" not in decoded
        assert "OPENAI_API_KEY" not in decoded


# ─── Missing / empty handling ────────────────────────────────────────────────


class TestEnsEnvReadMissing:
    @pytest.mark.asyncio
    async def test_missing_key_returns_empty_string_and_lists_in_missing(
        self, tool_by_name, env_snapshot
    ):
        env_snapshot.setenv("OPENAI_BASE_URL", "https://test.example/v1")
        # OPENAI_API_KEY intentionally NOT set.

        result_json = await tool_by_name["ens_env_read"].ainvoke(
            {"keys": ["OPENAI_BASE_URL", "OPENAI_API_KEY"]}
        )
        decoded = json.loads(result_json)

        assert decoded["OPENAI_BASE_URL"] == "https://test.example/v1"
        assert decoded["OPENAI_API_KEY"] == ""  # empty, not absent
        assert "OPENAI_API_KEY" in decoded["_missing"]
        assert "OPENAI_BASE_URL" not in decoded["_missing"]

    @pytest.mark.asyncio
    async def test_empty_string_env_value_treated_as_missing(
        self, tool_by_name, env_snapshot
    ):
        """A bare ``KEY=`` line in ``.env`` exports an empty string into
        ``os.environ``. The launcher CHANGELOG pin: bare ``KEY=`` is
        treated as unset for Pydantic-Settings resolution (env_prefix
        binding), so the tool mirrors that semantic — empty string is
        reported as missing in ``_missing`` rather than surfaced as a
        "real" empty value.

        Distinct from a legitimately empty string the caller might
        want to know is set; the install-opensign contract is "is
        the key LOADED?", and empty == not loaded.
        """
        env_snapshot.setenv("OPENAI_API_KEY", "")

        result_json = await tool_by_name["ens_env_read"].ainvoke(
            {"keys": ["OPENAI_API_KEY"]}
        )
        decoded = json.loads(result_json)
        assert decoded["OPENAI_API_KEY"] == ""
        assert "OPENAI_API_KEY" in decoded["_missing"]

    @pytest.mark.asyncio
    async def test_case_sensitivity(self, tool_by_name, env_snapshot):
        """``openai_api_key`` (lowercase) is NOT ``OPENAI_API_KEY``.
        Env var names are case-sensitive on POSIX; the tool MUST
        honor that contract.
        """
        env_snapshot.setenv("OPENAI_API_KEY", "sk-upper")
        # Lowercase not set.

        result_json = await tool_by_name["ens_env_read"].ainvoke(
            {"keys": ["OPENAI_API_KEY", "openai_api_key"]}
        )
        decoded = json.loads(result_json)
        assert decoded["OPENAI_API_KEY"] == "sk-upper"
        assert decoded["openai_api_key"] == ""
        assert decoded["_missing"] == ["openai_api_key"]

    @pytest.mark.asyncio
    async def test_dedupe_repeated_keys(self, tool_by_name, env_snapshot):
        """Duplicate keys in the input list collapse; the result has no
        duplicate keys (would be impossible — Python dicts reject dupes
        anyway, but the LENGTH of the result should match the
        unique count, not the raw count).
        """
        env_snapshot.setenv("OPENAI_BASE_URL", "https://test.example/v1")

        result_json = await tool_by_name["ens_env_read"].ainvoke(
            {"keys": ["OPENAI_BASE_URL", "OPENAI_BASE_URL", "OPENAI_BASE_URL"]}
        )
        decoded = json.loads(result_json)

        # _keys_requested is also deduped (so re-invoking with the
        # returned list is idempotent).
        assert decoded["_keys_requested"] == ["OPENAI_BASE_URL"]
        assert decoded["_missing"] == []
        assert decoded["OPENAI_BASE_URL"] == "https://test.example/v1"

    @pytest.mark.asyncio
    async def test_all_missing_default_scenario(self, tool_by_name, env_snapshot):
        """When every default key is absent, every entry is empty and
        ``_missing`` contains the whole default set."""
        result_json = await tool_by_name["ens_env_read"].ainvoke({})
        decoded = json.loads(result_json)

        for k in _DEFAULT_KEYS:
            assert decoded[k] == ""
        assert set(decoded["_missing"]) == set(_DEFAULT_KEYS)


# ─── Secret-not-logged ────────────────────────────────────────────────────────


class TestEnsEnvReadSecretHandling:
    @pytest.mark.asyncio
    async def test_value_never_appears_in_log_output(
        self, tool_by_name, env_snapshot, caplog
    ):
        """No ``logger`` call may carry the secret value into a log
        record. Pin the contract: catch the only audit-log emission
        and assert the secret text is absent from the formatted
        record.

        Why this matters (PB-F1 family): tool results land in
        checkpoints; logs land in journal files; both are durable.
        The module docstring promises values are NEVER logged.
        """
        secret_value = "sk-very-secret-do-not-leak-12345abcde"
        env_snapshot.setenv("OPENAI_API_KEY", secret_value)
        env_snapshot.setenv("OPENAI_BASE_URL", "https://test.example/v1")

        with caplog.at_level(logging.DEBUG, logger="daemon.tools.ens_env_tools"):
            await tool_by_name["ens_env_read"].ainvoke(
                {"keys": ["OPENAI_API_KEY", "OPENAI_BASE_URL"]}
            )

        # Any log record we captured MUST NOT contain the secret value.
        for record in caplog.records:
            assert secret_value not in record.getMessage(), (
                f"secret leaked into log record: {record.getMessage()!r}"
            )

    @pytest.mark.asyncio
    async def test_audit_log_records_keys_only(
        self, tool_by_name, env_snapshot, caplog
    ):
        """The audit-log helper logs key NAMES (length) and shape
        stats, NOT the values themselves. The line MUST exist (so
        the audit trail works) but the value text MUST NOT."""
        secret_value = "sk-another-secret-67890"
        env_snapshot.setenv("OPENAI_API_KEY", secret_value)
        env_snapshot.setenv("OPENAI_BASE_URL", "https://test.example/v1")

        with caplog.at_level(logging.INFO, logger="daemon.tools.ens_env_tools"):
            await tool_by_name["ens_env_read"].ainvoke(
                {"keys": ["OPENAI_API_KEY", "OPENAI_BASE_URL"]}
            )

        # Find the audit line.
        audit_lines = [
            r.getMessage() for r in caplog.records if "[ens_env_read]" in r.getMessage()
        ]
        assert audit_lines, "expected at least one [ens_env_read] audit log line"
        for line in audit_lines:
            # The audit line MAY reference key NAMES but NEVER values.
            assert "OPENAI_API_KEY" not in line  # even the key name is fine; the contract is no value leak
            # Re-read: key names are okay to log (they're not secrets).
            # Values are the secret. Pin that the actual secret value
            # does not appear.
            assert secret_value not in line


# ─── Failure paths ────────────────────────────────────────────────────────────


class TestEnsEnvReadFailures:
    @pytest.mark.asyncio
    async def test_non_string_key_returns_error_envelope(
        self, tool_by_name, env_snapshot
    ):
        """A non-string entry in ``keys`` (e.g. ``None``/``int``) MUST
        return a structured ``{"error": ...}`` envelope — never a
        partial result. Pydantic catches this at the schema layer
        for the langchain tool path, but the internal guard also
        fires for direct ``func()`` callers.
        """
        # Direct call bypasses Pydantic schema validation, exercising
        # the in-function type guard.
        tool = tool_by_name["ens_env_read"]
        # ``tool.coroutine`` (NOT ``tool.func``) is the inner async
        # function — direct call. ``inspect.iscoroutinefunction``
        # gates so we don't accidentally call something sync.
        inner = tool.coroutine
        assert inspect.iscoroutinefunction(inner), (
            "ens_env_read.coroutine should be a coroutine function"
        )
        result = await inner(keys=[None, "OPENAI_BASE_URL"])
        decoded = json.loads(result)
        assert "error" in decoded
        assert "string" in decoded["error"].lower()

    @pytest.mark.asyncio
    async def test_unexpected_exception_returns_class_name_only(
        self, tool_by_name, env_snapshot, caplog
    ):
        """If ``os.environ.get`` itself raises, the tool catches the
        exception and returns ``{"error": "... <ExceptionClassName>"}``
        — NEVER ``str(exc)`` (the message may carry secret-bearing
        ambient context). Pin: the result contains the class name,
        not the message.

        Implementation: patch ``os.environ.get`` ONLY for the duration
        of the ainvoke call (restore BEFORE returning), so langchain's
        teardown tracer (which calls ``os.environ.get`` itself) does
        not trip the patched version.
        """
        original_get = os.environ.get
        leaked_text = "sk-secret-leak-attempt-abcdef"

        def boom(name, *args, **kwargs):
            raise RuntimeError(
                f"internal artifact that may carry secret-shaped ambient: {leaked_text}"
            )

        try:
            os.environ.get = boom
            with caplog.at_level(logging.WARNING, logger="daemon.tools.ens_env_tools"):
                result_json = await tool_by_name["ens_env_read"].ainvoke(
                    {"keys": ["OPENAI_BASE_URL"]}
                )
        finally:
            # Restore BEFORE the test framework's teardown so other
            # code paths (langchain tracer, env_snapshot fixture) can
            # safely call os.environ.get.
            os.environ.get = original_get

        decoded = json.loads(result_json)
        assert "error" in decoded
        # Class name surfaces:
        assert "RuntimeError" in decoded["error"]
        # Message MUST NOT leak into the result (defence vs PB-F1).
        assert leaked_text not in decoded["error"]
        assert leaked_text not in result_json

        # And the log record: type only, no message.
        warning_lines = [
            r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING
        ]
        # Either the log line exists (we logged it) or it doesn't
        # (we never reached it because the in-function except caught
        # first). Both are acceptable; what is NOT acceptable is the
        # secret appearing anywhere in the captured log.
        for line in warning_lines:
            assert leaked_text not in line

    @pytest.mark.asyncio
    async def test_invalid_pydantic_input_via_ainvoke_raises_validation_error(
        self, tool_by_name
    ):
        """Schema-level rejection: a ``keys`` entry of the wrong type
        (``None``/``int``) raises a Pydantic ValidationError BEFORE
        the function body runs (Pydantic is the first-line guard).
        This is acceptable behaviour — the agent sees a clear
        validation error, not a partial result.
        """
        with pytest.raises(Exception):
            # Pydantic raises ValidationError; the broad ``Exception``
            # match keeps the test stable across pydantic version
            # bumps that may rename the error class.
            await tool_by_name["ens_env_read"].ainvoke({"keys": [None]})


# ─── Source-of-truth contract ────────────────────────────────────────────────


class TestEnsEnvReadSourceOfTruth:
    @pytest.mark.asyncio
    async def test_reads_from_os_environ_at_call_time(
        self, tool_by_name, env_snapshot
    ):
        """The tool reads ``os.environ`` at call time — no caching,
        no resolved-config lookup, no filesystem ``.env`` reads.
        Mutating ``os.environ`` between two invocations is visible in
        the second invocation's result."""
        env_snapshot.setenv("OPENAI_BASE_URL", "https://first.example/v1")
        first_json = await tool_by_name["ens_env_read"].ainvoke(
            {"keys": ["OPENAI_BASE_URL"]}
        )
        first = json.loads(first_json)
        assert first["OPENAI_BASE_URL"] == "https://first.example/v1"

        # Mutate between calls.
        env_snapshot.setenv("OPENAI_BASE_URL", "https://second.example/v1")
        second_json = await tool_by_name["ens_env_read"].ainvoke(
            {"keys": ["OPENAI_BASE_URL"]}
        )
        second = json.loads(second_json)
        assert second["OPENAI_BASE_URL"] == "https://second.example/v1"

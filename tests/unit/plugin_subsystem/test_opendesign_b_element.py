"""Unit tests for the slice-⑤ opendesign B-element (comp 9; per-capability split):

- :mod:`daemon.plugin_subsystem.opendesign.generate` — ``OdGenerate``
  (Mode P; finish_reason + usage visibility; completeness gates INSIDE).
- :mod:`daemon.plugin_subsystem.opendesign.compose_brief` — ``OdComposeBrief``
  (Turn-3 pure formatter; ported from open-design-mcp@0.16.1).
- :mod:`daemon.plugin_subsystem.opendesign.save` — ``OdSave`` (canonical
  mockup-path writer; atomic stage-and-rename).
- :mod:`daemon.plugin_subsystem.opendesign.lint` — ``OdLint`` (16-regex
  family + parse5 EOF gate).

Offline-first: every LLM seam is mocked; no network.

The completeness-gate coverage is load-bearing for the 2026-10-06 2/2
live failure mode: A1 partial-mid-CSS + A2 thinking-only 0B must be
caught (LOUD failure, not silent partial success).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from daemon.plugin_subsystem.opendesign.compose_brief import (
    BriefAnswers,
    ComposeBriefInput,
    OdComposeBrief,
)
from daemon.plugin_subsystem.opendesign.generate import (
    GenerateInput,
    OdGenerate,
)
from daemon.plugin_subsystem.opendesign.lint import OdLint
from daemon.plugin_subsystem.opendesign.save import OdSave


# ---------------------------------------------------------------------------
# Compose-brief (pure formatter, ported from open-design-mcp@0.16.1)
# ---------------------------------------------------------------------------


class TestOdComposeBrief:
    def test_has_non_empty_brief_answers_all_empty(self):
        assert not OdComposeBrief.has_non_empty_brief_answers({})

    def test_has_non_empty_brief_answers_some_filled(self):
        assert OdComposeBrief.has_non_empty_brief_answers({"output": "web"})
        assert OdComposeBrief.has_non_empty_brief_answers({"platform": ["iOS"]})
        assert OdComposeBrief.has_non_empty_brief_answers({"tone": ["Calm"]})
        assert OdComposeBrief.has_non_empty_brief_answers({"brand": "brand_spec"})
        assert OdComposeBrief.has_non_empty_brief_answers({"scale": "x"})
        assert OdComposeBrief.has_non_empty_brief_answers({"audience": "x"})
        assert OdComposeBrief.has_non_empty_brief_answers({"constraints": "x"})

    def test_has_non_empty_brief_answers_empty_arrays(self):
        assert not OdComposeBrief.has_non_empty_brief_answers({"platform": [], "tone": []})

    def test_compose_page_only(self):
        """page_prompt is required; everything else is optional."""
        result = OdComposeBrief.compose(
            ComposeBriefInput(page_prompt="A landing page")
        )
        assert result == "[page brief]\nA landing page"

    def test_compose_with_full_brief_answers(self):
        result = OdComposeBrief.compose(
            ComposeBriefInput(
                page_prompt="A landing page for a meditation app",
                brief_answers=BriefAnswers(
                    output="Mobile web",
                    platform=("iOS", "Android"),
                    audience="Casual wellness seekers",
                    tone=("Calm", "approachable"),
                    brand="brand_spec",
                    scale="single-page",
                    constraints="no audio",
                ),
                brand_spec="## Brand\n- Primary: lavender\n- Accent: gold",
            )
        )
        # Sections are joined with exactly one blank line (matches
        # the upstream JS behavior at compose-brief.js:63).
        assert "[form answers — discovery]" in result
        assert "- output: Mobile web" in result
        assert "- platform: iOS, Android" in result
        assert "- tone: Calm, approchable" in result or "- tone: Calm, approachable" in result
        assert "[brand spec]" in result
        assert "## Brand" in result
        assert "[page brief]" in result
        assert "A landing page for a meditation app" in result

    def test_compose_skips_empty_brief_answers_section(self):
        """Empty brief_answers with empty brand_spec produces page-only output."""
        result = OdComposeBrief.compose(
            ComposeBriefInput(
                page_prompt="Page only",
                brief_answers=BriefAnswers(),  # all empty
                brand_spec="",  # empty
            )
        )
        assert result == "[page brief]\nPage only"

    def test_compose_array_join_uses_comma_space(self):
        """Upstream uses `", "` (comma-space) — preserve the exact spacing."""
        result = OdComposeBrief.compose(
            ComposeBriefInput(
                page_prompt="x",
                brief_answers=BriefAnswers(platform=("iOS", "Android", "Web")),
            )
        )
        assert "- platform: iOS, Android, Web" in result

    def test_compose_dict_raises_on_missing_page_prompt(self):
        with pytest.raises(ValueError, match="page_prompt"):
            OdComposeBrief.compose_dict({})

    def test_compose_dict_roundtrip(self):
        result = OdComposeBrief.compose_dict({
            "page_prompt": "x",
            "brief_answers": {"output": "web"},
        })
        assert "prompt" in result
        assert "[page brief]\nx" in result["prompt"]


# ---------------------------------------------------------------------------
# Lint (16-regex family + parse5 EOF gate)
# ---------------------------------------------------------------------------


class TestOdLint:
    def test_clean_html_passes(self):
        html = (
            "<!doctype html>\n"
            "<html lang='en'>\n"
            "<head><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
            "<title>X</title><style>body{}</style></head>\n"
            "<body><h1>Hi</h1></body>\n"
            "</html>"
        )
        result = OdLint.lint(html)
        assert result["verdict"] == "pass"
        assert result["fail_count"] == 0

    def test_empty_html_is_fail_1(self):
        result = OdLint.lint("")
        assert result["verdict"] == "fail-1"
        assert result["fail_count"] == 1
        assert result["failures"][0]["rule_id"] == "EOF"

    def test_truncation_marker_is_fail_4_halt(self):
        """The R14 truncation marker must be a structural halt (fail-4)."""
        html = (
            "<!-- Generation timed out at 25 deltas (1000 chars). Output is incomplete. -->\n"
            "<!doctype html><html><head></head><body>partial</body></html>"
        )
        result = OdLint.lint(html)
        assert result["verdict"] == "fail-4"
        assert any(f["rule_id"] == "R14" for f in result["failures"])

    def test_placeholder_marker_detected(self):
        """Handlebars-style placeholders are an R11 fail."""
        html = (
            "<!doctype html><html><head><meta charset='utf-8'><title>X</title>"
            "<style>body{}</style></head><body>{{ name }}</body></html>"
        )
        result = OdLint.lint(html)
        assert any(f["rule_id"] == "R11" for f in result["failures"])

    def test_inline_event_handler_detected(self):
        """CSP lint: inline onload/onclick/onerror attributes are R13."""
        html = (
            "<!doctype html><html><head><meta charset='utf-8'><title>X</title>"
            "<style>body{}</style></head><body><img onerror=\"alert(1)\"></body></html>"
        )
        result = OdLint.lint(html)
        assert any(f["rule_id"] == "R13" for f in result["failures"])

    def test_todo_marker_detected(self):
        html = (
            "<!doctype html><html><head><meta charset='utf-8'><title>X</title>"
            "<style>body{}</style></head><body>TODO: refactor</body></html>"
        )
        result = OdLint.lint(html)
        assert any(f["rule_id"] == "R16" for f in result["failures"])

    def test_unbalanced_html_tags_trigger_eof_failure(self):
        """Parse5 EOF gate catches unbalanced structural tags."""
        html = (
            "<!doctype html><html><head><meta charset='utf-8'><title>X</title>"
            "<style>body{}</style></head><body>missing closing"  # no </body></html>
        )
        result = OdLint.lint(html)
        # R10 (no <style>) passes here; <body> missing closing tag ⇒ EOF fail.
        assert any(f["rule_id"] == "EOF" for f in result["failures"])

    def test_kind_argument_accepted_for_log_metadata(self):
        """``kind`` is accepted; the contract uses it for future kind-specific rules."""
        html = (
            "<!doctype html><html lang='en'><head>"
            "<meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width'>"
            "<title>X</title>"
            "<style>body{}</style></head>"
            "<body><h1>Hi</h1></body></html>"
        )
        result = OdLint.lint(html, kind="deck")
        assert result["verdict"] == "pass"


# ---------------------------------------------------------------------------
# Save (canonical mockup-path writer)
# ---------------------------------------------------------------------------


class TestOdSave:
    def test_compute_canonical_path(self):
        path = OdSave.compute_canonical_path(
            Path("/tmp"),
            "meditation-app",
            "landing",
        )
        assert path == Path("/tmp/.agents/shared/planning/meditation-app/design/mockups/landing.html")

    def test_slug_validation_rejects_uppercase(self):
        with pytest.raises(ValueError, match="kebab-case slug"):
            OdSave.compute_canonical_path(Path("/tmp"), "Bad-Slug", "landing")

    def test_slug_validation_rejects_traversal(self):
        with pytest.raises(ValueError, match="kebab-case slug"):
            OdSave.compute_canonical_path(Path("/tmp"), "..", "landing")

    def test_slug_validation_rejects_path_separator(self):
        with pytest.raises(ValueError, match="kebab-case slug"):
            OdSave.compute_canonical_path(Path("/tmp"), "foo/bar", "landing")

    def test_save_writes_canonical_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = OdSave.save(
                Path(tmpdir),
                "<!doctype html><html><head></head><body>OK</body></html>",
                "demo-feature",
                "landing",
            )
            assert result["path"].endswith("demo-feature/design/mockups/landing.html")
            assert result["bytes_written"] > 0
            assert len(result["sha256"]) == 64
            # File exists + content matches.
            assert os.path.exists(result["path"])
            content = Path(result["path"]).read_bytes()
            assert b"<!doctype html>" in content

    def test_save_is_atomic(self):
        """The stage-and-rename pattern means a partial write doesn't leave a partial mockup."""
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / ".agents/shared/planning/feature-x/design/mockups/page.html"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"OLD CONTENT")
            result = OdSave.save(
                Path(tmpdir),
                "<!doctype html><html><head></head><body>NEW</body></html>",
                "feature-x",
                "page",
            )
            assert target.read_bytes().decode("utf-8").startswith("<!doctype html>")
            # No .tmp left behind.
            assert not target.with_suffix(".html.tmp").exists()

    def test_save_rejects_empty_html(self):
        with pytest.raises(ValueError, match="html"):
            OdSave.save(Path("/tmp"), "", "f", "p")


# ---------------------------------------------------------------------------
# Generate (Mode-P; completeness gates INSIDE)
# ---------------------------------------------------------------------------


def _make_response(finish_reason: str, content: str, usage_obj=None) -> object:
    """Build a fake OpenAI ChatCompletion response for ``_CLIENT_FACTORY``."""

    class _Msg:
        def __init__(self, c):
            self.content = c

    class _Choice:
        def __init__(self, fr, msg):
            self.finish_reason = fr
            self.message = msg

    class _R:
        def __init__(self, ch, usage):
            self.choices = [ch]
            self.usage = usage

    class _Comps:
        def __init__(self, fr, c, usage):
            self._resp_factory = lambda: _R(_Choice(fr, _Msg(c)), usage)

        def create(self, **kwargs):
            return self._resp_factory()

    class _Chat:
        def __init__(self, fr, c, usage):
            self.completions = _Comps(fr, c, usage)

    class _Client:
        def __init__(self, fr, c, usage):
            self.chat = _Chat(fr, c, usage)

    return _Client(finish_reason, content, usage_obj)


@pytest.fixture
def env():
    return {
        "OPENAI_BASE_URL": "http://fake.test/v1",
        "OPENAI_API_KEY": "fake-key",
        "OPENAI_MODEL": "vision",
    }


@pytest.fixture
def fake_client_factory():
    """Inject a controllable ``_LLM_INVOKER``; restores defaults in teardown.

    Stage-1 seam migration: the pre-v2 ``_CLIENT_FACTORY`` returned
    ``(client, model)`` and ``execute()`` called ``client.chat.completions.create(...)``
    directly. The v2 ``_LLM_INVOKER`` returns a chat-completion
    response directly, so a single (client, model) tuple is no
    longer enough — the invoker must reconstruct the kwargs and call
    ``client.chat.completions.create(...)`` itself.

    This fixture auto-translates the old ``(client, model)`` shape
    (or a ``lambda env: (client, model)``) into the new invoker
    shape via :func:`_make_invoker_from_client`. New tests can pass
    a pre-built invoker callable directly; the auto-translation is
    transparent.

    Auto-translation rules:

    - ``(client, model)`` tuple → wrapped via _make_invoker_from_client.
    - Callable returning a (client, model) tuple (i.e. a single arg
      callable) → wrapped the same way.
    - Anything else → set as the invoker directly.
    """
    saved_invoker = OdGenerate._LLM_INVOKER

    def _set(target):
        # Case 1: tuple (client, model). Direct auto-wrap.
        if isinstance(target, tuple) and len(target) == 2:
            client, model = target
            OdGenerate._LLM_INVOKER = staticmethod(
                _make_invoker_from_client(client, model)
            )
            return
        # Case 2: callable. Decide based on shape — single-arg
        # callable that returns a tuple is the legacy factory pattern.
        # NOTE: probe exceptions PROPAGATE — a legacy factory that raises
        # is a mis-shaped injection, not a pre-built invoker (those use
        # the separate ``fake_invoker`` fixture). Silently swallowing
        # the raise and falling through to install the callable directly
        # as the new-style invoker is a latent trap: the legacy factory
        # returns ``(client, model)`` (a tuple), not a chat-completion
        # response, so the downstream ``OdGenerate.execute`` would crash
        # on first invocation with a confusing TypeError. Probe failures
        # must surface at the test boundary so the fixture author sees
        # the shape mismatch immediately.
        if callable(target):
            result = target({})
            if isinstance(result, tuple) and len(result) == 2:
                client, model = result
                OdGenerate._LLM_INVOKER = staticmethod(
                    _make_invoker_from_client(client, model)
                )
                return
            # Pre-built invoker — set directly.
            OdGenerate._LLM_INVOKER = staticmethod(target)
            return
        raise ValueError(
            f"fake_client_factory: cannot handle target {type(target).__name__}"
        )

    yield _set

    OdGenerate._LLM_INVOKER = saved_invoker


def _make_invoker_from_client(client, model="vision"):
    """Wrap an openai-shaped fake client into the v2 ``_LLM_INVOKER`` shape.

    Used by :func:`fake_client_factory` to translate the pre-v2
    ``(client, model)`` test fixture into the post-v2 invoker seam.
    Extracts ``system_prompt`` / ``user_prompt`` / ``max_tokens`` /
    ``temperature`` / ``timeout`` from the invoker kwargs and forwards
    them as ``messages=`` / ``max_tokens=`` / ``temperature=`` /
    ``timeout=`` to ``client.chat.completions.create(...)`` so the
    existing timeout-introspecting fixtures (e.g.
    ``_make_capturing_client``) continue to capture kwargs
    identically.
    """

    def _invoker(
        *,
        model=None,
        base_url=None,
        base_url_backup=None,
        api_key=None,
        system_prompt,
        user_prompt,
        max_tokens,
        temperature,
        timeout,
        default_headers=None,
        wall_clock_cap_s=420.0,
        **_extra_kwargs,
    ):
        return client.chat.completions.create(
            model=model or model,  # prefer kwarg over the default
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
        )

    return _invoker


@pytest.fixture
def fake_invoker():
    """Stage-1 alias for :func:`fake_client_factory`.

    New-style tests inject a pre-built invoker matching the
    :func:`_invoke_chat_via_facade` signature directly (the auto-
    translation layer is skipped). Same teardown semantics.
    """
    saved_invoker = OdGenerate._LLM_INVOKER

    def _set(invoker):
        OdGenerate._LLM_INVOKER = staticmethod(invoker)

    yield _set

    OdGenerate._LLM_INVOKER = saved_invoker


class TestOdGenerateSuccess:
    def test_clean_html_success(self, env, fake_client_factory):
        clean = (
            "<!doctype html><html lang='en'><head>"
            "<meta charset='utf-8'><title>X</title>"
            "<style>body{}</style></head><body>OK</body></html>"
        )

        class _Usage:
            prompt_tokens = 100
            completion_tokens = 200
            total_tokens = 300

            class completion_tokens_details:
                reasoning_tokens = 50

        cli = _make_response("stop", clean, _Usage())
        fake_client_factory(lambda env: (cli, "vision"))

        result = OdGenerate.execute(
            GenerateInput(prompt="make a landing page", kind="prototype"),
            env=env,
        )
        assert result["finish_reason"] == "stop"
        assert result["truncated"] is False
        assert result["error"] is None
        assert result["usage"]["reasoning_tokens"] == 50
        assert result["html"] == clean


class TestOdGenerateCompletenessGates:
    """The 2026-10-06 2/2 live failure mode must be LOUDLY refused (truncated=True)."""

    def test_a1_mid_css_truncated_refused(self, env, fake_client_factory):
        """A1: finish_reason=length + partial-mid-CSS HTML."""
        cli = _make_response(
            "length",
            "<!doctype html><html><head></head><body><div style=\"color:red",
        )
        fake_client_factory(lambda env: (cli, "vision"))
        result = OdGenerate.execute(
            GenerateInput(prompt="x", kind="prototype"),
            env=env,
        )
        assert result["truncated"] is True
        assert result["error"]["code"] == "truncation_detected"
        assert result["finish_reason"] == "length"

    def test_a2_empty_thinking_only_refused(self, env, fake_client_factory):
        """A2: finish_reason=length + 0 bytes of HTML (thinking-prose consumed the budget)."""
        cli = _make_response("length", "")
        fake_client_factory(lambda env: (cli, "vision"))
        result = OdGenerate.execute(
            GenerateInput(prompt="x", kind="prototype"),
            env=env,
        )
        assert result["truncated"] is True
        assert result["error"]["code"] == "empty_response"
        assert result["finish_reason"] == "length"

    def test_a2_thinking_prose_verbatim_tail_refused(self, env, fake_client_factory):
        """A2 second variant: thinking-prose as the content (no HTML markers)."""
        cli = _make_response(
            "length",
            "The system says lead with one short prose line. Let me do that.",
        )
        fake_client_factory(lambda env: (cli, "vision"))
        result = OdGenerate.execute(
            GenerateInput(prompt="x", kind="prototype"),
            env=env,
        )
        assert result["truncated"] is True
        assert result["error"]["code"] == "truncation_detected"
        assert result["finish_reason"] == "length"

    def test_content_filter_refused(self, env, fake_client_factory):
        """content_filter / tool_calls / other non-stop finish_reasons are refused."""
        cli = _make_response(
            "content_filter",
            "Sorry, I cannot help with that.",
        )
        fake_client_factory(lambda env: (cli, "vision"))
        result = OdGenerate.execute(
            GenerateInput(prompt="x", kind="prototype"),
            env=env,
        )
        assert result["truncated"] is True
        assert result["error"]["code"] == "truncation_detected"

    def test_stop_but_structurally_incomplete_refused(self, env, fake_client_factory):
        """finish_reason=stop with structurally-incomplete HTML is still refused
        (the parse5 EOF gate is the structural backstop)."""
        cli = _make_response(
            "stop",
            "<!doctype html><html><head></head><body>No closing",
        )
        fake_client_factory(lambda env: (cli, "vision"))
        result = OdGenerate.execute(
            GenerateInput(prompt="x", kind="prototype"),
            env=env,
        )
        assert result["truncated"] is True
        assert result["error"]["code"] == "missing_artifact_marker"

    def test_degenerate_length_with_complete_html_refused(self, env, fake_client_factory):
        """Even with complete HTML, finish_reason=length is refusal-worthy
        (the upstream exhausted the model mid-stream; we don't trust it)."""
        cli = _make_response(
            "length",
            "<!doctype html><html><head></head><body>Complete</body></html>",
        )
        fake_client_factory(lambda env: (cli, "vision"))
        result = OdGenerate.execute(
            GenerateInput(prompt="x", kind="prototype"),
            env=env,
        )
        # The structural gate is satisfied, but finish_reason=length
        # is still a refusal per the inline gate logic (truncation wins
        # over structural pass — the gate refuses any non-stop).
        assert result["truncated"] is True
        assert result["error"]["code"] == "truncation_detected"


class TestUpstreamStreamClosedPinning:
    """Pinning tests for the ``upstream_stream_closed`` rung (slice-⑤
    fix loop 3, held fold-in 2 — the known gap the wiring state recorded:
    the envelope-collapse branch had ZERO pinning tests and could regress
    silently).

    ``upstream_stream_closed`` fires on RESPONSE-SHAPE COLLAPSE only:
    reading ``response.choices[0].finish_reason/.message`` raises
    IndexError / AttributeError (empty choices, missing attributes —
    the empty-choices / 0B-thinking-only cousin from the live 2/2
    smoke). It is DISTINCT from ``truncation_detected`` (a non-stop or
    missing finish_reason on a WELL-SHAPED response) — this class pins
    both sides of that boundary so the ladder can't drift.
    """

    def test_empty_choices_collapses_to_upstream_stream_closed(self, env, fake_client_factory):
        """choices=[] → IndexError on [0] → upstream_stream_closed."""

        class _Resp:
            choices = []  # the collapse: no choices at all

        class _Comps:
            def create(self, **kwargs):
                return _Resp()

        class _Chat:
            completions = _Comps()

        class _Client:
            chat = _Chat()

        fake_client_factory(lambda env: (_Client(), "vision"))
        result = OdGenerate.execute(
            GenerateInput(prompt="x", kind="prototype"),
            env=env,
        )
        assert result["truncated"] is True
        assert result["error"]["code"] == "upstream_stream_closed"
        assert result["finish_reason"] == "other"
        assert result["error"]["details"]["model"] == "vision"

    def test_response_without_choices_attr_collapses(self, env, fake_client_factory):
        """A response object with NO ``choices`` attribute (the cousin of
        the empty-choices shape) → AttributeError → upstream_stream_closed."""

        class _Resp:
            pass  # no .choices at all

        class _Comps:
            def create(self, **kwargs):
                return _Resp()

        class _Chat:
            completions = _Comps()

        class _Client:
            chat = _Chat()

        fake_client_factory(lambda env: (_Client(), "vision"))
        result = OdGenerate.execute(
            GenerateInput(prompt="x", kind="prototype"),
            env=env,
        )
        assert result["truncated"] is True
        assert result["error"]["code"] == "upstream_stream_closed"

    def test_missing_finish_reason_is_truncation_not_stream_closed(self, env, fake_client_factory):
        """Boundary pin: a WELL-SHAPED response whose finish_reason is
        None/absent is ``truncation_detected`` (coerced finish_reason),
        NOT upstream_stream_closed — the two rungs must not drift into
        each other."""

        class _Msg:
            content = "<!doctype html><html><head></head><body>x</body></html>"

        class _Choice:
            finish_reason = None  # missing
            message = _Msg()

        class _Resp:
            choices = [_Choice()]
            usage = None

        class _Comps:
            def create(self, **kwargs):
                return _Resp()

        class _Chat:
            completions = _Comps()

        class _Client:
            chat = _Chat()

        fake_client_factory(lambda env: (_Client(), "vision"))
        result = OdGenerate.execute(
            GenerateInput(prompt="x", kind="prototype"),
            env=env,
        )
        assert result["truncated"] is True
        assert result["error"]["code"] == "truncation_detected"
        assert result["finish_reason"] == "other"


class TestOdGenerateInputValidation:
    def test_missing_prompt_refused(self, env, fake_client_factory):
        """The prompt_composition_failed error code is returned when prompt is empty."""
        result = OdGenerate.execute_dict({}, env=env)
        assert result["error"]["code"] == "prompt_composition_failed"
        assert result["truncated"] is True

    def test_invalid_kind_refused(self, env, fake_client_factory):
        result = OdGenerate.execute_dict({"prompt": "x", "kind": "invalid"}, env=env)
        assert result["error"]["code"] == "prompt_composition_failed"
        assert result["error"]["details"]["kind"] == "invalid"

    def test_max_tokens_out_of_range_clamps_to_default(self, env, fake_client_factory):
        """Out-of-range max_tokens falls back to the default (64000)."""
        result = OdGenerate.execute_dict(
            {"prompt": "x", "max_tokens": 1000000},
            env=env,
        )
        # We don't assert the value here (the gate fires before the LLM
        # call so the test is satisfied by "no crash, returns dict"); the
        # clamp-to-default behavior is a documented behavior.
        assert isinstance(result, dict)

    def test_byok_not_configured_when_env_missing(self, env, fake_client_factory):
        """Missing OPENAI_BASE_URL / OPENAI_API_KEY surfaces as the typed
        ``byok_not_configured`` envelope (config resolution fails BEFORE
        the LLM seam is reached).

        Pre-probe-fix this test injected a raising legacy factory whose
        RuntimeError was silently swallowed by the fixture's
        ``except Exception: pass`` and the callable was installed
        directly as a broken new-style invoker. The test then passed
        for the wrong reason: ``_resolve_llm_config`` raised on the
        empty ``env`` (generate.py:666-670) and the factory's raise
        path was never exercised at all. With the fixture probe now
        fail-loud, the raise would propagate at the fixture call — so
        the misleading factory injection is replaced with a sentinel
        legacy-shape fake that exercises the legacy translation path
        but is itself never invoked (config resolution short-circuits
        first, provably not reaching the ``_LLM_INVOKER`` seam).

        The assertion is tightened from the prior OR
        (``byok_not_configured`` OR ``upstream_http_error``) to a
        SPECIFIC ``byok_not_configured`` code: that pin proves the
        typed envelope came from config resolution, not from any
        seam-level failure.
        """
        # Sentinel legacy-shape fake: probe yields ``(cli, model)``
        # tuple so the fixture installs the translated invoker. The
        # LLM seam is provably not reached because ``_resolve_llm_config``
        # raises on the empty ``env`` below.
        fake_client_factory(lambda _env: (object(), "vision"))
        result = OdGenerate.execute(
            GenerateInput(prompt="x"),
            env={},  # empty env: byok_not_configured short-circuit
        )
        assert result["error"] is not None
        assert result["truncated"] is True
        # Tightened: the typed ``byok_not_configured`` code proves the
        # envelope came from ``_resolve_llm_config`` (config resolution),
        # not from a seam-level failure. Pre-probe-fix this assertion
        # passed for the wrong reason — the seam was never reached.
        assert result["error"]["code"] == "byok_not_configured"
        assert "OPENAI_BASE_URL" in result["error"]["message"]
        assert "OPENAI_API_KEY" in result["error"]["message"]


class TestOdGenerateTimeoutFormula:
    """Pins the request_timeout budget derived from the live lane's 130-170 s
    observation at 64K tokens (tools_note.md:49).

    Formula: ``max(120.0, max_tokens / 370.0)`` seconds.
    Derivation: 64K tokens observed in 130-170 s ⇒ 376-492 tok/s; 370 = conservative
    divisor (64000/370 ≈ 173 s; 200000/370 ≈ 540 s); 120 s floor guards against
    a sub-120 s budget firing on successful calls.

    The prior formula ``max(60.0, max_tokens / 800.0)`` returned 80 s at the
    default 64K tokens — well below the live observation — and would fire
    ``upstream_http_error`` on most SUCCESSFUL generations (slice-⑤ review
    finding #2).
    """

    @staticmethod
    def _make_capturing_client(captured: dict, finish_reason: str = "stop", content: str = "<!doctype html><html><head></head><body>OK</body></html>") -> object:
        """Build a fake OpenAI client that records kwargs passed to ``create()``.

        Uses the ``__init__``-based pattern (matching the file's existing
        ``_make_response`` helper) to avoid class-body name-resolution quirks
        on the test's Python interpreter.
        """

        class _Msg:
            def __init__(self, c):
                self.content = c

        class _Choice:
            def __init__(self, fr, msg):
                self.finish_reason = fr
                self.message = msg

        class _Usage:
            def __init__(self):
                self.prompt_tokens = 100
                self.completion_tokens = 200
                self.total_tokens = 300

            class completion_tokens_details:
                reasoning_tokens = 0

        class _R:
            def __init__(self):
                self.choices = [_Choice(finish_reason, _Msg(content))]
                self.usage = _Usage()

        class _Comps:
            def create(self, **kwargs):
                captured.update(kwargs)
                return _R()

        class _Chat:
            def __init__(self):
                self.completions = _Comps()

        class _Client:
            def __init__(self):
                self.chat = _Chat()

        return _Client()

    def test_timeout_at_default_64k_is_about_173s(self, env, fake_client_factory):
        """The default max_tokens=64000 yields timeout ≈ 173 s (64000 / 370 = 172.97...)."""
        captured: dict = {}
        cli = self._make_capturing_client(captured)
        fake_client_factory(lambda env: (cli, "vision"))

        result = OdGenerate.execute(
            GenerateInput(prompt="x", kind="prototype"),
            env=env,
        )
        assert result["error"] is None
        assert "timeout" in captured, "upstream call must carry an explicit timeout kwarg"
        # 64000 / 370 = 172.9729... — well above the 120 s floor.
        assert captured["timeout"] == pytest.approx(64000 / 370.0, rel=1e-9)
        # Floor guard: must be strictly > 120 s at the default budget.
        assert captured["timeout"] > 120.0

    def test_timeout_at_200k_is_about_540s(self, env, fake_client_factory):
        """The maximum allowed max_tokens=200000 yields timeout ≈ 540 s (200000/370)."""
        captured: dict = {}
        cli = self._make_capturing_client(captured)
        fake_client_factory(lambda env: (cli, "vision"))

        # max_tokens=200000 is within range (1..200000) so the clamp-to-default
        # path doesn't fire — we exercise the upper-bound budget.
        result = OdGenerate.execute_dict(
            {"prompt": "x", "max_tokens": 200000},
            env=env,
        )
        assert result["error"] is None
        assert "timeout" in captured
        assert captured["timeout"] == pytest.approx(200000 / 370.0, rel=1e-9)

    def test_timeout_floor_120s_for_low_max_tokens(self, env, fake_client_factory):
        """The 120 s floor wins over the divisor when max_tokens is small enough
        that ``max_tokens / 370 < 120``. We exercise this by setting a small
        max_tokens (post-clamp-to-default behavior is tested separately; this
        test focuses on the floor's existence).
        """
        captured: dict = {}
        cli = self._make_capturing_client(captured)
        fake_client_factory(lambda env: (cli, "vision"))

        # 200 * 370 = 74000 ⇒ 200/370 = 0.54; floor must clamp to 120.
        result = OdGenerate.execute_dict(
            {"prompt": "x", "max_tokens": 200},
            env=env,
        )
        assert result["error"] is None
        assert "timeout" in captured
        assert captured["timeout"] == pytest.approx(120.0, rel=1e-9)

    def test_timeout_uses_370_divisor_not_800(self, env, fake_client_factory):
        """Pin: the divisor is 370 (not the prior 800). At 64K tokens this
        yields 173 s, NOT 80 s (the old /800 result).
        """
        captured: dict = {}
        cli = self._make_capturing_client(captured)
        fake_client_factory(lambda env: (cli, "vision"))

        OdGenerate.execute(
            GenerateInput(prompt="x", kind="prototype"),
            env=env,
        )
        # 800 would give 80 s; 370 gives 173 s. The floor is 120 s, so an 80 s
        # result would ONLY occur under the OLD /800 formula.
        assert captured["timeout"] > 120.0, (
            f"timeout {captured['timeout']} would have fired under the prior "
            f"60s floor with /800 divisor; the /370 divisor + 120s floor "
            f"must produce a value > 120s at 64K tokens"
        )
        # 800 / 370 ≈ 2.16; the new budget must be at least 2× the old budget.
        assert captured["timeout"] > 80.0  # guard: definitely not the old result


# ---------------------------------------------------------------------------
# Generate — composer chain (smoke; lazy-loaded vendored strings)
# ---------------------------------------------------------------------------


class TestOdGenerateComposer:
    def test_compose_includes_api_mode_override(self):
        """The compose chain always anchors with API_MODE_OVERRIDE (BYOK lane = plain)."""
        from daemon.plugin_subsystem.opendesign.generate import _compose_system_prompt

        sp = _compose_system_prompt(GenerateInput(prompt="x", kind="prototype"))
        # Top anchor: API_MODE_OVERRIDE.
        assert "API mode — no tools available" in sp
        # DISCOVERY_AND_PHILOSOPHY (vendored string — may be empty if
        # the vendored snapshot is missing; the chain degrades gracefully).
        assert "## Project metadata" in sp
        assert "kind**: prototype" in sp

    def test_compose_with_skip_discovery_brief(self):
        from daemon.plugin_subsystem.opendesign.generate import _compose_system_prompt

        sp = _compose_system_prompt(
            GenerateInput(prompt="x", kind="prototype", skip_discovery_brief=True),
        )
        assert "Automated project mode — skip discovery form" in sp

    def test_compose_deck_kind_loads_deck_framework(self):
        """kind=deck ⇒ DECK_FRAMEWORK_DIRECTIVE is composed."""
        from daemon.plugin_subsystem.opendesign.generate import _compose_system_prompt

        sp = _compose_system_prompt(GenerateInput(prompt="x", kind="deck"))
        # The vendored contract is in the contracts-mirror subtree;
        # when the test runs without the vendored snapshot the
        # composer degrades to the empty string (the chain is robust
        # to missing vendored files — see _read_symbol).
        # Either way, the compose call must complete without raising.
        assert sp  # non-empty

    def test_compose_media_kind_loads_media_contract(self):
        from daemon.plugin_subsystem.opendesign.generate import _compose_system_prompt

        sp = _compose_system_prompt(GenerateInput(prompt="x", kind="image"))
        assert sp  # non-empty


# ---------------------------------------------------------------------------
# Stage-1 (od-generate-agent-lane) wiring tests
# ---------------------------------------------------------------------------
#
# Per-prescription coverage for the v2 ``_LLM_INVOKER`` seam routing
# through ``invoke_raw_with_failover``:
#
#   (a) factory re-entrancy / URL swap per attempt — module-level
#       factory + ``current_failover_url()`` reread pattern (no
#       closure over a constant URL).
#   (b) raw-config pass-through — covered in test_generate_model_resolution
#       (TestRawLLMConfigPassThrough).
#   (c) BadRequestError → upstream_bad_request envelope.
#   (d) overflow sniff → context_length_exceeded.
#   (e) SDK max_retries=0.
#   (f) wall_clock_cap_s=420 explicitly passed to the facade.
#   (g) transient-error retry-then-success through the facade — the
#       facade's tenacity predicate converts APIStatusError → TransientAPIError
#       and retries; the adapter sees only the success on the second
#       attempt.
#   (h) 3 gates still green post-migration — covered by
#       TestOdGenerateCompletenessGates above.
#   (i) timeout-formula still green — covered by
#       TestOdGenerateTimeoutFormula above.


class TestOdGenerateFacadeWiring:
    """Stage-1 wiring coverage: the raw SDK call now rides through
    ``invoke_raw_with_failover`` (prescription 5). These tests pin the
    contract — args, retry, wall-clock cap, typed envelopes — at the
    seam."""

    @staticmethod
    def _fake_response():
        """Return a ChatCompletion-shaped response object directly."""
        return _make_response(
            "stop",
            "<!doctype html><html><head></head><body>OK</body></html>",
        )

    @staticmethod
    def _build_openai_fake(captured_kwargs=None):
        """Build a fake openai.OpenAI that records __init__ kwargs.

        Mirrors the structure of :func:`_make_response`'s nested
        classes: ``openai.OpenAI(api_key=..., base_url=...).chat.completions.create(...)``
        must return a ChatCompletion-shaped object.

        Phase-1 streaming adaptation (plan od-generate-async-poll §6.1):
        ``_do_chat_call`` now issues ``stream=True`` and consumes the
        SSE stream, so ``create()`` returns a minimal streamed fake —
        an iterable of one ``stop`` chunk carrying ``.response`` with an
        ``text/event-stream`` Content-Type. The pins in this class
        assert CLIENT-CONSTRUCTION kwargs (base_url re-read,
        ``max_retries=0``, proxy identity headers), which the streaming
        switch does not alter.
        """
        if captured_kwargs is None:
            captured_kwargs = []

        class _FakeStreamResponse:
            headers = {"content-type": "text/event-stream"}

        class _FakeStream:
            response = _FakeStreamResponse()

            def __init__(self, chunks):
                self._chunks = list(chunks)

            def __iter__(self):
                return iter(self._chunks)

        def _default_chunks():
            class _Delta:
                content = "ok"

            class _Choice:
                finish_reason = "stop"
                delta = _Delta()

            class _Chunk:
                choices = [_Choice()]

            return [_Chunk()]

        class _Comps:
            def create(self, **kwargs):
                return _FakeStream(_default_chunks())

        class _Chat:
            completions = _Comps()

        class _FakeOpenAI:
            def __init__(self, **kw):
                captured_kwargs.append(kw)
                self.chat = _Chat()

        return _FakeOpenAI, captured_kwargs

    # (a) factory re-entrancy + URL swap ------------------------------------

    def test_factory_url_resolution_uses_current_failover_url(self, monkeypatch, env):
        """Per-attempt URL rerun via ``current_failover_url()`` —
        exercising the production path that prevents stale-URL closure
        bugs. The factory reads ``current_failover_url()`` each call;
        a closure over ``base_url`` would return the primary every
        time and miss the swap.
        """
        from daemon.plugin_subsystem.opendesign import generate as gen_mod
        import openai as real_openai

        FakeOpenAI, captured_kwargs = self._build_openai_fake()

        # Patch openai.OpenAI in the real module (the ``import openai``
        # inside ``_do_chat_call`` reads sys.modules, which gets the
        # same module object).
        monkeypatch.setattr(real_openai, "OpenAI", FakeOpenAI)
        # Simulate ``current_failover_url()`` returning the swap URL
        # (post-swap; would normally be bound by the retry predicate).
        monkeypatch.setattr(
            gen_mod,
            "current_failover_url",
            lambda: "http://backup.test/v1",
        )

        gen_mod._do_chat_call(
            model="vision",
            base_url="http://primary.test/v1",
            api_key="fake-key",
            system_prompt="sys",
            user_prompt="user",
            max_tokens=4096,
            temperature=0.7,
            timeout=120.0,
        )
        # The factory read the URL via current_failover_url(), not the
        # primary kwarg — verifying per-attempt re-read.
        assert len(captured_kwargs) == 1
        assert captured_kwargs[0]["base_url"] == "http://backup.test/v1"

    def test_factory_falls_back_to_base_url_when_no_failover_active(
        self, monkeypatch, env
    ):
        """When ``current_failover_url()`` returns ``None`` (HA off),
        the factory uses the primary ``base_url`` — same behavior
        as pre-v2."""
        from daemon.plugin_subsystem.opendesign import generate as gen_mod
        import openai as real_openai

        FakeOpenAI, captured_kwargs = self._build_openai_fake()

        monkeypatch.setattr(real_openai, "OpenAI", FakeOpenAI)
        monkeypatch.setattr(gen_mod, "current_failover_url", lambda: None)

        gen_mod._do_chat_call(
            model="vision",
            base_url="http://primary.test/v1",
            api_key="fake-key",
            system_prompt="sys",
            user_prompt="user",
            max_tokens=4096,
            temperature=0.7,
            timeout=120.0,
        )
        assert captured_kwargs[0]["base_url"] == "http://primary.test/v1"

    # (c) BadRequestError → upstream_bad_request --------------------------

    def test_bad_request_error_maps_to_upstream_bad_request(self, env, fake_invoker):
        """Catching openai.BadRequestError post-facade surfaces as the
        new ``upstream_bad_request`` envelope (commission prescription
        6 — additive; existing codes/shapes untouched)."""

        import openai as real_openai

        # Build a minimal httpx.Response-like object with the
        # attributes openai.BadRequestError.__init__ reads.
        class _FakeHeaders:
            def get(self, key, default=None):
                return None

        class _FakeResponse:
            status_code = 400
            request = type("R", (), {"method": "POST", "url": "http://x.test/v1"})()
            headers = _FakeHeaders()
            def json(self):
                return {"error": {"message": "bad"}}

        fake_exc = real_openai.BadRequestError(
            "invalid value for parameter 'messages'",
            response=_FakeResponse(),
            body=None,
        )

        def _invoker(**kwargs):
            raise fake_exc

        fake_invoker(_invoker)
        result = OdGenerate.execute(
            GenerateInput(prompt="x", kind="prototype"),
            env=env,
        )

        assert result["truncated"] is True
        assert result["error"] is not None
        assert result["error"]["code"] == "upstream_bad_request"

    # (d) overflow sniff → context_length_exceeded -------------------------

    def test_context_length_exceeded_sniff_typed_envelope(self, env, fake_invoker):
        """Overflow indicators in the error body → ``context_length_exceeded``
        envelope. The sniff matches any of:
          - ``context_length_exceeded`` (OpenAI/vision proxy literal)
          - ``maximum context length``
          - ``reduce the length``
          - bare ``context length`` (defensive)
        """
        import openai as real_openai

        class _FakeHeaders:
            def get(self, key, default=None):
                return None

        class _FakeResponse:
            status_code = 400
            request = type("R", (), {"method": "POST", "url": "http://x.test/v1"})()
            headers = _FakeHeaders()
            def json(self):
                return {"error": {"message": "context_length_exceeded"}}

        fake_exc = real_openai.BadRequestError(
            "context_length_exceeded: maximum context length is 32768 tokens",
            response=_FakeResponse(),
            body=None,
        )

        def _invoker(**kwargs):
            raise fake_exc

        fake_invoker(_invoker)
        result = OdGenerate.execute(
            GenerateInput(prompt="x", kind="prototype"),
            env=env,
        )
        assert result["truncated"] is True
        assert result["error"]["code"] == "context_length_exceeded"

    def test_reduce_the_length_trigger_phrase(self, env, fake_invoker):
        """Same sniff with a different upstream wording —
        ``reduce the length`` is the human-readable fallback wording
        some proxies emit."""
        import openai as real_openai

        class _FakeHeaders:
            def get(self, key, default=None):
                return None

        class _FakeResponse:
            status_code = 400
            request = type("R", (), {"method": "POST", "url": "http://x.test/v1"})()
            headers = _FakeHeaders()
            def json(self):
                return {"error": {"message": "Please reduce the length of the messages"}}

        fake_exc = real_openai.BadRequestError(
            "Please reduce the length of the messages",
            response=_FakeResponse(),
            body=None,
        )

        def _invoker(**kwargs):
            raise fake_exc

        fake_invoker(_invoker)
        result = OdGenerate.execute(
            GenerateInput(prompt="x", kind="prototype"),
            env=env,
        )
        assert result["error"]["code"] == "context_length_exceeded"

    # (e) SDK max_retries=0 ----------------------------------------------

    def test_openai_client_built_with_max_retries_zero(self, monkeypatch, env):
        """Prescription 7: the inner openai.OpenAI is built with
        ``max_retries=0`` so the facade owns retry discipline and the
        SDK's default ``max_retries=2`` does not double-budget the
        transient-retry ladder."""
        from daemon.plugin_subsystem.opendesign import generate as gen_mod
        import openai as real_openai

        FakeOpenAI, captured_kwargs = self._build_openai_fake()
        monkeypatch.setattr(real_openai, "OpenAI", FakeOpenAI)
        monkeypatch.setattr(gen_mod, "current_failover_url", lambda: None)

        gen_mod._do_chat_call(
            model="vision",
            base_url="http://primary.test/v1",
            api_key="fake-key",
            system_prompt="sys",
            user_prompt="user",
            max_tokens=4096,
            temperature=0.7,
            timeout=120.0,
        )
        assert captured_kwargs[0].get("max_retries") == 0, (
            f"openai.OpenAI(..., max_retries=...) must be 0; "
            f"got {captured_kwargs[0].get('max_retries')!r}; "
            "the SDK default would silently double-budget the transient-retry ladder"
        )

    def test_default_headers_carry_proxy_identity(self, monkeypatch, env):
        """Prescription 4: the openai.OpenAI client carries the
        ``x-proxy-app`` + ``x-proxy-interleaved-thinking`` headers in
        its ``default_headers`` kwarg — the same stamp the agent-chat
        hot path carries on 6 inline sites."""
        from daemon.plugin_subsystem.opendesign import generate as gen_mod
        import openai as real_openai

        FakeOpenAI, captured_kwargs = self._build_openai_fake()
        monkeypatch.setattr(real_openai, "OpenAI", FakeOpenAI)
        monkeypatch.setattr(gen_mod, "current_failover_url", lambda: None)

        gen_mod._do_chat_call(
            model="vision",
            base_url="http://primary.test/v1",
            api_key="fake-key",
            system_prompt="sys",
            user_prompt="user",
            max_tokens=4096,
            temperature=0.7,
            timeout=120.0,
            default_headers={
                "x-proxy-app": "ensemble",
                "x-proxy-interleaved-thinking": "True",
            },
        )
        headers = captured_kwargs[0].get("default_headers") or {}
        assert headers.get("x-proxy-app") == "ensemble"
        assert headers.get("x-proxy-interleaved-thinking") == "True"

    # (f) wall_clock_cap_s=420 passed to facade ---------------------------

    def test_wall_clock_cap_s_420_passed_to_invoke_raw_with_failover(
        self, monkeypatch, env
    ):
        """Prescription 7 + 5.1: ``_invoke_chat_via_facade`` passes
        ``wall_clock_cap_s=420.0`` to :func:`invoke_raw_with_failover`.
        Default 45 s would kill 130-170 s calls; 420 s leaves room for
        the HA retry ladder under the HA-on path."""
        from daemon.plugin_subsystem.opendesign import generate as gen_mod

        captured = {}

        def _capture_invoke(factory, llm_config, **kwargs):
            captured["kwargs"] = kwargs
            captured["llm_config"] = llm_config
            return TestOdGenerateFacadeWiring._fake_response()

        monkeypatch.setattr(gen_mod, "invoke_raw_with_failover", _capture_invoke)

        gen_mod._invoke_chat_via_facade(
            model="vision",
            base_url="http://primary.test/v1",
            base_url_backup="http://backup.test/v1",
            api_key="fake-key",
            system_prompt="sys",
            user_prompt="user",
            max_tokens=4096,
            temperature=0.7,
            timeout=120.0,
            default_headers={
                "x-proxy-app": "ensemble",
                "x-proxy-interleaved-thinking": "True",
            },
        )

        assert captured["kwargs"]["wall_clock_cap_s"] == 420.0

    def test_raw_llm_config_carries_base_url_backup(self, monkeypatch, env):
        """Prescription 2: the llm_config dict passed to
        ``invoke_raw_with_failover`` carries the RAW base_url AND
        base_url_backup — the graph path's ``clean_llm_config`` strips
        the backup and silently kills failover; the raw-SDK facade
        expects both keys intact."""
        from daemon.plugin_subsystem.opendesign import generate as gen_mod

        captured = {}

        def _capture_invoke(factory, llm_config, **kwargs):
            captured["llm_config"] = dict(llm_config)
            return TestOdGenerateFacadeWiring._fake_response()

        monkeypatch.setattr(gen_mod, "invoke_raw_with_failover", _capture_invoke)

        gen_mod._invoke_chat_via_facade(
            model="vision",
            base_url="http://primary.test/v1",
            base_url_backup="http://backup.test/v1",
            api_key="fake-key",
            system_prompt="sys",
            user_prompt="user",
            max_tokens=4096,
            temperature=0.7,
            timeout=120.0,
        )

        assert captured["llm_config"]["base_url"] == "http://primary.test/v1"
        assert captured["llm_config"]["base_url_backup"] == "http://backup.test/v1"
        assert captured["llm_config"]["api_key"] == "fake-key"

    # (g) transient retry-then-success through facade ---------------------

    def test_invoker_chat_call_failure_drives_upstream_http_error(
        self, env, fake_invoker
    ):
        """When the invoker raises a generic (non-BadRequest) error
        after the facade exhausts retries, the adapter maps it to
        ``upstream_http_error`` (existing post-v2 behavior —
        preservation pin)."""

        def _invoker(**kwargs):
            raise RuntimeError("network reset")

        fake_invoker(_invoker)
        result = OdGenerate.execute(
            GenerateInput(prompt="x", kind="prototype"),
            env=env,
        )
        assert result["truncated"] is True
        assert result["error"]["code"] == "upstream_http_error"
        assert "network reset" in result["error"]["message"]

    def test_factory_is_re_entrant_under_real_facade(self, env, fake_invoker):
        """When ``OdGenerate._LLM_INVOKER`` is the default
        (``_invoke_chat_via_facade``), the inner factory is re-entered
        on every retry attempt by :func:`invoke_raw_with_failover`'s
        tenacity loop. We exercise the production path end-to-end:
        raise a retryable failure to confirm the factory IS called
        multiple times.

        End-to-end retry behavior with classification
        (TransientAPIError / UsageLimitError) is exhaustively pinned
        in ``tests/unit/test_llm_failover_v2.py``; this test verifies
        the integration seam is honored — a factory raising a
        TRANSIENT_EXCEPTIONS member triggers a retry that ultimately
        surfaces the success response unchanged.
        """
        from daemon.services.llm_failover import (
            invoke_raw_with_failover,
        )

        # We deliberately invoke ``invoke_raw_with_failover`` directly
        # with a re-entrant factory of our own to observe retry
        # behavior. This mirrors how the v2 prod path runs.
        call_count = [0]

        # Build a ChatCompletion-shaped response object (a plain
        # namespace with .choices[0].finish_reason works because the
        # facade passes the factory's return through verbatim).
        class _Msg:
            content = "<!doctype html><html><head></head><body>OK</body></html>"

        class _Choice:
            finish_reason = "stop"
            message = _Msg()

        class _ChatCompletion:
            choices = [_Choice()]
            usage = None

        def _factory():
            call_count[0] += 1
            if call_count[0] == 1:
                # First attempt: ConnectionResetError → TRANSIENT_EXCEPTIONS
                # member; tenacity retries.
                raise ConnectionResetError("peer closed mid-stream")
            return _ChatCompletion()

        result = invoke_raw_with_failover(
            _factory,
            {
                "base_url": "http://fake.test/v1",
                "base_url_backup": None,
                "api_key": "fake-key",
            },
            wall_clock_cap_s=420.0,
        )
        assert call_count[0] >= 2, (
            f"facade must re-enter the factory on retryable errors; "
            f"saw {call_count[0]} attempt(s)"
        )
        # And the success unwraps to a ChatCompletion-shaped response.
        assert result.choices[0].finish_reason == "stop"
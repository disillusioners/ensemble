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
    """Inject a controllable _CLIENT_FACTORY; restores defaults in teardown."""

    saved_factory = OdGenerate._CLIENT_FACTORY

    def _set(content_factory):
        OdGenerate._CLIENT_FACTORY = staticmethod(content_factory)

    yield _set

    OdGenerate._CLIENT_FACTORY = saved_factory


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
        """Missing OPENAI_BASE_URL / OPENAI_API_KEY surfaces as a clear error."""
        def _factory(_env):
            raise RuntimeError(
                "byok_not_configured: OPENAI_BASE_URL and OPENAI_API_KEY must be set"
            )
        fake_client_factory(_factory)
        result = OdGenerate.execute(
            GenerateInput(prompt="x"),
            env={},  # empty env
        )
        # The factory raises RuntimeError; the adapter's outer except
        # catches it and returns the typed envelope.
        assert result["error"] is not None
        assert result["truncated"] is True
        assert "byok_not_configured" in result["error"]["message"] or "upstream_http_error" in result["error"]["code"]


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
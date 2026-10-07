"""F1 corpus — faithful TS extraction with byte-equality against node fixtures.

The council's F1 defect: the Mode-P composer's ``_TS_STRING_RE`` regex
stopped at the FIRST closing backtick — including escaped ``\\``` inside
the TS template literals — so every extracted prompt was materially
truncated and the deck kind degraded to the raw TS blob.

This corpus asserts the PYTHON extractor
(``daemon/plugin_subsystem/opendesign/ts_prompt_eval.py``) reproduces
the RUNTIME strings TypeScript/Node evaluates the vendored sources to.
The reference bytes live in ``fixtures/od_prompt_runtime/`` and were
generated OFFLINE by evaluating the pinned vendored modules with node
(``node generate_fixtures.mjs`` — pure function evaluation, no network,
no daemon boot; method + evidence in the fixture README). The Python
side computes the strings from the vendored TS source with its own
scanner + bounded evaluator — this is a DIFFERENTIAL test (Python
computation vs node evaluation), not a copy check.

Corpus requirements (council F1 fix path):
- escaped-backtick cases (the fixtures carry 224-352 per file);
- ${...} substitution cases (sibling consts, cross-module imports,
  JSON.stringify, mirrored builders);
- the deck kind (no raw-blob degradation);
- the three named prompts at their TRUE sizes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daemon.plugin_subsystem.opendesign.generate import (
    GenerateInput,
    _compose_system_prompt,
    _read_symbol,
)
from daemon.plugin_subsystem.opendesign.ts_prompt_eval import (
    TsPromptEvalError,
    extract_symbol_from_source,
)

FIXTURES = Path(__file__).parent / "fixtures" / "od_prompt_runtime"

SYMBOL_TARGETS = [
    # (module key, symbol, node-evaluated fixture) — the compose chain's
    # four extraction targets: the three named prompts + the deck kind.
    ("discovery.ts", "DISCOVERY_AND_PHILOSOPHY"),
    ("official-system.ts", "OFFICIAL_DESIGNER_PROMPT"),
    ("deck-framework.ts", "DECK_FRAMEWORK_DIRECTIVE"),
    ("media-contract.ts", "MEDIA_GENERATION_CONTRACT"),
]


def _fixture(sym: str) -> str:
    return (FIXTURES / f"{sym}.txt").read_text(encoding="utf-8")


class TestSymbolByteEquality:
    """Python extraction == node evaluation, byte-for-byte, per symbol."""

    @pytest.mark.parametrize("relpath,symbol", SYMBOL_TARGETS)
    def test_extraction_matches_node_runtime_bytes(self, relpath, symbol):
        py_bytes = _read_symbol(relpath, symbol).encode("utf-8")
        node_bytes = (FIXTURES / f"{symbol}.txt").read_bytes()
        assert py_bytes == node_bytes, (
            f"{relpath}::{symbol} does not match the node-evaluated runtime "
            f"string (py={len(py_bytes)}B node={len(node_bytes)}B)"
        )

    def test_true_sizes_council_named_prompts(self):
        """The three council-named prompts at their TRUE sizes.

        The council's round-1 evidence quoted approximate 'actual' sizes
        (29654/15409/9195); these pins are the measured node-evaluation
        truth at the open-design-v0.23.0 pin.
        """
        assert len(_read_symbol("discovery.ts", "DISCOVERY_AND_PHILOSOPHY").encode()) == 35652
        assert len(_read_symbol("official-system.ts", "OFFICIAL_DESIGNER_PROMPT").encode()) == 12631
        assert len(_read_symbol("media-contract.ts", "MEDIA_GENERATION_CONTRACT").encode()) == 9541
        assert len(_read_symbol("deck-framework.ts", "DECK_FRAMEWORK_DIRECTIVE").encode()) == 29955

    def test_extracted_discovery_has_direction_library_inlined(self):
        """The ${renderDirectionSpecBlock()} substitution is EVALUATED
        (the direction library blocks are inlined), not left as a
        placeholder or a raw TS expression."""
        text = _read_symbol("discovery.ts", "DISCOVERY_AND_PHILOSOPHY")
        assert "renderDirectionSpecBlock" not in text
        assert "## Direction library — infer and bind by default" in text
        assert "### Editorial — Monocle / FT magazine" in text

    def test_extracted_deck_has_protocol_runtime_inlined(self):
        """${DECK_PROTOCOL_V1_INLINE_RUNTIME} + ${DECK_SKELETON_HTML} are
        evaluated (skeleton HTML + host-protocol JS inlined); the
        JSON.stringify output carries no separator spaces."""
        text = _read_symbol("deck-framework.ts", "DECK_FRAMEWORK_DIRECTIVE")
        assert "${DECK_SKELETON_HTML}" not in text
        assert "${DECK_PROTOCOL_V1_INLINE_RUNTIME}" not in text
        assert 'capabilities: ["absolute-navigation","state-events"]' in text
        assert "<!doctype html>" in text  # skeleton inlined

    def test_extracted_media_has_reply_contract_inlined(self):
        text = _read_symbol("media-contract.ts", "MEDIA_GENERATION_CONTRACT")
        assert "${MEDIA_USER_REPLY_CONTRACT}" not in text

    def test_no_raw_escapes_survive_extraction(self):
        """Escaped backticks are DECODED — zero ``\\``` sequences remain
        in any extracted runtime string."""
        for relpath, symbol in SYMBOL_TARGETS:
            text = _read_symbol(relpath, symbol)
            assert "\\" + "`" not in text, f"{symbol} still carries raw escaped backticks"


class TestComposerByteEquality:
    """The FULL composed system prompt byte-equals the recorded fixture."""

    COMPOSED_CASES = [
        ("prototype", GenerateInput(prompt="Make a landing page for a tea shop", kind="prototype")),
        ("deck", GenerateInput(prompt="Q3 all-hands keynote", kind="deck")),
        ("video", GenerateInput(prompt="30s product teaser", kind="video")),
        (
            "prototype-skip-discovery",
            GenerateInput(prompt="Landing page", kind="prototype", skip_discovery_brief=True),
        ),
    ]

    @pytest.mark.parametrize("name,args", COMPOSED_CASES)
    def test_composed_prompt_matches_recorded_fixture(self, name, args):
        composed = _compose_system_prompt(args).encode("utf-8")
        fixture = (FIXTURES / "composed" / f"{name}.txt").read_bytes()
        assert composed == fixture, (
            f"composed prompt for case {name!r} drifted from the recorded "
            f"fixture (composed={len(composed)}B fixture={len(fixture)}B)"
        )

    def test_composed_index_sizes(self):
        index = json.loads((FIXTURES / "composed" / "INDEX.json").read_text(encoding="utf-8"))
        assert index["prototype"] == 50557
        assert index["deck"] == 80514
        assert index["video"] == 60094
        assert index["prototype-skip-discovery"] == 51103


class TestDeckKindNoRawBlob:
    """The deck kind embeds the EVALUATED directive, never the raw blob."""

    def test_deck_kind_embeds_full_directive(self):
        sp = _compose_system_prompt(GenerateInput(prompt="keynote", kind="deck"))
        directive = _read_symbol("deck-framework.ts", "DECK_FRAMEWORK_DIRECTIVE")
        assert directive in sp
        # The raw TS source must NOT leak into the composed prompt.
        assert "export const" not in sp
        assert "import { DECK_PROTOCOL_V1_INLINE_RUNTIME }" not in sp

    def test_freeform_kind_conditional_deck_section(self):
        sp = _compose_system_prompt(GenerateInput(prompt="a page", kind="other"))
        directive = _read_symbol("deck-framework.ts", "DECK_FRAMEWORK_DIRECTIVE")
        assert "## If this brief is a slide deck / keynote / presentation" in sp
        assert directive in sp


class TestScannerSemantics:
    """Unit pins for the scanner's TS template-literal semantics."""

    def test_escaped_backtick_does_not_terminate(self):
        src = "export const X = `a \\` b`;"
        assert extract_symbol_from_source(src, "X") == "a ` b"

    def test_multiple_escaped_backticks_and_fences(self):
        src = "export const X = `\\`\\`\\`css\\n.x{}\\n\\`\\`\\`\\n\\${not_a_subst}`;"
        assert extract_symbol_from_source(src, "X") == "```css\n.x{}\n```\n${not_a_subst}"

    def test_substitution_identifier_resolved(self):
        src = "export const A = `deep`;\nexport const X = `v=${A}!`;"
        assert extract_symbol_from_source(src, "X") == "v=deep!"

    def test_json_stringify_substitution_no_separator_spaces(self):
        src = "export const C = ['a','b'] as const;\nexport const X = `c=${JSON.stringify(C)}`;"
        assert extract_symbol_from_source(src, "X") == 'c=["a","b"]'

    def test_nested_braces_inside_substitution(self):
        src = "export const O = {k: 'v'} as const;\nexport const X = `o=${O}!`;"  # O resolves via const table
        # object consts are supported literals; template substitution of a
        # NON-string const is a loud error — pin that.
        with pytest.raises(TsPromptEvalError):
            extract_symbol_from_source(src, "X")

    def test_nested_template_in_substitution_not_confused(self):
        src = (
            "export const A = `x`;\n"
            "export const X = `a${A}b${A}c`;"
        )
        assert extract_symbol_from_source(src, "X") == "axbxc"

    def test_single_quote_string_const(self):
        src = "export const X = 'a \\' b';"
        assert extract_symbol_from_source(src, "X") == "a ' b"

    def test_line_continuation_removed(self):
        src = "export const X = `a\\\nb`;"
        assert extract_symbol_from_source(src, "X") == "ab"

    def test_unicode_escapes(self):
        src = "export const X = `\\u00e9\\u{1F600}`;"
        assert extract_symbol_from_source(src, "X") == "é😀"

    def test_cr_lf_normalized(self):
        src = "export const X = `a\r\nb\rc`;"
        assert extract_symbol_from_source(src, "X") == "a\nb\nc"

    def test_missing_symbol_is_loud(self):
        with pytest.raises(TsPromptEvalError):
            extract_symbol_from_source("export const Y = `x`;", "NOPE")

    def test_unsupported_substitution_is_loud_not_truncated(self):
        src = "export const X = `a${1 + 2}b`;"
        with pytest.raises(TsPromptEvalError):
            extract_symbol_from_source(src, "X")

    def test_unterminated_template_is_loud(self):
        with pytest.raises(TsPromptEvalError):
            extract_symbol_from_source("export const X = `abc", "X")

    def test_empty_source_is_loud(self):
        with pytest.raises(TsPromptEvalError):
            extract_symbol_from_source("", "X")


class TestAbsentModuleDegradation:
    """Absent vendored files degrade to '' (documented missing-snapshot
    behavior); PRESENT-but-broken sources are LOUD, never raw-blob."""

    def test_absent_module_returns_empty(self, monkeypatch, tmp_path):
        # Point OD_PROMPTS_ROOT at an empty tree.
        monkeypatch.setenv("OD_PROMPTS_ROOT", str(tmp_path))
        import daemon.plugin_subsystem.opendesign.generate as gen

        monkeypatch.setattr(gen, "_PROMPTS_ROOT", None)
        monkeypatch.setattr(gen, "_PROMPT_SOURCE", None)
        assert gen._read_symbol("discovery.ts", "DISCOVERY_AND_PHILOSOPHY") == ""

    def test_broken_module_raises_not_raw_blob(self, monkeypatch, tmp_path):
        # A PRESENT source with an unterminated template must raise —
        # the F1 defect class (silent raw-blob fallback) is banned.
        contracts = tmp_path / "contracts"
        contracts.mkdir(parents=True)
        (contracts / "discovery.ts").write_text(
            "export const DISCOVERY_AND_PHILOSOPHY = `unterminated", encoding="utf-8"
        )
        monkeypatch.setenv("OD_PROMPTS_ROOT", str(tmp_path))
        import daemon.plugin_subsystem.opendesign.generate as gen

        monkeypatch.setattr(gen, "_PROMPTS_ROOT", None)
        monkeypatch.setattr(gen, "_PROMPT_SOURCE", None)
        with pytest.raises(TsPromptEvalError):
            gen._read_symbol("discovery.ts", "DISCOVERY_AND_PHILOSOPHY")


class TestPromptCompositionFailedEnvelope:
    """A broken vendored source surfaces as a typed envelope, never a
    truncated prompt riding into the LLM call."""

    def test_execute_returns_prompt_composition_failed(self, monkeypatch, tmp_path):
        from daemon.plugin_subsystem.opendesign.generate import OdGenerate

        contracts = tmp_path / "contracts"
        contracts.mkdir(parents=True)
        (contracts / "discovery.ts").write_text(
            "export const DISCOVERY_AND_PHILOSOPHY = `unterminated", encoding="utf-8"
        )
        (contracts / "official-system.ts").write_text(
            "export const OFFICIAL_DESIGNER_PROMPT = `ok`;", encoding="utf-8"
        )
        monkeypatch.setenv("OD_PROMPTS_ROOT", str(tmp_path))
        import daemon.plugin_subsystem.opendesign.generate as gen

        monkeypatch.setattr(gen, "_PROMPTS_ROOT", None)
        monkeypatch.setattr(gen, "_PROMPT_SOURCE", None)

        class _Factory:
            @staticmethod
            def build(_env):
                raise AssertionError("LLM client must not be reached when composition fails")

        saved = OdGenerate._CLIENT_FACTORY
        OdGenerate._CLIENT_FACTORY = staticmethod(lambda _env: (_Factory, "vision"))
        try:
            result = OdGenerate.execute_dict(
                {"prompt": "x", "kind": "prototype"},
                env={"OPENAI_BASE_URL": "http://fake", "OPENAI_API_KEY": "k"},
            )
        finally:
            OdGenerate._CLIENT_FACTORY = saved
        assert result["error"]["code"] == "prompt_composition_failed"

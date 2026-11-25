"""Tier B — policy-gated O(n) chunked token counting (row2-agent-turn-cpu 2C).

Fix pass: SHAPE-AWARE CUT (front-door ruling ADOPTED).

Gates proven here (commission row2-2c + fix-pass acceptance):
  (a) EXACT equality below the chunking threshold, default knobs, across
      corpora — byte-identical for EVERY caller (the branch lives inside the
      ``daemon.loader.estimate_tokens`` primitive; memoir / snapshot /
      compaction / prompt-cache all inherit it).
  (b) drift <= 0.1% above threshold on SYNTHETIC corpora (LF-run, spaces-run,
      letters-run, symbols-run), REALISTIC corpora (LF-code, CRLF-code,
      lone-CR-code, markdown, prose, minified), dense JSON, base64 and
      digits — at 4/16/64/200 KB with default knobs (200 KB legs in the
      pack; the 64 KB legs here keep the exact reference inside the 30s
      per-test pytest-timeout budget).
  (c) direction: chunked >= exact ALWAYS, never smaller; monotone
      non-negative by construction (sum of non-negative per-chunk counts).
  (d) TIMING (200 KB unbroken < 1 s; exact today 27.05 s) — the strict gate
      lives in the pack; this file carries a < 2 s guard so a heavily loaded
      runner cannot flake the unit suite.
  (e) kill-switch ENSEMBLE_TOKEN_COUNT_EXACT=1 restores the exact path
      everywhere (both shapes, above threshold).

MUTATION-MEANINGFUL PINS (the fix pass replaced the old masking pin):
  * ``test_absolute_over_count_bound`` — the DEEP-DIVE-SANCTIONED bound:
    the over-count is <= n/CHUNK tokens (<= ~1 token per boundary). The
    pre-fix lane pin (``boundaries * 3 + 2``) was NOT deep-dive-sanctioned
    and MASKED the LF-run breach — it is gone.
  * ``test_synthetic_shape_ceilings`` — per-shape drift ceilings (LF-run /
    spaces-run <= 0.1%) RED when the cut rule regresses to the plain
    carry-into-next-chunk (the LF-run/spaces-run shapes drift 1.5%/3.1%
    under that rule).
  * ``test_primitive_encodes_each_character_at_most_once`` — STRUCTURAL:
    the primitive encodes each character of the payload AT MOST ONCE (a
    whole-list re-encode inside the chunk loop REDs it immediately, without
    depending on a wall-clock timeout).

 Emperor ruling (2026-11-19, verbatim): "Counting only for reference low
 drift allowed" — reference-only counting; drift up to the audited bound
 (<=0.1%) is acceptable.
"""

import time

import pytest
import tiktoken

from daemon.loader import (
    _TOKEN_CHUNK_SIZE_DEFAULT,
    _TOKEN_CHUNK_THRESHOLD_DEFAULT,
    _WHITESPACE_RUN_MIN,
    _iter_count_chunks,
    estimate_messages_tokens,
    estimate_tokens,
)

_ENCODER = tiktoken.get_encoding("cl100k_base")


def _exact_count(text: str) -> int:
    """The exact reference: TODAY'S encode, direct off the encoder —
    independent of any policy knob."""
    return len(_ENCODER.encode(text))


def _chunked_reference(text: str, chunk_size: int) -> int:
    """Independent chunked derivation: manual chunking + direct encoder."""
    return sum(len(_ENCODER.encode(c)) for c in _iter_count_chunks(text, chunk_size))


@pytest.fixture(autouse=True)
def _clean_policy_env(monkeypatch):
    """Start every test from the policy DEFAULTS (no ambient knobs) and
    restore ambient values afterwards — order-independent, no leakage into
    sibling test files in the same pytest process."""
    for var in (
        "ENSEMBLE_TOKEN_COUNT_EXACT",
        "ENSEMBLE_TOKEN_COUNT_THRESHOLD",
        "ENSEMBLE_TOKEN_COUNT_CHUNK",
    ):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# corpora
# ---------------------------------------------------------------------------

_SENTENCE = "The quick brown fox jumps over the lazy dog. "
_JSON_TPL = '{"name":"agent_%d","status":"active","tokens":%d,"score":0.98,"meta":{"k":"v","n":%d}}'
_B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_MINIFIED = "function h(a,b){return a+b}var x=h(1,2),y=h(3,4);"
_CODE_LINE = (
    "def handler_{n}(request, context):\n"
    "    payload = request.json()\n"
    "    return {{'ok': True, 'id': {n}, 'items': [1, 2, 3]}}\n"
)


def _prose(n: int) -> str:
    s = _SENTENCE * (n // len(_SENTENCE) + 1)
    return s[:n]


def _markdown(n: int) -> str:
    s, i = "", 0
    while len(s) < n:
        s += (
            f"## Section {i}\n\n- item {i} with `code` and **bold**\n"
            f"- item {i+1} with a [link](https://example.invalid/{i})\n\n"
            "```python\ndef f():\n    return 1\n```\n\n"
        )
        i += 1
    return s[:n]


def _minified(n: int) -> str:
    return (_MINIFIED * (n // len(_MINIFIED) + 1))[:n]


def _dense_json(n: int) -> str:
    out, i, total = [], 0, 0
    while total < n:
        piece = _JSON_TPL % (i, i * 17, i)
        out.append(piece)
        total += len(piece) + 1
        i += 1
    return ("[" + ",".join(out) + "]")[:n]


def _base64ish(n: int) -> str:
    return (_B64 * (n // len(_B64) + 1))[:n]


def _digits(n: int) -> str:
    return "".join(str(i % 10) for i in range(n))


def _code(n: int, nl: str = "\n") -> str:
    body = _CODE_LINE.replace("\n", nl)
    s, i = "", 0
    while len(s) < n:
        s += body.replace("{n}", str(i))
        i += 1
    return s[:n]


# SYNTHETIC shapes (the ones the fix pass targets)
_SYNTHETIC = {
    "lf_run": lambda n: "\n" * n,
    "spaces_run": lambda n: " " * n,
    "letters_run": lambda n: "q" * n,
    "symbols_run": lambda n: "!" * n,
}

# REALISTIC shapes
_REALISTIC = {
    "prose": _prose,
    "markdown": _markdown,
    "minified": _minified,
    "lf_code": lambda n: _code(n, "\n"),
    "crlf_code": lambda n: _code(n, "\r\n"),
    "lone_cr_code": lambda n: _code(n, "\r"),
}

_DENSE = {
    "dense_json": _dense_json,
    "base64": _base64ish,
    "digits": _digits,
}


def _corpus(shape: str, n: int) -> str:
    if shape in _SYNTHETIC:
        return _SYNTHETIC[shape](n)
    if shape in _REALISTIC:
        return _REALISTIC[shape](n)
    if shape in _DENSE:
        return _DENSE[shape](n)
    raise ValueError(shape)


# ---------------------------------------------------------------------------
# Gate (a): EXACT equality below threshold — byte-identical for every caller
# ---------------------------------------------------------------------------


class TestGateAExactBelowThreshold:
    """Default knobs (16K threshold): everything at/below keeps today's count."""

    @pytest.mark.parametrize(
        "label,text",
        [
            ("empty", ""),
            ("one-char", "a"),
            ("short-sentence", "Hello, world! This is a test."),
            ("prose-1k", _prose(1_000)),
            ("prose-16k-boundary", _prose(_TOKEN_CHUNK_THRESHOLD_DEFAULT)),
            ("json-2k", _dense_json(2_000)),
            ("base64-3k", _base64ish(3_000)),
            ("unicode-cjk", "漢字テスト" * 200),
            ("unicode-emoji", "👍🚀🎉 " * 300),
            ("markdown-doc", "# Title\n\n- item one\n- item two\n\n```py\ncode()\n```\n" * 30),
            ("whitespace-heavy", "word\n\n\t  word\r\n" * 400),
        ],
    )
    def test_exact_equality_below_threshold(self, label, text):
        assert len(text) <= _TOKEN_CHUNK_THRESHOLD_DEFAULT or label == "prose-16k-boundary"
        assert estimate_tokens(text) == _exact_count(text), label

    def test_threshold_boundary_below_is_exact_above_is_chunked(self):
        """len == THRESHOLD stays exact; len == THRESHOLD+1 enters the chunked
        branch (over-count >= 0, within one boundary's worth of drift)."""
        at = _prose(_TOKEN_CHUNK_THRESHOLD_DEFAULT)
        assert len(at) == _TOKEN_CHUNK_THRESHOLD_DEFAULT
        assert estimate_tokens(at) == _exact_count(at)

        above = at + " tail"
        got = estimate_tokens(above)
        ref = _chunked_reference(above, _TOKEN_CHUNK_SIZE_DEFAULT)
        assert got == ref  # chunked branch engaged with default knobs
        assert got >= _exact_count(above)  # never smaller (gate c)
        assert got - _exact_count(above) <= 2  # one boundary, at most ~1-2 tokens

    def test_special_token_raise_behavior_preserved_below_threshold(self):
        """Today's behavior preserved byte-for-byte BELOW the threshold:
        encode() raises on a disallowed special token — both through the
        primitive and straight off the encoder (same failure, same input)."""
        text = "hello <|endoftext|> world"
        with pytest.raises(ValueError):
            estimate_tokens(text)
        with pytest.raises(ValueError):
            _exact_count(text)


# ---------------------------------------------------------------------------
# Gate (b): drift <= 0.1% above threshold — EVERY shape
# ---------------------------------------------------------------------------


class TestGateBDriftBound:
    def test_dense_and_realistic_full_matrix_default_knobs(self):
        """Dense (JSON/base64/digits) + realistic (prose/markdown/minified/
        LF-code/CRLF-code/lone-CR-code) corpora at 4/16/64/200 KB, default
        knobs. Below threshold: exact equality; above: drift <= 0.1%."""
        for shape in list(_DENSE) + list(_REALISTIC):
            for size in (4_096, 16_384, 65_536, 200_000):
                text = _corpus(shape, size)
                exact = _exact_count(text)
                got = estimate_tokens(text)
                assert got >= exact, (shape, size)
                if size <= _TOKEN_CHUNK_THRESHOLD_DEFAULT:
                    assert got == exact, (shape, size)
                else:
                    drift_pct = 100.0 * (got - exact) / exact
                    assert drift_pct <= 0.1, (shape, size, got, exact, drift_pct)

    @pytest.mark.parametrize("shape", list(_SYNTHETIC))
    def test_synthetic_shape_ceilings(self, shape):
        """SYNTHETIC shapes at 64 KB with DEFAULT knobs — the largest size
        whose exact reference fits the per-test timeout. The ceiling is the
        COMMISSION bound (0.1%), not a widened lane-local number: under the
        pre-fix plain cut these shapes drifted 1.51% (LF-run) / 3.32%
        (spaces-run) and RED here.

        The 200 KB legs live in the pack (tier_b_token_drift_bound_unit_test.sh).
        """
        text = _corpus(shape, 65_536)
        exact = _exact_count(text)
        got = estimate_tokens(text)
        assert got >= exact, shape
        drift_pct = 100.0 * (got - exact) / exact
        assert drift_pct <= 0.1, (shape, got, exact, drift_pct)

    def test_absolute_over_count_bound(self):
        """THE DEEP-DIVE-SANCTIONED BOUND: the over-count is <= n/CHUNK tokens
        (<= ~1 token per boundary) on every shape — synthetic AND realistic.

        This replaces the pre-fix lane pin (``boundaries * 3 + 2``), which was
        NOT deep-dive-sanctioned and masked the LF-run breach (the plain cut
        pays ~2 tokens per boundary on a whitespace-run corpus).
        """
        cases = [
            ("lf_run", "\n" * 32_768),
            ("spaces_run", " " * 32_768),
            ("letters_run", "q" * 32_768),
            ("symbols_run", "!" * 32_768),
            ("prose", _prose(32_768)),
            ("dense_json", _dense_json(32_768)),
            ("base64", _base64ish(32_768)),
            ("minified", _minified(32_768)),
            ("lf_code", _code(32_768, "\n")),
            ("crlf_code", _code(32_768, "\r\n")),
            ("lone_cr_code", _code(32_768, "\r")),
        ]
        for shape, text in cases:
            exact = _exact_count(text)
            got = estimate_tokens(text)
            boundaries = -(-len(text) // _TOKEN_CHUNK_SIZE_DEFAULT)
            assert got >= exact, shape
            assert got - exact <= boundaries, (
                shape, got, exact, got - exact, boundaries
            )

    def test_boundary_semantics_shrink_drift_to_zero_for_word_text(self):
        """The 'skip the boundary char into the next chunk' rule: for
        space-separated words the chunk boundaries reproduce the exact
        piece shape, so word text drifts by at most ~1 token per boundary
        (proportionally tiny)."""
        text = " ".join(f"word{i:04d}" for i in range(6_000))
        assert len(text) > _TOKEN_CHUNK_THRESHOLD_DEFAULT
        got = estimate_tokens(text)
        exact = _exact_count(text)
        assert got >= exact
        drift_pct = 100.0 * (got - exact) / exact
        assert drift_pct <= 0.1, (got, exact, drift_pct)


# ---------------------------------------------------------------------------
# Gate (c): direction — chunked >= exact ALWAYS; chunker invariants
# ---------------------------------------------------------------------------


class TestGateCDirectionAndChunker:
    def test_direction_over_mixed_corpus_small_knobs(self):
        """Deterministic mixed corpus, tiny knobs (threshold=64, chunk=16):
        chunked >= exact on every member, including shapes engineered to
        straddle boundaries (word cadence, code, blank lines, unicode)."""
        mixed = [
            "",
            "a",
            "word word word word word word word word",
            "no-whitespace-unbroken-letters-run",
            "!!!!!!!!!***********",
            "   \n\n   \t\t   ",
            "line one\nline two\r\nline three",
            "漢字漢字漢字 漢字漢字漢字",
            '{"a":1,"b":[1,2,3],"c":"x"}',
            "x" * 1_000,
            _prose(500),
        ]
        for text in mixed:
            got = estimate_tokens(text)
            exact = _exact_count(text)
            assert got >= exact, (text[:40], got, exact)
            assert got >= 0

    def test_chunker_invariants(self):
        """Each chunk <= chunk_size; concatenation == original; every split
        position follows ONE of the three sanctioned rules:
          (1) plain carry-into-next-chunk — the next chunk starts ON the
              boundary whitespace char;
          (2) SHAPE-AWARE cut — the next chunk starts right AFTER a
              whitespace run of >= 16 identical chars (the run's last char
              stays in the closed chunk);
          (3) hard split — the closed chunk was a full window (an unbounded
              single-piece run has no whitespace to cut at).
        """
        text = _prose(10_000)
        chunks = list(_iter_count_chunks(text, 512))
        assert all(len(c) <= 512 for c in chunks)
        assert "".join(chunks) == text
        pos = 0
        for cur, nxt in zip(chunks, chunks[1:]):
            pos += len(cur)
            plain = text[pos].isspace()
            hard = len(cur) == 512
            shape_aware = (
                pos > 0
                and text[pos - 1].isspace()
                and _run_len_at(text, pos - 1, start=pos - len(cur)) >= _WHITESPACE_RUN_MIN
            )
            assert plain or hard or shape_aware, (pos, cur[-20:], nxt[:20])

        unbroken = "q" * 2_000
        uchunks = list(_iter_count_chunks(unbroken, 512))
        assert "".join(uchunks) == unbroken
        assert all(len(c) <= 512 for c in uchunks)
        assert all(c == "q" * 512 for c in uchunks[:-1])  # hard splits
        assert uchunks[-1] == "q" * (2_000 % 512 or 512)  # remainder tail

        # degenerate sizes terminate and preserve content
        assert list(_iter_count_chunks("", 512)) == []
        assert list(_iter_count_chunks("x", 512)) == ["x"]
        assert "".join(_iter_count_chunks("a b\nc  \n\nd", 2)) == "a b\nc  \n\nd"

    def test_shape_aware_cut_keeps_long_whitespace_runs_whole(self):
        """The shape-aware rule itself: when the boundary char TERMINATES a
        run of >= 16 IDENTICAL whitespace chars, the run's last char stays
        in the closed chunk (the run is NOT split); a SHORT run keeps the
        plain carry-into-next-chunk rule (the boundary char leads the next
        chunk)."""
        # long run (64 spaces) terminating at the window's last whitespace
        text = "b" * 100 + " " * 64 + "a" * 5_000
        chunks = list(_iter_count_chunks(text, 4096))
        assert "".join(chunks) == text
        assert chunks[0].endswith(" " * 64)  # the long run stays whole
        assert not chunks[1].startswith(" ")  # rule (2), not rule (1)

        # short run (4 spaces) -> plain carry-into-next-chunk rule
        text = "b" * 100 + " " * 4 + "a" * 5_000
        chunks = list(_iter_count_chunks(text, 4096))
        assert "".join(chunks) == text
        assert chunks[0].endswith(" " * 3)  # the boundary char was carried on
        assert chunks[1].startswith(" ")  # into the NEXT chunk

    def test_chunk_size_and_threshold_knobs_respected(self, monkeypatch):
        """Env knobs are read at call time: tiny threshold sends SHORT text
        down the chunked branch; tiny chunk changes the sum accordingly."""
        text = _prose(2_000)
        monkeypatch.setenv("ENSEMBLE_TOKEN_COUNT_THRESHOLD", "64")
        monkeypatch.setenv("ENSEMBLE_TOKEN_COUNT_CHUNK", "128")
        assert estimate_tokens(text) == _chunked_reference(text, 128)
        assert estimate_tokens(text) >= _exact_count(text)

    def test_knob_garbage_falls_back_to_defaults(self, monkeypatch):
        """Garbage / non-positive knob values fall back to the defaults:
        a 2 KB text stays EXACT (below the effective 16K default threshold)."""
        text = _prose(2_000)
        for garbage in ("abc", "0", "-5", "12.5", ""):
            monkeypatch.setenv("ENSEMBLE_TOKEN_COUNT_THRESHOLD", garbage)
            monkeypatch.setenv("ENSEMBLE_TOKEN_COUNT_CHUNK", garbage)
            assert estimate_tokens(text) == _exact_count(text), garbage


def _run_len_at(text: str, idx: int, start: int) -> int:
    """Length of the run of IDENTICAL chars ending at ``idx`` (bounded by
    ``start``) — mirrors the bounded back-scan in ``_iter_count_chunks``."""
    run = 1
    k = idx - 1
    while k > start and text[k] == text[idx]:
        run += 1
        k -= 1
    return run


# ---------------------------------------------------------------------------
# Gate (d): timing (loose unit guard; the strict <1s gate lives in the pack)
# ---------------------------------------------------------------------------


class TestGateDTiming:
    def test_200kb_unbroken_chunked_is_fast(self):
        """200 KB unbroken letter run through the DEFAULT policy path.
        Exact today: ~27.05 s (tester re-run, off-loop audit). Chunked with
        C=4 KB: model predicts ~0.55 s. Strict <1s gate lives in the pack;
        this guard carries headroom for loaded runners."""
        text = "q" * 200_000
        t0 = time.perf_counter()
        got = estimate_tokens(text)
        elapsed = time.perf_counter() - t0
        assert got > 0
        assert elapsed < 2.0, elapsed


# ---------------------------------------------------------------------------
# Gate (e): kill-switch restores the exact path
# ---------------------------------------------------------------------------


class TestGateEKillSwitch:
    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
    def test_kill_switch_truthy_restores_exact(self, monkeypatch, value):
        monkeypatch.setenv("ENSEMBLE_TOKEN_COUNT_EXACT", value)
        for text in (_prose(20_000), "q" * 17_000):
            assert estimate_tokens(text) == _exact_count(text), value

    @pytest.mark.parametrize("value", ["0", "false", "", "off"])
    def test_kill_switch_falsy_keeps_policy_default(self, monkeypatch, value):
        """Falsy/absent => policy ON: above-threshold text goes chunked."""
        monkeypatch.setenv("ENSEMBLE_TOKEN_COUNT_EXACT", value)
        text = _prose(20_000)
        got = estimate_tokens(text)
        assert got == _chunked_reference(text, _TOKEN_CHUNK_SIZE_DEFAULT)
        assert got >= _exact_count(text)


# ---------------------------------------------------------------------------
# STRUCTURAL pin: the primitive encodes each character AT MOST ONCE
# ---------------------------------------------------------------------------


class _SpyEncoder:
    """Wraps a real encoder and records every encode() call."""

    def __init__(self, inner):
        self._inner = inner
        self.calls = 0
        self.chars = 0

    def encode(self, text, *args, **kwargs):
        self.calls += 1
        self.chars += len(text)
        return self._inner.encode(text, *args, **kwargs)


class TestStructuralNoReEncode:
    def test_primitive_encodes_each_character_at_most_once(self, monkeypatch):
        """STRUCTURAL mutation pin: the chunked path must encode each
        character of the payload AT MOST ONCE (sum of chunk lengths == len
        text). Re-adding a whole-list encode INSIDE the chunk loop REDs
        this immediately — no wall-clock timeout involved."""
        spy = _SpyEncoder(_ENCODER)
        monkeypatch.setattr(tiktoken, "get_encoding", lambda name: spy)
        for shape in ("lf_run", "spaces_run", "prose", "dense_json"):
            text = _corpus(shape, 20_000)
            spy.calls = 0
            spy.chars = 0
            estimate_tokens(text)
            assert spy.chars == len(text), (shape, spy.chars, len(text))
            expected_calls = len(list(_iter_count_chunks(text, _TOKEN_CHUNK_SIZE_DEFAULT)))
            assert spy.calls == expected_calls, (shape, spy.calls, expected_calls)


# ---------------------------------------------------------------------------
# Inheritance: the primitive's consumers pick the branch up unchanged
# ---------------------------------------------------------------------------


class TestConsumerInheritance:
    def test_estimate_messages_tokens_inherits_chunked_branch(self):
        """estimate_messages_tokens counts message content THROUGH the
        primitive: a >threshold message is counted chunked (+4 role
        overhead), proving the blast-radius claim with zero edits there."""
        from types import SimpleNamespace

        content = _prose(20_000)
        total = estimate_messages_tokens([SimpleNamespace(content=content)])
        expected = _chunked_reference(content, _TOKEN_CHUNK_SIZE_DEFAULT) + 4
        assert total == expected
        assert total >= _exact_count(content) + 4

    def test_estimate_messages_tokens_below_threshold_unchanged(self):
        from types import SimpleNamespace

        content = _prose(1_000)
        total = estimate_messages_tokens([SimpleNamespace(content=content)])
        assert total == _exact_count(content) + 4

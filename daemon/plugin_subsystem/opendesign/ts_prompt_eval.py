"""Bounded TypeScript prompt-source evaluator (Mode-P Provider — slice ⑤ F1 fix).

The vendored OD prompt sources (``snapshot_with_drift_alarm/prompts/``)
are TypeScript modules whose exported string constants are the bodies
the compose chain embeds (``DISCOVERY_AND_PHILOSOPHY``,
``OFFICIAL_DESIGNER_PROMPT``, ``DECK_FRAMEWORK_DIRECTIVE``,
``MEDIA_GENERATION_CONTRACT``). The slice-⑤ regex extractor
(``_TS_STRING_RE``) stopped at the FIRST closing backtick — including
escaped backticks (``\\``` ) inside the template literals — so every
composed prompt was materially truncated and the deck kind degraded to
embedding the entire raw TS blob.

This module replaces the regex with a real scanner + bounded evaluator
that reproduces the RUNTIME string TypeScript/Node evaluates the
vendored source to:

- a state-machine template-literal scanner (in-literal state, ``\\``
  escapes never terminate the literal, ``${…}`` substitution spans incl.
  brace nesting, nested templates, plain-quote strings);
- JS escape decoding per ECMA-262 template-literal semantics
  (``\\\\ \\` \\$ \\n \\t \\r \\b \\f \\v \\0 \\xHH \\uHHHH \\u{…}``,
  line continuations, CR/LF normalization);
- a bounded substitution evaluator (identifier consts — same module or
  re-exported via parsed imports — plus ``JSON.stringify(<const>)``);
- Python mirrors of the THREE upstream builder functions the targets
  reference (``renderDirectionSpecBlock`` in directions.ts,
  ``renderDeckHandoff`` / ``renderDeckFrameworkDirective`` in
  deck-framework.ts) and a bounded object-literal parser for the
  ``DESIGN_DIRECTIONS`` data array they iterate.

Provenance of the mirrors: they are line-faithful re-expressions of the
functions in the vendored snapshot @ ``open-design-v0.23.0`` (the same
pin the byte-fidelity test enforces). The upstream modules are covered
by the snapshot-with-drift-alarm machinery; when a sync-runner pull
lands upstream prompt changes, regenerate the runtime fixtures
(``tests/unit/plugin_subsystem/fixtures/od_prompt_runtime/``) and
reconcile the mirrors in the same change.

Failure policy (the F1 lesson — silent degradation is the defect):
extraction is LOUD. A source that parses but yields an unsupported
construct raises :class:`TsPromptEvalError`; the compose chain surfaces
it as a ``prompt_composition_failed`` envelope instead of embedding a
truncated or raw-blob prompt. Only a genuinely ABSENT source file
degrades (empty string — the documented missing-snapshot behavior the
existing tests rely on).

Reference bytes: ``tests/unit/plugin_subsystem/fixtures/od_prompt_runtime/``
(node-evaluated runtime strings — differential oracle for the corpus
tests in ``test_opendesign_prompt_extraction.py``).
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

__all__ = [
    "TsPromptEvalError",
    "extract_symbol_from_source",
    "TsPromptSource",
]


class TsPromptEvalError(Exception):
    """A vendored prompt source could not be evaluated faithfully.

    Raised (never silently degraded) when the scanner or the bounded
    evaluator meets a construct outside the supported subset — the
    compose chain turns this into a typed ``prompt_composition_failed``
    envelope rather than embedding a corrupt prompt.
    """


# ---------------------------------------------------------------------------
# Low-level scanner (state machine; escape-aware)
# ---------------------------------------------------------------------------

_SIMPLE_ESCAPES = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "b": "\b",
    "f": "\f",
    "v": "\v",
    "0": "\0",
    "`": "`",
    "'": "'",
    '"': '"',
    "\\": "\\",
    "$": "$",
}
_HEX_DIGITS = set("0123456789abcdefABCDEF")


def _decode_escape_body(src: str, i: int) -> Tuple[str, int]:
    """Decode one escape sequence whose backslash is at ``src[i]``.

    Returns ``(decoded_text, next_index)`` per ECMA-262 escape
    semantics (shared by template literals and quote strings; the
    caller handles the quote-string-only ``\\'`` / ``\\"`` forms, which
    land in :data:`_SIMPLE_ESCAPES` anyway). A ``\\`` before a
    line terminator is a line continuation → empty output.
    """
    if i + 1 >= len(src):
        raise TsPromptEvalError("dangling backslash at end of source")
    ch = src[i + 1]
    if ch == "\r":
        # \<CR><LF> / \<CR> — line continuation, eats the whole terminator.
        if i + 2 < len(src) and src[i + 2] == "\n":
            return "", i + 3
        return "", i + 2
    if ch == "\n":
        return "", i + 2
    if ch == "x":
        hexpart = src[i + 2 : i + 4]
        if len(hexpart) == 2 and all(c in _HEX_DIGITS for c in hexpart):
            return chr(int(hexpart, 16)), i + 4
        raise TsPromptEvalError(f"malformed \\xHH escape at offset {i}")
    if ch == "u":
        if i + 2 < len(src) and src[i + 2] == "{":
            end = src.find("}", i + 3)
            if end < 0:
                raise TsPromptEvalError(f"malformed \\u{{…}} escape at offset {i}")
            hexpart = src[i + 3 : end]
            if not hexpart or any(c not in _HEX_DIGITS for c in hexpart):
                raise TsPromptEvalError(f"malformed \\u{{…}} escape at offset {i}")
            return chr(int(hexpart, 16)), end + 1
        hexpart = src[i + 2 : i + 6]
        if len(hexpart) == 4 and all(c in _HEX_DIGITS for c in hexpart):
            return chr(int(hexpart, 16)), i + 6
        raise TsPromptEvalError(f"malformed \\uHHHH escape at offset {i}")
    if ch in _SIMPLE_ESCAPES:
        return _SIMPLE_ESCAPES[ch], i + 2
    # NonEscapeCharacter — the character itself.
    return ch, i + 2


def _skip_comment(src: str, i: int) -> int:
    """Skip one ``//`` or ``/* */`` comment; ``src[i]`` is the opener."""
    if src.startswith("//", i):
        end = src.find("\n", i)
        return len(src) if end < 0 else end  # leave the newline itself
    if src.startswith("/*", i):
        end = src.find("*/", i + 2)
        if end < 0:
            raise TsPromptEvalError(f"unterminated block comment at offset {i}")
        return end + 2
    raise TsPromptEvalError(f"not a comment at offset {i}")


def _skip_ws_and_comments(src: str, i: int) -> int:
    n = len(src)
    while i < n:
        ch = src[i]
        if ch in " \t\r\n":
            i += 1
        elif src.startswith("//", i) or src.startswith("/*", i):
            i = _skip_comment(src, i)
        else:
            break
    return i


def _scan_template_literal(src: str, i: int) -> Tuple[List[Tuple[str, str]], int]:
    """Scan a template literal starting at ``src[i] == '`'``.

    Returns ``(parts, end_index_after_closing_backtick)`` where parts
    alternate ``("text", <raw-text-with-escapes>)`` and
    ``("subst", <substitution-expression-source>)``.
    """
    if i >= len(src) or src[i] != "`":
        raise TsPromptEvalError(f"expected template literal at offset {i}")
    parts: List[Tuple[str, str]] = []
    buf: List[str] = []
    j = i + 1
    n = len(src)
    while j < n:
        ch = src[j]
        if ch == "\\":
            buf.append(src[j : j + 2])
            j += 2
            continue
        if ch == "`":
            if buf:
                parts.append(("text", "".join(buf)))
            return parts, j + 1
        if ch == "$" and j + 1 < n and src[j + 1] == "{":
            if buf:
                parts.append(("text", "".join(buf)))
                buf = []
            expr, j2 = _scan_substitution(src, j + 2)
            parts.append(("subst", expr))
            j = j2
            continue
        if ch == "\r":
            # CR / CRLF normalize to LF inside template literals.
            buf.append("\n")
            j += 2 if (j + 1 < n and src[j + 1] == "\n") else 1
            continue
        buf.append(ch)
        j += 1
    raise TsPromptEvalError(f"unterminated template literal starting at offset {i}")


def _scan_substitution(src: str, i: int) -> Tuple[str, int]:
    """Scan a ``${ … }`` substitution body starting after ``${``.

    Tracks brace depth and skips over strings / templates / comments so
    braces inside them never miscount. Returns ``(expr_source,
    index_after_closing_brace)``.
    """
    start = i
    depth = 1
    j = i
    n = len(src)
    while j < n:
        ch = src[j]
        if ch == "/" and src.startswith("//", j):
            j = _skip_comment(src, j)
            continue
        if ch == "/" and src.startswith("/*", j):
            j = _skip_comment(src, j)
            continue
        if ch in ("'", '"'):
            _, j = _scan_quote_string(src, j)
            continue
        if ch == "`":
            _, j = _scan_template_literal(src, j)
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[start:j], j + 1
        j += 1
    raise TsPromptEvalError(f"unterminated substitution starting at offset {start - 2}")


def _scan_quote_string(src: str, i: int) -> Tuple[str, int]:
    """Scan a ``'…'`` / ``"…"`` string starting at ``src[i]``.

    Returns ``(decoded_value, index_after_closing_quote)``.
    """
    quote = src[i]
    j = i + 1
    n = len(src)
    out: List[str] = []
    while j < n:
        ch = src[j]
        if ch == "\\":
            text, j = _decode_escape_body(src, j)
            out.append(text)
            continue
        if ch == quote:
            return "".join(out), j + 1
        if ch == "\r":
            out.append("\n")
            j += 2 if (j + 1 < n and src[j + 1] == "\n") else 1
            continue
        out.append(ch)
        j += 1
    raise TsPromptEvalError(f"unterminated string literal at offset {i}")


def _decode_template_text(raw: str) -> str:
    """Decode the raw text parts of a template literal (escapes + CR/LF)."""
    out: List[str] = []
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        if ch == "\\":
            text, i = _decode_escape_body(raw, i)
            out.append(text)
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# ---------------------------------------------------------------------------
# Bounded literal-value parser (const inits + DESIGN_DIRECTIONS shapes)
# ---------------------------------------------------------------------------


def _parse_literal(src: str, i: int) -> Tuple[Any, int]:
    """Parse a bounded JS literal: template / quote string / number /
    ``[ … ]`` array / ``{ … }`` object / ``true`` / ``false`` / ``null``.

    Object keys are bare identifiers or quote strings; values are
    nested bounded literals. ``//`` comments and trailing commas are
    accepted (the vendored sources use them inside ``DESIGN_DIRECTIONS``).
    Returns ``(python_value, next_index)``.
    """
    i = _skip_ws_and_comments(src, i)
    if i >= len(src):
        raise TsPromptEvalError("unexpected end of source in literal")
    ch = src[i]
    if ch == "`":
        parts, end = _scan_template_literal(src, i)
        substs = [t for kind, t in parts if kind == "subst"]
        if substs:
            raise TsPromptEvalError(
                f"template literal with substitutions is not a bounded literal "
                f"(first: {substs[0][:60]!r})"
            )
        return _decode_template_text("".join(t for kind, t in parts if kind == "text")), end
    if ch in ("'", '"'):
        return _scan_quote_string(src, i)
    if ch in "-0123456789":
        m = re.match(r"-?\d+(?:\.\d+)?", src[i:])
        if not m:
            raise TsPromptEvalError(f"malformed number at offset {i}")
        text = m.group(0)
        return (float(text) if "." in text else int(text)), i + len(text)
    if ch == "[":
        items: List[Any] = []
        j = _skip_ws_and_comments(src, i + 1)
        while j < len(src) and src[j] != "]":
            value, j = _parse_literal(src, j)
            items.append(value)
            j = _skip_ws_and_comments(src, j)
            if src.startswith(",", j):
                j += 1
                j = _skip_ws_and_comments(src, j)
            elif src[j] != "]":
                raise TsPromptEvalError(f"expected ',' or ']' at offset {j}")
        return items, j + 1
    if ch == "{":
        obj: Dict[str, Any] = {}
        j = _skip_ws_and_comments(src, i + 1)
        while j < len(src) and src[j] != "}":
            if src[j] in ("'", '"'):
                key, j = _scan_quote_string(src, j)
            else:
                m = re.match(r"[A-Za-z_$][\w$]*", src[j:])
                if not m:
                    raise TsPromptEvalError(f"malformed object key at offset {j}")
                key = m.group(0)
                j += len(key)
            j = _skip_ws_and_comments(src, j)
            if j >= len(src) or src[j] != ":":
                raise TsPromptEvalError(f"expected ':' after key {key!r} at offset {j}")
            value, j = _parse_literal(src, j + 1)
            obj[key] = value
            j = _skip_ws_and_comments(src, j)
            if src.startswith(",", j):
                j += 1
                j = _skip_ws_and_comments(src, j)
            elif src[j] != "}":
                raise TsPromptEvalError(f"expected ',' or '}}' at offset {j}")
        return obj, j + 1
    m = re.match(r"true|false|null", src[i:])
    if m:
        text = m.group(0)
        return {"true": True, "false": False, "null": None}[text], i + len(text)
    raise TsPromptEvalError(f"unsupported literal at offset {i}: {src[i:i+24]!r}")


# ---------------------------------------------------------------------------
# Module model — top-level declarations, imports, lazy symbol evaluation
# ---------------------------------------------------------------------------

_IDENT_RE = re.compile(r"[A-Za-z_$][\w$]*")
_KEYWORDS = {"const", "let", "var", "function", "class", "interface", "type", "enum", "import", "export", "declare", "async"}


class _TsModule:
    """One vendored TS module: parsed imports + lazily-evaluated consts."""

    def __init__(self, source: str, module_key: str, registry: "TsPromptSource") -> None:
        self.source = source
        self.key = module_key
        self._registry = registry
        self.imports: Dict[str, Tuple[str, str]] = {}  # local name → (module key, remote name)
        self._const_decls: Dict[str, int] = {}  # name → init offset
        self._cache: Dict[str, Any] = {}
        self._evaluating: set[str] = set()
        self._parse()

    # -- parsing -----------------------------------------------------------

    def _parse(self) -> None:
        src = self.source
        n = len(src)
        i = 0
        while True:
            i = _skip_ws_and_comments(src, i)
            if i >= n:
                break
            j = i
            if src.startswith("export ", j):
                j = _skip_ws_and_comments(src, j + len("export "))
                if src.startswith("default ", j):
                    raise TsPromptEvalError(
                        f"{self.key}: 'export default' is outside the supported subset"
                    )
            m = _IDENT_RE.match(src, j)
            if not m:
                # Not a declaration start (e.g. stray semicolon); skip it.
                i = j + 1
                continue
            kw = m.group(0)
            if kw == "import":
                i = self._parse_import(j)
                continue
            if kw in ("const", "let", "var"):
                i = self._parse_const(j)
                continue
            # function/class/interface/type/enum/declare — bodies are never
            # evaluated from TS source (builders are mirrored in Python);
            # skip to the matching close of the declaration.
            i = self._skip_declaration(j)

    def _parse_import(self, i: int) -> int:
        m = re.compile(r"import\s+(?:type\s+)?(?:\{([^}]*)\}|(\w+))\s*from\s*['\"]([^'\"]+)['\"]\s*;", re.DOTALL).match(
            self.source, i
        )
        if not m:
            raise TsPromptEvalError(
                f"{self.key}: unsupported import form at offset {i} "
                f"(supported: named or default single-symbol imports)"
            )
        spec = m.group(3)
        if spec.startswith("."):
            target_key: Optional[str] = self._registry.resolve_specifier(self.key, spec)
        else:
            # Bare specifier (package import, e.g. a type-only module):
            # registered but only fails if a const actually references it.
            target_key = None
        if m.group(1):
            for raw_name in m.group(1).split(","):
                name = raw_name.strip()
                if not name:
                    continue
                if " as " in name:
                    local, remote = (p.strip() for p in name.split(" as ", 1))
                else:
                    local = remote = name
                if local.startswith("type "):  # import type { X }
                    local = local[len("type ") :].strip()
                    continue
                self.imports[local] = (target_key, remote)
        else:
            self.imports[m.group(2)] = (target_key, "default")
        return m.end()

    def _parse_const(self, i: int) -> int:
        src = self.source
        j = _skip_ws_and_comments(src, i + len("const"))
        m = _IDENT_RE.match(src, j)
        if not m:
            raise TsPromptEvalError(f"{self.key}: malformed const at offset {i}")
        name = m.group(0)
        # Skip any type annotation: scan to the first top-level '='.
        k = m.end()
        depth = 0
        while k < len(src):
            ch = src[k]
            if ch in ("'", '"'):
                _, k = _scan_quote_string(src, k)
                continue
            if ch == "`":
                _, k = _scan_template_literal(src, k)
                continue
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif ch == "=" and depth == 0 and not src.startswith("==", k):
                break
            k += 1
        self._const_decls[name] = k + 1
        # Skip the init (lazily evaluated) up to the statement-terminating ';'.
        k += 1
        depth = 0
        while k < len(src):
            ch = src[k]
            if ch in ("'", '"'):
                _, k = _scan_quote_string(src, k)
                continue
            if ch == "`":
                _, k = _scan_template_literal(src, k)
                continue
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif ch == ";" and depth == 0:
                return k + 1
            k += 1
        raise TsPromptEvalError(f"{self.key}: unterminated const {name!r}")

    def _skip_declaration(self, i: int) -> int:
        """Skip a non-const declaration (function/class/interface/…).

        For ``function``/``class``: skip the balanced ``{…}`` body.
        For ``interface``/``type``/``declare``: skip to the terminating
        ``;`` or balanced braces at depth 0.
        """
        src = self.source
        m = _IDENT_RE.match(src, i)
        kw = m.group(0) if m else ""
        # Skip name + any generic parameters / parens up to the first
        # top-level '{' or ';'.
        j = i
        depth = 0
        while j < len(src):
            ch = src[j]
            if ch in ("'", '"'):
                _, j = _scan_quote_string(src, j)
                continue
            if ch == "`":
                _, j = _scan_template_literal(src, j)
                continue
            if ch == "{":
                depth += 1
                if depth == 1:
                    j += 1
                    while j < len(src) and depth:
                        ch2 = src[j]
                        if ch2 in ("'", '"'):
                            _, j = _scan_quote_string(src, j)
                        elif ch2 == "`":
                            _, j = _scan_template_literal(src, j)
                        elif ch2 == "{":
                            depth += 1
                            j += 1
                        elif ch2 == "}":
                            depth -= 1
                            j += 1
                        else:
                            j += 1
                    return j
            elif ch == ";" and depth == 0:
                return j + 1
            j += 1
        return j

    # -- evaluation --------------------------------------------------------

    def value_of(self, name: str) -> Any:
        if name in self._cache:
            return self._cache[name]
        if name not in self._const_decls:
            # Imported?
            if name in self.imports:
                target_key, remote = self.imports[name]
                if target_key is None:
                    raise TsPromptEvalError(
                        f"{self.key}: {name!r} is imported from a bare (package) "
                        f"specifier — outside the supported evaluation subset"
                    )
                return self._registry.module(target_key).value_of(remote)
            raise TsPromptEvalError(f"{self.key}: symbol {name!r} not found in module")
        if name in self._evaluating:
            raise TsPromptEvalError(f"{self.key}: cyclic evaluation of {name!r}")
        self._evaluating.add(name)
        try:
            value = self._eval_init(self._const_decls[name])
        finally:
            self._evaluating.discard(name)
        self._cache[name] = value
        return value

    def _eval_init(self, i: int) -> Any:
        i = _skip_ws_and_comments(self.source, i)
        ch = self.source[i] if i < len(self.source) else ""
        if ch == "`":
            return self._eval_template(i)[0]
        if ch in ("'", '"'):
            return _scan_quote_string(self.source, i)[0]
        if ch in "[{":
            value, end = _parse_literal(self.source, i)
            # Allow an `as const` suffix after array/object literals.
            i = _skip_ws_and_comments(self.source, end)
            return value
        m = re.match(r"-?\d+(?:\.\d+)?", self.source[i:])
        if m:
            return json.loads(m.group(0))
        m = _IDENT_RE.match(self.source, i)
        if m:
            name = m.group(0)
            rest = self.source[m.end() :].lstrip()
            if rest.startswith("("):
                # Call form: the only sanctioned initializers are the
                # mirrored upstream builders.
                args, _ = self._parse_call_args(m.end() + len(rest) - len(rest.lstrip()) + 1)
                return self._call_builder(name, args)
            # `X as const` style or a plain identifier reference.
            return self.value_of(name)
        raise TsPromptEvalError(
            f"{self.key}: unsupported const initializer at offset {i}: {self.source[i:i+40]!r}"
        )

    def _parse_call_args(self, i: int) -> Tuple[List[Any], int]:
        """Parse a bounded ``(<literal>, …)`` argument list after ``(``."""
        args: List[Any] = []
        j = _skip_ws_and_comments(self.source, i)
        if self.source[j] == ")":
            return args, j + 1
        while True:
            value, j = _parse_literal(self.source, j)
            args.append(value)
            j = _skip_ws_and_comments(self.source, j)
            if self.source[j] == ",":
                j = _skip_ws_and_comments(self.source, j + 1)
                continue
            if self.source[j] == ")":
                return args, j + 1
            raise TsPromptEvalError(
                f"{self.key}: malformed call arguments at offset {j}"
            )

    def _call_builder(self, name: str, args: List[Any]) -> Any:
        # A call to an imported builder dispatches on the DEFINING module
        # (e.g. discovery.ts calls directions.ts's renderDirectionSpecBlock).
        key = self.key
        if name in self.imports:
            target_key, _remote = self.imports[name]
            if target_key is not None:
                key = target_key
        dispatched = self._registry.call_builder(key, name, args)
        if dispatched is _NOT_MIRRORED:
            raise TsPromptEvalError(
                f"{self.key}: call {name}({len(args)} arg(s)) has no mirrored "
                f"builder — extend TsPromptSource.call_builder if a compose "
                f"target legitimately calls it"
            )
        return dispatched

    def _eval_template(self, i: int) -> Tuple[str, int]:
        parts, end = _scan_template_literal(self.source, i)
        out: List[str] = []
        for kind, payload in parts:
            if kind == "text":
                out.append(_decode_template_text(payload))
            else:
                out.append(self._eval_substitution(payload))
        return "".join(out), end

    def _eval_substitution(self, expr: str) -> str:
        expr = expr.strip()
        m = re.match(r"JSON\.stringify\(\s*([A-Za-z_$][\w$]*)\s*\)$", expr, re.DOTALL)
        if m:
            value = self._resolve_name(m.group(1))
            # JSON.stringify has no spaces after separators (unlike json.dumps).
            return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
        m = re.match(r"([A-Za-z_$][\w$]*)\(\s*\)$", expr, re.DOTALL)
        if m:
            value = self._call_builder(m.group(1), [])
            if not isinstance(value, str):
                raise TsPromptEvalError(
                    f"{self.key}: substitution {expr!r} evaluated to a non-string"
                )
            return value
        m = _IDENT_RE.match(expr)
        if m and m.group(0) == expr:
            value = self._resolve_name(expr)
            if isinstance(value, str):
                return value
            if isinstance(value, (int, float)):
                return json.dumps(value)
            raise TsPromptEvalError(
                f"{self.key}: substitution {expr!r} evaluated to a non-string "
                f"({type(value).__name__}) — outside the supported subset"
            )
        raise TsPromptEvalError(
            f"{self.key}: unsupported substitution expression: {expr[:120]!r}"
        )

    def _resolve_name(self, name: str) -> Any:
        return self.value_of(name)


_NOT_MIRRORED = object()


# ---------------------------------------------------------------------------
# Registry — module keys, specifier resolution, mirrored builders
# ---------------------------------------------------------------------------


class TsPromptSource:
    """Loads vendored TS prompt modules and evaluates their exported strings.

    ``reader(relpath)`` returns the module source for a module key
    (relative to the prompts root — ``"discovery.ts"``,
    ``"runtime/deck-protocol.ts"``, …) or ``""`` when absent (the
    documented missing-snapshot degradation).
    """

    def __init__(self, reader: Callable[[str], str]) -> None:
        self._reader = reader
        self._modules: Dict[str, Optional[_TsModule]] = {}

    # -- module access -----------------------------------------------------

    def module(self, key: str) -> Optional[_TsModule]:
        if key in self._modules:
            return self._modules[key]
        source = self._reader(key)
        if not source:
            self._modules[key] = None
            return None
        mod = _TsModule(source, key, self)
        self._modules[key] = mod
        return mod

    def resolve_specifier(self, from_key: str, spec: str) -> str:
        """Map a relative import specifier to a module key.

        Layout (mirrors the vendored tree): root-level prompt keys
        (``deck-framework.ts``) are read from the ``contracts/`` subdir
        by :func:`_read_prompt_file`, so their relative imports resolve
        as if the module lived at ``contracts/<key>`` — i.e.
        ``'../runtime/deck-protocol.js'`` → ``runtime/deck-protocol.ts``.
        The upstream sources write ``.js`` specifiers (compiled-module
        convention); both spellings map to the same ``.ts`` key.
        """
        if not spec.startswith("."):
            raise TsPromptEvalError(
                f"{from_key}: bare specifier {spec!r} is outside the supported subset"
            )
        spec = spec[:-3] if spec.endswith(".js") else spec
        if not spec.endswith(".ts"):
            spec = spec + ".ts"
        base_parts = from_key.split("/")[:-1]
        if not base_parts:
            # Root-level key: the loader reads it from contracts/.
            base_parts = ["contracts"]
        for part in spec.split("/"):
            if part == ".":
                continue
            if part == "..":
                if not base_parts:
                    raise TsPromptEvalError(
                        f"{from_key}: specifier {spec!r} escapes the prompts root"
                    )
                base_parts.pop()
            else:
                base_parts.append(part)
        return "/".join(base_parts)

    # -- symbol evaluation ---------------------------------------------------

    def symbol(self, module_key: str, name: str) -> Any:
        mod = self.module(module_key)
        if mod is None:
            raise _AbsentModule(module_key)
        return mod.value_of(name)

    def symbol_string(self, module_key: str, name: str) -> str:
        value = self.symbol(module_key, name)
        if not isinstance(value, str):
            raise TsPromptEvalError(
                f"{module_key}: symbol {name!r} is {type(value).__name__}, expected string"
            )
        return value

    # -- mirrored builders ---------------------------------------------------

    def call_builder(self, module_key: str, name: str, args: List[Any]):
        """Dispatch a call to a Python mirror of an upstream builder.

        The mirrors cover exactly the builders reachable from the four
        compose-target symbols; anything else is ``_NOT_MIRRORED`` →
        loud evaluation error.
        """
        if name == "renderDirectionSpecBlock" and module_key.endswith("directions.ts"):
            if args:
                return _NOT_MIRRORED
            return _mirrored_render_direction_spec_block(self)
        if name == "renderDeckHandoff" and module_key.endswith("deck-framework.ts"):
            if len(args) != 1 or not isinstance(args[0], str):
                return _NOT_MIRRORED
            return _mirrored_render_deck_handoff(self, args[0])
        if name == "renderDeckFrameworkDirective" and module_key.endswith("deck-framework.ts"):
            if len(args) != 1 or not isinstance(args[0], str):
                return _NOT_MIRRORED
            body = self.symbol_string("deck-framework.ts", "DECK_FRAMEWORK_BODY")
            return f"{body}\n\n{_mirrored_render_deck_handoff(self, args[0])}"
        return _NOT_MIRRORED


class _AbsentModule(Exception):
    """Internal: the module source is absent (missing-snapshot degradation)."""


def _mirrored_render_deck_handoff(source: TsPromptSource, profile: str) -> str:
    """Mirror of deck-framework.ts renderDeckHandoff (ternary dispatch)."""
    if profile == "text_artifact":
        return source.symbol_string("deck-framework.ts", "DECK_TEXT_ARTIFACT_HANDOFF")
    return source.symbol_string("deck-framework.ts", "DECK_FILESYSTEM_HANDOFF")


def _mirrored_render_direction_spec_block(source: TsPromptSource) -> str:
    """Mirror of directions.ts renderDirectionSpecBlock @ open-design-v0.23.0.

    Line-faithful re-expression of the upstream loop (vendored snapshot
    is hash-pinned; the byte-equality corpus proves the mirror against
    node evaluation).
    """
    directions = source.symbol("directions.ts", "DESIGN_DIRECTIONS")
    if not isinstance(directions, list):
        raise TsPromptEvalError("directions.ts: DESIGN_DIRECTIONS did not parse to a list")
    lines: List[str] = [
        "## Direction library — infer and bind by default",
        "",
        (
            "Each direction below carries a CSS-ready palette (OKLch values) and "
            "font stacks. Infer the best match from the brief and known context, "
            "then bind it without asking. If the user explicitly requested "
            "direction comparison and selected one in a Host-owned direction "
            "form, its answer carries a stable Host `value`, a `foundation` id "
            "from this library, and visual `guidance`. Bind the named foundation "
            "from this library, then apply the guidance as the selected "
            "refinement; the Host value is catalogue identity and must not be "
            "passed to `od tools directions`. Replace the seed template's "
            "`:root` block with the chosen foundation's palette and font stacks "
            "**verbatim** — do not improvise. Posture cues describe how that "
            "direction *behaves* (border weight, radius, accent budget); honour "
            "them in the layout choices."
        ),
        "",
    ]
    for d in directions:
        lines.append(f"### {d['label']}  `(id: {d['id']})`")
        lines.append("")
        lines.append(f"**Mood:** {d['mood']}")
        lines.append("")
        lines.append(f"**References:** {', '.join(d['references'])}.")
        lines.append("")
        lines.append("**Palette (drop into `:root`):**")
        lines.append("")
        lines.append("```css")
        lines.append(":root {")
        lines.append(f"  --bg:      {d['palette']['bg']};")
        lines.append(f"  --surface: {d['palette']['surface']};")
        lines.append(f"  --fg:      {d['palette']['fg']};")
        lines.append(f"  --muted:   {d['palette']['muted']};")
        lines.append(f"  --border:  {d['palette']['border']};")
        lines.append(f"  --accent:  {d['palette']['accent']};")
        lines.append("")
        lines.append(f"  --font-display: {d['displayFont']};")
        lines.append(f"  --font-body:    {d['bodyFont']};")
        if d.get("monoFont"):
            lines.append(f"  --font-mono:    {d['monoFont']};")
        lines.append("}")
        lines.append("```")
        lines.append("")
        lines.append("**Posture:**")
        for p in d["posture"]:
            lines.append(f"- {p}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Single-source extraction (static symbols; the pure-scanner surface)
# ---------------------------------------------------------------------------


def extract_symbol_from_source(source: str, symbol_name: str) -> str:
    """Extract one exported const's RUNTIME string from a single TS source.

    Correct for static (escape-only) templates and quote strings — the
    shape of ``OFFICIAL_DESIGNER_PROMPT``. Cross-module/substitution
    symbols go through :class:`TsPromptSource` (which can resolve
    imports + substitutions). Raises :class:`TsPromptEvalError` when
    the symbol is absent or its evaluation needs unsupported constructs
    — LOUD, never a raw-blob fallback.
    """
    if not source:
        raise TsPromptEvalError("empty source")
    mod = _TsModule(source, "<inline>", _InlineRegistry())
    if symbol_name not in mod._const_decls:
        raise TsPromptEvalError(f"symbol {symbol_name!r} not found in source")
    value = mod.value_of(symbol_name)
    if not isinstance(value, str):
        raise TsPromptEvalError(f"symbol {symbol_name!r} is not a string")
    return value


class _InlineRegistry(TsPromptSource):
    """Registry shim for single-source extraction (no cross-module)."""

    def __init__(self) -> None:  # noqa: D107 - pragma-friendly
        super().__init__(reader=lambda key: "")

    def resolve_specifier(self, from_key: str, spec: str) -> str:  # noqa: D102
        raise TsPromptEvalError("cross-module imports are not available in single-source mode")

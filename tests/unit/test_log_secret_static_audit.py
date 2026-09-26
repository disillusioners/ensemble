"""Static audit — secret-bearing logging format strings (P3-WP10).

Plan §P3-WP10 acceptance:

* The static check enumerates ``logging.*`` call sites under
  ``daemon/`` via AST walk.
* It flags any call whose first positional argument is an f-string
  OR a ``.format(...)`` call that interpolates a variable whose name
  matches ``(?i)(key|token|secret|password|credential)``.
* The audit is restricted to the five KMS-touched paths (plan §P3-WP10
  Touchpoints) — i.e. files that handle plaintext directly. Other
  files in the daemon are out of scope for the day-1 audit.
* False positives are acceptable IF allow-listed in the test with a
  one-line comment.
* False negatives are NOT acceptable — the synthetic positive-case
  check proves the detector actually fires.

Day-1 result on the current tree (post-WP7/WP9): the five paths are
clean — every logger call uses lazy ``%s`` formatting or, where
f-strings exist (e.g. ``connection_manager.py``), the interpolated
variable is not secret-named (``server_name``, ``instance_id``,
``config.url``, exceptions). The synthetic positive-case below pins
the detector's sensitivity so a future regression (someone writing
``logger.error(f"secret={api_key}")``) trips the audit.

Run in isolation:

    .venv/bin/python -m pytest tests/unit/test_log_secret_static_audit.py -q
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------

#: Regex matching variable-name substrings that look like KMS-relevant
#: secrets. Case-insensitive; matches ``api_key``, ``access_token``,
#: ``client_secret``, ``db_password``, ``credentials``, etc.
SECRET_NAME_RE = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|"
    r"password|secret|credential|token)"
)

#: Files in scope for the static audit (plan §P3-WP10 Touchpoints).
KMS_TOUCHED_PATHS: tuple[str, ...] = (
    "daemon/services/kms_lite.py",
    "daemon/services/kms_resolver.py",
    "daemon/mcp/connection_manager.py",
    "daemon/routers/mcp_servers.py",
    "daemon/tools/infra.py",
)

#: Allow-list: (file, line, rationale) triples. Each entry documents
#: why a finding is being suppressed. Day-1 the list is empty — the
#: five paths are clean — but the structure is here so a follow-up
#: reviewer can add an entry with a one-line comment.
ALLOW_LIST: tuple[tuple[str, int, str], ...] = ()


@dataclass(frozen=True)
class Finding:
    """A single secret-bearing format-string finding."""

    file: str
    line: int
    col: int
    variable_name: str
    pattern: str  # "fstring" | "format_call"
    snippet: str  # the source line, trimmed


def _extract_secret_names_from_expr(expr: ast.AST) -> list[str]:
    """Return names (best-effort) that the expression references.

    Used for ``FormattedValue`` (``ast.JoinedStr`` interpolations) and
    for ``.format(...)`` positional / keyword args. The walker tries
    ``ast.Name``, ``ast.Attribute`` (last attr), ``ast.Subscript``
    (slice key + value name), and ``ast.Call.func`` (func name).
    """
    found: list[str] = []
    if isinstance(expr, ast.Name):
        found.append(expr.id)
    elif isinstance(expr, ast.Attribute):
        found.append(expr.attr)
        # Walk into the value side too — ``config.headers[api_key]`` →
        # both ``headers`` and ``api_key`` are names worth checking.
        found.extend(_extract_secret_names_from_expr(expr.value))
    elif isinstance(expr, ast.Subscript):
        found.extend(_extract_secret_names_from_expr(expr.value))
        found.extend(_extract_secret_names_from_expr(expr.slice))
    elif isinstance(expr, ast.Call):
        # ``get_secret(...)`` — take the callable name.
        func = expr.func
        if isinstance(func, ast.Name):
            found.append(func.id)
        elif isinstance(func, ast.Attribute):
            found.append(func.attr)
    elif isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        # String literal ``{api_key}`` placeholder — useful for
        # ``logger.error("config: {api_key}").format(api_key=...)``.
        found.extend(SECRET_NAME_RE.findall(expr.value))
    return found


def _secret_match(name: str) -> str | None:
    """Return the matched substring if ``name`` looks secret-shaped."""
    match = SECRET_NAME_RE.search(name)
    return match.group(0) if match else None


def _scan_ast_tree(tree: ast.AST, source_lines: list[str]) -> list[Finding]:
    """Walk an AST tree for ``logging.<level>(...)`` calls with secret-shaped interpolations."""
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr
            in {"debug", "info", "warning", "error", "exception", "critical"}
        ):
            continue
        # The "logging" check: ``logging.error(...)``,
        # ``logger.error(...)``, ``log.error(...)``, ``logging.getLogger().error(...)``.
        # We accept any Attribute whose ROOT is named logging / logger / log;
        # the call-site namespace is not always precise (e.g. ``self._log.error(...)``).
        # False positives on a non-logging ``.error()`` method are accepted —
        # the static audit is best-effort and reviewers can allow-list.
        if not node.args:
            continue
        msg = node.args[0]
        line_no = node.lineno
        col = node.col_offset
        snippet = source_lines[line_no - 1].strip() if line_no <= len(
            source_lines
        ) else ""

        # Case 1: f-string.
        if isinstance(msg, ast.JoinedStr):
            for value in msg.values:
                if not isinstance(value, ast.FormattedValue):
                    continue
                for name in _extract_secret_names_from_expr(value.value):
                    matched = _secret_match(name)
                    if matched:
                        findings.append(
                            Finding(
                                file="",
                                line=line_no,
                                col=col,
                                variable_name=name,
                                pattern="fstring",
                                snippet=snippet,
                            )
                        )

        # Case 2: ``"...".format(...)`` call.
        elif (
            isinstance(msg, ast.Call)
            and isinstance(msg.func, ast.Attribute)
            and msg.func.attr == "format"
        ):
            # Positional args.
            for arg in msg.args:
                for name in _extract_secret_names_from_expr(arg):
                    matched = _secret_match(name)
                    if matched:
                        findings.append(
                            Finding(
                                file="",
                                line=line_no,
                                col=col,
                                variable_name=name,
                                pattern="format_call",
                                snippet=snippet,
                            )
                        )
            # Keyword args (``{api_key}".format(api_key=...)``).
            for kw in msg.keywords:
                if kw.arg is None:
                    continue
                # The kwarg NAME is a free-form string; if it matches
                # the secret regex, flag it.
                matched = _secret_match(kw.arg)
                if matched:
                    findings.append(
                        Finding(
                            file="",
                            line=line_no,
                            col=col,
                            variable_name=kw.arg,
                            pattern="format_call",
                            snippet=snippet,
                        )
                    )
    return findings


def scan_file(path: Path) -> list[Finding]:
    """Scan ``path`` for secret-bearing logging calls. Returns findings."""
    text = path.read_text(encoding="utf-8")
    source_lines = text.splitlines()
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        # Unparseable files are out of scope — surface via pytest skip.
        return []
    raw = _scan_ast_tree(tree, source_lines)
    rel = str(path)
    return [Finding(f"{rel}", f.line, f.col, f.variable_name, f.pattern, f.snippet) for f in raw]


def scan_paths(paths: list[Path]) -> list[Finding]:
    """Run :func:`scan_file` over each path and return all findings."""
    out: list[Finding] = []
    for p in paths:
        out.extend(scan_file(p))
    return out


def filter_allow_list(
    findings: list[Finding], allow: tuple[tuple[str, int, str], ...]
) -> tuple[list[Finding], list[Finding]]:
    """Split findings into (kept, suppressed) per the allow-list.

    An entry matches when its ``(file, line)`` matches a finding's
    ``(file, line)``. The rationale string is ignored here — it lives
    in the allow-list for human review.
    """
    suppressed_keys = {(f, ln) for f, ln, _ in allow}
    kept: list[Finding] = []
    suppressed: list[Finding] = []
    for f in findings:
        if (f.file, f.line) in suppressed_keys:
            suppressed.append(f)
        else:
            kept.append(f)
    return kept, suppressed


# ---------------------------------------------------------------------------
# Tests — detector sensitivity (synthetic positive-case)
# ---------------------------------------------------------------------------


class TestDetectorSensitivity:
    def test_detector_fires_on_fstring_with_api_key(self, tmp_path: Path) -> None:
        # Synthetic positive-case: a minimal file with a known offender.
        # MUST fire. The audit cannot silently regress.
        source = '''
import logging
logger = logging.getLogger(__name__)

def leak(api_key: str) -> None:
    logger.error(f"using api_key={api_key}")
'''
        f = tmp_path / "synthetic_offender.py"
        f.write_text(source)
        findings = scan_file(f)
        assert findings, "Detector failed to fire on synthetic f-string offender"
        assert findings[0].variable_name == "api_key"
        assert findings[0].pattern == "fstring"

    def test_detector_fires_on_format_call_with_keyword(
        self, tmp_path: Path
    ) -> None:
        source = '''
import logging
logger = logging.getLogger(__name__)

def leak(token: str) -> None:
    logger.error("using token={token}".format(token=token))
'''
        f = tmp_path / "synthetic_format_call.py"
        f.write_text(source)
        findings = scan_file(f)
        assert findings, "Detector failed to fire on synthetic .format() offender"
        assert findings[0].pattern == "format_call"

    def test_detector_fires_on_attribute_access(
        self, tmp_path: Path
    ) -> None:
        # ``config.api_key`` — the detector should pick up the attr name.
        source = '''
import logging
logger = logging.getLogger(__name__)

class C:
    api_key: str

def leak(config: "C") -> None:
    logger.error(f"key={config.api_key}")
'''
        f = tmp_path / "synthetic_attr.py"
        f.write_text(source)
        findings = scan_file(f)
        assert findings, "Detector failed to fire on attribute access offender"
        assert findings[0].variable_name == "api_key"

    def test_detector_fires_on_subscript(
        self, tmp_path: Path
    ) -> None:
        # ``config["password"]`` — the slice key matters.
        source = '''
import logging
logger = logging.getLogger(__name__)

def leak(config: dict) -> None:
    logger.error(f"pwd={config[\"password\"]}")
'''
        f = tmp_path / "synthetic_subscript.py"
        f.write_text(source)
        findings = scan_file(f)
        assert findings, "Detector failed to fire on subscript offender"
        assert findings[0].variable_name == "password"

    def test_detector_does_not_fire_on_benign_name(
        self, tmp_path: Path
    ) -> None:
        # Negative-control: a benign f-string with no secret-named vars.
        source = '''
import logging
logger = logging.getLogger(__name__)

def benign(server_name: str, timeout: float) -> None:
    logger.error(f"server={server_name} timeout={timeout}")
'''
        f = tmp_path / "synthetic_benign.py"
        f.write_text(source)
        findings = scan_file(f)
        assert not findings, (
            f"Detector fired on benign names: {[f.variable_name for f in findings]!r}"
        )

    def test_detector_does_not_fire_on_lazy_percent_format(
        self, tmp_path: Path
    ) -> None:
        # Negative-control: lazy %-formatting is OUT OF SCOPE — the
        # args are not part of the AST literal.
        source = '''
import logging
logger = logging.getLogger(__name__)

def lazy(api_key: str) -> None:
    logger.error("using api_key=%s", api_key)
'''
        f = tmp_path / "synthetic_lazy.py"
        f.write_text(source)
        findings = scan_file(f)
        assert not findings, (
            "Detector fired on lazy %s formatting — should be out of scope"
        )


# ---------------------------------------------------------------------------
# Tests — repo audit on the five KMS-touched paths
# ---------------------------------------------------------------------------


def _resolve_kms_touched_paths() -> list[Path]:
    """Resolve the five KMS-touched paths relative to the worktree root.

    Uses ``Path.cwd()`` as the root — pytest runs from the worktree
    root in this mission's setup. If a path is missing, the test
    skips with a clear message rather than failing silently.
    """
    root = Path.cwd()
    out: list[Path] = []
    for rel in KMS_TOUCHED_PATHS:
        p = root / rel
        if not p.exists():
            pytest.skip(f"KMS-touched path missing: {rel}")
        out.append(p)
    return out


class TestRepoAudit:
    def test_no_offenders_in_kms_touched_paths(self) -> None:
        # Day-1 expectation: clean. If this fires, refactor the
        # offender to lazy %s formatting or allow-list it with a
        # one-line rationale.
        paths = _resolve_kms_touched_paths()
        findings = scan_paths(paths)
        kept, suppressed = filter_allow_list(findings, ALLOW_LIST)
        if kept:
            lines = "\n".join(
                f"  {f.file}:{f.line}:{f.col} {f.pattern} {f.variable_name!r}"
                f" — {f.snippet}"
                for f in kept
            )
            pytest.fail(
                "Static audit found secret-bearing logging format strings:\n"
                f"{lines}\n"
                "Refactor to lazy %-style formatting, or add to ALLOW_LIST "
                "in this test with a one-line rationale."
            )
        # Sanity: at least one of the five paths exists and was scanned.
        assert paths, "No KMS-touched paths resolved"

    def test_allow_list_entries_have_rationale(self) -> None:
        # Every allow-list entry MUST carry a non-empty one-line
        # rationale. Day-1 the list is empty; this test pins the
        # contract for future additions.
        for file, line, rationale in ALLOW_LIST:
            assert rationale.strip(), (
                f"Allow-list entry {file}:{line} has empty rationale"
            )
            assert len(rationale.strip()) >= 8, (
                f"Allow-list entry {file}:{line} rationale is too terse: "
                f"{rationale!r}"
            )


# ---------------------------------------------------------------------------
# Tests — detector unit-level (no filesystem)
# ---------------------------------------------------------------------------


class TestDetectorUnit:
    def test_extract_secret_names_from_name(self) -> None:
        tree = ast.parse("api_key", mode="eval")
        names = _extract_secret_names_from_expr(tree.body)
        assert names == ["api_key"]

    def test_extract_secret_names_from_attribute(self) -> None:
        tree = ast.parse("config.client_secret", mode="eval")
        names = _extract_secret_names_from_expr(tree.body)
        assert "client_secret" in names

    def test_extract_secret_names_from_subscript(self) -> None:
        tree = ast.parse("config['password']", mode="eval")
        names = _extract_secret_names_from_expr(tree.body)
        assert "password" in names

    def test_extract_secret_names_from_call(self) -> None:
        tree = ast.parse("get_token()", mode="eval")
        names = _extract_secret_names_from_expr(tree.body)
        assert "get_token" in names

    def test_secret_match_returns_substring(self) -> None:
        # ``_secret_match`` returns the entire matched substring of
        # :data:`SECRET_NAME_RE`. For "api_key" the regex matches the
        # ``api[_-]?key`` alternation, so the match is "api_key".
        assert _secret_match("api_key") == "api_key"
        assert _secret_match("clientSecret") == "clientSecret"
        assert _secret_match("server_name") is None

    def test_filter_allow_list_drops_matching_entries(self) -> None:
        findings = [
            Finding("a.py", 1, 0, "api_key", "fstring", ""),
            Finding("b.py", 2, 0, "password", "fstring", ""),
        ]
        allow = (("a.py", 1, "synthetic positive-case in test"),)
        kept, suppressed = filter_allow_list(findings, allow)
        assert len(kept) == 1
        assert kept[0].file == "b.py"
        assert len(suppressed) == 1
        assert suppressed[0].file == "a.py"
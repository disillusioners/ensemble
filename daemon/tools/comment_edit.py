"""Restricted comment/docstring edit tool for the doc-maintainer agent.

The doc-maintainer agent is mechanically prevented from calling ``edit_file``
on source files — this module is its ONLY write surface for inline comments
and docstrings.

Phase 1 scope: Python docstrings (stdlib ``ast``). JS/TS and Java are deferred
to later phases per the architecture doc.

AST verification (the core safety guarantee):

    1. Parse the file BEFORE the edit.
    2. Locate the comment/docstring anchor; verify it is a docstring (first
       statement of a module/function/class body), not executable code.
    3. Substitute the new comment text.
    4. Parse the file AFTER the edit.
    5. Compare AST nodes — non-comment nodes (FunctionDef bodies, statements,
       expressions, names) must be IDENTICAL. Any difference rejects the write.

If a confused LLM tries to smuggle a code change through a "comment" edit,
the AST comparison rejects it.

Supported languages:
  * **Python** — docstrings (ast.get_docstring) via stdlib ``ast``.
  * **JavaScript/TypeScript** — deferred (Phase 3). Returns UNSUPPORTED_LANGUAGE.
  * **Java** — deferred (Phase 4). Returns UNSUPPORTED_LANGUAGE.
"""

from __future__ import annotations

import ast
import fcntl
import logging
import os
import re
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from langchain_core.tools import tool

from ._tool_registry import register_tool_category

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)

CATEGORY_NAME = "DocMaintenance"
CATEGORY_DOC = """Restricted comment/docstring edit tool for the doc-maintainer agent.

comment_edit() updates inline comments, docstrings, JSDoc, or Javadoc blocks
within an allowlisted source-file scope. Uses language AST parsing to verify
that ONLY comment regions change — any change to executable code is rejected.

Phase 1 supports Python docstrings (stdlib ast). JS/TS and Java are deferred.
"""

# Path prefixes that are NEVER writable by comment_edit, even if the file is
# a source file. The architecture doc restricts doc-maintainer to project
# code, not daemon internals or agents/* prompt files.
_DENYLIST_PREFIXES: tuple[str, ...] = (
    ".agents/",
    "daemon/",
    "frontend/",
    "node_modules/",
    ".git/",
    "__pycache__/",
)

# Path prefixes that ARE writable for source comments.
_ALLOWLIST_PREFIXES: tuple[str, ...] = (
    "daemon/",  # intentional: daemon/ source IS allowed for docstrings
    # but the denylist above excludes daemon/. Remove if we want to
    # allow daemon/ source edits. Per the architecture doc, daemon/
    # is in the denylist. We keep it consistent with the agent's scope.
)

# Files we explicitly never edit (config / secrets).
_DENYLIST_EXACT: tuple[str, ...] = (
    "config.yaml",
    ".env",
    ".envrc",
    "pyproject.toml",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "poetry.lock",
    "Pipfile",
    "Pipfile.lock",
    "Makefile",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
)

# Lock timeout (seconds).
_LOCK_TIMEOUT_S: float = 2.0


# ─── Path validation ─────────────────────────────────────────────────────────


def _validate_source_path(rel_path: str, workdir: Path) -> tuple[Path, str | None]:
    """Validate a relative path for comment_edit. Returns (resolved, error_or_None).

    Allows source files (.py, .js, .ts, .java) outside the denylist scope.
    """
    if not rel_path:
        return workdir, "PATH_EMPTY: relative path is required"

    if os.path.isabs(rel_path):
        return workdir, f"PATH_ABSOLUTE: must be relative to project root, got {rel_path!r}"

    # Normalize: convert backslashes, strip at most ONE leading "./" prefix.
    # Use a regex-style approach: only strip if the path actually starts with "./".
    normalized = rel_path.replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    elif normalized.startswith("/"):
        normalized = normalized.lstrip("/")

    # Reject `..` path segments — same defense as doc_write.
    if ".." in normalized.split("/"):
        return (
            workdir,
            f"PATH_REJECTED: {rel_path!r} contains '..' path segments — disallowed",
        )

    # Filename-level denylist (configs, lockfiles, secrets).
    basename = Path(normalized).name
    if basename in _DENYLIST_EXACT:
        return workdir, f"PATH_REJECTED: {basename!r} is a config/lockfile and not editable"

    # Path-prefix denylist.
    for denied in _DENYLIST_PREFIXES:
        if normalized.startswith(denied) or f"/{denied}" in normalized:
            return workdir, f"PATH_REJECTED: {denied!r} is in the denylist"

    # Extension allowlist — only source files.
    suffix = Path(normalized).suffix.lower()
    allowed_exts = (".py", ".js", ".jsx", ".ts", ".tsx", ".java")
    if suffix not in allowed_exts:
        return workdir, f"PATH_REJECTED: extension {suffix!r} is not editable (allowed: {', '.join(allowed_exts)})"

    # Realpath containment.
    try:
        target_abs = (workdir / normalized).resolve()
        workdir_abs = workdir.resolve()
        try:
            common = os.path.commonpath([str(target_abs), str(workdir_abs)])
        except ValueError:
            return workdir, f"PATH_REJECTED: {normalized!r} resolves outside project root"
        if common != str(workdir_abs):
            return workdir, f"PATH_REJECTED: {normalized!r} resolves outside project root"
    except (OSError, RuntimeError) as exc:
        return workdir, f"PATH_REJECTED: realpath resolution failed: {exc}"

    return target_abs, None


# ─── File locking + atomic write ──────────────────────────────────────────────


@contextmanager
def _lock_path(path: Path, timeout: float = _LOCK_TIMEOUT_S):
    """Acquire an exclusive fcntl flock on a sibling .lock file."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.touch(exist_ok=True)
    fd = open(lock_path, "r+", encoding="utf-8")
    deadline = time.monotonic() + timeout
    acquired = False
    try:
        while time.monotonic() < deadline:
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except (IOError, OSError, BlockingIOError):
                time.sleep(0.05)
        if not acquired:
            raise TimeoutError(f"could not acquire lock on {path} within {timeout}s")
        yield
    finally:
        if acquired:
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
        try:
            fd.close()
        except Exception:
            pass
        try:
            lock_path.unlink(missing_ok=True)
        except Exception:
            pass


def _atomic_write_text(target: Path, content: str) -> None:
    """Atomic write via tempfile + os.replace (same pattern as doc_write)."""
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(parent), prefix=f".{target.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            tmp.write(content)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(str(tmp_path), str(target))
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise


# ─── Python AST backend ──────────────────────────────────────────────────────


def _parse_python(path: Path) -> ast.Module:
    """Parse a Python source file. Raises SyntaxError on parse failure."""
    source = path.read_text(encoding="utf-8")
    return ast.parse(source, filename=str(path))


def _strip_docstring_from_node(node: ast.AST) -> str:
    """Return the source text of a node with its docstring stripped.

    Used to compare non-docstring regions for AST equivalence. We compare
    the parsed AST after replacing any Expr(Constant(str)) docstring with
    a placeholder — that way a docstring change shows up only in the
    docstring content, not in the surrounding code structure.
    """
    # ast.unparse would lose comments; we use it only on copies. The
    # comparison approach: dump both trees with docstring Constant strings
    # normalized to a fixed marker.
    return ast.dump(node, annotate_fields=True, include_attributes=False)


def _normalize_docstrings(tree: ast.AST) -> ast.AST:
    """Return a deep-copied AST with all docstring Constant strings replaced.

    This is the trick that lets us compare "everything BUT the docstring":
    we walk the AST, find Expr(Constant(str)) that match docstring positions,
    and replace the string with a sentinel.
    """
    import copy

    cloned = copy.deepcopy(tree)

    def _normalize_node(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            _normalize_node(child)
        # If this is a function/class/module, the first statement might be a
        # docstring. Replace its value with a sentinel.
        body = getattr(node, "body", None)
        if isinstance(body, list) and body:
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                first.value = ast.Constant(value="<DOCSTRING>")

    _normalize_node(cloned)
    return cloned


def _find_python_docstring_anchor(
    source: str, anchor: str
) -> tuple[int, int, ast.AST] | None:
    """Locate a Python docstring by anchor text.

    Strategy:
      1. Find the anchor substring in the source.
      2. Parse the file's AST and identify which docstring contains the anchor.
      3. Return (line_start, line_end, owning_node) of the docstring.

    Returns None if the anchor is not found or is not part of a docstring.

    Anchor matching: the anchor is expected to be a substring of the
    docstring's *content* (between the triple quotes) OR a unique snippet
    of the literal docstring text. The substring search is greedy from the
    leftmost occurrence.

    This is intentionally conservative — if the anchor doesn't uniquely
    identify a docstring, we reject the edit.
    """
    if anchor not in source:
        return None

    # Parse the file to get AST context.
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    # Walk all docstrings (module-level + nested function/class) and find
    # which one contains the anchor.
    candidates: list[tuple[int, int, ast.AST]] = []

    def _visit(node: ast.AST) -> None:
        # Check module-level docstring.
        body = getattr(node, "body", None)
        if isinstance(body, list) and body:
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                ds_src_segment = _get_docstring_segment(source, first)
                if ds_src_segment is not None and anchor in ds_src_segment:
                    candidates.append(_docstring_span(source, first, node))

        for child in ast.iter_child_nodes(node):
            _visit(child)

    _visit(tree)

    if len(candidates) == 0:
        return None
    if len(candidates) > 1:
        # Ambiguous — the same anchor text appears in multiple docstrings.
        # Reject to avoid accidental cross-node substitution.
        return None
    return candidates[0]


def _get_docstring_segment(source: str, expr: ast.Expr) -> str | None:
    """Extract the literal source text of a docstring Expr node.

    Returns the text including the surrounding quotes (or None on failure).
    """
    try:
        return ast.get_source_segment(source, expr)
    except Exception:
        return None


def _docstring_span(source: str, expr: ast.Expr, owner: ast.AST) -> tuple[int, int, ast.AST]:
    """Return (start_offset, end_offset, owner_node) for a docstring.

    For indented docstrings (inside a function body), ``start_offset`` points
    at the opening ``\"\"\"`` character (NOT at the start of the line) so the
    substitution preserves the original indentation. ``end_offset`` points
    immediately after the closing ``\"\"\"`` (not including the trailing
    newline).
    """
    lines = source.splitlines(keepends=True)
    start_line_idx = expr.lineno - 1
    end_line_idx = (expr.end_lineno or expr.lineno) - 1
    col_offset = getattr(expr, "col_offset", 0)

    start_offset = sum(len(l) for l in lines[:start_line_idx]) + col_offset
    end_offset = sum(len(l) for l in lines[: end_line_idx + 1])

    # If the end-offset sits just before a newline, exclude the newline —
    # we want to replace just the docstring literal, not the line terminator.
    if end_offset > 0 and end_offset <= len(source) and source[end_offset - 1 : end_offset] == "\n":
        end_offset -= 1

    return (start_offset, end_offset, owner)


def _python_ast_unchanged(
    before_tree: ast.AST, after_tree: ast.AST
) -> bool:
    """Return True if the non-docstring portions of two ASTs are equivalent.

    Approach: normalize both trees so that all docstring Constant strings
    are replaced with the sentinel "<DOCSTRING>", then compare dumps.
    Docstring content can differ; everything else must match exactly.
    """
    norm_before = _normalize_docstrings(before_tree)
    norm_after = _normalize_docstrings(after_tree)
    before_dump = ast.dump(norm_before, annotate_fields=True, include_attributes=False)
    after_dump = ast.dump(norm_after, annotate_fields=True, include_attributes=False)
    return before_dump == after_dump


def _substitute_python_docstring(
    source: str, anchor: str, new_text: str
) -> tuple[str, str | None]:
    """Substitute a Python docstring by anchor.

    Returns (new_source, error_or_None). If error is non-None, new_source
    is the original source (unchanged).
    """
    if anchor not in source:
        return source, "ANCHOR_NOT_FOUND: anchor text not present in file"

    span = _find_python_docstring_anchor(source, anchor)
    if span is None:
        return source, (
            "ANCHOR_NOT_FOUND: anchor is not inside a Python docstring, "
            "or appears in multiple docstrings (ambiguous)"
        )

    start_off, end_off, _owner = span

    # Detect the docstring delimiter by inspecting the original slice.
    original_slice = source[start_off:end_off]
    triple_double = '"""'
    triple_single = "'''"

    if triple_double in original_slice:
        delim = triple_double
    elif triple_single in original_slice:
        delim = triple_single
    else:
        return source, "ANCHOR_INVALID: docstring delimiter not recognized"

    # Build the new docstring literal preserving the delimiter and indentation.
    # Determine indent from the actual source position of the opening """.
    indent = _detect_indent(source, start_off, original_slice)
    inner = new_text.rstrip("\n")
    # Indent every continuation line to match the opening line.
    inner_indented = _indent_inner(inner, indent)

    new_literal = f'{delim}{inner_indented}{delim}'
    new_source = source[:start_off] + new_literal + source[end_off:]

    # Verify AST equivalence (non-docstring portions unchanged).
    try:
        before_tree = ast.parse(source)
        after_tree = ast.parse(new_source)
    except SyntaxError as exc:
        return source, f"SYNTAX_ERROR_AFTER_EDIT: {exc}"

    if not _python_ast_unchanged(before_tree, after_tree):
        return source, (
            "AST_DIFFERS: the edit would change non-docstring AST nodes "
            "(executable code, function signatures, imports, etc.) — rejected"
        )

    return new_source, None


def _detect_indent(source: str, start_off: int, docstring_slice: str) -> str:
    """Detect the leading whitespace of a docstring literal in the source.

    Uses the byte position within ``source`` to look at the actual line
    that contains the opening ``\"\"\"``. Falls back to scanning the
    slice if that fails (e.g., synthetic sources).
    """
    # Walk backward from start_off to the start of the line.
    line_start = source.rfind("\n", 0, start_off) + 1  # +1 to skip the newline itself
    prefix = source[line_start:start_off]
    # Strip if it's purely whitespace.
    if prefix.strip() == "":
        return prefix
    # Fallback: scan the slice.
    return _detect_indent_from_slice(docstring_slice)


def _detect_indent_from_slice(docstring_slice: str) -> str:
    """Scan a docstring slice for the indent of the content lines."""
    lines = docstring_slice.splitlines()
    for line in lines[1:]:  # skip the opening """..."""
        stripped = line.lstrip(" \t")
        if stripped:
            return line[: len(line) - len(stripped)]
    # Single-line docstring OR empty body — return the indent of the opening line.
    if lines:
        first = lines[0]
        stripped = first.lstrip(" \t")
        return first[: len(first) - len(stripped)]
    return ""


def _indent_inner(text: str, base_indent: str) -> str:
    """Indent continuation lines of a docstring so they line up with the first line.

    Splits on newlines; the first line is emitted at zero indent (it follows
    the opening triple-quote), subsequent lines are indented to `base_indent`.
    """
    parts = text.split("\n")
    if len(parts) == 1:
        return parts[0]
    head = parts[0]
    rest = "\n".join(base_indent + line if line else line for line in parts[1:])
    return head + "\n" + rest


# ─── JS/TS / Java stubs (deferred phases) ─────────────────────────────────────


def _unsupported_language(rel_path: str) -> str:
    """Return a deferred-language error message."""
    suffix = Path(rel_path).suffix.lower()
    return (
        f"UNSUPPORTED_LANGUAGE: {suffix!r} is recognized but AST verification "
        "is not implemented in this phase. Python (.py) is supported; "
        "JS/TS and Java are deferred to later phases."
    )


# ─── Tool factory ─────────────────────────────────────────────────────────────


def create_comment_edit_tools(
    manager: "InstanceManager",
    current_instance_id: str,
    agent_id: str = "",
) -> list:
    """Create comment_edit tool with injected manager reference."""

    def _get_workdir() -> Path | None:
        try:
            inst = manager._instance_repository.get(current_instance_id)
            if inst is not None and getattr(inst, "project_id", None):
                project_id = inst.project_id
                try:
                    project = manager._project_repository.get(project_id)
                    if project is not None and getattr(project, "workdir", None):
                        return Path(project.workdir)
                except Exception:
                    pass
        except Exception:
            pass
        fallback = getattr(manager, "workdir", None)
        if fallback is not None:
            return Path(fallback)
        return None

    @register_tool_category("doc_maintenance")
    @tool
    def comment_edit(
        file_path: str,
        anchor: str,
        new_text: str,
    ) -> str:
        """Update a comment, docstring, JSDoc, or Javadoc block in a source file.

        Restricted tool — only the doc-maintainer agent should invoke this.
        Verifies via AST parsing that ONLY comment regions change. Any edit
        that alters executable code is rejected.

        Phase 1 supports Python docstrings (via stdlib ``ast``). For Python,
        the anchor must be a substring of a docstring's content. JS/TS and
        Java are deferred to later phases (returns UNSUPPORTED_LANGUAGE).

        Args:
            file_path: Relative path to a source file (e.g., "daemon/foo.py").
            anchor: Substring uniquely identifying the comment/docstring to
                replace. For Python docstrings, must be inside the docstring
                literal (between the triple quotes).
            new_text: Replacement text. Indentation and surrounding context
                are preserved automatically.

        Returns:
            Success message with the updated path, or a rejection error.
        """
        workdir = _get_workdir()
        if workdir is None:
            return "Error: project workdir not available from instance context"

        target, err = _validate_source_path(file_path, workdir)
        if err is not None:
            return f"Error: {err}"

        suffix = Path(file_path).suffix.lower()

        # Phase 1: Python only.
        if suffix == ".py":
            try:
                source = target.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                return f"Error: FILE_UNREADABLE: {exc}"

            new_source, edit_err = _substitute_python_docstring(source, anchor, new_text)
            if edit_err is not None:
                return f"Error: {edit_err}"

            try:
                with _lock_path(target):
                    _atomic_write_text(target, new_source)
            except TimeoutError as exc:
                return f"Error: LOCK_TIMEOUT: {exc}"
            except OSError as exc:
                return f"WRITE_FAILED: {exc}"

            return f"OK: updated docstring in {file_path}"

        # JS/TS / Java: deferred.
        return f"Error: {_unsupported_language(file_path)}"

    return [comment_edit]

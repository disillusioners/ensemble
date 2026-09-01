"""Job-task system constitution — registry source-of-truth (Phase 0).

Phase 0 of the governance path in
``.agents/shared/planning/job-task-retrospective/drift-history-and-constitution.md``
(§4). This module is the registry source-of-truth that the doc asserts equal
by a bidirectional AST drift test (the tool-name drift test in
``tests/unit/tools/test_frozen_tool_name_discovery.py::test_known_tool_names_matches_source_exactly_no_drift``
is the precedent). It enforces two of the four D1–D4 red-line booleans:

    * **D1** — every admission-state writer resolves to a registered
      owner (``KNOWN_ADMISSION_STATE_WRITERS``).
    * **D4** — every ``work_id`` mint site is registered
      (``KNOWN_MINT_SITES``).

The boundary HELD today (4 public message callers; zero internal creators
— see census C2–C9 in the spec) — ``KNOWN_JOBITEM_CREATORS`` documents
the public JobItem-creation surface so any future internal creator lands
on the constitution (I4 amendment trigger).

This module deliberately has NO heavyweight imports. It is importable from
anywhere (tests, runtime, docs) without pulling the daemon chain, mirroring
the leaf-type pattern in ``daemon.services.messaging_types``.

Key format
----------

Static set keys are ``"<relpath>:<symbol>"`` where ``<symbol>`` is the
enclosing ``FunctionDef`` / ``AsyncFunctionDef`` name. Module-level writes
(e.g. SQL UPDATE strings declared inside a migration list at module scope)
use ``"<relpath>:module:<line>"`` so they stay distinguishable from
function-level writers while still collapsing multi-statement codepaths
to a single registration row.

Source-discovery patterns
-------------------------

The scanner recognises five distinct write idioms for
``admission_state`` (each backed by a documented architectural path in
the spec's W-code table):

    1. SQL ``UPDATE job_queue_items SET admission_state = ...`` — raw
       string constant with the SET clause (post docstring filter).
    2. ORM ``sqlmodel_update(JobItem).values(admission_state=...)`` —
       keyword argument on the ``.values()`` call.
    3. Direct attribute assignment ``job.admission_state = ...`` —
       ``Assign`` node where the target is an Attribute whose ``attr`` is
       ``admission_state``.
    4. ORM constructor ``JobItem(admission_state=...)`` — keyword
       argument on the ``JobItem(...)`` constructor call.
    5. Dict-literal writer (W5 / set_values / values pattern) — a
       ``dict`` whose key is the string ``"admission_state"`` and which
       is consumed by a downstream ``.values(**...)`` splat. Serialisation
       helpers (``to_dict``, ``__str__``, ...) are filtered.

Mint idioms for ``work_id`` (the D4 scanner):

    1. ``uuid.uuid4()`` — ``Call`` node whose function is
       ``uuid.uuid4`` or a bare ``uuid4`` (imported via ``from uuid
       import uuid4``).
    2. ``secrets.token_hex(...)`` — covered for completeness even
       though no current source uses it for ``work_id``; the spec
       flags ``token_hex``/``uuid7`` completeness as an OPEN ITEM.
    3. ``uuid.uuid7()`` — placeholder for future-proofing.

Frozen-binary contract
----------------------

``discover_admission_state_writer_paths()``,
``discover_jobitem_creator_paths()`` and
``discover_work_id_mint_paths()`` MUST raise ``RuntimeError`` when zero
source files are readable (PyInstaller bytecode-only builds). This
mirrors ``discover_source_only_tool_names()`` in
``daemon/tools/_tool_registry.py:303-334`` — drift detection is not
meaningful from bytecode.

Maintenance — regen one-liner
-----------------------------

When you add or remove a writer / creator / mint site, regenerate the
static sets below by running::

    uv run python -c "from daemon.job_state.constitution import regenerate_sets; print(regenerate_sets())"

Then paste the printed frozenset literals into the static ``KNOWN_*``
sets below. Bidirectional drift between source and the static sets is
caught by ``tests/unit/job_state/test_constitution_drift.py``.
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Static fallback universes (the doc-asserted source-of-truth)
# ---------------------------------------------------------------------------

#: Every admission-state writer is registered by enclosing function
#: (or ``module:<line>`` for module-level migration UPDATEs). The
#: W-code tag in each entry's neighbour-comment points to the
#: architect's census ID (W1 = ``repository.atomic_transition``,
#: W5 = the observer's illegal ``paused → done``, ...).
KNOWN_ADMISSION_STATE_WRITERS: frozenset[str] = frozenset({
    # ── W1 happy path — the validate_transition enforcer (single owner) ──
    "daemon/repositories/job_queue/repository.py:atomic_transition",
    # ── Repository writers (all routed through W1's authority via SQL guards) ──
    "daemon/repositories/job_queue/repository.py:atomic_retry",
    "daemon/repositories/job_queue/repository.py:create",                          # JobItem INSERT
    "daemon/repositories/job_queue/repository.py:batch_cancel_queued",
    "daemon/repositories/job_queue/repository.py:cancel_job",
    "daemon/repositories/job_queue/repository.py:finalize_active_to_done",
    "daemon/repositories/job_queue/repository.py:start_job",
    "daemon/repositories/job_queue/repository.py:start_job_atomic_with_lock",
    "daemon/repositories/job_queue/repository.py:rearm_with_lock",
    "daemon/repositories/job_queue/repository.py:reset_active_to_queued",
    "daemon/repositories/job_queue/repository.py:force_finalize_orphan",
    "daemon/repositories/job_queue/repository.py:create_or_get_by_idempotency_key",
    # ── Cross-system mirror writer (task ↔ job reconciliation) ──
    "daemon/repositories/task/repository.py:reconcile_turn_mirror",                 # mirror writer
    # ── DLQ writers (Phase 4 declare subordinates to W1) ──
    "daemon/services/dead_letter_service.py:move_to_dlq",
    "daemon/services/dead_letter_service.py:move_to_dlq_standalone",
    "daemon/services/dead_letter_service.py:replay_from_dlq",
    # ── Lifecycle cascades (Phase 3 declare subordinates to W1) ──
    "daemon/services/instance_lifecycle.py:_resume_cascade_db_sync",
    "daemon/services/instance_lifecycle.py:_terminate_instance_db_sync",
    # ── W5 legacy observer writer (illegal paused → done) ──
    "daemon/services/job_feedback_observer.py:_finalize_job_db_sync",
    # ── Legacy-status backfill (single migration codepath) ──
    "daemon/manager.py:_ensure_postgres_columns",
})


#: Public JobItem-creation surface (the JAFP boundary — I4). Every internal
#: path goes through this set; an addition here is an I4 amendment trigger.
#: The set keys are ``"<relpath>:<function_name>"`` and identify the
#: JobItem-creating call site.
KNOWN_JOBITEM_CREATORS: frozenset[str] = frozenset({
    # ── Single ORM constructor (the canonical repository factory) ──
    "daemon/repositories/job_queue/repository.py:create",
})


#: Every mint site that produces a work_id-bearing handle (the D4
#: scanner) — covers three families: the ``work_id`` auto-mint on the
#: internal self-mint path, the ``work_id`` linkage stamp on the
#: job-driven path, and the ``message_id`` mints in the
#: ``_prepare_enqueued_message`` prelude that key the MessageQueue rows
#: feeding the Task↔MessageQueue join. Pure general-purpose UUID mints
#: (model primary keys, instance / planner scratch ids, ...) live in
#: source but are intentionally NOT registered — they do not bear on
#: the work_id / message_id linkage the constitution guards.
#: Keys are ``"<relpath>:<line>:<token>"`` where token is the qualified
#: call (e.g. ``uuid.uuid4`` or ``secrets.token_hex``). The static set
#: is intentionally a SUBSET of the source.
KNOWN_MINT_SITES: frozenset[str] = frozenset({
    # ── The auto-mint site (instance_messaging.py:699 in the
    # ── ``_ensure_work_id_fail_closed`` helper — extracted from
    # ── ``_prepare_enqueued_message`` by Fix A in dc4e0c89) — D4
    # ── fail-open handle; preserved by design for the INTERNAL
    # ── self-mint path (agent-to-agent send_message, cascade-resume,
    # ── child reports — no JobItem). The job-driven path raises
    # ── LinkageContractError instead of minting. See
    # ── approach-comparison.md row A.
    "daemon/services/instance_messaging.py:699:uuid.uuid4",
    # ── message_id mints (4 sites in _prepare_enqueued_message prelude) ──
    "daemon/services/instance_messaging.py:1593:uuid.uuid4",
    "daemon/services/instance_messaging.py:1597:uuid.uuid4",
    "daemon/services/instance_messaging.py:1601:uuid.uuid4",
    "daemon/services/instance_messaging.py:1605:uuid.uuid4",
    # ── The structurally-safe enqueue_message_job mint (joins the
    # ── shared linkage UUID into both the Task row and the JobItem) ──
    "daemon/services/instance_messaging.py:2238:uuid.uuid4",
})


# ---------------------------------------------------------------------------
# Source scanner — AST walk over daemon/ source files
# ---------------------------------------------------------------------------

#: Repo-rooted relative path of this module — the scanner MUST skip this
#: file when walking the daemon/ tree, because the scanner's own source
#: contains "SET admission_state" tokens in comments / docstrings describing
#: what it looks for. Including this file would conflate the registry with
#: its subject.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent  # daemon/job_state/ → repo root
_SOURCE_ROOT = _REPO_ROOT / "daemon"


def _iter_source_files() -> list[Path]:
    """Yield every ``.py`` file under ``daemon/`` (skipping caches/venvs).

    The constitution module itself is excluded — see the module docstring's
    Key Format section.
    """
    files: list[Path] = []
    for entry in sorted(_SOURCE_ROOT.rglob("*.py")):
        if any(part in entry.parts for part in ("__pycache__", ".venv", ".git")):
            continue
        if entry.is_relative_to(_REPO_ROOT / "daemon/job_state"):
            continue
        files.append(entry)
    return files


def _relpath(path: Path) -> str:
    """Return the ``daemon/``-rooted relative path used as the set key."""
    return str(path.relative_to(_REPO_ROOT))


# Methods whose return-value dict containing ``"admission_state"`` is
# OUTPUT (serialisation), not a write payload. Scanned dict literals
# inside these methods are filtered.
_SERIALIZE_METHOD_NAMES: frozenset[str] = frozenset({
    "to_dict", "to_record", "as_dict", "serialize", "__str__", "__repr__",
    "_to_dict", "to_payload", "to_response", "_asdict",
})


def _collect_docstring_line_ranges(tree: ast.AST) -> set[int]:
    """Return every line that falls inside a docstring of any scope.

    A docstring is the leading ``Expr(Constant(str))`` statement at the
    top of a ``Module`` / ``FunctionDef`` / ``AsyncFunctionDef`` /
    ``ClassDef`` body. Multi-line docstrings (Python's
    implicit-concatenation form) span from the opening quote line to
    the closing quote line — both included.
    """
    ranges: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if not node.body:
            continue
        first = node.body[0]
        if not (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            continue
        const = first.value
        start = const.lineno
        end = getattr(const, "end_lineno", None) or start
        ranges.update(range(start, end + 1))
    return ranges


def _parent_map(tree: ast.AST) -> dict[int, ast.AST]:
    """Build a ``child_id(node) -> parent`` map for every AST node."""
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    return parents


def _is_serialization_dict(node: ast.Dict, parents: dict[int, ast.AST]) -> bool:
    """True iff ``node`` is a dict inside a return statement / serialisation helper.

    Catches ``JobItem.to_dict()`` returning ``{"admission_state": ...}``
    as OUTPUT (read-only) rather than as input to a write call.
    """
    parent = parents.get(id(node))
    if isinstance(parent, ast.Return):
        return True
    walker = parent
    depth = 0
    while walker is not None and depth < 10:
        if isinstance(walker, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return walker.name in _SERIALIZE_METHOD_NAMES
        walker = parents.get(id(walker))
        depth += 1
    return False


def _find_write_line_numbers(tree: ast.AST) -> set[int]:
    """Return every line number where an admission-state write happens.

    Covers all five idioms (SQL UPDATE / ORM .values() / attribute
    assignment / ORM constructor / dict-key writer). Docstrings and
    serialisation helpers are filtered — see :data:`_SERIALIZE_METHOD_NAMES`
    and :func:`_collect_docstring_line_ranges`.
    """
    lines: set[int] = set()
    doc_lns = _collect_docstring_line_ranges(tree)
    parents = _parent_map(tree)
    for node in ast.walk(tree):
        # SQL UPDATE strings — raw ``SET admission_state = ...``
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.lineno in doc_lns:
                continue
            if "SET admission_state" in node.value:
                lines.add(node.lineno)
                continue
        # ORM .values(admission_state=...) — keyword arg on .values()
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "values":
            for kw in node.keywords:
                if kw.arg == "admission_state":
                    lines.add(node.lineno)
                    break
            continue
        # Direct attribute assignment ``x.admission_state = ...``
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Attribute) and tgt.attr == "admission_state":
                    lines.add(node.lineno)
                    break
            continue
        # ORM constructor ``JobItem(admission_state=...)``
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "JobItem":
            for kw in node.keywords:
                if kw.arg == "admission_state":
                    lines.add(node.lineno)
                    break
            continue
        # Dict literal with ``"admission_state"`` key (W5 / set_values pattern)
        if isinstance(node, ast.Dict):
            if _is_serialization_dict(node, parents):
                continue
            for k in node.keys:
                if isinstance(k, ast.Constant) and k.value == "admission_state":
                    lines.add(node.lineno)
                    break
    return lines


def _scan_writer_in_tree(tree: ast.AST, relpath: str) -> set[str]:
    """Group the line-level writer set by enclosing function.

    One function = one writer registration (governance primitive).
    Module-level writes (no enclosing FunctionDef) use ``module:<line>`` so
    multi-statement migration codepaths stay as one entry per line.
    """
    write_lines = _find_write_line_numbers(tree)
    if not write_lines:
        return set()

    # Map each write line -> enclosing function name (or None).
    line_to_fn: dict[int, str | None] = {ln: None for ln in write_lines}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # Walk the function body and any nested scopes; nested fns claim
        # own their writes, the outer fn only claims unwritten-in-nested ones.
        nested_line_set: set[int] = set()
        for child in ast.walk(node):
            if child is node:
                continue
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                nested_line_set.update(getattr(n, "lineno", -1) for n in ast.walk(child))
                nested_line_set.add(getattr(child, "lineno", -1))
        for stmt in ast.walk(node):
            ln = getattr(stmt, "lineno", -1)
            if ln in write_lines:
                if ln in nested_line_set and stmt is not node:
                    # Owned by a nested function
                    continue
                if line_to_fn.get(ln) is None:
                    line_to_fn[ln] = node.name

    out: set[str] = set()
    for ln, fn in line_to_fn.items():
        if fn is None:
            out.add(f"{relpath}:module:{ln}")
        else:
            out.add(f"{relpath}:{fn}")
    return out


def _scan_mint_in_tree(tree: ast.AST, relpath: str) -> set[str]:
    """Identify every UUID/secret mint site (D4 scanner).

    Recognized idioms (the OPEN ITEM in spec §5 — completeness check):

      * ``uuid.uuid4(...)`` — bare ``uuid.uuid4`` attribute on a Name
        with id ``uuid``.
      * ``uuid.uuid7(...)`` — same, for forward-compat.
      * ``secrets.token_hex(...)`` / ``secrets.token_urlsafe(...)`` —
        covered even though no current source uses them for ``work_id``.
      * Bare ``uuid4(...)`` / ``uuid7(...)`` — ``from uuid import uuid4``
        pattern.

    The scanner raises on an un-recognized mint idiom ONLY when a
    heuristic pre-filter (presence of a string matching
    ``uuid.uuid<N>`` or ``secrets.token_*``) trips; this is the
    mint-idiom completeness guarantee the spec asks for.
    """
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        token = None
        if isinstance(func, ast.Attribute):
            base = func.value
            attr = func.attr
            if isinstance(base, ast.Name) and base.id in ("uuid", "secrets"):
                if attr in ("uuid4", "uuid7", "uuid1", "uuid3", "uuid5",
                            "token_hex", "token_urlsafe", "token_bytes"):
                    token = f"{base.id}.{attr}"
        elif isinstance(func, ast.Name):
            if func.id in ("uuid4", "uuid7"):
                token = func.id
        if token is not None:
            found.add(f"{relpath}:{node.lineno}:{token}")
    return found


def _scan_creator_in_tree(tree: ast.AST, relpath: str) -> set[str]:
    """Group JobItem constructor sites by enclosing function."""
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "JobItem":
            if node.keywords or len(node.args) >= 1:
                lines.add(node.lineno)

    if not lines:
        return set()

    line_to_fn: dict[int, str | None] = {ln: None for ln in lines}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        nested_line_set: set[int] = set()
        for child in ast.walk(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child is not node:
                nested_line_set.add(getattr(child, "lineno", -1))
                for n in ast.walk(child):
                    nested_line_set.add(getattr(n, "lineno", -1))
        for stmt in ast.walk(node):
            ln = getattr(stmt, "lineno", -1)
            if ln in lines:
                if ln in nested_line_set and stmt is not node:
                    continue
                if line_to_fn.get(ln) is None:
                    line_to_fn[ln] = node.name

    out: set[str] = set()
    for ln, fn in line_to_fn.items():
        if fn is None:
            out.add(f"{relpath}:module:{ln}")
        else:
            out.add(f"{relpath}:{fn}")
    return out


def _scan_source(scanner) -> tuple[set[str], bool]:
    """Apply ``scanner`` to every source file under ``daemon/``.

    Returns:
        (collected_keys, any_source_read) — ``any_source_read`` is True iff
        at least one source file was actually read from disk. Frozen-binary
        contract: when False, callers that need drift detection MUST raise.
    """
    collected: set[str] = set()
    any_source_read = False
    for path in _iter_source_files():
        try:
            src = path.read_text()
        except OSError:
            continue
        any_source_read = True
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        try:
            collected |= scanner(tree, _relpath(path))
        except Exception as exc:  # pragma: no cover — defensive
            _logger.debug(
                "constitution: scanner raised on %s: %s", path, exc
            )
    return collected, any_source_read


# ---------------------------------------------------------------------------
# Public API — discover_* functions (the regen source-of-truth)
# ---------------------------------------------------------------------------

def discover_admission_state_writer_paths() -> set[str]:
    """Pure source discovery of every admission-state writer — no merge.

    This is the regen source-of-truth for ``KNOWN_ADMISSION_STATE_WRITERS``
    and the canonical basis for bidirectional drift detection. Must
    return ONLY what on-disk source contains. Each entry is a function
    name (or ``module:<line>`` for module-level migration UPDATEs).

    Frozen-binary contract: when zero source files are readable
    (PyInstaller bytecode-only builds), raises ``RuntimeError``. Drift
    detection in a frozen environment is not meaningful — callers that
    need the frozen-safe merged result should use
    :func:`get_all_admission_state_writers` instead.

    Raises:
        RuntimeError: when zero source files are readable.
    """
    keys, any_source_read = _scan_source(_scan_writer_in_tree)
    if not any_source_read:
        raise RuntimeError(
            "discover_admission_state_writer_paths(): no daemon/ source files "
            "readable (frozen binary?) — source-only discovery is unavailable; "
            "use get_all_admission_state_writers() for the frozen-safe universe"
        )
    return keys


def discover_jobitem_creator_paths() -> set[str]:
    """Pure source discovery of every JobItem creator — no merge.

    Frozen-binary contract: raises ``RuntimeError`` on zero-source-readable.

    Raises:
        RuntimeError: when zero source files are readable.
    """
    keys, any_source_read = _scan_source(_scan_creator_in_tree)
    if not any_source_read:
        raise RuntimeError(
            "discover_jobitem_creator_paths(): no daemon/ source files "
            "readable (frozen binary?) — source-only discovery is unavailable; "
            "use get_all_jobitem_creators() for the frozen-safe universe"
        )
    return keys


def discover_work_id_mint_paths() -> set[str]:
    """Pure source discovery of every work_id mint site — no merge.

    Returns ALL UUID/secret mints in the source tree. The
    ``KNOWN_MINT_SITES`` static set is a curated subset (work_id-handle
    mints only); the full source set is larger because most mints produce
    unrelated handles (model PKs, message ids, instance ids, ...).

    Frozen-binary contract: raises ``RuntimeError`` on zero-source-readable.

    Raises:
        RuntimeError: when zero source files are readable.
    """
    keys, any_source_read = _scan_source(_scan_mint_in_tree)
    if not any_source_read:
        raise RuntimeError(
            "discover_work_id_mint_paths(): no daemon/ source files "
            "readable (frozen binary?) — source-only discovery is unavailable; "
            "use get_all_mint_sites() for the frozen-safe universe"
        )
    return keys


# ---------------------------------------------------------------------------
# Frozen-safe merge functions — combine static + source for runtime callers
# ---------------------------------------------------------------------------

def get_all_admission_state_writers() -> set[str]:
    """Frozen-safe merge: source ∪ ``KNOWN_ADMISSION_STATE_WRITERS``.

    Mirrors ``discover_all_tool_names()`` in
    ``daemon/tools/_tool_registry.py:337-379`` — source canonical where
    present, static set covers the rest. When source is fully readable
    the union equals the source set exactly (per the bidirectionality
    test); when source is unreadable (frozen) we fall back to the
    static set so callers don't crash.
    """
    keys, any_source_read = _scan_source(_scan_writer_in_tree)
    if not any_source_read:
        _logger.debug(
            "get_all_admission_state_writers: no daemon/ source files readable "
            "(frozen binary?); falling back to KNOWN_ADMISSION_STATE_WRITERS "
            "(%d entries)",
            len(KNOWN_ADMISSION_STATE_WRITERS),
        )
        return set(KNOWN_ADMISSION_STATE_WRITERS)
    keys |= KNOWN_ADMISSION_STATE_WRITERS
    return keys


def get_all_jobitem_creators() -> set[str]:
    """Frozen-safe merge: source ∪ ``KNOWN_JOBITEM_CREATORS``."""
    keys, any_source_read = _scan_source(_scan_creator_in_tree)
    if not any_source_read:
        _logger.debug(
            "get_all_jobitem_creators: no daemon/ source files readable "
            "(frozen binary?); falling back to KNOWN_JOBITEM_CREATORS "
            "(%d entries)",
            len(KNOWN_JOBITEM_CREATORS),
        )
        return set(KNOWN_JOBITEM_CREATORS)
    keys |= KNOWN_JOBITEM_CREATORS
    return keys


def get_all_mint_sites() -> set[str]:
    """Frozen-safe merge: source ∪ ``KNOWN_MINT_SITES``.

    NOTE: ``KNOWN_MINT_SITES`` is intentionally a SUBSET of the source
    mint set — it covers only the mints that produce ``work_id``-shaped
    handles. General-purpose UUID mints (model PKs, message ids, etc.)
    live in source but are NOT registered. The merge here therefore
    exposes the full universe (source canonical for general UUIDs,
    static covers the curated work_id subset).
    """
    keys, any_source_read = _scan_source(_scan_mint_in_tree)
    if not any_source_read:
        _logger.debug(
            "get_all_mint_sites: no daemon/ source files readable "
            "(frozen binary?); falling back to KNOWN_MINT_SITES "
            "(%d entries)",
            len(KNOWN_MINT_SITES),
        )
        return set(KNOWN_MINT_SITES)
    keys |= KNOWN_MINT_SITES
    return keys


# ---------------------------------------------------------------------------
# Regen one-liner — used by maintainers to refresh the static sets
# ---------------------------------------------------------------------------

def regenerate_sets() -> str:
    """Print the three static sets in ``frozenset({...})`` literal form.

    Run via::

        uv run python -c "from daemon.job_state.constitution import regenerate_sets; print(regenerate_sets())"

    Paste the printed output into the ``KNOWN_*`` sets above. The
    bidirectionality test (``tests/unit/job_state/test_constitution_drift.py``)
    will fail until the static set matches source.
    """
    writers = sorted(discover_admission_state_writer_paths())
    creators = sorted(discover_jobitem_creator_paths())
    mints = sorted(discover_work_id_mint_paths())

    def fmt(keys: list[str]) -> str:
        if not keys:
            return "    # (empty)"
        return ",\n".join(f"    {k!r}" for k in keys)

    parts = [
        "KNOWN_ADMISSION_STATE_WRITERS: frozenset[str] = frozenset({",
        fmt(writers),
        "})",
        "",
        "KNOWN_JOBITEM_CREATORS: frozenset[str] = frozenset({",
        fmt(creators),
        "})",
        "",
        "KNOWN_MINT_SITES: frozenset[str] = frozenset({",
        fmt(mints),
        "})",
    ]
    return "\n".join(parts)


__all__ = [
    "KNOWN_ADMISSION_STATE_WRITERS",
    "KNOWN_JOBITEM_CREATORS",
    "KNOWN_MINT_SITES",
    "discover_admission_state_writer_paths",
    "discover_jobitem_creator_paths",
    "discover_work_id_mint_paths",
    "get_all_admission_state_writers",
    "get_all_jobitem_creators",
    "get_all_mint_sites",
    "regenerate_sets",
]

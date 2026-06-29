#!/usr/bin/env python3
"""Surgical migration script: replace JobStatus with AdmissionState in tests/.

Phase 5 Batch 2 (Job-as-Queue-Proxy). The production code is already
migrated — admission_state is the SOLE queue-admission authority.
``JobStatus`` is now a compat shim (kept as an enum for the API/tool
alias layer). Tests must be migrated off ``JobStatus.X.value`` and any
``status=`` kwargs on ``JobItem(...)`` constructors — the column is gone
from ``JobItem``.

This script is intentionally conservative. It performs FOUR operations:

1. **Enum value replacements** (always safe):
   ``JobStatus.PENDING.value`` → ``AdmissionState.QUEUED.value``
   ``JobStatus.PROCESSING.value`` → ``AdmissionState.ACTIVE.value``
   ``JobStatus.PAUSED.value`` → ``AdmissionState.ACTIVE.value``
   ``JobStatus.COMPLETED.value`` → ``AdmissionState.DONE.value``
   ``JobStatus.FAILED.value`` → ``AdmissionState.DONE.value``
   ``JobStatus.CANCELLED.value`` → ``AdmissionState.DONE.value``
   ``JobStatus.DEAD_LETTER.value`` → ``AdmissionState.DEAD.value``

2. **JobItem(...) constructor ``status=`` kwargs** (context-sensitive):
   Find every ``JobItem(...)`` constructor block (balanced-paren scan)
   and remove any line of the form ``status=<expr>,`` within it.
   The ``admission_state=<expr>,`` line is preserved unchanged.
   These constructors span multiple lines and the pattern
   ``status=status,`` / ``status=<literal>,`` sits alongside the
   already-present ``admission_state=...`` line — removing the
   ``status=`` line is the correct fix because the column is gone.

3. **Flag ``job.status`` reads/writes for manual review** (no auto-fix):
   Print every ``<var>.status`` reference where ``<var>`` looks like a
   job variable (``job``, ``jobs``, ``job_item``, ``next_job_item``,
   ``new_job``, ``pending_job``, ``queued_job``, ``processing_job``,
   ``completed_job``, ``failed_job``, ``cancelled_job``, ``dead_job``,
   ``dead_letter_job``, ``dlq_job``, ``mock_job``, ``seeded_job``).
   Explicitly skip ``instance``, ``task``, ``agent``, ``project``,
   ``user``, ``session``, ``work``, ``record``, ``meta``, ``health``,
   ``source``, ``schedule``, ``msg``, ``progress``, ``worker`` — these
   are different models whose ``.status`` column is intact.

4. **Conservative import replacement**:
   In ``from daemon.repositories.job_queue.models import JobStatus``
   lines, replace ``JobStatus`` with ``AdmissionState`` ONLY if no
   ``JobStatus.<NAME>`` (enum-member access) reference remains in the
   file after steps 1–3. Files that still reference ``JobStatus.X``
   (e.g. ``test_status_alias_mapping.py``, which tests the compat shim)
   keep their ``JobStatus`` import.

**Deliberately NOT touched:**
- ``instance.status``, ``task.status``, ``agent.status``, etc. (other models)
- ``.status`` on variables that are NOT clearly job/job_item
- ``status_code``, ``HTTPException(status_code=...)`` (HTTP, not job status)
- Comments and docstrings referencing the legacy status (informational)
- The ``JobStatus`` enum definition itself (lives in ``daemon/``, not tests/)

Run from project root: ``python scripts/migrate_jobstatus_to_admission.py``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable

TESTS_DIR = Path("tests")

# Mapping of JobStatus enum values (uppercase) to AdmissionState values (uppercase).
# ``PAUSED`` is a JobStatus value that maps to ``ACTIVE`` in admission state
# because pause is an Instance concern, not a queue-admission concern.
STATUS_VALUE_MAP: dict[str, str] = {
    "PENDING": "QUEUED",
    "PROCESSING": "ACTIVE",
    "PAUSED": "ACTIVE",
    "COMPLETED": "DONE",
    "FAILED": "DONE",
    "CANCELLED": "DONE",
    "DEAD_LETTER": "DEAD",
}

# Variable prefixes / exact names whose ``.status`` reads/writes MAY be
# JobItem.status and therefore require manual review. Listed without
# the leading ``.`` so we can build regexes around them.
JOB_VAR_NAMES: tuple[str, ...] = (
    "job",
    "jobs",
    "job_item",
    "job_items",
    "job_row",
    "job_rows",
    "next_job",
    "next_job_item",
    "new_job",
    "queued_job",
    "processing_job",
    "pending_job",
    "completed_job",
    "failed_job",
    "cancelled_job",
    "dead_job",
    "dead_letter_job",
    "dlq_job",
    "seeded_job",
    "test_job",
    "mock_job",
    "retrieved",
    "current_job",
    "target_job",
)

# Variable prefixes / exact names whose ``.status`` is on a DIFFERENT
# model — explicit ignore list to avoid false-positive flags.
NON_JOB_VAR_NAMES: tuple[str, ...] = (
    "instance",
    "instances",
    "inst",
    "inst_after_pause",
    "inst_after_resume",
    "inst_final",
    "task",
    "tasks",
    "routed_task",
    "task_after_pause",
    "project",
    "projects",
    "agent",
    "agents",
    "user",
    "users",
    "session",
    "sessions",
    "work",
    "works",
    "record",
    "records",
    "row",
    "rows",
    "meta",
    "mock_meta",
    "mock_instance_meta",
    "instance_meta",
    "schedule",
    "source",
    "sources",
    "health",
    "msg",
    "worker",
    "progress",
    "p",
    "evt",
    "expected",
    "result",
    "response",
    "value",
)

# Match ``<var>.status`` where ``<var>`` looks like a job variable name.
# Word-boundary on the LEFT so we don't match ``xxxjob.status`` (false
# positives) but DO match ``job.status`` and ``mock_job.status``.
_JOB_STATUS_RE = re.compile(
    r"(?<![\w.])(" + "|".join(re.escape(v) for v in JOB_VAR_NAMES) + r")\.status\b"
)

# Match any ``JobStatus`` identifier (bare or member access). Used to
# decide whether a file still needs the ``JobStatus`` import — we
# leave imports alone if the file uses ``JobStatus`` at all (e.g.
# ``{s.value for s in JobStatus}`` to iterate the enum, or a leftover
# reference in a comment). This protects files like
# ``test_status_alias_mapping.py`` that test the compat shim.
_JOBSTATUS_ANY_RE = re.compile(r"\bJobStatus\b")

# Find ``JobItem(...)`` constructor openings.
_JOBITEM_OPEN_RE = re.compile(r"\bJobItem\s*\(")


def find_jobitem_blocks(content: str) -> list[tuple[int, int]]:
    """Locate the (start, end_exclusive) ranges of every ``JobItem(...)`` block.

    Uses balanced-paren scanning so we correctly handle nested
    parentheses (e.g. default-arg function calls inside kwargs).
    Returns a list of (start, end) tuples where ``content[start:end]``
    is the full ``JobItem(<args>)`` source slice.
    """
    blocks: list[tuple[int, int]] = []
    for m in _JOBITEM_OPEN_RE.finditer(content):
        open_paren = m.end() - 1  # index of '('
        depth = 0
        i = open_paren
        # Track string boundaries so we don't count parens inside
        # string literals as real parens.
        in_string: str | None = None
        while i < len(content):
            ch = content[i]
            if in_string is not None:
                if ch == "\\" and i + 1 < len(content):
                    i += 2
                    continue
                if ch == in_string:
                    in_string = None
            elif ch in ("'", '"'):
                in_string = ch
            elif ch == "#" and in_string is None:
                # Line comment — skip to end of line.
                while i < len(content) and content[i] != "\n":
                    i += 1
                continue
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        if depth == 0:
            blocks.append((m.start(), i + 1))
    return blocks


def remove_status_kwarg_in_block(block: str) -> tuple[str, int]:
    """Remove ``status=<expr>,`` lines from a ``JobItem(...)`` constructor block.

    The column ``status`` was dropped from ``JobItem`` in Phase 5; only
    ``admission_state`` remains. Test code that seeds ``JobItem`` rows
    via kwargs must drop the ``status=`` kwarg. We intentionally only
    remove the line, not transform it to ``admission_state=`` — the
    paired ``admission_state=...`` line already exists in every seed
    helper we audited, and it's already correctly written.

    Returns (new_block, removed_count).
    """
    lines = block.split("\n")
    out: list[str] = []
    removed = 0
    # Match a line that is JUST the ``status=<expr>,`` kwarg (with
    # optional surrounding whitespace). Examples we want to match:
    #   ``            status=status,``
    #   ``            status=JobStatus.COMPLETED.value,``
    # We do NOT match ``job.status = X`` (assignment to .status) or
    # ``assert foo.status == X`` (read) — those are flag-only.
    status_line_re = re.compile(r"^\s*status\s*=\s*[^,\n]+,\s*$")
    for line in lines:
        if status_line_re.match(line):
            removed += 1
            continue
        out.append(line)
    return "\n".join(out), removed


# Names of JobItem columns that were dropped in Phase 5 alongside
# ``status``. Test fixtures that seed rows via kwargs must drop these
# too. ``failed_at`` was RE-ADDED in Phase 5 (Plan §6.2) as the live
# retry marker — we keep it. NOTE: this set is ONLY for ``JobItem``
# constructor kwargs (handled by the balanced-paren scan). Instance
# and Task rows still have ``started_at`` / ``completed_at`` columns;
# those are NOT touched here.
JOBITEM_REMOVED_COLUMN_KWARGS: tuple[str, ...] = (
    "started_at",
    "completed_at",
    "result_summary",
    "error_message",
    "cancelled_at",
)


def remove_removed_column_kwargs_in_block(block: str) -> tuple[str, int]:
    """Remove other JobItem-dropped column kwargs (``started_at=`` etc.) from a block.

    Same scoping rules as :func:`remove_status_kwarg_in_block` — only
    operates inside ``JobItem(...)`` constructor blocks identified by
    the balanced-paren scanner. Instance and Task constructors still
    have these columns, so we MUST scope tightly to JobItem.

    Returns (new_block, removed_count).
    """
    # Build one regex with alternation. Each branch anchors to the
    # start-of-line indent so we never strip a substring inside a
    # multi-line expression.
    if not JOBITEM_REMOVED_COLUMN_KWARGS:
        return block, 0
    alt = "|".join(re.escape(name) for name in JOBITEM_REMOVED_COLUMN_KWARGS)
    # Match a line of the form
    #   ``            started_at=<value>,``
    # where ``<value>`` extends to the trailing comma (consuming the
    # comma so we don't leave a dangling one) or to end-of-line.
    kwargs_line_re = re.compile(
        r"^[ \t]*(?:" + alt + r")\s*=[ \t]*[^,\n]+,?[ \t]*$"
    )
    new_lines: list[str] = []
    removed = 0
    for line in block.split("\n"):
        if kwargs_line_re.match(line):
            removed += 1
            continue
        new_lines.append(line)
    return "\n".join(new_lines), removed


def find_job_status_refs(content: str) -> list[tuple[int, str]]:
    """Find every ``<job_var>.status`` read/write/assertion for manual review.

    Returns a list of (line_number, line_text) tuples. The caller decides
    whether to print, write to a report file, or just count them.
    """
    refs: list[tuple[int, str]] = []
    seen_lines: set[int] = set()
    for m in _JOB_STATUS_RE.finditer(content):
        # Skip matches that start with one of the non-job prefixes —
        # defensive guard against ``record.status`` style false matches
        # if someone adds ``record``-shaped names later.
        var = m.group(1)
        if var in NON_JOB_VAR_NAMES:
            continue
        line_no = content.count("\n", 0, m.start()) + 1
        if line_no in seen_lines:
            continue
        seen_lines.add(line_no)
        line_start = content.rfind("\n", 0, m.start()) + 1
        line_end = content.find("\n", m.end())
        if line_end == -1:
            line_end = len(content)
        refs.append((line_no, content[line_start:line_end]))
    return refs


def _dedupe_imports(line: str) -> str:
    """Remove duplicate names from a comma-separated ``from ... import a, b, a`` line.

    Preserves order of first appearance. Used after rewriting ``JobStatus``
    to ``AdmissionState`` so we don't end up with ``AdmissionState,
    AdmissionState`` in the import statement when ``AdmissionState``
    was already imported.
    """
    # Only operates on ``from X import Y[, Z, ...]`` style.
    m = re.match(r"^(\s*from\s+\S+\s+import\s+)(.+?)(\s*(?:#.*)?)$", line)
    if not m:
        return line
    prefix, names_str, suffix = m.group(1), m.group(2), m.group(3)
    # Split on commas, strip, dedupe preserving order, rejoin.
    raw_names = [n.strip() for n in names_str.split(",") if n.strip()]
    seen: set[str] = set()
    deduped: list[str] = []
    for n in raw_names:
        if n in seen:
            continue
        seen.add(n)
        deduped.append(n)
    if len(deduped) == len(raw_names):
        return line  # nothing changed
    return f"{prefix}{', '.join(deduped)}{suffix}"


def _has_jobstatus_use_outside_imports(content: str) -> bool:
    """Return True if any non-import line references ``JobStatus``.

    We deliberately ignore import lines so the import itself doesn't
    count as a "use" — otherwise we'd never be able to drop the
    now-unused import for files where Step 1 replaced every
    ``JobStatus.X.value`` use. Files that have a real non-import use
    (e.g. ``{s.value for s in JobStatus}`` to iterate the enum, or a
    leftover reference in code) keep their ``JobStatus`` import.
    """
    for line in content.split("\n"):
        if re.match(r"^\s*(from\s+\S+\s+import\s+|import\s+)", line):
            continue
        if re.search(r"\bJobStatus\b", line):
            return True
    return False


def replace_import_jobstatus(content: str) -> tuple[str, bool]:
    """Conservatively replace ``JobStatus`` with ``AdmissionState`` in import lines.

    Only acts if the file has NO remaining ``JobStatus`` identifier
    anywhere after the enum-value replacements have already been
    applied. This protects files like ``test_status_alias_mapping.py``
    that legitimately test the compat shim — they keep ``JobStatus``
    (e.g. ``{s.value for s in JobStatus}`` to iterate the enum).

    After renaming, dedupes the resulting import line so a file that
    already had ``AdmissionState`` doesn't end up with a duplicate
    (e.g. ``import AdmissionState, JobStatus`` → ``import
    AdmissionState, AdmissionState`` → ``import AdmissionState``).

    Returns (new_content, changed).
    """
    if _has_jobstatus_use_outside_imports(content):
        # File still uses ``JobStatus`` somewhere in code — leave imports alone.
        return content, False

    # Walk lines; rewrite only import lines that mention ``JobStatus``.
    changed = False
    new_lines: list[str] = []
    for line in content.split("\n"):
        new_line = line
        if re.match(r"^\s*(from\s+\S+\s+import\s+|import\s+)", line):
            if re.search(r"\bJobStatus\b", line):
                candidate = re.sub(r"\bJobStatus\b", "AdmissionState", line)
                candidate = _dedupe_imports(candidate)
                if candidate != line:
                    new_line = candidate
                    changed = True
        new_lines.append(new_line)
    return ("\n".join(new_lines), changed)


def migrate_enum_values(content: str) -> tuple[str, int]:
    """Step 1: replace ``JobStatus.X.value`` with ``AdmissionState.Y.value``.

    Returns (new_content, replacement_count).
    """
    count = 0
    new = content
    for old, new_state in STATUS_VALUE_MAP.items():
        needle = f"JobStatus.{old}.value"
        replacement = f"AdmissionState.{new_state}.value"
        occurrences = new.count(needle)
        if occurrences:
            new = new.replace(needle, replacement)
            count += occurrences
    return new, count


def migrate_file(file_path: Path) -> dict:
    """Apply all migration steps to a single file.

    Returns a dict with keys:
      - ``changed``: bool — any text changes were written
      - ``enum_replacements``: int — count of JobStatus.X.value → AdmissionState.Y.value
      - ``jobitem_blocks_removed``: int — count of ``status=`` lines removed in JobItem(...) blocks
      - ``flagged_job_status``: list[tuple[line_no, line_text]] — refs needing manual review
      - ``import_replaced``: bool — JobStatus import line was rewritten
    """
    original = file_path.read_text()
    content = original

    # Step 1: enum value replacements.
    content, enum_count = migrate_enum_values(content)

    # Step 2: remove ``status=`` and other removed-column kwargs from
    # JobItem(...) blocks. The columns ``status``, ``started_at``,
    # ``completed_at``, ``result_summary``, ``error_message``,
    # ``cancelled_at`` were all dropped from ``JobItem`` in Phase 5;
    # test fixtures must drop the corresponding kwargs.
    jobitem_blocks = find_jobitem_blocks(content)
    jobitem_removed = 0
    if jobitem_blocks:
        # Apply right-to-left so earlier offsets remain valid.
        for start, end in reversed(jobitem_blocks):
            block = content[start:end]
            new_block, removed = remove_status_kwarg_in_block(block)
            new_block, removed_more = remove_removed_column_kwargs_in_block(new_block)
            removed_total = removed + removed_more
            if removed_total:
                content = content[:start] + new_block + content[end:]
                jobitem_removed += removed_total

    # Step 3: collect flagged job.status references (BEFORE we touch
    # imports — we want to report what's actually in the file).
    flagged = find_job_status_refs(content)

    # Step 4: conservative import replacement.
    content, import_changed = replace_import_jobstatus(content)

    changed = content != original
    if changed:
        file_path.write_text(content)

    return {
        "changed": changed,
        "enum_replacements": enum_count,
        "jobitem_blocks_removed": jobitem_removed,
        "flagged_job_status": flagged,
        "import_replaced": import_changed,
    }


def iter_python_files(root: Path) -> Iterable[Path]:
    """Yield every .py file under ``root`` (recursive)."""
    return root.rglob("*.py")


def main() -> int:
    if not TESTS_DIR.exists():
        print(f"ERROR: {TESTS_DIR} does not exist; run from project root.", file=sys.stderr)
        return 2

    total_files_changed = 0
    total_enum_replacements = 0
    total_jobitem_lines_removed = 0
    total_flagged = 0
    files_changed: list[str] = []
    files_flagged: list[tuple[str, list[tuple[int, str]]]] = []
    imports_replaced: list[str] = []

    for f in iter_python_files(TESTS_DIR):
        result = migrate_file(f)
        if result["changed"]:
            total_files_changed += 1
            files_changed.append(str(f))
        total_enum_replacements += result["enum_replacements"]
        total_jobitem_lines_removed += result["jobitem_blocks_removed"]
        if result["flagged_job_status"]:
            files_flagged.append((str(f), result["flagged_job_status"]))
            total_flagged += len(result["flagged_job_status"])
        if result["import_replaced"]:
            imports_replaced.append(str(f))

    # ── Report ────────────────────────────────────────────────────────────────
    print("=" * 70)
    print("MIGRATION REPORT")
    print("=" * 70)
    print(f"Total files changed: {total_files_changed}")
    print(f"Total enum replacements (JobStatus.X.value → AdmissionState.Y.value): {total_enum_replacements}")
    print(f"Total JobItem(...) constructor 'status=' lines removed: {total_jobitem_lines_removed}")
    print(f"Total flagged job.status refs needing manual review: {total_flagged}")
    print(f"Files where JobStatus import was rewritten to AdmissionState: {len(imports_replaced)}")
    print()
    print("Changed files:")
    for path in files_changed:
        print(f"  - {path}")
    print()
    if files_flagged:
        print("=" * 70)
        print("FLAGGED job.status REFERENCES — MANUAL REVIEW NEEDED")
        print("=" * 70)
        for path, refs in files_flagged:
            print(f"\n{path}")
            for line_no, line_text in refs:
                print(f"  L{line_no:>4}: {line_text.strip()}")
    print()
    print("=" * 70)
    print("Done. Review the flagged refs above before running tests.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
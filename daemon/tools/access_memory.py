"""Memory access tool — read specific memory files from the version store.

User Directive #3 (2026-10-14, RESOLUTION-CORE) — the read side of
the MEMORY-class carve-out. inner_soul's DUAL-PATH rewrite routes
``memory`` / ``memories/<slug>`` writes to ``create_memory_version``,
so the canonical memory state lives in the ``prompt_versions`` table
(MEMORY-class rows). This tool reads from the version store FIRST
(the latest MEMORY row for the agent+version_tag+key), then falls
back to the on-disk file for cold-boot / historical content.

The external contract is preserved:

* ``access_memory("20260401_1430-foo.md")`` reads the per-file
  memory row (key = ``"memories/20260401_1430-foo.md"``). Falls
  back to file content for pre-rewrite observations.
* ``access_memory("archive/2026/01/foo.md")`` reads the same key
  shape (``memories/archive/2026/01/foo.md``).
* Missing key (no DB row, no file) returns the documented
  not-found shape (list of available files) — never a crash.
* Symlink-safety: the path-resolution fence is kept on the file
  fallback path; the DB path goes through the typed repo.

The agent's version_tag and identity are threaded from the
factory closure (C1 fix — base/v1 was being read for v2
instances).
"""

import re
from pathlib import Path
from langchain_core.tools import tool

from ._tool_registry import register_tool_category

ARCHIVE_PATTERN = re.compile(r'^(\d{4})/(\d{2})/[a-zA-Z0-9_\-]+\.md$')

CATEGORY_NAME = "Memory Access"
CATEGORY_DOC = """\
Read memory files from your memories/ directory.

**Filename convention**: `{date}-{descriptive-title}.md`
- Example: `2026-04-01-k8s-db-connection.md`
"""

_FULL_DOC = """Read a specific memory file from your memories/ directory.

You see your recent memory filenames in the '## Recent Memories' section of your system prompt.
Use this tool to read the full content of any of those files.

The memory state is stored in the prompt-version DB (MEMORY-class rows,
keyed ``memories/<slug>`` per inner_soul's DUAL-PATH rewrite). The
on-disk file is the cold-boot F6 fallback for hosts with zero DB rows.

Args:
    filename: The exact filename (e.g., "20260401_1430-remember-user-prefers-terse-replies.md")

Returns:
    The full content of the memory file (DB row preferred, file fallback), or an error message if not found.
"""


def create_access_memory_tool(agent_id: str, version_tag: str | None = None):
    """Create access_memory tool bound to specific agent.

    Args:
        agent_id: The base agent identifier (e.g., ``"developer"``).
        version_tag: Optional version tag (e.g., ``"v2"``) used to resolve the
            versioned agent directory so memory reads target the correct
            version's ``memories/`` subtree. When ``None``, falls back to the
            base resolved agent meta. Threaded from
            ``daemon.tools.instance.create_instance_tools`` so versioned
            agents read their own memory subtree (C1 fix — base/v1 was being
            read for v2 instances).
    """
    from ..registry import get_registry

    registry = get_registry()
    agent_meta = registry.get_version(agent_id, version_tag) or registry.get_resolved(agent_id)
    agent_path = agent_meta.path if agent_meta else Path(agent_id)
    # Normalize version_tag the same way the repo does (None → "").
    effective_version_tag = version_tag or ""

    def _resolve_db_content(key: str) -> str | None:
        """Read the latest MEMORY-class DB row for ``memories/<key>``.

        Returns the row content if a MEMORY row exists, else None.
        The repo is the canonical read path — mirrors
        ``inner_soul._update_memory_md`` / ``_update_memories``
        which write via ``create_memory_version`` on the same key.

        F4 NORMALIZATION (User Directive, 2026-10-14) — the
        canonical row key shape is ``memories/<slug>.md`` where
        ``<slug>.md`` is the legacy timestamped filename WITH
        the ``.md`` extension (``inner_soul._update_memories``
        line 1690 sets ``artifact=f"memories/{slug}.md"``
        matching the prompt-visible filename index in the
        loader's recent-memory list — the same shape the agent
        passes to ``access_memory("X.Y.md")``). The on-the-wire
        ``access_memory("X.Y.md")`` arg carries the ``.md``
        suffix verbatim; the canonical form is the same
        shape — we only normalize a bare ``X.Y`` input by
        appending the extension so the candidate set uses
        the canonical key.

        Backward tolerance (the F4 advisory): we ALSO try
        the no-extension form on the row side, so any
        pre-normalization rows that were written WITHOUT
        the extension (the pre-F4 default — ``memories/
        <slug>`` rows that exist in promotion runs prior to
        this normalization change) still resolve. The cost
        is one extra string comparison per candidate row,
        scoped to the per-call iteration below. Archive
        keys (``archive/2026/01/foo.md``) keep the ``.md``
        suffix because the F4 fix applies only to the
        per-slug leaf — the archive path shape is
        preserved verbatim.
        """
        try:
            # The repo is the canonical injection site — see
            # daemon/loader.py for the module-level singleton
            # (set by daemon/api.py lifespan via
            # ``set_prompt_version_repo``). The defensive
            # hasattr keeps the helper importable in test
            # contexts that haven't wired the singleton.
            from .. import loader as _loader
            repo = _loader._get_prompt_version_repo() \
                if hasattr(_loader, "_get_prompt_version_repo") else None
            if repo is None:
                return None
            # Walk the (agent_id, version_tag) effective rows ONCE
            # (A8 single-SELECT guarantee). If the row exists for
            # ``memories/<key>``, the ``content`` is the
            # transition-free latest.
            try:
                eff_rows = repo.get_effective_rows(
                    agent_id, effective_version_tag
                )
            except Exception:
                return None
            # F4 canonical key — the row key carries the
            # ``.md`` extension (matches the prompt-visible
            # filename index). Normalize a bare ``X.Y`` to
            # ``X.Y.md`` so the candidate set uses the
            # canonical key. Archive keys (e.g.,
            # ``archive/2026/01/foo.md``) already carry
            # ``.md`` at the tail and round-trip unchanged.
            canonical = key if key.endswith(".md") else f"{key}.md"
            # Build the candidate key set ONCE — the
            # canonical (.md-suffixed) form plus the legacy
            # no-extension form for backward tolerance with
            # pre-F4 rows (the pre-F4 default — see the
            # advisory in the docstring).
            candidates = {
                f"memories/{canonical}",
                f"memories/{canonical[:-3]}",
            }
            for row in eff_rows:
                if getattr(row, "artifact_class", None) != "MEMORY":
                    continue
                if row.artifact not in candidates:
                    continue
                # The EffectiveRow.latest_content is the
                # transition-free latest MEMORY row's content.
                content = getattr(row, "latest_content", None)
                if content is not None:
                    return content
            return None
        except Exception:
            # Fail-open to the file fallback on any DB error;
            # the contract is "never crash" (D-Boot-tolerance).
            return None

    @register_tool_category("self")
    @tool
    def access_memory(filename: str) -> str:
        """Read a memory file from your memories/ directory. Use tool_help("access_memory") for details."""
        memories_dir = agent_path / "memories"

        # Check if this is an archive access
        if filename.startswith("archive/"):
            remainder = filename[len("archive/"):]
            if ARCHIVE_PATTERN.match(remainder):
                # Validated archive path: YYYY/MM/<safe_name>.md
                # Use archive subdirectory, preserve validated path components
                memories_dir = agent_path / "memories" / "archive"
                safe_name = remainder  # Keep full validated path: 2026/01/file.md
                db_key = f"archive/{remainder}"
            else:
                # Invalid archive path — sanitize to filename only
                safe_name = Path(remainder).name
                db_key = remainder
        else:
            # Normal access — strip any path components
            safe_name = Path(filename).name
            db_key = filename

        # 1) DB-first read (Directive #3, 2026-10-14). The latest
        #    MEMORY-class row for ``memories/<db_key>`` is the
        #    transition-free canonical content.
        db_content = _resolve_db_content(db_key)
        if db_content is not None:
            return db_content

        # 2) File fallback — the cold-boot F6 chain (when no DB
        #    row exists). Symlink-safety fence mirrors the
        #    pre-rewrite path.
        if not memories_dir.exists() or not memories_dir.is_dir():
            return "No memories/ directory found."

        filepath = (memories_dir / safe_name).resolve()
        if not str(filepath).startswith(str(memories_dir.resolve())):
            return "Access denied"

        # Explicit symlink check for defense-in-depth
        if filepath.is_symlink():
            return "Access denied"

        if not filepath.is_file():
            try:
                available = sorted([f.name for f in memories_dir.iterdir() if f.suffix == ".md"])
            except (PermissionError, OSError):
                return "Unable to list memories: permission denied."
            if available:
                return f"Memory file '{filename}' not found. Available:\n" + "\n".join(f"- {n}" for n in available[-10:])
            return f"Memory file '{filename}' not found. No memories exist yet."

        return filepath.read_text(encoding="utf-8")

    access_memory._full_doc = _FULL_DOC
    return access_memory

"""HTML artifact persistence (Port: od.save).

Captures one HTML artifact to the canonical mockup path:

    .agents/shared/planning/{feature_slug}/design/mockups/{page_slug}.html

The repo copy at this canonical path is the **developer deliverable**
(per ``agents/designer/workflow.md`` Mockup lane). The OD-UI provenance
was historically ``od_save_artifact`` / ``od_save_project_file`` in the
MCP lane; at slice-⑤ that surface is NOT carried (per REC §4.3 row ⑤;
the OD-UI reference is dropped at §7 retirement along with the MCP
server). The ``od_url`` field on the inputs is accepted and ignored
(reference only — never the developer deliverable).

**No-runtime-loading (CON §7).** The save writes through stdlib only
(no plugin source imports).

**Schema surface.** Mirrors the Port's ``inputs_schema`` (``html``,
``feature_slug``, ``page_slug`` required + ``od_url`` optional) and
``outputs_schema`` (``path``, ``bytes_written``, ``sha256``).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, Optional

__all__ = ["OdSave"]


# Path safety: ``feature_slug`` and ``page_slug`` must be kebab-case
# (lowercase + hyphens + digits). Reject anything else to avoid path
# traversal (``..``, ``/``, etc.).
_SAFE_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class OdSave:
    """The mockup-path writer (Port: od.save).

    Stateless; instantiated once at boot. The save is an explicit
    repo-write operation — the canonical mockup path is
    ``.agents/shared/planning/{feature_slug}/design/mockups/{page_slug}.html``
    inside the project's working directory.
    """

    @staticmethod
    def _validate_slug(value: str, field_name: str) -> None:
        """Validate a kebab-case slug; raise ``ValueError`` on violation.

        Slugs are restricted to ``^[a-z0-9][a-z0-9-]*$`` — lowercase,
        digits, hyphens; must start with an alphanumeric. Rejecting
        ``..``, ``/``, and Unicode confusables is the purpose; the
        path the save writes is rooted at the project's
        ``.agents/shared/planning/`` tree.
        """
        if not isinstance(value, str) or not value:
            raise ValueError(f"od.save: {field_name!r} is required and must be a non-empty string")
        if not _SAFE_SLUG_RE.match(value):
            raise ValueError(
                f"od.save: {field_name!r}={value!r} is not a kebab-case slug "
                f"(allowed: ^[a-z0-9][a-z0-9-]*$)"
            )

    @staticmethod
    def compute_canonical_path(project_root: Path, feature_slug: str, page_slug: str) -> Path:
        """Compute the canonical mockup path without writing.

        Returns ``{project_root}/.agents/shared/planning/{feature_slug}/design/mockups/{page_slug}.html``.
        """
        OdSave._validate_slug(feature_slug, "feature_slug")
        OdSave._validate_slug(page_slug, "page_slug")
        return (
            project_root
            / ".agents"
            / "shared"
            / "planning"
            / feature_slug
            / "design"
            / "mockups"
            / f"{page_slug}.html"
        )

    @classmethod
    def save(
        cls,
        project_root: Path,
        html: str,
        feature_slug: str,
        page_slug: str,
        od_url: Optional[str] = None,  # noqa: ARG003 - reference only, intentionally unused
    ) -> Dict[str, Any]:
        """Write ``html`` to the canonical mockup path.

        Returns the Port's ``outputs_schema`` dict (``path``,
        ``bytes_written``, ``sha256``). Raises ``ValueError`` on
        slug/path violations; ``OSError`` on write failure (the caller
        catches and routes through the CON §3 error envelope).

        The ``od_url`` parameter is accepted for backward compatibility
        with the MCP lane (the original ``od_save_artifact`` accepted
        it). At slice-⑤ OD-UI provenance is dropped per REC §4.3 row
        ⑤; the field is reference-only and never the developer
        deliverable. We accept and ignore.
        """
        if not isinstance(html, str) or not html:
            raise ValueError("od.save: 'html' is required and must be a non-empty string")

        canonical = cls.compute_canonical_path(Path(project_root), feature_slug, page_slug)
        # Create parent dirs (idempotent; mkdir -p semantics). We
        # intentionally use stdlib only — no plugin source imports.
        canonical.parent.mkdir(parents=True, exist_ok=True)

        # Write atomically: stage into a sibling temp file and rename.
        # This survives mid-write interruptions without leaving a
        # partial mockup behind (mirrors the sync-runner's atomic
        # rename discipline for copy_freely / snapshot pulls).
        tmp = canonical.with_suffix(canonical.suffix + ".tmp")
        try:
            tmp.write_bytes(html.encode("utf-8"))
            tmp.replace(canonical)
        except OSError:
            # Clean up the temp file on failure; the canonical write
            # was not atomic if rename raised.
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
            raise

        body = canonical.read_bytes()
        sha = hashlib.sha256(body).hexdigest()
        return {
            "path": str(canonical),
            "bytes_written": len(body),
            "sha256": sha,
        }

    @classmethod
    def save_dict(cls, raw: Dict[str, Any], project_root: Path) -> Dict[str, Any]:
        """Adapter-friendly entrypoint: accept a dict matching the
        Port's ``inputs_schema`` and return the ``outputs_schema``.
        """
        html = raw.get("html")
        if not isinstance(html, str) or not html:
            raise ValueError("od.save: 'html' is required and must be a non-empty string")
        feature_slug = raw.get("feature_slug")
        page_slug = raw.get("page_slug")
        od_url = raw.get("od_url")
        return cls.save(
            project_root=project_root,
            html=html,
            feature_slug=feature_slug if isinstance(feature_slug, str) else "",
            page_slug=page_slug if isinstance(page_slug, str) else "",
            od_url=od_url if isinstance(od_url, str) else None,
        )
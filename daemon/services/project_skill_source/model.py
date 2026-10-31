"""The ``ProjectFileSkill`` duck-typed object + the identity spine.

Phase 1 of the project-skill-source-loader commission. This module is
the SINGLE DEFINITION of the file-skill identity primitives —
:func:`mint_pseudo_id` (the ``pf-<sha16>`` internal id) and
:func:`mint_address` (the ``pf:<namespace>:<name>`` human address).
Every consumer (``project_skill_files`` PK, ``project_skill_embeddings``
rows, the Phase-2 fusion seam, the Phase-3 load path) imports THESE
functions; a divergent re-implementation anywhere is a defect the
Phase-5 grep fence hunts for.

Identity model (decisions.md D-Q5 + architect A3)
-------------------------------------------------

* **Human address** ``pf:<namespace>:<name>`` — typeable at the tool
  surface, round-trips through every ``skill_id``-accepting tool via
  prefix check. Deliberately NOT changed by A3.
* **Internal pseudo-id** ``pf-<sha16>`` where the hash input is the
  pipe-joined ``root_path|project_id|namespace|name`` — A3 AMENDED to
  include ``root_path``: two roots bound to one project (live today via
  ``ENSEMBLE_PROJECT_SKILL_SOURCE_EXTRA_ROOTS``) must not share index
  rows or stage-2 vectors. The pseudo-id is therefore a PER-HOST-PER-ROOT
  index/cache identity, consistent with P1's derived/non-authoritative
  nature of everything the loader persists.
* The prefix ``pf-`` is deliberately not uuid-shaped so DB tooling can
  never misroute a file id into a ``skills``-row lookup and so the ids
  are SQL-greppable.

A16 cache invariant (architect A16, MUST, 2026-10-31)
-----------------------------------------------------

The in-process index cache (``service.py``) stores
:class:`SkillIndexMeta` objects — METADATA ONLY. **Never body bytes.**
:class:`ProjectFileSkill` objects carry ``.content`` and are constructed
FRESH at corpus-assembly / load time from disk reads; they are never
cached. (A cached content-bearing object would bound at ≈64 ns × 128
dirs × 64 KB ≈ 512 MB per root in the worst case AND would violate P1's
read-at-call-time canon semantics.) A cheap unit assertion in the test
pack pins "no cached object exposes non-empty ``content``" by
construction: :class:`SkillIndexMeta` has no ``content`` field at all.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Optional

#: A5 (architect, MUST, CRITICAL): namespace / name path components must
#: match this pattern — starts with an alphanumeric, then alphanumerics,
#: dots, underscores, or dashes. Rejects ``.``, ``..``, path separators,
#: absolute paths, and whitespace-only names BEFORE any filesystem access.
COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

#: The ``pf-`` pseudo-id prefix (frozen — D-Q5).
PSEUDO_ID_PREFIX = "pf-"

#: The ``pf:`` address prefix (frozen — D-Q5).
ADDRESS_PREFIX = "pf:"


def valid_component(value: str) -> bool:
    """Return True iff *value* is a legal namespace/skill-dir component."""
    return bool(isinstance(value, str) and COMPONENT_RE.match(value or ""))


def mint_pseudo_id(root_path: str, project_id: str, namespace: str, name: str) -> str:
    """Mint the stable internal pseudo-id for one file skill.

    A3: ``root_path`` joins the hash — two roots bound to one project
    never share rows or vectors. The join separator is ``|``; inputs are
    used verbatim (callers pass REALPATH'd roots — the service
    normalizes before calling).

    Returns ``"pf-<first 16 hex chars of sha256>"``.
    """
    canonical = "|".join([root_path or "", project_id or "", namespace or "", name or ""])
    return PSEUDO_ID_PREFIX + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def mint_address(namespace: str, name: str) -> str:
    """Mint the human-tool-surface address ``pf:<namespace>:<name>``."""
    return f"{ADDRESS_PREFIX}{namespace}:{name}"


def is_pf_address(value: str) -> bool:
    """Return True iff *value* is shaped like a ``pf:<ns>:<name>`` address."""
    if not isinstance(value, str) or not value.startswith(ADDRESS_PREFIX):
        return False
    rest = value[len(ADDRESS_PREFIX):]
    return rest.count(":") == 1 and all(part.strip() for part in rest.split(":"))


def parse_pf_address(value: str) -> Optional[tuple[str, str]]:
    """Split a ``pf:<namespace>:<name>`` address into its components.

    Returns ``(namespace, name)`` or ``None`` when *value* is not a
    well-formed address.
    """
    if not is_pf_address(value):
        return None
    rest = value[len(ADDRESS_PREFIX):]
    namespace, name = rest.split(":", 1)
    return namespace, name


@dataclass
class ProjectFileSkill:
    """The duck-typed file-skill object the pipeline renders.

    Satisfies every verified ``getattr`` the existing pipeline performs:

    * BM25 tokenization (``skill_search_service.py:720-723``):
      ``name`` / ``description`` / ``content``.
    * Stage-3 candidate rendering + ``name.lower()`` map
      (``skill_search_service.py:901-912``): ``name`` / ``description``.
    * Injection formatter (``skill_injection_service.py:1019``): ``name``
      / ``content`` / ``id``.
    * A/B fast path (``skill_injection_service.py:639``): ``ab_test_group``
      / ``status``.
    * Requirement §4.1 contract: ``source`` + a stable ``address``; never
      a DB-UUID-shaped id.

    ``content`` is populated ONLY by fresh disk reads at corpus-assembly
    or load time (P1/FR-P1-3) — objects of this class are never cached.

    Additive fields beyond the original plan field set (each documented):

    * ``root_path`` — the resolved root this skill was read from (S5
      advisory: the Phase-3 provenance header renders ``project_id`` +
      ``root_path``; carrying it here keeps the renderer dependency-free).
    * ``version`` property — reads ``manifest["version"]`` (the
      ``skill_view`` header and the S5 provenance line render it).
    """

    id: str
    address: str
    name: str
    description: str
    content: str
    namespace: str
    project_id: str
    body_path: str  # RELATIVE to root_path (A5: validated pre-realpath)
    root_path: str = ""
    category: Optional[str] = None
    status: str = "active"
    is_active: bool = True
    generation: int = 0
    ab_test_group: Optional[str] = None
    source: str = "project-file"
    body_char_len: int = 0
    manifest: dict[str, Any] = field(default_factory=dict)
    digest: str = ""
    shadows: list[str] = field(default_factory=list)

    @property
    def version(self) -> str:
        """The manifest semver string (empty when absent)."""
        return str((self.manifest or {}).get("version", "") or "")

    def to_summary(self) -> dict[str, Any]:
        """JSON-safe summary (no body bytes) for list surfaces."""
        return {
            "id": self.id,
            "address": self.address,
            "name": self.name,
            "description": self.description,
            "namespace": self.namespace,
            "project_id": self.project_id,
            "root_path": self.root_path,
            "body_path": self.body_path,
            "body_char_len": self.body_char_len,
            "category": self.category,
            "version": self.version,
            "source": self.source,
            "status": self.status,
            "is_active": self.is_active,
            "generation": self.generation,
            "digest": self.digest,
            "shadows": list(self.shadows),
            "manifest": dict(self.manifest or {}),
        }


@dataclass
class SkillIndexMeta:
    """METADATA-ONLY cache entry (A16) — the object the index cache holds.

    Deliberately has NO ``content`` field: the A16 invariant is enforced
    by construction. freshness stat tuple + digests drive the per-access
    invalidation gate (D-Q2): stat fast-path first, sha256 digest on
    suspicion.
    """

    pseudo_id: str
    address: str
    name: str
    description: str
    category: Optional[str]
    namespace: str
    project_id: str
    body_path: str  # relative to root
    body_char_len: int
    manifest: dict[str, Any]
    manifest_sha256: str
    body_sha256: str
    digest: str  # sha256(manifest_bytes + body_bytes) — the invalidation digest
    manifest_mtime_ns: int
    manifest_size: int
    body_mtime_ns: int = 0
    body_size: int = 0
    valid: bool = True
    last_error: Optional[str] = None
    embedded_at: Optional[str] = None

    def to_row(self, root_path: str, indexed_at: str) -> dict[str, Any]:
        """Flatten into a ``project_skill_files`` row dict (metadata only)."""
        return {
            "id": self.pseudo_id,
            "project_id": self.project_id,
            "root_path": root_path,
            "namespace": self.namespace,
            "name": self.name,
            "address": self.address,
            "description": self.description,
            "category": self.category,
            "body_path": self.body_path,
            "manifest_sha256": self.manifest_sha256,
            "body_sha256": self.body_sha256,
            "body_char_len": self.body_char_len,
            "manifest_json": self.manifest_json_text(),
            "valid": self.valid,
            "last_error": self.last_error,
            "embedded_at": self.embedded_at,
            "indexed_at": indexed_at,
            "updated_at": indexed_at,
        }

    def manifest_json_text(self) -> str:
        """Serialized manifest metadata (P1-safe: metadata only by construction)."""
        import json

        try:
            return json.dumps(self.manifest or {}, sort_keys=True)
        except (TypeError, ValueError):
            return "{}"

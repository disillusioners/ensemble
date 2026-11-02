"""Loader for the committed Plane MCP catalog fixture (lane ``plane-pm-issue-tools``).

The fixture ``tests/fixtures/plane_catalog.json`` is the committed copy of the
lane's gate-(i) ``tools/list`` re-capture (2026-11-02, HTTP 200, 177 tools —
``.agents/shared/planning/plane-pm-issue-tools/gate-i-tools-list-recapture.md``).
It is the **hermetic** test input the Phase-2 coverage pack consumes: it must
not depend on a planning-dir file that is not shipped.

This module is the fixture's single producer-side loader. It exposes:

* :func:`load_catalog` — parse + shape-validate the fixture.
* :func:`catalog_names` / :func:`tool_properties` / :func:`tool_required` /
  :func:`tool_schema_tokens` — per-tool accessors.
* :func:`sorted_names_sha256` — recompute the sorted-name-set sha256 (FC2/FC10
  catalog-drift detector: a re-capture that changes the catalog changes this
  digest).
* :func:`assert_keep_set_matches_catalog` — the gate-(i) criterion-4
  **set-membership** assertion (P-G4 / X1.10): every raw verb named in
  ``gate-i-keep-set.json`` must be a member of the re-captured catalog. This is
  the mechanical form of FC1 — a verb in the keep-set that is absent from the
  catalog raises :class:`CatalogIntegrityError`.

No secrets: the fixture carries tool names, schema property names, required
params and token counts only — never a credential value.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "plane_catalog.json"
_KEEP_SET = (
    Path(__file__).resolve().parents[2]
    / ".agents/shared/planning/plane-pm-issue-tools/gate-i-keep-set.json"
)

_SHA_ALGORITHM = 'sha256( "\\n".join(sorted_names) + "\\n" ) over UTF-8'


class CatalogIntegrityError(AssertionError):
    """Raised when the keep-set artifact does not match the committed catalog.

    This is the FC1 condition: a verb the charter's reconciled vocabulary
    names is absent from the re-captured catalog ⇒ Path B re-opens.
    """


def fixture_path() -> Path:
    return _FIXTURE


def keep_set_path() -> Path:
    return _KEEP_SET


def load_catalog(path: Path | None = None) -> dict[str, Any]:
    """Load and shape-validate the catalog fixture.

    Raises:
        CatalogIntegrityError: if the fixture is missing, not JSON, or does not
            carry the required top-level fields.
    """
    p = path or _FIXTURE
    if not p.is_file():
        raise CatalogIntegrityError(f"plane catalog fixture not found: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CatalogIntegrityError(f"plane catalog fixture is not valid JSON: {exc}") from exc
    for field in ("sorted_names", "tool_schemas", "tool_count"):
        if field not in data:
            raise CatalogIntegrityError(
                f"plane catalog fixture missing required field {field!r}"
            )
    names = data["sorted_names"]
    schemas = data["tool_schemas"]
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise CatalogIntegrityError("sorted_names must be a list of strings")
    if sorted(names) != names:
        raise CatalogIntegrityError("sorted_names is not sorted")
    if len(set(names)) != len(names):
        raise CatalogIntegrityError("sorted_names contains duplicates")
    if data["tool_count"] != len(names):
        raise CatalogIntegrityError(
            f"tool_count {data['tool_count']} != len(sorted_names) {len(names)}"
        )
    missing_schemas = [n for n in names if n not in schemas]
    if missing_schemas:
        raise CatalogIntegrityError(
            f"tool_schemas missing entries for {len(missing_schemas)} names"
        )
    return data


def catalog_names(catalog: dict[str, Any] | None = None) -> list[str]:
    """The sorted raw verb names (no ``plane_`` prefix — that is daemon-side)."""
    data = catalog if catalog is not None else load_catalog()
    return list(data["sorted_names"])


def tool_properties(name: str, catalog: dict[str, Any] | None = None) -> list[str]:
    """The input-schema property names for one tool, sorted."""
    data = catalog if catalog is not None else load_catalog()
    entry = data["tool_schemas"].get(name)
    if entry is None:
        raise KeyError(name)
    return list(entry["properties"])


def tool_required(name: str, catalog: dict[str, Any] | None = None) -> list[str]:
    """The required params for one tool, sorted."""
    data = catalog if catalog is not None else load_catalog()
    entry = data["tool_schemas"].get(name)
    if entry is None:
        raise KeyError(name)
    return list(entry["required"])


def tool_schema_tokens(name: str, catalog: dict[str, Any] | None = None) -> int:
    """The per-tool inputSchema token count (tiktoken cl100k_base)."""
    data = catalog if catalog is not None else load_catalog()
    entry = data["tool_schemas"].get(name)
    if entry is None:
        raise KeyError(name)
    return int(entry["schema_tokens"])


def schema_token_total(catalog: dict[str, Any] | None = None) -> int:
    """The summed inputSchema token count over the whole catalog (FC9 input)."""
    data = catalog if catalog is not None else load_catalog()
    return int(data["schema_token_total"])


def compute_sorted_names_sha256(names: list[str]) -> str:
    """The canonical sorted-name-set digest (see ``sorted_names_sha256_algorithm``)."""
    return hashlib.sha256(("\n".join(sorted(names)) + "\n").encode("utf-8")).hexdigest()


def sorted_names_sha256(catalog: dict[str, Any] | None = None) -> str:
    """Recompute the fixture's sorted-name sha256 from its own name list."""
    data = catalog if catalog is not None else load_catalog()
    return compute_sorted_names_sha256(data["sorted_names"])


def load_keep_set(path: Path | None = None) -> dict[str, Any]:
    """Load the gate-(i) keep-set artifact (the planning-dir evidence file)."""
    p = path or _KEEP_SET
    if not p.is_file():
        raise CatalogIntegrityError(f"gate-i-keep-set.json not found: {p}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CatalogIntegrityError(f"gate-i-keep-set.json is not valid JSON: {exc}") from exc


def assert_keep_set_matches_catalog(
    keep_set: dict[str, Any] | None = None,
    catalog: dict[str, Any] | None = None,
    *,
    expected_count: int = 28,
) -> dict[str, Any]:
    """Gate-(i) criterion 4 as a **set-membership** check (P-G4 / X1.10).

    Asserts, mechanically:

    1. the keep-set artifact is machine-readable and carries exactly
       ``expected_count`` entries (28 — 8 work-item CRUD + 5 comments +
       10 cycles + 1 assignee + 4 discovery/typing reads);
    2. every entry carries BOTH forms (``raw_verb`` + ``daemon_name``) and the
       daemon name is exactly ``"plane_" + raw_verb``;
    3. **every raw verb is a member of the re-captured catalog** — the FC1
       condition (a keep-set verb absent from the catalog ⇒ RED);
    4. the artifact's own ``pruned_count`` and
       ``keep_set_sorted_names_sha256`` are internally consistent with its
       name list and the catalog's tool count.

    Returns a small verdict dict. Raises :class:`CatalogIntegrityError` on any
    violation (the RED leg).
    """
    ks = keep_set if keep_set is not None else load_keep_set()
    cat = catalog if catalog is not None else load_catalog()
    names = set(catalog_names(cat))

    entries = ks.get("keep_set")
    if not isinstance(entries, list):
        raise CatalogIntegrityError("keep_set must be a list of entries")
    if len(entries) != expected_count:
        raise CatalogIntegrityError(
            f"keep_set has {len(entries)} entries, expected exactly {expected_count}"
        )
    raws = [e.get("raw_verb") for e in entries]
    daemons = [e.get("daemon_name") for e in entries]
    if len(set(raws)) != len(raws):
        raise CatalogIntegrityError("keep_set contains duplicate raw verbs")
    for raw, daemon in zip(raws, daemons):
        if not isinstance(raw, str) or not raw:
            raise CatalogIntegrityError("keep_set entry missing raw_verb")
        if daemon != f"plane_{raw}":
            raise CatalogIntegrityError(
                f"daemon_name {daemon!r} is not 'plane_' + raw_verb {raw!r}"
            )
    absent = sorted(r for r in raws if r not in names)
    if absent:
        # THE FC1 CONDITION — gate-(i) failure action re-opens Path B.
        raise CatalogIntegrityError(
            "FC1: keep-set verbs absent from the re-captured catalog: "
            + ", ".join(absent)
        )
    pruned = ks.get("pruned_count")
    if pruned != cat["tool_count"] - len(entries):
        raise CatalogIntegrityError(
            f"pruned_count {pruned} != catalog {cat['tool_count']} - keep-set {len(entries)}"
        )
    declared = ks.get("keep_set_sorted_names_sha256")
    actual = compute_sorted_names_sha256(sorted(raws))
    if declared != actual:
        raise CatalogIntegrityError(
            f"keep_set_sorted_names_sha256 {declared} != recomputed {actual}"
        )
    return {
        "keep_set_count": len(entries),
        "all_raw_verbs_in_catalog": True,
        "pruned_count": pruned,
        "keep_set_sorted_names_sha256": actual,
        "catalog_sorted_names_sha256": sorted_names_sha256(cat),
        "catalog_tool_count": cat["tool_count"],
        "fc1_fires": False,
    }

"""Manifest loader + validator for the project skill source (Phase 1 task 2).

Implements the Q7 per-skill fail-loud validation contract with the
architect amendments:

* **A5 (MUST, CRITICAL) — path containment at index AND every read:**
  (a) namespace/name components validated ``^[A-Za-z0-9][A-Za-z0-9._-]*$``
  (b) manifest ``body`` / ``assets[]`` paths rejected PRE-realpath when
  absolute or containing ``..``; (c) ``realpath()`` containment inside
  ``realpath(root)/.agents/<ns>/<name>/`` — SKILL-DIR confinement, not
  merely root; (d) symlinked ``skill.json``/body/namespace-dir resolving
  outside the root refused with fail-loud reasons (``path_escape`` /
  ``symlink_escape`` / ``bad_component``). The SERVICE re-invokes
  :func:`validate_containment` on EVERY load-time read — serve-fresh
  applies to content freshness only, never as a containment bypass.
* **A12 (MUST) — scanner glob:** depth-exact
  ``<root>/.agents/<ns>/skills/<name>/skill.json``; ``skills`` matched
  case-sensitively as a DIRECTORY; one-level walk, no recursion;
  ``<ns>``/``<name>`` are any legal dirname validated per A5. The
  "namespace must be an AGENT dir name" leg is DELETED (contradicted
  P6) — ``.agents/shared/skills/<name>/`` is a legitimate ingest.
* **Dual-leg legacy exclusion:** the depth-exact glob is leg 1 (the
  legacy ``.agents/project-skills/bump-version/`` shape has no
  ``skills/`` segment ⇒ never matched); the ``schema_version == 1`` gate
  is leg 2 (that schema carries no ``schema_version`` ⇒ skipped even if
  handed to the validator directly). Both legs pinned by the FR-VAL-1
  anti-conflation test with ``bump-version`` as the named negative
  fixture.

Every failure returns a structured reason (never raises into the scan
loop) so one bad skill cannot poison a root scan (Q7 per-skill
isolation); the SERVICE aggregates reasons into ``invalid_skills`` and
persists ``last_error``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

from .model import SkillIndexMeta, mint_address, mint_pseudo_id, valid_component

logger = logging.getLogger(__name__)

#: schema v1 (the de-facto canon spec, requirements §4.4).
SCHEMA_VERSION = 1

#: Hard caps (D-Q2): manifest ≤16 KB, body ≤64 KB, ≤64 namespaces,
#: ≤128 skill dirs per namespace. Enforced at scan time.
MANIFEST_MAX_BYTES = 16 * 1024
BODY_MAX_BYTES = 64 * 1024
MAX_NAMESPACES = 64
MAX_SKILL_DIRS_PER_NAMESPACE = 128

#: Q7 required manifest keys (each a non-empty string when present).
REQUIRED_KEYS = ("name", "description", "body")

#: A5 fail-loud reason vocabulary (frozen — pinned by the validation matrix).
REASON_BAD_COMPONENT = "bad_component"
REASON_PATH_ESCAPE = "path_escape"
REASON_SYMLINK_ESCAPE = "symlink_escape"
REASON_INVALID_JSON = "invalid_json"
REASON_UNKNOWN_SCHEMA = "unknown_schema"
REASON_MISSING_FIELD = "missing_field"
REASON_NAME_MISMATCH = "name_mismatch"
REASON_MANIFEST_TOO_LARGE = "manifest_too_large"
REASON_BODY_MISSING = "body_missing"
REASON_BODY_EMPTY = "body_empty"
REASON_BODY_TOO_LARGE = "body_too_large"
REASON_UNRESOLVED_LOAD = "unresolved_load"
REASON_MISSING_ASSET = "missing_asset"
REASON_BAD_SUPERSEDES = "bad_supersedes"
REASON_MANIFEST_UNREADABLE = "manifest_unreadable"


@dataclass
class ParsedSkill:
    """The outcome of validating one skill dir (Q7 per-skill result)."""

    ok: bool
    reason: Optional[str] = None  # one of the REASON_* constants when not ok
    detail: str = ""  # human context for WARNING logs / last_error
    meta: Optional[SkillIndexMeta] = None
    debug_notes: list[str] = field(default_factory=list)  # unknown-field names etc.


def _now_meta(
    root_real: str,
    namespace: str,
    name: str,
    project_id: str,
    manifest: dict[str, Any],
    manifest_bytes: bytes,
    body_bytes: bytes,
    body_rel: str,
    manifest_stat: os.stat_result,
    body_stat: Optional[os.stat_result] = None,
) -> SkillIndexMeta:
    digest = hashlib.sha256(manifest_bytes + body_bytes).hexdigest()
    return SkillIndexMeta(
        pseudo_id=mint_pseudo_id(root_real, project_id, namespace, name),
        address=mint_address(namespace, name),
        name=name,
        description=str(manifest.get("description", "") or ""),
        category=(str(manifest["category"]) if manifest.get("category") is not None else None),
        namespace=namespace,
        project_id=project_id,
        body_path=body_rel,
        body_char_len=len(body_bytes.decode("utf-8", errors="replace")),
        manifest=manifest,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        body_sha256=hashlib.sha256(body_bytes).hexdigest(),
        digest=digest,
        manifest_mtime_ns=manifest_stat.st_mtime_ns,
        manifest_size=manifest_stat.st_size,
        body_mtime_ns=body_stat.st_mtime_ns if body_stat else 0,
        body_size=body_stat.st_size if body_stat else 0,
    )


def validate_component(value: str, what: str) -> Optional[str]:
    """A5(a): component check. Returns a fail reason or None."""
    if not value or not valid_component(value):
        return f"{REASON_BAD_COMPONENT}: illegal {what} component {value!r}"
    return None


def _reject_relpath_pre_realpath(rel: str, what: str) -> Optional[str]:
    """A5(b): pre-realpath rejection of absolute / ``..``-carrying paths."""
    if not isinstance(rel, str) or not rel:
        return f"{REASON_MISSING_FIELD}: {what} path empty"
    if os.path.isabs(rel):
        return f"{REASON_PATH_ESCAPE}: {what} path is absolute: {rel!r}"
    parts = rel.replace("\\", "/").split("/")
    if any(part == ".." for part in parts):
        return f"{REASON_PATH_ESCAPE}: {what} path contains '..': {rel!r}"
    return None


def skill_dir_paths(root: str, namespace: str, name: str) -> tuple[str, str]:
    """Return ``(expected_real_skill_dir, naive_skill_dir)`` for A5 checks.

    The canon path is depth-exact ``<root>/.agents/<ns>/skills/<name>/``
    (A12) — the ``skills`` segment is part of the canonical identity.
    """
    naive = os.path.join(root, ".agents", namespace, "skills", name)
    return os.path.realpath(naive), naive


def validate_containment(root: str, namespace: str, name: str) -> Optional[str]:
    """A5(c)+(d): skill-dir confinement under ``realpath(root)/.agents``.

    Pure-filesystem check with NO manifest knowledge — this is the gate
    the service re-invokes on EVERY load-time read (serve-fresh never
    bypasses containment).
    """
    reason = validate_component(namespace, "namespace")
    if reason:
        return reason
    reason = validate_component(name, "skill")
    if reason:
        return reason
    root_real = os.path.realpath(root)
    expected_prefix = os.path.join(root_real, ".agents")
    expected_real, _naive = skill_dir_paths(root, namespace, name)
    # (c) the REAL skill dir must sit under realpath(root)/.agents/... —
    # a symlinked ns/name dir is fine ONLY if it stays inside the root's
    # own .agents tree (A5(d): outside-root symlinks refused).
    if not expected_real.startswith(expected_prefix + os.sep):
        return (
            f"{REASON_SYMLINK_ESCAPE}: skill dir resolves outside the root "
            f"({expected_real!r} not under {expected_prefix!r})"
        )
    if expected_real != os.path.join(expected_prefix, namespace, "skills", name):
        return (
            f"{REASON_SYMLINK_ESCAPE}: skill dir symlinked away from its "
            f"canonical path ({expected_real!r})"
        )
    return None


def _validate_file_inside_skill_dir(
    skill_dir_real: str, rel: str, what: str
) -> tuple[Optional[str], Optional[str]]:
    """A5(c) for one referenced file: containment inside the SKILL DIR.

    Returns ``(fail_reason_or_None, realpath_or_None)``.
    """
    pre = _reject_relpath_pre_realpath(rel, what)
    if pre:
        return pre, None
    resolved = os.path.realpath(os.path.join(skill_dir_real, rel))
    if not resolved.startswith(skill_dir_real + os.sep):
        return (
            f"{REASON_SYMLINK_ESCAPE}: {what} resolves outside the skill dir "
            f"({resolved!r} not under {skill_dir_real!r})"
        ), resolved
    return None, resolved


def discover_skill_dirs(root: str) -> tuple[list[tuple[str, str]], list[str]]:
    """A12 scanner: depth-exact ``<root>/.agents/<ns>/skills/<name>/`` glob.

    Returns ``(candidates, skip_notes)`` where candidates are
    ``(namespace, skill_dir_name)`` pairs whose ``skill.json`` exists.
    ``skills`` is matched case-sensitively as a directory; a FILE named
    ``skills`` is skipped (no crash); there is NO recursion; caps are
    enforced (≤64 namespaces, ≤128 skill dirs/ns).
    """
    candidates: list[tuple[str, str]] = []
    notes: list[str] = []
    agents_dir = os.path.join(root, ".agents")
    if not os.path.isdir(agents_dir):
        return candidates, [f"no .agents tree under {root!r}"]
    namespaces = sorted(
        entry
        for entry in os.listdir(agents_dir)
        if os.path.isdir(os.path.join(agents_dir, entry))
    )
    if len(namespaces) > MAX_NAMESPACES:
        notes.append(
            f"namespace cap hit: {len(namespaces)} > {MAX_NAMESPACES}; "
            f"truncating to first {MAX_NAMESPACES} (sorted)"
        )
        namespaces = namespaces[:MAX_NAMESPACES]
    for ns in namespaces:
        skills_dir = os.path.join(agents_dir, ns, "skills")
        if os.path.islink(skills_dir) and not os.path.isdir(skills_dir):
            notes.append(f"ns={ns}: broken skills symlink; skipped")
            continue
        if not os.path.isdir(skills_dir):
            # A FILE named ``skills`` (or nothing) — skip silently per A12
            # ("skills as a file (no crash)"); a plain absence is the norm.
            continue
        try:
            entries = sorted(
                entry
                for entry in os.listdir(skills_dir)
                if os.path.isdir(os.path.join(skills_dir, entry))
            )
        except OSError as exc:
            notes.append(f"ns={ns}: skills dir unreadable ({exc}); skipped")
            continue
        if len(entries) > MAX_SKILL_DIRS_PER_NAMESPACE:
            notes.append(
                f"ns={ns}: dir cap hit {len(entries)} > {MAX_SKILL_DIRS_PER_NAMESPACE}; truncating"
            )
            entries = entries[:MAX_SKILL_DIRS_PER_NAMESPACE]
        for name in entries:
            manifest_path = os.path.join(skills_dir, name, "skill.json")
            if os.path.isfile(manifest_path):
                candidates.append((ns, name))
    return candidates, notes


def _validate_supersedes(manifest: dict[str, Any]) -> Optional[str]:
    sup = manifest.get("supersedes")
    if sup is None:
        return None
    if not isinstance(sup, dict):
        return f"{REASON_BAD_SUPERSEDES}: supersedes must be an object"
    store = sup.get("store")
    skill_id = sup.get("skill_id")
    if not isinstance(store, str) or not store:
        return f"{REASON_BAD_SUPERSEDES}: supersedes.store missing/not a string"
    if not isinstance(skill_id, str) or not skill_id:
        return f"{REASON_BAD_SUPERSEDES}: supersedes.skill_id missing/not a string"
    return None


def parse_and_validate(
    root: str,
    namespace: str,
    skill_dir_name: str,
    project_id: str,
    allow_unknown_schema_warning: bool = True,
) -> ParsedSkill:
    """Validate one skill dir end-to-end (Q7 rule order, A5 containment).

    Never raises into the scan loop. ``.ok=False`` carries a frozen
    ``reason`` + human ``detail``.
    """
    for label, value in (("namespace", namespace), ("skill", skill_dir_name)):
        reason = validate_component(value, label)
        if reason:
            return ParsedSkill(ok=False, reason=REASON_BAD_COMPONENT, detail=reason)

    containment = validate_containment(root, namespace, skill_dir_name)
    if containment:
        return ParsedSkill(
            ok=False,
            reason=REASON_SYMLINK_ESCAPE
            if containment.startswith(REASON_SYMLINK_ESCAPE)
            else REASON_PATH_ESCAPE,
            detail=containment,
        )

    _expected_real, naive_dir = skill_dir_paths(root, namespace, skill_dir_name)
    manifest_path = os.path.join(naive_dir, "skill.json")

    try:
        mstat = os.stat(manifest_path)
    except OSError as exc:
        return ParsedSkill(
            ok=False, reason=REASON_MANIFEST_UNREADABLE, detail=f"stat failed: {exc}"
        )
    if mstat.st_size > MANIFEST_MAX_BYTES:
        return ParsedSkill(
            ok=False,
            reason=REASON_MANIFEST_TOO_LARGE,
            detail=f"skill.json is {mstat.st_size} bytes > {MANIFEST_MAX_BYTES}",
        )
    try:
        with open(manifest_path, "rb") as fh:
            manifest_bytes = fh.read()
    except OSError as exc:
        return ParsedSkill(
            ok=False, reason=REASON_MANIFEST_UNREADABLE, detail=f"read failed: {exc}"
        )
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        return ParsedSkill(
            ok=False, reason=REASON_INVALID_JSON, detail=f"skill.json: {exc}"
        )
    if not isinstance(manifest, dict):
        return ParsedSkill(
            ok=False, reason=REASON_INVALID_JSON, detail="skill.json is not a JSON object"
        )

    debug_notes: list[str] = []
    known_keys = {
        "schema_version",
        "name",
        "version",
        "description",
        "category",
        "scope",
        "tiers",
        "body",
        "assets",
        "loads",
        "supersedes",
    }
    extra = sorted(set(manifest.keys()) - known_keys)
    if extra:
        debug_notes.extend(f"unknown manifest field: {key}" for key in extra)
        logger.debug(
            "[ProjectSkillSource] unknown manifest fields tolerated (forward-compat): %s%s",
            extra,
            f" @ {manifest_path}" if allow_unknown_schema_warning else "",
        )

    # Leg 2 of the dual-leg legacy exclusion: schema_version == 1 gate.
    # Strict typing: a BOOL True compares equal to 1 in Python — reject
    # non-integer schema_version outright (§4.4 "wrong types" → skip).
    schema_version = manifest.get("schema_version")
    if not isinstance(schema_version, int) or isinstance(schema_version, bool) or (
        schema_version != SCHEMA_VERSION
    ):
        return ParsedSkill(
            ok=False,
            reason=REASON_UNKNOWN_SCHEMA,
            detail=(
                f"schema_version={schema_version!r} != {SCHEMA_VERSION} "
                "(unknown schema — skipped, never guessed)"
            ),
        )

    for key in REQUIRED_KEYS:
        value = manifest.get(key)
        if not isinstance(value, str) or not value.strip():
            return ParsedSkill(
                ok=False,
                reason=REASON_MISSING_FIELD,
                detail=f"required manifest key {key!r} missing/empty",
            )

    if manifest["name"] != skill_dir_name:
        return ParsedSkill(
            ok=False,
            reason=REASON_NAME_MISMATCH,
            detail=f"manifest name {manifest['name']!r} != dir name {skill_dir_name!r}",
        )

    body_rel = manifest["body"]
    skill_dir_real = os.path.realpath(naive_dir)
    fail, body_real = _validate_file_inside_skill_dir(skill_dir_real, body_rel, "body")
    if fail:
        reason = (
            REASON_PATH_ESCAPE
            if fail.startswith(REASON_PATH_ESCAPE)
            else REASON_SYMLINK_ESCAPE if fail.startswith(REASON_SYMLINK_ESCAPE)
            else REASON_MISSING_FIELD
        )
        return ParsedSkill(ok=False, reason=reason, detail=fail)
    if not os.path.isfile(body_real):
        return ParsedSkill(
            ok=False,
            reason=REASON_BODY_MISSING,
            detail=f"body file not found: {body_rel!r}",
        )
    try:
        bstat = os.stat(body_real)
        if bstat.st_size > BODY_MAX_BYTES:
            return ParsedSkill(
                ok=False,
                reason=REASON_BODY_TOO_LARGE,
                detail=f"body is {bstat.st_size} bytes > {BODY_MAX_BYTES} (indexed-as-listed class)",
            )
        with open(body_real, "rb") as fh:
            body_bytes = fh.read()
    except OSError as exc:
        return ParsedSkill(
            ok=False, reason=REASON_BODY_MISSING, detail=f"body unreadable: {exc}"
        )
    if not body_bytes.strip():
        return ParsedSkill(
            ok=False, reason=REASON_BODY_EMPTY, detail="body file is empty"
        )

    # loads[] / assets[] reference validation (Q6 v1: index-time validation)
    loads = manifest.get("loads") or []
    if not isinstance(loads, list):
        return ParsedSkill(
            ok=False, reason=REASON_UNRESOLVED_LOAD, detail="loads[] must be an array"
        )
    skills_dir = os.path.join(os.path.dirname(naive_dir))  # .../skills
    for dep in loads:
        if not isinstance(dep, str) or not valid_component(dep):
            return ParsedSkill(
                ok=False,
                reason=REASON_UNRESOLVED_LOAD,
                detail=f"loads[] entry not a legal component: {dep!r}",
            )
        dep_dir = os.path.join(skills_dir, dep)
        if not os.path.isdir(dep_dir) or not os.path.isfile(
            os.path.join(dep_dir, "skill.json")
        ):
            return ParsedSkill(
                ok=False,
                reason=REASON_UNRESOLVED_LOAD,
                detail=f"loads[] dependency {dep!r} not found in namespace {namespace!r}",
            )

    assets = manifest.get("assets") or []
    if not isinstance(assets, list):
        return ParsedSkill(
            ok=False, reason=REASON_MISSING_ASSET, detail="assets[] must be an array"
        )
    for asset in assets:
        fail, asset_real = _validate_file_inside_skill_dir(
            skill_dir_real, asset, "asset"
        )
        if fail:
            reason = (
                REASON_PATH_ESCAPE
                if fail.startswith(REASON_PATH_ESCAPE)
                else REASON_SYMLINK_ESCAPE if fail.startswith(REASON_SYMLINK_ESCAPE)
                else REASON_MISSING_ASSET
            )
            return ParsedSkill(ok=False, reason=reason, detail=fail)
        if not os.path.exists(asset_real):
            return ParsedSkill(
                ok=False,
                reason=REASON_MISSING_ASSET,
                detail=f"asset path does not exist: {asset!r}",
            )

    sup_fail = _validate_supersedes(manifest)
    if sup_fail:
        return ParsedSkill(ok=False, reason=REASON_BAD_SUPERSEDES, detail=sup_fail)

    meta = _now_meta(
        root_real=os.path.realpath(root),
        namespace=namespace,
        name=skill_dir_name,
        project_id=project_id,
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        body_bytes=body_bytes,
        body_rel=body_rel,
        manifest_stat=mstat,
        body_stat=bstat,
    )
    return ParsedSkill(ok=True, meta=meta, debug_notes=debug_notes)

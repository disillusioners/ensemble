"""The ProjectSkillSourceService — index + validation core (Phase 1).

The spine of the project-skill-source-loader commission:

* **Resolver** (:meth:`resolve_active_project`) — the ONE canonical
  ``instance_id → (project_id, file_roots)`` turn (D-Q1). Rung 1 is
  CONFIRM-ONLY (A6): an explicit project may confirm the instance-derived
  one, never widen — a conflict REFUSES the file corpus for that call
  (WARNING with both values; DB results unaffected). The W-5
  EXTRA_ROOTS leg realpaths each entry and consumes it ONLY when it
  binds to the caller's resolved project via ``get_by_directory``
  semantics (main_directory == entry OR related_directories contains
  entry) — fail-closed, the knob can never become a P3 cross-project
  read vector.
* **Lazy index** (:meth:`ensure_index`) — idempotent, per-root
  in-process lock so concurrent first-touches coalesce (NFR-IDEM-2).
  Nothing at boot beyond construction (D-Q2). Hard caps: ≤64
  namespaces / ≤128 skill dirs per namespace / manifest ≤16 KB /
  body ≤64 KB.
* **Invalidation** — per-access freshness gate: stat fast-path
  (mtime+size per manifest) → sha256 digest on suspicion → re-parse
  changed / drop vanished / schedule embedding refresh async
  (fire-and-forget — a git pull never adds latency to a turn).
* **A4 lifecycle rules** — (i) wholesale corpus swap under the per-root
  lock; (ii) index mutation never blocks or fails a search; (iii) the
  vector for a skill is generated from the SAME root's body that will
  be served (A3 root-keyed pseudo-ids make this hold by construction);
  (iv) delete-with-row; (v) gc sweep for orphaned embedding rows;
  (vi) delete-AFTER-success; (vii) single-flight + digest-stability
  debounce per pseudo-id (branch toggling must not thrash MAIN-model
  LLM calls — S1).
* **Embedding refresh dispatch (Q3/A1)** — trigger queries generated
  through the EXISTING ``SkillEmbeddingService`` generation pattern
  (``generate_trigger_queries`` + ``embed_text`` — pure LLM calls that
  import zero evolution machinery, F1-confirmed), persisted into the
  DEDICATED no-FK ``project_skill_embeddings`` table. Provider
  absent/error ⇒ BM25-only, never fails a search.
* **A16** — the in-process cache holds :class:`SkillIndexMeta` objects
  (METADATA ONLY); corpus/load objects get fresh disk reads at call
  time.

Phase-1 consumers: Phase 2 (``corpus_for`` — the frozen corpus
contract) and Phase 3 (``lookup_explicit`` — the frozen load contract).
This service performs ZERO writes into any FK-constrained child table
of ``skills`` (the A2/D2 census rule — see the pinned census artifact
in the planning dir); its writes land exclusively in the lane's own
three tables.
"""

from __future__ import annotations

import asyncio
import errno
import hashlib
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from daemon.repositories.project_skill_file.models import (
    ProjectSkillFile,
)
from daemon.repositories.project_skill_file.repository import (
    ProjectSkillEmbeddingRepository,
    ProjectSkillFileRepository,
)
from daemon.services.project_skill_source.manifest import (
    BODY_MAX_BYTES,
    REASON_BODY_MISSING,
    REASON_BODY_TOO_LARGE,
    REASON_UNKNOWN_SCHEMA,
    ParsedSkill,
    _validate_file_inside_skill_dir,
    discover_skill_dirs,
    parse_and_validate,
    validate_containment,
)
from daemon.services.project_skill_source.model import (
    ProjectFileSkill,
    SkillIndexMeta,
    is_pf_address,
    mint_address,
    mint_pseudo_id,
    parse_pf_address,
)

logger = logging.getLogger(__name__)

#: A17 insurance cap — accept-unbounded WITH instruments, but refuse
#: (INFO, not error) roots beyond this many for one project. No
#: legitimate project canon approaches this; the cap exists so a
#: misconfigured EXTRA_ROOTS/monorepo cannot turn one resolution into
#: an unbounded directory walk.
MAX_ROOTS = 32

#: Env knob for the W-5 dev-only extra roots (comma-separated absolute
#: paths). Binding is enforced per-entry against the caller's resolved
#: project — fail-closed.
EXTRA_ROOTS_ENV = "ENSEMBLE_PROJECT_SKILL_SOURCE_EXTRA_ROOTS"

#: A4(vii): the digest-stability debounce — a refresh fires only after
#: the digest has held stable for this long (bounded backoff below).
EMBED_REFRESH_DEBOUNCE_S = 5.0
#: A4(vii): the bounded backoff ceiling for rescheduled refreshes.
EMBED_REFRESH_BACKOFF_MAX_S = 60.0

#: The resolver's instance-metadata key for the (inert-until-v2) work_dir
#: convention (PA-A3).
WORK_DIR_METADATA_KEY = "work_dir"


class ProjectSkillBodyUnavailable(Exception):
    """D-Q7: the body is missing/corrupt/contained-unsafe at LOAD time.

    Phase 3 renders this into the explicit error envelope — never an
    empty injection, never a silent fallback.
    """

    def __init__(self, address: str, reason: str) -> None:
        super().__init__(
            f"file skill {address} body unreadable: {reason}; re-index scheduled"
        )
        self.address = address
        self.reason = reason


class ExplicitConflictRefused(Exception):
    """A6: the explicit project param conflicts with the instance-derived
    one — the file corpus is REFUSED for this call (DB results
    unaffected). Carries both values for the WARNING / envelope."""


@dataclass
class RootScanStats:
    """Aggregate counters for one ensure_index pass."""

    root_path: str
    project_id: str
    skills_indexed: int = 0
    invalid_skills: int = 0
    namespaces: int = 0
    changed: int = 0
    vanished: int = 0
    db_upserted: int = 0
    db_deleted: int = 0
    last_scan_ms: float = 0.0
    last_scan_at: str = ""
    full_scan: bool = False
    last_error: Optional[str] = None
    skip_notes: list[str] = field(default_factory=list)


@dataclass
class _RootIndex:
    """The per-root in-process snapshot (A16: metadata only)."""

    root_path: str
    project_id: str
    metas: dict[str, SkillIndexMeta] = field(default_factory=dict)  # by pseudo-id
    invalid: list[dict[str, str]] = field(default_factory=list)
    stats: RootScanStats = field(default=None)  # type: ignore[assignment]
    invalid_schema_warned: bool = False  # warn-once-per-root state (Q7)


@dataclass
class Resolution:
    """The resolver's outcome (D-Q1 + A6 + W-5)."""

    project_id: Optional[str]
    roots: list[str] = field(default_factory=list)
    refused: bool = False
    refusal_reason: Optional[str] = None
    drift: Optional[tuple[str, str]] = None  # (metadata project_id, row project_id)


class ProjectSkillSourceService:
    """File-backed project skill source (Phase 1 core)."""

    def __init__(
        self,
        instance_repository: Any = None,
        project_repository: Any = None,
        file_repository: Optional[ProjectSkillFileRepository] = None,
        embedding_repository: Optional[ProjectSkillEmbeddingRepository] = None,
        embedding_service: Any = None,
        llm_config: Optional[dict[str, Any]] = None,
        extra_roots: Optional[list[str]] = None,
        refresh_debounce_s: float = EMBED_REFRESH_DEBOUNCE_S,
    ) -> None:
        """Construct the service (cheap — no LLM, no DB, no scan; Q8b).

        ``extra_roots`` overrides the ``EXTRA_ROOTS_ENV`` env read
        (tests); ``None`` reads the environment at resolve time.
        """
        self._instance_repository = instance_repository
        self._project_repository = project_repository
        self._files = file_repository
        self._embeddings = embedding_repository
        self._embedding_service = embedding_service
        self._llm_config = dict(llm_config) if llm_config else {}
        self._extra_roots_override = extra_roots
        self._refresh_debounce_s = refresh_debounce_s

        self._roots: dict[str, _RootIndex] = {}
        self._root_locks: dict[str, asyncio.Lock] = {}
        self._inflight_refreshes: set[str] = set()
        self._refresh_tasks: set[asyncio.Task[Any]] = set()

    def set_embedding_service(self, embedding_service: Any) -> None:
        """Inject the (optional) trigger-query embedding service post-
        construction (Phase-4 manager wiring).

        The manager constructs this service UNCONDITIONALLY but the
        skill-evolution embedding service is only built when the
        evolution config is present. This setter lets the manager bind
        it later without reordering its init sequence (the
        ``set_file_source`` / ``set_clone_service`` pattern). Left
        ``None`` (evolution absent) the A4(vii) refresh degrades
        fail-soft — file skills stay BM25-only with neutral rerank
        scores.

        Args:
            embedding_service: Duck-typed
                :class:`~daemon.services.skill_embedding_service.SkillEmbeddingService`
                (``embed_text``-shaped) or ``None``.
        """
        self._embedding_service = embedding_service

    def file_embeddings_for(self, skill_id: str) -> list[Any]:
        """Stage-2 dual-source adapter (Phase 4, leader-commissioned).

        Returns the cached trigger-query embedding rows for a FILE
        skill from the DEDICATED ``project_skill_embeddings`` table
        (A1 — the legacy ``skill_embeddings`` table is FK-walled
        against ``pf-`` ids). Rows carry ``.embedding`` (list[float]),
        the same consumption shape as the DB repo's rows, so
        ``SkillSearchService._embedding_rerank`` scores them through
        the identical cosine path.

        Fail-soft: no embedding repository, a missing table, or a read
        error all return ``[]`` (neutral 0.0 in the rerank — the
        pre-adapter behavior for file skills). Never raises into the
        search path.
        """
        if self._embeddings is None:
            return []
        try:
            return list(self._embeddings.list_by_skill(skill_id) or [])
        except Exception as exc:  # neutral — DB rerank must not wobble
            logger.debug(
                "[ProjectSkillSource] file-embedding lookup failed for "
                "%s: %s (neutral rerank)",
                skill_id,
                exc,
            )
            return []

    # ------------------------------------------------------------------
    # Resolver (D-Q1 + A6 + W-5) — the SANCTIONED SCOPE READER for new code
    # ------------------------------------------------------------------

    async def resolve_active_project(
        self,
        instance_id: Optional[str] = None,
        explicit_project_id: Optional[str] = None,
    ) -> Resolution:
        """Turn ``instance_id → (project_id, file_roots)`` (the only entry).

        Priority chain (D-Q1): (1) explicit — CONFIRM-ONLY per A6;
        (2) ``instance_metadata["project_id"]``; (3)
        ``instances.project_id`` row column (drift ⇒ proceed with 2 +
        WARNING); (4) None ⇒ file skills entirely invisible. File-root
        chain: ``work_dir`` hook (bound-checked) →
        ``projects.main_directory`` → project-bound EXTRA_ROOTS entries
        (W-5) → graceful zero.
        """
        derived: Optional[str] = None
        drift: Optional[tuple[str, str]] = None
        work_dir: Optional[str] = None

        if instance_id and self._instance_repository is not None:
            try:
                inst = await asyncio.to_thread(
                    self._instance_repository.get, instance_id
                )
            except Exception as exc:  # fail-soft: a broken repo must not break resolution
                logger.warning(
                    "[ProjectSkillSource] instance lookup failed for %s: %s",
                    str(instance_id)[:8],
                    exc,
                )
                inst = None
            if inst is not None:
                meta = getattr(inst, "instance_metadata", None) or {}
                meta_pid = meta.get("project_id") or None
                row_pid = getattr(inst, "project_id", None) or None
                work_dir = meta.get(WORK_DIR_METADATA_KEY) or None
                if meta_pid:
                    derived = str(meta_pid)
                elif row_pid:
                    derived = str(row_pid)
                if meta_pid and row_pid and str(meta_pid) != str(row_pid):
                    drift = (str(meta_pid), str(row_pid))
                    logger.warning(
                        "[ProjectSkillSource] project-scope drift for instance "
                        "%s: metadata=%s row=%s — proceeding with metadata "
                        "value (D-Q1)",
                        str(instance_id)[:8],
                        meta_pid,
                        row_pid,
                    )

        # A6 — rung 1 is CONFIRM-ONLY, never widening. When the caller
        # supplies an explicit project that is not exactly the
        # instance-derived one (including when there IS no
        # instance-derived one — the REST shape), the file corpus is
        # REFUSED for this call; DB results are unaffected.
        if explicit_project_id is not None and str(explicit_project_id) != (derived or None):
            logger.warning(
                "[ProjectSkillSource] explicit project conflict — refusing the "
                "file corpus for this call (A6): explicit=%s derived=%s",
                explicit_project_id,
                derived,
            )
            return Resolution(
                project_id=derived,
                roots=[],
                refused=True,
                refusal_reason="explicit_project_conflict",
                drift=drift,
            )

        if not derived:
            return Resolution(project_id=None, roots=[], drift=drift)

        roots = await self._resolve_roots(derived, instance_id, work_dir)
        return Resolution(project_id=derived, roots=roots, drift=drift)

    async def _resolve_roots(
        self,
        project_id: str,
        instance_id: Optional[str],
        work_dir: Optional[str],
    ) -> list[str]:
        """File-root chain: work_dir hook → main_directory → EXTRA_ROOTS."""
        roots: list[str] = []
        main_dir: Optional[str] = None

        if self._project_repository is not None:
            try:
                project = await asyncio.to_thread(
                    self._project_repository.get, project_id
                )
            except Exception as exc:
                logger.warning(
                    "[ProjectSkillSource] project lookup failed for %s: %s",
                    project_id[:8],
                    exc,
                )
                project = None
            if project is not None:
                main_dir = getattr(project, "main_directory", None) or None

        if work_dir:
            # Inert-until-v2 convention (PA-A3) — but bound-checked with
            # the same W-5 predicate so the hook can never become a
            # cross-project read vector.
            accepted = await self._bind_extra_root(work_dir, project_id)
            if accepted:
                roots.append(accepted)
            else:
                logger.warning(
                    "[ProjectSkillSource] work_dir %r does not bind to project "
                    "%s — ignored (fail-closed)",
                    work_dir,
                    project_id[:8],
                )

        if main_dir and os.path.isdir(main_dir):
            roots.append(os.path.realpath(main_dir))

        roots.extend(await self._resolve_extra_roots(project_id))
        # Dedupe, preserve order.
        seen: set[str] = set()
        ordered: list[str] = []
        for root in roots:
            if root not in seen:
                seen.add(root)
                ordered.append(root)
        # A17 insurance: INFO-refuse roots beyond MAX_ROOTS. Legit
        # projects never approach this; a runaway EXTRA_ROOTS /
        # monorepo misconfig must not turn one resolution into an
        # unbounded walk. Refusal is INFO-level (misconfiguration
        # signal, not a health event) and deterministic (first-32 in
        # resolver order).
        if len(ordered) > MAX_ROOTS:
            dropped = ordered[MAX_ROOTS:]
            logger.info(
                "[ProjectSkillSource] root cap: project %s resolved %d "
                "roots — refusing %d beyond MAX_ROOTS=%d (first refused: "
                "%s)",
                project_id[:8],
                len(ordered),
                len(dropped),
                MAX_ROOTS,
                dropped[0],
            )
            ordered = ordered[:MAX_ROOTS]
        return ordered

    async def _bind_extra_root(self, entry: str, project_id: str) -> Optional[str]:
        """W-5 binding predicate: realpath(entry) must resolve to the
        caller's project via get_by_directory semantics
        (main_directory == entry OR related_directories contains entry).
        Returns the realpath when bound, else None."""
        if self._project_repository is None:
            return None
        try:
            resolved = os.path.realpath(entry)
            projects = await asyncio.to_thread(
                self._project_repository.get_by_directory, resolved
            )
        except Exception as exc:
            logger.warning(
                "[ProjectSkillSource] extra-root lookup failed for %r: %s",
                entry,
                exc,
            )
            return None
        for project in projects or []:
            if str(getattr(project, "project_id", "")) == str(project_id):
                return resolved
        return None

    def _configured_extra_roots(self) -> list[str]:
        if self._extra_roots_override is not None:
            return [entry for entry in self._extra_roots_override if entry]
        raw = os.environ.get(EXTRA_ROOTS_ENV, "") or ""
        return [entry.strip() for entry in raw.split(",") if entry.strip()]

    async def _resolve_extra_roots(self, project_id: str) -> list[str]:
        """W-5: consume ONLY entries that bind to the caller's project;
        ignore the rest with a WARNING (fail-closed)."""
        accepted: list[str] = []
        for entry in self._configured_extra_roots():
            if not os.path.isabs(entry):
                logger.warning(
                    "[ProjectSkillSource] EXTRA_ROOTS entry %r is not absolute "
                    "— ignored (fail-closed)",
                    entry,
                )
                continue
            bound = await self._bind_extra_root(entry, project_id)
            if bound:
                accepted.append(bound)
            else:
                logger.warning(
                    "[ProjectSkillSource] EXTRA_ROOTS entry %r does not bind to "
                    "project %s — ignored (fail-closed, W-5)",
                    entry,
                    str(project_id)[:8],
                )
        return accepted

    # ------------------------------------------------------------------
    # Index (ensure_index) + invalidation gate
    # ------------------------------------------------------------------

    def _lock_for(self, root_real: str) -> asyncio.Lock:
        lock = self._root_locks.get(root_real)
        if lock is None:
            lock = asyncio.Lock()
            self._root_locks[root_real] = lock
        return lock

    async def ensure_index(self, root: str, project_id: str) -> RootScanStats:
        """Idempotent per-root index (first build awaited; afterwards the
        per-access stat gate runs — 0 full re-scans per search after
        index, NFR-PERF-4). Concurrent first-touches coalesce
        (NFR-IDEM-2). One bad skill never poisons the scan (Q7)."""
        root_real = os.path.realpath(root)
        lock = self._lock_for(root_real)
        async with lock:
            cached = self._roots.get(root_real)
            if cached is not None and cached.project_id == project_id:
                stats = await self._stat_gate(cached)
                if stats is not None:
                    return stats
                # Stat gate found changes and already reconciled them.
                return cached.stats
            return await self._full_scan(root_real, project_id)

    async def _full_scan(self, root_real: str, project_id: str) -> RootScanStats:
        """Cold scan + wholesale swap (A4-i)."""
        t0 = time.perf_counter()
        candidates, skip_notes = await asyncio.to_thread(discover_skill_dirs, root_real)
        index = _RootIndex(
            root_path=root_real,
            project_id=project_id,
            stats=RootScanStats(root_path=root_real, project_id=project_id),
        )
        index.stats.skip_notes = list(skip_notes)
        metas: dict[str, SkillIndexMeta] = {}
        invalid: list[dict[str, str]] = []
        warned_unknown_schema = False
        namespaces = {ns for ns, _ in candidates}
        for ns, name in candidates:
            parsed: ParsedSkill = await asyncio.to_thread(
                parse_and_validate, root_real, ns, name, project_id
            )
            if parsed.ok and parsed.meta is not None:
                metas[parsed.meta.pseudo_id] = parsed.meta
                for note in parsed.debug_notes:
                    logger.debug(
                        "[ProjectSkillSource] %s/%s: %s", ns, name, note
                    )
            else:
                invalid.append(
                    {
                        "namespace": ns,
                        "name": name,
                        "reason": parsed.reason or "unknown",
                        "detail": parsed.detail,
                    }
                )
                if parsed.reason == REASON_UNKNOWN_SCHEMA and not warned_unknown_schema:
                    logger.warning(
                        "[ProjectSkillSource] unknown-schema skill skipped at "
                        "%s/.agents/%s/%s: %s (warn-once per root)",
                        root_real,
                        ns,
                        name,
                        parsed.detail,
                    )
                    warned_unknown_schema = True
                elif parsed.reason != REASON_UNKNOWN_SCHEMA:
                    logger.warning(
                        "[ProjectSkillSource] skill skipped: .agents/%s/%s — "
                        "%s: %s",
                        ns,
                        name,
                        parsed.reason,
                        parsed.detail,
                    )
        index.metas = metas
        index.invalid = invalid
        index.invalid_schema_warned = warned_unknown_schema
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        stats = RootScanStats(
            root_path=root_real,
            project_id=project_id,
            skills_indexed=len(metas),
            invalid_skills=len(invalid),
            namespaces=len(namespaces),
            last_scan_ms=round(elapsed_ms, 3),
            last_scan_at=_utc_now(),
            full_scan=True,
            skip_notes=list(skip_notes),
        )
        index.stats = stats
        self._roots[root_real] = index  # A4-i: wholesale swap (single ref assign)
        # Invalid entries are INDEXED-AS-LISTED (D-Q2): a valid=False row
        # with last_error lands in project_skill_files so the failure is
        # durable + operator-visible; the corpus excludes them by
        # construction (index.metas carries only ok entries).
        all_rows = [meta_to_row(m, root_real, stats.last_scan_at) for m in metas.values()]
        all_rows.extend(_invalid_rows(root_real, project_id, invalid, stats.last_scan_at))
        upserted, deleted = await self._sync_db_rows(index, all_rows)
        stats.db_upserted = upserted
        stats.db_deleted = deleted
        self._schedule_refresh_for(index, list(metas.values()))
        logger.info(
            "[ProjectSkillSource] indexed root=%s project=%s skills=%d invalid=%d "
            "ns=%d in %.3fms",
            root_real,
            str(project_id)[:8],
            len(metas),
            len(invalid),
            len(namespaces),
            elapsed_ms,
        )
        return stats

    async def _stat_gate(self, index: _RootIndex) -> Optional[RootScanStats]:
        """Per-access freshness gate (D-Q2): stat fast-path per manifest;
        digest on suspicion; re-parse changed / drop vanished. Returns
        None when the index project mismatches (caller should full-scan);
        returns the (possibly updated) stats otherwise. The heavy work
        (re-parse of changed skills) is bounded by the caps and stays
        off the loop; the EMBEDDING refresh stays fire-and-forget."""
        t0 = time.perf_counter()
        changed: list[SkillIndexMeta] = []
        vanished: list[str] = []
        invalid_dirty = False
        candidates, skip_notes = await asyncio.to_thread(discover_skill_dirs, index.root_path)
        candidate_set = {
            mint_pseudo_id(index.root_path, index.project_id, ns, name): (ns, name)
            for ns, name in candidates
        }
        # Vanished → drop (A4-iv delete-with-row through _sync_db).
        for pseudo_id in list(index.metas.keys()):
            if pseudo_id not in candidate_set:
                vanished.append(pseudo_id)
        # Changed → re-parse (metadata freshness is cheap; content is
        # fresh per read regardless).
        for pseudo_id, (ns, name) in sorted(candidate_set.items()):
            existing = index.metas.get(pseudo_id)
            manifest_path = os.path.join(
                index.root_path, ".agents", ns, "skills", name, "skill.json"
            )
            try:
                st = os.stat(manifest_path)
            except OSError:
                if existing is not None:
                    vanished.append(pseudo_id)
                continue
            if existing is not None:
                # Stat fast-path. The MANIFEST stat alone cannot see a
                # BODY-only edit (the common git-pull shape — canon
                # bodies are the substance), so the fast path also stats
                # the recorded BODY file when its relative path is known;
                # any stat newer than the recorded mtimes falls through
                # to the digest re-parse. (Phase-0 T2 measured this
                # two-stat shape at 0.066 ms for 5 skills.)
                needs_reparse = not (
                    existing.manifest_mtime_ns == st.st_mtime_ns
                    and existing.manifest_size == st.st_size
                )
                if not needs_reparse and existing.body_path:
                    try:
                        bst = os.stat(
                            os.path.join(
                                index.root_path,
                                ".agents",
                                ns,
                                "skills",
                                name,
                                existing.body_path,
                            )
                        )
                        needs_reparse = (
                            bst.st_mtime_ns != existing.body_mtime_ns
                            or bst.st_size != existing.body_size
                        )
                    except OSError:
                        needs_reparse = True  # body vanished — re-validate
                if not needs_reparse:
                    continue  # fast-path hit — no re-parse
            parsed = await asyncio.to_thread(
                parse_and_validate, index.root_path, ns, name, index.project_id
            )
            if parsed.ok and parsed.meta is not None:
                # Preserve embedding state across a content change — the
                # refresh (A4-vi/vii) will renew vectors fire-and-forget.
                if existing is not None:
                    parsed.meta.embedded_at = existing.embedded_at
                index.metas[pseudo_id] = parsed.meta
                # A previously-invalid skill that now validates must lose
                # its stale indexed-as-listed entry — otherwise the
                # invalid row (same pseudo-id) would overwrite the valid
                # row in the next sync.
                index.invalid = [
                    entry
                    for entry in index.invalid
                    if not (entry["namespace"] == ns and entry["name"] == name)
                ]
                changed.append(parsed.meta)
            else:
                # Now-invalid: drop from the corpus, surface as invalid.
                index.metas.pop(pseudo_id, None)
                index.invalid = [
                    entry
                    for entry in index.invalid
                    if not (entry["namespace"] == ns and entry["name"] == name)
                ]
                index.invalid.append(
                    {
                        "namespace": ns,
                        "name": name,
                        "reason": parsed.reason or "unknown",
                        "detail": parsed.detail,
                    }
                )
                # The invalid entry is indexed-as-listed (D-Q2): the DB
                # row must reflect the transition (valid→False /
                # last_error), including first-seen invalid dirs.
                invalid_dirty = True
                if parsed.reason == REASON_UNKNOWN_SCHEMA:
                    # Q7 warn-once-per-root: the state lives on the root
                    # index entry and survives stat-gate passes; a NEW
                    # unknown-schema dir after the first warning logs at
                    # DEBUG (the invalid list stays operator-visible).
                    if index.invalid_schema_warned:
                        logger.debug(
                            "[ProjectSkillSource] unknown-schema skill skipped at "
                            ".agents/%s/skills/%s: %s (already warned for this root)",
                            ns,
                            name,
                            parsed.detail,
                        )
                    else:
                        logger.warning(
                            "[ProjectSkillSource] unknown-schema skill skipped at "
                            "%s/.agents/%s/skills/%s: %s (warn-once per root)",
                            index.root_path,
                            ns,
                            name,
                            parsed.detail,
                        )
                        index.invalid_schema_warned = True
                else:
                    logger.warning(
                        "[ProjectSkillSource] re-validation failed for .agents/%s/%s: "
                        "%s (%s)",
                        ns,
                        name,
                        parsed.reason,
                        parsed.detail,
                    )
        # An invalid entry whose skill dir vanished from the candidate set
        # must be dropped (otherwise its indexed-as-listed row lingers
        # forever — the sync's stale-delete works from `rows`, which would
        # keep re-emitting it).
        live_pairs = set(candidate_set.values())
        pruned_invalid = [
            entry
            for entry in index.invalid
            if (entry["namespace"], entry["name"]) in live_pairs
        ]
        if len(pruned_invalid) != len(index.invalid):
            index.invalid = pruned_invalid
            invalid_dirty = True
        for pseudo_id in vanished:
            index.metas.pop(pseudo_id, None)
        # ALWAYS record a stat-gate-pass stats snapshot (full_scan=False)
        # — even when nothing changed — so callers see the gate ran and
        # the reported last_scan_ms is this pass's, not the cold scan's.
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        index.stats = RootScanStats(
            root_path=index.root_path,
            project_id=index.project_id,
            skills_indexed=len(index.metas),
            invalid_skills=len(index.invalid),
            namespaces=len({meta.namespace for meta in index.metas.values()}),
            changed=len(changed),
            vanished=len(vanished),
            last_scan_ms=round(elapsed_ms, 3),
            last_scan_at=_utc_now(),
            full_scan=False,
            skip_notes=list(skip_notes),
        )
        if changed or vanished or invalid_dirty:
            all_rows = [
                meta_to_row(m, index.root_path, index.stats.last_scan_at)
                for m in index.metas.values()
            ]
            all_rows.extend(
                _invalid_rows(
                    index.root_path,
                    index.project_id,
                    index.invalid,
                    index.stats.last_scan_at,
                )
            )
            upserted, deleted = await self._sync_db_rows(index, all_rows)
            index.stats.db_upserted = upserted
            index.stats.db_deleted = deleted
            if changed:
                self._schedule_refresh_for(index, changed)
        logger.info(
            "[ProjectSkillSource] stat-gate root=%s changed=%d vanished=%d "
            "(%.3fms)",
            index.root_path,
            len(changed),
            len(vanished),
            elapsed_ms,
        )
        return index.stats

    async def _sync_db_rows(
        self, index: _RootIndex, rows: list[ProjectSkillFile]
    ) -> tuple[int, int]:
        """Persist the snapshot (valid + indexed-as-listed invalid rows)
        into ``project_skill_files`` (own-table writes ONLY). DB
        failures NEVER fail the index/corpus path — the in-memory
        snapshot remains authoritative for the process (A4-ii)."""
        if self._files is None:
            return 0, 0
        try:
            return await asyncio.to_thread(
                self._files.sync_scan, index.root_path, index.project_id, rows
            )
        except Exception as exc:
            index.stats.last_error = f"db_sync_failed: {exc}"
            logger.warning(
                "[ProjectSkillSource] DB sync failed for root=%s: %s "
                "(in-memory index remains authoritative)",
                index.root_path,
                exc,
            )
            return 0, 0

    # ------------------------------------------------------------------
    # Corpus + explicit lookup (the Phase-2/Phase-3 frozen contracts)
    # ------------------------------------------------------------------

    async def corpus_for(self, project_id: str, root: str) -> list[ProjectFileSkill]:
        """The fused-search corpus (Phase 2 seam calls this).

        Index-backed + stat-gated (0 directory re-scans per search after
        index); the BODY READ at search time is required and never
        cached (P1/FR-P1-3) — every object is built from a fresh
        ``asyncio.to_thread`` disk read behind the A5 containment
        re-check. Digest mismatch at read ⇒ serve fresh anyway (file is
        canon) + INFO + async re-index; unreadable body ⇒ that skill is
        skipped with a WARNING (fail-loud per skill; one bad skill never
        poisons the corpus)."""
        root_real = os.path.realpath(root)
        try:
            await self.ensure_index(root_real, project_id)
        except Exception as exc:
            logger.warning(
                "[ProjectSkillSource] ensure_index failed for root=%s: %s — "
                "contributing nothing to this corpus",
                root_real,
                exc,
            )
            return []
        index = self._roots.get(root_real)
        # W1 (code-review fix-cycle, SECURITY-GRADE): ``ensure_index``
        # released the per-root lock before this read, so a concurrent
        # index of a DIFFERENT project bound to the same realpath can
        # have replaced the entry in the window — handing project X
        # project Y's corpus. Re-assert the project binding before
        # serving anything (the re-check ``lookup_explicit`` performs at
        # its post-gate re-fetch). Mismatch ⇒ contribute nothing.
        #
        # Longer-term form (deferred to the gc/keys follow-up): key
        # ``_roots`` by ``(root_real, project_id)`` so the swap class
        # disappears structurally instead of being re-checked per read.
        if index is None or index.project_id != project_id:
            if index is not None:
                logger.warning(
                    "[ProjectSkillSource] corpus_for project mismatch at "
                    "root=%s: index bound to %s, caller scoped to %s — "
                    "contributing nothing (TOCTOU guard)",
                    root_real,
                    index.project_id,
                    project_id,
                )
            return []
        # A4-i readers take the CURRENT dict entry; a concurrent swap
        # replaces the entry, never mutates the list we hold.
        metas = list(index.metas.values())
        out: list[ProjectFileSkill] = []
        for meta in metas:
            try:
                content = await self._read_body_fresh(root_real, meta)
            except ProjectSkillBodyUnavailable as exc:
                logger.warning(
                    "[ProjectSkillSource] corpus skip %s: %s", meta.address, exc
                )
                continue
            except Exception as exc:  # defensive: never fail the corpus
                logger.warning(
                    "[ProjectSkillSource] corpus read error %s: %s",
                    meta.address,
                    exc,
                )
                continue
            out.append(self._to_file_skill(root_real, meta, content))
        return out

    async def lookup_explicit(
        self, name_or_address: str, project_id: str
    ) -> Optional[ProjectFileSkill]:
        """Resolve a bare name or ``pf:<ns>:<name>`` address against the
        caller's project scope (Phase-3 STEP-0 contract).

        Returns the object with FRESH disk content, or ``None`` on miss.
        Raises :class:`ProjectSkillBodyUnavailable` when the indexed
        skill's body is missing/corrupt/contained-unsafe at read time
        (fail-closed — never an empty injection). Raises
        :class:`ExplicitConflictRefused` never here — the caller
        resolves scope first via :meth:`resolve_active_project`."""
        if not project_id:
            return None
        target_ns: Optional[str] = None
        target_name: str = name_or_address
        if is_pf_address(name_or_address):
            parsed_addr = parse_pf_address(name_or_address)
            if parsed_addr is None:
                return None
            target_ns, target_name = parsed_addr
        elif not target_name:
            return None

        for root_real, index in list(self._roots.items()):
            if index.project_id != project_id:
                continue
            match: Optional[SkillIndexMeta] = None
            for meta in index.metas.values():
                if meta.name != target_name:
                    continue
                if target_ns is not None and meta.namespace != target_ns:
                    continue
                match = meta
                break
            if match is None:
                continue
            # Stat-gate before serving so a just-landed git pull is
            # reflected (the "picked up on next call" contract).
            try:
                await self.ensure_index(root_real, project_id)
                index = self._roots.get(root_real) or index
                if index.project_id != project_id:
                    # W1 sibling (same TOCTOU window as ``corpus_for``):
                    # the re-fetched entry may now be bound to a
                    # DIFFERENT project on the same realpath — never
                    # resolve a body out of it (fail-closed miss).
                    return None
                match = next(
                    (
                        m
                        for m in index.metas.values()
                        if m.name == target_name
                        and (target_ns is None or m.namespace == target_ns)
                    ),
                    match,
                )
            except Exception as exc:
                logger.warning(
                    "[ProjectSkillSource] pre-lookup stat gate failed for %s: %s",
                    root_real,
                    exc,
                )
            content = await self._read_body_fresh(root_real, match)
            return self._to_file_skill(root_real, match, content)
        return None

    async def _read_body_fresh(self, root_real: str, meta: SkillIndexMeta) -> str:
        """A5(e): containment RE-CHECKED on every load-time read, then a
        fresh disk read. Digest mismatch ⇒ serve fresh anyway (canon) +
        INFO + async re-index."""
        containment = await asyncio.to_thread(
            validate_containment, root_real, meta.namespace, meta.name
        )
        if containment:
            # Task 8 (§4.5): the cross-project-leak guard TRIPPED — a
            # would-be escape from the canon root was blocked. ERROR,
            # not WARNING: this is a security signal (an operator must
            # see it even without knowing to look), and it is the only
            # way to distinguish "skill body vanished" (expected
            # churn, WARNING elsewhere) from "containment invariant
            # violated" (never expected).
            logger.error(
                "[ProjectSkillSource] LEAK GUARD TRIP: read-time containment "
                "re-check FAILED for %s (root=%s ns=%s name=%s): %s — "
                "refusing the read (fail-closed)",
                meta.address,
                root_real,
                meta.namespace,
                meta.name,
                containment,
            )
            raise ProjectSkillBodyUnavailable(meta.address, containment)
        body_path = _skill_file_path(root_real, meta, meta.body_path)
        pre = _reject_body_relpath(meta.body_path)
        if pre:
            logger.error(
                "[ProjectSkillSource] LEAK GUARD TRIP: body relpath rejected "
                "for %s (relpath=%r): %s — refusing the read (fail-closed)",
                meta.address,
                meta.body_path,
                pre,
            )
            raise ProjectSkillBodyUnavailable(meta.address, pre)

        def _read() -> bytes:
            # W6 (code-review fix-cycle): the A5(e) re-check above
            # validates the SKILL DIR, not the body FILE — a symlink
            # planted on the body path AFTER indexing was served in the
            # stat↔open window. Re-validate the body file itself and
            # open it O_NOFOLLOW, INSIDE this single thread pass, so the
            # final component can never be a symlink escape.
            skill_dir_real = os.path.realpath(
                os.path.join(
                    root_real, ".agents", meta.namespace, "skills", meta.name
                )
            )
            body_fail, body_real = _validate_file_inside_skill_dir(
                skill_dir_real, meta.body_path, "body"
            )
            if body_fail:
                raise ProjectSkillBodyUnavailable(meta.address, body_fail)
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(body_real or body_path, flags)
            try:
                chunks: list[bytes] = []
                while True:
                    chunk = os.read(fd, 1 << 16)
                    if not chunk:
                        break
                    chunks.append(chunk)
                return b"".join(chunks)
            except OSError as exc:
                if getattr(exc, "errno", None) == errno.ELOOP:
                    raise ProjectSkillBodyUnavailable(
                        meta.address,
                        f"symlink_refused: body path is a symlink ({meta.body_path!r})",
                    ) from None
                raise
            finally:
                os.close(fd)

        try:
            raw = await asyncio.to_thread(_read)
        except FileNotFoundError as exc:
            raise ProjectSkillBodyUnavailable(
                meta.address, f"{REASON_BODY_MISSING}: {exc}"
            ) from None
        except OSError as exc:
            raise ProjectSkillBodyUnavailable(
                meta.address, f"{REASON_BODY_MISSING}: {exc}"
            ) from None
        if len(raw) > BODY_MAX_BYTES:
            raise ProjectSkillBodyUnavailable(
                meta.address,
                f"{REASON_BODY_TOO_LARGE}: {len(raw)} > {BODY_MAX_BYTES}",
            )
        content = raw.decode("utf-8", errors="replace")
        fresh_digest = hashlib.sha256(
            (await asyncio.to_thread(_read_manifest_bytes, root_real, meta)) + raw
        ).hexdigest()
        if fresh_digest != meta.digest:
            logger.info(
                "[ProjectSkillSource] digest mismatch at read for %s — serving "
                "fresh (canon wins) and scheduling re-index",
                meta.address,
            )
            root_index = self._roots.get(root_real)
            if root_index is not None:
                self._schedule_refresh_for(root_index, [meta])
        return content

    def _to_file_skill(
        self, root_real: str, meta: SkillIndexMeta, content: str
    ) -> ProjectFileSkill:
        return ProjectFileSkill(
            id=meta.pseudo_id,
            address=meta.address,
            name=meta.name,
            description=meta.description,
            content=content,
            namespace=meta.namespace,
            project_id=meta.project_id,
            body_path=meta.body_path,
            root_path=root_real,
            category=meta.category,
            body_char_len=meta.body_char_len,
            manifest=dict(meta.manifest or {}),
            digest=meta.digest,
            shadows=[],
        )

    # ------------------------------------------------------------------
    # Embedding refresh dispatch (Q3 + A1 + A4 vi/vii; S1: MAIN model)
    # ------------------------------------------------------------------

    def _schedule_refresh_for(
        self, index: _RootIndex, metas: list[SkillIndexMeta]
    ) -> None:
        """Fire-and-forget embedding refresh (never awaited by corpus/
        lookup paths). Single-flight per pseudo-id (A4-vii); debounce +
        digest-stability check inside the task."""
        if self._embedding_service is None or self._embeddings is None:
            return  # provider absent ⇒ BM25-only; embeddings_pending stays > 0
        for meta in metas:
            if meta.pseudo_id in self._inflight_refreshes:
                continue  # single-flight
            self._inflight_refreshes.add(meta.pseudo_id)
            task = asyncio.create_task(
                self._refresh_embeddings(index, meta, self._refresh_debounce_s)
            )
            self._refresh_tasks.add(task)
            task.add_done_callback(self._refresh_tasks.discard)

    async def _refresh_embeddings(
        self, index: _RootIndex, meta: SkillIndexMeta, delay: float
    ) -> None:
        """A4(vi)+(vii): debounce on digest stability → generate ALL rows
        (delete-AFTER-success) → replace in one transaction → stamp
        embedded_at / last_error. Provider failures never propagate."""
        try:
            pseudo_id = meta.pseudo_id
            waited = 0.0
            backoff = delay
            stable = False
            while waited < EMBED_REFRESH_BACKOFF_MAX_S:
                await asyncio.sleep(backoff)
                waited += backoff
                # Digest-stability: recompute the current on-disk digest;
                # if it moved, reschedule (bounded).
                try:
                    current = await asyncio.to_thread(
                        _current_digest, index.root_path, meta
                    )
                except OSError:
                    # body/manifest gone — let the next stat gate handle it
                    return
                if current == meta.digest:
                    stable = True
                    break
                backoff = min(backoff * 2, EMBED_REFRESH_BACKOFF_MAX_S - waited)
                if backoff <= 0:
                    break
            if not stable:
                logger.warning(
                    "[ProjectSkillSource] refresh debounce window exhausted for "
                    "%s — skipping (digest never stabilized)",
                    meta.address,
                )
                return

            body = await asyncio.to_thread(_read_body_bytes, index.root_path, meta)
            content = body.decode("utf-8", errors="replace")
            # Rule A4(iii): the vector is generated from the SAME root's
            # body that will be served — the fresh read above IS that
            # body (root-keyed pseudo-id makes the pairing structural).
            gen_skill = _GenerationView(
                name=meta.name,
                description=meta.description,
                content=content,
            )
            try:
                queries = await self._embedding_service.generate_trigger_queries(
                    gen_skill
                )
            except Exception as exc:
                await self._record_refresh_failure(meta, f"trigger_generation_failed: {exc}")
                return
            if not queries:
                await self._record_refresh_failure(
                    meta, "trigger_generation_empty (provider unreachable or unusable)"
                )
                return
            vectors: list[tuple[str, list[float]]] = []
            for query in queries:
                try:
                    vector = await self._embedding_service.embed_text(query)
                    vectors.append((query, list(vector)))
                except Exception as exc:
                    logger.warning(
                        "[ProjectSkillSource] embed failed for %s query=%r: %s",
                        meta.address,
                        str(query)[:60],
                        exc,
                    )
            if not vectors:
                await self._record_refresh_failure(
                    meta, "embedding_failed: no query produced a vector"
                )
                return
            # A4(vi): generation SUCCEEDED — only now touch stored rows.
            written, removed = await asyncio.to_thread(
                self._embeddings.replace_for_skill, pseudo_id, vectors
            )
            await asyncio.to_thread(
                self._files.set_embedding_state, pseudo_id, _utc_now(), None
            )
            logger.info(
                "[ProjectSkillSource] embeddings refreshed for %s: written=%d "
                "replaced=%d",
                meta.address,
                written,
                removed,
            )
        except Exception as exc:  # never fail a search/corpus path
            logger.warning(
                "[ProjectSkillSource] embedding refresh crashed for %s: %s",
                meta.address,
                exc,
            )
            try:
                await self._record_refresh_failure(meta, f"refresh_crashed: {exc}")
            except Exception:
                pass
        finally:
            self._inflight_refreshes.discard(meta.pseudo_id)

    async def _record_refresh_failure(self, meta: SkillIndexMeta, error: str) -> None:
        logger.warning(
            "[ProjectSkillSource] embedding refresh failed for %s: %s",
            meta.address,
            error,
        )
        if self._files is not None:
            try:
                await asyncio.to_thread(
                    self._files.set_embedding_state, meta.pseudo_id, meta.embedded_at, error
                )
            except Exception as exc:
                logger.warning(
                    "[ProjectSkillSource] failed to persist last_error for %s: %s",
                    meta.address,
                    exc,
                )

    # ------------------------------------------------------------------
    # gc + observability
    # ------------------------------------------------------------------

    async def gc_sweep(self) -> int:
        """A4(v) + A17 (Phase 4): the gc tick.

        Two additive passes, both one-statement and idempotent:

        1. **A17 orphan-project purge** — ``project_skill_files`` rows
           whose ``project_id`` no longer exists in ``projects`` (a
           deleted project must not leave index rows behind; the census
           in ``detail.skill_sources.roots`` reads the live index, not
           the DB, so this purge is what keeps the durable view
           convergent after project deletion).
        2. **A4(v) embedding gc** — ``project_skill_embeddings`` rows
           whose ``skill_id`` is no longer present in
           ``project_skill_files`` (covers root changes, project_id
           changes, and everything pass 1 just deleted).

        Returns the total number of rows removed. Both passes are
        independently None-safe (no file repo / no embedding repo ⇒
        that pass is skipped).
        """
        removed = 0
        if self._files is not None:
            try:
                removed += await asyncio.to_thread(
                    self._files.purge_orphan_projects
                )
            except Exception as exc:  # advisory gc — never raise
                logger.warning(
                    "[ProjectSkillSource] A17 orphan-project purge failed: %s",
                    exc,
                )
        if self._embeddings is not None:
            try:
                removed += await asyncio.to_thread(self._embeddings.gc_orphans)
            except Exception as exc:  # advisory gc — never raise
                logger.warning(
                    "[ProjectSkillSource] A4(v) embedding gc failed: %s", exc
                )
        return removed

    def snapshot_status(self) -> dict[str, Any]:
        """Advisory-shaped status (Phase-4's ``detail.skill_sources``
        consumes this; Phase 1 exposes it for tests). Honest zeros —
        never an absent key."""
        roots = []
        for index in self._roots.values():
            roots.append(
                {
                    "root_path": index.root_path,
                    "project_id": index.project_id,
                    "namespaces": len({m.namespace for m in index.metas.values()}),
                    "skills_indexed": len(index.metas),
                    "invalid_skills": len(index.invalid),
                    "last_scan_ms": index.stats.last_scan_ms,
                    "last_scan_at": index.stats.last_scan_at,
                    "last_error": index.stats.last_error,
                }
            )
        return {
            "roots": roots,
            "embeddings_pending": sum(
                1
                for index in self._roots.values()
                for meta in index.metas.values()
                if meta.embedded_at is None
            ),
        }

    # ------------------------------------------------------------------
    # A16 assertion helper
    # ------------------------------------------------------------------

    def cached_objects(self) -> list[Any]:
        """Every object the in-process cache currently holds (A16: all
        metadata-only). The unit pack asserts none exposes non-empty
        ``content`` — enforced structurally (``SkillIndexMeta`` has no
        ``content`` field), asserted here for the pin."""
        out: list[Any] = []
        for index in self._roots.values():
            out.extend(index.metas.values())
        return out


# ----------------------------------------------------------------------
# Module-level helpers (pure, thread-safe)
# ----------------------------------------------------------------------


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _reject_body_relpath(rel: str) -> Optional[str]:
    """A5(b) at read time: absolute or ``..``-carrying body paths never
    reach the filesystem (belt for the index-time gate)."""
    if not isinstance(rel, str) or not rel:
        return "path_escape: empty body path"
    if os.path.isabs(rel):
        return f"path_escape: absolute body path {rel!r}"
    if any(part == ".." for part in rel.replace("\\", "/").split("/")):
        return f"path_escape: body path contains '..' {rel!r}"
    return None


def _skill_file_path(root_real: str, meta: SkillIndexMeta, rel: str) -> str:
    """The depth-exact canon path (A12): ``<root>/.agents/<ns>/skills/<name>/<rel>``."""
    return os.path.join(
        root_real, ".agents", meta.namespace, "skills", meta.name, rel
    )


def _read_manifest_bytes(root_real: str, meta: SkillIndexMeta) -> bytes:
    with open(_skill_file_path(root_real, meta, "skill.json"), "rb") as fh:
        return fh.read()


def _read_body_bytes(root_real: str, meta: SkillIndexMeta) -> bytes:
    with open(_skill_file_path(root_real, meta, meta.body_path), "rb") as fh:
        return fh.read()


def _current_digest(root_real: str, meta: SkillIndexMeta) -> str:
    return hashlib.sha256(
        _read_manifest_bytes(root_real, meta) + _read_body_bytes(root_real, meta)
    ).hexdigest()


def meta_to_row(meta: SkillIndexMeta, root_path: str, now: str) -> ProjectSkillFile:
    """Flatten a cache meta into a ``project_skill_files`` row."""
    return ProjectSkillFile(
        id=meta.pseudo_id,
        project_id=meta.project_id,
        root_path=root_path,
        namespace=meta.namespace,
        name=meta.name,
        address=meta.address,
        description=meta.description,
        category=meta.category,
        body_path=meta.body_path,
        manifest_sha256=meta.manifest_sha256,
        body_sha256=meta.body_sha256,
        body_char_len=meta.body_char_len,
        manifest_json=meta.manifest_json_text(),
        valid=meta.valid,
        last_error=meta.last_error,
        embedded_at=meta.embedded_at,
        indexed_at=now,
        updated_at=now,
    )


def _invalid_rows(
    root_path: str,
    project_id: str,
    invalid: list[dict[str, str]],
    now: str,
) -> list[ProjectSkillFile]:
    """Indexed-as-listed rows (D-Q2) for per-skill validation failures:
    ``valid=False`` + ``last_error`` — visible, never silent (Q7);
    excluded from the corpus by construction (only ok metas enter
    ``index.metas``). Digests are empty (the failing skill's bytes are
    not trusted content)."""
    rows: list[ProjectSkillFile] = []
    for entry in invalid:
        ns = entry.get("namespace", "")
        name = entry.get("name", "")
        rows.append(
            ProjectSkillFile(
                id=mint_pseudo_id(root_path, project_id, ns, name),
                project_id=project_id,
                root_path=root_path,
                namespace=ns,
                name=name,
                address=mint_address(ns, name) if ns and name else f"pf:{ns}:{name}",
                description="",
                category=None,
                body_path="",
                manifest_sha256="",
                body_sha256="",
                body_char_len=0,
                manifest_json=None,
                valid=False,
                last_error=f"{entry.get('reason', 'unknown')}: "
                f"{entry.get('detail', '')}",
                embedded_at=None,
                indexed_at=now,
                updated_at=now,
            )
        )
    return rows


class _GenerationView:
    """Minimal duck-type for ``SkillEmbeddingService.generate_trigger_queries``
    (reads ``name`` / ``description`` / ``content`` only)."""

    def __init__(self, name: str, description: str, content: str) -> None:
        self.name = name
        self.description = description
        self.content = content

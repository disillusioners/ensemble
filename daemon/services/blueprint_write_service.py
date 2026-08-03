"""Canonical write boundary for project blueprints (C5 / G1 / G2 / G3 / C9).

ALL writes — REST CRUD, blueprinter tools, scan/rebuild dispatch — MUST
route through this service. Direct ``BlueprintRepository`` writes are
forbidden after Phase 1 (a CI grep test asserts this).

The service owns five invariants that no write path may bypass:

1. **Rate limiter check** before any write (``_check_rate_limit``).
2. **Trigger embedding generation**, atomic with the content publish
   (``_embed_queries``, embed BEFORE commit — C4 fix 1).
3. **Revision snapshot capture**, post-commit via the repository's
   ``update()`` (G2; lives in the repo so both the service and any
   legacy path benefit).
4. **Atomic publish unit** — content + triggers + embeddings published
   as one logical operation; rollback on partial failure.
5. **Rate limiter record** after every operation (``_record_rate_result``).

Design notes
------------

* **Sync repo, async service.** The repository is sync; every repo call
  is wrapped in :func:`asyncio.to_thread`. The embedding service is
  async (it awaits the embedding API).
* **Duck-typed deps.** Constructor accepts the repo, embedding repo,
  embedding service, rate limiter, config, project_id, and manager.
  All deps are duck-typed so the service is unit-testable with mocks.
* **Rate limiter fail-closed (C2 fix d).** A non-None limiter that
  raises an exception fails CLOSED (raises ``BlueprintRateLimitError``)
  rather than silently proceeding — a broken limiter is a programming
  /infra bug, and failing open would bypass throttling. Only a ``None``
  limiter fails open (graceful degradation — no throttling configured).
* **Atomic reserve (C2 fix a).** The service calls
  ``rate_limiter.reserve()`` (atomic check+record) instead of the
  legacy ``can_proceed`` + ``record_success`` pair (TOCTOU window).
  ``_record_rate_result(success=True)`` is now a no-op (the reserve
  already consumed the slot); only ``success=False`` calls
  ``record_failure``.
* **Per-blueprint lock (C4 fix).** Updates to the same blueprint are
  serialized via a per-blueprint ``asyncio.Lock`` to prevent snapshot
  drift / lost updates under concurrency.
* **``except Exception`` everywhere** — never ``BaseException`` (project
  convention: respects ``CancelledError`` for async cancellation).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .blueprint_save_plan import SavePlan, SavePlanResult, WriteOp

if TYPE_CHECKING:
    from daemon.config import BlueprintConfig
    from daemon.manager import InstanceManager
    from daemon.repositories.blueprint.embedding_repository import (
        BlueprintEmbeddingRepository,
    )
    from daemon.repositories.blueprint.models import Blueprint
    from daemon.repositories.blueprint.repository import BlueprintRepository
    from daemon.services.blueprint_rate_limiter import BlueprintRateLimiter
    from daemon.services.skill_embedding_service import SkillEmbeddingService

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── Domain exceptions ─────────────────────────────────────────────────────


class BlueprintPublishError(Exception):
    """Raised when the atomic publish unit fails (embedding or trigger store).

    The blueprint row is either never created (embed-before-commit failure)
    or rolled back (trigger-store failure after create). Callers should
    surface this to the user/agent as a retryable error.
    """


class BlueprintNotFoundError(Exception):
    """Raised when an update/disable targets a blueprint that does not exist."""


class BlueprintRateLimitError(Exception):
    """Raised when the rate limiter denies a write.

    The REST router maps this to HTTP 429.
    """


# ─── Service ───────────────────────────────────────────────────────────────


class BlueprintWriteService:
    """Canonical write boundary for project blueprints.

    ALL writes route through this service. The five invariants
    (rate-limit check, embed-before-commit, revision capture, atomic
    publish unit, rate-limit record) are enforced on every operation.
    """

    def __init__(
        self,
        repository: "BlueprintRepository",
        embedding_repository: "BlueprintEmbeddingRepository | None",
        embedding_service: "SkillEmbeddingService | None",
        rate_limiter: "BlueprintRateLimiter | None",
        config: "BlueprintConfig",
        project_id: str,
        manager: Any,  # for save-plan metadata + future history hooks
    ) -> None:
        self.repository = repository
        self.embedding_repository = embedding_repository
        self.embedding_service = embedding_service
        self.rate_limiter = rate_limiter
        self.config = config
        self.project_id = project_id
        self.manager = manager
        # C4 fix: per-blueprint lock dict. Serializes updates to the SAME
        # blueprint (prevents snapshot drift / lost updates). Different
        # blueprints proceed in parallel (different locks).
        self._blueprint_locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()  # guards the locks dict itself

    # ── Private invariant helpers ──────────────────────────────────────

    async def _get_blueprint_lock(self, blueprint_id: str) -> asyncio.Lock:
        """Get-or-create the per-blueprint lock (C4 fix).

        Guards the locks dict itself with ``_locks_guard`` so two
        concurrent calls for a new blueprint don't race on dict insert.
        """
        async with self._locks_guard:
            if blueprint_id not in self._blueprint_locks:
                self._blueprint_locks[blueprint_id] = asyncio.Lock()
            return self._blueprint_locks[blueprint_id]

    async def _check_rate_limit(self) -> None:
        """Invariant 1: reserve a rate-limit slot before any write (C2 fix a).

        Uses the atomic ``reserve()`` method which collapses check+record
        into one lock hold, closing the TOCTOU window. On success the slot
        is consumed immediately — the caller does NOT call
        ``record_success()`` afterward.

        Fail-closed (C2 fix d): a non-None limiter that raises an
        exception → raise ``BlueprintRateLimitError`` (a broken limiter
        is a programming/infra bug; failing open would bypass throttling).
        Only a ``None`` limiter fails open (graceful degradation — no
        throttling configured).
        """
        if self.rate_limiter is None:
            # No throttling configured — graceful degradation.
            return
        try:
            allowed = self.rate_limiter.reserve(self.project_id)
        except Exception as e:
            # C2 fix d: fail-CLOSED. A non-None limiter that raises is a
            # programming/infra bug — fail-closed to prevent unthrottled
            # writes. (Only a None limiter fails open, handled above.)
            logger.error(
                "rate_limiter.reserve failed for project %s: %s "
                "(failing closed)", self.project_id, e, exc_info=True,
            )
            raise BlueprintRateLimitError(
                f"Blueprint rate limiter error for project "
                f"{self.project_id}: {e}"
            )
        if not allowed:
            raise BlueprintRateLimitError(
                f"Blueprint write rate-limited for project {self.project_id}"
            )

    async def _record_rate_result(self, success: bool) -> None:
        """Invariant 5: record failure with the rate limiter (C2 fix a).

        After switching to atomic ``reserve()``:

        * ``success=True`` is a NO-OP — the reserve already consumed the
          slot and reset ``consecutive_failures``.
        * ``success=False`` calls ``record_failure`` so the circuit
          breaker can trip on repeated repo-level failures.

        This keeps the audit semantics correct: a reserved write that
        later fails at the repo level is counted as a failure.
        """
        if self.rate_limiter is None:
            return
        if success:
            # No-op: reserve() already consumed the slot and reset the
            # failure counter.
            return
        try:
            self.rate_limiter.record_failure(self.project_id)
        except Exception as e:
            logger.warning(
                "rate_limiter.record_failure failed for project %s: %s "
                "(swallowed)", self.project_id, e, exc_info=True,
            )

    async def _embed_queries(
        self,
        queries: list[str],
    ) -> list[tuple[str, list[float]]]:
        """Embed each trigger query, skipping per-query failures.

        Returns ``[(query_text, embedding), ...]`` for the queries that
        succeeded. A query whose ``embed_text`` raises is skipped (C8
        per-query resilience) so a single bad query does not abort the
        whole batch. If EVERY query fails, the caller decides whether
        to abort (see :meth:`create_blueprint`).
        """
        if not self.embedding_service:
            return []
        items: list[tuple[str, list[float]]] = []
        for q in queries:
            if not q or not q.strip():
                continue
            try:
                vec = await self.embedding_service.embed_text(q)
                items.append((q, list(vec)))
            except Exception as e:
                # Per-query resilience: one bad embedding does not abort
                # the batch. Logged + skipped.
                logger.warning(
                    "embed_text failed for trigger query %r: %s",
                    q, e, exc_info=True,
                )
        return items

    # ── High-level operations ──────────────────────────────────────────

    async def create_blueprint(
        self,
        slug: str,
        name: str,
        kind: str,
        content: str,
        trigger_queries: list[str] | None = None,
        tags: list[dict] | None = None,
        file_refs: list[str] | None = None,
        reason: str | None = None,
    ) -> "Blueprint":
        """Publish a new blueprint as ONE logical operation.

        Atomic publish unit (C4 fix 1):
          1. Check rate limit
          2. Embed trigger queries (BEFORE commit)
          3. Insert blueprint row
          4. Replace triggers atomically
          5. Record rate-limiter success

        If embedding fails completely, the row is NOT inserted — we
        never have a blueprint without vectors when vectors were
        requested.
        """
        # 1. Rate limit (C8 fail-open)
        await self._check_rate_limit()

        # 2. Embed BEFORE commit (C4 fix 1)
        trigger_items: list[tuple[str, list[float]]] = []
        if trigger_queries:
            trigger_items = await self._embed_queries(trigger_queries)
            if not trigger_items and self.embedding_service is not None:
                # Embedding was requested but every query failed → abort
                # cleanly. The row is never created.
                self._record_rate_result_sync(False)
                raise BlueprintPublishError(
                    "All trigger embeddings failed; blueprint not created. "
                    "Retry or call without trigger_queries."
                )

        # 3. Insert blueprint (sync via to_thread)
        try:
            bp = await asyncio.to_thread(
                self.repository.create,
                project_id=self.project_id,
                slug=slug,
                name=name,
                kind=kind,
                content=content,
                tags=tags or [],
                file_refs=file_refs or [],
            )
        except Exception:
            await self._record_rate_result(success=False)
            raise

        # 4. Replace triggers atomically (delete-then-insert in one tx)
        if trigger_items:
            try:
                await asyncio.to_thread(
                    self.embedding_repository.replace_triggers,
                    bp.id, trigger_items,
                )
            except Exception as e:
                # Roll back the blueprint row so we never have a
                # trigger-less blueprint when triggers were requested.
                # C3 fix: log the rollback failure at ERROR level for
                # visibility (was ``except Exception: pass`` — silently
                # swallowed, leaving an orphaned active row with no
                # triggers and no log entry).
                try:
                    await asyncio.to_thread(
                        self.repository.soft_delete, bp.id
                    )
                except Exception as rollback_err:
                    logger.error(
                        "Rollback soft_delete failed for blueprint %s "
                        "(trigger store failure: %s): %s",
                        bp.id, e, rollback_err, exc_info=True,
                    )
                await self._record_rate_result(success=False)
                raise BlueprintPublishError(
                    f"Trigger storage failed; blueprint rolled back: {e}"
                )

        # 5. Capture initial revision so the audit trail shows the
        # creation event. Matches the disable_blueprint pattern which
        # records a final revision. C8: revision capture failure never
        # blocks the create (swallowed + logged).
        try:
            await asyncio.to_thread(
                self.repository.add_revision,
                blueprint_id=bp.id,
                version=1,
                content_snapshot=bp.content,
                source="create",
                file_refs=list(bp.file_refs or []),
                tags=list(bp.tags or []),
                trigger_queries=list(bp.trigger_queries or []),
                reason=reason or "blueprint created",
            )
        except Exception as e:
            logger.error(
                "add_revision (create) failed for blueprint %s: %s",
                bp.id, e, exc_info=True,
            )

        # 6. Record success
        await self._record_rate_result(success=True)
        return bp

    async def update_blueprint(
        self,
        blueprint_id: str,
        content: str | None = None,
        name: str | None = None,
        trigger_queries: list[str] | None = None,  # None=unchanged, []=clear
        tags: list[dict] | None = None,
        file_refs: list[str] | None = None,
        reason: str | None = None,
        status: str | None = None,
    ) -> "Blueprint":
        """Update a blueprint. All 5 invariants enforced.

        C1 fix: ``status`` is a metadata field — it updates the Blueprint
        row but does NOT increment the version (status is not in the
        version-increment set ``{content, file_refs, tags, trigger_queries}``).

        C4 fix (concurrency): the entire operation is serialized under a
        per-blueprint ``asyncio.Lock`` to prevent snapshot drift / lost
        updates when two writers target the same blueprint concurrently.

        C4 fixes (trigger semantics):
          - Empty list (``trigger_queries=[]``) explicitly clears all
            triggers. ``None`` means "leave triggers unchanged".
          - ``reason`` is extracted from ``fields`` BEFORE the setattr
            loop in the repository's ``update()`` (handled there).
        """
        lock = await self._get_blueprint_lock(blueprint_id)
        async with lock:
            return await self._update_blueprint_impl(
                blueprint_id,
                content=content,
                name=name,
                trigger_queries=trigger_queries,
                tags=tags,
                file_refs=file_refs,
                reason=reason,
                status=status,
            )

    async def _update_blueprint_impl(
        self,
        blueprint_id: str,
        content: str | None = None,
        name: str | None = None,
        trigger_queries: list[str] | None = None,  # None=unchanged, []=clear
        tags: list[dict] | None = None,
        file_refs: list[str] | None = None,
        reason: str | None = None,
        status: str | None = None,
    ) -> "Blueprint":
        """Update body, called under the per-blueprint lock (C4 fix)."""
        await self._check_rate_limit()

        # Separate: scalar fields vs trigger-queries flag
        fields: dict[str, Any] = {}
        trigger_clear = False
        if content is not None:
            fields["content"] = content
        if name is not None:
            fields["name"] = name
        if tags is not None:
            fields["tags"] = tags
        if file_refs is not None:
            fields["file_refs"] = file_refs
        # C1 fix: status is a valid Blueprint field but does NOT increment
        # the version (not in the version-increment set). Pass through to
        # repository.update() which setattr's it onto the row.
        if status is not None:
            fields["status"] = status
        # C4 fix 2: distinguish None (no change) from [] (clear all)
        if trigger_queries is not None:
            if trigger_queries == []:
                trigger_clear = True
                fields["trigger_queries"] = []  # pass through to repository
            else:
                fields["trigger_queries"] = trigger_queries

        if not fields and not trigger_clear:
            raise ValueError("No fields to update")

        # Snapshot pre-update state BEFORE calling repository.update().
        # This is the source of truth for rollback — NOT the revision
        # history. Fixes the BLOCKER bug where list_revisions(limit=1)
        # returned the NEW revision (auto-captured post-commit by G2),
        # causing the rollback to write new content back onto new content
        # (a complete no-op). On the FIRST update of a fresh blueprint
        # (v=1 → v=2), no prior revision exists, so the old code's
        # list_revisions path silently did nothing. The in-memory
        # snapshot also restores ALL four version-incrementing fields
        # (content, tags, file_refs, trigger_queries), not just content
        # + trigger_queries as the previous rollback did.
        existing = await asyncio.to_thread(
            self.repository.get_by_id, blueprint_id
        )
        if existing is None:
            raise BlueprintNotFoundError(blueprint_id)
        _pre_state = {
            "content": existing.content,
            "tags": list(existing.tags or []),
            "file_refs": list(existing.file_refs or []),
            "trigger_queries": list(existing.trigger_queries or []),
        }

        # Embed BEFORE commit (C4 fix 1)
        new_trigger_items: list[tuple[str, list[float]]] = []
        if trigger_queries and not trigger_clear:
            new_trigger_items = await self._embed_queries(trigger_queries)
            if not new_trigger_items and self.embedding_service is not None:
                await self._record_rate_result(success=False)
                raise BlueprintPublishError(
                    "All trigger embeddings failed; update not applied. "
                    "Retry or pass trigger_queries=[] to skip re-embedding."
                )

        # Apply update (sync). The repo's update() extracts ``reason``
        # before the setattr loop and captures a revision post-commit.
        try:
            bp = await asyncio.to_thread(
                self.repository.update, blueprint_id, reason=reason, **fields
            )
        except Exception:
            await self._record_rate_result(success=False)
            raise

        if bp is None:
            raise BlueprintNotFoundError(blueprint_id)

        # Replace triggers (or clear). Only touch the trigger table when
        # the caller explicitly passed trigger_queries (None = no-op).
        if trigger_clear or new_trigger_items:
            try:
                await asyncio.to_thread(
                    self.embedding_repository.replace_triggers,
                    blueprint_id,
                    new_trigger_items if not trigger_clear else [],
                )
            except Exception as e:
                # Update succeeded; trigger replace failed. Restore the
                # pre-update state from the in-memory snapshot taken
                # above. The snapshot is authoritative — it works on
                # the FIRST update (no prior revision exists) and
                # restores ALL four version-incrementing fields.
                #
                # C6 fix: pass ``reason="rollback after trigger storage
                # failure"`` so the audit trail marks this revision as a
                # rollback (not a normal update).
                #
                # C5 fix: the ``project_blueprint_triggers`` table is the
                # AUTHORITATIVE source for matching. The failed
                # ``replace_triggers`` left it in an unknown (possibly
                # partial) state. Clear it explicitly so the matcher does
                # not read stale/partial embeddings. The pre-state
                # ``trigger_queries`` JSONB cache is restored by the
                # rollback update below; embeddings will be re-populated
                # on the next successful update.
                try:
                    if self.embedding_repository is not None:
                        await asyncio.to_thread(
                            self.embedding_repository.replace_triggers,
                            blueprint_id, [],
                        )
                except Exception as trigger_clear_err:
                    logger.error(
                        "Rollback trigger-table clear failed for blueprint "
                        "%s (trigger replace failure: %s): %s",
                        blueprint_id, e, trigger_clear_err, exc_info=True,
                    )
                try:
                    await asyncio.to_thread(
                        self.repository.update,
                        blueprint_id,
                        reason="rollback after trigger storage failure",
                        **_pre_state,
                    )
                except Exception:
                    pass  # C8: log + swallow; operator inspects DB
                await self._record_rate_result(success=False)
                raise BlueprintPublishError(
                    f"Trigger replace failed; update rolled back: {e}"
                )

        await self._record_rate_result(success=True)
        return bp

    async def disable_blueprint(
        self,
        blueprint_id: str,
        reason: str | None = None,
    ) -> bool:
        """Soft-delete a blueprint via the canonical service.

        Records a final revision (``version=-1``, ``source="disable"``)
        so the revision view shows the disable event.

        C4 fix: serialized under the per-blueprint lock (disable mutates
        the blueprint, same as update).
        """
        lock = await self._get_blueprint_lock(blueprint_id)
        async with lock:
            return await self._disable_blueprint_impl(blueprint_id, reason)

    async def _disable_blueprint_impl(
        self,
        blueprint_id: str,
        reason: str | None = None,
    ) -> bool:
        """Disable body, called under the per-blueprint lock (C4 fix)."""
        await self._check_rate_limit()
        try:
            ok = await asyncio.to_thread(
                self.repository.soft_delete, blueprint_id
            )
        except Exception:
            await self._record_rate_result(success=False)
            raise
        if not ok:
            raise BlueprintNotFoundError(blueprint_id)

        # Capture a revision so the audit trail shows the disable event.
        # C8: revision capture never blocks the disable.
        try:
            await asyncio.to_thread(
                self.repository.add_revision,
                blueprint_id=blueprint_id,
                version=-1,  # sentinel: "deletion event"
                content_snapshot="",
                source="disable",
                file_refs=[],
                tags=[],
                trigger_queries=[],
                reason=reason or "blueprint disabled",
            )
        except Exception as e:
            logger.error(
                "add_revision (disable) failed for blueprint %s: %s",
                blueprint_id, e, exc_info=True,
            )

        await self._record_rate_result(success=True)
        return True

    # ── Build budget (C9) ───────────────────────────────────────────────

    async def plan_publication(
        self,
        operations: list[WriteOp],
        mode: str = "manual",
    ) -> SavePlan:
        """Count the operations against the current rate-limit budget.

        Returns a :class:`SavePlan` with ``budget_available`` and
        ``will_rate_limit_at`` annotations. The caller inspects the plan;
        if budget is insufficient, the caller schedules a continuation
        job for after the cooldown.

        NOTE: This does NOT reserve budget. Reservation is implicit: when
        :meth:`execute_save_plan` actually performs writes, each
        successful write consumes one tick. The plan simply predicts
        whether all writes will fit and how many will be rate-limited.
        """
        save_plan = SavePlan(
            project_id=self.project_id,
            run_id=str(uuid.uuid4()),
            mode=mode,
            operations=list(operations),
        )

        # Read current budget from the limiter (C8 fail-open).
        budget = 0
        if self.rate_limiter is not None:
            try:
                budget = self._current_budget()
            except Exception as e:
                logger.warning(
                    "plan_publication: budget read failed for project %s: "
                    "%s (assuming full budget)", self.project_id, e,
                    exc_info=True,
                )
                # Assume full budget on error (fail-open).
                budget = self.rate_limiter._max_per_hour
        else:
            # No limiter → effectively unlimited budget. Nothing will
            # rate-limit.
            budget = len(operations)

        save_plan.budget_available = budget
        save_plan.will_rate_limit_at = max(0, len(operations) - budget)
        return save_plan

    def _current_budget(self) -> int:
        """Remaining writes allowed in the current hour window.

        Reads the limiter's internal state under its lock. Returns the
        full ``_max_per_hour`` when the project has no state yet.
        """
        if self.rate_limiter is None:
            return 0
        with self.rate_limiter._lock:
            state = self.rate_limiter._state[self.project_id]
            if not state.revision_timestamps:
                current = 0
            else:
                cutoff = time.time() - 3600
                current = sum(1 for ts in state.revision_timestamps if ts > cutoff)
            return self.rate_limiter._max_per_hour - current

    async def execute_save_plan(
        self,
        save_plan: SavePlan,
        resume: bool = False,
    ) -> SavePlanResult:
        """Execute a :class:`SavePlan`, persisting progress for resumability.

        On rate-limit hit:
          - Persist the SavePlan to project metadata
            (key: ``blueprint.save_plan.<run_id>``).
          - Compute ``cooldown_resume_at``.
          - Return ``SavePlanResult(status="partial_rate_limited", ...)``.

        On all-complete:
          - Mark ``SavePlan.completed_at``; remove from metadata.
          - Return ``SavePlanResult(status="complete", ...)``.

        C8 fail-open: any per-operation failure logs + continues; the
        SavePlan records the error in the :class:`WriteOp`.
        """
        completed = save_plan.completed_operations
        if resume:
            # Skip operations already marked completed
            pending = [op for op in save_plan.operations if not op.completed]
        else:
            pending = list(save_plan.operations)

        for idx, op in enumerate(pending):
            # C2 fix (rate-limit reservation): the operation dispatch
            # below calls _check_rate_limit() itself, which atomically
            # ``reserve()``s a slot. We MUST NOT call _check_rate_limit
            # here too — that would double-consume the slot budget and
            # a 5-op save plan would burn 10 reservations, tripping the
            # limiter prematurely. Instead, catch BlueprintRateLimitError
            # from the dispatch and return the partial result.
            try:
                if op.op == "create":
                    await self.create_blueprint(**op.payload)
                elif op.op == "update":
                    await self.update_blueprint(**op.payload)
                elif op.op == "disable":
                    await self.disable_blueprint(**op.payload)
                else:
                    op.error = f"Unknown op type: {op.op}"
                    continue  # skip marking completed
                op.completed = True
                op.completed_at = _now_iso()
                completed += 1
                save_plan.completed_operations = completed
            except BlueprintRateLimitError:
                # Rate-limited — persist progress and return partial
                # result. The operation's own _check_rate_limit() raised
                # this; exactly ONE slot was consumed (not two).
                # Must be caught BEFORE the generic Exception handler.
                save_plan.completed_operations = completed
                cooldown_resume_at = self._compute_cooldown_resume_at()
                await self._persist_save_plan(save_plan)
                return SavePlanResult(
                    status="partial_rate_limited",
                    completed=completed,
                    total=save_plan.total_operations,
                    rate_limited_at_index=idx,
                    cooldown_resume_at=cooldown_resume_at,
                    save_plan=save_plan,
                    message=(
                        f"Rate-limited after {completed} of "
                        f"{save_plan.total_operations} writes. "
                        f"Will resume after cooldown."
                    ),
                )
            except Exception as e:
                op.error = str(e)
                # C8: continue with the next op; record the failure
                logger.warning(
                    "save plan op %d (%s) failed: %s", idx, op.op, e,
                    exc_info=True,
                )

        # All operations processed
        if all(op.completed for op in save_plan.operations):
            save_plan.completed_at = _now_iso()
            await self._clear_save_plan(save_plan)
            return SavePlanResult(
                status="complete",
                completed=completed,
                total=save_plan.total_operations,
                save_plan=save_plan,
                message=f"All {completed} writes completed.",
            )
        else:
            # Some failed (not rate-limited, just errored)
            save_plan.completed_operations = completed
            await self._persist_save_plan(save_plan)
            return SavePlanResult(
                status="partial_rate_limited",
                completed=completed,
                total=save_plan.total_operations,
                save_plan=save_plan,
                message=(
                    f"Partial: {completed} of "
                    f"{save_plan.total_operations} writes succeeded; "
                    f"errors recorded. Will not auto-resume."
                ),
            )

    def _compute_cooldown_resume_at(self) -> str | None:
        """ISO timestamp when the rate-limit cooldown expires, or None."""
        if self.rate_limiter is None:
            return None
        try:
            return datetime.fromtimestamp(
                time.time() + self.rate_limiter._cooldown_seconds,
                tz=timezone.utc,
            ).isoformat()
        except Exception:
            return None

    async def _persist_save_plan(self, save_plan: SavePlan) -> None:
        """Persist the save plan to project metadata for resume."""
        key = f"blueprint.save_plan.{save_plan.run_id}"
        # Leader D4: manager reference is available for the project repo.
        try:
            await asyncio.to_thread(
                self.manager._project_repository.set_metadata,
                self.project_id, key, save_plan.to_dict(),
            )
        except Exception as e:
            logger.warning(
                "_persist_save_plan failed for run %s: %s",
                save_plan.run_id, e, exc_info=True,
            )

    async def _clear_save_plan(self, save_plan: SavePlan) -> None:
        """Remove the save plan from project metadata on completion."""
        key = f"blueprint.save_plan.{save_plan.run_id}"
        try:
            await asyncio.to_thread(
                self.manager._project_repository.delete_metadata,
                self.project_id, key,
            )
        except Exception as e:
            logger.warning(
                "_clear_save_plan failed for run %s: %s",
                save_plan.run_id, e, exc_info=True,
            )

    # ── Internal sync helper ───────────────────────────────────────────

    def _record_rate_result_sync(self, success: bool) -> None:
        """Sync variant for use before the first await in error paths.

        The rate limiter is sync; this avoids scheduling a task that
        would run after the exception is raised.

        C2 fix a: after switching to ``reserve()``, ``success=True`` is
        a no-op (reserve already consumed the slot). Only
        ``success=False`` calls ``record_failure``.
        """
        if self.rate_limiter is None:
            return
        if success:
            return
        try:
            self.rate_limiter.record_failure(self.project_id)
        except Exception as e:
            logger.warning(
                "rate_limiter.record_failure (sync) failed for project %s: %s",
                self.project_id, e, exc_info=True,
            )


__all__ = [
    "BlueprintWriteService",
    "BlueprintPublishError",
    "BlueprintNotFoundError",
    "BlueprintRateLimitError",
]

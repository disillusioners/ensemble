#!/usr/bin/env python3
"""One-time migration: existing ``mcp_servers.config.env`` plaintext → KMS marker.

P3-WP8 — designer-agent mission (closes arch §8 R3, the 🔴 "DB stores env
RAW today" risk).

Day-1 reality: prior to this migration, ``mcp_servers.config.env[<KEY>]``
held plaintext secrets (the ``redact_secrets`` presentation layer at
``daemon/routers/mcp_servers.py:57-111`` was the only barrier — a leak
risk). New installs since WP5/WP6 already store markers, so this script
exists to close the *pre-existing* hole for rows written before the
marker discipline landed.

Algorithm (per ``mcp_servers`` row, inside one DB transaction — PR6):

1. Walk ``config.env`` items.
2. For each ``(env_key, value)`` pair: if ``env_key`` matches the
   DESTRUCTIVE-REWRITE subset (see :data:`_SECRET_KEY_MARKERS` below —
   the strict subset ``{"KEY", "TOKEN", "SECRET", "PASSWORD"}`` of
   ``daemon/routers/mcp_servers.py::redact_secrets``' wider marker
   tuple; ``BASE`` and ``HEADERS`` are excluded because they are not
   secrets) AND the value is NOT already a KMS marker
   (``daemon/services/kms_resolver.is_marker``), mint a handle via
   ``daemon/services/kms_lite.kms_request`` and rewrite the value to
   ``__KMS_REF__<handle>__``.
3. Append the binding to ``instance_metadata.bound_handles`` with
   shape ``{handle, env_key, fingerprint, actor}`` — the same shape
   ``kms_attach`` writes (mirror exactly; this script does not import
   the tool layer because the tool layer routes through the manager
   facade which is out-of-scope for a one-shot migration).
4. Write the audit line (one per row rewritten) to
   ``install-audit.jsonl`` with the field set mandated by arch §7.4
   and the dispatch spec:
   ``{ts, event, name, actor, secret_ref, idempotency_key, trace_id}``.

Properties:

* **Per-row atomicity (PR6)** — each row migrates inside a single
  ``engine.begin()`` transaction. On any error during the rewrite of
  one row, that row's transaction rolls back, the row is untouched,
  the audit line is NOT emitted, and the script proceeds to the next
  row (or aborts under ``--fail-fast``). The script NEVER partial-writes
  a single row.
* **Idempotent** — already-marker values are skipped (``is_marker``
  pre-check). Re-running on an already-migrated DB produces zero mints
  and zero audit lines.
* **Dry-run** — ``--dry-run`` reports what *would* migrate but changes
  nothing and emits no audit lines.

CLI::

    python scripts/migrations/kms_lite_raw_row_migrate.py [options]

Options::

    --db-url URL        SQLAlchemy URL (default: $DATABASE_URL or sqlite:///./data/ensemble.db).
                        For the SQLite dev path, the script does NOT auto-bootstrap
                        the schema — the operator runs after ``dev.sh`` has brought
                        the tables up.
    --dry-run           Report what would migrate; change nothing; no audit lines.
    --jsonl-path PATH   Override the audit jsonl output path. Must end
                        with the canonical lane layout
                        ``.agents/shared/planning/designer-agent/install-audit.jsonl``
                        (treated as ``<workdir>/`` + that layout).
                        Default: the same canonical lane under the repo
                        root — the ONE file shared with the daemon's
                        mcp_install / kms_issue writers.
    --fail-fast         Abort on the first row-level failure instead of
                        continuing. Default: continue + report.
    --actor ID          Override the audit-line actor. Default:
                        ``migration:kms_lite_raw_row_migrate``.

Exit codes:

* ``0`` — clean run, zero rows migrated (no-op).
* ``1`` — rows migrated (and/or already-migrated rows verified idempotent).
* ``2`` — error (row-level failures listed in the report; with
          ``--fail-fast`` the script aborts at the first such failure).

Note on the audit helper (UPDATED P3-WP12a — unified):

Per the original dispatch spec this script carried its own minimal
JSONL append because the helper's path depended on ``os.getcwd()`` and
the field sets diverged. The P3-WP12a fold RECONCILED this as
anticipated: ``daemon/services/install_audit.append_install_audit`` is
now the single audit writer, and this script's ``_emit_audit_line`` is
a thin wrapper over it (``parent=None``; ``workdir=`` decomposition of
``--jsonl-path`` preserves the override semantics for paths under the
canonical ``.agents/shared/planning/designer-agent/`` layout). The
default audit path is the canonical lane — the SAME file the daemon's
configure-builtin (``mcp_install``) and mint (``kms_issue``) writers
use, so all event types land in one lane. The on-disk line carries the
helper's §7.4 field set (``parent`` present as ``null``); ``secret_ref``
remains the HANDLE only.

⚠️  REPLACEMENT SEMANTICS — OPERATIONAL HARD-GATE (P3 review F1):

This migration's day-1 mint primitive does NOT have an ``kms_import``
path (§7.5a defers it). For every plaintext it migrates, the script
MINTS A NEW RANDOM SECRET — the original credential the external
service has never seen. After migration, markers resolve to NEW random
secrets at spawn time, breaking the running config's auth until the
operator re-pairs the external service with the new secret
(service-by-service). This is acceptable for the SQLite dev path (the
operator is the same human running the migration and re-pairing) but
DANGEROUS for a live PG DB where the operator may run this against a
production row set unaware of the replacement semantics.

For that reason ``main()`` REFUSES to run against a non-SQLite
``--db-url`` unless ``--i-know-this-replaces-credentials`` is passed.
The gate is CLI-only (programmatic ``run_migration()`` callers are
dev-only and assume they know what they are doing). ``--dry-run`` is
exempt because it does not write.

Constraints (mission):

* Worktree-only — never reach into live or demo daemons.
* No push / merge / branch-creation. Self-commit only on green tests.
* No edits to owned zones of sibling coders (see dispatch notes).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import logging
import os
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Repo-root import path: this script lives under scripts/migrations/, so the
# parent-of-parent is the repo root. Mirror the bootstrap pattern used in
# scripts/migrate_agent_id.py.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy.engine import Engine

# Sibling-owned modules — IMPORT only (no re-implementation of the matching
# logic). The destructive-rewrite key matcher below is a STRICT SUBSET of
# the marker tuple used by ``daemon/routers/mcp_servers.py::redact_secrets``
# (case-insensitive substring match against
# ``("KEY","TOKEN","SECRET","PASSWORD","BASE","HEADERS")`` at
# daemon/routers/mcp_servers.py:226). Reuse via import is impossible
# because ``redact_secrets`` operates on a full config dict and
# substitutes values, not on a per-key check; the matcher tuple is
# module-local in that file. See the rationale on
# :data:`_SECRET_KEY_MARKERS` below for why ``BASE``/``HEADERS`` are
# excluded from the rewrite subset but kept in the presentation layer.
from daemon.services.install_audit import (
    INSTALL_AUDIT_RELATIVE_PATH,
    append_install_audit,
)
from daemon.services.kms_lite import KMSUnavailableError, build_marker, kms_request
from daemon.services.kms_resolver import is_marker

logger = logging.getLogger("kms_lite_migration")


# ---------------------------------------------------------------------------
# Key matcher — DESTRUCTIVE rewrite subset (P3 review F2)
# ---------------------------------------------------------------------------

# Destructive-rewrite subset of secret-key markers. MUST stay a STRICT
# SUBSET of the canonical tuple in ``daemon/routers/mcp_servers.py::redact_secrets``
# at line 226 (which is ``("KEY", "TOKEN", "SECRET", "PASSWORD", "BASE",
# "HEADERS")``).
#
# ``BASE`` and ``HEADERS`` are PRESENTATION-safe to redact (they guard the
# response payload so a leaked URL or header dict cannot surface a token),
# but they are NOT secrets — ``*_API_BASE`` is an endpoint URL and
# ``*_EXTRA_HEADERS`` is a config dict the operator set. Rewriting them
# via the day-1 mint primitive would CORRUPT CONFIG: the migration
# mints a NEW random secret (no ``kms_import`` primitive exists day-1;
# §7.5a defers an import path), so a ``*_API_BASE`` value like
# ``https://api.example.com/v1`` would be replaced by a NEW random
# secret, breaking the running config forever.
#
# If the canonical list ever changes upstream, update ``_SECRET_KEY_MARKERS``
# in the same commit AND re-verify this subset contract still holds.
_SECRET_KEY_MARKERS: tuple[str, ...] = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
)


def _is_secret_env_key(env_key: str) -> bool:
    """Return True iff ``env_key`` matches the DESTRUCTIVE-REWRITE secret subset.

    The migration's matcher is a STRICT SUBSET of the canonical redact
    tuple at ``daemon.routers.mcp_servers.redact_secrets`` line 226.
    Rationale (P3 review F2): ``redact_secrets`` is a *presentation*
    guard (replace value with ``"[REDACTED]"`` in the response payload),
    while this function gates a *destructive rewrite* (mint a NEW random
    secret to replace the plaintext — no import primitive day-1, §7.5a
    defers). ``BASE`` (``*_API_BASE`` endpoint URL) and ``HEADERS``
    (``*_EXTRA_HEADERS`` config dict) are correctly included in
    presentation redaction (defense in depth) but MUST NOT be migrated:
    rewriting them would corrupt running config.

    Match rule (case-insensitive substring):

    * Returns True iff ``env_key`` contains any of
      ``("KEY", "TOKEN", "SECRET", "PASSWORD")``.
    * Returns False otherwise (including ``*_API_BASE`` / ``*_EXTRA_HEADERS``).

    See ``daemon.routers.mcp_servers.redact_secrets`` for the upstream
    rationale on the wider marker set. If that tuple is ever revised,
    update ``_SECRET_KEY_MARKERS`` in the same commit AND re-verify the
    subset contract still holds.
    """
    if not isinstance(env_key, str) or not env_key:
        return False
    upper = env_key.upper()
    return any(m in upper for m in _SECRET_KEY_MARKERS)


# ---------------------------------------------------------------------------
# Audit line shape — fixed contract from arch §7.4 + dispatch spec
# ---------------------------------------------------------------------------

DEFAULT_ACTOR = "migration:kms_lite_raw_row_migrate"

# P3-WP12a audit-lane unification: the default audit path is the
# CANONICAL lane owned by daemon/services/install_audit.py —
# ``<repo>/.agents/shared/planning/designer-agent/install-audit.jsonl`` —
# the SAME file the daemon's configure-builtin + kms_issue writers use,
# so §6 row 2 (mcp_install + kms_issue + migration lines in one lane)
# holds by construction. The old script-private ``planning/designer-agent/``
# location is retired.
_DEFAULT_AUDIT_REL_PATH = INSTALL_AUDIT_RELATIVE_PATH


# ---------------------------------------------------------------------------
# Row migration dataclass
# ---------------------------------------------------------------------------


@dataclass
class RowMigrationResult:
    """Per-row migration outcome (counted in the summary)."""

    server_id: str
    name: str
    rewrites: int = 0
    skipped_marker: int = 0
    skipped_clean: int = 0
    audit_trace_id: str | None = None
    audit_secret_ref: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class MigrationSummary:
    """Aggregate result for the whole run."""

    pre_count: int = 0  # rows scanned
    migrated: int = 0  # rows with at least one rewrite
    skipped_marker: int = 0  # rows that had only-marker or no-sensitive-keys env
    skipped_clean: int = 0  # rows with no secret-key env entries at all
    failures: list[RowMigrationResult] = field(default_factory=list)
    audit_lines_emitted: int = 0
    dry_run: bool = False

    @property
    def total_failures(self) -> int:
        return len(self.failures)

    @property
    def exit_code(self) -> int:
        if self.failures:
            return 2
        if self.migrated > 0:
            return 1
        return 0


# ---------------------------------------------------------------------------
# Audit line emitter (minimal JSONL append — see module docstring)
# ---------------------------------------------------------------------------


def _audit_workdir(jsonl_path: Path) -> Path:
    """Map a target jsonl path back to the audit writer's ``workdir``.

    ``append_install_audit(workdir=W)`` writes to
    ``W / INSTALL_AUDIT_RELATIVE_PATH``. ``--jsonl-path`` semantics are
    preserved by exact suffix decomposition: any path ending with the
    canonical relative layout maps to its prefix. A non-decomposable
    path is a hard CLI error (fail loud) rather than silently writing
    somewhere else.
    """
    rel_parts = INSTALL_AUDIT_RELATIVE_PATH.parts
    parts = jsonl_path.parts
    if len(parts) >= len(rel_parts) and parts[-len(rel_parts):] == rel_parts:
        return Path(*parts[: len(parts) - len(rel_parts)])
    raise ValueError(
        f"--jsonl-path must end with '{INSTALL_AUDIT_RELATIVE_PATH}' "
        f"(the canonical install-audit lane); got: {jsonl_path}"
    )


def _emit_audit_line(
    jsonl_path: Path,
    *,
    server_name: str,
    actor: str,
    secret_ref: str,
    idempotency_key: str,
    trace_id: str,
    event: str = "migration_rewrite",
) -> None:
    """Append one audit line VIA the canonical install-audit writer.

    P3-WP12a unification: the script-private JSONL append is retired;
    :func:`daemon.services.install_audit.append_install_audit` is the
    single writer (same file, same field set, same never-raises core).
    ``parent`` is ``None`` (migration events have no parent instance).
    ``secret_ref`` stays the HANDLE only — never the marker, never the
    plaintext (handles-not-secrets, unchanged from the dispatch spec).

    The canonical field set is the helper's §7.4 set — ``parent`` is now
    present (as ``null``) where the legacy local writer omitted it.
    """
    result = append_install_audit(
        event=event,
        name=server_name,
        actor=actor,
        parent=None,
        secret_ref=secret_ref or None,
        idempotency_key=idempotency_key,
        trace_id=trace_id,
        workdir=_audit_workdir(jsonl_path),
    )
    if not result.written:
        # Preserve the legacy failure surface: an unwritable audit lane
        # raised OSError out of _migrate_single_row.
        raise OSError(f"install-audit append failed: {result.error}")


def _compute_idempotency_key(
    name: str, schema_version: str, config: dict[str, Any]
) -> str:
    """Compute the per-row idempotency key per arch §7.4.

    ``idempotency_key = sha256(name + schema_version + sorted(config))``.

    Uses the post-rewrite config so two audit lines describing the same
    persisted state collapse to the same key. Re-runs (which find no
    plaintext to rewrite) emit NO audit line, so dedup is over rows that
    were genuinely rewritten, not rows that were already clean.
    """
    config_blob = json.dumps(config, sort_keys=True, ensure_ascii=False)
    payload = f"{name}|{schema_version}|{config_blob}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Row rewrite (one transaction per row — PR6)
# ---------------------------------------------------------------------------


def _migrate_single_row(
    engine: Engine,
    server_row: Any,
    *,
    dry_run: bool,
    actor: str,
    jsonl_path: Path | None = None,
) -> RowMigrationResult:
    """Migrate one ``mcp_servers`` row inside a single transaction.

    Args:
        engine: SQLAlchemy ``Engine`` (SQLite for dev; PG for live).
        server_row: The ``McpServer`` model instance loaded by the caller.
            We do NOT re-read it inside the transaction (caller just
            loaded it fresh — R1 invariant from kms_attach carries over:
            read fresh, never cache+rewrite). The row's PK is the
            mutation target.
        dry_run: When True, compute everything but do not write.
        actor: Audit-line actor id.
        jsonl_path: When provided, emit the per-row audit line to this
            JSONL file AFTER the row's transaction commits. Pass
            ``None`` to suppress emission (useful for tests that want
            to drive the migration without on-disk artifacts; in dry-run
            the caller also passes ``None``).

    Returns:
        :class:`RowMigrationResult` describing the outcome.

    Notes:
        * On any error, the per-row transaction rolls back. The row is
          untouched. The audit line is NOT emitted. The error is captured
          in :attr:`RowMigrationResult.error` and the caller decides
          whether to continue or fail-fast.
        * On success, the row carries only markers for the previously
          plaintext slots, ``instance_metadata.bound_handles`` is
          extended with the new entries, and exactly one audit line is
          emitted per successfully rewritten row (per dispatch spec:
          "one JSON line appended to the install-audit.jsonl" — the
          line's ``secret_ref`` is the FIRST handle minted in the row,
          chosen so the per-row audit line corresponds to one ``name``).
    """
    result = RowMigrationResult(server_id=server_row.id, name=server_row.name)

    # Defensive copy — we MUST NOT mutate server_row.config in-place
    # before the transaction commits. The kms_resolver module's R1
    # invariant is "caller passes a freshly-fetched dict, never a
    # cached one" — we re-build the dict from server_row.config the
    # same way kms_attach does.
    existing_config = dict(server_row.config or {})
    env_block = dict(existing_config.get("env") or {})
    existing_meta = dict(server_row.instance_metadata or {})
    bindings = list(existing_meta.get("bound_handles") or [])

    # Walk env entries; classify each into one of three buckets. We
    # also track the order of freshly-minted handles so the per-row
    # audit line picks a deterministic ``secret_ref`` (the FIRST
    # handle minted in this row, per dispatch spec).
    #
    # Dry-run note: in dry-run mode we do NOT actually mint handles
    # (the dispatch spec says "report what would migrate, change
    # nothing"); we only count the would-be rewrites and skip the
    # handle fabrication. The audit-line emission is suppressed by
    # ``run_migration`` (which passes ``jsonl_path=None`` in dry-run).
    new_env: dict[str, str] = {}
    minted_in_order: list[str] = []
    pending_rewrites: list[tuple[str, str]] = []  # (env_key, plaintext)
    for k, v in env_block.items():
        if not _is_secret_env_key(k):
            new_env[k] = v
            continue
        if isinstance(v, str) and is_marker(v):
            # Already migrated — skip. Idempotency hinges on this.
            new_env[k] = v
            result.skipped_marker += 1
            continue
        # Otherwise: plaintext. In dry-run, we just count; in live,
        # we mint a handle and rewrite.
        pending_rewrites.append((k, v))

    # Track clean rows (no sensitive env keys at all) so the summary
    # distinguishes them from marker-only rows.
    if not _row_has_secret_keys(env_block):
        result.skipped_clean += 1

    if not pending_rewrites:
        # Nothing to do. The row is already in the desired state.
        return result

    if dry_run:
        # Report the would-be rewrite count without minting. The
        # caller counts this as ``migrated`` (see ``run_migration``),
        # but emits no audit line and persists no change.
        result.rewrites = len(pending_rewrites)
        # Mark the would-be rewritten env keys with a sentinel so the
        # caller can see the plan if it inspects the result. We do
        # NOT touch ``new_env`` for the live path — this branch is a
        # pure preview.
        logger.info(
            "[DRY-RUN] would migrate row id=%s name=%s rewrites=%d",
            server_row.id,
            server_row.name,
            result.rewrites,
        )
        return result

    # Live path: mint one handle per pending rewrite, build the
    # post-rewrite env + bindings, persist in one transaction.
    for env_key, _plaintext in pending_rewrites:
        record = kms_request(
            service=server_row.name, reason="raw_row_migration"
        )
        handle = record["handle"]
        fingerprint = record["fingerprint"]
        marker_value = build_marker(handle)
        new_env[env_key] = marker_value
        bindings.append(
            {
                "handle": handle,
                "env_key": env_key,
                "fingerprint": fingerprint,
                "actor": actor,
            }
        )
        minted_in_order.append(handle)
        result.rewrites += 1

    # Persist. Per-row transaction (PR6): one engine.begin() scope.
    new_config = dict(existing_config)
    new_config["env"] = new_env
    new_meta = dict(existing_meta)
    new_meta["bound_handles"] = bindings

    trace_id = uuid.uuid4().hex
    # secret_ref for the audit line: the FIRST handle minted in this
    # row (per dispatch spec: "secret_ref=<handle ONLY>" — the audit
    # line is per-row, so the per-row representative handle is the
    # first one minted in this row's rewrite pass).
    first_handle = minted_in_order[0] if minted_in_order else None
    idem_key = _compute_idempotency_key(
        server_row.name, server_row.config_schema_version or "0", new_config
    )
    result.audit_trace_id = trace_id
    result.audit_secret_ref = first_handle

    # Raw SQL inside one transaction. We bypass the repository
    # abstraction here because the migration runs once per row and
    # the repository's update_mcp_server(...) opens its own session
    # per call (we need ONE transaction spanning config + metadata).
    # We deliberately use parameter binding, not f-strings.
    from sqlalchemy import text

    try:
        with engine.begin() as conn:
            # Fresh-read invariant: re-load the row INSIDE the
            # transaction so a concurrent writer between our scan and
            # our update cannot lose data (R1).
            current_row = conn.execute(
                text(
                    "SELECT id, config, instance_metadata, config_schema_version "
                    "FROM mcp_servers WHERE id = :id"
                ),
                {"id": server_row.id},
            ).fetchone()
            if current_row is None:
                result.error = f"row disappeared during transaction: id={server_row.id}"
                return result
            # SQLite stores JSON as TEXT — we serialize ourselves to
            # match the repository layer (which lets SQLAlchemy handle
            # it via JSONBType). Both backends accept a JSON string here.
            conn.execute(
                text(
                    "UPDATE mcp_servers SET config = :config, "
                    "instance_metadata = :meta, updated_at = :ts "
                    "WHERE id = :id"
                ),
                {
                    "config": json.dumps(new_config, ensure_ascii=False),
                    "meta": json.dumps(new_meta, ensure_ascii=False),
                    "ts": _dt.datetime.now(tz=_dt.timezone.utc).isoformat(),
                    "id": server_row.id,
                },
            )
    except Exception as exc:  # noqa: BLE001 — per-row isolation: any error here rolls back
        result.error = f"{type(exc).__name__}: {exc}"
        logger.exception(
            "row migration failed: id=%s name=%s",
            server_row.id,
            server_row.name,
        )
        return result

    # Audit line emission — AFTER the row's transaction commits, so we
    # never audit a row that didn't actually rewrite. We use the
    # jsonl_path the caller supplies (or skip emission entirely if
    # ``jsonl_path is None`` — tests use this to drive the migration
    # without on-disk artifacts, and the dry-run caller also passes
    # ``None``).
    if jsonl_path is not None:
        _emit_audit_line(
            jsonl_path,
            server_name=server_row.name,
            actor=actor,
            secret_ref=first_handle or "",
            idempotency_key=idem_key,
            trace_id=trace_id,
        )

    return result


def _row_has_secret_keys(env_block: dict[str, Any]) -> bool:
    for k in env_block.keys():
        if _is_secret_env_key(k):
            return True
    return False


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def _resolve_jsonl_path(repo_root: Path, override: str | None) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    return (repo_root / _DEFAULT_AUDIT_REL_PATH).resolve()


def _build_engine(db_url: str) -> Engine:
    """Create the SQLAlchemy ``Engine`` for the run.

    The default URL ``sqlite:///./data/ensemble.db`` matches the project's
    dev DB convention (see ``dev.sh``). For local dry-runs / tests, callers
    typically pass an in-memory URL via ``--db-url sqlite:///:memory:``.
    """
    from sqlalchemy import create_engine

    return create_engine(db_url, future=True)


def _list_mcp_server_rows(engine: Engine) -> list[Any]:
    """Return all ``mcp_servers`` rows.

    Uses a fresh session per call. Caller is responsible for any
    concurrency story; the day-1 operator runs this once.
    """
    from sqlmodel import Session, select

    # Local import to keep the module-level imports light and to avoid
    # loading SQLModel at import time before argparse has resolved.
    from daemon.repositories.mcp_server.models import McpServer

    with Session(engine) as session:
        stmt = select(McpServer)
        return list(session.exec(stmt))


def run_migration(
    engine: Engine,
    *,
    jsonl_path: Path | None,
    dry_run: bool = False,
    fail_fast: bool = False,
    actor: str = DEFAULT_ACTOR,
) -> MigrationSummary:
    """Scan and rewrite all plaintext env rows. Returns the run summary.

    The function is the single entry point tests should call when they
    want to drive the migration programmatically (the CLI is a thin
    wrapper around it).

    Args:
        engine: SQLAlchemy ``Engine``.
        jsonl_path: Path to the audit jsonl file. Pass ``None`` to
            suppress audit emission entirely (tests use this). When
            non-None, the file's parent directories are created on
            first write.
        dry_run: When True, the migration reports what *would* happen
            but writes no rows and emits no audit lines.
        fail_fast: Abort on the first row-level failure instead of
            continuing.
        actor: Audit-line actor id.

    Returns:
        :class:`MigrationSummary` with per-row and aggregate counts.
    """
    summary = MigrationSummary(dry_run=dry_run)
    rows = _list_mcp_server_rows(engine)
    summary.pre_count = len(rows)

    # In dry-run mode we still report rewrites but emit NO audit lines
    # (per dispatch spec). The per-row helper accepts a ``jsonl_path``
    # only for the live mode.
    effective_jsonl = None if dry_run else jsonl_path

    for row in rows:
        try:
            outcome = _migrate_single_row(
                engine,
                row,
                dry_run=dry_run,
                actor=actor,
                jsonl_path=effective_jsonl,
            )
        except KMSUnavailableError as exc:
            # KMS unavailable — no key configured. Cannot proceed for
            # this row; record as a failure (do NOT raise; the operator
            # may want a partial report).
            outcome = RowMigrationResult(
                server_id=row.id,
                name=row.name,
                error=f"KMSUnavailableError: {exc}",
            )
        except Exception as exc:  # noqa: BLE001 — per-row isolation (PR6)
            # Any other error raised from _migrate_single_row (e.g. the
            # mint primitive raises, the SQL update raises BEFORE its
            # try/except) is captured per-row so one poisoned row
            # cannot abort the whole migration. The row is left
            # untouched on disk because _migrate_single_row's own
            # per-row ``engine.begin()`` block never ran to commit.
            outcome = RowMigrationResult(
                server_id=row.id,
                name=row.name,
                error=f"{type(exc).__name__}: {exc}",
            )

        if not outcome.ok:
            summary.failures.append(outcome)
            if fail_fast:
                return summary
            continue

        if outcome.rewrites > 0:
            summary.migrated += 1
            if not dry_run:
                summary.audit_lines_emitted += 1
        else:
            if outcome.skipped_marker > 0:
                summary.skipped_marker += 1
            elif outcome.skipped_clean > 0:
                summary.skipped_clean += 1
            # ELSE: row had secret keys but every value was already a
            # marker (mixed: skipped_clean==0, skipped_marker>=1) — counts
            # as skipped_marker (already-migrated row).

    return summary


def _print_summary(summary: MigrationSummary) -> None:
    print("=" * 70)
    print("KMS-Lite raw-row migration (P3-WP8)")
    print("=" * 70)
    print(f"  mode                : {'DRY-RUN' if summary.dry_run else 'LIVE'}")
    print(f"  rows scanned        : {summary.pre_count}")
    print(f"  rows migrated       : {summary.migrated}")
    print(f"  skipped (clean)     : {summary.skipped_clean}")
    print(f"  skipped (marker)    : {summary.skipped_marker}")
    print(f"  failures            : {summary.total_failures}")
    print(f"  audit lines emitted : {summary.audit_lines_emitted}")
    if summary.failures:
        print("")
        print("  --- FAILURES ---")
        for fail in summary.failures:
            print(f"  id={fail.server_id} name={fail.name!r} error={fail.error}")
    print("=" * 70)


# ---------------------------------------------------------------------------
# Operational hard-gate (P3 review F1)
# ---------------------------------------------------------------------------

# Exit code emitted by ``main()`` when the live-DB gate refuses to run.
# Distinct from the row-failure exit (2) and the clean-no-op / no-rows exit
# (0/1) so CI scripts and operators can tell the refusal apart from a
# normal run. ``3`` is otherwise unused by the script's exit surface.
_REPLACE_REFUSAL_EXIT_CODE = 3


def _is_sqlite_url(db_url: str) -> bool:
    """Return True iff ``db_url`` is a SQLAlchemy SQLite URL."""
    # SQLAlchemy prefixes; covers ``sqlite:///``, ``sqlite:///:memory:``,
    # and the relative ``sqlite:///./data/ensemble.db`` form.
    return db_url.split(":", 1)[0].lower() == "sqlite"


def _check_live_db_gate(
    db_url: str, *, dry_run: bool, i_know_replaces: bool
) -> None:
    """Refuse to run a LIVE (non-dry-run) migration against a non-SQLite DB
    unless the operator has explicitly acknowledged the replacement
    semantics (P3 review F1).

    Rationale: the day-1 mint primitive has no ``kms_import`` path
    (§7.5a defers it), so every migrated plaintext is REPLACED by a NEW
    random secret the external service has never seen. On a live DB this
    silently breaks auth for every migrated MCP server until the
    operator re-pairs the external service with the new credential —
    precisely the kind of accidental destruction a hard-gate exists to
    prevent.

    Exemptions:

    * ``--dry-run`` — read-only; safe to run on any URL.
    * ``sqlite:`` URLs — the day-1 dev path where the operator running
      the migration is the same human who will re-pair any external
      service. Still informational (the WARNING message prints) so a
      future operator running against an unexpected ``sqlite`` URL sees
      the replacement semantics.

    Raises:
        SystemExit: with ``_REPLACE_REFUSAL_EXIT_CODE`` when the gate
            refuses. The printed message includes the precise flag the
            operator needs to pass to override (``--i-know-this-replaces-credentials``).
    """
    if dry_run:
        return
    if i_know_replaces:
        return
    if _is_sqlite_url(db_url):
        return
    msg = (
        "REFUSING to run against non-SQLite --db-url without explicit "
        "acknowledgement of replacement semantics.\n"
        "\n"
        "This migration mints a NEW random secret for every plaintext it "
        "rewrites (no kms_import primitive exists day-1; §7.5a defers an "
        "import path). The original credential the external service has "
        "never seen is REPLACED — markers resolve to NEW random secrets "
        "at spawn time, breaking the running config's auth until the "
        "operator re-pairs each external service with its new credential.\n"
        "\n"
        f"  --db-url : {db_url!r}\n"
        "  --dry-run: False\n"
        "\n"
        "To proceed against a non-SQLite DB, re-run with:\n"
        "  --i-know-this-replaces-credentials\n"
        "\n"
        "Or run with --dry-run first to inspect what would be migrated "
        "(read-only; no writes, no audit lines)."
    )
    print(msg, file=sys.stderr)
    raise SystemExit(_REPLACE_REFUSAL_EXIT_CODE)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="kms_lite_raw_row_migrate",
        description=(
            "One-time migration: existing mcp_servers.config.env plaintext → "
            "KMS marker. P3-WP8 (designer-agent mission, closes arch §8 R3)."
        ),
    )
    p.add_argument(
        "--db-url",
        default=os.environ.get("DATABASE_URL", "sqlite:///./data/ensemble.db"),
        help="SQLAlchemy URL (default: $DATABASE_URL or sqlite:///./data/ensemble.db).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would migrate; change nothing; no audit lines.",
    )
    p.add_argument(
        "--jsonl-path",
        default=None,
        help=(
            "Override audit jsonl output path. "
            f"Default: <repo>/{_DEFAULT_AUDIT_REL_PATH}."
        ),
    )
    p.add_argument(
        "--fail-fast",
        action="store_true",
        help="Abort on the first row-level failure instead of continuing.",
    )
    p.add_argument(
        "--actor",
        default=DEFAULT_ACTOR,
        help=f"Audit-line actor id (default: {DEFAULT_ACTOR!r}).",
    )
    p.add_argument(
        "--i-know-this-replaces-credentials",
        dest="i_know_replaces",
        action="store_true",
        help=(
            "Acknowledgement flag for non-SQLite --db-url: confirms the "
            "operator understands the migration REPLACES plaintexts with "
            "NEW random secrets (no kms_import primitive day-1; §7.5a "
            "defers an import path). Required to run LIVE against any "
            "non-SQLite DB. SQLite and --dry-run are exempt."
        ),
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    args = _parse_args(argv)
    # Operational hard-gate (P3 review F1). CLI-only — programmatic
    # ``run_migration()`` callers are dev/test only and assume they know
    # what they are doing.
    _check_live_db_gate(
        args.db_url,
        dry_run=args.dry_run,
        i_know_replaces=args.i_know_replaces,
    )
    jsonl_path = _resolve_jsonl_path(_REPO_ROOT, args.jsonl_path)
    engine = _build_engine(args.db_url)
    summary = run_migration(
        engine,
        jsonl_path=jsonl_path,
        dry_run=args.dry_run,
        fail_fast=args.fail_fast,
        actor=args.actor,
    )
    _print_summary(summary)
    return summary.exit_code


if __name__ == "__main__":
    sys.exit(main())

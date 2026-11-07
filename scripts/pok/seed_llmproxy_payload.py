#!/usr/bin/env python3
"""POK Phase-3 seeder — materialize the §11 llmproxy payload on ensemble_dev.

Drives the REAL write path (``POKService.upsert`` → ``POKRepository`` —
the same service+repository layer ``daemon/tools/pok.py`` binds, fences,
attribution, append-with-supersede and admission semantics all live) —
NOT SQL backfill. The POK tables are materialized the way the PG boot
path does it (``daemon/manager.py``: register the POK models, then
``SQLModel.metadata.create_all`` — the file-based migration runner is
intentionally a NO-OP on non-SQLite engines).

STATUS: dev-proven, LIVE-GATED. This script targets ensemble_dev only;
the Phase-1.5 LIVE replay is owned by the leader (Emperor promote gate).

Safety contract (house env-poison discipline, anomaly 101a02e4):
  * the ambient login shell carries PROD PG vars — this script NEVER
    reads ``POSTGRES_*`` from ``os.environ``; its ONLY credential source
    is the worktree ``.env`` (dev-fenced), parsed here, and the resolved
    triple is hard-asserted to be
    ``ensemble_dev_app@127.0.0.1:5432/ensemble_dev`` BEFORE connect;
  * after connect it asserts ``current_database() == 'ensemble_dev'``
    and ``current_user == 'ensemble_dev_app'`` BEFORE any write;
  * the default project is refused BOTH directions by the service layer
    (A-5/AC10) — an accidental system-default write is structurally
    impossible.

Idempotency: append-with-supersede makes re-runs safe — a re-run is an
exact-dup NO-OP echo for every slug (no new rows, tier unchanged), the
project row is looked up (never re-created), and the pin re-asserts.
Run it twice; the second run must echo ``action=no-op`` per slug.

Usage (from the worktree root, worktree venv, env-scrubbed invocation):
    env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB \
        -u POSTGRES_USER -u POSTGRES_PASSWORD \
        .venv/bin/python scripts/pok/seed_llmproxy_payload.py

NEVER ``env -u 'POSTGRES_*'`` (shell glob — silent no-op). Per-variable
scrubs only — they are HYGIENE (keep ambient POSTGRES_* from being
read), NOT the fence. The REAL fence is in-code: the file-only env
source (parse .env, never os.environ), the SELF_ENV=dev assert,
and the current_database()/current_user() asserts below — those are
what make a non-dev DB structurally unreachable.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: Worktree root (this file lives at ``scripts/pok/``).
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import URL  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

from daemon.constants import BOOT_DB_TIMEOUT_S  # noqa: E402
from daemon.repositories.factory import (  # noqa: E402  (B2/B3 UTC contract)
    MANAGER_PG_SESSION_CONNECT_ARGS,
)
from daemon.repositories.pok.models import (  # noqa: E402  (register for create_all)
    ProjectOperatingKnowledge,
    ProjectOperatingKnowledgeEmbedding,
)
from daemon.repositories.pok.repository import POKRepository  # noqa: E402
from daemon.repositories.project.repository import (  # noqa: E402
    SQLModelProjectRepository,
)
from daemon.services.pok_service import (  # noqa: E402
    POK_ACTIVE_CAP_PER_PROJECT,
    POK_BODY_MAX_CHARS,
    POK_FAMILY_MAX,
    POK_SLUG_MAX_CHARS,
    POKError,
    POKService,
    fence_check,
)
from daemon.services.context_messages import (  # noqa: E402
    escape_for_context_block,
)

from payload_llmproxy import (  # noqa: E402  (local data module)
    PINNED_SLUG,
    POK_ENTRY_BODY_BOUND_DEFAULT,
    SEED_AGENT_ID,
    SEED_ENTRIES,
    TARGET_PROJECT_DESCRIPTION,
    TARGET_PROJECT_MAIN_DIRECTORY,
    TARGET_PROJECT_NAME,
)

# ── hard-asserted dev triple (the ONLY acceptable target) ────────────────
EXPECTED_PG_HOST = "127.0.0.1"
EXPECTED_PG_PORT = "5432"
EXPECTED_PG_DB = "ensemble_dev"
EXPECTED_PG_USER = "ensemble_dev_app"

ENV_PATH = REPO_ROOT / ".env"


def _load_worktree_env() -> dict[str, str]:
    """Parse the worktree ``.env`` — the ONLY credential source.

    Refuses when the giter dev-fence markers are missing (a stray or
    prod ``.env`` must never drive a write).
    """
    raw = ENV_PATH.read_text(encoding="utf-8")
    if "ENSEMBLE_SELF_ENV=dev" not in raw:
        raise SystemExit(
            f"REFUSED: {ENV_PATH} is not dev-fenced "
            "(missing ENSEMBLE_SELF_ENV=dev) — never target a non-dev DB"
        )
    env: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def _build_engine(env: dict[str, str]):
    """The manager-engine recipe, narrowed to one script connection.

    Mirrors ``create_postgres_engine`` (daemon/repositories/factory.py):
    ``postgresql+psycopg`` sync driver, ``URL.create`` (never an f-string
    — password escaping), UTC session connect_args (B2/B3), bounded
    libpq connect phase. Pool sizing collapses to the script's needs.
    """
    host = env["POSTGRES_HOST"]
    port = env["POSTGRES_PORT"]
    db = env["POSTGRES_DB"]
    user = env["POSTGRES_USER"]
    password = env["POSTGRES_PASSWORD"]
    triple = f"{user}@{host}:{port}/{db}"
    expected = f"{EXPECTED_PG_USER}@{EXPECTED_PG_HOST}:{EXPECTED_PG_PORT}/{EXPECTED_PG_DB}"
    if triple != expected:
        raise SystemExit(
            f"REFUSED: resolved DB target {triple!r} != dev triple "
            f"{expected!r} — fix the worktree .env, never the assert"
        )
    url = URL.create(
        drivername="postgresql+psycopg",
        username=user,
        password=password,
        host=host,
        port=int(port),
        database=db,
        query={
            "application_name": "pok-seed-phase3",
            "connect_timeout": str(BOOT_DB_TIMEOUT_S),
        },
    ).render_as_string(hide_password=False)
    return create_engine(
        url,
        echo=False,
        pool_size=2,
        max_overflow=2,
        pool_pre_ping=True,
        pool_reset_on_return="rollback",
        connect_args=dict(MANAGER_PG_SESSION_CONNECT_ARGS),
    )


def _assert_dev(engine) -> None:
    """``current_database()`` + ``current_user()`` asserted BEFORE writes."""
    with engine.connect() as conn:
        db = conn.execute(text("SELECT current_database()")).scalar_one()
        usr = conn.execute(text("SELECT current_user")).scalar_one()
    if db != EXPECTED_PG_DB or usr != EXPECTED_PG_USER:
        raise SystemExit(
            f"REFUSED: connected as {usr}@{db} — expected "
            f"{EXPECTED_PG_USER}@{EXPECTED_PG_DB}"
        )
    print(f"[assert] connected {usr}@{db} — dev triple verified")


def _ensure_pok_tables(engine) -> None:
    """The PG boot path (daemon/manager.py): register + create_all."""
    SQLModel.metadata.create_all(engine)
    with engine.connect() as conn:
        for table in (
            "project_operating_knowledge",
            "project_operating_knowledge_embeddings",
        ):
            exists = conn.execute(
                text("SELECT to_regclass(:r) IS NOT NULL"), {"r": f"public.{table}"}
            ).scalar_one()
            if not exists:
                raise SystemExit(f"REFUSED: table {table} failed to materialize")
    print("[schema] POK tables materialized (create_all; PG path)")


def _resolve_or_create_project(engine) -> tuple[str, bool]:
    """Look the dev P-1 row up by name; create via the REAL create path
    when absent (plan §4.5b convention). Returns (project_id, created)."""
    repo = SQLModelProjectRepository(engine)
    existing = repo.get_by_name(TARGET_PROJECT_NAME)
    if existing is not None:
        if existing.main_directory != TARGET_PROJECT_MAIN_DIRECTORY:
            raise SystemExit(
                f"REFUSED: project {TARGET_PROJECT_NAME!r} exists with "
                f"main_directory {existing.main_directory!r} — expected "
                f"{TARGET_PROJECT_MAIN_DIRECTORY!r}"
            )
        print(f"[project] found {TARGET_PROJECT_NAME!r} id={existing.project_id}")
        return existing.project_id, False
    project = repo.create(
        name=TARGET_PROJECT_NAME,
        project_type="general",
        main_directory=TARGET_PROJECT_MAIN_DIRECTORY,
        description=TARGET_PROJECT_DESCRIPTION,
        creator_agent_id=SEED_AGENT_ID,
    )
    print(
        f"[project] CREATED {TARGET_PROJECT_NAME!r} id={project.project_id} "
        "(FLAG: row was absent in ensemble_dev; created via the real "
        "ProjectRepository.create path per plan §4.5b)"
    )
    return project.project_id, True


def _validate_payload() -> None:
    """Pre-flight: the REAL fence + budgets + vocabularies + family cap."""
    from collections import Counter

    family_counts: Counter[str] = Counter()
    for entry in SEED_ENTRIES:
        fence = fence_check(entry.body)
        if fence is not None:
            raise SystemExit(f"REFUSED (fence) {entry.slug}: {fence.message}")
        escaped = len(escape_for_context_block(entry.body))
        if escaped > POK_BODY_MAX_CHARS:
            raise SystemExit(
                f"REFUSED (budget) {entry.slug}: escaped {escaped} > "
                f"{POK_BODY_MAX_CHARS}"
            )
        if not 54 <= len(entry.body) <= entry.body_bound:
            exempt = (
                ""
                if entry.body_bound == POK_ENTRY_BODY_BOUND_DEFAULT
                else (
                    f" (ceiling raised to {entry.body_bound}c by the "
                    "leader-mandated delta-shape exemption)"
                )
            )
            raise SystemExit(
                f"REFUSED (body bound) {entry.slug}: body is {len(entry.body)}c, "
                f"bound is 54-{entry.body_bound}c "
                f"(§11 sizing {POK_ENTRY_BODY_BOUND_DEFAULT}){exempt}"
            )
        if len(entry.slug) > POK_SLUG_MAX_CHARS:
            raise SystemExit(f"REFUSED (slug) {entry.slug}: > {POK_SLUG_MAX_CHARS}c")
        if entry.domain not in (
            "artifact",
            "deploy",
            "verify",
            "environment",
            "catalog",
            "other",
        ):
            raise SystemExit(f"REFUSED (domain) {entry.slug}: {entry.domain}")
        if entry.audience not in ("operation", "implementation", "both"):
            raise SystemExit(f"REFUSED (audience) {entry.slug}: {entry.audience}")
        parts = entry.slug.split("-")
        if len(parts) >= 3 and parts[-2].isdigit() and len(parts[-2]) == 2:
            family_counts[parts[0]] += 1
    oversized = [f for f, n in family_counts.items() if n > POK_FAMILY_MAX]
    if oversized:
        raise SystemExit(f"REFUSED (family cap): {oversized}")
    if len(SEED_ENTRIES) > POK_ACTIVE_CAP_PER_PROJECT:
        raise SystemExit("REFUSED: payload exceeds the 64-active cap")
    print(f"[validate] {len(SEED_ENTRIES)} entries fence+bound clean")


def _seed(engine, project_id: str) -> None:
    """The real write path: upsert × 8, then the §4.5 pin."""
    svc = POKService(engine)
    repo = POKRepository(engine)
    receipts: list[dict] = []
    for entry in SEED_ENTRIES:
        try:
            outcome = svc.upsert(
                project_id=project_id,
                slug=entry.slug,
                body=entry.body,
                domain=entry.domain,
                provenance="observed",
                audience=entry.audience,
                source_tier=None,
                agent_id=SEED_AGENT_ID,
                escape_fn=escape_for_context_block,
            )
        except POKError as exc:
            raise SystemExit(f"REFUSED (upsert) {entry.slug}: {exc.to_dict()}") from exc
        row = outcome.entry
        receipts.append(
            {
                "slug": row.slug,
                "action": outcome.action,
                "id": row.id,
                "domain": row.domain,
                "audience": row.audience,
                "source_tier": row.source_tier,
                "status": row.status,
                "created_by_agent": row.created_by_agent,
                "created_at": row.created_at,
                "superseded_id": outcome.superseded_id or "",
                "sources": "+".join(entry.source_note_ids),
            }
        )
    # §4.5 row 4: pin pipeline-03 via the real curate path (leader gate).
    try:
        pinned_row = svc.curate(
            project_id=project_id,
            slug=PINNED_SLUG,
            pinned=True,
            agent_id=SEED_AGENT_ID,
        )
    except POKError as exc:
        raise SystemExit(f"REFUSED (curate pin): {exc.to_dict()}") from exc
    print("[pin] {} pinned={} (curator={})".format(
        PINNED_SLUG, pinned_row.pinned, SEED_AGENT_ID
    ))
    # Post-write verification straight from the store.
    rows = repo.list_entries(project_id, statuses=("active",))
    print(f"[verify] active entries: {len(rows)} / {len(SEED_ENTRIES)} expected")
    print(
        f"{'slug':<45} {'action':<10} {'audience':<10} {'tier':<8} {'entry id'}"
    )
    for r in receipts:
        print(
            f"{r['slug']:<45} {r['action']:<10} {r['audience']:<10} "
            f"{r['source_tier']:<8} {r['id']}"
        )
    ids = {r["slug"]: r["id"] for r in receipts}
    if len(rows) != len(SEED_ENTRIES) or set(ids.values()) != {
        str(row.id) for row in rows
    }:
        raise SystemExit("REFUSED: post-write store state != payload")
    print(
        f"[receipts] total={len(receipts)} attribution={SEED_AGENT_ID} "
        "provenance=observed"
    )


def main() -> None:
    env = _load_worktree_env()
    engine = _build_engine(env)
    _assert_dev(engine)
    _ensure_pok_tables(engine)
    project_id, created = _resolve_or_create_project(engine)
    _validate_payload()
    _seed(engine, project_id)
    engine.dispose()
    print("[done] seed complete (re-run = exact-dup no-op echo per slug)")


if __name__ == "__main__":
    main()

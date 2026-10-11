"""Operator token mint/revoke CLI — oauth2-auth Phase 4 (T4.1 / D4.1).

``python -m daemon.auth.cli <subcommand>`` — Lane B's mint surface:

    issue-token --label LABEL [--ttl SECONDS] [--scopes "a,b"] [--bootstrap]
    revoke --label LABEL

One-shot bootstrap consumption (AMENDED v1.1 / C2 (b), AMEND v1.3 /
F10 SENTINEL ROW): ``--bootstrap`` reads
``ENSEMBLE_AUTH_BOOTSTRAP_TOKEN``; the first mint inserts the bearer
row AND the ``auth_bootstrap_consumption`` sentinel row
(``sha256(env_value)``) in ONE transaction; every later mint with the
same env value is REJECTED with the stored audit detail
(``consumed_at`` + ``actor_label``). Empty env + ``--bootstrap`` →
the helpful §6.3-row-10 failure.

Hash-only discipline (NFR-13 / D20b): the plaintext token is printed
ONCE on stdout and NEVER persisted — the bearer row and the sentinel
row both carry ``sha256(material)`` hex only. The bearer lane's
namespace is distinct from the session lane and the module lanes
(D20b): a bearer authenticates the /api surface, nothing else.

DB resolution mirrors the daemon's own chain (ensemble.json →
postgres | sqlite over ``config.persistence.db_path``); the tables are
created by the daemon boot (manager ``create_all``) — a CLI run
against an unbooted store fails loudly with the boot hint.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import sys
from pathlib import Path
from typing import Any

from daemon.auth.scopes import (
    BOOTSTRAP_DEFAULT_SCOPES,
    normalize_scope_list,
)
from daemon.config import AUTH_BOOTSTRAP_TOKEN_REQUIRED_MESSAGE
from daemon.repositories.auth_session.models import BearerToken
from daemon.repositories.auth_session.repository import AuthSessionRepository
from daemon.repositories.auth_bootstrap.repository import (
    AuthBootstrapRepository,
    BootstrapConsumed,
    DuplicateTokenMaterial,
    mint_with_sentinel,
)
from daemon.services.timestamps import now_utc_iso

#: Bootstrap env var (C2 (b) — the one-shot mint material).
BOOTSTRAP_ENV_VAR = "ENSEMBLE_AUTH_BOOTSTRAP_TOKEN"

#: Token material entropy (bytes, pre-URL-safe-encoding ≈ 43 chars —
#: the same 256-bit class as session ids).
TOKEN_BYTES = 32

#: Shell-history risk warning (printed to stderr on every --bootstrap
#: use — the plan's §6 risk "Bootstrap token mishandled" mitigation).
BOOTSTRAP_WARN = (
    "warning: --bootstrap reads ENSEMBLE_AUTH_BOOTSTRAP_TOKEN from the "
    "process env; a value passed on the command line can land in shell "
    "history. Prefer `read -s` into the env, and REMOVE the env var "
    "after the first mint (docs/runbooks/oauth2-auth.md §Token mint)."
)


class CliError(Exception):
    """Operator-actionable CLI failure — message printed, exit 1."""


def hash_token(material: str) -> str:
    """``sha256(material)`` hex — the ONLY persisted token shape."""
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m daemon.auth.cli",
        description="OAuth2 bearer-token mint/revoke CLI (oauth2-auth P4).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    issue = sub.add_parser(
        "issue-token", help="Mint a static bearer token (plaintext printed ONCE)."
    )
    issue.add_argument(
        "--label",
        required=True,
        help="Owner label (the token's subject; e.g. mcpservers-test).",
    )
    issue.add_argument(
        "--ttl",
        type=int,
        default=None,
        help="TTL seconds (default: auth.bearer_token_ttl_seconds, 30d; "
        "accepted range 60..31536000 — aligned with the config ceiling).",
    )
    issue.add_argument(
        "--scopes",
        default=None,
        help="CSV/space scope list (validated against the v1 taxonomy). "
        "Default for --bootstrap: auth:admin only (OD-14).",
    )
    issue.add_argument(
        "--bootstrap",
        action="store_true",
        help="Consume ENSEMBLE_AUTH_BOOTSTRAP_TOKEN (one-shot) to mint "
        "the FIRST admin token.",
    )

    revoke = sub.add_parser(
        "revoke", help="Soft-revoke every bearer row carrying --label."
    )
    revoke.add_argument(
        "--label",
        required=True,
        help="The owner label issued at mint time.",
    )
    return parser


def resolve_engine(config: Any = None):
    """Resolve the daemon's MAIN engine (the auth tables' engine).

    Mirrors the manager's backend selection: ``ensemble.json`` →
    ``create_postgres_engine`` when postgres, else SQLite over
    ``config.persistence.db_path``. Callers may inject an engine
    directly (tests / DI) — production resolves this chain.
    """
    from sqlalchemy.engine import Engine

    if isinstance(config, Engine):  # DI convenience
        return config
    if config is None:
        from daemon.config import load_config

        config = load_config()

    from daemon.ensemble_config import EnsembleConfig

    data_dir = Path(
        os.environ.get("ENSEMBLE_DATA_DIR")
        or os.environ.get("DATA_DIR")
        or "./data"
    )
    ensemble_config = EnsembleConfig.load_or_create(data_dir)
    if ensemble_config.is_postgres:
        from daemon.repositories.factory import create_postgres_engine

        return create_postgres_engine(ensemble_config)
    from daemon.repositories.factory import (
        DatabaseConfig,
        create_engine_from_config,
    )

    return create_engine_from_config(
        DatabaseConfig.sqlite(db_path=str(config.persistence.db_path))
    )


def _tables_present(engine: Any) -> bool:
    """True when the auth tables exist (daemon booted at least once)."""
    from sqlalchemy import inspect as sa_inspect

    names = set(sa_inspect(engine).get_table_names())
    return {
        "auth_sessions",
        "auth_states",
        "auth_bearer_tokens",
        "auth_bootstrap_consumption",
    } <= names


def _issue_token(
    *,
    label: str,
    ttl: int,
    scopes: tuple[str, ...],
    bootstrap_material: str | None,
    engine: Any,
    bearer_ttl_default: int,
    pid: int,
) -> str:
    """The mint core. Returns the PLAINTEXT token (printed once)."""
    material = secrets.token_urlsafe(TOKEN_BYTES)
    now_iso = now_utc_iso()
    row = BearerToken(
        id_hash=hash_token(material),
        subject=label,
        scopes=",".join(scopes) if scopes else "",
        label=label,
        created_at=now_iso,
        expires_at=_iso_plus_seconds(now_iso, ttl or bearer_ttl_default),
        last_used_at=None,
        revoked_at=None,
    )
    if bootstrap_material is not None:
        sentinel_hash = hash_token(bootstrap_material)
        try:
            mint_with_sentinel(
                engine,
                bearer_row=row,
                sentinel_id_hash=sentinel_hash,
                actor_label=label,
                consumed_at=now_iso,
                consumed_by_pid=pid,
            )
        except BootstrapConsumed as exc:
            raise CliError(
                f"bootstrap token already consumed at {exc.consumed_at} "
                f"for {exc.actor_label}; rotate the env var"
            ) from exc
        except DuplicateTokenMaterial as exc:
            raise CliError(
                "refusing to mint: identical token material already "
                "exists (revoked_at="
                f"{exc.revoked_at or '-'}; rotate and retry)"
            ) from exc
    else:
        # Non-bootstrap mint: duplicate material is practically
        # impossible (fresh random), but the same storage verdict
        # applies — a plain insert surfaces the PK conflict.
        AuthSessionRepository(engine).insert_bearer(row)
    return material


def _revoke(*, label: str, engine: Any) -> int:
    """Soft-revoke every row carrying ``label``; returns revoked count."""
    repository = AuthSessionRepository(engine)
    rows = repository.get_bearer_hashes_by_label(label)
    if not rows:
        raise CliError(f"no bearer tokens carry label {label!r}")
    now_iso = now_utc_iso()
    performed = 0
    for row in rows:
        if repository.revoke_bearer(row.id_hash, now_iso=now_iso):
            performed += 1
    return performed


def main(
    argv: list[str] | None = None,
    *,
    engine: Any = None,
    config: Any = None,
    env: dict[str, str] | None = None,
    stdout: Any = None,
    stderr: Any = None,
) -> int:
    """CLI entry (DI-clean: engine/config/env/streams injectable)."""
    environ = env if env is not None else os.environ
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    args = _build_parser().parse_args(argv)

    if engine is None:
        if config is None:
            from daemon.config import load_config

            config = load_config()
        engine = resolve_engine(config)
    if not _tables_present(engine):
        print(
            "auth tables not found on the resolved database — boot the "
            "daemon once (the manager creates the auth schema at init), "
            "then retry.",
            file=err,
        )
        return 1

    auth_config = getattr(config, "auth", None) if config is not None else None
    if auth_config is None and config is not None and hasattr(
        config, "bearer_token_ttl_seconds"
    ):
        # A bare AuthConfig was injected (test/DI convenience).
        auth_config = config
    bearer_ttl_default = (
        int(auth_config.bearer_token_ttl_seconds)
        if auth_config is not None
        else 2592000
    )

    if args.command == "issue-token":
        # ADV-4 rider (2026-10-11 review): validate --ttl BEFORE any
        # mint. Defect being closed: a negative TTL slipped through
        # ``ttl or bearer_ttl_default`` (non-zero → truthy) and minted
        # an already-expired token with exit 0; 0 silently meant
        # "default". Bounds AGREE with the §6.1 config ceiling
        # (auth.bearer_token_ttl_seconds ∈ [60, 31536000],
        # daemon/config.py) — the two sides are intentionally
        # identical; do not weaken either.
        if args.ttl is not None and not 60 <= args.ttl <= 31536000:
            print(
                f"invalid --ttl {args.ttl}: accepted range is "
                "60..31536000 seconds (aligned with the "
                "auth.bearer_token_ttl_seconds config ceiling); "
                "omit --ttl to use the configured default",
                file=err,
            )
            return 1
        scopes: tuple[str, ...] = ()
        if args.scopes:
            scopes = normalize_scope_list(args.scopes)
            if not scopes:
                print(
                    f"invalid --scopes {args.scopes!r}: unknown scope "
                    "(the v1 taxonomy is closed — see daemon/auth/scopes.py)",
                    file=err,
                )
                return 1
        bootstrap_material: str | None = None
        if args.bootstrap:
            print(BOOTSTRAP_WARN, file=err)
            bootstrap_material = environ.get(BOOTSTRAP_ENV_VAR, "").strip()
            if not bootstrap_material:
                print(AUTH_BOOTSTRAP_TOKEN_REQUIRED_MESSAGE, file=err)
                return 1
            if not scopes:
                # OD-14: the first-mint default is auth:admin ONLY.
                scopes = BOOTSTRAP_DEFAULT_SCOPES
        elif not scopes:
            print(
                "--scopes is required for non-bootstrap mints "
                "(conservative default: nothing implicit). Example: "
                '--scopes "instances:read,jobs:read"',
                file=err,
            )
            return 1
        try:
            material = _issue_token(
                label=args.label,
                ttl=args.ttl or 0,
                scopes=scopes,
                bootstrap_material=bootstrap_material,
                engine=engine,
                bearer_ttl_default=bearer_ttl_default,
                pid=os.getpid(),
            )
        except CliError as exc:
            print(str(exc), file=err)
            return 1
        # The plaintext crosses stdout EXACTLY ONCE — nothing else is
        # printed on stdout, and the persisted rows carry hashes only.
        print(material, file=out)
        print(
            f"issued token label={args.label} scopes={','.join(scopes)} "
            f"ttl_seconds={args.ttl or bearer_ttl_default} "
            f"bootstrap={'yes' if bootstrap_material else 'no'}",
            file=err,
        )
        return 0

    if args.command == "revoke":
        try:
            performed = _revoke(label=args.label, engine=engine)
        except CliError as exc:
            print(str(exc), file=err)
            return 1
        print(f"revoked {performed} token(s) for label {args.label!r}", file=out)
        return 0

    return 2  # unreachable — argparse enforces the subcommand


def _iso_plus_seconds(iso: str, seconds: int) -> str:
    from datetime import datetime, timedelta, timezone

    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt + timedelta(seconds=seconds)).isoformat()


if __name__ == "__main__":  # pragma: no cover — the module entry
    raise SystemExit(main())


__all__ = [
    "BOOTSTRAP_ENV_VAR",
    "CliError",
    "main",
    "resolve_engine",
]

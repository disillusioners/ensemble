"""Shared types for the infra asset repository.

This module exposes:

* :class:`JSONBType` — a SQLAlchemy ``TypeDecorator`` that resolves
  to ``JSONB`` on PostgreSQL and ``JSON`` on SQLite. The decorator
  keeps a single set of SQLModel definitions working on both
  drivers without per-dialect schema forks.

* :class:`InfraTypeDefinition` and
  :data:`INFRA_TYPE_DEFINITIONS` — Pydantic definitions for the
  three built-in infrastructure asset types (datacenter, server,
  k8s_cluster). These are seed data consumed by
  ``SQLModelInfraRepository.bootstrap_default_types()`` (used to
  pre-populate ``infra_asset_types`` on first run) and by the
  DevOps tool layer for type-aware validation.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import JSON, TypeDecorator
from sqlalchemy.dialects.postgresql import JSONB


class JSONBType(TypeDecorator):
    """Dialect-aware JSON column type.

    * **PostgreSQL** → ``JSONB`` (enables ``@>`` containment, ``?``
      key-existence, ``@?`` path predicates, and GIN indexes).
    * **SQLite** → ``JSON`` (TEXT storage with ``json_extract``
      support). Basic ``json_extract()``-based filtering is the
      best SQLite can offer; richer containment is a PostgreSQL-only
      feature.

    The decorator pattern is preferred over per-dialect table
    forks because it keeps a single ``SQLModel`` table definition
    portable across both backends, which is required by the
    project's "PostgreSQL is the default, SQLite is for dev"
    rule (``v0.5.2+``).
    """

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        """Return the dialect-specific column type.

        Args:
            dialect: SQLAlchemy ``Dialect`` instance. We branch on
                ``dialect.name``; the project only ships SQLite and
                PostgreSQL drivers, so anything else falls back to
                generic ``JSON`` (which is correct for any future
                dialect that supports JSON columns).

        Returns:
            A SQLAlchemy type descriptor (e.g. ``JSONB()`` for
            PostgreSQL, ``JSON()`` otherwise).
        """
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        """Coerce a Python value to its on-the-wire representation.

        The decorator is transparent in both directions: SQLAlchemy's
        default JSON serialization (which calls ``json.dumps`` on the
        way to PostgreSQL and stores the dict as TEXT on SQLite) is
        exactly what we want, so this is a no-op.
        """
        return value

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        """Coerce a DB-read value back to its Python representation.

        Likewise a no-op: SQLAlchemy's default JSON deserializer
        returns a Python ``dict``/``list`` for both backends.
        """
        return value


class InfraTypeDefinition(BaseModel):
    """Pydantic definition of a built-in infra asset type.

    Captures the human-readable name, a one-line description, and
    a JSON-Schema-shaped ``schema`` document that the DevOps tool
    layer uses to validate ``attributes`` before insert.

    The schema is intentionally loose (a plain ``dict``) — runtime
    validation lives in the tool layer, not the repository. The
    repository only stores the ``schema`` blob verbatim.
    """

    # Note: we name the field ``schema_doc`` (not ``schema``)
    # because Pydantic v2 hard-checks for shadowing of the
    # ``BaseModel.schema()`` serialization method and emits a
    # ``UserWarning`` at class-creation time. The field's public
    # meaning is the same ("JSON-Schema-shaped definition of the
    # asset type"); only the Python attribute name differs.

    type_name: str = Field(..., description="Unique type identifier, e.g. 'server'")
    description: str = Field(..., description="One-line human-readable description")
    schema_doc: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON-Schema-shaped hint for the attributes payload",
    )


INFRA_TYPE_DEFINITIONS: list[InfraTypeDefinition] = [
    InfraTypeDefinition(
        type_name="datacenter",
        description="A physical datacenter or cloud region",
        schema_doc={
            "type": "object",
            "properties": {
                "location": {"type": "string"},
                "provider": {"type": "string"},
                "tier": {"type": "string", "enum": ["tier1", "tier2", "tier3", "tier4"]},
                "power_capacity_kw": {"type": "number"},
            },
        },
    ),
    InfraTypeDefinition(
        type_name="server",
        description="A physical or virtual server host",
        schema_doc={
            "type": "object",
            "properties": {
                "hostname": {"type": "string"},
                "ip_address": {"type": "string"},
                "os": {"type": "string"},
                "cpu_count": {"type": "integer", "minimum": 1},
                "memory_gb": {"type": "number", "minimum": 0},
                "environment": {
                    "type": "string",
                    "enum": ["production", "staging", "development", "test"],
                },
                "datacenter": {"type": "string"},
            },
            "required": ["hostname"],
        },
    ),
    InfraTypeDefinition(
        type_name="k8s_cluster",
        description="A Kubernetes cluster",
        schema_doc={
            "type": "object",
            "properties": {
                "version": {"type": "string"},
                "region": {"type": "string"},
                "node_count": {"type": "integer", "minimum": 1},
                "network_cidr": {"type": "string"},
                "managed_by": {"type": "string"},
            },
            "required": ["version"],
        },
    ),
]

"""Scope taxonomy + grant model — oauth2-auth Phase 4 (D4.2 / D9 / §7.3).

The SINGLE source of truth for v1 scope names and for what each auth
lane GRANTS. Every consumer of a scope name (decorators, CLI defaults,
tests, runbook tables) reads from here; the §7.3 minimum-scope table
and the §5.4 per-router matrix reference these constants.

The v1 taxonomy (D4.2 — the fine-grained set shipped day 1):

    system:read  system:probe
    instances:read  instances:write
    jobs:read  jobs:write
    messages:write
    sources:write
    maintenance:read
    mcp:read  mcpkb:admin
    snapshot:write  workspace:read
    auth:admin
    prompts:read  prompts:submit  prompts:approve  prompts:audit  prompts:write
    settings:read  settings:write

Naming sources ([REQ] §5.4 / §7.3 / T3.12): the seven D4.2 scopes plus
the named additions — ``mcpkb:admin`` (the OAuth2-managed KB-key CRUD
family, T3.12), ``snapshot:write`` (§5.4 settings snapshot-admin
paths), ``workspace:read`` (§5.4 workspace row), and the five
``prompts:*`` scopes (Phase B agent-prompt-immutability commission —
T4 mint per phaseB-plan.md D11; ``prompts:approve`` joins the operator
family via SESSION_OPERATOR_SCOPES = SCOPE_TAXONOMY), and
``settings:{read,write}`` (db-backed-settings commission, architect
Ruling 4 — the daemon-settings surface is the infrastructure
precedent class: GET reports effective daemon config incl. env/yaml
tier values, so its reads gate like instances/maintenance rather than
the ungated language/timezone preference family). Nothing invented
beyond the plan text.

Grant model (P4 — the M-3 close; supersedes the P3
"session-valid-is-enough" decorator branch):

* **Session lane (cookie, the browser operator)** — the session row is
  persisted with an EXPLICIT grant set: the configured OIDC scopes
  (``openid profile email``) UNION :data:`SESSION_OPERATOR_SCOPES`
  (the whole operator family — the browser lane IS the operator, and
  the SPA calls every control-plane family; NFR-18 keeps the SPA
  unchanged). ``@require_scope`` checks set membership like any other
  lane — NO lane satisfies scopes by identity alone anymore, so the
  grant is visible, auditable, and per-scope revocable in v2.
* **Bearer lane (machine)** — EXACTLY the scopes minted onto the token
  row (CLI ``--scopes``; default ``auth:admin`` only for the bootstrap
  first mint, OD-14). Nothing implicit, ever.
* The literal ``*`` scope satisfies everything (reserved for explicit
  wildcard declarations; no v1 mint path produces it).
"""

from __future__ import annotations

#: The full v1 scope taxonomy (D4.2 + the named §5.4/§7.3 additions +
#: Phase B agent-prompt-immutability T4 — ``prompts:{read,submit,approve,
#: audit,write}`` per phaseB-plan.md D11 + db-backed-settings —
#: ``settings:{read,write}`` per architect Ruling 4).
SCOPE_TAXONOMY: frozenset[str] = frozenset(
    {
        "system:read",
        "system:probe",
        "instances:read",
        "instances:write",
        "jobs:read",
        "jobs:write",
        "messages:write",
        "sources:write",
        "maintenance:read",
        "mcp:read",
        "mcpkb:admin",
        "snapshot:write",
        "workspace:read",
        "auth:admin",
        # Phase B / agent-prompt-immutability T4 — minted per
        # phaseB-plan.md D11. ``prompts:approve`` is the operator-
        # family scope (it joins SESSION_OPERATOR_SCOPES via the
        # SCOPE_TAXONOMY reference below); ``prompts:submit`` is
        # broader (machine + human can hold it; the agent's internal
        # lane bypasses HTTP scope entirely — see D10); ``prompts:
        # read`` / ``prompts:audit`` / ``prompts:write`` cover the
        # remaining surface (create-draft + selection + audit read).
        "prompts:read",
        "prompts:submit",
        "prompts:approve",
        "prompts:audit",
        "prompts:write",
        # db-backed-settings (architect Ruling 4) — the daemon
        # runtime-settings API: GET /api/settings/daemon gates
        # ``settings:read`` (reports effective daemon config incl.
        # env/yaml tier values — infrastructure class); PUT/DELETE
        # gate ``settings:write``. Mass-noun resource naming mirrors
        # ``workspace`` / ``maintenance``; the read/write split
        # mirrors the dominant instances family. Auto-joins
        # SESSION_OPERATOR_SCOPES via the SCOPE_TAXONOMY reference
        # below (SPA session lane needs no change).
        "settings:read",
        "settings:write",
    }
)

#: The wildcard scope (reserved; satisfies every requirement).
SCOPE_WILDCARD: str = "*"

#: The OPERATOR grant family — what the session lane carries on top of
#: the configured OIDC scopes. The browser lane IS the operator (§5.4
#: verdicts make every control-plane family SPA-consumed), so the
#: session grant is the full taxonomy. Declared HERE (not sprinkled in
#: the service) so the grant surface is one reviewable constant.
SESSION_OPERATOR_SCOPES: frozenset[str] = SCOPE_TAXONOMY

#: The bootstrap first-mint default (OD-14): ``auth:admin`` ONLY — the
#: first token can administer (rotate/revoke via the CLI) but touches
#: no control-plane family until re-minted with explicit scopes.
BOOTSTRAP_DEFAULT_SCOPES: tuple[str, ...] = ("auth:admin",)


def normalize_scope_list(raw: str | None) -> tuple[str, ...]:
    """Parse a CSV/space scope string into a validated tuple.

    Accepts both list conventions used across the auth tables (the
    space-joined session convention and the CSV bearer convention —
    the same shape :func:`daemon.services.auth_service._parse_scopes`
    accepts). Unknown names are REJECTED (return ``()`` signals the
    caller to fail loudly) — the taxonomy is closed in v1; a typo'd
    scope must never silently mint an under- or over-privileged token.
    The wildcard is allowed verbatim.
    """
    if not raw or not raw.strip():
        return ()
    fragments = {
        fragment
        for fragment in raw.replace(",", " ").split()
        if fragment
    }
    ordered = tuple(sorted(fragments))
    for scope in ordered:
        if scope != SCOPE_WILDCARD and scope not in SCOPE_TAXONOMY:
            return ()
    return ordered


__all__ = [
    "BOOTSTRAP_DEFAULT_SCOPES",
    "SCOPE_TAXONOMY",
    "SCOPE_WILDCARD",
    "SESSION_OPERATOR_SCOPES",
    "normalize_scope_list",
]

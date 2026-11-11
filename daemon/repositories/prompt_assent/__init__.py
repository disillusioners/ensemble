"""The ``prompt_assent`` repository package (2026-11-10).

The durable leg of the conversational approval lane ("The Emperor's Word /
Assent"): the ``prompt_assent_tokens`` table (hash-only, item-bound,
mint-context-bound), its repository (mint / find / consume / own-transaction
attempt counters / TTL sweep) and the fail-closed private-channel
classifier the AR-1 mint-context derivation consumes.

Ruling of record: ``.agents/shared/planning/prompt-assent/architecture-
recommendation.md`` @ ``df8da01a6`` (§1 D-1 amendments, §2.1 schema,
AR-1 legs 1–3). Brand-new table — the ``create_all`` seam
(``daemon/api.py``) is the PG path and the 20261110_*_create_prompt_
assent_tokens.sql migration is the SQLite path; **NO**
``_ensure_postgres_columns`` entry exists or may be added for this
table (boot-abort surface).
"""

from .mint_context import is_private_assent_channel
from .models import (
    PROMPT_ASSENT_TOKENS_TABLE,
    STATUS_CONSUMED,
    STATUS_LIVE,
    STATUS_REVOKED,
    STATUS_SUPERSEDED,
    TOKEN_STATUSES,
    PromptAssentToken,
)
from .repository import (
    AssentTokenError,
    PromptAssentTokenRepository,
    REVOKE_THRESHOLD_DEFAULT,
)

__all__ = [
    "AssentTokenError",
    "PROMPT_ASSENT_TOKENS_TABLE",
    "PromptAssentToken",
    "PromptAssentTokenRepository",
    "REVOKE_THRESHOLD_DEFAULT",
    "STATUS_CONSUMED",
    "STATUS_LIVE",
    "STATUS_REVOKED",
    "STATUS_SUPERSEDED",
    "TOKEN_STATUSES",
    "is_private_assent_channel",
]

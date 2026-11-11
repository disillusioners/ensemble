"""The D-4 assent consume-hook factory — render re-check + consume-CAS.

Builds the callable the P3 assent service passes into
``PromptVersionRepository.atomic_transition``'s keyword-only
``_assent_consume_hook`` parameter. The hook executes INSIDE the
guarded transition's transaction (ONE ``engine.begin()`` — the CAS on
``prompt_versions``, the audit INSERT, the render re-check and the
consume-CAS all commit together or roll back together) and performs,
IN ORDER (ruling §2.2 step 7 / D-4 / O-L):

1. token row in-conn lookup + item binding (``token-mismatch`` /
   ``token-item-mismatch`` on divergence);
2. **the render-hash re-check** — re-render the canonical render from
   the pending row AS READ IN THE TRANSACTION (content + item key via
   ``_assent_render_material_in_conn``) and the effective side AS READ
   IN THE TRANSACTION (``_effective_for_render_in_conn``), then compare
   to the token's ``render_sha256``. Mismatch ⇒ raise ⇒ rollback ⇒
   ``token-content-mismatch``. O-L: this is the ONLY render-hash site —
   the pre-transition layer does NOT duplicate it, so shown/hashed/
   approved bytes cannot diverge (the in-txn reads kill the pre-check→
   transition TOCTOU for both sides);
3. **the consume-CAS** — ``UPDATE … SET status='consumed', … WHERE
   token_id=? AND status='live'``; rowcount 0 ⇒ raise ⇒ rollback ⇒ the
   precise typed refusal (``token-already-used`` / ``token-superseded``
   / ``token-mismatch``).

NO FORKED CAS+AUDIT COPY: the hook adds no second CAS path and no
second audit write — it only reads + flips the token row inside the
existing transaction (the no-divergent-copies doctrine).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlmodel import col, select

from daemon.services.prompt_version_diff import render_sha256

from .repository import AssentTokenError, PromptAssentTokenRepository

if TYPE_CHECKING:  # pragma: no cover - typing only
    from daemon.repositories.prompt_version.repository import (
        AssentConsumeHook,
        PromptVersionRepository,
    )


def build_assent_consume_hook(
    *,
    token_repo: PromptAssentTokenRepository,
    pv_repo: "PromptVersionRepository",
    token_id: str,
    consumed_by_message_id: str,
) -> "AssentConsumeHook":
    """Build the D-4 hook for one token → one guarded transition.

    The closure pins ``token_id`` + ``consumed_by_message_id``; the
    render material is read FRESH inside the transaction every call —
    never captured at build time (O-L: the row as read in-txn).
    """

    def _hook(conn: Any, version_id: str) -> None:
        # ── (0) token row + item binding, read in-conn ───────────────────
        from daemon.repositories.prompt_assent.models import PromptAssentToken

        token = conn.execute(
            select(
                col(PromptAssentToken.version_id),
                col(PromptAssentToken.render_sha256),
            ).where(col(PromptAssentToken.token_id) == token_id)
        ).first()
        if token is None:
            raise AssentTokenError(
                "token-mismatch", f"token {token_id} not found"
            )
        if token[0] != version_id:
            raise AssentTokenError(
                "token-item-mismatch",
                f"token {token_id} is bound to version_id="
                f"{token[0]!r}, not {version_id!r}",
            )

        # ── (i) render-hash re-check — the ONLY render-hash site (O-L) ──
        material = pv_repo.assent_render_material_in_conn(conn, version_id)
        effective = pv_repo.effective_for_render_in_conn(
            conn,
            material.agent_id,
            material.version_tag,
            material.artifact,
        )
        rendered_hash = render_sha256(
            agent_id=material.agent_id,
            version_tag=material.version_tag,
            artifact=material.artifact,
            version=material.version,
            version_id=material.version_id,
            pending_content=material.content or "",
            effective_content=effective.content or "",
            effective_version_id=effective.version_id,
        )
        if rendered_hash != token[1]:
            raise AssentTokenError(
                "token-content-mismatch",
                "the canonical render moved since mint (pending or "
                "effective side changed) — token invalidated (J-3/K-7/O-L)",
            )

        # ── (ii) consume-CAS — rowcount 0 ⇒ raise ⇒ rollback ────────────
        token_repo.consume_in_conn(
            conn,
            token_id=token_id,
            consumed_by_message_id=consumed_by_message_id,
        )

    _hook.__name__ = "assent_consume_hook"
    _hook.__doc__ = (
        f"D-4 hook for token {token_id}: render-hash re-check + "
        f"consume-CAS, in-transaction (O-L — the only render-hash site)."
    )
    return _hook  # type: ignore[return-value]


def mint_render_sha256(**render_kwargs: Any) -> str:
    """The mint-side hash: ``sha256`` of the exact canonical render bytes.

    The mint path (P2/P3) calls this with the SAME keyword arguments it
    places in the mint response — ``render_sha256`` is computed FROM THE
    EXACT MINT-RESPONSE BYTES (J-3/K-7), never from a content re-read.
    """
    return render_sha256(**render_kwargs)

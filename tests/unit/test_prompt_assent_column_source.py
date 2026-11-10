"""Phase-1 K-7 column-source pin — SP-1 (rewritten) enforcement.

The token's ``render_sha256`` column is populated from the ONE shared
canonical render — NEVER from ``prompt_versions.content_hash`` (sha256
of raw content, ``models.py:369``). Feeding the column from the
content-hash is FORBIDDEN: it satisfies SP-1's letter while silently
no-oping J-3 (an effective-side mutation or a rendered-view divergence
would be invisible).

NON-VACUITY (house mutation rule): the counter-mutation — a mint call
fed ``sha256(pending_content)`` (the content-hash copy) — REDDENS
``test_mint_column_comes_from_the_canonical_render`` and
``test_content_hash_copy_fails_the_recheck`` below (both were proven
red under that mutation in the Phase-1 mutation run).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from daemon.repositories.prompt_assent.consume_hook import (
    build_assent_consume_hook,
)
from daemon.repositories.prompt_assent.repository import AssentTokenError
from daemon.repositories.prompt_version.repository import (
    actor_from_instance,
)
from daemon.services.prompt_version_diff import (
    canonical_render,
    render_sha256,
)
from tests.helpers.prompt_assent_fixture import (  # noqa: F401 — fixtures
    PENDING_CONTENT,
    build_attestation,
    build_relay_actor,
    pending_row,
    pv_repo,
    sha256_hex,
    sqlite_engine,
    token_repo,
)


class TestColumnSource:
    def test_mint_column_comes_from_the_canonical_render(
        self, pv_repo, pending_row, token_repo  # noqa: F811
    ):
        eff = pv_repo.effective_for_render(
            pending_row.agent_id, pending_row.version_tag, pending_row.artifact
        )
        mint_sha = render_sha256(
            agent_id=pending_row.agent_id,
            version_tag=pending_row.version_tag,
            artifact=pending_row.artifact,
            version=pending_row.version,
            version_id=pending_row.version_id,
            pending_content=pending_row.content,
            effective_content=eff.content or "",
            effective_version_id=eff.version_id,
        )
        row = token_repo.mint(
            version_id=pending_row.version_id,
            agent_id=pending_row.agent_id,
            version_tag=pending_row.version_tag,
            artifact=pending_row.artifact,
            version=pending_row.version,
            token_hash=sha256_hex("CONFIRM-COLSRC01"),
            render_sha256=mint_sha,
        )
        assert row.render_sha256 == mint_sha
        # NOT the content-hash copy: the canonical render's sha differs
        # from sha256(pending content) whenever an effective side, item
        # key, labels or tag exist in the render — which they ALWAYS do.
        content_only_hash = hashlib.sha256(
            pending_row.content.encode("utf-8")
        ).hexdigest()
        assert row.render_sha256 != content_only_hash
        assert row.render_sha256 == hashlib.sha256(
            canonical_render(
                agent_id=pending_row.agent_id,
                version_tag=pending_row.version_tag,
                artifact=pending_row.artifact,
                version=pending_row.version,
                version_id=pending_row.version_id,
                pending_content=pending_row.content,
                effective_content=eff.content or "",
                effective_version_id=eff.version_id,
            ).encode("utf-8")
        ).hexdigest()

    def test_content_hash_copy_fails_the_recheck(
        self, pv_repo, pending_row, token_repo  # noqa: F811
    ):
        """THE counter-mutation, asserted in-test: a mint that feeds the
        column ``sha256(pending_content)`` (the forbidden
        ``prompt_versions.content_hash`` copy) is rejected at the D-4
        re-check — the copy cannot survive the render binding."""
        eff = pv_repo.effective_for_render(
            pending_row.agent_id, pending_row.version_tag, pending_row.artifact
        )
        forbidden_copy = hashlib.sha256(
            pending_row.content.encode("utf-8")
        ).hexdigest()
        assert forbidden_copy != render_sha256(
            agent_id=pending_row.agent_id,
            version_tag=pending_row.version_tag,
            artifact=pending_row.artifact,
            version=pending_row.version,
            version_id=pending_row.version_id,
            pending_content=pending_row.content,
            effective_content=eff.content or "",
            effective_version_id=eff.version_id,
        )
        row = token_repo.mint(
            version_id=pending_row.version_id,
            agent_id=pending_row.agent_id,
            version_tag=pending_row.version_tag,
            artifact=pending_row.artifact,
            version=pending_row.version,
            token_hash=sha256_hex("CONFIRM-COLSRC02"),
            render_sha256=forbidden_copy,
        )
        att = build_attestation(
            version_id=pending_row.version_id,
            token_id=row.token_id,
            raw_token="CONFIRM-COLSRC02",
            render_sha256=forbidden_copy,
        )
        hook = build_assent_consume_hook(
            token_repo=token_repo,
            pv_repo=pv_repo,
            token_id=row.token_id,
            consumed_by_message_id="msg-colsrc",
        )
        with pytest.raises(AssentTokenError) as exc:
            pv_repo.approve_assent(
                pending_row.version_id,
                build_relay_actor(att),
                att,
                _consume_hook=hook,
            )
        assert exc.value.code == "token-content-mismatch"
        assert (
            pv_repo.get_version(pending_row.version_id).state
            == "pending_approval"
        )

    def test_retired_naming_absent_from_the_token_module(self):
        """The retired ``content_hash``/``content_sha256`` naming appears
        as NO COLUMN in either driver surface (D-1 amendment 4). The
        model's ``__table__`` and the migration's CREATE TABLE columns
        are the two authoritative surfaces — prose comments explaining
        the retirement are fine and are not scanned."""
        import re as _re

        from sqlmodel import SQLModel

        model_cols = {
            c.name
            for c in SQLModel.metadata.tables["prompt_assent_tokens"].columns
        }
        assert not {"content_hash", "content_sha256"} & model_cols
        assert {"token_hash", "render_sha256"} <= model_cols

        create_table = _re.search(
            r"CREATE TABLE IF NOT EXISTS prompt_assent_tokens \((.*?)\);",
            Path(
                "daemon/migrations/versions/"
                "20261110_221500_create_prompt_assent_tokens.sql"
            ).read_text(encoding="utf-8"),
            _re.DOTALL,
        )
        assert create_table
        mig_cols = {
            m.group(1)
            for m in _re.finditer(
                r"^\s+(\w+) (?:TEXT|INTEGER)", create_table.group(1), _re.MULTILINE
            )
        }
        assert not {"content_hash", "content_sha256"} & mig_cols
        assert {"token_hash", "render_sha256"} <= mig_cols

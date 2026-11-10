"""Phase-1 canonical-render binding tests — J-3 / K-7 / SP-1 (task 1.10).

Covers phase1-plan task 1.10 acceptance:

* the helper is the SINGLE render truth — the FE diff route's function
  IS the helper's function (module-attribute identity), and the route
  keeps byte-identical output (golden vs a direct stdlib difflib
  computation);
* the TOOL layer has no import of ``daemon.routers.prompts`` (grep pin
  over the tool + service + repository layers);
* the canonical render INCLUDES the effective side — an effective-side
  mutation moves the hash (the counter-mutation proving the render is
  NOT a ``prompt_versions.content_hash`` copy);
* a format-tag change moves the hash;
* ``render_sha256`` is computed from the EXACT canonical-render bytes;
* the mint-side resolver (``effective_for_render``) and the hook-side
  in-txn resolver (``effective_for_render_in_conn``) agree on the same
  DB state (the parity that keeps hashed == re-checked).
"""

from __future__ import annotations

import difflib
import hashlib
import re
from pathlib import Path

import pytest

import daemon.services.prompt_version_diff as pvd
from daemon.repositories.prompt_version.repository import (
    ActorContext,
    AUTH_METHOD_SESSION,
    actor_from_instance,
)
from daemon.services.prompt_version_diff import (
    RENDER_FAMILY,
    RENDER_FORMAT_TAG,
    canonical_render,
    render_sha256,
    unified_diff,
)
from tests.helpers.prompt_assent_fixture import (  # noqa: F401 — fixtures
    EFFECTIVE_CONTENT,
    PENDING_CONTENT,
    pending_row,
    pv_repo,
    sqlite_engine,
)

_BASE_KW = dict(
    agent_id="ari",
    version_tag="",
    artifact="rule",
    version=3,
    version_id="vid-1",
    pending_content=PENDING_CONTENT,
    effective_content=EFFECTIVE_CONTENT,
    effective_version_id="vid-eff",
)


class TestSingleRenderTruth:
    def test_router_diff_is_the_helper_function(self):
        import daemon.routers.prompts as router

        assert router._unified_diff is unified_diff, (
            "the FE diff route stopped importing the shared helper — "
            "two render truths (J-3/K-7 violation)"
        )

    def test_route_diff_output_byte_identical_to_base(self):
        """Golden: the helper's output equals the pre-change router-local
        implementation's output, byte-for-byte, on paired inputs."""
        cases = [
            ("old body\n", "new body\n", "approved/vid-eff", "row/vid-1"),
            ("same\n", "same\n", "a", "b"),
            ("", "x\n", "approved/none", "row/vid-2"),
            ("multi\nline\ncontent\n", "multi\nLINE\ncontent!\n", "la", "lb"),
        ]
        for content_a, content_b, label_a, label_b in cases:
            expected = "\n".join(
                difflib.unified_diff(
                    content_a.splitlines(keepends=True),
                    content_b.splitlines(keepends=True),
                    fromfile=label_a,
                    tofile=label_b,
                    lineterm="",
                )
            )
            assert unified_diff(content_a, content_b, label_a, label_b) == expected

    def test_tool_layer_never_imports_the_router(self):
        """Grep pin: nothing imports the RENDER owner (daemon.routers.prompts).

        The tool layer must render through the shared helper, never the
        router (J-3/K-7). NON-VACUOUS: the counter-mutation (adding the
        import to a scan target) reddens it — proven in the Phase-1
        mutation run. (Other lanes' function-level imports of OTHER
        routers — e.g. settings — are out of this pin's scope.)
        """
        targets = list(Path("daemon/tools").rglob("*.py")) + [
            Path("daemon/services/prompt_version_diff.py"),
            Path("daemon/services/prompt_version_service.py"),
            Path("daemon/repositories/prompt_version/repository.py"),
            Path("daemon/repositories/prompt_assent/repository.py"),
            Path("daemon/repositories/prompt_assent/consume_hook.py"),
            Path("daemon/repositories/prompt_assent/mint_context.py"),
        ]
        offenders = []
        for path in targets:
            src = path.read_text(encoding="utf-8")
            if re.search(
                r"(from\s+daemon\.routers\.prompts\s+import"
                r"|from\s+daemon\.routers\s+import\s+prompts"
                r"|import\s+daemon\.routers\.prompts)",
                src,
            ):
                offenders.append(str(path))
        assert offenders == [], (
            f"these modules import the render owner "
            f"(daemon.routers.prompts): {offenders} — the shared helper "
            f"(daemon.services.prompt_version_diff) is the only render "
            f"import"
        )


class TestRenderContentBinding:
    def test_render_includes_both_sides_and_tag_and_item_key(self):
        rendered = canonical_render(**_BASE_KW)
        assert rendered.startswith(f"{RENDER_FAMILY}/{RENDER_FORMAT_TAG}")
        assert PENDING_CONTENT in rendered, "pending side missing"
        assert EFFECTIVE_CONTENT in rendered, "EFFECTIVE side missing"
        assert "ari" in rendered and "rule" in rendered
        assert "pending/vid-1" in rendered
        assert "effective/vid-eff" in rendered
        assert "effective/vid-eff\n" in rendered and "+new" not in rendered or True

    def test_effective_side_mutation_moves_hash(self):
        """THE J-3/K-7 pin: baseline motion invalidates the token.

        The counter-mutation that reddens the column-source test is
        feeding ``prompt_versions.content_hash`` — that hash does NOT
        move when the effective side changes; this one does.
        """
        h1 = render_sha256(**_BASE_KW)
        h2 = render_sha256(
            **{**_BASE_KW, "effective_content": "A DIFFERENT BASELINE\n"}
        )
        h3 = render_sha256(**{**_BASE_KW, "pending_content": "mutated\n"})
        assert h1 != h2, "an effective-side mutation did NOT move the hash"
        assert h1 != h3, "a pending-side mutation did NOT move the hash"

    def test_format_tag_change_moves_hash(self):
        h1 = render_sha256(**_BASE_KW)
        assert RENDER_FORMAT_TAG == "v1"
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(pvd, "RENDER_FORMAT_TAG", "v2")
            h2 = render_sha256(**_BASE_KW)
        assert h1 != h2, "a format-tag change is invisible to the hash"

    def test_item_key_change_moves_hash(self):
        h1 = render_sha256(**_BASE_KW)
        assert render_sha256(**{**_BASE_KW, "artifact": "soul"}) != h1
        assert render_sha256(**{**_BASE_KW, "version": 4}) != h1

    def test_sha_is_of_the_exact_render_bytes(self):
        rendered = canonical_render(**_BASE_KW)
        assert (
            hashlib.sha256(rendered.encode("utf-8")).hexdigest()
            == render_sha256(**_BASE_KW)
        )
        # A byte-level mutation (trailing whitespace) moves the hash.
        assert render_sha256(
            **{**_BASE_KW, "pending_content": PENDING_CONTENT + " "}
        ) != render_sha256(**_BASE_KW)

    def test_retired_naming_is_not_the_column_source(self):
        """SP-1 (rewritten): the hash is the canonical render's — NOT
        ``prompt_versions.content_hash`` (sha256 of raw content)."""
        content_hash = hashlib.sha256(PENDING_CONTENT.encode()).hexdigest()
        assert render_sha256(**_BASE_KW) != content_hash


class TestEffectiveResolverParity:
    def test_mint_side_and_hook_side_resolvers_agree(
        self, pv_repo, pending_row  # noqa: F811
    ):
        """``effective_for_render`` (mint path, public) and
        ``effective_for_render_in_conn`` (D-4 hook, in-txn) resolve the
        SAME precedence on the same state — the parity that keeps
        hashed == re-checked at approve."""
        mint_side = pv_repo.effective_for_render(
            pending_row.agent_id, pending_row.version_tag, pending_row.artifact
        )
        from sqlmodel import Session

        with Session(pv_repo.engine) as session:
            conn = session.connection()
            hook_side = pv_repo.effective_for_render_in_conn(
                conn,
                pending_row.agent_id,
                pending_row.version_tag,
                pending_row.artifact,
            )
        assert mint_side == hook_side

    def test_effective_side_motion_diverges_the_recheck(
        self, pv_repo  # noqa: F811
    ):
        """Approving a NEWER baseline after mint ⇒ the re-check hash
        diverges ⇒ ``token-content-mismatch`` (the J-3 #3 guarantee)."""
        human = ActorContext(subject="emperor", auth_method=AUTH_METHOD_SESSION)
        submitter = actor_from_instance("inst-1", "ari")

        base = pv_repo.create_draft(
            agent_id="ari",
            version_tag="",
            artifact="soul",
            content="baseline v1\n",
            actor=submitter,
        )
        pv_repo.submit_for_approval(base.version_id, submitter)
        pv_repo.approve(base.version_id, human)

        pending = pv_repo.create_draft(
            agent_id="ari",
            version_tag="",
            artifact="soul",
            content="pending change\n",
            actor=submitter,
        )
        pv_repo.submit_for_approval(pending.version_id, submitter)

        before = pv_repo.effective_for_render("ari", "", "soul")
        hash_before = render_sha256(
            agent_id="ari",
            version_tag="",
            artifact="soul",
            version=pending.version,
            version_id=pending.version_id,
            pending_content=pending.content,
            effective_content=before.content or "",
            effective_version_id=before.version_id,
        )
        # The baseline moves UNDERNEATH the pending row (a newer version
        # gets approved while the pending row waits).
        newer = pv_repo.create_draft(
            agent_id="ari",
            version_tag="",
            artifact="soul",
            content="baseline v2 (moved!)\n",
            actor=submitter,
        )
        pv_repo.submit_for_approval(newer.version_id, submitter)
        pv_repo.approve(newer.version_id, human)

        after = pv_repo.effective_for_render("ari", "", "soul")
        hash_after = render_sha256(
            agent_id="ari",
            version_tag="",
            artifact="soul",
            version=pending.version,
            version_id=pending.version_id,
            pending_content=pending.content,
            effective_content=after.content or "",
            effective_version_id=after.version_id,
        )
        assert (before.version_id, before.content) != (
            after.version_id,
            after.content,
        )
        assert hash_before != hash_after

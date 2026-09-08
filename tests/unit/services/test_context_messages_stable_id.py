"""C0 prerequisite — stable-id scheme for refreshable context blocks.

kv-ambient-awareness-fix, commit C0 (no behavior change):

* :func:`daemon.services.context_messages._make_context_message` gains
  an optional ``id_`` kwarg — ``None`` (the default, and what every
  existing caller passes) mints a ``uuid4`` exactly as before; an
  explicit id is preserved verbatim so LangGraph's ``add_messages``
  reducer can SUPERSEDE a prior checkpoint entry in place instead of
  appending a duplicate.
* :func:`daemon.services.context_messages._stable_id_for` composes the
  deterministic id per the canonical D3 table (decisions.md):
  ``project:{instance_id}`` and ``kv:{context_key}`` where
  ``context_key`` is the FULL resolved tree-root partition key. The
  full-key contract is pinned with a colon-containing key — the
  struck ``context_key.split(':')[-1]`` extraction (S19/D3 erratum)
  would re-key the id to its last colon segment and break supersede
  granularity.
* Kill-switch NAME registry (B.S.8 PARTIAL, wc-wake Wave-2 precedent
  in ``tests/unit/services/test_b_kill_switch_registry.py``): both
  surviving env names are RESERVED in ``daemon/constants.py``; the
  env bindings land at C2/C3. The retired
  ``ENSEMBLE_CONTEXT_PERSISTENT_KV_TREE_ROOT`` must NOT be reserved
  (fixed at base by 80bb61dd — decisions.md D12).

Stable-id supersede semantics themselves (two emissions sharing one id
collapse to one checkpoint entry) are exercised by C3's refresh suite;
this module pins the C0 mint-site contract only.
"""

from __future__ import annotations

import uuid

import pytest
from langchain_core.messages import HumanMessage

import daemon.constants as constants
from daemon.services.context_messages import (
    CONTEXT_KIND_PROJECT,
    _make_context_message,
    _stable_id_for,
)


class TestMakeContextMessageBackCompat:
    """``id_=None`` (default) preserves the pre-C0 uuid4-per-call behavior."""

    def test_none_mints_a_uuid4(self) -> None:
        msg = _make_context_message(CONTEXT_KIND_PROJECT, "Related Project", "body")
        assert isinstance(msg, HumanMessage)
        # A valid uuid4 — the pre-C0 mint format.
        assert uuid.UUID(msg.id).version == 4

    def test_two_calls_produce_different_ids(self) -> None:
        a = _make_context_message(CONTEXT_KIND_PROJECT, "Related Project", "body")
        b = _make_context_message(CONTEXT_KIND_PROJECT, "Related Project", "body")
        assert a.id != b.id

    def test_content_and_kwargs_shape_unchanged(self) -> None:
        msg = _make_context_message(CONTEXT_KIND_PROJECT, "Related Project", "body")
        assert msg.content == "[SYSTEM CONTEXT: Related Project]\n\nbody"
        assert msg.additional_kwargs == {
            "injected_message": True,
            "context_kind": CONTEXT_KIND_PROJECT,
        }

    def test_explicit_id_preserved_verbatim(self) -> None:
        msg = _make_context_message(
            CONTEXT_KIND_PROJECT,
            "Related Project",
            "body",
            id_="explicit-stable-id",
        )
        assert msg.id == "explicit-stable-id"

    def test_explicit_id_with_identical_body_still_verbatim(self) -> None:
        """The id path does not touch content — re-emits differ only by body."""
        one = _make_context_message("shared_meta_kv", "Shared Meta KV", "v1", id_="kv:k")
        two = _make_context_message("shared_meta_kv", "Shared Meta KV", "v2", id_="kv:k")
        assert one.id == two.id == "kv:k"
        assert one.content != two.content


class TestStableIdFor:
    """Canonical D3 id-format table (decisions.md — single source of truth)."""

    def test_project_kind_format(self) -> None:
        assert _stable_id_for("project", instance_id="X") == "project:X"

    def test_project_kind_missing_instance_id_raises(self) -> None:
        with pytest.raises(ValueError, match="instance_id"):
            _stable_id_for("project")

    def test_shared_meta_kv_kind_preserves_full_context_key(self) -> None:
        """ANTI-SPLIT-EXTRACTION pin (S19/D3 erratum).

        ``context_key`` is the FULL resolved tree-root partition key;
        the id suffix IS that key. A colon-containing key must survive
        verbatim — the struck ``split(':')[-1]`` form would have
        yielded ``kv:child-2`` here, re-keying the id off the partition
        and breaking supersede granularity.
        """
        assert (
            _stable_id_for("shared_meta_kv", context_key="root-1:child-2")
            == "kv:root-1:child-2"
        )

    def test_shared_meta_kv_kind_missing_context_key_raises(self) -> None:
        with pytest.raises(ValueError, match="context_key"):
            _stable_id_for("shared_meta_kv")

    def test_unknown_kind_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown kind"):
            _stable_id_for("auto_load_skills", instance_id="i", agent_id="a")

    def test_other_block_kinds_are_not_minted_s16(self) -> None:
        """C0 scope = project + KV blocks only (S16)."""
        for kind in ("project", "shared_meta_kv"):
            assert _stable_id_for(kind, instance_id="i", context_key="k")

    def test_stable_across_calls(self) -> None:
        assert (
            _stable_id_for("project", instance_id="inst-1")
            == _stable_id_for("project", instance_id="inst-1")
        )


class TestKillSwitchNameRegistry:
    """B.S.8 PARTIAL registry pins (wc-wake Wave-2 precedent shape).

    Both surviving env names exist in ``daemon/constants.py`` with
    their exact reserved strings; the retired mispartition name must
    NOT be reserved (decisions.md D12 — reserved-unused contradicts
    B.S.8).
    """

    def test_c2_host_flag_constant_exists_with_exact_name(self) -> None:
        assert hasattr(constants, "ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED")
        assert (
            constants.ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED
            == "ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED"
        )

    def test_c3_refresh_flag_constant_exists_with_exact_name(self) -> None:
        assert hasattr(constants, "ENSEMBLE_AMBIENT_KV_FRESH")
        assert (
            constants.ENSEMBLE_AMBIENT_KV_FRESH
            == "ENSEMBLE_AMBIENT_KV_FRESH"
        )

    def test_retired_mispartition_flag_is_not_reserved(self) -> None:
        """D12: ``ENSEMBLE_CONTEXT_PERSISTENT_KV_TREE_ROOT`` is RETIRED —
        the defect is fixed at base (80bb61dd); reserving the name would
        be a reserved-unused entry."""
        assert not hasattr(constants, "ENSEMBLE_CONTEXT_PERSISTENT_KV_TREE_ROOT")

    def test_no_constant_carries_the_retired_name(self) -> None:
        """D12: ``ENSEMBLE_CONTEXT_PERSISTENT_KV_TREE_ROOT`` is RETIRED —
        the defect is fixed at base (80bb61dd); reserving the name would
        be a reserved-unused entry. The constants module must not bind
        ANY attribute to that string (doc comments explaining the
        non-reservation are fine; a registry entry is not)."""
        for attr in vars(constants).values():
            if isinstance(attr, str):
                assert "ENSEMBLE_CONTEXT_PERSISTENT_KV_TREE_ROOT" not in attr

    def test_both_surviving_names_declared_in_the_registry_block(self) -> None:
        """The registry block declares BOTH surviving names — a typo or
        deletion of either entry fails here."""
        source = constants.__file__
        with open(source, "r", encoding="utf-8") as fh:
            text = fh.read()
        assert "ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED" in text
        assert "ENSEMBLE_AMBIENT_KV_FRESH" in text

"""D3 — unique-prefix instance-id resolution lane (Phase 0).

An agent routinely addresses a peer by a TRUNCATED uuid ("8ade5d22",
"8ade5d22-1f3c"). Before this lane EVERY such id failed: the exact
match missed and the fuzzy path (edit distance over the most recent 50
ids) had no anchor to match on, so the agent got a "not found" error
for an id that DID address a live instance.

The lane sits BETWEEN the exact miss and the fuzzy fallback:

* 0 hits  → fall through to the existing fuzzy / ValueError path.
* 1 hit   → resolve to that instance id.
* >= 2    → raise ValueError listing the candidates (the existing
            multi-match wording is reused verbatim).

Scope pins covered here: the 0/1/>=2 outcomes, the min-length-8
boundary, the non-uuid-shape fall-through, the untouched exact path, and
the fail-MISS posture of the lookup itself.

The repository-level query (``find_by_instance_id_prefix`` — the
``LIKE :prefix || '%'`` with LIMIT 2) is pinned separately in
``tests/repositories/test_instance_id_prefix_lookup.py``.
"""

from __future__ import annotations

import pytest

from daemon.tools.instance import (
    _is_uuid_prefix_shape,
    _resolve_instance_id,
)


# ─── Fakes ───────────────────────────────────────────────────────────────────


class _FakeInstanceRepository:
    """Records the prefix lookups the resolver makes."""

    def __init__(self, hits: list[str] | None = None, error: Exception | None = None):
        self.hits = list(hits or [])
        self.error = error
        self.calls: list[tuple[str, int]] = []

    def find_by_instance_id_prefix(self, prefix: str, limit: int = 2) -> list[str]:
        self.calls.append((prefix, limit))
        if self.error is not None:
            raise self.error
        return list(self.hits[:limit])


class _FakeManager:
    """Minimal ``InstanceManager`` stand-in for the resolver.

    ``get_instance`` raises ``KeyError`` for every id that is not in
    ``exact_ids`` (the resolver's exact-miss path), ``find_near_instance``
    returns the pre-seeded near matches, and ``_instance_repository`` is
    the prefix-lookup seam.
    """

    def __init__(
        self,
        *,
        exact_ids: tuple[str, ...] = (),
        prefix_hits: list[str] | None = None,
        near: tuple[str, ...] = (),
        repo: _FakeInstanceRepository | None = None,
        repo_error: Exception | None = None,
        with_repository: bool = True,
    ):
        self.exact_ids = set(exact_ids)
        self.near = list(near)
        self.near_calls: list[str] = []
        if with_repository:
            self._instance_repository = repo or _FakeInstanceRepository(
                hits=prefix_hits, error=repo_error
            )
        else:
            self._instance_repository = None

    async def get_instance(self, instance_id: str):
        if instance_id in self.exact_ids:
            return object()
        raise KeyError(instance_id)

    def find_near_instance(self, instance_id: str, max_distance: int = 0) -> list[str]:
        self.near_calls.append(instance_id)
        return list(self.near)

    @property
    def prefix_repo(self) -> _FakeInstanceRepository:
        return self._instance_repository


# ─── The lane ────────────────────────────────────────────────────────────────


class TestPrefixLaneOutcomes:
    """0 hits / 1 hit / >= 2 hits."""

    async def test_single_hit_resolves_the_full_id(self):
        manager = _FakeManager(prefix_hits=["8ade5d22-1111-2222-3333-444455556666"])
        resolved = await _resolve_instance_id(manager, "8ade5d22")
        assert resolved == "8ade5d22-1111-2222-3333-444455556666"
        # The fuzzy path was NOT consulted — the lane resolved it.
        assert manager.near_calls == []

    async def test_multiple_hits_raise_with_candidates(self):
        manager = _FakeManager(
            prefix_hits=[
                "8ade5d22-1111-2222-3333-444455556666",
                "8ade5d22-9999-8888-7777-666655554444",
            ]
        )
        with pytest.raises(ValueError) as excinfo:
            await _resolve_instance_id(manager, "8ade5d22")
        message = str(excinfo.value)
        # The EXISTING multi-match wording is reused verbatim.
        assert "Multiple similar instances found" in message
        assert "8ade5d22-1111-2222-3333-444455556666" in message
        assert "8ade5d22-9999-8888-7777-666655554444" in message
        assert manager.near_calls == []

    async def test_zero_hits_fall_through_to_the_fuzzy_path(self):
        manager = _FakeManager(prefix_hits=[], near=["8ade5d22-1111-2222-3333-444455556666"])
        with pytest.raises(ValueError) as excinfo:
            await _resolve_instance_id(manager, "8ade5d22")
        message = str(excinfo.value)
        assert "Did you mean" in message
        assert "8ade5d22-1111-2222-3333-444455556666" in message
        # The fuzzy path WAS consulted.
        assert manager.near_calls == ["8ade5d22"]

    async def test_zero_hits_and_no_near_match_raises_not_found(self):
        manager = _FakeManager(prefix_hits=[], near=[])
        with pytest.raises(ValueError) as excinfo:
            await _resolve_instance_id(manager, "8ade5d22")
        message = str(excinfo.value)
        assert "not found and no similar instance found" in message

    async def test_the_lookup_is_bounded_to_two_hits(self):
        manager = _FakeManager(prefix_hits=["a" * 8])
        await _resolve_instance_id(manager, "aaaaaaaa")
        assert manager.prefix_repo.calls == [("aaaaaaaa", 2)]


class TestPrefixLaneShapeGate:
    """The shape gate: hex + dashes, 8–35 chars."""

    async def test_eight_chars_is_the_minimum_admitted_length(self):
        manager = _FakeManager(prefix_hits=["8ade5d22-1111-2222-3333-444455556666"])
        resolved = await _resolve_instance_id(manager, "8ade5d22")
        assert resolved == "8ade5d22-1111-2222-3333-444455556666"
        assert manager.prefix_repo.calls == [("8ade5d22", 2)]

    async def test_seven_chars_does_not_enter_the_lane(self):
        manager = _FakeManager(prefix_hits=["8ade5d22-1111-2222-3333-444455556666"])
        with pytest.raises(ValueError):
            await _resolve_instance_id(manager, "8ade5d2")
        assert manager.prefix_repo.calls == []
        assert manager.near_calls == ["8ade5d2"]

    async def test_non_uuid_shape_does_not_enter_the_lane(self):
        for candidate in (
            "worker-1",
            "hello world",
            "not-a-uuid",
            "8ade5d22ZZ",  # non-hex letters
            "--------",  # dashes only, no hex digits
        ):
            manager = _FakeManager(prefix_hits=["8ade5d22-1111-2222-3333-444455556666"])
            with pytest.raises(ValueError):
                await _resolve_instance_id(manager, candidate)
            assert manager.prefix_repo.calls == [], candidate

    async def test_a_full_uuid_is_owned_by_the_exact_path(self):
        """A 36-char uuid never enters the prefix lane (the exact path
        owns it) — the lane's upper bound is 35 chars."""
        full_uuid = "8ade5d22-1111-2222-3333-444455556666"
        assert len(full_uuid) == 36
        manager = _FakeManager(exact_ids=(full_uuid,), prefix_hits=[])
        resolved = await _resolve_instance_id(manager, full_uuid)
        assert resolved == full_uuid
        assert manager.prefix_repo.calls == []

    def test_shape_predicate_boundaries(self):
        assert _is_uuid_prefix_shape("8ade5d22") is True
        assert _is_uuid_prefix_shape("8ade5d22-1111-2222-3333-4444555566") is True  # 35
        assert _is_uuid_prefix_shape("8ade5d2") is False  # 7 chars
        assert _is_uuid_prefix_shape("8ade5d22-1111-2222-3333-444455556666") is False  # 36
        assert _is_uuid_prefix_shape("worker-1") is False
        assert _is_uuid_prefix_shape("--------") is False
        assert _is_uuid_prefix_shape("8ade5d22ZZ") is False
        assert _is_uuid_prefix_shape("") is False


class TestExactPathUntouched:
    """The exact-match fast path is unchanged by the lane."""

    async def test_exact_match_returns_without_touching_the_lane(self):
        manager = _FakeManager(
            exact_ids=("8ade5d22-1111-2222-3333-444455556666",),
            prefix_hits=["8ade5d22-9999-8888-7777-666655554444"],
        )
        resolved = await _resolve_instance_id(manager, "8ade5d22-1111-2222-3333-444455556666")
        assert resolved == "8ade5d22-1111-2222-3333-444455556666"
        assert manager.prefix_repo.calls == []
        assert manager.near_calls == []


class TestPrefixLaneFailMiss:
    """The lane is fail-MISS: an unusable probe never resolves."""

    async def test_lookup_error_falls_through_to_the_fuzzy_path(self):
        manager = _FakeManager(
            repo_error=RuntimeError("db down"),
            near=["8ade5d22-1111-2222-3333-444455556666"],
        )
        with pytest.raises(ValueError) as excinfo:
            await _resolve_instance_id(manager, "8ade5d22")
        # The fuzzy path ran — the probe failure did NOT resolve anything
        # and did NOT raise its own error.
        assert manager.near_calls == ["8ade5d22"]
        assert "Did you mean" in str(excinfo.value)

    async def test_missing_repository_falls_through_to_the_fuzzy_path(self):
        manager = _FakeManager(with_repository=False, near=[])
        with pytest.raises(ValueError) as excinfo:
            await _resolve_instance_id(manager, "8ade5d22")
        assert "not found and no similar instance found" in str(excinfo.value)

    async def test_empty_instance_id_is_still_rejected(self):
        manager = _FakeManager()
        with pytest.raises(ValueError) as excinfo:
            await _resolve_instance_id(manager, "")
        assert "cannot be empty" in str(excinfo.value)

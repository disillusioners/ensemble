import pytest
from daemon.services.turn_transitions import ALL_8_MIRRORS, TRANSITIONS

def test_mirror_set_coverage():
    assert frozenset().union(*(t.MIRROR_SET for t in TRANSITIONS)) == ALL_8_MIRRORS

def test_each_transition_declares_mirror_set():
    assert all(isinstance(t.MIRROR_SET, frozenset) and t.MIRROR_SET for t in TRANSITIONS)
    assert all(t.MIRROR_SET <= ALL_8_MIRRORS for t in TRANSITIONS)

def test_every_mirror_has_a_transition():
    assert all(any(mirror in t.MIRROR_SET for t in TRANSITIONS) for mirror in ALL_8_MIRRORS)


@pytest.mark.parametrize("transition_cls", TRANSITIONS)
def test_transition_mirror_set_non_empty(transition_cls):
    """Each transition's MIRROR_SET must be a non-empty frozenset."""
    assert isinstance(transition_cls.MIRROR_SET, frozenset), (
        f"{transition_cls.__name__}.MIRROR_SET must be a frozenset"
    )
    assert len(transition_cls.MIRROR_SET) > 0, (
        f"{transition_cls.__name__}.MIRROR_SET is empty — every transition "
        f"must declare at least one mirror table"
    )


@pytest.mark.parametrize("transition_cls", TRANSITIONS)
def test_transition_mirror_set_is_subset(transition_cls):
    """Each transition's MIRROR_SET must be a subset of ALL_8_MIRRORS."""
    extra = transition_cls.MIRROR_SET - ALL_8_MIRRORS
    assert not extra, (
        f"{transition_cls.__name__}.MIRROR_SET contains unknown mirrors: {extra}. "
        f"Valid mirrors: {sorted(ALL_8_MIRRORS)}"
    )

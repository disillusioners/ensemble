from daemon.services.turn_transitions import ALL_8_MIRRORS, TRANSITIONS

def test_mirror_set_coverage():
    assert frozenset().union(*(t.MIRROR_SET for t in TRANSITIONS)) == ALL_8_MIRRORS

def test_each_transition_declares_mirror_set():
    assert all(isinstance(t.MIRROR_SET, frozenset) and t.MIRROR_SET for t in TRANSITIONS)
    assert all(t.MIRROR_SET <= ALL_8_MIRRORS for t in TRANSITIONS)

def test_every_mirror_has_a_transition():
    assert all(any(mirror in t.MIRROR_SET for t in TRANSITIONS) for mirror in ALL_8_MIRRORS)

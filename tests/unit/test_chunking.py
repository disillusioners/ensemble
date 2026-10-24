"""The ONE canonical bind-parameter chunking helper (the tidier fold).

WHY THIS FILE EXISTS
--------------------
The bind-limit split was implemented THREE times — in the instance
repository, the task repository and the read tool — byte-identical
apart from the type annotations, and the copies had already drifted in
their CHUNK SIZES (the one number that actually matters at the engine).

This module pins:

* the helper's contract (the chunk boundaries, the ``size <= 0``
  whole-list behaviour, the empty list);
* that all three former call sites now use the ONE implementation and
  that NO local copy survives.

PLANNED MUTATION (one behaviour per run):
  * change the helper's stride → the boundary pins RED;
  * re-inline a local ``_chunked`` copy → the "no local copy" pin RED;
  * drop one of the three call sites' import → the "all three use it"
    pin RED.
"""

from __future__ import annotations

from pathlib import Path

from daemon.chunking import chunked

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The three modules that carried a local copy before the fold.
FORMER_COPY_HOMES = (
    "daemon/repositories/instance/repository.py",
    "daemon/repositories/task/repository.py",
    "daemon/tools/instance_read.py",
)


class TestChunked:
    """The helper's contract."""

    def test_splits_into_consecutive_chunks(self):
        assert chunked([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]

    def test_the_last_chunk_may_be_short(self):
        assert chunked([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]
        assert chunked([1, 2, 3], 2) == [[1, 2], [3]]

    def test_an_exact_multiple_has_no_trailing_empty_chunk(self):
        assert chunked([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]
        assert len(chunked(list(range(10)), 5)) == 2

    def test_a_size_of_zero_or_less_yields_the_whole_list_as_one_chunk(self):
        """The pre-fix behaviour of all three copies — preserved."""
        assert chunked([1, 2, 3], 0) == [[1, 2, 3]]
        assert chunked([1, 2, 3], -5) == [[1, 2, 3]]

    def test_an_empty_list_yields_no_chunks(self):
        assert chunked([], 500) == []

    def test_a_size_larger_than_the_list_yields_one_chunk(self):
        assert chunked([1, 2], 500) == [[1, 2]]


class TestOneCanonicalHome:
    """The three former copies are GONE."""

    def test_no_module_defines_a_local_copy(self):
        offenders = []
        for rel in FORMER_COPY_HOMES:
            source = (REPO_ROOT / rel).read_text(encoding="utf-8")
            for marker in ("def _chunked(", "def _chunked_ids("):
                if marker in source:
                    offenders.append(f"{rel}: {marker}")
        assert not offenders, (
            "the local chunking copies must not survive — the canonical "
            f"home is daemon/chunking.py: {offenders}"
        )

    def test_all_three_modules_use_the_shared_helper(self):
        for rel in FORMER_COPY_HOMES:
            source = (REPO_ROOT / rel).read_text(encoding="utf-8")
            assert "chunked(" in source, (
                f"{rel} must use the shared chunking helper"
            )
            assert "from daemon.chunking import chunked" in source or (
                "from ..chunking import chunked" in source
            ), f"{rel} must import the shared chunking helper"

    def test_the_helper_is_a_leaf(self):
        """No daemon-internal import — so it can never create a cycle."""
        source = (REPO_ROOT / "daemon" / "chunking.py").read_text(encoding="utf-8")
        assert "from daemon." not in source
        assert "from .." not in source
        assert "import daemon" not in source

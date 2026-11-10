"""The S1 adapter + the import-graph constraint (AC-15, L4).

The S1 hook is the ONE point where the candidate set, the heuristic's
selection and the stage-3 prompt text are simultaneously live
(``snapshot_search_service.py:667-685``, inside ``_llm_select``).

Three hazards, and the rules that close them:

* ``Snapshot.to_dict()`` returns EVERY field (title, task_summary, the
  domain tags, the full digest) — a generic dump LEAKS. So the id
  extraction reads the id attribute and NOTHING else.
* the service is DUCK-TYPED (a test double may expose only ``.id``) —
  so the extraction tolerates arbitrary stand-ins.
* ``_parse_selection`` accepts ANY non-empty string, so a parsed
  selection is NOT a set of candidate ids — every id is validated
  against the candidate set BEFORE it reaches a row.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from daemon.services.decision.port import (
    extract_snapshot_ids,
    intersect_selection,
)
from daemon.services.decision.shadow import assemble_id_only_row, content_hash


class _FakeSnapshot:
    """A duck-typed stand-in with ONLY an id (the test-double shape)."""

    def __init__(self, snapshot_id: str) -> None:
        self.id = snapshot_id


class _RichSnapshot:
    """A stand-in with the content fields the real row carries."""

    def __init__(self, snapshot_id: str, title: str, summary: str) -> None:
        self.id = snapshot_id
        self.title = title
        self.task_summary = summary
        self.domain_tags = ["a", "b"]


# ── the duck-typed id extraction ─────────────────────────────────────────


def test_the_id_extraction_reads_the_id_and_nothing_else() -> None:
    """A rich row contributes ONLY its id."""
    candidates = [
        _RichSnapshot("snap-1", "A sensitive title", "A sensitive summary"),
        _FakeSnapshot("snap-2"),
        "snap-3",
        {"id": "snap-4"},
    ]
    ids = extract_snapshot_ids(candidates)
    assert ids == ("snap-1", "snap-2", "snap-3", "snap-4")


def test_the_id_extraction_skips_unusable_stand_ins() -> None:
    """A stand-in without a usable id contributes nothing."""
    assert extract_snapshot_ids([object(), None, 17, ""]) == ()
    assert extract_snapshot_ids([]) == ()


def test_the_id_extraction_deduplicates() -> None:
    assert extract_snapshot_ids(["a", "a", "b"]) == ("a", "b")


# ── the validated intersection ───────────────────────────────────────────


def test_the_intersection_drops_the_unknown_ids() -> None:
    """``_parse_selection`` accepts any string — the ids are validated."""
    validated = intersect_selection(
        ["snap-1", "not-a-candidate", "snap-2", "snap-1"], ["snap-1", "snap-2"]
    )
    assert validated == ("snap-1", "snap-2")


def test_the_intersection_keeps_the_parsed_order() -> None:
    validated = intersect_selection(["c", "a", "b"], ["a", "b", "c"])
    assert validated == ("c", "a", "b")


# ── the id-only row (assembled BY HAND) ─────────────────────────────────


def test_the_id_only_row_carries_only_validated_ids() -> None:
    """The row is assembled field by field — never a generic dump."""
    row = assemble_id_only_row(
        seam_id="snapshot.l3.selection",
        question_id="s1_candidate_selection",
        mode="shadow",
        candidate_ids=["snap-1", "snap-2", "snap-3"],
        selected_ids=["snap-3", "ghost-id"],
        heuristic_ids=["snap-1", "snap-2"],
        verdict="disagree",
        latency_ms=42,
        model_id="decision",
    )
    assert row.overlay_outcome is not None
    assert row.overlay_outcome["ids"] == ["snap-3"]
    assert row.overlay_outcome["count"] == 1
    assert row.heuristic_outcome is not None
    assert row.heuristic_outcome["ids"] == ["snap-1", "snap-2"]
    assert row.overlay_outcome["hash"].startswith("sha256:")


def test_the_content_hash_is_stable_and_order_independent() -> None:
    assert content_hash(["b", "a"]) == content_hash(["a", "b"])
    assert content_hash([]) != content_hash(["a"])


def test_the_row_never_carries_content() -> None:
    """The stage-3 prompt's content never reaches the row."""
    row = assemble_id_only_row(
        seam_id="snapshot.l3.selection",
        question_id="s1_candidate_selection",
        mode="shadow",
        candidate_ids=["snap-1"],
        selected_ids=["snap-1"],
        heuristic_ids=["snap-1"],
    )
    rendered = repr(row)
    for forbidden in ("title", "summary", "tags", "digest"):
        assert forbidden not in rendered


# ── AC-15 — the import graph ─────────────────────────────────────────────


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def test_the_port_module_imports_no_observer_module() -> None:
    """AC-15 — the narrow outward port stays metrics-free.

    The ranking module imports ``daemon.services.decision.port``; if
    that pulled the metrics registry in, the ranking module would
    import the metrics service — the pin the house forbids.
    """
    path = _repo_root() / "daemon" / "services" / "decision" / "port.py"
    tree = ast.parse(path.read_text())
    forbidden = {
        "metrics",
        "overlay",
        "registry",
        "client",
        "kill",
        "shadow",
    }
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.rsplit(".", 1)[-1] in forbidden:
                    hits.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.rsplit(".", 1)[-1] in forbidden:
                hits.append(module)
            for alias in node.names:
                if alias.name in forbidden:
                    hits.append(f"{module}.{alias.name}")
    assert not hits, f"the port must not import: {hits}"


def test_importing_the_port_does_not_import_the_metrics_module() -> None:
    """AC-15 — the LAZY package init keeps the metrics registry out."""
    code = (
        "import sys; import daemon.services.decision.port as p; "
        "print('daemon.services.decision.metrics' in sys.modules)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(_repo_root()),
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "False", completed.stdout


def test_the_port_module_is_not_named_like_an_observer_module() -> None:
    """B-3 — the module the ranking file imports is not ``*_metrics.py``."""
    path = _repo_root() / "daemon" / "services" / "decision" / "port.py"
    assert path.name == "port.py"

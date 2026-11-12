"""B4 — range-wide transcript-marker gate (fix-round 2).

The committed evidence artifact ``tests/bind_race_rogue_containment_mutation_matrix.md``
had once been corrupted by a self-overwriting heredoc: raw model transcript
control markup and a duplicated heredoc body landed INSIDE a tracked file.
This gate makes that class of corruption impossible to re-commit silently:
no tracked text file may contain a transcript control marker.

There is NO allowlist. This test itself builds its marker constants from
parts (so it carries no literal marker), and a genuine fixture needing the
literals must escape them (``<\\|start\\|>``) so an artifact is never
byte-identical to harness protocol.
"""

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_LT = "<"
MARKERS = (
    _LT + "|start|>",
    _LT + "|end|>",
    _LT + "rfl:parameter",
)

TEXT_SUFFIXES = {
    ".md", ".py", ".sh", ".yaml", ".yml", ".json", ".txt", ".toml",
    ".cfg", ".ini", ".sql", ".service", ".template", ".bash", ".rsh",
    ".html", ".css", ".js", ".ts", ".tsx", ".j2", ".env",
}

TEXT_NAMES = {"Makefile", "Dockerfile", ".gitignore", ".gitattributes"}


def _tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        check=True,
    )
    return [REPO_ROOT / rel for rel in out.stdout.decode("utf-8", "replace").split("\0") if rel]


def _is_text_candidate(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES or path.name in TEXT_NAMES


def test_no_transcript_markers_in_tracked_text_files():
    offenders = []
    for path in _tracked_files():
        if not _is_text_candidate(path):
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:4096]:  # binary sniff
            continue
        text = data.decode("utf-8", "replace")
        for marker in MARKERS:
            if marker in text:
                rel = path.relative_to(REPO_ROOT).as_posix()
                offenders.append(f"{rel}: {marker}")
    assert not offenders, (
        "transcript control markers found in tracked text files "
        "(B4 gate):\n  " + "\n  ".join(offenders)
    )

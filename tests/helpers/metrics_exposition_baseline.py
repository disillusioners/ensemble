"""The exposition structural-identity baseline (AM-16 / D-11a).

The query door is ADDITIVE: the Prometheus text exposition must stay
STRUCTURALLY IDENTICAL to the lane's base artifact for the same
registry state — the same series set, the same TYPE lines, the same
label vocabularies and the same HELP structure.

Exactly FOUR carriers may differ in VALUE (the ratified allowlist):

1. the ``# frozen vocabulary version: N`` footer;
2. the ``ensemble_metrics_vocabulary_version`` gauge value;
3. the ``# HELP ensemble_checkpoint_op_seconds …`` text;
4. the ``# frozen vocabulary tool_category: count=N <set>`` line — the
   TOOL-CATEGORY vocabulary, whose frozen SET is the committed artifact
   ``.agents/shared/metrics/tool-categories.json``.  The
   metrics-agent-tools lane registered the ``metrics-read`` privileged
   category, so the derived set legitimately grew; the artifact pack
   (``tests/unit/tools/test_tool_category_artifact.py``) is the
   authority on that set's integrity, so masking its VALUE here costs
   no coverage.

Every other byte must match.  :func:`mask_exposition` replaces those
four with a fixed placeholder, so the masked bodies of the two legs are
directly comparable; anything else that moved is a gate RED.

THE CAPTURE IS A SUBPROCESS on purpose.  The registry is a PROCESS-WIDE
singleton: inside the pytest process other modules may have registered
metrics, so the rendered body would depend on the test's import order.
A fresh interpreter that imports ONLY the metrics modules renders a
DETERMINISTIC body — the same one on every leg, in every session.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

#: The repository root (the capture runs with this as its cwd, so the
#: tree's OWN ``daemon`` package is the one that renders).
REPO_ROOT = Path(__file__).resolve().parents[2]

#: The THREE allowlisted value-delta carriers (AM-16/D-11a).
ALLOWLIST: dict[str, str] = {
    "frozen_vocabulary_version": (
        "the '# frozen vocabulary version: N' footer"
    ),
    "vocabulary_version_gauge": (
        "the 'ensemble_metrics_vocabulary_version' gauge value"
    ),
    "tool_category_vocabulary": (
        "the '# frozen vocabulary tool_category: count=N <set>' line — the "
        "TOOL-CATEGORY vocabulary, whose frozen SET is the committed "
        "artifact `.agents/shared/metrics/tool-categories.json`; the "
        "metrics-agent-tools lane registered the `metrics-read` privileged "
        "category, so the derived set legitimately grew (the artifact pack "
        "is the authority on that set's integrity)"
    ),
    "checkpoint_op_seconds_help": (
        "the '# HELP ensemble_checkpoint_op_seconds ...' text — and its "
        "CANONICAL spelling 'message_api_saver_op_latency_seconds' (the "
        "same metric under its alias name; the contract names the alias, "
        "the canonical name carries the identical HELP text, so BOTH "
        "spellings are masked — an allowlist that covered only one "
        "spelling would be incomplete against its own carrier)"
    ),
}

#: The HELP-line prefixes the third carrier renders under (the alias
#: name and the canonical name — ONE metric, TWO exposition names).
_OP_SECONDS_HELP_PREFIXES: tuple[str, ...] = (
    "# HELP ensemble_checkpoint_op_seconds",
    "# HELP message_api_saver_op_latency_seconds",
)

#: The capture script — a fresh interpreter, the metrics modules only.
_CAPTURE_SCRIPT = """
import sys
sys.path.insert(0, ".")
from daemon import checkpoint_metrics as cm
try:
    from daemon.services.metrics_listener import ensure_f0_1_registered
except Exception:
    ensure_f0_1_registered = None
if ensure_f0_1_registered is not None:
    ensure_f0_1_registered()
sys.stdout.write(cm.render_metrics())
"""


def mask_exposition(body: str) -> str:
    """Replace the four allowlisted carriers with a fixed placeholder."""
    out: list[str] = []
    for line in body.splitlines():
        if line.startswith("# frozen vocabulary version:"):
            out.append("# frozen vocabulary version: <N>")
            continue
        if line.startswith("# frozen vocabulary tool_category:"):
            # The carrier is the VALUE (the count + the set); the LINE
            # PREFIX is kept, so a renamed vocabulary line still shows up
            # as a delta.
            out.append("# frozen vocabulary tool_category: <SET>")
            continue
        if line.startswith(_OP_SECONDS_HELP_PREFIXES):
            # "# HELP <name> <text>" → keep the NAME (a renamed carrier
            # must show up as a delta) and mask only the TEXT.
            name = line.split(" ", 2)[2].split(" ", 1)[0]
            out.append(f"# HELP {name} <TEXT>")
            continue
        if line.startswith("ensemble_metrics_vocabulary_version"):
            out.append("ensemble_metrics_vocabulary_version <N>")
            continue
        out.append(line)
    return "\n".join(out) + "\n"


def capture_exposition(cwd: str | os.PathLike[str] | None = None) -> str:
    """The exposition body of a FRESH interpreter (deterministic)."""
    root = Path(cwd) if cwd is not None else REPO_ROOT
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("POSTGRES_")
        and key not in {"DATABASE_URL", "PGHOST", "PGPORT", "PGDATABASE"}
    }
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", _CAPTURE_SCRIPT],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "the exposition capture failed "
            f"(rc={completed.returncode}): {completed.stderr[-2000:]}"
        )
    return completed.stdout


def capture_masked_exposition(cwd: str | os.PathLike[str] | None = None) -> str:
    """The MASKED exposition body (the comparable form)."""
    return mask_exposition(capture_exposition(cwd))


__all__ = [
    "ALLOWLIST",
    "REPO_ROOT",
    "capture_exposition",
    "capture_masked_exposition",
    "mask_exposition",
]

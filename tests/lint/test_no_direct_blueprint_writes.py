"""Lint test: all blueprint writes route through BlueprintWriteService.

Verifies that no file outside ``blueprint_write_service.py`` calls
``BlueprintRepository.create`` / ``update`` / ``soft_delete`` directly.
This enforces the canonical write boundary (C5 fix / G1): all writes
must go through :class:`~daemon.services.blueprint_write_service.BlueprintWriteService`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def test_no_direct_blueprint_writes() -> None:
    """No file outside blueprint_write_service.py may call
    BlueprintRepository.(create|update|soft_delete) directly."""
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            "grep", "-rnE",
            r"_blueprint_repo\.(create|update|soft_delete)\b",
            str(root / "daemon"),
            "--include=*.py",
        ],
        capture_output=True,
        text=True,
    )
    violations = [
        line
        for line in result.stdout.splitlines()
        if "blueprint_write_service" not in line
        and "test_" not in line
    ]
    assert not violations, (
        f"Direct repo writes found outside write service:\n"
        f"{chr(10).join(violations)}"
    )

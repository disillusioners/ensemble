"""Hermetic pins for the per-run scratch + fail-closed restore in
``scripts/pok_gate_prompt_mutations.py``.

Two independent properties are pinned:

* Two concurrent runs against DIFFERENT checkouts (whose target files have
  DIFFERENT sha256 values) each end with their OWN pre-run bytes, and neither
  leaves a per-run scratch directory behind.
* A tampered or truncated backup is REFUSED: the target is never overwritten
  with it and the guard reports the mismatch loudly (fail-closed).

No network, no DB, and no real pytest mutation runs: the script is launched
with POK_GATE_MUTATIONS_DRY_RUN=1, which stubs only the pytest invocations and
leaves the backup / guard / restore / cleanup lifecycle fully exercised.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

_THIS = pathlib.Path(__file__).resolve()
_REPO = _THIS.parents[2]
_SCRIPT = _REPO / "scripts" / "pok_gate_prompt_mutations.py"
_REAL_TARGET = _REPO / "daemon" / "services" / "pok_gate.py"
_REL_TARGET = pathlib.Path("daemon") / "services" / "pok_gate.py"
_SCRATCH_PREFIX = "pok-gate-prompt-mutations-"


def _sha(path: pathlib.Path) -> str:
    """Independent sha256 (do not reuse the code under test for assertions)."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_script_module():
    spec = importlib.util.spec_from_file_location(
        "pok_gate_prompt_mutations", _SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_checkout(root: pathlib.Path, name: str, extra: bytes = b""):
    """Minimal throwaway checkout: the real script + a copy of the real target.

    `extra` is appended to the target so two checkouts start with DIFFERENT
    sha256 values (non-vacuity: a cross-restore would then be visible).
    """
    co = root / name
    (co / "scripts").mkdir(parents=True)
    (co / "daemon" / "services").mkdir(parents=True)
    shutil.copy2(_SCRIPT, co / "scripts" / _SCRIPT.name)
    target = co / _REL_TARGET
    target.write_bytes(_REAL_TARGET.read_bytes() + extra)
    return co, target, _sha(target)


def _scratch_leftovers(root: pathlib.Path) -> list[str]:
    return sorted(
        p.name for p in root.iterdir() if p.name.startswith(_SCRATCH_PREFIX)
    )


@pytest.mark.parametrize("kind", ["tampered", "truncated"])
def test_tampered_or_truncated_backup_is_refused(tmp_path, kind):
    mod = _load_script_module()
    target = tmp_path / "pok_gate.py"
    original = b"# original target bytes\n" + b"x" * 128 + b"\n"
    target.write_bytes(original)
    expected = _sha(target)

    backup = tmp_path / "backup.bak"
    if kind == "tampered":
        backup.write_bytes(original + b"\n# INJECTED BY ANOTHER RUN\n")
    else:
        backup.write_bytes(original[:40])
    assert _sha(backup) != expected  # non-vacuous: the backup really differs

    ok, detail = mod.restore_verified(backup, target, expected)

    assert ok is False
    assert "mismatch" in detail.lower()
    assert "REFUSING" in detail
    assert target.read_bytes() == original  # NOT overwritten with the backup


def test_verified_backup_is_installed(tmp_path):
    """The guard is not vacuously refusing everything: a good backup restores."""
    mod = _load_script_module()
    target = tmp_path / "pok_gate.py"
    target.write_bytes(b"mutated bytes\n")
    backup = tmp_path / "backup.bak"
    original = b"original bytes\n"
    backup.write_bytes(original)

    ok, detail = mod.restore_verified(backup, target, _sha(backup))

    assert ok is True
    assert "verified" in detail
    assert target.read_bytes() == original


def test_concurrent_runs_keep_each_checkout_file(tmp_path):
    scratch_root = tmp_path / "scratch-root"
    scratch_root.mkdir()
    co_a, target_a, sha_a = _build_checkout(tmp_path, "checkoutA")
    co_b, target_b, sha_b = _build_checkout(
        tmp_path, "checkoutB", b"\n# checkout B byte-level difference\n"
    )
    assert sha_a != sha_b  # non-vacuity: a cross-restore would change the sha

    env = os.environ.copy()
    env["TMPDIR"] = str(scratch_root)
    env["POK_GATE_MUTATIONS_DRY_RUN"] = "1"

    procs = [
        subprocess.Popen(
            [sys.executable, str(co / "scripts" / _SCRIPT.name)],
            cwd=str(co),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        for co in (co_a, co_b)
    ]
    results = []
    for proc in procs:
        out, _ = proc.communicate(timeout=120)
        results.append((proc.returncode, out))

    for rc, out in results:
        assert rc == 0, out
        assert "failures: none" in out, out

    assert _sha(target_a) == sha_a  # A never installed B's file
    assert _sha(target_b) == sha_b  # B never installed A's file
    assert _scratch_leftovers(scratch_root) == []  # no per-run scratch left


def test_scratch_cleaned_when_a_leg_fails(tmp_path):
    scratch_root = tmp_path / "scratch-root"
    scratch_root.mkdir()
    co, target, _ = _build_checkout(tmp_path, "broken")
    # Drop the jargon anchor so that leg SKIPs -> main() returns non-zero.
    data = target.read_bytes().replace(
        b"You are the write gate for the Project Operating Knowledge (POK) ",
        b"You are the write gate for the POK ",
    )
    target.write_bytes(data)

    env = os.environ.copy()
    env["TMPDIR"] = str(scratch_root)
    env["POK_GATE_MUTATIONS_DRY_RUN"] = "1"
    proc = subprocess.run(
        [sys.executable, str(co / "scripts" / _SCRIPT.name)],
        cwd=str(co),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 1
    assert "SKIPPED" in proc.stdout
    assert _scratch_leftovers(scratch_root) == []  # finally ran on failure

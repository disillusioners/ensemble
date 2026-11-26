#!/usr/bin/env python3
"""The mutation legs for the POK write-gate prompt pins (Wave 2 Phase 7/8).

Each leg backs the target file up into a UNIQUE, PER-RUN temporary directory,
applies the mutation, runs the named test and PROVES IT IS RED, then restores
the file ONLY after verifying the backup still hashes to the exact sha256 that
was recorded when the backup was taken. A tampered or clobbered backup is
REFUSED (fail-closed) and the leg fails loudly -- no wrong-file restore. The
scratch directory is removed on exit (including on failure/interrupt), so two
concurrent runs against different checkouts cannot overwrite each other's
backups. (NEVER git stash.)

Usage: .venv/bin/python scripts/pok_gate_prompt_mutations.py
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
_venv_python = REPO / ".venv" / "bin" / "python"
PYTEST = _venv_python if _venv_python.exists() else pathlib.Path(sys.executable)

BASE_ENV = {
    "PATH": "/usr/bin:/bin:/usr/local/bin",
    "HOME": str(pathlib.Path.home()),
    "ENSEMBLE_SELF_ENV": "dev",
    "ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES": "0",
    "PYTHONDONTWRITEBYTECODE": "1",
}

# Hermetic-test seam: when set, run_pytest is stubbed with a deterministic
# RED-then-GREEN sequence so the backup/guard/restore lifecycle can be pinned
# without launching the real mutation suite. Normal runs never set this.
_DRY_RUN = os.environ.get("POK_GATE_MUTATIONS_DRY_RUN") == "1"
_pytest_calls = 0

LEGS = [
    {
        "name": "merged-into-one-message",
        "file": "daemon/services/pok_gate.py",
        "find": (
            "            messages=[{\"role\": \"system\", \"content\": _system_content},\n"
            "                     {\"role\": \"user\", \"content\": _user_content}],"
        ),
        "replace": (
            "            messages=[{\"role\": \"user\", \"content\": _system_content + \"\\\\n\" + _user_content}],"
        ),
        "tests": [
            "tests/unit/services/test_pok_gate_unit.py::TestPromptClauses::test_the_house_call_sends_system_and_user_roles",
        ],
    },
    {
        "name": "jargon-bare-pok",
        "file": "daemon/services/pok_gate.py",
        "find": '"You are the write gate for the Project Operating Knowledge (POK) "',
        "replace": '"You are the write gate for the POK "',
        "tests": [
            "tests/unit/services/test_pok_gate_unit.py::TestPromptClauses::test_no_bare_pok_or_rag_anywhere_in_either_message",
        ],
    },
    {
        "name": "digest-header-bare-pok",
        "file": "daemon/services/pok_gate.py",
        "find": '"PROJECT OPERATING KNOWLEDGE (POK) ACTIVE-ROW DIGEST "',
        "replace": '"POK ACTIVE-ROW DIGEST "',
        "tests": [
            "tests/unit/services/test_pok_gate_unit.py::TestRealDigestHeader::test_the_real_digest_header_is_the_expansion_form",
        ],
    },
    {
        "name": "unnamed-placeholder",
        "file": "daemon/services/pok_gate.py",
        "find": '"the project this write targets (its name was not available to "',
        "replace": '"the project this write targets (unnamed) (its name was not available to "',
        "tests": [
            "tests/unit/services/test_pok_gate_unit.py::TestPromptClauses::test_the_prompt_never_contains_unnamed_placeholder",
        ],
    },
]


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def restore_verified(
    backup: pathlib.Path, target: pathlib.Path, expected_sha: str
) -> tuple[bool, str]:
    """Install `backup` over `target` ONLY if it still hashes to `expected_sha`.

    `expected_sha` is the pre-mutation sha256 recorded when the backup was
    taken. On mismatch the backup is NOT installed and `target` is left
    untouched (fail-closed); the caller is told to fail the leg loudly.
    Returns (ok, detail).
    """
    actual = sha256(backup)
    if actual != expected_sha:
        return False, (
            f"backup sha256 mismatch: expected {expected_sha}, got {actual} "
            f"-- REFUSING to install backup over {target}"
        )
    shutil.copy2(backup, target)
    return True, "restored (backup sha256 verified)"


def run_pytest(targets: list[str]) -> tuple[int, str]:
    global _pytest_calls
    if _DRY_RUN:
        _pytest_calls += 1
        rc = 1 if _pytest_calls % 2 else 0
        return rc, "dry-run (pytest stubbed)"
    cmd = [
        str(PYTEST),
        "-m",
        "pytest",
        *targets,
        "-p",
        "no:randomly",
        "-q",
        "--tb=line",
        "--timeout=120",
    ]
    proc = subprocess.run(
        cmd, cwd=REPO, env=BASE_ENV, capture_output=True, text=True
    )
    return proc.returncode, (proc.stdout + proc.stderr)[-1500:]


def main() -> int:
    # Unique scratch per invocation: two concurrent runs against different
    # checkouts can never share (and clobber) each other's backups.
    scratch = pathlib.Path(
        tempfile.mkdtemp(prefix="pok-gate-prompt-mutations-")
    )
    failures: list[str] = []
    try:
        for leg in LEGS:
            name = leg["name"]
            target = REPO / leg["file"]
            backup = scratch / f"{name}.{target.name}.bak"
            before = sha256(target)
            shutil.copy2(target, backup)
            text = target.read_text(encoding="utf-8")
            if leg["find"] not in text:
                print(f"[{name}] SKIPPED — the mutation anchor was not found")
                failures.append(name)
                continue
            target.write_text(
                text.replace(leg["find"], leg["replace"], 1), encoding="utf-8"
            )
            rc, tail = run_pytest(leg["tests"])
            red = rc != 0
            ok, detail = restore_verified(backup, target, before)
            if not ok:
                # Fail closed: never install a contaminated backup. Restore
                # the exact pre-mutation bytes read above so the checkout is
                # left clean, then fail this leg loudly.
                target.write_text(text, encoding="utf-8")
                print(f"[{name}] FAIL — {detail}")
                failures.append(name)
                print(tail)
                continue
            after = sha256(target)
            restored = before == after
            rc_green, tail_green = run_pytest(leg["tests"])
            green = rc_green == 0
            status = "PASS" if (red and restored and green) else "FAIL"
            print(
                f"[{name}] {status} — mutation: RED={red} (rc={rc}) | "
                f"restore sha256 verified+identical={restored} | "
                f"re-green={green} (rc={rc_green})"
            )
            if not (red and restored and green):
                failures.append(name)
                print(tail)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    print()
    print(f"legs: {len(LEGS)} — failures: {failures or 'none'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

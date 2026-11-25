#!/usr/bin/env python3
"""The mutation legs for the POK write-gate prompt pins (Wave 2 Phase 7/8).

Every leg: copy the file -> apply the mutation -> run the named test and
PROVE IT IS RED -> restore from the COPY (sha256-verified; NEVER git
stash) -> prove it is GREEN again.

Usage: .venv/bin/python scripts/pok_gate_prompt_mutations.py
"""

from __future__ import annotations

import hashlib
import pathlib
import shutil
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRATCH = pathlib.Path("/tmp/pok-gate-prompt-mutations")
_venv_python = REPO / ".venv" / "bin" / "python"
PYTEST = _venv_python if _venv_python.exists() else pathlib.Path(sys.executable)

BASE_ENV = {
    "PATH": "/usr/bin:/bin:/usr/local/bin",
    "HOME": str(pathlib.Path.home()),
    "ENSEMBLE_SELF_ENV": "dev",
    "ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES": "0",
    "PYTHONDONTWRITEBYTECODE": "1",
}

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


def run_pytest(targets: list[str]) -> tuple[int, str]:
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
    SCRATCH.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    for leg in LEGS:
        name = leg["name"]
        target = REPO / leg["file"]
        backup = SCRATCH / f"{name}.{target.name}.bak"
        before = sha256(target)
        shutil.copy2(target, backup)
        text = target.read_text(encoding="utf-8")
        if leg["find"] not in text:
            print(f"[{name}] SKIPPED — the mutation anchor was not found")
            failures.append(name)
            continue
        target.write_text(text.replace(leg["find"], leg["replace"], 1), encoding="utf-8")
        rc, tail = run_pytest(leg["tests"])
        red = rc != 0
        shutil.copy2(backup, target)
        after = sha256(target)
        restored = before == after
        rc_green, tail_green = run_pytest(leg["tests"])
        green = rc_green == 0
        status = "PASS" if (red and restored and green) else "FAIL"
        print(
            f"[{name}] {status} — mutation: RED={red} (rc={rc}) | "
            f"restore sha256 identical={restored} | re-green={green} (rc={rc_green})"
        )
        if not (red and restored and green):
            failures.append(name)
            print(tail)
    print()
    print(f"legs: {len(LEGS)} — failures: {failures or 'none'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

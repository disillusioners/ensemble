#!/usr/bin/env python3
"""The mutation legs for the POK librarian (Wave 2 Phase 10/11).

Every leg: copy the file → apply the mutation → run the named test and
PROVE IT IS RED → restore from the COPY (sha256-verified; NEVER git
stash) → prove it is GREEN again.

Usage: .venv/bin/python scripts/pok_librarian_mutations.py
"""

from __future__ import annotations

import hashlib
import pathlib
import shutil
import subprocess
import sys

REPO = pathlib.Path("/home/nea/ensemble-src-wt-pok-write-gate")
SCRATCH = pathlib.Path("/tmp/pok-librarian-mutations")
PYTEST = REPO / ".venv/bin/python"

BASE_ENV = {
    "PATH": "/usr/bin:/bin:/usr/local/bin",
    "HOME": str(pathlib.Path.home()),
    "ENSEMBLE_SELF_ENV": "dev",
    "ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES": "0",
    "PYTHONDONTWRITEBYTECODE": "1",
}

LEGS = [
    {
        "name": "soft-max-recompute",
        "file": "daemon/services/pok_librarian.py",
        "find": "    value = max(effective_floor, capacity)",
        "replace": "    value = 512  # MUTATION: a hand-set constant",
        "tests": [
            "tests/unit/services/test_pok_librarian_unit.py::TestSoftMaxDerivation::test_the_value_is_derived_not_hand_set",
        ],
    },
    {
        "name": "consolidation-basis-guard",
        "file": "daemon/services/pok_librarian.py",
        "find": """    return any(
        s.trusted and not s.corroborates_only and s.kind in TRUSTED_METRIC_KINDS
        for s in signals
    )""",
        "replace": """    return any(
        s.trusted and not s.corroborates_only and s.kind in TRUSTED_METRIC_KINDS
        for s in signals
    ) or any(s.kind == "access_count" for s in signals)  # MUTATION""",
        "tests": [
            "tests/unit/services/test_pok_librarian_unit.py::TestMetricSet::test_access_count_alone_never_triggers_a_consolidation",
        ],
    },
    {
        "name": "drift-report",
        "file": "daemon/services/pok_librarian.py",
        "find": "    delta = int(value) - int(previous.value)",
        "replace": "    delta = 0  # MUTATION: the drift is never reported",
        "tests": [
            "tests/unit/services/test_pok_librarian_unit.py::TestDriftReport::test_a_moved_value_reports_the_delta",
        ],
    },
    {
        "name": "r-ret-alone-archives",
        "file": "daemon/services/pok_librarian.py",
        "find": """    if entry.merge_verdicts:
        return "covered-by-surviving-row\"""",
        "replace": """    if entry.merge_verdicts:
        return "covered-by-surviving-row"

    if entry.never_retrieved:
        return "covered-by-surviving-row"  # MUTATION: R-RET alone archives""",
        "tests": [
            "tests/unit/services/test_pok_librarian_unit.py::TestRRetDetection::test_r_ret_alone_never_archives",
        ],
    },
    {
        "name": "unwired-recorder",
        "file": "daemon/services/context_messages.py",
        "find": """                on_retrieval=make_retrieval_recorder(
                    service._repo,
                    project_id,
                    agent_id=getattr(agent_meta, "id", None),
                    source="injection",
                ),""",
        "replace": """                # MUTATION: the recorder is not wired (stay-dark)""",
        "tests": [
            "tests/unit/services/test_pok_librarian_unit.py::TestRetrievalRecorderWiring::test_the_context_seam_passes_the_recorder",
        ],
    },
    {
        "name": "boot-path-runs-the-pass",
        "file": "daemon/manager.py",
        "find": """        logger.debug(
            "pok_librarian registered: interval=%sh",
            POK_LIBRARIAN_INTERVAL_HOURS,
        )""",
        "replace": """        logger.debug(
            "pok_librarian registered: interval=%sh",
            POK_LIBRARIAN_INTERVAL_HOURS,
        )
        # MUTATION: the boot path executes a librarian pass
        from .services.pok_librarian import run_librarian_pass

        run_librarian_pass(self._engine, "p1")""",
        "tests": [
            "tests/unit/services/test_pok_librarian_unit.py::TestPassRegistration::test_the_manager_registers_the_job_without_running_it",
        ],
    },
    {
        "name": "soft-max-row-in-the-pok-store",
        "file": "daemon/services/pok_librarian.py",
        "find": """        repo = POKRepository(engine)
        store = read_store_metrics(repo, project_id)""",
        "replace": """        repo = POKRepository(engine)
        store = read_store_metrics(repo, project_id)
        # MUTATION: the soft max is written INTO the POK store
        try:
            from daemon.repositories.pok.models import (
                ProjectOperatingKnowledge as _MutaRow,
            )

            repo.insert(
                _MutaRow(
                    id="pok-soft-max-row",
                    project_id=project_id,
                    slug="pok-soft-max-v1",
                    domain="other",
                    audience="both",
                    body="the soft max lives here",
                    provenance="observed",
                    status="active",
                    content_sha256="muta",
                    created_by_agent="librarian",
                )
            )
        except Exception:
            pass""",
        "tests": [
            "tests/unit/services/test_pok_librarian_unit.py::TestSoftMaxPlacement::test_the_pok_store_never_carries_a_soft_max_row",
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
        # restore from the COPY (never git stash)
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

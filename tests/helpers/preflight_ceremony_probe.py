#!/usr/bin/env python3
"""Pack I probe — the ceremony-readiness guard's BEHAVIOURAL contract.

This helper is the executable half of ``tests/packs/preflight_ceremony_readiness_test.sh``.
It exercises the DAEMON-SIDE guard that phase 6 §4.2/§4.3 specifies: an arm
against an explicitly pinned ``ENSEMBLE_UPGRADE_SCRIPTS_DIR`` must be judged
BEFORE the ceremony nonce burns, from a stamp read out of ``<dir>/lib.sh``,
with git ANCESTRY as the primary mechanism and a non-git dir WARNING-only.

SEAM CONTRACT (declared here because the pack exists before the guard does —
phase5 T5.16 / N-18 "the pack's *content* depends on phase6's spec, not on
phase6's implementation"):

    callable(scripts_dir: Path, floor_commit: str | None = None,
             env: dict[str, str] | None = None) -> tuple[str, str]
        -> (verdict, detail) with verdict in {"arm", "warn", "refuse"}

The probe DISCOVERS the callable by name (a short, ordered candidate list) and
reports which one it used, so P6 may pick any of the documented names. When
none is found the probe exits 3 with the single line

    PF_MISSING_GUARD: <reason>

and the pack records a PF_DEFERRED_RED state — it can never be mistaken for a
silent pass (the shell pack's ``--expect-implemented`` mode turns every
deferral into a hard failure).

Scenario ids are the pack's own (I1–I12); the expected verdicts come from
``tests/packs/preflight_ceremony_readiness.txt``.

Usage:  python3 tests/helpers/preflight_ceremony_probe.py --root <scratch-dir>
Exit:   0 all scenarios pass · 1 a scenario FAILED · 3 guard absent
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

CANDIDATE_SEAMS = (
    "scripts_dir_provenance_verdict",
    "check_scripts_dir_provenance",
    "scripts_dir_staleness_check",
    "_scripts_dir_provenance_verdict",
    "_scripts_dir_staleness_gate",
    "_arm_scripts_provenance_check",
)

PF_MISSING = 3


def _git(cwd: Path, *args: str) -> str:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(cwd),
        "GIT_AUTHOR_NAME": "pf", "GIT_AUTHOR_EMAIL": "pf@example.invalid",
        "GIT_COMMITTER_NAME": "pf", "GIT_COMMITTER_EMAIL": "pf@example.invalid",
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True, env=env, timeout=120, check=True,
    ).stdout.strip()


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")


def _commit(path: Path, message: str, body: str = "") -> str:
    f = path / "lib.sh"
    f.write_text(f"#!/usr/bin/env bash\n# {message}\n{body}\n", encoding="utf-8")
    _git(path, "add", "lib.sh")
    _git(path, "commit", "-q", "-m", message)
    return _git(path, "rev-parse", "HEAD")


def build_fixtures(root: Path) -> dict[str, Path]:
    """Plant the seven pinned-dir shapes. All under ``root`` (the pack's
    scratch), never a live checkout."""
    root.mkdir(parents=True, exist_ok=True)
    fx: dict[str, Path] = {}

    # a fresh repo whose history CONTAINS a floor commit, with the stamp in
    # lib.sh at HEAD (I1) and a diverged checkout without the floor (I2).
    fresh = root / "fresh-git"
    _init_repo(fresh)
    _commit(fresh, "base")
    floor = _commit(fresh, "the preflight-hang fix", "PIPELINE_SCRIPTS_REV=deadbeef")
    _commit(fresh, "later work", "PIPELINE_SCRIPTS_REV=feedface")
    (root / "floor.sha").write_text(floor + "\n", encoding="utf-8")
    fx["fresh"] = fresh

    diverged = root / "diverged-git"
    _init_repo(diverged)
    _commit(diverged, "unrelated base", "PIPELINE_SCRIPTS_REV=cafebabe")
    fx["diverged"] = diverged

    # a dir whose lib.sh carries NO stamp (B3: the stamp is read from the FILE)
    nostamp = root / "no-stamp"
    _init_repo(nostamp)
    _commit(nostamp, "no stamp here", "echo hi")
    fx["no_stamp"] = nostamp

    # non-git dir WITH a stamp (I4) and WITHOUT one (I5)
    plain_stamp = root / "plain-stamp"
    plain_stamp.mkdir()
    (plain_stamp / "lib.sh").write_text(
        "#!/usr/bin/env bash\nPIPELINE_SCRIPTS_REV=abcabc\n", encoding="utf-8"
    )
    fx["plain_stamp"] = plain_stamp

    plain_nostamp = root / "plain-nostamp"
    plain_nostamp.mkdir()
    (plain_nostamp / "lib.sh").write_text(
        "#!/usr/bin/env bash\necho hi\n", encoding="utf-8"
    )
    fx["plain_nostamp"] = plain_nostamp

    # a DIRTY git checkout that CONTAINS the floor (I7: warn, never refuse)
    dirty = root / "dirty-git"
    _init_repo(dirty)
    _commit(dirty, "base")
    _commit(dirty, "the preflight-hang fix", "PIPELINE_SCRIPTS_REV=deadbeef")
    (dirty / "lib.sh").write_text(
        "#!/usr/bin/env bash\nPIPELINE_SCRIPTS_REV=deadbeef\n# local edit\n",
        encoding="utf-8",
    )
    fx["dirty"] = dirty
    return fx


def discover_seam():
    try:
        from daemon.tools import upgrade_tools as ut  # noqa: WPS433 (lazy)
    except Exception as exc:  # pragma: no cover - import failure reports
        return None, "", f"daemon.tools.upgrade_tools unimportable: {exc}"
    for name in CANDIDATE_SEAMS:
        fn = getattr(ut, name, None)
        if callable(fn):
            return fn, name, ""
    return None, "", (
        "no scripts-dir provenance seam on daemon.tools.upgrade_tools "
        f"(looked for: {', '.join(CANDIDATE_SEAMS)})"
    )


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="scratch dir for fixtures")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args(argv[1:])

    root = Path(args.root)
    fx = build_fixtures(root)
    floor = (root / "floor.sha").read_text(encoding="utf-8").strip()

    seam, name, why = discover_seam()
    if seam is None:
        print(f"PF_MISSING_GUARD: {why}")
        return PF_MISSING
    print(f"PF_SEAM: {name}")

    scenarios = [
        # id, kwargs, expected verdict, note
        ("I1", dict(scripts_dir=fx["fresh"], floor_commit=floor), "arm",
         "git dir CONTAINING the floor ⇒ arms (however new)"),
        ("I2", dict(scripts_dir=fx["diverged"], floor_commit=floor), "refuse",
         "git dir NOT containing the floor ⇒ refuse"),
        ("I3", dict(scripts_dir=fx["fresh"], floor_commit="0" * 40), "refuse",
         "missing/unknown floor object ⇒ refuse"),
        ("I4", dict(scripts_dir=fx["plain_stamp"], floor_commit=floor), "arm",
         "non-git dir WITH a stamp ⇒ arms"),
        ("I5", dict(scripts_dir=fx["plain_nostamp"], floor_commit=floor), "warn",
         "non-git dir WITHOUT a stamp ⇒ WARNING only (Q2)"),
        ("I6", dict(scripts_dir=fx["no_stamp"], floor_commit=floor), "refuse",
         "stamp absent from <dir>/lib.sh ⇒ refuse (B3 file acquisition)"),
        ("I7", dict(scripts_dir=fx["dirty"], floor_commit=floor), "arm",
         "dirty checkout that CONTAINS the floor ⇒ arms (warn, never refuse)"),
    ]

    failures = 0
    for sid, kw, expected, note in scenarios:
        try:
            verdict, detail = seam(env=dict(os.environ), **kw)
        except Exception as exc:
            print(f"SCENARIO {sid} FAIL raised {type(exc).__name__}: {exc} -- {note}")
            failures += 1
            continue
        ok = str(verdict) == expected
        print(
            f"SCENARIO {sid} {'PASS' if ok else 'FAIL'} "
            f"expected={expected} got={verdict} :: {detail} -- {note}"
        )
        if not ok:
            failures += 1

    # I8 — the escape hatch arms a STALE dir, loudly.
    stale = fx["diverged"]
    try:
        env = dict(os.environ)
        env["ENSEMBLE_ALLOW_STALE_SCRIPTS"] = "1"
        verdict, detail = seam(scripts_dir=stale, floor_commit=floor, env=env)
        ok = str(verdict) in ("arm", "warn")
        print(
            f"SCENARIO I8 {'PASS' if ok else 'FAIL'} expected=arm got={verdict} "
            f":: {detail} -- ENSEMBLE_ALLOW_STALE_SCRIPTS=1 arms a stale dir"
        )
        failures += 0 if ok else 1
    except Exception as exc:
        print(f"SCENARIO I8 FAIL raised {type(exc).__name__}: {exc}")
        failures += 1

    if not args.keep:
        shutil.rmtree(root, ignore_errors=True)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

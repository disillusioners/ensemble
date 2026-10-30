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

Scenario ids are the pack's own (I1–I15); the expected verdicts come from
``tests/packs/preflight_ceremony_readiness.txt``.

I13–I15 are the W-1/W-3 (verifier 2026-10-30) legs: a probe that cannot RUN
at all must degrade to a verdict instead of unwinding the arm path
(I13 git-shaped ⇒ refuse, I14 non-git ⇒ warn), and an unusable scripts dir
must refuse (I15) — three branches that previously had zero executed
coverage.

I13b/I13c are the W2 (code council 2026-10-30) legs: `git rev-parse` answers
128 (not the 255 probe sentinel) for "not a git repository", dubious
ownership, a corrupt/pruned `.git` and a broken gitfile. On a GIT-SHAPED dir
that must REFUSE (I13b — ancestry unverifiable, a stamp cannot prove it); on
a dir with NO `.git` the same rc must stay stamp-provenance (I13c).

Usage:  python3 tests/helpers/preflight_ceremony_probe.py --root <scratch-dir>
Exit:   0 all scenarios pass · 1 a scenario FAILED · 3 guard absent
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent

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

    # a dir with NO lib.sh at all (I15: scripts-dir-unusable)
    no_libsh = root / "no-libsh"
    no_libsh.mkdir()

    plain_nostamp = root / "plain-nostamp"
    plain_nostamp.mkdir()
    (plain_nostamp / "lib.sh").write_text(
        "#!/usr/bin/env bash\necho hi\n", encoding="utf-8"
    )
    fx["plain_nostamp"] = plain_nostamp
    fx["no_libsh"] = no_libsh

    # a DIRTY git checkout that CONTAINS the floor (I7: warn, never refuse).
    # DETERMINISM (phase 6 wave 4): COPY the fresh repo — including its
    # .git — instead of re-building "equivalent" commits, whose sha equality
    # would depend on both repos committing inside the SAME wall-clock
    # second (observed flake under ambient load: the dirty repo's fix
    # commit got a later timestamp, a different sha, and the shared floor
    # object vanished from its history ⇒ I7 spuriously refused).
    dirty = root / "dirty-git"
    shutil.copytree(fresh, dirty)
    (dirty / "lib.sh").write_text(
        "#!/usr/bin/env bash\nPIPELINE_SCRIPTS_REV=deadbeef\n# local edit\n",
        encoding="utf-8",
    )
    fx["dirty"] = dirty
    return fx


def discover_seam() -> (
    tuple[Callable[..., tuple[str, str]] | None, str, str]
):
    # Resolve the daemon package from the TREE UNDER TEST (this repo), not
    # from whatever checkout the interpreter's site-packages/.pth points
    # at — in script mode sys.path[0] is tests/helpers/, so an ambient
    # editable-install .pth would otherwise shadow the worktree's module
    # and the probe would judge the WRONG tree (found while flipping Pack
    # I green, phase 6; the D-22 seam contract itself is unchanged).
    repo_root = str(HERE.parent.parent)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
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
        # W-3 (verifier 2026-10-30): the scripts-dir-unusable branch — a pinned
        # dir that is not a pipeline scripts tree at all.
        ("I15", dict(scripts_dir=fx["no_libsh"], floor_commit=floor), "refuse",
         "a dir WITHOUT lib.sh ⇒ refuse (scripts-dir-unusable)"),
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

    # ── I13/I14 — W-1 (verifier 2026-10-30): a probe that CANNOT RUN AT ALL
    # must degrade to a verdict. The defect being pinned: an exception out of
    # ``bounded_git_probe`` unwinds the arm path's generic ``except Exception``,
    # which does NOT release the pipeline lock — an orphaned lock until the
    # 300 s stale-break. The probe is forced to raise the way an absent/broken
    # git or the 30 s bound would (TimeoutExpired is a SubprocessError; the
    # absent-git class is OSError — the same except arm).
    from daemon.tools import upgrade_journal as _uj

    _real_run = _uj.subprocess.run

    def _boom(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=30)

    _uj.subprocess.run = _boom
    try:
        for sid, key, expected, note in (
            ("I13", "fresh", "refuse",
             "git-SHAPED dir + probe error ⇒ refuse (D6-10 command-error), never an exception"),
            ("I14", "plain_stamp", "warn",
             "non-git dir + probe error ⇒ warn (Q2: the stamp IS the provenance), never an exception"),
        ):
            try:
                verdict, detail = seam(
                    scripts_dir=fx[key], floor_commit=floor, env=dict(os.environ)
                )
            except Exception as exc:  # noqa: BLE001 - the pinned defect
                print(
                    f"SCENARIO {sid} FAIL raised {type(exc).__name__}: {exc} -- {note}"
                )
                failures += 1
                continue
            ok = str(verdict) == expected
            print(
                f"SCENARIO {sid} {'PASS' if ok else 'FAIL'} expected={expected} "
                f"got={verdict} :: {detail} -- {note}"
            )
            failures += 0 if ok else 1
    finally:
        _uj.subprocess.run = _real_run

    # ── I13b — W2 (code council 2026-10-30): git ANSWERS 128 for "not a git
    # repository" — and for dubious ownership (`safe.directory`), a
    # corrupt/unreadable `.git` and a gitfile whose worktree metadata was
    # pruned. Only the 255 PROBE sentinel (I13) was handled, so every 128 on a
    # GIT-SHAPED dir fell through to the non-git branch, where a self-asserted
    # stamp ARMED — fail-open on the one gate that must stop a stale scripts
    # dir (a stamp cannot prove ancestry). Pinned by forcing the probe to
    # answer rc=128 on a directory that IS git-shaped.
    _real_probe = _uj.bounded_git_probe

    def _probe_128(*_a, **_k):
        return 128, "", "fatal: not a git repository (or any of the parent directories): .git"

    _uj.bounded_git_probe = _probe_128
    try:
        for sid, key, expected, note in (
            ("I13b", "fresh", "refuse",
             "git-SHAPED dir + rev-parse rc=128 ⇒ refuse (W2: ancestry UNVERIFIABLE, no stamp arm)"),
        ):
            try:
                verdict, detail = seam(
                    scripts_dir=fx[key], floor_commit=floor, env=dict(os.environ)
                )
            except Exception as exc:  # noqa: BLE001 - the pinned defect class
                print(
                    f"SCENARIO {sid} FAIL raised {type(exc).__name__}: {exc} -- {note}"
                )
                failures += 1
                continue
            ok = str(verdict) == expected
            print(
                f"SCENARIO {sid} {'PASS' if ok else 'FAIL'} expected={expected} "
                f"got={verdict} :: {detail} -- {note}"
            )
            failures += 0 if ok else 1
        # Control: the SAME rc on a dir with NO `.git` must stay WARN-only
        # (Q2/D6-11) — the new arm must not invent a refusal for a genuinely
        # non-git pinned dir.
        try:
            verdict, detail = seam(
                scripts_dir=fx["plain_stamp"], floor_commit=floor,
                env=dict(os.environ),
            )
            ok = str(verdict) == "arm"
            print(
                f"SCENARIO I13c {'PASS' if ok else 'FAIL'} expected=arm "
                f"got={verdict} :: {detail} -- rc=128 on a NON-git dir stays "
                f"stamp-provenance (Q2), never a refusal"
            )
            failures += 0 if ok else 1
        except Exception as exc:  # noqa: BLE001
            print(f"SCENARIO I13c FAIL raised {type(exc).__name__}: {exc}")
            failures += 1
    finally:
        _uj.bounded_git_probe = _real_probe

    if not args.keep:
        shutil.rmtree(root, ignore_errors=True)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

"""Canary-asserted mutation matrix runner (loop-2 cleanup, ITEM-2).

For each plugin: run the lane file with the plugin loaded, then
ASSERT the canary before counting:
  * LOAD canary   — the plugin printed its "plugin loaded" marker;
  * CALL canary   — the plugin printed its first-CALL marker (the
    mutants that wrap the seam print "[CANARY] ... IS INSTALLED AND
    CALLED"); a plugin that never prints a call marker is reported
    as "no call canary" (its failure count is then checked for
    ERROR-cascade contamination: 5xx responses / NameError).

A failure count is only meaningful if the mutant actually ran AND
did not fail for an unrelated reason (NameError cascade, 500s).

Usage: .venv/bin/python /tmp/views-review/run_matrix.py <plugin> [...]
"""
from pathlib import Path
import os
import re
import subprocess
import sys

def _repo_root() -> str:
    """Walk up to the repo root (the directory holding ``daemon/``)."""
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / "daemon" / "routers").is_dir():
            return str(candidate)
    return str(here.parents[6])

REPO = _repo_root()
LANE = "tests/unit/routers/test_live_views_session_bypass.py"

SCRUB = [
    "env", "-u", "ENSEMBLE_AUTH_ALLOWED_ORIGINS",
    "-u", "ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE",
    "-u", "ENSEMBLE_AUTH_ISSUER",
    "-u", "ENSEMBLE_AUTH_DISCOVERY_URL",
    "-u", "ENSEMBLE_AUTH_CLIENT_ID",
    "-u", "ENSEMBLE_AUTH_CLIENT_SECRET",
    "-u", "ENSEMBLE_AUTH_ENABLED",
    "-u", "POSTGRES_DB", "-u", "POSTGRES_USER",
    "-u", "POSTGRES_PASSWORD", "-u", "POSTGRES_HOST",
    "-u", "POSTGRES_PORT",
]


def run(plugin: str) -> dict:
    here = Path(__file__).resolve().parent
    # MERGE the ambient environment (a stripped env breaks git/PATH for
    # the blob-loading plugins) and expose BOTH the plugin dir and the
    # labelled non-evidence dir on PYTHONPATH.
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(here), str(here / "non-evidence")]
    )
    proc = subprocess.run(
        SCRUB
        + [
            f"{REPO}/.venv/bin/python", "-m", "pytest", LANE,
            "-q", "--timeout", "120", "--no-header", "-s", "-p", plugin,
        ],
        cwd=REPO, env=env, capture_output=True, text=True,
    )
    out = proc.stdout + proc.stderr
    m = re.search(r"(\d+) failed, (\d+) passed", out)
    failed = int(m.group(1)) if m else -1
    passed = int(m.group(2)) if m else -1
    load_canary = bool(re.search(r"plugin loaded|installed", out, re.I))
    call_canary = bool(re.search(r"\[CANARY\]", out))
    cascade = bool(
        re.search(r"NameError|Internal Server Error", out)
    )
    names = sorted(
        re.findall(r"^FAILED [^:]*::([\w\.\-\[\]]+)", out, re.M)
    )
    return {
        "plugin": plugin,
        "failed": failed,
        "passed": passed,
        "load_canary": load_canary,
        "call_canary": call_canary,
        "cascade": cascade,
        "names": names,
    }


if __name__ == "__main__":
    plugins = sys.argv[1:]
    if not plugins:
        plugins = [
            "mut_fwd_canary", "mut_fwd_unconstrained", "mut_fwd_54",
            "mut_fwd_nameerror", "mut_fwd_revert2", "mut_fwd_full",
            "mut_fwd_module", "mut_fwd_onlyfn", "mut_veto_missing",
            "mut_veto_always", "mut_ungate", "ungate_plugin",
            "mut_rawhost", "mut_fwdtail", "nolegs_plugin",
            "mut_www", "mut_www2", "mut_suffix", "mut_port",
            "mut_originport", "mut_startswith", "mut_secfetch",
            "mut_reject_all_null", "mut_no_declared_anchor",
            "mut_xfh_only", "mut_xfh_only_v2", "mut_novouch",
            "mut_nolegd",
        ]
    print(f"{'plugin':<26} {'F':>4} {'P':>4}  load  call  cascade")
    for plugin in plugins:
        try:
            res = run(plugin)
        except Exception as exc:  # noqa: BLE001
            print(f"{plugin:<26} ERROR {exc}")
            continue
        print(
            f"{res['plugin']:<26} {res['failed']:>4} {res['passed']:>4}"
            f"  {'YES' if res['load_canary'] else 'no ':>4}"
            f"  {'YES' if res['call_canary'] else 'no ':>4}"
            f"  {'YES' if res['cascade'] else 'no ':>6}"
        )
        for name in res["names"]:
            print(f"      - {name}")

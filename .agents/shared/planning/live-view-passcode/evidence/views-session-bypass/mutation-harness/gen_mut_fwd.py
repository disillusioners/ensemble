"""Generate /tmp/views-review/mut_fwd_unconstrained.py.

The mutant restores the PRE-FIX-10 ``_views_trusted_hosts`` (the
shape at 6398a29a3): the forwarded-derived values ANCHOR
unconditionally and there is NO agreement veto — i.e. exactly the
same-class bypass the tester found (anomaly 6c543ecc): a forged
``X-Forwarded-Host: evil.example`` + ``Origin:
https://evil.example`` self-matches and mints a full view_grant.

If FIX-10 is really pinned, K1/K2/K4 MUST fail under this mutant.
"""
from pathlib import Path
import re
import subprocess

def _repo_root() -> str:
    """Walk up to the repo root (the directory holding ``daemon/``)."""
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / "daemon" / "routers").is_dir():
            return str(candidate)
    return str(here.parents[6])

REPO = _repo_root()
OLD_REV = "6398a29a3"

old_src = subprocess.check_output(
    ["git", "-C", REPO, "show", f"{OLD_REV}:daemon/routers/live_views.py"],
    text=True,
)

start = old_src.index("def _views_trusted_hosts(request: Request)")
end = old_src.index("def _same_host_origin_check(request: Request)")
old_fn = old_src[start:end].rstrip() + "\n"

plugin = (
    '"""Mutation: restore UNCONSTRAINED forwarded trust (pre-FIX-10).\n\n'
    "Generated from the pre-FIX-10 blob at %s — the exact\n"
    "``_views_trusted_hosts`` that shipped in that commit: the\n"
    "forwarded-derived values (first X-Forwarded-Host value, first\n"
    "RFC 7239 Forwarded host= element) ANCHOR UNCONDITIONALLY inside\n"
    "the declared-front block, and there is NO agreement veto.\n\n"
    "K1/K2/K4 MUST fail under this mutant.\n"
    '"""\n'
    "import daemon.routers.live_views as lv\n\n\n"
    + old_fn
    + "\n\nlv._views_trusted_hosts = _views_trusted_hosts\n"
    'print("[mut_fwd_unconstrained] pre-FIX-10 forwarded trust RESTORED")\n'
)

with open(str(Path(__file__).resolve().parent / "mut_fwd_broken_cascade.py"), "w") as fh:
    fh.write(plugin)
print("written; old fn lines:", old_fn.count("\n"))

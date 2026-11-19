#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# fe_home_poll_interval_unit_test.sh
#
# sonic-tester verification wave, 2026-11-19 (commission instances-optd-fe-poll).
#
# WHY THIS PACK EXISTS (detection-power gap):
#   The FE half of the commission moves the home page's instance-list poll
#   from 10 s to 120 s (frontend/src/app/pages/home/home.component.ts:110).
#   The FE jest suite (home.component.spec.ts, 36 nodes) does NOT pin the
#   poll interval — it never touches setInterval / fake timers / the literal
#   — so a REVERT of the cadence (120000 -> 10000) leaves all 36 jest nodes
#   GREEN.  This pack closes that gap with a static source pin:
#
#     leg 1  exactly ONE setInterval call site in home.component.ts
#     leg 2  the interval literal at that call site is 120000
#     leg 3  NO other interval literal survives in the file (10000 is gone)
#     leg 4  the polling MECHANISM is intact (clearInterval teardown +
#            ngOnDestroy still present) — the cadence changed, the wiring did not
#
# USAGE:
#   bash test/packs/fe_home_poll_interval_unit_test.sh [TREE]
#   (TREE defaults to $PWD; it must contain
#    frontend/src/app/pages/home/home.component.ts)
#
# FENCES: read-only — the pack only READS the source file.  No npm, no jest,
#   no dev-server, no network, no daemon, no DB, no live-install contact.
# ---------------------------------------------------------------------------
set -euo pipefail

WT="${1:-$(pwd)}"
WT="$(cd "$WT" && pwd)"
SRC="$WT/frontend/src/app/pages/home/home.component.ts"

if [ ! -f "$SRC" ]; then
  echo "FATAL: no $SRC (pass the tree root as \$1)" >&2
  exit 2
fi

python3 - "$SRC" <<'PYFILE'
import re
import sys

src_path = sys.argv[1]
with open(src_path, encoding="utf-8") as fh:
    lines = fh.readlines()

text = "".join(lines)
fails = []


def leg(name, ok, detail=""):
    status = "PASS" if ok else "FAIL"
    print(f"{status}: {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        fails.append(name)


# ── leg 1: exactly ONE setInterval call site ────────────────────────────────
call_sites = [i for i, ln in enumerate(lines) if "setInterval(" in ln]
leg(
    "leg1 exactly one setInterval call site in home.component.ts",
    len(call_sites) == 1,
    f"found {len(call_sites)} at lines {[i + 1 for i in call_sites]}",
)

# ── leg 2: the interval literal at that call site is 120000 ─────────────────
literal = None
literal_line = None
if call_sites:
    start = call_sites[0]
    window = "".join(lines[start : start + 4])
    m = re.search(r"\}\s*,\s*([0-9_]+)\s*\)\s*;", window)
    if m:
        literal = m.group(1).replace("_", "")
        literal_line = start + window[: m.start()].count("\n") + 1
    else:
        # single-line form: setInterval(fn, 120000)
        m2 = re.search(r"setInterval\([^;]*?,\s*([0-9_]+)\s*\)", window)
        if m2:
            literal = m2.group(1).replace("_", "")
            literal_line = start + window[: m2.start()].count("\n") + 1
leg(
    "leg2 the poll interval literal is 120000 (120 s)",
    literal == "120000",
    f"literal={literal!r} at home.component.ts:{literal_line}",
)

# ── leg 3: NO other interval literal survives in the file ──────────────────
# 10000 (the pre-change 10 s cadence) must be gone; 120000 must appear once.
n_10000 = text.count("10000")
n_120000 = text.count("120000")
leg(
    "leg3 no stale 10 s interval literal remains (10000 absent, 120000 once)",
    n_10000 == 0 and n_120000 == 1,
    f"10000 x{n_10000}, 120000 x{n_120000}",
)

# ── leg 4: the polling MECHANISM is intact ──────────────────────────────────
leg("leg4a clearInterval teardown still present", "clearInterval(" in text)
leg(
    "leg4b ngOnDestroy lifecycle hook still present",
    "ngOnDestroy" in text,
)

print()
if fails:
    print(f"RESULT: FAIL ({len(fails)} leg(s) red: {', '.join(fails)})")
    sys.exit(1)
print("RESULT: PASS (4/4 legs green)")
PYFILE

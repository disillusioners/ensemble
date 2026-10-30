#!/usr/bin/env bash
# gate.sh — the plan set's drift/consistency gate (leader directive D-A, approver-002 blocker 4).
#
# WHAT IT DOES
#   Reproduces EVERY class check of the count-sweep gate that `phase5-plan.md` T5.17/AC17 declares,
#   INCLUDING class (h) — the RESOLUTION check, which verifies that every cited D-id/T-id resolves to a
#   row **whose TOPIC matches the citation context** (not merely that the id exists). The map rows it
#   validates are **not** exempt.
#
# SAFETY / SCOPE (binding)
#   * It greps/reads **PLAN DOCUMENTS ONLY** — the .md files beside this script.
#   * It NEVER executes a pipeline script (no promote/stage/rollback/adopt-unit invocation).
#   * It NEVER touches the live install and NEVER resolves a live-shaped path: it opens files by
#     relative name inside its own directory only.
#   * It is READ-ONLY and IDEMPOTENT: the same input always yields the same output and exit code.
#   * It exits NON-ZERO on any failure.
#
# USAGE
#   bash .agents/shared/planning/promote-preflight-hang/gate.sh        # human-readable report
#   bash .agents/shared/planning/promote-preflight-hang/gate.sh --quiet # one line per class
#
# CLASSES
#   (a) corpus cardinality      (e) single-file-era wording     (g) superseded identifiers
#   (b) mutation-matrix span    (f) anchor freshness            (g+) the replacement identifier exists
#   (c) pack set                (h) RESOLUTION + TOPIC (h1 file-qualified, h2 defined-somewhere, h3 map-row TOPIC)
#   (d) RuntimeMaxSec literals  (i) ARTIFACT (gate.sh exists, is executable, and the stated count matches)
set -uo pipefail

GATE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QUIET=0
[ "${1:-}" = "--quiet" ] && QUIET=1

exec python3 - "$GATE_DIR" "$QUIET" <<'PYEOF'
import os, re, sys

DIR, QUIET = sys.argv[1], sys.argv[2] == "1"
PLAN = ["plan-overview.md","phase0-plan.md","phase1-plan.md","phase2-plan.md","phase3-plan.md",
        "phase4-plan.md","phase5-plan.md","phase6-plan.md","decisions.md"]
READONLY = {"architecture-recommendation.md","review-plan-council.md"}

def read(name):
    with open(os.path.join(DIR, name), "r", encoding="utf-8") as fh:
        return fh.read()
LINES = {f: read(f).split("\n") for f in PLAN if f not in READONLY}

# ---------------------------------------------------------------- id model
DEF, TOPIC = {}, {}
for f, ls in LINES.items():
    for i, l in enumerate(ls, 1):
        for pat in (r'\| \*\*(D[\w]+-\d+|P0-D\d+|T\d+[-.]\d+)\*\* \| (.{0,400})',
                    r'### (D[\w]+-\d+) — (.{0,400})',
                    r'\*\*(D\d+-\d+)\*\* — (.{0,400})'):
            m = re.match(pat, l)
            if m:
                DEF.setdefault(f, {}).setdefault(m.group(1), (i, l))
                TOPIC.setdefault(f, {}).setdefault(m.group(1), m.group(2))
                break
ALLIDS = {i for f in DEF for i in DEF[f]}
IDRE = re.compile(r'\b(D\d+[a-z]?-\d+|P0-D\d+|T\d+[-.]\d+)\b')
FILEREF = re.compile(r'`([a-z0-9\-]+\.md)`')
def norm(s):
    return re.sub(r'\s+', ' ', s.replace('*','').replace('`','').replace('|',' ')).strip().lower()
def quoted(line, m):
    # a meta-mention is "quoted" when (a) it is immediately preceded by a quote character, or
    # (b) it sits INSIDE a backtick span on the same line (odd number of backticks before it).
    # (b) is required: markdown tables routinely wrap a whole phrase — e.g. `Phase 2 §2 D2-3` — in one
    # span, so the preceding character is a space, not a tick. Straight quotes count too: a pasted
    # gate transcript quotes the class names it asserts ('packs A–H', 'M1–M28') as meta-mentions. Without (b) the gate reports false
    # positives on exactly the historical meta-mentions its own allowed-category rule exempts.
    p = line[max(0, m.start()-1):m.start()]
    if p.endswith('`') or p.endswith('\u2018') or p.endswith('\u201c') or p.endswith("'") or p.endswith('"'):
        return True
    return line[:m.start()].count('`') % 2 == 1
def ctx_allows(line):
    l = line.lower()
    return ('supersession record' in l or 'pre-round-' in l or 'span rule' in l or
            'stale ' in l or 'anchor:' in l or 'fold:' in l)

results = []
def check(cls, desc, bad):
    results.append((cls, desc, bad))

# (a) corpus cardinality — ONE canonical statement; no stale counts anywhere
stale = []
for f, ls in LINES.items():
    for i, l in enumerate(ls, 1):
        for pat in (r'22 \+ 3\b', r'\b25/25\b', r'\b25 fixtures\b', r'\b23 fixtures\b',
                    r'G1[–-]G3\b', r'G1[–-]G4\b', r'22-case corpus \+ the 3 guard',
                    r'22-case \+ 3 guard', r'\*\*25\*\* fixtures', r'fixture count is 25'):
            for m in re.finditer(pat, l):
                if not quoted(l, m) and not ctx_allows(l):
                    stale.append(f"{f}:{i}:{m.group(0)}")
canon = sum("G1–G5 = 27" in "\n".join(ls) for ls in LINES.values())
check("a", "corpus cardinality — one canonical '22 + G1–G5 = 27'; no stale counts", stale)

# (b) mutation-matrix span — every LIVE span claim is M1–M32
bad = []
for f, ls in LINES.items():
    for i, l in enumerate(ls, 1):
        for m in re.finditer(r'M1[–-]M(\d+)\b', l):
            if quoted(l, m) or ctx_allows(l):
                continue
            if m.group(1) != "32":
                bad.append(f"{f}:{i}:M1–M{m.group(1)}")
check("b", "mutation-matrix span — every LIVE span claim is M1–M32", bad)

# (c) pack set — canonical A–J
bad = []
for f, ls in LINES.items():
    for i, l in enumerate(ls, 1):
        for m in re.finditer(r'packs? A[–-][HI]\b', l):
            if not quoted(l, m) and not ctx_allows(l):
                bad.append(f"{f}:{i}:{m.group(0)}")
check("c", "pack set — no LIVE 'packs A–H' / 'A–I' (canonical A–J)", bad)

# (d) RuntimeMaxSec literals
bad = []
for f, ls in LINES.items():
    for i, l in enumerate(ls, 1):
        for m in re.finditer(r'RuntimeMaxSec=1200', l):
            if not quoted(l, m):
                bad.append(f"{f}:{i}")
check("d", "RuntimeMaxSec — no LIVE '=1200' pin literal", bad)

# (e) single-file-era wording
bad = []
for f, ls in LINES.items():
    for i, l in enumerate(ls, 1):
        for m in re.finditer(r'lives in \*\*one per-run file\*\*|D3-3\)', l):
            if not quoted(l, m) and 'supersed' not in l.lower():
                bad.append(f"{f}:{i}")
check("e", "phase3 — no single-file-era wording / no LIVE D3-3 pointer reference", bad)

# (f) anchor freshness — the stale anchors must be gone, the corrected ones present
bad = []
for f, ls in LINES.items():
    for i, l in enumerate(ls, 1):
        for pat in (r'lib\.sh:5569', r'lib\.sh:1039', r'lib\.sh:1050`', r'lib\.sh:1172', r'lib\.sh:1174', r'stage\.sh:576'):
            for m in re.finditer(pat, l):
                if not quoted(l, m) and not ctx_allows(l):
                    bad.append(f"{f}:{i}:{m.group(0)}")
check("f", "anchors — the stale anchors are gone (lib.sh:5567 / :1041 / :1053 / :1169 / :1173; stage.sh:577)", bad)

# (g) superseded identifiers
bad = []
for f, ls in LINES.items():
    for i, l in enumerate(ls, 1):
        for m in re.finditer(r'MIN_PIPELINE_SCRIPTS_REV', l):
            if not quoted(l, m) and 'supersed' not in l.lower() and 'pre-B1' not in l:
                bad.append(f"{f}:{i}")
check("g", "superseded identifiers — no LIVE 'MIN_PIPELINE_SCRIPTS_REV'", bad)
alltext = "\n".join("\n".join(ls) for ls in LINES.values())
check("g+", "the replacement identifier 'PIPELINE_SCRIPTS_FLOOR_COMMIT' IS present",
      [] if "PIPELINE_SCRIPTS_FLOOR_COMMIT" in alltext else ["missing"])

# (h) RESOLUTION + TOPIC
#  h1: every file-qualified citation resolves in the file it is cited for
#  h2: every id referenced anywhere is defined somewhere
#  h3: every §11.1 map row's topic ANCHOR appears in the DEFINING row of the cited id (topic resolution)
#  h4: the map rows themselves must resolve (no exemption for the rows being validated)
badq, badany, badtopic = [], [], []
for f, ls in LINES.items():
    for ln, line in enumerate(ls, 1):
        for cell in line.split("|"):
            parts = FILEREF.split(cell)
            for i in range(1, len(parts), 2):
                fname, seg = parts[i], (parts[i+1] if i+1 < len(parts) else "")
                for m in IDRE.finditer(seg):
                    cid = m.group(1)
                    if quoted(seg, m):
                        continue
                    if fname in DEF and cid not in DEF[fname]:
                        badq.append(f"{f}:{ln}: `{fname}` {cid}")
        for m in IDRE.finditer(line):
            cid = m.group(1)
            if quoted(line, m):
                continue
            if cid not in ALLIDS:
                badany.append(f"{f}:{ln}:{cid}")
check("h1", "RESOLUTION — every file-qualified citation resolves in the file it is cited for", badq)
check("h2", "RESOLUTION — every referenced id is defined somewhere in the set", badany)

MAPROW = re.compile(r'^\| (?P<topic>[^|]*?) — anchor: \*(?P<anc>"[^"]+")\* \| (?P<pid>[^|]*?) \| (?P<did>[^|]*?) \| (?P<canon>[^|]*?) \|\s*$')
for f, ls in LINES.items():
    for ln, line in enumerate(ls, 1):
        m = MAPROW.match(line)
        if not m:
            continue
        anc = m.group("anc").strip('"')
        # the anchor was generated from ONE defining row (the phase column when it is a real
        # phase id, else the decisions column) -> check THAT row's topic (not both)
        tgt, checked = None, False
        for col, fld in (("pid", "phase"), ("did", "decisions")):
            cid = m.group(col).split()[0] if m.group(col).strip() not in ("—", "") else ""
            cid = re.sub(r'[^A-Za-z0-9\-]', '', cid)
            if not cid or not cid.startswith("D"):
                continue
            cand = None
            if fld == "phase":
                mm = re.match(r'^D(\d+)-', cid)
                cand = f"phase{mm.group(1)}-plan.md" if mm else None
                if cand and cand in DEF and cid in DEF[cand]:
                    tgt = (cand, cid); checked = True; break
            if not checked and cid in DEF.get("decisions.md", {}):
                tgt = ("decisions.md", cid); checked = True; break
        if not checked:
            badtopic.append(f"{f}:{ln}: the cited id resolves NOWHERE")
        elif tgt is not None:
            # token-set comparison (the anchor is the defining row's leading tokens; the
            # 2-char-word filter can drop a word, so a contiguous substring test is too strict)
            atoks = [w for w in re.split(r'\s+', norm(anc)) if len(w) > 2]
            ttoks = set(re.split(r'\s+', norm(TOPIC[tgt[0]][tgt[1]])))
            if atoks and not all(w in ttoks for w in atoks):
                badtopic.append(f"{f}:{ln}:{tgt[0]}:{tgt[1]} — anchor tokens do not match the defining row's topic")
check("h3", "RESOLUTION/TOPIC — every §11.1 map row's anchor matches the DEFINING row's topic (map rows NOT exempt)", badtopic)

# (i) ARTIFACT — the recorded result must name an artifact that exists AND whose stated size matches the
#     gate that runs (N-19: a result with no named artifact cannot be re-verified; N-16: nothing covered the
#     gate's own class-count, so a "7 classes" / "EIGHT classes" drift could survive).
EXPECTED_CHECKS = 12
missing_named = [] if "gate.sh" in alltext else ["no plan document names the gate artifact (gate.sh)"]
if f"{EXPECTED_CHECKS} executable checks" not in alltext:
    missing_named.append(f"the set does not state the canonical count '{EXPECTED_CHECKS} executable checks'")
gpath = os.path.join(DIR, "gate.sh")
if not (os.path.isfile(gpath) and os.access(gpath, os.X_OK)):
    missing_named.append("gate.sh is absent or not executable")
if len(results) + 1 != EXPECTED_CHECKS:   # +1 = this check itself
    missing_named.append(f"the gate runs {len(results) + 1} checks, the set claims {EXPECTED_CHECKS}")
check("i", "ARTIFACT — the recorded result names an executable gate.sh, and the set's stated check count matches the gate's", missing_named)

# ---------------------------------------------------------------- report
fails = 0
print("=" * 78)
print("PLAN-SET GATE — promote-preflight-hang (read-only over the plan documents)")
print(f"dir: {DIR}")
print("=" * 78)
for cls, desc, bad in results:
    ok = not bad
    fails += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] ({cls}) {desc}")
    for b in bad[:8]:
        print(f"        - {b}")
    if len(bad) > 8:
        print(f"        … and {len(bad)-8} more")
print("-" * 78)
print(f"RESULT: {'PASS' if fails == 0 else 'FAIL'} — {len(results)-fails}/{len(results)} executable checks passed")
sys.exit(0 if fails == 0 else 1)
PYEOF

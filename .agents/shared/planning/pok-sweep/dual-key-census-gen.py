#!/usr/bin/env python3
# PROVENANCE (committed copy, fix iteration 2): this generator produced the
# committed artifact `.agents/shared/planning/pok-sweep/dual-key-census-2026-11-18.md`
# from `/tmp/pok_census_raw_20261118.json` (sha256 d8a6e80b…, 2026-11-18).
# No DB access, no network, no body text read.
"""POK dual-key family census generator — 2026-11-18.

Reads the read-only census snapshot (no DB access) and emits the
corrected-family-key blast-radius map artifact.

python3 stdlib only. No DB access. No network. No hand-typed ids/slugs:
every id/slug below is copied machine-side out of the snapshot JSON.
"""

import hashlib
import json
import os
import re
import sys
from collections import defaultdict

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
SRC = "/tmp/pok_census_raw_20261118.json"
EXPECT_SHA = "d8a6e80bbc417530ac6d0e1ea8ba454e6fe3314b7a49e0e02b041054360b8647"
EXPECT_SIZE = 362415
EXPECT_CAPTURED_AT = "2026-11-18T20:57:11Z"

OUT_PATH = (
    "/home/nea/ensemble-src-wt-pok-family-key/.agents/shared/planning/"
    "pok-sweep/dual-key-census-2026-11-18.md"
)
SCRATCH = os.path.dirname(os.path.abspath(__file__))
SIDECAR = os.path.join(SCRATCH, "dual_key_census.json")

# CURRENT view = the LIVE single-token family key
#   daemon/repositories/pok/models.py:131 POK_FAMILY_SLUG_PATTERN
CURRENT_RE = re.compile(r"^([a-z][a-z0-9]*)-(\d{2})-(.+)$")
# CORRECTED view = the documented multi-token intent
CORRECTED_RE = re.compile(r"^([a-z][a-z0-9]*(?:-[a-z][a-z0-9]*)*)-(\d{2})-(.+)$")
TOKEN_RE = re.compile(r"^[a-z][a-z0-9]*$")
ORD_RE = re.compile(r"^\d{2}$")

POK_FAMILY_MAX = 3

EXPECTED_TOTALS = {
    "83da04de-a410-4fb5-9e92-251a99d28a52": (282, 34, 83),
    "54debc61-f61c-4a59-bc02-2310e560a3a9": (217, 8, 25),
    "d5ef370a-d517-476e-9cbd-ce59b7229e8c": (7, 0, 1),
}
FOCUS_PROJECT = "83da04de-a410-4fb5-9e92-251a99d28a52"

# PM dual-key headroom table (family-cap-consolidation-2026-11-18.md,
# post-wave 18:36Z) — numbered-regex census view / daemon prefix view.
PM_HEADROOM = {
    "prompt-assent": (4, 5),
    "snapshot-l3": (3, 3),
    "git": (2, 4),
}
PM_WAVE_UTC = "2026-11-18T18:36:00"

# PM-stated member composition, transcribed from
# family-cap-consolidation-2026-11-18.md ("Family-cap headroom" table +
# the per-family sections). Used only to DIFF the PM ledger against the
# census; the census is authoritative.
PM_PREFIX_SLUGS = {
    "prompt-assent": [
        "prompt-assent-01-gate-owner-and-evidence",
        "prompt-assent-02-category-registration-seam",
        "prompt-assent-03-paths-use-assent-not-assist",
        "prompt-assent-04-dm-shield-tool-gate",
        "prompt-assent-flip-on-gate",
    ],
    "git": [
        "git-01-canonical-detached-diff-trap",
        "git-01-commit-msg-before-dashdash",
        "git-sha-transcription-discipline",
        "git-update-ref-worktree-index-stale",
    ],
}
PM_ROW_IDS = {
    "git-01-canonical-detached-diff-trap": "cb7dea97",
    "git-01-commit-msg-before-dashdash": "fb0cc19f",
    "git-sha-transcription-discipline": "48b2c876",
    "git-update-ref-worktree-index-stale": "b02c4f39",
}

FAILS = []


def check(cond, msg):
    """Hard assert — abort the whole run on failure (no partial artifact)."""
    if not cond:
        FAILS.append(msg)
        raise SystemExit("ASSERT FAILED: " + msg)


def fam_current(slug):
    m = CURRENT_RE.match(slug)
    return m.group(1) if m else None


def fam_corrected(slug):
    m = CORRECTED_RE.match(slug)
    return m.group(1) if m else None


def tail_corrected(slug):
    m = CORRECTED_RE.match(slug)
    return m.group(3) if m else None


def ref_extract(slug):
    """Independent reference: leftmost -NN- ordinal, family = the maximal
    run of letter-led tokens in front of it. Used to cross-check the
    (greedy) CORRECTED regex against the literal spec wording."""
    segs = slug.split("-")
    for i in range(1, len(segs)):
        if ORD_RE.match(segs[i]):
            fam = segs[:i]
            tail = segs[i + 1:]
            if not tail or not all(TOKEN_RE.match(t) for t in fam):
                return None
            return ("-".join(fam), segs[i], "-".join(tail))
    return None


# --------------------------------------------------------------------------
# STEP 0 — verify the input snapshot
# --------------------------------------------------------------------------
raw = open(SRC, "rb").read()
actual_sha = hashlib.sha256(raw).hexdigest()
check(actual_sha == EXPECT_SHA,
      "snapshot sha256 mismatch: got %s expected %s" % (actual_sha, EXPECT_SHA))
check(len(raw) == EXPECT_SIZE,
      "snapshot size mismatch: got %d expected %d" % (len(raw), EXPECT_SIZE))
print("STEP0 sha256 OK: %s (%d bytes)" % (actual_sha, len(raw)))

doc = json.loads(raw.decode("utf-8"))
rows = doc["rows"]
check(doc["row_count"] == len(rows), "row_count != len(rows)")
check(doc["sha256_verified_count"] == len(rows),
      "sha256_verified_count != len(rows)")
for r in rows:
    check(re.fullmatch(r"[0-9a-f]{64}", r["content_sha256"]),
          "row %s has malformed content_sha256" % r.get("id"))
    check("body" not in r and "body_text" not in r, "row unexpectedly carries a body")

captured_at = doc["captured_at"]
check(captured_at.startswith("2026-11-18T20:57:11"), "captured_at drifted: %s" % captured_at)

# --------------------------------------------------------------------------
# STEP 1 — mandated regex assertions
# --------------------------------------------------------------------------
check(fam_corrected("snapshot-l3-01-x") == "snapshot-l3",
      "corrected(snapshot-l3-01-x) != snapshot-l3")
check(fam_corrected("prompt-assent-01-y") == "prompt-assent",
      "corrected(prompt-assent-01-y) != prompt-assent")
check(fam_corrected("verify-03-jsonpath-null-trap") == "verify",
      "corrected(verify-03-jsonpath-null-trap) != verify")
check(fam_corrected("model-catalogs-not-drift") is None,
      "corrected(model-catalogs-not-drift) is not None")
check(fam_corrected("x-01-99-foo") == "x",
      "corrected(x-01-99-foo) != x")
check(tail_corrected("x-01-99-foo") == "99-foo",
      "corrected tail(x-01-99-foo) != 99-foo")
check(fam_current("snapshot-l3-01-x") is None,
      "current(snapshot-l3-01-x) is not None")
check(fam_current("prompt-assent-01-y") is None,
      "current(prompt-assent-01-y) is not None")
print("STEP1 mandated regex asserts OK")

# --------------------------------------------------------------------------
# STEP 1b — per-project totals reconciliation
# --------------------------------------------------------------------------
status_counts = defaultdict(int)
for entry in doc["project_status_counts"]:
    status_counts[(entry["project_id"], entry["status"])] = entry["n"]

active_by_project = defaultdict(int)
for r in rows:
    active_by_project[r["project_id"]] += 1

recon = []
for pid in (FOCUS_PROJECT,
            "54debc61-f61c-4a59-bc02-2310e560a3a9",
            "d5ef370a-d517-476e-9cbd-ce59b7229e8c"):
    exp_a, exp_ar, exp_s = EXPECTED_TOTALS[pid]
    got_a = active_by_project[pid]
    got_ar = status_counts[(pid, "archived")]
    got_s = status_counts[(pid, "superseded")]
    check(got_a == exp_a,
          "active count mismatch for %s: computed %d expected %d (delta %+d)"
          % (pid, got_a, exp_a, got_a - exp_a))
    check(got_ar == exp_ar,
          "archived count mismatch for %s: computed %d expected %d"
          % (pid, got_ar, exp_ar))
    check(got_s == exp_s,
          "superseded count mismatch for %s: computed %d expected %d"
          % (pid, got_s, exp_s))
    recon.append((pid, got_a, exp_a, got_ar, exp_ar, got_s, exp_s))

check(len(rows) == 506, "active total mismatch: %d != 506" % len(rows))
check(sum(active_by_project.values()) == 506, "sum(active_by_project) != 506")
print("STEP1b totals reconciled OK (506 active / 3 projects)")

# --------------------------------------------------------------------------
# STEP 1c — regex vs independent leftmost-ordinal reference (all slugs)
# --------------------------------------------------------------------------
for r in rows:
    slug = r["slug"]
    ref = ref_extract(slug)
    ref_fam = ref[0] if ref else None
    cor = fam_corrected(slug)
    check(cor == ref_fam,
          "corrected/reference disagreement on %r: regex=%r ref=%r"
          % (slug, cor, ref_fam))
    if cor is not None:
        check(tail_corrected(slug) == ref[2],
              "tail disagreement on %r" % slug)
    cur = fam_current(slug)
    if cur is not None:
        # Theorem: for every row gated under the current key, the corrected
        # key yields the SAME family (a current-family "differs" case cannot
        # occur for a gated row).
        check(cor == cur,
              "current-gated row %r has differing families: cur=%r cor=%r"
              % (slug, cur, cor))
print("STEP1c regex == leftmost-ordinal reference on all %d slugs" % len(rows))

# Typo-guard for the hand-transcribed PM ledger constants: every slug and
# every family key must exist verbatim in the snapshot.
_all_slugs = {r["slug"] for r in rows}
_all_fams = {fam_corrected(r["slug"]) for r in rows} | {fam_current(r["slug"]) for r in rows}
for _fam, _slugs in PM_PREFIX_SLUGS.items():
    check(_fam in _all_fams, "PM_PREFIX_SLUGS family key %r is not a census family" % _fam)
    for _s in _slugs:
        check(_s in _all_slugs, "PM_PREFIX_SLUGS slug %r is not in the census" % _s)
for _slug in PM_ROW_IDS:
    check(_slug in _all_slugs, "PM_ROW_IDS slug %r is not in the census" % _slug)
print("STEP1d PM-ledger constants verified against the census (typo-guard OK)")

# --------------------------------------------------------------------------
# STEP 2 — family views
# --------------------------------------------------------------------------
by_project = defaultdict(list)
for r in rows:
    by_project[r["project_id"]].append(r)

# family -> view stats
FAMILIES = {}          # (pid, family) -> dict
for pid in EXPECTED_TOTALS:
    for r in by_project[pid]:
        for view, fn in (("cur", fam_current), ("cor", fam_corrected)):
            f = fn(r["slug"])
            if f is None:
                continue
            key = (pid, f)
            st = FAMILIES.setdefault(key, {
                "cur": {"numbered": [], "prefix": []},
                "cor": {"numbered": [], "prefix": []},
            })
            st[view]["numbered"].append(r)
    # prefix rows (view-independent: startswith(family-))
    for r in by_project[pid]:
        for (p2, f), st in list(FAMILIES.items()):
            if p2 != pid:
                continue
            if r["slug"].startswith(f + "-"):
                st["cur"]["prefix"].append(r)
                st["cor"]["prefix"].append(r)

all_families = {}
for pid in EXPECTED_TOTALS:
    fams = sorted({f for (p, f) in FAMILIES if p == pid})
    for f in fams:
        st = FAMILIES[(pid, f)]
        all_families[(pid, f)] = {
            "cur_numbered": len(st["cur"]["numbered"]),
            "cur_prefix": len(st["cur"]["prefix"]),
            "cor_numbered": len(st["cor"]["numbered"]),
            "cor_prefix": len(st["cor"]["prefix"]),
            "cor_numbered_members": sorted(
                (r["id"], r["slug"]) for r in st["cor"]["numbered"]),
            "cur_numbered_members": sorted(
                (r["id"], r["slug"]) for r in st["cur"]["numbered"]),
            "prefix_members": sorted(
                (r["id"], r["slug"]) for r in st["cor"]["prefix"]),
            "prefix_unnumbered": sorted(
                (r["id"], r["slug"], fam_corrected(r["slug"]))
                for r in st["cor"]["prefix"]
                if fam_corrected(r["slug"]) != f),
            "multi_token": ("-" in f),
        }

# Theorem checks on the computed tables
for (pid, f), d in all_families.items():
    if d["cur_numbered"] > 0:
        check(d["cur_numbered"] == d["cor_numbered"],
              "single-token family %s/%s: cur=%d != cor=%d"
              % (pid, f, d["cur_numbered"], d["cor_numbered"]))
    check(d["cur_prefix"] == d["cor_prefix"],
          "prefix_count must be view-independent for %s/%s" % (pid, f))
    check(d["cur_numbered"] <= d["cur_prefix"],
          "numbered > prefix for %s/%s" % (pid, f))
print("STEP2 family views computed: %d (project, family) pairs" % len(all_families))

# --------------------------------------------------------------------------
# STEP 3 — blast radius
# --------------------------------------------------------------------------
# (a) families with corrected numbered count >= 3
at_or_over = []
for (pid, f), d in all_families.items():
    if d["cor_numbered"] >= POK_FAMILY_MAX:
        at_or_over.append((pid, f, d))

# (b) NEW-refusal families: corrected numbered >= 3 AND (ungated under
#     CURRENT view OR current-family differs)
new_refusal = []
for pid, f, d in at_or_over:
    differs = False
    for _rid, slug in d["cor_numbered_members"]:
        if fam_current(slug) != f:
            differs = True
    if differs:
        new_refusal.append((pid, f, d))

# (c) unchanged gating: single-token families
single_token = [(pid, f, d) for (pid, f), d in all_families.items()
                if not d["multi_token"]]
for pid, f, d in single_token:
    check(d["cor_numbered"] == d["cur_numbered"],
          "UNCHANGED-GATING assert failed for %s/%s" % (pid, f))
for pid, f, d in new_refusal:
    check(d["cor_numbered"] >= POK_FAMILY_MAX,
          "new-refusal family %s/%s below bound" % (pid, f))

# (d) unnumbered-prefix rows counted toward prefix_count, not gated themselves
ungated_prefix_rows = []
for (pid, f), d in sorted(all_families.items()):
    for rid, slug, ex in d["prefix_unnumbered"]:
        ungated_prefix_rows.append((pid, f, rid, slug, ex))

# PM headroom cross-check (focus project)
pm_rows = []
for fam, (pm_n, pm_p) in PM_HEADROOM.items():
    d = all_families.get((FOCUS_PROJECT, fam))
    our_n = d["cor_numbered"] if d else 0
    our_p = d["cor_prefix"] if d else 0
    members = []
    if d:
        for rid, slug in d["cor_numbered_members"]:
            row = next(x for x in rows if x["id"] == rid)
            members.append({
                "id": rid, "slug": slug,
                "created_at": row["created_at"], "updated_at": row["updated_at"],
                "created_by_agent": row["created_by_agent"],
                "created_in_window": row["created_at"] > PM_WAVE_UTC,
                "updated_in_window": row["updated_at"] > PM_WAVE_UTC,
            })
    extra_prefix = []
    if d:
        for rid, slug, ex in d["prefix_unnumbered"]:
            row = next(x for x in rows if x["id"] == rid)
            extra_prefix.append({
                "id": rid, "slug": slug, "extraction": ex,
                "created_at": row["created_at"], "updated_at": row["updated_at"],
                "created_in_window": row["created_at"] > PM_WAVE_UTC,
                "updated_in_window": row["updated_at"] > PM_WAVE_UTC,
            })
    pm_rows.append({
        "family": fam, "pm_numbered": pm_n, "pm_prefix": pm_p,
        "our_numbered": our_n, "our_prefix": our_p,
        "delta_numbered": our_n - pm_n, "delta_prefix": our_p - pm_p,
        "members": members, "prefix_extra": extra_prefix,
        "census_prefix_slugs": ([slug for _rid, slug in d["cor_numbered_members"]]
                                + [slug for _rid, slug, _ex in d["prefix_unnumbered"]]
                                if d else []),
        "pm_prefix_slugs": PM_PREFIX_SLUGS.get(fam, []),
        "id_diff": [(slug, PM_ROW_IDS[slug],
                     next((rid for rid, s in d["cor_numbered_members"] if s == slug),
                          next((rid for rid, s, _e in d["prefix_unnumbered"] if s == slug), None)))
                    for slug in PM_ROW_IDS
                    if d and slug in ([s for _r, s in d["cor_numbered_members"]]
                                      + [s for _r, s, _e in d["prefix_unnumbered"]])],
    })

# window rows (created after the PM wave) for attribution narrative
window_rows = []
for r in rows:
    if r["project_id"] == FOCUS_PROJECT and r["created_at"] > PM_WAVE_UTC:
        window_rows.append({
            "id": r["id"], "slug": r["slug"], "created_at": r["created_at"],
            "created_by_agent": r["created_by_agent"],
            "cor_family": fam_corrected(r["slug"]),
            "cur_family": fam_current(r["slug"]),
        })
window_rows.sort(key=lambda x: x["created_at"])

# --------------------------------------------------------------------------
# STEP 3 — artifact rendering
# --------------------------------------------------------------------------
L = []
w = L.append

w("# POK Dual-Key Family Census — 2026-11-18 (corrected family-key blast-radius map)")
w("")
w("Point-in-time, script-generated map of how the POK family key changes when the")
w("**single-token** family pattern is replaced by the **corrected multi-token** pattern.")
w("It answers one question for the PM: *which families would newly hit the")
w("`POK_FAMILY_MAX` refusal if the corrected key were adopted, and which are unaffected.*")
w("")
w("Sibling artifacts in this directory (`family-cap-consolidation-2026-11-18.md`,")
w("`census-active-2026-11-18.md` / `.json`, `phase4-recensus-2026-11-18.md`,")
w("`cluster-proposals-2026-11-18.md`) were read for convention and were NOT modified.")
w("")

w("## 0. Provenance & method")
w("")
w("- **generated_at (UTC):** %s" % "2026-11-18T21:12:38Z")
w("- **source snapshot:** `%s`" % SRC)
w("- **snapshot sha256:** `%s`" % actual_sha)
w("- **snapshot size:** %d bytes" % len(raw))
w("- **captured_at (store):** %s (source field: `%s`)" % (EXPECT_CAPTURED_AT, captured_at))
w("- **source database / table:** `%s` / `%s` (read-only: `%s`)"
  % (doc["database"], doc["table"], doc["read_only_mode"]))
w("- **row count:** %d ACTIVE rows across 3 projects; per-row `content_sha256` pre-verified %d/%d"
  % (len(rows), doc["sha256_verified_count"], len(rows)))
w("- **no body text in the snapshot:** the snapshot carries metadata only (slug, id, timestamps, `content_sha256`, `body_len`); no POK body was read, so no body is reproduced here.")
w("- **method:** script-generated (python3 stdlib) from the read-only snapshot — no DB access, no network, no `pok_*` tool calls. Every id and slug below is copied machine-side out of the snapshot JSON; **no id/slug was retyped by hand**.")
w("- **generator scratch dir (uncommitted, audit):** `%s`" % SCRATCH)
w("- **no live systems touched:** this lane performed no POK writes, no git push, and no source-code change; the single repo write is the commit of this artifact.")
w("")
w("### 0.1 The two views")
w("")
w("| view | family-key regex | source |")
w("|---|---|---|")
w("| **CURRENT** | `%s` | live pattern — `daemon/repositories/pok/models.py:131` (`POK_FAMILY_SLUG_PATTERN`), consumed by `daemon/services/pok_service.py:129` (`POK_FAMILY_RE`), `daemon/services/pok_injection.py:139` |"
  % CURRENT_RE.pattern)
w("| **CORRECTED** | `%s` | the documented multi-token intent (maximal run of letter-led `[a-z][a-z0-9]*` tokens before the leftmost `-NN-` ordinal) |"
  % CORRECTED_RE.pattern)
w("")
w("Bound + gate semantics (read from source, unchanged by this lane):")
w("")
w("- `daemon/services/pok_service.py:117` — `POK_FAMILY_MAX = 3`.")
w("- `daemon/services/pok_service.py:471-479` — on admission, `family = _slug_family(slug)`; if the family is not `None` and `count_active_in_family(project_id, family) >= POK_FAMILY_MAX` the write is refused with `pok_family_full`.")
w("- `daemon/repositories/pok/repository.py:221-229` — `count_active_in_family` counts ACTIVE rows whose slug `startswith(f\"{family}-\")`, i.e. **unnumbered prefix rows count toward the bound**.")
w("- `daemon/services/pok_service.py:538-541` — the supersede path is exempt from `pok_family_full` (a same-slug supersede is count-preserving).")
w("")
w("**Reading the numbers.** `prefix n` is a function of the family NAME only (it is a")
w("`startswith` count), so it is identical in both views; the divergence lives in")
w("`numbered n` (how many rows the view actually GATES) and in the family SET itself.")
w("A family that is multi-token has `numbered n = 0` under CURRENT — it is not gated at")
w("all today, so no admission ever consults its prefix count.")
w("")

# ---- Section 1: totals
w("## 1. Per-project totals (reconciliation)")
w("")
w("Computed from the snapshot (ACTIVE rows enumerated) against the expected store")
w("totals. Every cell below was asserted inside the generator; a mismatch aborts the")
w("run before the artifact is written.")
w("")
w("| project_id | active (computed) | active (expected) | archived | superseded | verdict |")
w("|---|---|---|---|---|---|")
for pid, ga, ea, gar, ear, gs, es in recon:
    w("| `%s` | %d | %d | %d | %d | MATCH |" % (pid, ga, ea, gar, gs))
w("| **TOTAL** | **%d** | **506** | **42** | **109** | **MATCH** |"
  % sum(active_by_project.values()))
w("")
w("- Active total enumerated = **%d** == snapshot `row_count` == `sha256_verified_count` == 506." % len(rows))
w("- Archived/superseded counts are taken from the snapshot's `project_status_counts` block and reconciled against the expected per-project values.")
w("- Distinct ACTIVE rows carry no `status` field (the snapshot is already status-filtered to `active`); the archived/superseded legs are reconciled from the counts block, not re-derived from rows.")
w("- **Zero active-count mismatch, zero delta.** The focus project `83da04de…` holds %d ACTIVE rows."
  % active_by_project[FOCUS_PROJECT])
w("")

# ---- Section 2: dual-view family tables
w("## 2. Dual-view family tables")
w("")
w("One table per project. `cur n` = rows the CURRENT key gates into the family;")
w("`cur prefix` / `cor prefix` = ACTIVE rows whose slug starts with `<family>-`")
w("(the `count_active_in_family` count, unnumbered rows included); `cor n` = rows the")
w("CORRECTED key gates into the family; `delta` = `cor n - cur n`.")
w("`multi` marks a family whose name contains a hyphen — such a family is UNGATED under")
w("the current key.")
w("")
for pid in (FOCUS_PROJECT,
            "54debc61-f61c-4a59-bc02-2310e560a3a9",
            "d5ef370a-d517-476e-9cbd-ce59b7229e8c"):
    fams = sorted([f for (p, f) in all_families if p == pid],
                  key=lambda f: (-all_families[(pid, f)]["cor_numbered"],
                                 -all_families[(pid, f)]["cor_prefix"], f))
    w("### 2.%d project `%s`" % (list(EXPECTED_TOTALS).index(pid) + 1, pid))
    w("")
    w("ACTIVE rows: %d | families under CORRECTED: %d | families under CURRENT: %d"
      % (active_by_project[pid],
         len([f for f in fams if all_families[(pid, f)]["cor_numbered"] > 0]),
         len([f for f in fams if all_families[(pid, f)]["cur_numbered"] > 0])))
    w("")
    w("| family | cur n | cur prefix | cor n | cor prefix | delta (cor-cur) | multi |")
    w("|---|---|---|---|---|---|---|")
    for f in fams:
        d = all_families[(pid, f)]
        w("| `%s` | %d | %d | %d | %d | %+d | %s |"
          % (f, d["cur_numbered"], d["cur_prefix"], d["cor_numbered"],
             d["cor_prefix"], d["cor_numbered"] - d["cur_numbered"],
             "yes" if d["multi_token"] else "no"))
    w("")

# ---- Section 3: blast radius
w("## 3. Blast radius vs `POK_FAMILY_MAX = 3` (CORRECTED view, all 3 projects)")
w("")
w("### 3a. Families at or over the bound (corrected numbered count >= 3)")
w("")
w("Exact member ids + slugs, machine-copied. `marker` = AT (== 3) or OVER (> 3).")
w("`prefix n` is the `count_active_in_family` count that the live gate would use.")
w("")
if at_or_over:
    w("| project | family | corrected n | marker | prefix n | member id | member slug |")
    w("|---|---|---|---|---|---|---|")
    for pid, f, d in sorted(at_or_over, key=lambda x: (-x[2]["cor_numbered"],
                                                        -x[2]["cor_prefix"], x[0], x[1])):
        marker = "AT" if d["cor_numbered"] == POK_FAMILY_MAX else "OVER"
        for rid, slug in d["cor_numbered_members"]:
            w("| `%s` | `%s` | %d | %s | %d | `%s` | `%s` |"
              % (pid, f, d["cor_numbered"], marker, d["cor_prefix"], rid, slug))
else:
    w("_none_")
w("")
w("- Families at or over the bound (corrected numbered n >= 3): **%d** across 3 projects."
  % len(at_or_over))
w("- Focus project `83da04de…`: **%d**."
  % len([x for x in at_or_over if x[0] == FOCUS_PROJECT]))
w("")

w("### 3b. NEW-refusal families (4th-member admissions that pass today and would refuse)")
w("")
w("Definition applied: corrected numbered count >= 3 **AND** at least one corrected")
w("member whose CURRENT-view family differs (which, for a multi-token family, means")
w("*ungated today*). Today such a write is admitted (no family is parsed, so")
w("`count_active_in_family` is never consulted); under the corrected key it hits")
w("`pok_family_full`.")
w("")
if new_refusal:
    w("| project | family | corrected n | prefix n | would refuse at | member id | member slug | current family |")
    w("|---|---|---|---|---|---|---|---|")
    for pid, f, d in sorted(new_refusal, key=lambda x: (-x[2]["cor_numbered"],
                                                         -x[2]["cor_prefix"], x[0], x[1])):
        for rid, slug in d["cor_numbered_members"]:
            w("| `%s` | `%s` | %d | %d | %d-th member | `%s` | `%s` | %s |"
              % (pid, f, d["cor_numbered"], d["cor_prefix"], d["cor_numbered"] + 1,
                 rid, slug, fam_current(slug) or "None (ungated)"))
else:
    w("_none_")
w("")
w("- **New-refusal families: %d.**" % len(new_refusal))
for pid, f, d in sorted(new_refusal, key=lambda x: (x[0], x[1])):
    w("  - `%s` in `%s` — corrected numbered n=%d, prefix n=%d, multi-token=%s, currently ungated under the live key."
      % (f, pid, d["cor_numbered"], d["cor_prefix"], "yes" if d["multi_token"] else "no"))
w("")

w("### 3c. Unchanged gating — single-token families + PM headroom cross-check")
w("")
w("**Asserted:** for every single-token family (name contains no hyphen) in every")
w("project, `corrected numbered n == current numbered n`. The corrected key is a strict")
w("SUPERSET of the current one, so a single-token family is gated identically and its")
w("admission behaviour is unchanged.")
w("")
w("- Single-token families checked: **%d** across 3 projects." % len(single_token))
w("- Assertion failures: **0** (the generator aborts before writing on any mismatch).")
multi_fams = [1 for (p, f), d in all_families.items() if d["multi_token"]]
w("- Multi-token families (gated under CORRECTED, ungated under CURRENT): **%d** across"
  " 3 projects; of those, **%d** are at or over the corrected bound (see §3a) and **%d**"
  " are a new-refusal family (see §3b)."
  % (len(multi_fams), len([1 for pid, f, d in at_or_over if d["multi_token"]]),
     len([1 for pid, f, d in new_refusal])))
w("")
w("**PM dual-key headroom table cross-check** (focus project `83da04de…`; PM table is")
w("post-wave, ~18:36Z; this census is %s):" % EXPECT_CAPTURED_AT)
w("")
w("| family | PM numbered | PM prefix | census numbered | census prefix | delta numbered | delta prefix |")
w("|---|---|---|---|---|---|---|")
for e in pm_rows:
    w("| `%s` | %d | %d | %d | %d | %+d | %+d |"
      % (e["family"], e["pm_numbered"], e["pm_prefix"], e["our_numbered"],
         e["our_prefix"], e["delta_numbered"], e["delta_prefix"]))
w("")
w("**Row-level attribution** for the three tracked families (member rows of the census):")
w("")
for e in pm_rows:
    w("- **`%s`** — PM %d/%d vs census %d/%d (delta %+d numbered, %+d prefix)"
      % (e["family"], e["pm_numbered"], e["pm_prefix"], e["our_numbered"],
         e["our_prefix"], e["delta_numbered"], e["delta_prefix"]))
    if e["members"]:
        for m in e["members"]:
            flags = []
            if m["created_in_window"]:
                flags.append("CREATED-IN-WINDOW")
            if m["updated_in_window"]:
                flags.append("UPDATED-IN-WINDOW")
            w("  - `%s` `%s` — created %s (by %s), updated %s%s"
              % (m["id"], m["slug"], m["created_at"], m["created_by_agent"],
                 m["updated_at"], ("  [" + ", ".join(flags) + "]") if flags else ""))
    else:
        w("  - _no ACTIVE member rows in the census_")
    for p in e["prefix_extra"]:
        w("  - (unnumbered prefix member) `%s` `%s` — created %s, updated %s"
          % (p["id"], p["slug"], p["created_at"], p["updated_at"]))
w("")
w("**Ledger-vs-census diff** — the PM's stated composition (transcribed from")
w("`family-cap-consolidation-2026-11-18.md`) diffed against the census. This is where")
w("a delta is attributed to either (i) store drift in the window or (ii) a precision gap")
w("in the PM ledger itself.")
w("")
for e in pm_rows:
    fam = e["family"]
    w("- **`%s`**" % fam)
    if e["pm_prefix_slugs"]:
        census_set = set(e["census_prefix_slugs"])
        pm_set = set(e["pm_prefix_slugs"])
        only_census = sorted(census_set - pm_set)
        only_pm = sorted(pm_set - census_set)
        if only_census:
            for slug in only_census:
                row = next(x for x in rows if x["slug"] == slug
                           and x["project_id"] == FOCUS_PROJECT)
                tag = ("CREATED-IN-WINDOW (store drift)"
                       if row["created_at"] > PM_WAVE_UTC
                       else "PRE-WINDOW row, untouched since — PM-ledger undercount, NOT drift")
                w("  - in census prefix set but NOT in the PM-stated set: `%s` (`%s`) — created %s, updated %s — %s"
                  % (slug, row["id"], row["created_at"], row["updated_at"], tag))
        if only_pm:
            for slug in only_pm:
                w("  - in the PM-stated set but NOT in the census prefix set: `%s`" % slug)
        if not only_census and not only_pm:
            w("  - prefix slug sets are IDENTICAL to the PM-stated set — no composition drift.")
    else:
        w("  - the PM doc does not enumerate the full slug set for this family (it names `snapshot-l3-01-malformed-json-empty-collapse` as the post-wave survivor and `snapshot-l3-05-decline-byte-identical` as the ARCHIVED absorbed member), so a slug-set diff is not possible for it; the counts are the comparable quantity — PM %d/%d vs census %d/%d — they %s."
          % (e["pm_numbered"], e["pm_prefix"], e["our_numbered"], e["our_prefix"],
             "MATCH" if (e["delta_numbered"] == 0 and e["delta_prefix"] == 0) else "DIFFER"))
    if e["id_diff"]:
        w("  - id cross-check (PM-cited id prefix vs live ACTIVE id):")
        for slug, pm_id, live_id in e["id_diff"]:
            if live_id is None:
                w("    - `%s` — PM id `%s`; NOT present in the ACTIVE census" % (slug, pm_id))
            elif live_id.startswith(pm_id):
                w("    - `%s` — PM id `%s` == live `%s` (MATCH)" % (slug, pm_id, live_id))
            else:
                row = next(x for x in rows if x["id"] == live_id)
                w("    - `%s` — PM id `%s` vs live ACTIVE id `%s` (**MISMATCH**; the live row was created %s by %s, i.e. BEFORE the PM wave, so the PM-cited id is not the live active id for this slug)"
                  % (slug, pm_id, live_id, row["created_at"], row["created_by_agent"]))
w("")
w("**Window census** — ACTIVE rows in the focus project created after the PM wave")
w("boundary %s (attribution for any drift above):" % PM_WAVE_UTC)
w("")
if window_rows:
    w("| id | slug | created_at | created_by_agent | corrected family | current family |")
    w("|---|---|---|---|---|---|")
    for r in window_rows:
        w("| `%s` | `%s` | %s | %s | %s | %s |"
          % (r["id"], r["slug"], r["created_at"], r["created_by_agent"],
             r["cor_family"] or "None", r["cur_family"] or "None"))
else:
    w("_none_")
w("")

w("### 3d. Unnumbered-prefix rows counted toward `prefix_count` but not gated themselves")
w("")
w("Rows whose slug starts with `<family>-` (so the family prefix count includes them)")
w("but which are NOT numbered members of that family. Two shapes are separated below,")
w("because they mean different things to the gate:")
w("")
w("- **3d.1 — family extraction `None`:** the row is not gated by ANY key. It still")
w("  counts toward the family's prefix count, so it adds pressure to the bound without")
w("  being a member the PM could merge, supersede or archive as part of the family.")
w("- **3d.2 — family extraction is a LONGER family `G` (`G` starts with `<family>-`):**")
w("  the row IS gated, but under `G`, not under the shorter family. It inflates the")
w("  shorter family's prefix count while belonging to a different family — a prefix")
w("  collision, not a loose member.")
w("")
ungated_none = [x for x in ungated_prefix_rows if x[4] is None]
cross_family = [x for x in ungated_prefix_rows if x[4] is not None]
w("#### 3d.1 family extraction `None` (ungated rows inside a family prefix)")
w("")
if ungated_none:
    w("| project | family | prefix n | row id | row slug | family extraction |")
    w("|---|---|---|---|---|---|")
    for pid, f, rid, slug, ex in ungated_none:
        w("| `%s` | `%s` | %d | `%s` | `%s` | None |"
          % (pid, f, all_families[(pid, f)]["cor_prefix"], rid, slug))
else:
    w("_none_")
w("")
w("- Rows in this shape: **%d**." % len(ungated_none))
w("")
w("#### 3d.2 cross-family prefix rows (row belongs to a LONGER family `G`)")
w("")
if cross_family:
    w("| project | family (shorter) | row id | row slug | extraction (G) | G numbered n | G prefix n |")
    w("|---|---|---|---|---|---|---|")
    for pid, f, rid, slug, ex in cross_family:
        gd = all_families.get((pid, ex))
        w("| `%s` | `%s` | `%s` | `%s` | `%s` | %s | %s |"
          % (pid, f, rid, slug, ex,
             gd["cor_numbered"] if gd else "n/a",
             gd["cor_prefix"] if gd else "n/a"))
else:
    w("_none_")
w("")
w("- Rows in this shape: **%d**." % len(cross_family))
w("- **Gate consequence:** for the affected short families the prefix count is larger")
w("  than the family's own member set, so a short family can sit at/over the bound")
w("  while owning fewer than 3 rows of its own. Both quantities are listed for every")
w("  family in §2 and §3a.")
w("")

# ---- Section 4: next wave starting map
w("## 4. Next consolidation wave starting map")
w("")
w("This section is the PM's next-wave map: every family that is AT or OVER the bound")
w("under the corrected key, with the exact member ids + slugs (numbered members and")
w("unnumbered-prefix members separated).")
w("")
over = [(pid, f, d) for pid, f, d in at_or_over if d["cor_numbered"] > POK_FAMILY_MAX]
at = [(pid, f, d) for pid, f, d in at_or_over if d["cor_numbered"] == POK_FAMILY_MAX]
w("**Priority A — OVER the bound (corrected numbered n > 3): %d families**" % len(over))
w("")
if over:
    for pid, f, d in sorted(over, key=lambda x: (-x[2]["cor_numbered"], x[0], x[1])):
        w("- `%s` (project `%s`) — corrected numbered n=**%d**, prefix n=%d"
          % (f, pid, d["cor_numbered"], d["cor_prefix"]))
        for rid, slug in d["cor_numbered_members"]:
            w("  - numbered member: `%s` `%s`" % (rid, slug))
        for rid, slug, ex in d["prefix_unnumbered"]:
            w("  - unnumbered-prefix member (extraction %s): `%s` `%s`"
              % (ex or "None", rid, slug))
else:
    w("_none_")
w("")
w("**Priority B — AT the bound (corrected numbered n == 3; one more admission would")
w("refuse): %d families**" % len(at))
w("")
if at:
    for pid, f, d in sorted(at, key=lambda x: (-x[2]["cor_prefix"], x[0], x[1])):
        w("- `%s` (project `%s`) — corrected numbered n=**%d**, prefix n=%d"
          % (f, pid, d["cor_numbered"], d["cor_prefix"]))
        for rid, slug in d["cor_numbered_members"]:
            w("  - numbered member: `%s` `%s`" % (rid, slug))
        for rid, slug, ex in d["prefix_unnumbered"]:
            w("  - unnumbered-prefix member (extraction %s): `%s` `%s`"
              % (ex or "None", rid, slug))
else:
    w("_none_")
w("")
w("**Gate truth to read with the map.** The live refusal condition is")
w("`count_active_in_family >= 3`, i.e. the **prefix** count — not the numbered count.")
w("A family whose prefix n is already >= 3 refuses its next numbered admission even if")
w("its numbered n is below 3; conversely a family whose prefix n is below 3 but whose")
w("numbered n is >= 3 would refuse only once the unnumbered rows are counted in the")
w("same project. Both numbers are listed above so the PM can adjudicate on the real")
w("gate quantity.")
w("")
w("**Prefix inflation.** The prefix count is a `startswith` count, so a SHORT family")
w("absorbs the rows of every LONGER family that shares its prefix — see §3d.2 for the")
w("row-level list. A short family can therefore read `prefix n = 9` while owning 3")
w("rows of its own, and it would refuse on the `>= 3` prefix test even though only")
w("its own 3 rows are gated. The corrected key does not change this (the count is a")
w("`startswith` count under both keys); it changes WHICH rows are gated members, so it")
w("is the numbered count that decides how much of the pressure is real.")
w("")
w("---")
w("*Generated %s by the dual-key census generator (python3 stdlib) from the read-only"
  % "2026-11-18T21:12:38Z")
w("snapshot `%s` (sha256 `%s`); artifact written to `%s`.*" % (SRC, actual_sha, OUT_PATH))

body = "\n".join(L) + "\n"
os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
with open(OUT_PATH, "w", encoding="utf-8") as fh:
    fh.write(body)

sidecar = {
    "generated_at": "2026-11-18T21:12:38Z",
    "source": SRC,
    "source_sha256": actual_sha,
    "source_size": len(raw),
    "captured_at": captured_at,
    "pok_family_max": POK_FAMILY_MAX,
    "totals": {pid: {"active": active_by_project[pid],
                     "archived": status_counts[(pid, "archived")],
                     "superseded": status_counts[(pid, "superseded")]}
               for pid in EXPECTED_TOTALS},
    "families": {"%s|%s" % k: {kk: vv for kk, vv in v.items()}
                 for k, v in all_families.items()},
    "at_or_over": [(pid, f, d["cor_numbered"], d["cor_prefix"])
                    for pid, f, d in at_or_over],
    "new_refusal": [(pid, f, d["cor_numbered"], d["cor_prefix"])
                     for pid, f, d in new_refusal],
    "ungated_prefix_rows": ungated_prefix_rows,
    "pm_crosscheck": pm_rows,
    "window_rows": window_rows,
}
with open(SIDECAR, "w", encoding="utf-8") as fh:
    json.dump(sidecar, fh, indent=1, sort_keys=True, default=str)

print("WROTE %s (%d bytes)" % (OUT_PATH, len(body)))
print("WROTE %s" % SIDECAR)
print("SUMMARY at_or_over=%d new_refusal=%d ungated_prefix=%d window_rows=%d"
      % (len(at_or_over), len(new_refusal), len(ungated_prefix_rows), len(window_rows)))
for pid, f, d in sorted(at_or_over, key=lambda x: (-x[2]["cor_numbered"], x[0], x[1])):
    print("  AT/OVER %s %s cor_n=%d prefix_n=%d multi=%s"
          % (pid[:8], f, d["cor_numbered"], d["cor_prefix"], d["multi_token"]))
for pid, f, d in sorted(new_refusal, key=lambda x: (x[0], x[1])):
    print("  NEW-REFUSAL %s %s cor_n=%d prefix_n=%d" % (pid[:8], f, d["cor_numbered"], d["cor_prefix"]))
for e in pm_rows:
    print("  PM-XCHECK %s pm=%d/%d ours=%d/%d" % (e["family"], e["pm_numbered"],
                                                  e["pm_prefix"], e["our_numbered"], e["our_prefix"]))

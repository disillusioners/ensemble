# FM (FINAL MERGED-TREE VERIFICATION) key-evidence bundle — 2026-10-29

Compact companion to the `FINAL MERGED-TREE VERIFICATION (vs origin/latest 69d767b2a)` section of
`../../2026-10-29-dbpool-functional-gate.md` (VERDICT: **CLEARED-FOR-SWEEP-STAGE**, net-new
lane-caused = 0). Assembled at the final evidence commit. Every carried file is sha256-listed in
`MANIFEST-fm.sha256`; every skipped-for-size/count file is sha256-listed in `SKIPPED-FOR-SIZE.txt`.

| dir | leg | source (primary) | standing-worktree mirror |
|---|---|---|---|
| ladder/ | 17-row ladder @ merged tree | /tmp/tdbp-fm2/raw/ (8 logs: full-run ×2, check-mode, T-H1 g9.9, rerun G2.5/G2.6, verbatim G2.5 harness-env ±allowlist) | this repo raw/2026-10-29-dbpool-fm-ladder/ (cmp-verified byte-identical at assembly) |
| f4a/ | F4-A surface (mutations + self-inflicted-outage proof + independent driver) | /tmp/tdbp-fm3/ (fm4a_driver.py + raw/×15) | /home/nea/ensemble-src-wt-tdbp-fmf4a/.../2026-10-29-dbpool-fm-f4a/ (README.md exists only there; carried from the mirror) |
| wholetree/ | whole-tree union judgment (census 31,150; union 363; unknown 2 → both NOT lane-caused) | /tmp/tdbp-fm4/ | /home/nea/ensemble-src-wt-tdbp-fmwt/.../2026-10-29-dbpool-fm-wholetree/ (the lane's own curated compact bundle — carried WHOLESALE, incl. its internal MANIFEST.sha256 + SKIPPED-FOR-SIZE.txt + chunk_tails/ + isolation/) |

Assembly verification (2026-10-29): `diff -r` vs both mirrors = identical; the wholetree internal
`MANIFEST.sha256` re-verified 100% OK against the carried copy; every file shared with the /tmp
primaries cmp-proven byte-identical (ladder 8/8, f4a 16/16 + mirror-only README.md, wholetree
24 top-level + 54 isolation logs + driver.status).

Cross-references: the 346-id base failed set lives at `../wtbase/failed.base.txt` (already
committed); `known_red_union.txt` = wtbase failed ∪ 15 rehead HEAD-only ledger ids ∪ 2 rehead
extras = 363.

NOTE: `.gitignore` `*.log` silently excludes log files — this bundle's `.log` files (ladder ×8,
wholetree chunk_tails ×78 + isolation ×54 + regression11.green.log) were added with targeted
`git add -f`, exactly as the reverify bundle did.

# dbpool-idle-txn FUNCTIONAL GATE — key-evidence bundle (2026-10-29)

Compact text-only companion to `../2026-10-29-dbpool-functional-gate.md` (VERDICT: FAIL — G3.5).
Full raw corpora live under the /tmp paths below AND the standing worktree mirrors (read-only
copies; both verified byte-identical where both exist). Every file here is sha256-listed in
MANIFEST.sha256; every skipped-for-size/count file is sha256-listed in SKIPPED-FOR-SIZE.txt.

| dir | source (primary) | standing-worktree mirror |
|---|---|---|
| wtbase/ | /tmp/tdbp-wtbase/ | /home/nea/ensemble-src-wt-tdbp-base-wt/.agents/tester/RESULTS/raw/2026-10-29-dbpool-wholetree-base/ |
| wthead/ | /tmp/tdbp-wthead-120/ | (this repo) .agents/tester/RESULTS/raw/2026-10-29-dbpool-wholetree-head/ — ATTEMPT1 json lives only there (attempt1_timeout30_superseded/) |
| classify/ | /tmp/tdbp-classify/raw/ | /home/nea/ensemble-src-wt-tdbp-mut1/.agents/tester/RESULTS/raw/2026-10-29-dbpool-classify/ |
| ladder/ | /tmp/tdbp-ladder/ | (this repo) .agents/tester/RESULTS/raw/2026-10-29-dbpool-ladder/ |
| delta3/ | /tmp/tdbp-delta3/raw/ | /home/nea/ensemble-src-wt-tdbp-delta3/.agents/tester/RESULTS/raw/2026-10-29-dbpool-delta3/ |
| j5a/ | /tmp/tdbp-j5a/raw/ + mirror | /home/nea/ensemble-src-wt-tdbp-j5a/.agents/tester/RESULTS/raw/2026-10-29-dbpool-j5a/ (EVIDENCE-INDEX.md exists only in mirror) |
| j5b/ | /tmp/tdbp-j5b/raw/ | /home/nea/ensemble-src-wt-tdbp-j5b/.agents/tester/RESULTS/raw/2026-10-29-dbpool-j5b/ |
| j5c/ | /tmp/tdbp-j5c/raw/ | /home/nea/ensemble-src-wt-tdbp-j5c/.agents/tester/RESULTS/raw/2026-10-29-dbpool-j5c/ |
| mut1/ | /tmp/tdbp-mut1/raw/ + mirror | /home/nea/ensemble-src-wt-tdbp-mut1/.agents/tester/RESULTS/raw/2026-10-29-dbpool-mut1/ (00-SUMMARY.md exists only in mirror) |
| mut2/ | /tmp/tdbp-mut2/raw/ + mirror | /home/nea/ensemble-src-wt-tdbp-mut2/.agents/tester/RESULTS/raw/2026-10-29-dbpool-mut2/ (leg1_*_copy.sh exist only in mirror; NO 00-SUMMARY.md exists for mut2) |

Notes: ladder/full-run.log is 7.6 KB (< 2 MB) — copied whole, no tail needed.
delta3/delta3-measure.json is 149,812 B (< 5 MB) — copied, NOT skipped.
Six record-quoted sha256s (base/head failed+collect, base ids+excluded) re-verified MATCH against
the copies in this bundle at assembly time (2026-10-29).

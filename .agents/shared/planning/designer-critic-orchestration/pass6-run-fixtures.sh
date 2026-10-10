#!/usr/bin/env bash
# Pass-6 gate runner — executes the FROZEN gates against the Pass-6 fixtures.
# Every gate below is pasted verbatim from the cured plan text (AC4, AC8 orphan
# gates, phase3 AC9); targets are the fixtures, except where marked LIVE
# (today's real files = positive controls).
set -u
P="$(cd "$(dirname "$0")" && pwd)"
F="$P/pass6-fixtures/fixture-a-rulemd-body.txt"
echo "=================================================================="
echo "GATE 1 — phase1 AC4 (Pass-6 per-alternative form) vs T5 fixture"
echo "=================================================================="
echo "-- gate 1.0: the RETIRED combined form (recorded for the arithmetic:"
echo "   all four alternatives land on the ONE re-keyed :35 bullet)"
OLD='grep -nE "lane_preference|same-code-class|other:user-requested-text-only|other:proxy-ceiling-"'
echo "\$ $OLD agents/designer/rule.md   [fixture-substituted]"
grep -nE "lane_preference|same-code-class|other:user-requested-text-only|other:proxy-ceiling-" "$F" || true
echo "hit lines = $(grep -cE "lane_preference|same-code-class|other:user-requested-text-only|other:proxy-ceiling-" "$F")  → ≥4 UNSATISFIABLE (defect confirmed on the frozen T5 text)"
echo
echo "-- gate 1.1: grep -nE \"lane_preference\" → ≥1"
grep -nE "lane_preference" "$F"; echo "exit=$? hits=$(grep -cE "lane_preference" "$F")"
echo "-- gate 1.2: grep -nE \"same-code-class\" → ≥1"
grep -nE "same-code-class" "$F"; echo "exit=$? hits=$(grep -cE "same-code-class" "$F")"
echo "-- gate 1.3: grep -nE \"other:user-requested-text-only\" → ≥1"
grep -nE "other:user-requested-text-only" "$F"; echo "exit=$? hits=$(grep -cE "other:user-requested-text-only" "$F")"
echo "-- gate 1.4: grep -nE \"other:proxy-ceiling-\" → ≥1"
grep -nE "other:proxy-ceiling-" "$F"; echo "exit=$? hits=$(grep -cE "other:proxy-ceiling-" "$F")"
echo "ARITHMETIC: 4 alternatives × ≥1 hit each, all on the single :35 bullet → PASS BY CONSTRUCTION"
echo
echo "=================================================================="
echo "GATE 2 — AC8 orphan gates as TODAY positive controls (LIVE files)"
echo "=================================================================="
TN=agents/designer/tools_note.md
echo "-- 2.1: grep -c \"Provenance tag policy\" $TN  (must be 1 pre-move, 0 post-move)"
grep -c "Provenance tag policy" "$TN"
echo "-- 2.2: grep -c \"Failure modes I expect\" $TN  (must be 1 pre-move, 0 post-move)"
grep -c "Failure modes I expect" "$TN"
echo "-- 2.3: fence count in the PRESCRIBED-OLD range :55-110 (defect demo — must be ODD)"
sed -n '55,110p' "$TN" | grep -cE '^\s*```'
echo "-- 2.4: fence count in the HEADER-DELIMITED section body :55-139 (must be EVEN = 6)"
sed -n '55,139p' "$TN" | grep -cE '^\s*```'
echo "-- 2.5: TOTAL designer fence count today (must be EVEN = 6)"
grep -cE '^\s*```' "$TN"
echo "-- 2.6: proof the old range cuts mid-fence (last 3 lines of :55-110)"
sed -n '55,110p' "$TN" | tail -n 3
echo "-- 2.7: section boundaries (heading + delimiter + next heading) LIVE"
grep -nE '^## Capture Procedure|^---$|^## Two-Channel Image Reality|^### Provenance tag policy|^### Failure modes I expect' "$TN"
echo
echo "=================================================================="
echo "GATE 3 — phase3 AC9 cardinal-count arithmetic (fixtures B)"
echo "=================================================================="
echo "-- 3.0: LIVE positive control — grep -cE '^[0-9]+\.' agents/designer/rule.md today (must be 7)"
grep -cE '^[0-9]+\.' agents/designer/rule.md
echo "-- 3.1: OLD hand-off form fixture (3 new numbered Cardinals): count must be 10 > 7 = FAIL"
grep -cE '^[0-9]+\.' "$P/pass6-fixtures/fixture-b-OLD-form.txt"
echo "-- 3.2: NEW hand-off form fixture (Guidelines (e)/(f)/(g), zero new numerals): count must be 7 ≤ 7 = PASS"
grep -cE '^[0-9]+\.' "$P/pass6-fixtures/fixture-b-NEW-form.txt"
grep -nE '^[0-9]+\.' "$P/pass6-fixtures/fixture-b-NEW-form.txt" | tail -n 2
echo "-- 3.3: guideline lines present but NOT numeral-led (the (e)/(f)/(g) encoding):"
grep -nE '^Guideline \([efg]\)' "$P/pass6-fixtures/fixture-b-NEW-form.txt"
echo "=================================================================="
echo "GATE 4 — AC4/AC5 co-gate sanity on the fixture (enum line byte-shape)"
echo "=================================================================="
echo "-- 4.1: AC5 five-token grep on fixture (≥2 hits on the :15 + :38 shape)"
grep -nE "tool-not-bound|call-error|timeout|daemon-unavailable|other:" "$F" | head -n 5
echo "-- 4.2: AC5 od-token zero-grep on fixture (0 — the re-keys are token-free)"
grep -cE "od\.[a-z_]+" "$F"

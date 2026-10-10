# Pass-6 fixture B — AC9 cardinal-count arithmetic (phase3 hand-off bullet, :104)

Two post-phase-3 variants of designer rule.md's numbered-line set:
- `fixture-b-OLD-form.txt` — the PRE-Pass-6 hand-off reading (three NEW numbered Cardinals appended): 7 existing + 3 new = 10 numbered lines → AC9 grep `grep -cE '^[0-9]+\.'` = 10 > 7 → FAIL.
- `fixture-b-NEW-form.txt` — the POST-Pass-6 hand-off reading (controls encoded as Guidelines (e)/(f)/(g), zero new numbered lines): 7 existing + 0 = 7 → AC9 grep = 7 ≤ 7 → PASS.

Lines 1-7 in both variants are the REAL seven Cardinal openers from `agents/designer/rule.md:9-15` (byte-true prefixes, truncated only where the line exceeds the gate's relevance — the count grep `^[0-9]+\.` keys on the line-leading numeral only).

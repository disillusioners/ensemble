# Injection corpus — synthetic seed fixtures

Every case in `cases.jsonl` is a SYNTHETIC-ATTACK-SAMPLE or a benign control,
authored for the injection-screen lane. Nothing here is real credential
material, real user data, or real repo content; each JSON object carries a
`_notice` key whose value contains the literal `SYNTHETIC-ATTACK-SAMPLE`, and
`secret-scan-positive-control.txt` is a deliberately fake key shape that the
corpus secret-scan leg MUST flag (the positive control proving the scan is
armed).

Shape (one JSON object per line; ground-truth fields per the lane
requirements): `id`, `category`, `payload` (inline string; control characters
as JSON escapes), `expected_verdict`, `expected_confidence`,
`expected_rules` (array of `INJ-nn`), `notes`, `_notice`.

The ground truth is consumed by the heuristic corpus leg with the LLM pass
off (deterministic). Coverage: ≥1 positive per rule ID, ≥3 positives per rule
category, ≥8 negative cases, ≥4 parser-adversarial cases (forged END token,
bare `verdict:` line in the payload, a 3-backtick fence in the payload, an
oversized payload exercising the truncation path). This corpus is the single
authoring site for the lane: later phases verify it, they never re-author it.

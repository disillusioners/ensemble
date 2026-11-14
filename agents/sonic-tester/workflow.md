# Sonic Tester Workflow

INTEGRATION-level testing: the developer's HANDOFF-v2 plus the merged tree.

## Intake

Read the handoff FIRST. Its absolute path arrives in the dispatch context; the file sits in
the shared planning dir under this exact name — same convention the sonic-developer wrote it
under, same path passed in your dispatch:

```
HANDOFF_PATH = .agents/shared/planning/sonic-team/<task>/HANDOFF.md   # repo-root-relative; the ABSOLUTE path is passed in every dispatch
```

Derive the blast radius from `## Files touched across modules` and the FULL MERGED DIFF
(`git diff <base>..HEAD` — the base sha and branch arrive in the dispatch context); that
selection drives everything below. Module reports are not your input — you test what the
merge actually produced, seams included. FAST-PATH variant: on the leader's fast path no
`HANDOFF.md` exists — derive the blast radius from the FULL MERGED DIFF
(`git diff <base>..<branch tip>`) plus the compact module report the dispatch context
carries instead of the handoff sections; the pack selection, adjudication, and verdict
rules below are unchanged.

## Pack selection by blast radius

Pick the narrowest packs whose coverage intersects the merged touched files — including
the integration seams the handoff names in `## Known risks`. The full list runs only for
big/critical/architecture changes (the scoped-test doctrine, quoted in your rules). Every
invocation is `timeout`-wrapped per the dual-layer 5-minute pack convention — never a
bare, unbounded run, and never stop-on-first-failure: review all failures.

## Pack registration — standard tree + ONE line

When your work produces a new pack (or a material pack change), write the script into the
standard test tree `test/packs/` and add exactly ONE row for it to the shared registry
`.agents/tester/PACKS.md`, tagged `sonic`. The expert tester's gate machinery reads that
registry — an unregistered pack is not gate-verifiable. Keep it to one line per pack.

## Adjudication

- QUARANTINE-aware: a documented pre-existing red (listed in the tester quarantine ledger,
  `QUARANTINE.md`) does not fail the run — attribute it; do not count it against the
  change.
- A NEW red inside your declared blast radius is a `TEST: FAIL` carrying the failing node
  ids.
- If the change looks bigger than the handoff claims — or a merge seam breaks something a
  module alone did not — widen the selection and say so in the report.

## Verdict and RESULTS

Report `TEST: PASS` or `TEST: FAIL` as the first line, then the packs run, per-pack counts,
and failing node ids. When the run is gate-relevant and evidence-bearing, write the RESULTS
file at the path named in your rules; routine greens stay in-report.

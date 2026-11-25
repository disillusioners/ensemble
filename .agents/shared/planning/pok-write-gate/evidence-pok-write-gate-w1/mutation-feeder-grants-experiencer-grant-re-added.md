# Mutation evidence — feeder-grants experiencer grant ABSENCE pin (module A)

**Artifact for:** feeder-grants module A — "experiencer does NOT hold `pok` +
mutation leg (re-add immediately before `nest-report` → 2 RED, ordering GREEN)".

**Status:** RED CONFIRMED (2026-11-25).

**Fence:** the mutation was applied to a THROWAWAY `git archive` extract of
the branch tip. The live worktree was NEVER mutated — the extract carried
the two working-tree files under test copied in explicitly; `git status`
in the live worktree shows only the three files this commission edits.

---

## The claim under test

Module A removed `"pok"` from `agents/experiencer/meta.json` `tools.allow`
(Emperor pre-approval 2026-11-25; this commission's ONLY grant change). The
capture duty stays with the operational agents (ari / project-manager /
leader / devops); the experiencer is a KNOWLEDGE extractor and keeps RAG-side
routing only (`"rag"` survives in the allow).

The new `TestTheExperiencerGrantAbsence` class pins the ABSENCE positively:
re-adding the grant must RED both the absence pin and the census pin. The
completeness walk's exception (`GRANT_ABSENT_EXCEPTIONS`, now
`{"kb-writer", "experiencer"}`) is a DECLARED decision, not a hole in the
walk. The ordering pin must stay GREEN because the grant is re-inserted at
its legal insert-before slot (immediately before `"nest-report"`).

## Procedure (reproducible)

```sh
WT=/home/nea/ensemble-src-wt-feeder-grants
TH=/tmp/pok-mutation-feeder-grants
rm -rf "$TH" && mkdir -p "$TH"
cd "$WT" && git archive HEAD | tar -x -C "$TH"
cp "$WT/tests/unit/test_pok_grant_pins.py" "$TH/tests/unit/test_pok_grant_pins.py"
cp "$WT/agents/experiencer/meta.json"         "$TH/agents/experiencer/meta.json"
cp "$WT/scripts/run_tests_scrubbed.sh"        "$TH/scripts/run_tests_scrubbed.sh"
ln -s "$WT/.venv" "$TH/.venv"
cd "$TH"

# STEP 1 — BASELINE (grant absent → absence pin GREEN)
ENSEMBLE_SELF_ENV=dev PYTHONPATH="$TH" ./scripts/run_tests_scrubbed.sh \
  "tests/unit/test_pok_grant_pins.py::TestTheExperiencerGrantAbsence" \
  -p no:randomly -q
# → 4 passed, exit 0

# STEP 2 — MUTATE: re-add the grant immediately before nest-report
"$WT/.venv/bin/python" - <<'PY'
import json
p = "/tmp/pok-mutation-feeder-grants/agents/experiencer/meta.json"
d = json.load(open(p))
allow = d["tools"]["allow"]
allow.insert(allow.index("nest-report"), "pok")
json.dump(d, open(p, "w"), indent=2)
PY

# STEP 3 — MUTATED (absence pin RED + census pin RED; ordering GREEN)
ENSEMBLE_SELF_ENV=dev PYTHONPATH="$TH" ./scripts/run_tests_scrubbed.sh \
  "tests/unit/test_pok_grant_pins.py::TestTheExperiencerGrantAbsence" \
  "tests/unit/test_pok_grant_pins.py::TestThePokGrantCompletenessWalk::test_exactly_52_carry_the_grant_explicitly" \
  "tests/unit/test_pok_grant_pins.py::TestThePokGrantOrdering::test_grant_is_immediately_before_nest_report_everywhere" \
  -p no:randomly -q
# → 2 failed, 4 passed, exit 1
```

## Result

| Step | State | Nodes | Exit |
|------|-------|-------|------|
| 1 — BASELINE | grant ABSENT | 4 passed | **0** |
| 3 — MUTATED | grant RE-ADDED before `nest-report` | **2 failed**, 4 passed | **1** |

RED nodes (verbatim from the short summary):

```
FAILED tests/unit/test_pok_grant_pins.py::TestTheExperiencerGrantAbsence::test_experiencer_does_not_hold_the_grant
FAILED tests/unit/test_pok_grant_pins.py::TestThePokGrantCompletenessWalk::test_exactly_52_carry_the_grant_explicitly
```

RED assertions (verbatim):

```
E       AssertionError: experiencer must NOT hold the 'pok' grant — the capture duty lives with the operational agents (ari / project-manager / leader / devops); the allow is ['rag', 'help', 'time', 'mcp', 'context', 'shared_meta_kv', 'pok', 'nest-report']
E       AssertionError: 53 agents carry 'pok' explicitly, expected 52
```

The ordering node
`TestThePokGrantOrdering::test_grant_is_immediately_before_nest_report_everywhere`
PASSED in the mutated run (progress `F...F.` — the 6th node is green): the
grant was re-inserted at its legal slot, so only the ABSENCE and COUNT legs
are load-bearing here.

**Both** directions of the pin are load-bearing: the absence pin REDs on the
re-added grant, and the census pin REDs on the count drift the same mutation
causes (53 vs 52). The ordering pin is deliberately NOT a detector for this
mutation — it guards a different invariant.

## Restore proof

The live worktree was never mutated. After the leg:

```
$ cd /home/nea/ensemble-src-wt-feeder-grants && git status --short
 M agents/experiencer/meta.json
 M tests/unit/test_pok_grant_pins.py
?? .agents/shared/planning/pok-write-gate/evidence-pok-write-gate-w1/mutation-feeder-grants-experiencer-grant-re-added.md
```

Only the three files this commission edits appear; no stray grant re-add.
The live `agents/experiencer/meta.json` still carries the 7-tool allow with
NO `pok`; the mutated copy exists only under `/tmp/pok-mutation-feeder-grants`.

## Note on the extract

The extract carries the branch tip's `agents/` tree plus the two working-tree
files under test (`tests/unit/test_pok_grant_pins.py` and
`agents/experiencer/meta.json`), the house scrubber, and a symlinked venv —
the scrubber gives the extract the identical env-fence as the live test run.
No other file in the extract differs from the branch tip.

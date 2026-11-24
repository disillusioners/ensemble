# Mutation evidence — T4.4 kb-writer grant ABSENCE pin (Wave 1 Phase 4)

**Artifact for:** pok-write-gate Phase 4, T4.4 — "NEW pin: kb-writer does
NOT hold `pok` + mutation leg (re-add → RED)".

**Status:** RED CONFIRMED (2026-11-24).

**Fence:** the mutation was applied to a THROWAWAY `git archive` extract of
the branch tip. The live worktree was NEVER mutated — `git status` after
the leg shows only the not-mine `plan-review-4.md`.

---

## The claim under test

T4.1 removed `"pok"` from `agents/kb-writer/meta.json` `tools.allow`
(Emperor ruling of 2026-11-23, addendum-3 item 1 — the capture duty moves
to the operational agents: ari / project-manager / leader / devops).

T4.4 pins the ABSENCE positively: re-adding the grant must RED. The
completeness walk's exception (`GRANT_ABSENT_EXCEPTIONS`) is a DECLARED
decision, not a hole in the walk — so the pin asserts both the absence
AND the declaration (plus that the RAG-side routing tool survives and that
the four compensating agents hold the grant).

## Procedure (reproducible)

```sh
WT=/home/nea/ensemble-src-wt-pok-write-gate
TH=/tmp/pok-mutation-t44
rm -rf "$TH" && mkdir -p "$TH"
cd "$WT" && git archive HEAD | tar -x -C "$TH"
cp "$WT/tests/unit/test_pok_grant_pins.py" "$TH/tests/unit/test_pok_grant_pins.py"
cp "$WT/agents/kb-writer/meta.json"         "$TH/agents/kb-writer/meta.json"
cd "$TH"

# STEP 1 — BASELINE (grant absent → pin GREEN)
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
    -u POSTGRES_HOST -u POSTGRES_PORT \
  PYTHONPATH="$TH" "$WT/.venv/bin/python" -m pytest \
  "tests/unit/test_pok_grant_pins.py::TestTheKbWriterGrantAbsence" \
  -p no:randomly -q
# → 4 passed, exit 0

# STEP 2 — MUTATE: re-add the grant to kb-writer
python3 - <<'PY'
import json
p = "/tmp/pok-mutation-t44/agents/kb-writer/meta.json"
d = json.load(open(p))
allow = d["tools"]["allow"]
allow.insert(allow.index("nest-report"), "pok")
json.dump(d, open(p, "w"), indent=2)
PY

# STEP 3 — MUTATED (pin must RED)
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
    -u POSTGRES_HOST -u POSTGRES_PORT \
  PYTHONPATH="$TH" "$WT/.venv/bin/python" -m pytest \
  "tests/unit/test_pok_grant_pins.py::TestTheKbWriterGrantAbsence" \
  "tests/unit/test_pok_grant_pins.py::TestThePokGrantCompletenessWalk::test_exactly_53_carry_the_grant_explicitly" \
  -p no:randomly -q
```

## Result

| Step | State | Nodes | Exit |
|------|-------|-------|------|
| 1 — BASELINE | grant ABSENT | 4 passed | **0** |
| 3 — MUTATED | grant RE-ADDED | **2 failed**, 3 passed | **1** |

RED assertions (verbatim):

```
FAILED tests/unit/test_pok_grant_pins.py::TestTheKbWriterGrantAbsence::test_kb_writer_does_not_hold_the_grant
FAILED tests/unit/test_pok_grant_pins.py::TestThePokGrantCompletenessWalk::test_exactly_53_carry_the_grant_explicitly

E  AssertionError: kb-writer must NOT hold the 'pok' grant — the capture
E  duty lives with the operational agents (ari / project-manager / leader /
E  devops); the allow is ['rag_insert_text', 'tool_help', 'time', 'pok', 'nest-report']

E  AssertionError: 54 agents carry 'pok' explicitly, expected 53
```

**Both** directions of the pin are load-bearing: the absence pin REDs on
the re-added grant, and the census pin REDs on the count drift the same
mutation causes.

## Note on the extract

The extract carries the branch tip's `agents/` tree plus the two working-
tree files under test (`tests/unit/test_pok_grant_pins.py` and
`agents/kb-writer/meta.json`) — the files T4.1/T4.2/T4.3/T4.4 edit. No
other file in the extract differs from the branch tip.

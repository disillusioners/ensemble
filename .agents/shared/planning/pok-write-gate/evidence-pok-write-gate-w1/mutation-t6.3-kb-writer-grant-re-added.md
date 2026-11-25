# Mutation evidence — T6.3 kb-writer grant re-added (Wave 1 Phase 6)

**Artifact for:** pok-write-gate Phase 6, T6.3 — "Mutation: grant
re-added to kb-writer → RED".

**Status:** RED CONFIRMED (2026-11-24).

**Fence:** the mutation was applied to a THROWAWAY `git archive` extract
of the branch tip (`d76510eff`). The live worktree was NEVER mutated —
`git status --porcelain` after the leg is EMPTY, and the live
`agents/kb-writer/meta.json` still hashes to
`1f0775f1ed92218079e768737e1ad1f5c22298e65ea464f5b5074429a9926b33`.

**Relationship to the Phase-4 artifact.** The SAME leg was first
evidenced in Phase 4 as `mutation-t4.4-kb-writer-grant-absence.md`
(commit `fd7dc6480`). This artifact is the Phase-6 re-proof at the
Phase-6 tip, with the Phase-5 commit in the extract. The result is
BYTE-FOR-BYTE the same RED set — an independent reproduction, not a
re-litigation.

---

## The claim under test

Phase 4 T4.1 removed `"pok"` from `agents/kb-writer/meta.json`
`tools.allow` (Emperor ruling of 2026-11-23, addendum-3 item 1 — the
capture duty moves to the operational agents: ari / project-manager /
leader / devops). Phase 4 T4.4 pinned the ABSENCE positively: the
completeness walk's exception (`GRANT_ABSENT_EXCEPTIONS`) is a
DECLARED decision, not a hole in the walk — so the pin asserts the
absence AND the declaration, plus that the RAG-side routing tool
survives and that the four compensating agents hold the grant.

The claim under test: **re-adding the grant must RED.**

## Procedure (reproducible)

```sh
WT=/home/nea/ensemble-src-wt-pok-write-gate
TH=/tmp/pok-mutation-t63
rm -rf "$TH" && mkdir -p "$TH"
cd "$WT" && git archive HEAD | tar -x -C "$TH"
cd "$TH"

# STEP 1 — BASELINE (grant absent → pins GREEN)
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
    -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL \
    -u ENSEMBLE_DB_DSN -u PORT -u DAEMON_PORT \
  PYTHONPATH="$TH" ENSEMBLE_SELF_ENV=dev \
  ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 PYTHONDONTWRITEBYTECODE=1 \
  "$WT/.venv/bin/python" -m pytest \
  "tests/unit/test_pok_grant_pins.py::TestTheKbWriterGrantAbsence" \
  "tests/unit/test_pok_grant_pins.py::TestThePokGrantCompletenessWalk::test_exactly_53_carry_the_grant_explicitly" \
  -p no:randomly -q --tb=line -rfE

# STEP 2 — MUTATE: re-add the grant to kb-writer
python3 - "$TH" <<'PY'
import sys, json, pathlib
p = pathlib.Path(sys.argv[1]) / "agents/kb-writer/meta.json"
d = json.loads(p.read_text())
allow = d["tools"]["allow"]
allow.insert(allow.index("nest-report"), "pok")
p.write_text(json.dumps(d, indent=2) + "\n")
PY

# STEP 3 — MUTATED (pins must RED)
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
    -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL \
    -u ENSEMBLE_DB_DSN -u PORT -u DAEMON_PORT \
  PYTHONPATH="$TH" ENSEMBLE_SELF_ENV=dev \
  ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 PYTHONDONTWRITEBYTECODE=1 \
  "$WT/.venv/bin/python" -m pytest \
  "tests/unit/test_pok_grant_pins.py::TestTheKbWriterGrantAbsence" \
  "tests/unit/test_pok_grant_pins.py::TestThePokGrantCompletenessWalk::test_exactly_53_carry_the_grant_explicitly" \
  -p no:randomly -q --tb=short -rfE

rm -rf "$TH"
```

## Result

| Step | State | Nodes | Exit |
|------|-------|-------|------|
| 1 — BASELINE | grant ABSENT | 5 passed | **0** |
| 3 — MUTATED | grant RE-ADDED | **2 failed**, 3 passed | **1** |

RED assertions (verbatim):

```
FAILED tests/unit/test_pok_grant_pins.py::TestTheKbWriterGrantAbsence::test_kb_writer_does_not_hold_the_grant
FAILED tests/unit/test_pok_grant_pins.py::TestThePokGrantCompletenessWalk::test_exactly_53_carry_the_grant_explicitly
2 failed, 3 passed, 17 warnings in 0.57s

E   AssertionError: kb-writer must NOT hold the 'pok' grant — the capture duty lives with the operational agents (ari / project-manager / leader / devops); the allow is ['rag_insert_text', 'tool_help', 'time', 'pok', 'nest-report']

E   AssertionError: 54 agents carry 'pok' explicitly, expected 53
```

**Both** directions of the pin are load-bearing: the absence pin REDs
on the re-added grant, and the census pin REDs on the count drift the
same mutation causes. The 3 GREEN legs are CONTROLS — the declared
exception, the RAG-side routing survival, and the four compensating
agents' grant.

## Scope note

This leg covers T6.3 ONLY. It does NOT mutate
`.agents/shared/metrics/tool-categories.json` — that artifact is
VOCABULARY, not grants, and Phase 4 T4.5 requires it to stay
UNTOUCHED (no regen, no version bump).

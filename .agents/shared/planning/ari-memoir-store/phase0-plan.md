# Phase 0 — Anchor re-verification + scaffolding

**Depends on:** — (first phase). **Flags consumed:** #13 (knob module shape).

## 1. Objective

Re-verify every code anchor at the implementation-time HEAD (this plan's anchors were grep-verified at `0d2f31dde` on 2026-11-05; house rule P-7: re-grep at use time), create the module skeletons so later phases land in prepared homes, and land the kill-switch knob module once (every consumer imports it — one authority).

## 2. Decisions

| ID | Decision | Rationale (verified anchors) | Rejected alternatives |
|----|----------|------------------------------|------------------------|
| D0.1 | New repository package `daemon/repositories/memoir/` (`__init__.py`, `models.py`, `repository.py`, `seed.py`) | the `bash_spawn` lane template (`daemon/repositories/bash_spawn/{__init__,models,repository}.py` — the newest small-lane layout, itself citing the board/PSL precedents) | a `daemon/models/memoir.py` placement (models there are manager-owned tables; this lane ships a repository family) |
| D0.2 | Services: `daemon/services/memoir_retrieval.py`, `daemon/services/memoir_briefing.py`, `daemon/services/memoir_embedding_worker.py` | mirrors `critical_notes_selector.py` placement (service beside its consumer seam) | putting retrieval in the tool module (tool bodies must stay thin; service is briefed by both tools AND the briefing builder) |
| D0.3 | Tool module `daemon/tools/memoir.py`; knob module `daemon/tools/memoir_knob.py` — `memoir_enabled() -> bool` tristate resolver + `MEMOIR_ENABLED_ENV = "ENSEMBLE_MEMOIR_ENABLED"` | notify_user keeps its knob logic in-module but consumers span modules here (worker in api.py, briefing in context_messages.py, redirect in inner_soul.py) — ONE shared resolver, no drift; env name is design text (§5.3) | each consumer parsing the env itself (three parsers = three drifts; flag #13's indeterminate semantics must live in exactly one place) |
| D0.4 | Config block `memoir:` in config.yaml (`enabled: true`, `embedding_tick_s: 30`, `embedding_batch_cap: 8`) + `MemoirConfig` (config.py, the `EMBEDDING_*`-family validator region ~:4281-4400 as the pattern) | notify_user knob-resolution family ("env OR config.yaml, one authority") | env-only knobs (operator ergonomics; config.yaml is the documented surface) |

## 3. Tasks

| ID | Task | Outcome (testable) | Touchpoints | Depends on | AC |
|----|------|--------------------|-------------|------------|----|
| T0.1 | Fresh anchor sweep: re-grep every anchor in phase1–6 plans at implementation HEAD; record verified values + drift in a `phase-notes.md` scratch (committed with the phase it belongs to) | sweep table exists; any drift → update plan constants in-code comments; P-7 notes, no §4 rows except real deviations | all files named in phase plans | — | AC1 |
| T0.2 | Module skeletons: empty-but-importable packages/modules with docstrings stating the design contract refs (§ refs) | `python -c "import daemon.repositories.memoir, daemon.tools.memoir_knob"` succeeds | daemon/repositories/memoir/*, daemon/services/memoir_*.py, daemon/tools/memoir.py | T0.1 | AC2 |
| T0.3 | Knob module: tristate resolver (true/1/yes/on; false/0/no/off; unset→config default; garbage→OFF + one-time WARNING naming var+value+canonical spellings) + `MemoirConfig` block | unit pack: 8 polarity cases incl. garbage→OFF+WARNING, unset→ON | daemon/tools/memoir_knob.py, daemon/config.py, config.yaml | T0.2 | AC3 |
| T0.4 | Migration timestamp reservation: mint `YYYYMMDD_HHMMSS` prefix, verify uniqueness vs the 119-file census (`ls daemon/migrations/versions/*.sql \| wc -l` == 119 pre-lane) | reserved prefix recorded; census re-run at P1 write time | (record only) | T0.1 | AC4 |

## 4. Acceptance criteria

| ID | Criterion |
|----|-----------|
| AC1 | Every anchor cited in phases 1–6 has an implementation-HEAD verification entry (value + verifier); drift handled per P-7 |
| AC2 | All skeleton modules import cleanly; no behavior (pure scaffolding) |
| AC3 | Knob pack green: explicit on/off × case, unset→ON, garbage→OFF+WARNING exactly once; config.yaml `memoir.enabled:false` respected when env unset |
| AC4 | Reserved migration prefix unique against the census; census count recorded |

## 5. Test packs

- `test/packs/memoir_knob_unit_test.sh` — the AC3 matrix + a mutation leg (invert fail-closed → RED).
- Registration into `.agents/tester/PACKS.md` happens in P7's consolidation (one registration block for the lane).

## 6. Flag consumption

#13 (knob shape + polarity lands here; consumers in P3/P4/P5/P6). Anchor re-verification covers #4's standing rule.

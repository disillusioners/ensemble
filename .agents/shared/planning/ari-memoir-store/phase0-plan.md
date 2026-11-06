# Phase 0 — Anchor re-verification + scaffolding

**Depends on:** — (first phase). **Flags consumed:** #13 (the knob truth table — adjudicated, DR-3).

## 1. Objective

Re-verify every code anchor at the implementation-time HEAD (this plan's anchors were grep-verified at `0d2f31dde` on 2026-11-05; house rule P-7: re-grep at use time), create the module skeletons so later phases land in prepared homes, and land the kill-switch knob ONCE — one authority, resolved through the model, read at call time.

## 2. Decisions

| ID | Decision | Rationale (verified anchors) | Rejected alternatives |
|----|----------|------------------------------|------------------------|
| D0.1 | New repository package `daemon/repositories/memoir/` (`__init__.py`, `models.py`, `repository.py`, `seed.py`) | the `bash_spawn` lane template (`daemon/repositories/bash_spawn/{__init__,models,repository}.py` — the newest small-lane layout, itself citing the board/PSL precedents) | a `daemon/models/memoir.py` placement (models there are manager-owned tables; this lane ships a repository family) |
| D0.2 | Services: `daemon/services/memoir_retrieval.py`, `daemon/services/memoir_briefing.py`, `daemon/services/memoir_embedding_worker.py` | mirrors `critical_notes_selector.py` placement (service beside its consumer seam) | putting retrieval in the tool module (tool bodies must stay thin; the service is consumed by BOTH the tools and the briefing builder) |
| D0.3 | Tool module `daemon/tools/memoir.py`; knob module `daemon/tools/memoir_knob.py` exposing `memoir_enabled() -> bool` + the resolved-state accessor | consumers span modules (worker in api.py, briefing in context_messages.py, redirect in inner_soul.py) — ONE shared resolver, no drift; env name is design text (§5.3) | each consumer parsing the env itself (three parsers = three drifts; the indeterminate semantics must live in exactly one place) |
| D0.4 | `MemoirConfig(BaseSettings)` with `env_prefix="ENSEMBLE_MEMOIR_"`, `enabled: bool = True`, plus `embedding_tick_s` / `embedding_batch_cap` / `embedding_dimensions` fields; config.yaml `memoir:` block for the non-env surface | the house knob-resolution family — `notify_user` resolves `ENSEMBLE_NOTIFY_USER_*` **through the model**, never hand-parsed (the `notify_user.py:110-115` pattern); the `EMBEDDING_*` shared family lives at `daemon/config.py:4374-4397`. **Mechanism (N-2):** the raw env string is classified **INSIDE the model** — a `field_validator(mode="before")` on `MemoirConfig` maps the raw value onto the truth table (absent/empty ⇒ the config default; the explicit truthy/falsy spellings ⇒ true/false; **any other present value ⇒ INDETERMINATE**, surfaced so the resolver fails it closed) — because a Pydantic `bool` field would otherwise raise on a garbage value before any resolver could classify it. "Never hand-parsed" means **no consumer** parses it (not that the model may not classify it) | hand-parsing `os.environ` in each consumer (three parsers, three truths — and the fail-closed rule would have to be re-implemented three times) |
| D0.5 | **The knob truth table (single authority — every phase cites this table verbatim; phases 3/4/5/6 each state their consumption semantics):** the value is resolved **through the model**, never hand-parsed; a present-but-unparseable value is **INDETERMINATE** and **fails CLOSED**; the knob is read at **call time**. **Mechanism (N-2):** classification of the raw env string happens **inside the model** (`field_validator(mode="before")` on `MemoirConfig`), so "resolved through the model" and "a present-but-unparseable value is INDETERMINATE ⇒ fail closed" are implementable together; **no consumer** reads/parses the env var itself | DD-15's letter: "fail-closed fixes only how an **indeterminate** value is treated (it must not silently enable the surface)". Unset is NOT indeterminate — it resolves to the documented config default, and that default is admissible **only because it is never silent** | the house `notify_user` repair-to-default behaviour (= fail-OPEN) — see D0.6 |

**The knob truth table (canonical — copy verbatim into phases 3, 4, 5, 6):**

| `ENSEMBLE_MEMOIR_ENABLED` | Resolved | Surface | Log (once per boot) |
|---|---|---|---|
| absent | config default (**ON** in v1) | ENABLED | **WARNING** — "unset — memoir surface ENABLED by default; set `ENSEMBLE_MEMOIR_ENABLED=false` to disable" |
| empty string | = absent | ENABLED | as above |
| `true` / `1` / `yes` / `on` / `t` / `y` (any case) | true | ENABLED | INFO |
| `false` / `0` / `no` / `off` / `f` / `n` (any case) | false | DISABLED | INFO (the refusal names the var) |
| any other present value | **INDETERMINATE** | **DISABLED (fail closed)** | **WARNING** naming the var, the rejected value, and the canonical spelling |
| unparseable AND the fallback also fails | — | DISABLED | ERROR |

**OFF semantics (operator-facing contract, stated not implied):** tools refuse `memoir_disabled`; the briefing is **not built** and the memory is **unreadable** (no other read path exists in v1); the embedding worker **parks**; the `inner_soul` ari branch is **still taken** and returns the loud not-recorded notice (never the RAG void); **the data is untouched** — rows stay, the kill-switch is not a delete.

| ID | Decision | Rationale | Rejected alternatives |
|----|----------|-----------|----------------------|
| D0.6 | **Deliberate divergence from the `notify_user` house precedent**, recorded: `notify_user` repairs a garbage value to the validated fallback (= the default = ON) and is therefore **fail-OPEN**; the memoir knob fails **CLOSED** on the indeterminate case | DD-15's clause attaches to the indeterminate case, and the **cost asymmetry** is severe: a mis-set notify knob leaves a **bounded, rate-limited, audited chat message**; a mis-set memoir knob leaves **unbounded, append-only, never-hard-deleted first-person narrative rows** | copying the notify repair (fail-open on a typo ⇒ a silently-enabled unbounded narrative surface) |
| D0.7 | The resolved state must be **discoverable without reading logs**: surfaced in the tool's `tool_help` text (and on the health/readiness surface where one exists) | "unset ⇒ ON" is admissible **only because the default is never silent**; a log-only state is a silent state for every operator who does not read logs | log-only disclosure (the boot WARNING alone is not discoverability) |
| D0.8 | **Accepted residual, recorded:** a **misspelled variable NAME** is indistinguishable from unset ⇒ the surface is ON and only the per-boot WARNING distinguishes it | unpreventable by any knob design; mitigation is **loudness, not prevention** | treating the residual as a blocker (it would make the feature unshippable for a typo risk the house accepts everywhere) |

## 3. Tasks

| ID | Task | Outcome (testable) | Touchpoints | Depends on | AC |
|----|------|--------------------|-------------|------------|----|
| T0.1 | Fresh anchor sweep: re-grep every anchor in phase1–6 plans at implementation HEAD; record verified values + drift in a `phase-notes.md` scratch (committed with the phase it belongs to) | sweep table exists; any drift → update plan constants in-code comments; P-7 notes, no §4 rows except real deviations | all files named in phase plans | — | AC1 |
| T0.2 | Module skeletons: empty-but-importable packages/modules with docstrings stating the design contract refs (§ refs) | `python -c "import daemon.repositories.memoir, daemon.tools.memoir_knob"` succeeds | daemon/repositories/memoir/*, daemon/services/memoir_*.py, daemon/tools/memoir.py | T0.1 | AC2 |
| T0.3 | `MemoirConfig(BaseSettings)` (D0.4) + knob resolver implementing the truth table (D0.5) + the divergence + residual documented in the module docstring | unit pack: every truth-table row, including garbage ⇒ OFF + WARNING naming var/value/canonical spelling, and empty-string ⇒ absent | daemon/tools/memoir_knob.py, daemon/config.py, config.yaml | T0.2 | AC3 |
| T0.4 | Migration timestamp reservation: mint `YYYYMMDD_HHMMSS` prefix, verify uniqueness vs the 119-file census (`ls daemon/migrations/versions/*.sql \| wc -l` == 119 pre-lane) | reserved prefix recorded; census re-run at P1 write time | (record only) | T0.1 | AC4 |
| T0.5 | Resolved-state discoverability (D0.7): `tool_help` text for the memoir tools states the live resolved state; health/readiness surface carries it where one exists | a caller can learn ON/OFF without reading logs | daemon/tools/memoir.py (help text), readiness surface (if one exists) | T0.3 | AC5 |
| T0.6 | Call-time read discipline (D0.5): the knob is resolved **per call** by every consumer (tools, briefing, worker tick, redirect) — never cached at import time | flipping the env var (or the config) changes behaviour without a restart | daemon/tools/memoir_knob.py + consumers | T0.3 | AC6 |

## 4. Acceptance criteria

| ID | Criterion |
|----|-----------|
| AC1 | Every anchor cited in phases 1–6 has an implementation-HEAD verification entry (value + verifier); drift handled per P-7 |
| AC2 | All skeleton modules import cleanly; no behavior (pure scaffolding) |
| AC3 | Knob pack green: all six truth-table rows exactly (absent/empty ⇒ ON + one WARNING per boot naming var + resolved state; explicit on/off ⇒ INFO; garbage ⇒ OFF + WARNING naming var, rejected value, canonical spelling; fallback-fail ⇒ OFF + ERROR); config.yaml `memoir.enabled:false` honoured when the env is absent |
| AC4 | Reserved migration prefix unique against the census; census count recorded |
| AC5 | The resolved state is retrievable through `tool_help` (and the health surface) without reading logs |
| AC6 | No consumer caches the knob value at import time; a per-call read is asserted by a test that flips the resolved state between two calls in one process |

## 5. Test packs

- `test/packs/memoir_knob_unit_test.sh` — the AC3 matrix + a mutation leg (garbage repaired to the default instead of failing closed ⇒ RED) + AC5/AC6 legs.
- Registration into `.agents/tester/PACKS.md` happens in P7's consolidation (one registration block for the lane).

## 6. Flag consumption

#13 (the truth table, the polarity, the divergence, the residual — adjudicated as DR-3), and the AM-25/AM-26/AM-27 substance this phase owns. Anchor re-verification covers #4's standing rule.

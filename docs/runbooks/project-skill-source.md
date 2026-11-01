# Project Skill Source (file-backed project skills) — operator runbook

Covers the v1 file-backed project skill source (project-skill-loader, 2026-10-31):
`.agents/*/skills/` canon directories indexed into the daemon's skill system —
**files are canon, the DB is a view**. Skill bodies are NEVER copied into any
DB table; load/inject reads the file from disk at call time.

## What shipped (v1 surface)

- **Fused search** — `skill_search` (and skill auto-injection) merges the file
  corpus with the DB corpus for callers resolving into the project. Every
  result entry carries the additive `source: "db" | "project-file"` tag; file
  entries additionally carry `address` (`pf:<ns>:<name>`) and, when they shadow
  a same-name DB skill, `shadows: [<db ids>]`.
- **`pf:` explicit load** — `load_skill="pf:<ns>:<name>"` resolves from disk
  (fail-closed: a `pf:` address NEVER falls through to the DB clone path).
- **`skill_list` / `skill_view`** — file skills listed/served with fresh bodies.
- **Metrics-only feedback** — `skill_feedback` on `pf:` ids records into the
  `project_skill_usage_records` companion table; no lineage rows, no evolution
  dispatch (**P7: file skills evolve via git PRs against `.agents/<ns>/skills/`
  only** — `skill_fix` refuses `pf:` with git-PR guidance).
- **Readiness census** — `/readyz` `detail.skill_sources` (ADVISORY ONLY —
  never flips readiness): `{enabled, db_stack_present, roots[], shadowed_db_skills,
  overflow_dropped, embeddings_pending}`. Always published (`enabled:true|false`);
  empty roots = honest zero. A broken canon root shows in `roots[].last_error`
  while the daemon stays READY.

## Knobs (boot-time only, restart required)

| Env | Default | Meaning |
|---|---|---|
| `ENSEMBLE_PROJECT_SKILL_SOURCE_ENABLED` | `1` (ON) | Kill-switch. Default ON per the **two-class rule (A18)**: ON for zero-user-action read/serve surfaces, OFF for agent-authored mutation lanes — the file source is read/serve. OFF ⇒ `file_source=None` everywhere: byte-identical search/list/view RESULTS (the additive `source: db` tag on `skill_list` rows is emitted ONLY when the source is wired — OFF renders the pre-lane line verbatim), zero `pf-` rows in any skill table. |
| `ENSEMBLE_PROJECT_SKILL_SOURCE_EXTRA_ROOTS` | empty | Comma-separated ABSOLUTE extra canon roots, **dev/testing only**. Each entry is W-5-bound to the caller's resolved project (fail-closed; non-binding entries ignored with a WARNING). Boot logs WARN on nonexistent paths. |

Knob-off posture is fully reversible: nothing is written under OFF (no index ⇒
no refresh ⇒ no `pf-` rows), so flipping OFF and back on is clean.

## Modes (the M0–M11 matrix, operator view)

| Knob | Evolution config | Behavior |
|---|---|---|
| OFF | any | Today's daemon exactly: DB-only search, `pf:` load refused ("file skill source is not available on this deployment"), `/readyz` shows `enabled:false`. |
| ON | present | **Full fusion** — DB + file corpus, shadow pass (file wins same-name), embedding rerank participates for both sources. |
| ON | absent | **File-only degraded (M10)** — `skill_search` answers BM25-only over the file corpus with the `degraded: "file_only"` marker; `skill_view`/`skill_list` serve files; create/fix stay gated. `/readyz` shows `enabled:true, db_stack_present:false`. **REACHABILITY (code-review W7):** on a `load_config`-built daemon `config.skill_evolution` is non-Optional (`Field(default_factory=SkillEvolutionConfig)`), so this cell is a **test/defensive seam** — exercised by the wiring pack, not reachable through the shipped config path. It is NOT what "evolution lane disabled" means; see the A18.2 note below. |

## ⚠️ DUAL CEREMONY DISCLOSURE (A18.2 — read before the next promote)

1. **P4 shadowing in THIS project.** This deployment's DB carries three legacy
   gate-runner skill copies that the file canon replaces by name —
   `9e79ccad` / `fd1e01dc` / `76e3e1a7a`. From the first boot with the knob ON
   they are SHADOWED in project-scope search (the file twin wins; the DB row is
   hidden — absence plus the `shadows` tag, never a visible "deprecated" marker).
   The shadow decisions log at WARNING (`project-file shadowing: file skill …
   retires DB skill(s) …`) — greppable by an operator who doesn't know to look.
2. **First boot activates the file source where it was dark.** Two shapes:
   - **Evolution-config-ABSENT installs** (``skill_evolution`` None — the
     M10 cell; a **test/defensive seam only**, see the W7 note at the end —
     not reachable through ``load_config``): `skill_search`/`skill_list`/
     `skill_view` go from "not yet available" to live FILE-ONLY results
     (`degraded: "file_only"`, BM25-only).
   - **Evolution-config-PRESENT installs — including THIS one** (the
     shipped `config.yaml` carries a `skill_evolution:` section, so
     `config.skill_evolution` is never None through `load_config`): the
     first boot turns on **full fusion** — the file corpus merges into the
     existing DB-backed search/auto-injection, and the P4 shadowing above
     takes effect immediately. The evolution LANE being OFF
     (`evolution_lane_enabled=false`) does not change this: it gates
     skill-evolution jobs, not the search stack.

**REST/FE consequence (A6/S2):** the REST skill list is DB-only in v1, so the
FE Skills page keeps showing the 3 legacy DB skills as active with NO shadow
indication. The shadowing is real but invisible in the UI until v2 — do not
"clean up" the DB rows by hand; they are inert in search and the file canon
owns the names now.

## Operational notes

- **Boot cost: zero.** Service construction is cheap (no LLM, no DB, no
  filesystem reads at init); the first index scan is lazy per root on first
  touch. `/readyz` shows an honest empty-roots census until something searches.
- **Logs.** Index scan INFO (counts + duration); per-skill validation WARNING
  (path + reason, warn-once per root); shadow decisions WARNING; cross-project
  leak-guard trips ERROR; EXTRA_ROOTS hygiene WARNINGs at boot.
- **GC.** `gc_sweep()` runs the A17 orphan-project purge (index rows for
  deleted projects) plus the A4 embedding gc; both idempotent, advisory-grade.
  **It is wired** (code-review W3): `MaintenanceService` job
  `project_skill_gc`, every `PROJECT_SKILL_GC_INTERVAL_HOURS` (24h, idle-gated,
  failure WARN-logged and retried next tick). No operator action needed; the
  first run fires on the first maintenance tick after boot. On-demand
  equivalent (manual, same effect, run against the daemon's DB): call
  `ProjectSkillSourceService.gc_sweep()` from a REPL holding the manager's
  file/embedding repositories — the maintenance job simply schedules that.
- **Root cap.** `MAX_ROOTS=32` per project (INFO-refuse beyond) — insurance
  against a runaway EXTRA_ROOTS/monorepo misconfig.
- **Rollback.** The lane is additive-only (3 new tables, zero FK, zero body
  columns). An old binary simply never references the tables — inert on
  rollback. Git-revert of the lane commits is a clean rollback.

> **A18.2 wording note (Phase-4 structural finding; W7 code-review fold):** the
> original decisions.md sentence ("THIS install runs evolution-OFF ⇒ first boot
> activates file-only search/list/view") describes the M10
> (evolution-config-ABSENT) shape — which, because `Config.skill_evolution` is
> non-Optional (`Field(default_factory=SkillEvolutionConfig)`), is **a
> test/defensive seam, not a production reachable mode**. So that sentence is
> wrong on two counts: (i) the config is never absent through `load_config`, and
> (ii) **"evolution lane disabled" ≠ "evolution config absent"** —
> `evolution_lane_enabled=false` gates evolution JOBS, and the shipped
> `config.yaml` carries a `skill_evolution:` section, so the first boot actually
> runs FULL FUSION (M9) with shadowing — the STRONGER activation. Both shapes
> are disclosed above; the Phase-4 report carries the derivation.

## Rollback

The lane is additive-only (3 new tables, zero FK, zero body columns). An old
binary simply never references the tables — inert on rollback. Git-revert of
the lane commits is a clean rollback.

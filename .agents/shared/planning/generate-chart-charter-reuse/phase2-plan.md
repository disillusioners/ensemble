# Phase 2: Carrier Docs (chart/skill.md) + Gated Charter Prompt Delta + Integrity Gate

## Objective

Broadcast the new default semantics to every chart-skill carrier: update the canonical caller documentation `agents/_prompt_system/innate-skills/chart/skill.md` (signature table + refine rule) as an EXPLICIT task with its integrity-gate run, and execute the charter-prompt delta ONLY as gated by D-charter-prompt-delta (default = none). No daemon code changes in this phase.

File targets:
- `agents/_prompt_system/innate-skills/chart/skill.md` — signature table `:41-46`; "One diagram per call" `:61`; "Refine, don't hand-edit" `:62`; re-call guidance `:56` area; cross-file ref to charter soul.md `## My Expertise` at `:80` (MUST stay stable — convention-v2 section reference).
- `agents/charter/{rule,soul,workflow}.md` — ONLY under P6=(c); current anchors: `rule.md:11` (functional agent), `rule.md:12` (NEEDS MORE INFO re-invoke convention), `rule.md:31` (per-instance temp-file isolation — filesystem-scoped, NOT conversation-scoped), `soul.md:34`, `workflow.md:43`, `workflow.md:165-207` (return shapes).
- `.agents/shared/conventions.md` — optional one-shot migration note (T3).

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | T1: chart/skill.md — signature row + refine-rule evolution + one-diagram-per-call note + wedged-charter operator guidance | P4 ADJUDICATED (`fresh: bool = False`) | Skill documents reuse default + fresh escape hatch + busy-reject ladder; integrity gate green |
| 2 | T2: charter prompt delta — ADJUDICATED P6=(a): verified no-op (C1 dormant) | P6 (resolved) | Branch-specific acceptance (below) |
| 3 | T3: conventions.md migration note — CONFIRMED by architect (default-semantics change is Certain for ~15 carriers) | Phase 1 merged | Note present; no agent prompt files touched |
| 4 | T4: doc-truth sweep + full prompt-integrity gate run | 1–3 | Zero stale "always spawns fresh" claims repo-wide; gate suite green |

### T1 — chart/skill.md update

1. **Signature table (`:41-46`)** — add row: ``| `fresh` | bool | no (default `false`) | Pass `true` to spawn a brand-new charter instead of continuing your existing one |`` (P4 ADJUDICATED CONFIRMED — this is the final kwarg shape).
2. **"Refine, don't hand-edit" (`:62`)** — evolve to: successive `generate_chart()` calls continue the SAME charter (it remembers your prior diagram from its conversation history), so refinement needs no re-pasting of the old chart; pass `fresh=True` for a clean slate when the new diagram must not inherit prior context. Keep the existing first sentence intact (it is the pinned contract carriers quote). Architect note folded in: today's always-fresh default made this documented contract a blind re-derivation — default-reuse FIXES documented behavior.
3. **"One diagram per call" (`:61`)** — reword (W4 — must match the busy-guard, not imply interleaving): **awaited successive calls** run one at a time on the same charter (their histories share the charter's context). **Truly concurrent in-flight calls are REJECTED** with `"Error: Charter busy; pass fresh=True for parallel charts."` — await each `generate_chart()` result before the next; pass `fresh=True` when you need parallel charts or a fully isolated history.
4. **Wedged-charter operator guidance (architect P7 addition, W3-reworded):** if `generate_chart` returns `"Error: Charter busy; pass fresh=True for parallel charts."` repeatedly (persistent `busy-reject` in the logs), a previous charter turn is likely hung. Ladder: **(1)** caller escapes immediately with `fresh=True` — the new charter wins discovery determinism (latest `last_activity_at`) so all subsequent reuse lands there; **(2)** an operator clears the hung orphan via manual `terminate_instance` through the daemon API — termination stops the wasted turn but does NOT retire the old charter from discovery (TERMINATED revives are free, `instance_messaging.py:1944-1953`); retirement comes from the `fresh=True` spawn, not the termination; **(3)** a daemon restart clears the in-memory busy/counter locks. A paused charter surfaces a distinct error: `"Error: Charter is paused; resume it or pass fresh=True for a new charter."` (exact string — source of truth for the Phase 1 T8.10 pin). There is deliberately NO charter-terminate tool surface (explicitly rejected — blast radius for a rare event). Also: if context compaction is ever observed mid-refine-loop, switch to `fresh=True` (compaction-fidelity guard, residual risk R2).
5. **Intro line (`:3`) reword (W5):** "it spawns the charter specialist internally" goes stale post-merge — planned replacement: "it **continues your charter specialist** internally and returns validated, render-ready Mermaid" (reuse means the specialist persists across your calls).
6. **Re-call guidance (`:56`)** — leave byte-identical (validation-warning re-call advice is unchanged by reuse).
7. Do NOT touch `:80` ("See charter's My Expertise") or any heading — integrity-gate v2 section references (`tests/unit/tools/test_prompt_section_reference_integrity.py:80-117` covers ALL `innate-skills/*`).
- Acceptance: `uv run python -m pytest tests/unit/tools/test_prompt_section_reference_integrity.py -q` green; grep shows `fresh` documented in the table and the refine rule, AND the busy-reject + paused error strings quoted verbatim (they are the pins' source of truth — Phase 1 T8.5/T8.10); `git diff --stat` shows exactly the files intended (multi-edit verification, M12).
- **Gates:** P4 ADJUDICATED (final kwarg name + default direction); P7 ADJUDICATED CONFIRMED (a) — skill.md is the ONLY carrier-facing surface; the operator-guidance sentence is the architect's P7/doc-duty addition.

### T2 — charter prompt delta (ADJUDICATED P6=(a): verified no-op; C1 dormant)

- **(a) none [ADJUDICATED]:** no file edits. Verification-only: grep `agents/charter/*.md` to confirm no prompt claims conversation memory that doesn't exist and nothing contradicts reuse (`rule.md:31` isolation stays as-is — it is about CONCURRENT instances colliding on `/tmp/charter_*` temp files, filesystem-scoped, not conversation-scoped; technical-analysis Axis 7 note; architect confirmed refinement works via checkpoint history alone). Acceptance: grep evidence recorded in the task notes; zero diffs in `agents/charter/`.
- **(b) message note [dormant]:** the code-side edit is Phase 1 task C1 (message trailing line + new pin); activation ONLY if the post-merge A/B shows the charter missing refinement intent; this phase then syncs skill wording. Acceptance when activated: Phase 1 C1 pins green + integrity gate green.
- **(c) prompt edit [rejected for v1]:** retained as historical record — minimal addition, one rule in `rule.md` after `:12`, headings stable (`soul.md` `## My Expertise` referenced from skill.md `:80`), single-fenced-block contract untouched (`rule.md:24`, `workflow.md:165-207`), integrity gate + tool-lane suite green.
- **Gates:** P6 ADJUDICATED CONFIRMED (a) — this task is a verified no-op; A/B question is post-merge, evidence-gated.

### T3 — conventions.md migration note (CONFIRMED by architect)

- One bullet in `.agents/shared/conventions.md` (**create the file if absent** — F10): as of this feature, `generate_chart` defaults to per-caller charter reuse; carriers that rely on per-call independence pass `fresh=True`. Concrete case (W7): **doc-writer** enriches multiple sections of one document with `generate_chart` diagrams (`rule.md:7`, `soul.md:6/22/63`, `workflow.md:20`) — its awaited successive calls share one charter sequentially; if it ever needs isolated chart histories per section, that is the pass-`fresh=True` case. Rationale: the default-semantics change is Certain (analysis R4.1) and silent for carriers whose prompts don't re-read the skill at decision time — the architect's ruling on the previously-optional call is YES.
- **Gates:** none — P4 ADJUDICATED CONFIRMED (default-reuse); the note documents the final behavior.

### T4 — doc-truth sweep + gate run

- Sweep `agents/` + `docs/` for stale claims that `generate_chart` "spawns a fresh instance/charter every call" — variant-tolerant (spaced and unspaced forms; enumerate-by-grep closure; born-false quotations grepped exactly). **Known hits to check explicitly (W7):** `chart/skill.md:3` ("spawns the charter specialist internally" — replaced per T1 item 5 with "continues your charter specialist") and the **doc-writer** references (`rule.md:7`, `soul.md:6`, `soul.md:22`, `soul.md:63`, `workflow.md:20`) — those five are caller-side usage references that remain VALID under reuse (they say "call `generate_chart`", which is unchanged); verify each, list it in the closure, and edit only if the wording asserts freshness/isolation rather than plain usage. Also `agents/project-manager/tools_note.md:59` (denial-by-name note, unaffected).
- Run the FULL gate suite: `uv run python -m pytest tests/unit/tools/test_prompt_section_reference_integrity.py tests/test_chart_tools.py -q`.
- Acceptance: sweep closure enumerated (even if empty); both suites green.
- **Gates:** none (runs regardless of flips — it verifies whatever landed).

## Coupling

- **Tight with Phase 1** — every wording choice quotes Phase 1's final kwarg name (`fresh`, P4 adjudicated), default direction, busy-error string, and the operator-guidance ladder.
- **Loose with Phase 3** — Phase 3 T4 consumes this phase's output for doc-truth checks; no test depends on Phase 2 internals.
- **Independent of** — daemon code (none touched here).

## Risks

- Integrity-gate failure from a broken section reference or renamed heading (Medium/Low) — keep headings byte-stable; convention-v2 `file.md → Section Name` shape only; gate run is part of T1/T4 acceptance.
- Skill wording drifts from the implemented kwarg (Low/Low post-adjudication) — P4 is final (`fresh: bool = False`, default reuse); wording proceeds directly.
- Charter prompt edit weakening the single-fenced-block contract under a future P6 activation (Medium/Low) — contract-pinned at `rule.md:24`/`workflow.md:165-207`; edit must not touch those sections; integrity gate + existing pins guard.

## Exit Criterion

`chart/skill.md` documents the new semantics + escape hatch + operator guidance and passes the integrity gate; charter prompts carry zero unverified memory claims (adjudicated P6=(a): no delta in v1); conventions.md note landed (architect-confirmed); doc-truth sweep closure enumerated; `git diff --stat` matches exactly the files this phase declares.

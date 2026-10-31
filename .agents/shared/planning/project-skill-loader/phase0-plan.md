# Phase 0: Evidence + Measurements (Light)

## Objective

Bound the plan's cost claims and pre-answer the P4 retirement census with four read-only measurements plus a filesystem inventory — no fixture harness, one session, zero source changes (technical-analysis §G verdict: LIGHT Phase 0 is warranted; the architecture does not depend on the outcomes).

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Canon census sweep across projects: enumerate `projects.main_directory` (read-only SELECT or registry read) and detect `.agents/*/skills/` trees per project; count namespaces + skill dirs | none | A table of project → {namespaces, skill dirs, legacy dirs} exists in the findings file; answers "does the gap multiply beyond agents-ensemble today?" |
| 2 | Cold-scan + stat-gate cost at canon scale: scratch script (in `/tmp` ONLY, fake-HOME fenced) walks `.agents/tester/skills/`, parses 5 manifests, hashes bodies; repeat stat-gate pass | 1 (optional) | Measured cold-scan ms + stat-gate ms recorded; pins the `last_scan_ms` budget and validates NFR-PERF-1 (≤2 s) headroom. **Advisory S3 promotion condition (architect 2026-10-31): if cold scan >50 ms ⇒ the dedicated bounded executor for file reads (S3) is promoted from SHOULD to MUST in phase2 task 2** |
| 3 | Embedding regeneration cost per digest flip: one measured run of 5 skills × 3-10 trigger queries through the embedding provider (if wired in dev) | none | Per-skill regen latency recorded; N/A acceptable if provider unavailable (record + note the BM25-only fallback path covers tests) |
| 4 | Live DB census of the shadow-retirement set: SELECT-only lookup of `9e79ccad`, `fd1e01dc`, `76e3e1a7a`, `08e67030` in `skills` (existence, name, generation, is_active) **PLUS the collision census across ALL in-scope file names vs DB names (case-insensitive) in this project** | none | Census table recorded; the P4 retirement diff at enable-time is a KNOWN set, not an assumption. **Gate output: the shadowed set must be EXACTLY the 3 known ids — see Named Gate below** |
| 5 | `.agents/` tree inventory of the ensemble project: list all `.agents/*/` namespaces, flag canon dirs (`*/skills/*/skill.json`) vs legacy dirs (`.agents/project-skills/`) | none | Inventory recorded; feeds the Phase-1 anti-conflation fixture design (FR-VAL-1 negative fixture) |
| 6 | Write `phase0-findings.md` into this planning dir with all measurements + caveats | 1-5 | Findings file committed to the planning dir; numbers cross-referenced into Phase-1 budget claims |

## Coupling

- **Tight with:** none (read-only).
- **Loose with:** Phase 1 (measurement 2 pins its `last_scan_ms` budget; measurement 4 pre-answers its shadow-census test fixture), Phase 5 (findings are the evidence baseline the gate cites for retirement-diff validation).
- **Independent of:** Phases 2-4 (their designs are already fixed by technical-analysis).

## Blast Radius

- **Files/dirs touched:** NONE in the source tree. Scratch scripts live in `/tmp` with mktemp FAKE_HOME fencing (never in the repo; never resolving live-shaped install paths). One planning-dir file (`phase0-findings.md`).
- **Core seam hit:** N.
- **Live contact rules:** DB census is SELECT-only, per-variable env scrub if any env fencing is needed (`env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT` — NEVER `env -u POSTGRES_*`, a silent no-op); zero writes to live rows; no daemon restarts; no boot smokes.

## Gate Tier

**Tier 0** — no regression leg (docs/planning-only diff). Changed-pack PASS covers the gate (here: no packs — findings review is the exit check).

## Named Gate: Collision Census (Q8 falsifier — architect A18.4, 2026-10-31)

This is a NAMED GATE with exit criteria, not a footnote. It is the Q8 knob-posture falsifier (decisions.md Part 5).

- **Assertion:** the Phase-0 census must show the project-scoped shadow set (case-insensitive name collisions between `.agents/*/skills/` canon names and in-scope DB `skills` rows) is **EXACTLY the 3 known legacy ids** — `9e79ccad`, `fd1e01dc`, `76e3e1a7a` (the `08e67030` supersedes-pointer row is censused for state but is not a shadow-collision finding unless it collides by name). **Census-dependent normalization (iteration 003): the count is CENSUS-DEPENDENT — 3 legacy ids per the commission; the 4th predecessor id `08e67030` appears in the canon's supersedes fields; the Phase-0 census is authoritative if it collides by name.**
- **PASS:** shadowed set == {the 3 known ids} ⇒ default-ON posture (fusion + shadow) proceeds unchanged; census recorded as a stage-time gate input.
- **FAIL (superset found ⇒ any additional collision beyond the 3):** RE-ADJUDICATE BEFORE STAGE — the fusion/shadow split re-enters (fusion ON, shadowing gated — Option C's only legitimate use, architect A18.4). The re-adjudication is a stage-time gate input: staging MUST NOT proceed on an unresolved superset census. Record the extra collisions in `phase0-findings.md` and route to the leader/architect.
- **Method:** SELECT-only (task 4 discipline); census pairs every canon skill name against in-scope DB rows in this project's scope.
- **Provenance:** architect A18.4 (MUST) + leader ruling 2026-10-31 (Q8 posture falsifier); decisions.md Part 5.

## Dependencies

None (first phase).

## Exit / Acceptance Criteria

- `phase0-findings.md` exists with: project census table, cold-scan + stat-gate timings, embedding-regen cost (or N/A + fallback note), shadow-set census (exact rows found), `.agents/` inventory.
- **Collision Census gate EXIT CRITERIA (approver P1, 2026-10-31):** the superset condition is part of Phase-0 exit — if the collision census finds a shadowed set LARGER than the 3 known legacy ids (`9e79ccad`/`fd1e01dc`/`76e3e1a7a`), Phase-0 exit is CONDITIONAL on re-adjudicating the fusion/shadow split before stage (fusion ON, shadowing gated — architect A18.4 falsifier). Phase 0 does NOT exit green on an unresolved superset.
- Every measurement carries its method + caveat (SELECT-only, fake-HOME fence, per-var env scrub).
- Zero source-tree modifications (`git status` shows only the planning dir).

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Live-DB census accidentally writes | High (live data corruption) | SELECT-only discipline; no ORM session writes; read-only connection if available |
| Scratch script resolves live install paths | Critical (live-install deletion class, 2026-10-12) | mktemp FAKE_HOME + escape guard; scripts never in repo |
| Env scrub idiom silently no-ops (`env -u POSTGRES_*`) | High (prod contact) | Per-variable scrub, one `-u` per var |
| Embedding provider call burns quota unexpectedly | Low | One bounded run (5 skills × ≤10 triggers); skip if provider unset |

# Lesson: pack cleanup must be surgical at ROW granularity — file-level `rm -f` destroys cross-lane evidence

Date: 2026-11-10 · Gate: skill-revival Phase 1 tester gate (RESULTS/2026-11-10-skill-revival-phase1-tester-gate.md, finding F-G1)

## What happened
`test/packs/metrics_capture_lane_off_seam_unit_test.sh` leg M-C2d plants a leak marker into the capture-observability sink, then cleans it via `clean_marker_leaks()`, which does `rm -f` on the WHOLE dated sink file (`data/skill-capture-observability/skill-capture-YYYYMMDD.jsonl`) whenever the planted marker is present. The sink appends ALL lanes into one daily file (`skill_capture_observability.py:451/:455`, `open(path, "a")`). Result: the pack deleted 116 rows (26 pre-gate + 90 the gate itself had just measured), gitignored ⇒ unrecoverable. The pack's own comment ("a concurrent lane's residue is never touched") was true at marker granularity and false at file granularity.

## The rule
- **Any pack that plants-and-cleans artifacts in a SHARED append-only file must clean by row-rewrite** (read → filter out rows carrying the pack's unique marker → atomic rewrite), never by deleting the file.
- Plant markers must be unique per run (`agent-c2-<uuid>` style) so the filter cannot match foreign rows.
- A gate (or any runner) must treat the shared sink as evidence: **snapshot bytes before the run**, diff after, and treat any shrink as an incident — a clean END state can be the residue of destruction, not cleanliness (this gate's dispatch literally asked for "0 files / 0 bytes at END"; the file reached 0/0 by deletion).

## Also recorded here (same gate, same class of "infrastructure lies to you")
- **Pack internal budgets > the 300s gate cap are registration violations**: `skill_counter_drift_unit_test` shipped BUDGET_S=600 — the outer cap killed it at 300s mid-mutation-leg with NO RESULT line (bare rc=124). Split landed (`60972185f`): a–d 189s + e–h 185s, both < 240s. Any pack whose measured wall under load approaches 240s (`skill_id_verbatim_contract` hit 288s) is a split candidate NOW, not after it breaches.
- **Partial auth scrub is worse than none**: the prescribed 2-var `ENSEMBLE_AUTH` scrub left 5 family vars alive → `create_app()` collection ERRORs that MASK the route-census red the census files exist to report. Always scrub the FULL `ENSEMBLE_AUTH_*` family per-variable and verify `env | grep -c ENSEMBLE_AUTH` == 0 before the first pytest.

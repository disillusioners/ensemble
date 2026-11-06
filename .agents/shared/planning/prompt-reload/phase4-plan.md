# Phase 4 — Tests/pins (the tester-gate contract)

## 1. Objective

The complete, explicit test enumeration the tester functional gate consumes. Environment: dev daemon :8079, throwaway PG, stub LLM (the probe's harness shape). **Zero live contact for the whole gate.** Suggested packs: `tests/unit/services/test_prompt_refresh_primitive.py`, `tests/unit/tools/test_prompt_reload_tool.py`, `tests/unit/tools/test_prompt_reload_refusals.py`, `tests/unit/tools/test_prompt_reload_pins.py`, + the dev-daemon E2E leg (probe harness shape).

## 2. Decisions

| ID | Decision | Rationale | Rejected alternatives |
|----|----------|-----------|----------------------|
| D18 | Every functional leg names its assertion set and its mutation companion up front; every mutation leg must be RED at the planted mutation and GREEN at HEAD (digest-proven, house standard). | Tester-gate contract quality; prevents vacuous pins (house canon: comparator + FAIL-LOUD VACUOUS guard). | Ad-hoc test discovery during the gate. |
| D19 | Manifest-tamper legs run on a COPY/scratch tree, NEVER the live tree; live-fence leg L4 proves the env-aware validation both ways. | R6 + live-fence discipline; env-poison family conventions (per-variable env scrub — NEVER `env -u POSTGRES_*`; `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned on any frozen-binary smoke; scratch PG dropped after). | Tampering any real tree. |

## 3. Tasks — functional legs (dispatch legs 1–6, enumerated)

| ID | Leg | Assertions (testable) | Mutation companion |
|----|-----|----------------------|--------------------|
| L1 | **Sentinel reload E2E** (dev daemon :8079, throwaway PG, stub LLM): append a sentinel line to an ari artifact in the ACTIVE tree (dev harness tree) → invoke `prompt_reload` via the tool path. | (a) tool returns diff + provenance; (b) CURRENT turn completes on the OLD persona (no mid-turn change); (c) NEXT dispatch carries the sentinel (P3 shape); (d) history intact — message count unchanged, no duplication (P1/P2 shape); (e) in-flight-turn survival: REST probes 200 during the window. | M1 |
| L2 | **Idempotent no-op + force**: unchanged fingerprint → invoke → (a) clear "no changes" message; (b) `manager.instances` entry STILL PRESENT (assert); (c) full provenance present (D17 dry-run value). Then `force=true` on the unchanged tree → pops anyway, message says forced. | Both sub-paths proven; no-op provably does not evict. | M2 |
| L3 | **Refusal ladder** — each typed code proven INDEPENDENTLY: (a) `prompt_reload_dir_invalid` (live-shape fixture pointing outside `<install>/releases/`; dev fixture pointing at a foreign path); (b) `prompt_reload_manifest_missing_live` + `prompt_reload_manifest_mismatch` (tamper a COPY/scratch manifest, D19); (c) `prompt_reload_compose_failed` + `prompt_reload_db_unavailable` (injected loader/repo faults → fail-closed); (d) `prompt_reload_disabled` (kill-switch OFF); (e) non-ari caller denied: category gate (default-grant universe excludes), allow-list gate, AND the in-body factory-captured agent_id layer (notify_user.py:28-29 literal-name bypass). | Every code has ≥1 dedicated test; no code reachable only via another's failure. | M3 |
| L4 | **Live-fence proof**: self-env=live → resolved dir must be under `<install>/releases/` (fixture trees both sides); dev → repo path accepted; divergence reporting (`current_matches_running`) proven for matched AND flipped-symlink fixtures. Zero live contact across the WHOLE gate (fence assertion). | Fence matrix green; no live daemons/files touched. | — |
| L5 | **Mutation legs** (each RED at plant, GREEN at HEAD, digest-proven): **M1** remove the pop (tool claims success but next turn does NOT carry the new persona → RED); **M2** skip the fingerprint check (no-op path never taken → RED); **M3** allow-list widening (any non-ari agent gains access → RED via gate tests); **M4** hand-edit `tool-categories.json` (F-1 trap) → RED via the generator/baseline artifact check. | 4/4 mutation legs meaningful; planted-vs-HEAD digests recorded. | — |
| L6 | **Pin/census legs**: privileged pin census updated to the new site count; category registered with ari-EXACT allow-list (pin + runtime surface: ari has it, control agent does not); `VOCABULARY_VERSION` 7 == artifact field; **prompt-artifact baseline UNAFFECTED** — `agents/.prompt-artifacts.sha256` stays 181 rows, `--check` clean (D7: this lane adds NO agent prompt files; if implementation diverges from D7, re-stamp rules apply and the divergence must be disclosed explicitly in the lane report). | Census count asserted; artifact pack green; baseline 181/181. | — |
| L7 | **Concurrency/edge pins** (OQ1 evidence): pop during own in-flight turn survives (P3 shape, harness-level); no-op never pops (with L2); P4/P5 forbidden-mechanism greps (`_release_cached_instance` absent from the module; no graph construction; no closure mutation). | Edge pins green; forbidden mechanisms structurally absent. | — |

## 4. Acceptance Criteria

| ID | Criterion |
|----|-----------|
| AC4.1 | L1 passes end-to-end with all five assertion groups (a)–(e). |
| AC4.2 | L2 proves no-op (no eviction, message + provenance) AND force-on-unchanged (pop + forced wording). |
| AC4.3 | L3: all 10 refusal codes independently proven; the three-layer denial chain proven for a non-ari caller. |
| AC4.4 | L4: env-aware validation matrix green; `current_matches_running` both ways; zero-live-contact fence holds for the entire gate. |
| AC4.5 | M1–M4 all RED-at-plant / GREEN-at-HEAD, digest-proven. |
| AC4.6 | L6: census updated, ari-exact allow-list pinned, artifact/version match green, baseline 181 unchanged. |
| AC4.7 | L7: edge + forbidden-mechanism pins green. |

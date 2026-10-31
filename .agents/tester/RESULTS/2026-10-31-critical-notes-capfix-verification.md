# Critical-Notes Cap-Fix + Remove-By-ID Resolver — Verification Gate (2026-10-31)

**Branch:** `feature/critical-notes-cap-fix` @ **`c8252f916`** (chain `78f1aaead` → `5aa92c915` → `c8252f916`; fix round +962/−149 over 13 files)
**Base:** `78f1aaead` (latest). **Worktree:** `agents-ensemble-wt-critical-notes-cap-fix` (own `.venv` 3.13.3; fenced `.env` → PG localhost:5432, user `ensemble`, DB `ensemble_dev`).
**Workers:** 10 lanes (e8022d76† replaced ×2 upstream-rate-limit, d01b077f† report lost — unique deliverable re-run by 60737b68, 57cb15fe, eb125596, 44e41fc1, c7357d18, 309b2786, 09c3ee84, df02823e ×2, 0b5cc7c2 ×2, e094e92a, 30d31a48, e71ab5de, 4b0f7bfe, 60737b68). † = Fan-In escape-valve replacements, max 1 each.
**Verdict: SHIP.**

---

## 1. Scope Decision (blast radius, ensure.md Tier-1)

Single-feature lane, no core seam touched (diff: `daemon/repositories/project/{critical_note_ids.py(A),models.py,repository.py}`, `daemon/tools/critical_notes.py`, `daemon/services/context_messages.py`, `daemon/services/critical_notes_selection_orchestrator.py`, `daemon/config.py`, `docs/usage.md`, `CHANGELOG.md` + 4 test files). Tier-1 scoped blast-radius leg; full suite NOT run (per gate doctrine and brief item 5). Families run = all critical-notes families + project-family importers + PG lane. `tests/test_api.py`-style stale reds not in blast radius (no router/manager files touched).

## 2. Counts (all invocations @ `c8252f916`, `timeout 300` outer, env-scrubbed `-u POSTGRES_* -u DATABASE_URL -u SSL_*`, census-first, `-p no:randomly`)

| # | Lane (worker) | Scope | Result |
|---|---|---|---|
| 1 | 44e41fc1 | `tests/unit/repositories/test_critical_note_id_resolution.py` (NEW) | ✅ 15/15, 0.85s |
| 2 | c7357d18 | `tests/unit/repositories/` (int4_repositories) | ✅ 242/242, 10.70s |
| 3 | 309b2786 | tools family: test_critical_notes.py + lifecycle | ✅ 88/88 (49+39), 12.62s |
| 4 | 09c3ee84 | schema (23) + api (14) | ✅ 37/37, 0.69s |
| 5 | df02823e | `test_critical_notes_render_phase1.py` | ✅ 23/23, 0.30s |
| 6 | df02823e | phase2 family (orchestrator 19 + filters 10 + selection 18 + bm25_safety 13 + r25_id 6) | ✅ 66/66, 0.41s |
| 7 | 0b5cc7c2 | adjacents A: importer set in the 2 unit dirs | ✅ 121/121, 16s |
| 8 | 0b5cc7c2 | adjacents B: `tests/test_project*.py` ×6 | ✅ 271/271, 18s |
| 9 | 4b0f7bfe | `tests/postgres/test_critical_notes_repository_pg.py` (PG 14.22 scratch) | ✅ 2P `-m postgres` + 2P no-marker |
| — | 4b0f7bfe | PG parity harness (throwaway DB) | ✅ 11/11 probes |

**Unit-side union: 729 executed / 0 failed / 0 skipped / 0 timeouts. Developer's scoped claim (182P) is a subset — confirmed and exceeded.**

## 3. A/B Defect Proof (brief item 1) — worker 30d31a48

Identical harness both legs (sha256 `0a18b9cc…574c`, printed in-log), import-proof per leg (`daemon.__file__` under each tree root; base leg = detached worktree `/tmp/cncap-base-78f1aaead`, removed + pruned after; porcelain byte-identical pre/post). SQLite in-memory, tree's own SQLModel metadata, legacy-row seeding via raw session.add (bypasses HEAD's `__init__` normalization; `stored_bytes_ok` self-check on every seed).

| Variant (stored bytes) | BASE 78f1aaead | HEAD c8252f916 |
|---|---|---|
| UPPERCASE | listed ✓, remove(canonical)=**False**, row stays, get=None | remove=**True**, row gone |
| TRAILING SPACE | same defect | closed |
| U+200B PREFIX | same defect | closed |
| UN-HYPHENATED 32-hex | same defect | closed |
| MALFORMED 5-char 4th group | unresolvable via canonical (limitation) | same (W1 asserted limitation); byte-exact stored-bytes retry removes on BOTH legs |
| CANONICAL control | removable (harness sane at base) | removable |

Resolver returns STORED bytes — PK never rewritten. Evidence: `EVIDENCE/2026-10-31-cn-capfix-ab-defect-proof.md`.

## 4. Mutation Gate (brief item 2 / amendment item 3) — worker e71ab5de

Scratch-clone canon recipe (cp -a → /tmp, git detached, `git-common-dir` verified INSIDE scratch, `.pth` + both pytest shebangs rewritten, `__pycache__` purged 55 dirs, import-proof every leg via realpath). Baseline 126 GREEN; final post-restore 126 GREEN. Every leg: exit exactly 1 + 'failed' summary, `git diff --stat` = ONE site, restore sha == pre-sha.

| Leg | Mutation | RED nodes (verbatim) | Verdict |
|---|---|---|---|
| M1 (W4) | exact-PK fast path restored in `_resolve_critical_note_id` | `test_exact_hit_with_representation_twin_is_ambiguous` (+2 collateral: degraded-line, tri-state) | MUTATION-PROVEN |
| M2 (W2) | fingerprint `fullmatch`→`search` | `test_junk_prefixed_id_never_fingerprint_matches`, `test_predicate_rejects_junk_and_blank_unit_level` | MUTATION-PROVEN |
| M3 (W8) | `section_cap = None` unconditional | 4× `TestRenderAllSectionCap` (large-set-bounded, cap-cannot-cut-trailing-line, cap-installable, build-message-bounded) | MUTATION-PROVEN |
| M4 | `resolved = entry_id` bypass in remove | 7/13 at REPOSITORY seam (all 4 `test_variant_resolves_and_removable[*]` + junk/twin/degraded); tool file 5/5 GREEN — mechanism: `project_cn_remove` calls resolver-routed `get_critical_note` FIRST (double-cover, documented) | MUTATION-PROVEN |
| M5 | `_MAX_ENTRIES` 200→50 | `test_cap_regression_51_preexisting_accepted`, `test_max_entries_is_200`; 2 boundary nodes survive BY CONSTRUCTION (`range(_MAX_ENTRIES)`-built, cap-value-agnostic) | MUTATION-PROVEN |
| M6 | normalize-on-update PK rewrite injection | pk node RED at `assert raw_id == variant` | PK PROBE REAL |

**PK-immutability probe audit: LOAD-PATH-REAL + WRITE-PATH-REAL** — raw-SQL legacy insert (`:279-280`), ORM-external raw-SELECT compare (`:290-297`); not a construct-only tautology. Residual (noted): add-path PK preservation outside this probe's coverage. Evidence: `EVIDENCE/2026-10-31-cn-capfix-mutation-gate.md`.

## 5. Fix-Round Claim Verification (amendment) — worker eb125596

All W1–W9 CONFIRMED on tree (CHANGELOG rescope + forensic note; fullmatch `[0-9a-f]{32}` both sides after `[\s\-]+`-only strip; `empty_after_normalize` pre-scan guard; exact-PK fast path REMOVED — scan subsumes, twin+exact ⇒ `AmbiguousCriticalNoteId`; "matches N entries' representation" + caplog pin at repository logger; `select(id)` + `(id, superseded_by_id)` projection + bulk UPDATE; render-all bounded 12000 via `_resolve_critical_notes_section_char_cap`, tiered never re-capped, canonical truncation line; real project-B cross-project leg). Dead constant `CRITICAL_NOTES_MAX_ENTRIES` = 0 hits. `_MAX_ENTRIES = 200` @ `daemon/tools/critical_notes.py:70`. Evidence: `EVIDENCE/2026-10-31-cn-capfix-delta-map.md` (451 lines).

## 6. PG-Dialect Parity (brief item 3) — worker 4b0f7bfe

PG 14.22 (Homebrew, aarch64). Registered file 2P under BOTH marker forms (conftest `PG_TEST_*` read first). Ad-hoc harness on throwaway `ensemble_test_cncap_parity_43592`: 11/11 probes — matrix IDENTICAL to SQLite (variants removable via canonical; junk-prefixed ×2 refused with control surviving; twin + byte-exact ⇒ `AmbiguousCriticalNoteId(match_count=2)`, both rows untouched; malformed = limitation + exact-bytes escape hatch; control removable). Matching is pure-Python tri-state over a project-scoped id-column scan — no dialect divergence. Teardown: DROP … FORCE, `pg_database` count 0, backends 0; **ensemble_prod connection census 17 before == after; ensemble_dev 0 throughout** (never a write target). Evidence: `EVIDENCE/2026-10-31-cn-capfix-pg-parity.md`.

## 7. Cap Boundary + 50-Remnant Sweep (brief item 4) — workers 309b2786, 60737b68

Green pins: `test_cap_boundary_at_exact_cap_rejected` (200th add rejected), `test_cap_boundary_one_below_accepted` (199), `test_cap_regression_51_preexisting_accepted` (legacy 51 stores), `test_max_entries_is_200`, pinned-cap-8 family (`test_cap_8_reject_names_demotion_candidates_and_changes_nothing`, `test_cap_counts_only_active_pins_not_superseded_ones`), `test_add_above_old_30_threshold_still_succeeds`, `test_cap_message_names_current_count`. Sweep (daemon/docs/frontend/src/agents/tests/CHANGELOG, excludes venv/dist/etc.): **ZERO stale user-facing 50-cap texts**; every CN surface says 200; FE has 0 critical-notes files. Evidence: EVIDENCE/cn-capfix-gate-ledger.md + banner.

## 8. Classified Red Ledger

**Branch-caused failures: 0. Pre-existing failures encountered: 0 (no reds anywhere — no A/B adjudication inputs).** Non-test defects found & classified:
- `cn_injection_unit_test` PACKS.md row DEFECTIVE — target deleted pre-base (`eeef8845a`, 2026-08-01, ancestor of base) ⇒ registry drift, NOT branch-caused. Row marked DEFECTIVE by tester (registry edit only).
- `int4_repositories` 116P baseline stale (2026-09-04-era) — dir was 227 at base. Row re-baselined to 242 @ c8252f916.
- Registry name drift: render phase2 orchestrator file is `test_critical_notes_phase2_orchestrator.py` (no "render" prefix) — covered, nothing deleted on the branch (locator e094e92a).

## 9. ensure.md (scoped, Tier-1)

- Core #1 (no regressions in changed packs): ✅ all scoped packs PASS (table §2).
- Core #2/#3 (concurrency_atomic): scoped OUT — no DB-helper/event-loop/concurrency code touched (same single-tool blast-radius scoping as the 2026-09-10 critical-notes matcher gate).
- Core #4 (dev.sh `--timeout-graceful-shutdown 10`): PASS-by-unchanged — `dev.sh` not in the branch diff; last verified green at dev.sh:99/:102 (2026-09-22 gate).
- Release Gate: NOT triggered (single-feature lane, no core seam).
- Contradictions: none (ensure.md methods are pack-mapped and were followed as such).

## 10. Follow-ups (non-blocking)

- F1 🟢 M4: tool-level routing pin is double-covered by the repository seam — add a tool-only pin if independent detection at the tool seam is wanted.
- F2 🟢 M5: `test_cap_boundary_at_exact_cap_rejected` / `_one_below_accepted` build via `range(_MAX_ENTRIES)` and are cap-value-agnostic — the value pin + 51-row pin are the actual killers; consider pinning the boundary at a literal count.
- F3 🟢 PK add-path preservation is outside the current probe's coverage (load+update covered).
- F4 🟢 PACKS.md: register a successor row for CN injection/render coverage (render phase1 + phase2 family) under this gate's banner (done in-banner; a permanent table row can follow).

## 11. Artifacts

- This report: `RESULTS/2026-10-31-critical-notes-capfix-verification.md`
- Evidence: `EVIDENCE/2026-10-31-cn-capfix-delta-map.md` · `…-ab-defect-proof.md` · `…-mutation-gate.md` · `…-pg-parity.md` · `cn-capfix-gate-ledger.md`
- /tmp logs (this lane): `cncap_repo_newfile.log`, `cncap_repo_dir.log`, `cncap_tools_family.log`, `cncap_schema_api.log`, `cncap_render.log`, `cncap_phase2_family.log`, `cncap_adj_project.log`, `cncap_adj_project_top.log`, `cncap_ab_head.log`, `cncap_ab_base.log`, `cncap_pg_parity.log`
- PACKS.md: gate banner + re-baselined rows (int4_repositories 242P; cn_tools 49; cn_schema 23; cn_api 14; cn_injection DEFECTIVE). Uncommitted for giter, as instructed.

## 12. Overall

- Family + adjacency runs: ✅ (729 unit + 4 PG-file + 11 PG-probe executions, 0 failed)
- A/B defect proof: ✅ reproduced at base / closed at HEAD
- Mutation gate: ✅ 5/5 + M6; PK probe real
- PG parity: ✅ identical matrix
- Cap boundary + sweep: ✅
- ensure.md (scoped): ✅
- **Verdict: SHIP @ c8252f916.**

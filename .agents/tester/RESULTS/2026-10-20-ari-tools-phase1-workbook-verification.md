# ari-tools Phase 1 — The Workbook: Verification Report

Date: 2026-10-20
Tester instance: (session ec3a03e8) — Test Leader; 10 worker instances (see §8)
Worktree: `/home/nea/ensemble-src-wt-ari-tools` · Branch: `feature/ari-tools` · HEAD: `20cd96bcab715957566e46a694f8abed2e2427ba`
Cumulative range verified: `a01aed0a6..20cd96bca` (7 commits, ancestry confirmed) — 20 files, +4244/−37
Binding spec: `.agents/shared/planning/ari-tools/phase1-plan.md` + `decisions.md` (A-ADJUDICATED-3 RD-1/RD-2 honored)

## Verdict

**🟢 PASS — all 10 packs green; ZERO product defects found in phase scope; original capability verified end-to-end through the REAL tool layer and on real PG.** One live-collateral incident during the boot smoke (NOT a board defect — dev.sh exit-trap hazard; §6) and one watch item (xdist census artifact; §7).

### Scope Decision
Full suite NOT warranted: single-feature additive change (board subsystem + 2 meta.json allow entries + 5 SAME-PR pin touches), no cross-module refactor, no release gate. Ran 10 scoped packs (all dual-layer `timeout 300`), incl. ensure.md Core scoped to the change set. Release Gate NOT run — deliberate.

---

## 1. Per-focus-area results (the 7 commission verification items)

### 1.1 Cross-instance global namespace (REAL tool layer) — ✅ PASS
- **Tool layer (in-memory SQLite, REAL `BoardService`, zero mocks):** `tests/unit/test_board_gap_pins.py::test_gap_item_created_by_ari_is_visible_to_the_leader` — factory A (`current_instance_id="inst-ari-ctx"`, agent `ari`) creates; factory B (`inst-leader-ctx`, agent `leader`) lists and SEES the id; `created_by_instance == "inst-ari-ctx"`; note appended via B stamps `author_instance == "inst-leader-ctx"`. Third factory (`agent_id="worker"`) over the SAME service: all four tools → `"ERROR:"` envelopes, row count unchanged (`test_gap_unauthorized_factory_sees_nothing_and_changes_nothing`).
- **Real PG (daemon-created tables):** T11 boot smoke e2e — identical proof on `ensemble_dev` PG, 23/23 assertions.
- **Structural:** schema carries NO partitioning column (no `context_key`/`tree_root_id`/`source`) — the deliberate opposite of `shared_meta_kv` (plan D1). Mutation-verified: injected per-tree scoping → namespace pin fails.

### 1.2 Dual gate — ✅ PASS
- **Category gate (privileged, entry #9):** `PRIVILEGED_TOOL_CATEGORIES` exact-set asserted in BOTH integration pin files (`test_service_tool_flag_off_byte_identical.py:823-862`, `test_maintenancer_spawn_resolves_tools.py:333-357`) with `"board"` ninth; P2 `TestPrivilegedCategory` 3/3 (membership + exact set + ordinal 9). Privileged default-deny: empty-allow agent → **0** board tools; agent with NO `tools` key → **0** (real `create_instance_tools` factory, P2 `TestToolVisibility` 4/4).
- **Body gate under the literal tool-name bypass** (`daemon/tools/instance.py:478-479`, pre-existing, untouched by the range — recon diff of all 4 hunks): `test_gap_literal_allow_entry_grants_visibility_only` — synthetic agent `allow=["board_list"]` → `board_list` IS resolved (bypass proven) while the other three are NOT; REAL `board_list` invoked with `agent_id="worker"` → typed error `"ERROR: board operation not permitted for this agent"` + **zero side effects**. Category arm: `allow=["board"]` → all four (P9 + P2).
- **Fail-closed on empty/None:** `_is_board_authorized(None)` is False (P9, unpinned before); `""` refused on all four tools with `assert_not_called()` service proof (P2 parametrized `worker, watcher, tester, designer, ""`).
- **Allowed set exactly {ari, leader}:** `BOARD_AUTHORIZED_AGENTS == frozenset({"ari","leader"})` pinned (P2 + P9); `"board"` present in exactly `agents/ari/meta.json` + `agents/leader/meta.json` (entire `agents/` delta vs base = those 2 files, prompt artefacts byte-identical).

### 1.3 F1 regression — pagination metadata REACHABLE (service-backed, NOT service-mocked) — ✅ PASS
- `tests/unit/test_board_service.py::TestListPaginationMetadata` (9 tests) runs against the **REAL BoardService** with a real-tool bridge (`_real_tools()` at `:749-760`; class docstring: a mocked service "would have re-pinned the fiction"):
  - limit<total → `has_more is True` + truthful counts (`count==2`, `len(items)==2`, `applied_limit==2`)
  - limit>total → `has_more is False`
  - limit>200 → clamp VISIBLE: service `applied_limit==200`; tool payload `clamped is True` + `requested_limit==500`
  - exact boundary both sides: `limit==total` → False; `limit==total−1` → True
  - filter-aware `has_more` (respects active filter, not table size); in-bounds payload carries NO `clamped`/`requested_limit` keys
- **PG corroboration** (T11): `board_list(limit=2)` on real PG → `has_more=True, count=2, applied_limit=2`.
- **`applied_limit` additive-only / frozen seam:** `git log -G 'def list_items|def get_item' a01aed0a6..20cd96bca -- daemon/services/board_service.py` → **exactly ONE commit (041ceec93, the creating commit)** — the F1 fix (70de770a4) did NOT touch the def lines. `applied_limit` exists ONLY as a returned-dict key (service `:425`) read by the tool layer (`board_tools.py:165`, re-emitted under `_pagination` `:169`) — it appears in NEITHER service signature. HEAD signatures match plan §5.2 keyword-for-keyword (`list_items(*, status, project_id, tag, due_before, limit, with_notes)`, `get_item(*, item_id)`).

### 1.4 MUST-FIX constraint behaviors — ✅ PASS
- **`due_before` non-UTC offset (the architect-caught inversion):** service probe `test_due_before_mixed_offset_does_not_invert` (`+07:00` == 01:00Z vs stored 00:30Z/01:00Z/02:00Z — returns only genuinely-earlier rows) + `_to_utc_iso` round-trip byte-identity + naive rejection; NEW tool-layer probe `test_gap_tool_due_before_does_not_invert`. **Contract note:** the filter is **strictly** before (plan §7.1 + docstrings); the gap pin pins the documented contract with equivalent discriminating power (strictly-between filter → exactly the earlier item; on-boundary → empty; raw-TEXT comparison verified by direct SQL to invert both — the pins bite the defect class).
- **Duplicate input tags deduped at service level:** `["x","y","x","x"]` → `["x","y"]` order-preserving, asserted on create return AND get re-read.
- **Tags IMMUTABLE post-create:** triple proof — schema property (`BoardUpdateInput` has no `tags` field; full update field set pinned `{id,status,note,add_job_id,body,due_at}`), runtime (stray `tags` key never reaches `update_item`), structural (`update_item(tags=...)` → `TypeError`, P9). Junction rows written only at create.
- **`due_at=''` fail-closed (was docstring-only, zero tests — gap closed):** P9 ×5 — service `create_item(due_at='')` / `update_item(due_at='')` / `list_items(due_before='')` all → `BoardValidationError`; tool `board_create`/`board_update` with `due_at=''` → `"ERROR:"` envelopes; zero rows created. Never a silent clear.
- **Notes append-only:** PRIMARY SQL-capture guard — the ONLY statements ever touching `board_notes` are INSERT/SELECT (before_cursor_execute listener; UPDATE/DELETE explicitly asserted absent); count monotonic `[1,2,3,4]`; static source scan; no `update_note`/`delete_note`/`edit_note` API exists; no mutation expressible through the tool surface (4 tools only; stray keys never reach the service); schema itself has no edit column; child FKs NO ACTION; no `board_delete` tool.

### 1.5 Migration reality — ✅ PASS (`tests/migration/test_board_index_parity.py` 15/15 + NEW `test_board_runner_apply_rollback.py` 14/14, mutation-verified)
- **Real `MigrationRunner` apply:** ledger row `20261020_060000` recorded; 3 tables; **exactly 6 declared indexes** (`idx_board_items_status_updated/updated_at/project_id/due_at`, `idx_board_notes_item_id_ts`, `idx_board_item_tags_tag_item`) — plus 2 SQLite-internal `sqlite_autoindex_*` PK rows asserted explicitly (no silent filter); NO auto-named `ix_board_*`.
- **Idempotency (IF NOT EXISTS):** second `run_pending_migrations()` → no-op, still 6 indexes, 1 ledger row.
- **Rollback (`-- DOWN` works):** `rollback_migration('20261020_060000')` → all 3 tables GONE (child-before-parent drop order), 6 indexes GONE, ledger row removed, `schema_migrations` survives. Round-trip re-apply restores a schema matching the models **column-for-column**.
- **Prefix hygiene:** `ls daemon/migrations/versions/ | grep '^20261020_'` → exactly 1 file; the only duplicate prefixes are the two known pre-existing ones.
- **Non-blame proof:** full-dir fresh-SQLite dies at pre-existing `20260714_000001` (verbatim: `ALTER TABLE job_queues DROP CONSTRAINT IF EXISTS … near "EXISTS": syntax error`) with create_all, at `20240101_000001` without (`no such table: projects`) — the board file is NEVER the blocker; with the two known blockers ledger-pre-marked, the board file applies cleanly inside the real 96-migration sequence.

### 1.6 T11 boot smoke (owed to tester) — ✅ PASS (30s; first-run incident see §6)
- `./dev.sh` on the dev fence (8079 / `ensemble_dev` / `ENSEMBLE_SELF_ENV=dev`): **livez 200** `{"status":"alive","version":"0.25.4"}`, **readyz 200** (database/queue_freshness/services/checkpoint_saver all true) — time-to-healthy 21s. Banner `Starting Ensemble v0.25.4`; PG engine target `127.0.0.1:5432/ensemble_dev`; zero prod references; migration-runner line correct for PG.
- Schema on `ensemble_dev`: exactly the 3 board tables + the 6 `idx_board_*` + 3 structural `*_pkey`, **0** auto-named `ix_board_*`. (E1 nuance: tables pre-existed from an earlier same-lane dev boot, so this boot's create_all was a no-op for them — the wiring is proven by `manager.py` late-import-before-create_all + the 6 model-declared indexes + PG runner no-op line; tables were NOT dropped to force from-scratch, per fence discipline.)
- Cross-instance e2e through REAL tools on the daemon-created PG tables: 23/23 — namespace visibility, authorship stamps (`created_by_instance` unspoofable), `+07:00` → `02:00:00.000000+00:00` storage normalization, worker typed refusal with zero side effects (counts unchanged), PG pagination truthful.
- Cleanup: rows deleted (baseline 0/0/0 restored), port 8079 freed, no orphan processes; **9797/8088/7979 never contacted, signalled, or killed** (listed only).

### 1.7 Query pins — ✅ PASS
- Service layer (pre-existing pins): `list` without notes → **exactly 2** statements; `with_notes=True` → **≤3** (exactly 1 batched tags query + 1 batched notes query, each ` IN (`); the limit+1 probe → **zero** extra queries (probe lives INSIDE the items LIMIT).
- Tool layer (NEW, gap closed): REAL tool invocations under a statement counter — `board_list` → **exactly 2** (including when `has_more=True`); `board_get` with notes → **≤3**. Mutation-verified: one extra statement in the list path → both budget pins fail.

---

## 2. The 5 pre-existing flags (reviewer council ce047910) — dispositions

| # | Flag | Disposition |
|---|---|---|
| 1 | Integration pin files deselected by default addopts — run BOTH explicitly (22 expected) | ✅ **DONE — 22/22** (13 + 9). Mechanism: `addopts = -m 'not integration …'`; flag_off file is fully `@integration`-marked (9 nodes), maintenancer file unmarked (13 run by default). `--override-ini="addopts="` proven load-bearing via control run (13 passed / 9 deselected). |
| 2 | Seal criterion = mismatch before==after, baseline 7 | ✅ **DONE — 7==7, zero new entries.** Authoritative gate: e2e tripwire `TestPhaseDDoesNotModifySealedArtifacts` (7 mismatched entries, ALL byte-identical to base `630cefebb`; base-blob hashing proof: NEW=[], RESOLVED=[]). The audit file is a 24-pin content-anchor audit (46/46 green) with NO hash gate of its own — disclosed; the literal "audit mismatch count" is unsatisfiable by that file as written. No ari/leader/board entry in the set; phase `agents/` delta = exactly the 2 meta.json. |
| 3 | 13 pre-existing failures in tests/unit/tools/ — reproduce standalone, document, do NOT fix | ✅ **DONE — stable set 13, byte-identical branch-vs-base** (7-run ladder incl. read-only base-tree census via `git archive` + PYTHONPATH shadowing; branch-only: NONE, base-only: NONE). Root-cause inventory: 3× `test_inner_soul_compaction` (memory-md integration), 5× `test_prompt_section_reference_integrity` (prompt lint), 2× `test_instance_tools` (planner allow-list gaps), 2× `test_knowledge_tools` (explore model override), 1× `test_snapshot_tools` (snapshot_enabled duplicate developer). Nothing fixed/quarantined. **Zero board files in the set.** |
| 4 | Fresh-SQLite full-ledger boot blocked pre-existing at 20260714_000001 | ✅ **CORROBORATED** — verbatim blocker errors captured both with and without create_all (dies at 20260714_000001 / 20240101_000001 respectively); duplicate-prefix 20261018_000001 is the second blocker; **board migration never the blocker**; board file applies cleanly in the full 96-sequence once the two known blockers are ledger-pre-marked. T11 boot smoke ran on PG dev fence as owed. |
| 5 | All fixtures tmp_path-fenced (2026-10-12 live-path convention) | ✅ **DONE** — static fence check: board corpus uses in-memory `sqlite://` + `tmp_path` only (PRAGMA foreign_keys=ON listeners); the two NEW files (P3/P9) assert their fence in code; zero HOME-derived/live-path resolutions in any phase test file. The one live-path hazard that DID fire came from `dev.sh` itself, not test fixtures (§6). |

---

## 3. ensure.md (Core, scoped to change set) — ✅ ALL PASS

- **Critical — no regressions in changed packs:** every pack in the change set PASS (§5 table).
- **Critical — deadlock/concurrency integrity:** `concurrency_atomic_unit_test` **baseline-exact 99P/0F/74S** (109.6s; `env -u POSTGRES_DB` proven load-bearing — outer shell carried ambient `ensemble_prod`; zero prod-DB contact in log).
- **Critical — no sync DB calls on event loop:** covered by the same pack — PASS.
- **Critical — dev.sh `--timeout-graceful-shutdown 10`:** present (`dev.sh:142`).
- **Important — await callers:** sweep clean (real call site awaits; remainder comments/log strings/hasattr probes).
- **Nice-to-have — dead code:** n/a (additive feature; F1 fix *removed* a dead truncation pass + import).
- **Release Gate:** NOT run — not warranted by blast radius (no promote in this commission).

## 4. Foreign-file non-interference — ✅ CONFIRMED

`.agents/shared/planning/designer-agent/install-audit.jsonl` remained the ONLY worktree dirt at every checkpoint; never staged, committed, reverted, or deleted by any dispatch. Composition of its dirt: designer-lane appends pre-dating the session (+6/−0), plus ambient `kms_issue` appends during the wave produced by the daemon's own audit seam (`create_instance_tools` → `daemon/services/kms_lite.py:467`) — a side effect of exercising the REAL registry seam, not manual edits; identical appends recur on any real-factory invocation and were left in place (reverting would destroy another lane's audit trail). Final commit uses targeted adds only (§8).

## 5. Suites executed (every file named)

| # | Suite / file | Result | Runtime | Worker |
|---|---|---|---|---|
| 1 | `tests/unit/test_board_service.py` (56 nodes) | ✅ 56/56 | 6.2s | bb0cba5c |
| 2 | `tests/unit/tools/test_board_tools.py` (34) | ✅ 34/34 | 5.5s | c15425fa |
| 3 | `tests/unit/test_board_registration.py` (20) | ✅ 20/20 | (with #2) | c15425fa |
| 4 | `tests/migration/test_board_index_parity.py` (15) | ✅ 15/15 | 1.6s | e0bd3227 |
| 5 | `tests/migration/test_board_runner_apply_rollback.py` (14) — **NEW** | ✅ 14/14 | 12.4s | e0bd3227 |
| 6 | `tests/unit/test_board_gap_pins.py` (18) — **NEW** | ✅ 18/18 | 3.4s | a18ec2d6 |
| 7 | `tests/integration/test_maintenancer_spawn_resolves_tools.py` (13, explicit) | ✅ 13/13 | 1.8s | 9015c36f |
| 8 | `tests/integration/test_service_tool_flag_off_byte_identical.py` (9, explicit, addopts cleared) | ✅ 9/9 | 10.0s | 9015c36f |
| 9 | `tests/test_chart_image_delivery_audit.py` (46) | ✅ 46/46 (content anchors; no hash gate — see flag 2) | 0.4s | 4937c63a |
| 10 | `tests/test_chart_image_delivery_e2e.py::…test_phase_d_does_not_modify_sealed_artifacts` | ✅ criterion PASS (expected-red; mismatch == 7 == baseline) | 0.8s | 4937c63a |
| 11 | `tests/unit/tools/` census (3810 nodes; xdist −n 8 + loadfile + serial + base-tree adjudication, 7 runs) | ✅ 3789–3790 passed / **13 stable pre-existing failures** (flag 3) / 7 skipped | 109s primary; 10m54s total pytest | 9c98f3f8 |
| 12 | `test/packs/concurrency_atomic_unit_test.sh` | ✅ 99/0/74 baseline-exact | 116s | f36e0e51 |
| 13 | T11 boot smoke (dev.sh boot + PG schema proof + cross-instance e2e, `/tmp/rt-ari-p1/p10/`) | ✅ 23/23 assertions; livez/readyz 200 | 30s | e94b6199 |

Board/pin corpus green: **179 nodes** (56+34+20+15+14+18+22). Recon (no suite execution): c1018239. Raw logs: `/tmp/rt-ari-p1/**`.

## 6. ⚠️ Incident (NOT a board defect) — dev.sh EXIT-trap live collateral

First boot-smoke run killed the **live install's code-server** (pid 2021281) ~1s after dev-daemon shutdown: `dev.sh:28-44`'s EXIT trap falls back to `pkill -f "code-server.*--bind-addr"` when its precise pid-file branch misses — and a dev boot that never spawns a code-server ALWAYS misses, so the pattern matches the LIVE install's code-server. Impact: ~2s VSCode-server outage on the live install, **auto-healed by the live daemon's supervisor** (restart 1/5, new pid 2099408, still alive); no daemon outage, no data loss, no prod DB impact; live ensemble-prod (9797) itself never touched. Mitigation proven in the passing run: sentinel pid-file (`data_dev/vscode-server.pid` holding an owned pid) makes dev.sh's precise branch win; pack now asserts the live code-server pid invariant before/after. **Standing recommendation (repo-level fix, phase-owner decision): scope the fallback pattern to the worktree or drop it — it is a live-collateral hazard for EVERY dev boot on this box.** Full write-up: `LESSONS/2026-10-20-devsh-exit-trap-live-collateral.md`; skill `devsh-exit-trap-live-collateral-guard` created; `skill_fix` filed on `devsh-boot-probe-isolation`.

## 7. Watch items & spec-delta notes (no action required from this commission)

1. **xdist `--dist load` census artifact:** `test_snapshot_tools.py::…test_loader_warm_list_wires_snapshot_factory` fails under `--dist load` on branch (2/2) but 0/2 on base-tree, 0/1 under `--dist loadfile`, 0/2 serial — module-global warm-scan cache guard (`daemon/loader.py:471-487`) + distribution order. Board tools never enter `_tool_metadata` (factory-built); isolated probes show identical behavior both trees. Reported as **correlated-not-caused** for the phase owner; census packs should use `--dist loadfile` (deterministic 13). LESSONS entry filed.
2. **Strictly-before `due_before` contract:** the tester's dispatch example assumed an inclusive boundary; product implements the documented STRICT semantics (plan §7.1). Pins follow the product; inversion-defect discrimination preserved.
3. **Audit-file shape:** `test_chart_image_delivery_audit.py` is a content-anchor audit (no `hashlib`/sha gate) — the seal hash gate lives only in the e2e tripwire. Future seal gates should name the e2e node explicitly.
4. Pre-existing (documented, untouched): fresh-SQLite full-ledger blockers ×2 (`20260714_000001`, duplicate prefix `20261018_000001`); the 13 unit/tools failures (flag 3); `computer-use` MCP warmup ERROR (missing `libXtst.so.6`, dev-env).

## 8. Code changes by this test cycle (all TEST code; committed on the branch by the aggregation commit)

- `tests/unit/test_board_gap_pins.py` — NEW, 18 tests (author a18ec2d6): due_at=''/due_before='' fail-closed ×5, literal-tool-name bypass arm, None agent_id, exact authorized set, cross-instance REAL tool layer ×2, tool-layer query pins ×3, tool due_before +07:00, tags TypeError, update-schema no-tags.
- `tests/migration/test_board_runner_apply_rollback.py` — NEW, 14 tests (author e0bd3227): real-runner apply/idempotency/rollback/round-trip + full-dir reality trio.
- `.agents/tester/RESULTS/2026-10-20-ari-tools-phase1-workbook-verification.md` — this report.
- `.agents/tester/LESSONS/2026-10-20-devsh-exit-trap-live-collateral.md`, `.agents/tester/LESSONS/2026-10-20-unit-tools-census-xdist-loadfile.md` — durable lessons.
- `.agents/tester/PACKS.md` — Phase-1 pack block (newest-first).
- Commit: targeted adds ONLY (the 5 paths above); foreign file explicitly excluded. Hash: see final report.

## 9. Worker roster

recon c1018239 · P1 bb0cba5c · P2 c15425fa · P3 e0bd3227 · P5 9015c36f · P6 9c98f3f8 · P7 4937c63a · P8 f36e0e51 · P9 a18ec2d6 · P10 e94b6199. All packs ≤ 5-min cap; all dual-layer timeout; no pack TIMEOUT; no re-dispatch needed.

## 10. Overall

- Unit/board suites: ✅ PASS · Migration: ✅ PASS · Tool layer + gates: ✅ PASS · Boot smoke: ✅ PASS · ensure.md Core: ✅ PASS · Seal: ✅ baseline-exact · Pre-existing flags: ✅ all dispositioned
- **Phase 1 verification: READY** — no blocker for merge/stage decisions (which remain the leader's lanes; NO promote).

# POK active-entry cap 64 → 256 — VERIFICATION GATE

- **Date**: 2026-11-10
- **Branch**: `feature/pok-cap-256`
- **Base**: `40e6a842c293a76fdb4f5e0e070b7aedee91d2ec`
- **Commissioned target**: `b25217863ffd29ba00e993fc2281a97db22e44d1` — "fix: raise POK active-entry cap 64 -> 256"
- **Final tip verified**: `9a23a7ebafa7eb3ac0115ba38c4208e35b7575c1`
- **Gate tier**: Tier 1 (no core seam touched: `daemon/graph.py`, `manager.py`, `api.py`, `services/instance_messaging.py`, `services/task_processor.py`, `services/job_queue_service.py` all clean)
- **Lane**: VERIFICATION ONLY — nothing fixed in source, canonical checkout never touched

**Final commit list on this branch (5 commits above base):**

| # | sha | subject |
|---|---|---|
| 1 | `b25217863` | fix: raise POK active-entry cap 64 -> 256 |
| 2 | `e6977acf5` | fix: sync POK design-of-record to 256-cap + harden docstring rendering |
| 3 | `6d5d9f19b` | fix: restore @tool decorator form for pok_upsert + supersede 64-cap design rejection |
| 4 | `9a23a7eba` | docs(pok): restore byte-intact 64-cap residual wording in discharge-round-2.md |
| 5 | *this artifact* | docs(tester): add POK cap 64→256 verification gate report |

*(The artifact's own sha is self-referential and is therefore recorded in the gate report and is the branch tip at read time.)*

---

## VERDICT — FINAL

| Leg | Subject | Verdict |
|---|---|---|
| A | POK unit packs (8 files) + seed-script fence | ✅ PASS — 214/214 |
| B | Behavioral acceptance (real admission path) | ✅ PASS 6/6 |
| C | Net-new reds vs base, at `b25217863` | ✅ **NET-NEW = 0** |
| **C2** | **Net-new reds vs base, at FINAL TIP `9a23a7eba`** | ✅ **NET-NEW = 0** (merge gate) |
| D | Residue sweep / SQL constraints | ✅ 0 code defects · ⚠️ doc mentions (curator-owned) |
| D-pg | Postgres materialization | ✅ PASS 10/10 · schema holds 300 rows |
| E | Invariants unmoved | ✅ 5/6 unmoved (only `POK_ACTIVE_CAP_PER_PROJECT` moved) |
| F | Cleanliness | ✅ clean tree · 9 files · 1 commit at `b25217863` |
| — | D1 at `e6977acf5` | 🔴 **REAL DEFECT — found, then FIXED at `6d5d9f19b`** |

**TRUE NET-NEW FAILURE COUNT AT FINAL TIP `9a23a7eba` = 0.**
**The branch is mergeable.** See §D1 for the defect this gate caught and its fix arc.

---

## §D1 — THE DEFECT THIS GATE CAUGHT (and its fix arc)

### Discovery

A **foreign commit `e6977acf5`** landed in the worktree *while the first full-suite sweep was running* (04:37:02Z). Chunks 00–14 ran on `b25217863`; chunk 15 straddled; chunks 16–23 ran on `e6977acf5`. The HEAD side was therefore a **mix of two trees** and was not attributable as-is. Pinning per-tree detached worktrees and attributing per chunk resolved the mixture.

The candidate that emerged was:

```
tests/unit/tools/test_frozen_tool_name_discovery.py::test_known_tool_names_matches_source_exactly_no_drift
```

**Red 3/3 at `e6977acf5`; green 3/3 at `b25217863`; green 3/3 at base `40e6a842c`.**

### Root cause

`e6977acf5` moved `pok_upsert`'s decoration from decorator syntax to a post-hoc assignment:

```python
# b25217863 (correct)
@register_tool_category(CATEGORY_NAME)
@tool
def pok_upsert(...):
    ...

# e6977acf5 (broken discovery)
def pok_upsert(...):
    ...
pok_upsert = tool(pok_upsert)
pok_upsert = register_tool_category(CATEGORY_NAME)(pok_upsert)
```

`daemon/tools/_tool_registry.py` `discover_source_only_tool_names()` **AST-scans source files for `@tool`-decorated FUNCTION DEFS**. After the re-decoration, `pok_upsert` is no longer AST-visible as decorated, while the static `KNOWN_TOOL_NAMES` still lists it — so the bidirectional drift pin fires:

```
AssertionError: KNOWN_TOOL_NAMES drift vs source: only_in_static=['pok_upsert'] only_in_source=[]
  Extra items in the left set: 'pok_upsert'
```

**Independent AST proof (the tester's own scan, not the suite's):**

| tree | `@tool`-decorated names |
|---|---|
| base `40e6a842c` | `['pok_curate', 'pok_query', 'pok_upsert']` |
| `b25217863` | `['pok_curate', 'pok_query', 'pok_upsert']` ✅ |
| `e6977acf5` | `['pok_curate', 'pok_query']` ❌ |
| tip `9a23a7eba` | `['pok_curate', 'pok_query', 'pok_upsert']` ✅ |

### Severity

**Important → major.** The tool still *works*: `daemon/registry.py:1364` merges the source set with `KNOWN_TOOL_NAMES` as a **union**, so `pok_upsert` stays in the tool universe and stays allowed. The damage is (a) the branch ships a **red repo-wide invariant pin**, and (b) the **source half of the tool-name drift detector goes blind** to `pok_upsert` — future drift there becomes invisible. The commit's stated motive (avoid a post-construction `pok_upsert.description = …` write on a possibly-`frozen=True` `BaseTool`) was sound; the *mechanism* broke the discovery contract.

### Fix arc — `6d5d9f19b`

Restore the decorator form **and** substitute the docstring **before** `@tool` bakes it, using an innermost helper decorator:

```python
@register_tool_category(CATEGORY_NAME)
@tool
@_substitute_active_cap          # innermost: mutates __doc__ before @tool bakes the description
def pok_upsert(...):
    """... at <active_cap> active entries ..."""
```

This satisfies **both** properties at once: the AST source-scan sees `@tool` again, and the description still renders from the live constant (no hardcoded literal, no post-construction attribute write).

**Verified at the tip:** the canary file is **7/7 green**, the D1 node is **green 3/3** (6/6 across two independent sweeps), and the registry's own discovery returns `pok_upsert` (`registry_discovered_total: 254`, `pok_subset` contains `pok_upsert`).

### Why this mattered for gate design

**The D1 defect was invisible to the scoped packs.** `pok_service_unit` and `pok_tools_unit` were green at `e6977acf5` — the breakage lives in a *repo-wide* invariant pin that no POK-scoped pack exercises. Only a **full-suite** sweep surfaced it. That is the standing evidence for treating the whole-tree dual-side diff as the merge gate rather than a scoped-pack run.

---

## §GATE 2 — focused confirmations at tip `9a23a7eba`

| Leg | Verdict | Receipt |
|---|---|---|
| D1 canary | ✅ PASS | frozen-tool file **7/7 green**; D1 node **3/3** (+3 re-verify); AST name set contains `pok_upsert`; live registry cross-check 254 names |
| POK test files | ✅ PASS | tip **71 passed** (49 + 22); base **69** (48 + 21); FAILED-lists parsed from JUnit XML — **both empty, diff empty** |
| W-7 discrimination | ✅ PASS | 999-patch node green; spot-verified via a `/tmp` mutant copy (constant → hardcoded `"64"`) → description reads `at 64 active`, contains neither 256 nor 999 ⇒ **the pin would redden**. Repo sha256 before == after |
| PACKS.md rows | ✅ PASS | `:157` = "49 nodes … AC19 (256-cap …) … PASS 49/49"; `:160` = "22 nodes … PASS 22/22" — both match measured 49/22 |
| discharge-round-2 | ⚠️ PARTIAL | see §F5 below |
| cleanliness | ✅ PASS | porcelain empty; canonical md5 unchanged; base worktree venv intact |

---

## §GATE 1 — full-suite net-new diff at FINAL TIP `9a23a7eba` (merge gate)

### Base digest re-derivation (proof row)

| | sha256 | ids |
|---|---|---|
| stored base digest | `c4e1f82c807134f7b83e1966e33f3366ae0a7dad8b889120b73a3ad1c42460ab` | 406 |
| **re-derived from preserved logs** | **identical** | **406** |

**PROOF: REPRODUCED byte-for-byte (`cmp` rc=0).** The base tree is unchanged, so the base leg was not re-run; its digest was rebuilt from the preserved raw logs with **one** capture expression applied to **both** log shapes (`SUMMARY FAILED <nodeid>` and `VERBOSE <nodeid> FAILED [ 80%]`), unioned, `grep -F '::'` to drop daemon/CAPLOG log lines, `LC_ALL=C sort -u`. The capture script was regression-checked (it reproduces the stored digest byte-for-byte) **before** being applied to the head side.

### Tip-side totals

Collected **23,971** nodes (`--collect-only`, rc=0) vs base 23,969 → **+2, fully attributed**: `tests/unit/services/test_pok_service_unit.py` 48→49 (+1) and `tests/unit/tools/test_pok_tools_unit.py` 21→22 (+1) — two new tests plus one rename (net 0).

**Totals: passed 22,706 · failed 357 · errors 40 · skipped 42 · xfailed 1 · xpassed 0 = 23,971.** Wall 3,999 s.
`declared == executed` on **every** shard. Accounting closure: 22,358 (completed shards) + 788 (shard 14) + 825 (shard 17) = **23,971 = collected**.

### Timeout handling — the hole discipline

Shards **14** and **17** exited rc=124 — the **same shape as base**, not a regression:
- **Shard 14** printed its full summary (786+1+1 = 788 = declared) before the cap killed the exiting process. Per-file recovery re-ran all 40 files: **788/788**, zero UNKNOWN.
- **Shard 17** was genuinely killed at 69%. Per-file recovery re-ran all 40 files: **825/825** (786 passed, 7 failed, 3 errors, 29 skipped), zero UNKNOWN.

**Non-vacuity:** no shard has declared == 0.

### Form-matched comparison

| form | base | head | in both | head-only | base-only |
|---|---|---|---|---|---|
| BARE-ID | 406 | 407 | 406 | **1** | 0 |
| GREP-FULL | 410 | 411 | 406 | **1** | 0 |

House tool `sweep_digest_verdict.sh` → exit **1** (net-new not empty — non-vacuous; exit 2 = vacuous, never reached).

### 3× stability ladder

| node | tip 3× | base 3× | bucket |
|---|---|---|---|
| `test_secret_do_not_touch_lane_static.py::TestSpawnPointsStillResolve::test_spawn_site_resolution_present[daemon/mcp/connection_manager.py-2]` | RED 3/3 | RED 3/3 | **B — BASE-PROVEN** |

**Why it surfaced head-only:** it is a **base-side observation hole**, not a regression. The base sweep's shard 17 was killed at 576/825 and its verbose recovery stopped alphabetically *before* that file (file #37 of 40), so the base digest never contained the red. The tip side recovered both shards fully, so the comparison is one-sided in the base's disfavour and the ladder closed it.

### THE NUMBER

> ## **TRUE NET-NEW AT TIP `9a23a7eba` = 0**
> **NET-NEW = 0**

Zero bucket-A (true net-new). Zero bucket-C (flaky). All 406 shared reds are pre-existing at base.

---

## §LEG A — unit packs (at `b25217863`)

Command shape (per-variable env scrub, dual-layer timeout, serial):

```
timeout 300 env -u SSL_CERT_FILE -u SSL_CERT_DIR -u PYTEST_XDIST_WORKER \
  -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
  .venv/bin/python -m pytest <FILE> -q --timeout=30 --no-header --tb=short -p no:cacheprovider
```

| Pack | Nodes | Result |
|---|---|---|
| `tests/unit/services/test_pok_service_unit.py` | 49 (base 48) | PASS |
| `tests/unit/tools/test_pok_tools_unit.py` | 22 (base 21) | PASS |
| `tests/unit/services/test_pok_injection_unit.py` | 42 | PASS |
| `tests/unit/services/test_pok_ari_seam_unit.py` | 13 | PASS |
| `tests/unit/services/test_pok_trust_tier_unit.py` | 40 | PASS |
| `tests/unit/services/test_pok_metadata_cap_unit.py` | 23 | PASS |
| `tests/unit/test_pok_grant_pins.py` | 10 | PASS |
| `tests/unit/test_anomaly_report_grant_pins.py` | 15 | PASS |

**214 passed / 0 failed / 0 skipped / 0 timeouts.**

Refusal message rendered from the constant:

```
project holds 256 active POK entries — the 256-cap refuses rather than evicts
(terminal 256-cap lockout is CURABLE: archive a superseded fact via pok_curate)
```

Tool description renders `at 256 active`; `<active_cap>` absent; `64 active entries` absent.

**Seed script**: no `--validate` flag exists; a plain invocation **WRITES** 8 rows to `ensemble_dev`. The only safe entrypoint is importing the module and calling `_validate_payload()` (runs before any DB contact) → `[validate] 8 entries fence+bound clean`, exit 0, zero writes. The commit's seed-script edit is behaviorally INERT for the current 8-entry payload (8 ≤ 64 and ≤ 256) — it only future-proofs the message.

---

## §LEG B — behavioral acceptance (the gap this commission named)

Real `POKService.upsert` + `POKRepository.insert` against `ensemble_dev` (verified by `current_database()`; `ensemble_prod` never reachable). Harness: `/tmp/pokcap_accept/harness.py`.

| Scenario | Result | Observed |
|---|---|---|
| S1 — 65 active rows → 66th write | ✅ | `action='created'`, `active_count=66` — **writes beyond 64 no longer refused** |
| S2 — 255 active → 256th write | ✅ | `action='created'`, `active_count=256` |
| S3 — at 256 → refusal | ✅ | `code='pok_active_cap'`, cure=`'f000-01-x'` (a real seeded slug) |
| S4 — anti-vacuity control | ✅ | cap patched → same write REFUSES; restored → succeeds |
| S5 — per-project scoping | ✅ | project at 256 refusing; a different project writes freely |
| S6 — cleanup | ✅ | 323 rows deleted; total POK rows 9 before == 9 after; zero residue |

S4 verbatim (proves the harness is non-vacuous — the gate reads the module global at call time):

```
project holds 65 active POK entries — the 64-cap refuses rather than evicts
(terminal 64-cap lockout is CURABLE: archive a superseded fact via pok_curate)
```

**Harness adjustment (disclosed, not fakery):** the suggested slug pattern `f"{i:02d}-a-x"` collapses into ONE family and trips `POK_FAMILY_MAX=3` before the active cap (the admission ladder checks family BEFORE active-count). `f"{i:03d}-01-x"` gives each row its own family — independently re-confirming the family cap is intact and firing.

**Standing at the tip:** the admission path and service layer are untouched by `6d5d9f19b` / `9a23a7eba` (`daemon/tools/pok.py` mechanism + docs only), so this acceptance remains valid at the final tip.

---

## §LEG D / E / F — residue, invariants, cleanliness

- `grep 64 daemon/services/pok_service.py` → **CLEAN** (empty).
- 1085 raw `64` hits classified: 1072 false positives (other subsystems' char/byte/LRU/VARCHAR caps, sha256 width, line cites) + 3 CORRECT negative guards (the new test asserts `"64 active entries"` is ABSENT from the tool description) + 13 real stale mentions — **ALL prose, ZERO executable code**.
- **No SQL CHECK, trigger, or migration encodes the cap.** The only POK migration is `20261107_110003_add_pok_source_tier.sql`; its only CHECK is `source_tier IN ('agent','curated')`. `uq_pok_active_slug` = `CREATE UNIQUE INDEX … (project_id, slug) WHERE status='active'` — slug uniqueness, not row count. Empirically **300 active rows inserted into one project with zero errors** ⇒ the cap is enforced ONLY in Python ⇒ no schema migration needed, none added (correct).
- **Invariants unmoved**: `POK_FAMILY_MAX=3`, `POK_PIN_CAP_PER_PROJECT=2`, `POK_BLOCK_MAX_ENTRIES=8`, `POK_BLOCK_MAX_CHARS=2_900`, `POK_CURATED_CAP_PER_VIEW=2` — all byte-identical. The entire constant-block diff is ONE line: `POK_ACTIVE_CAP_PER_PROJECT = 64 → 256`. `daemon/services/pok_injection.py` diff is EMPTY.
- **Cleanliness at `b25217863`**: `git status --short` EMPTY; exactly the 9 intended files; exactly 1 commit; test changes confined to the 2 POK test files; **0 assertions removed** (one line reworded `64`→`cap`, strictly stronger); net +2 nodes / +8 assertions.

---

## §F5 — design-of-record clause: `discharge-round-2.md` (disclosure)

`diff` of `discharge-round-2.md` base → tip is **not additions-only**: one base line (162, the constant-set restatement row) was rewritten by `6d5d9f19b` to point at `decisions.md` A-13.

| Clause | Verdict |
|---|---|
| Dated SUPERSEDED annotation present | ✅ PASS (tip lines 19 and 48) |
| B2' "cap raise (delays only)" rejection row byte-identical | ✅ PASS |
| "curator-absence at a full 64-cap project" residual byte-identical | ✅ PASS (restored verbatim by `9a23a7eba`) |
| Diff is additions-only | ❌ one row rewritten (base line 162) |

**Adjudication: PASS-with-disclosure.** `decisions.md` A-13 is now the single source of truth ("every other site MUST reference A-13 instead of re-typing the numbers"; the "identical across all 4 files" duplication invariant is RETIRED), and the lane's own method elsewhere is append-and-annotate — the approver tracking doc keeps its historical `:44` byte-intact with a dated forward pointer. **No historical information is lost**: the value `64` survives in the same file's byte-intact B2' row, its byte-intact residual row, and ER-2; the other nine constants are unchanged in A-13 and `architecture-recommendation.md` §4.7 (which is explicitly reframed as a mirror of A-13). **The consistent, zero-cost form, if a literal reading is preferred, is a one-line doc commit**: revert base line 162 to its base text and extend the SUPERSEDED annotation to cover it — a marked historical row is not a bare unmarked restatement, so both rules then hold.

---

## §OTHER FINDINGS & FOLLOW-UPS

- **Coverage delta in the refusal leg (disclosed, not a defect).** `b25217863` replaces 64 REAL inserted rows with 3 real rows + a monkeypatched `count_active`. The refuse → name-cure → archive → succeed arc still runs against real rows, and the message is now asserted to render FROM the constant. The genuine loss is "real `count_active` SQL at scale". §LEG B closes that gap independently. A cheap middle ground: seed ~20 real rows and monkeypatch only the gap.
- **`pok_query` cost genuinely moved 64 → 256.** `council-fold-record.md` (A10) records "every `pok_query` pays an up-to-64-row bookkeeping UPDATE + an N+1 lineage walk". Verified in code: `POKRepository.list_entries` has **no LIMIT** → `bump_access()` UPDATEs every returned row + a per-entry `lineage()` walk. The tool lane is now 4× costlier; the INJECTION lane is unaffected (bounded 8/view). `oldest_least_verified_active` also loads+sorts ALL active rows on every at-cap refusal. Re-disclosed at the tip.
- **Live-daemon surface (promoter follow-up, self-resolving).** Live install is `releases/v0.29.7` and still emits *"the 64-cap refuses rather than evicts (terminal 64-cap lockout is CURABLE: …)"*. Confirmed live during this lane: a `pok_upsert` capture was **refused with `pok_active_cap`** — *"project holds 64 active POK entries — the 64-cap refuses rather than evicts"* — i.e. the live daemon still enforces 64 and this branch is not promoted. (The same refusal blocked this lane's own POK lesson capture: the cap this branch raises is the one blocking its own capture.)
- **`install-audit.jsonl` discipline (house violation, NOT a code defect in this branch).** Tests in `tests/unit/tools/` resolve the tracked `.agents/shared/planning/designer-agent/install-audit.jsonl` path against the **process CWD** and append to it — violating the house rule at `tests/unit/test_plane_bearer_echo_scrub.py:110` ("Never append to the repo's tracked install-audit file from a test"). Observed three times during this lane: 3 lines at 09:20Z (a foreign whole-directory pytest run with CWD = the worktree), 3 lines at 09:31Z (this gate's own post-quiescence re-run, in the feature worktree), and 27 lines in the pinned tip worktree during the sweep. Each occurrence was backed up to `/tmp` and the file restored to its HEAD blob (`881e133a…`); the canonical tree's copy was never touched (October mtime). **The offending test should be pointed at a tmp dir instead of `<cwd>/.agents/.../install-audit.jsonl`.**
- **Worktree claim is advisory only.** `wt.claim.pok-cap-256` (claimed_by `giter`) did not prevent a second agent from running a 9-minute whole-directory pytest inside the claimed worktree. A "worktree busy" advisory visible to the claiming agent would close that.

---

## §ARTIFACTS

**Gate 1 (tip):** `/tmp/pokcap_tip_{00..23}.log` · `/tmp/pokcap_tip_collect.log` · `/tmp/pokcap_tip_sweep_console.log` · `/tmp/pokcap_tip_pf_{14,17}_*.log` (per-file recovery) · `/tmp/pokcap_tip_pf_17_recovery.tsv` · base proof digest `c4e1f82c…` (406 ids).

**Gate 2:** `/tmp/pok-lane-f1-f6/` (audit backups incl. `audit_appended_lines.jsonl` and `audit_full_pre_restore.jsonl`; `ast_scan_pok.py`; `pok_hardcoded_copy.py` mutant).

**Earlier gate (at `b25217863`):** `/tmp/pokcap_head_failed.txt` (408) · `/tmp/pokcap_base_failed.txt` (356) · `/tmp/pokcap_norm_{head,base}.txt` (364 / 312) · `/tmp/pokcap_c3_{head,base}_formmatched.txt` (409 / 406) · `/tmp/pokcap_c3_final.txt` · `/tmp/pokcap_c3_verdict_formmatched/sweep_digest_verdict.log` · 144 re-run logs `/tmp/pokcap_c3_*.log` · `/tmp/pokcap_accept/harness.py` + `run.log` · per-chunk logs `/tmp/pokcap_{head,base}_00..23.log` · `/tmp/pokcap_head_perfile.tsv` · `/tmp/pokcap_base_{14,17}v.log`.

**Verification worktrees (all detached, porcelain clean):**
`/home/nea/ensemble-src-wt-pok-tip-9a23a7eba` (@ `9a23a7eba`) ·
`/home/nea/ensemble-src-wt-pok-head-b252178` (@ `b25217863`) ·
`/home/nea/ensemble-src-wt-pok-drift-e6977acf5` (@ `e6977acf5`) ·
`/home/nea/ensemble-src-wt-pok-base-40e6a842` (@ `40e6a842c`) ·
plus the branch worktree `/home/nea/ensemble-src-wt-pok-cap-256`.

## §HOUSE LESSONS (durable)

1. **Digest form must be identical on both legs.** Capture ERROR ids, not just FAILED. One leg capturing `^FAILED` only silently drops the other's 40 ERROR ids → each resurfaces as a phantom head-only id.
2. **A shard that exits rc=124 contributes ZERO ids, never zero failures.** Recover it per-file or mark it UNKNOWN. The base digest is a **floor**, not a census — a base-side hole surfaces the base's own pre-existing red as a false "net-new" until the ladder closes it.
3. **Pin the gate target in a dedicated detached worktree.** A branch worktree can receive a foreign commit mid-sweep and silently make the "HEAD side" a mix of two trees.
4. **Auth env: scrub the FULL family or NONE.** A partial scrub breaks collection; the residual ambient origin mismatch creates ~36 fixture ERROR nodes identical on both sides.
5. **A cap-constant test that monkeypatches the counter is cap-agnostic but loses real-SQL-at-scale coverage.** Pair it with a constant-value pin AND a real-row acceptance leg.
6. **Tool-name discovery depends on `@tool` DECORATOR SYNTAX.** Re-decorating a tool post-hoc (`fn = tool(fn)`) silently removes it from the AST source-scan — the tool still works (registry merges source ∪ static) but the repo's drift detector goes blind to it, and the bidirectional pin reds. Substitute docstrings with an innermost helper decorator applied UNDER `@tool`, never by re-decoration.
7. **A whole-tree dual-side diff is the merge gate for changes that touch a tool-registration mechanism.** The D1 defect was invisible to every scoped POK pack.

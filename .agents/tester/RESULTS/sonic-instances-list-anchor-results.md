# SONIC TESTER RESULTS — instances-list-anchor (standard-path verification wave)

**TEST: PASS**

- **Commission:** instances-list-anchor (standard-path verification wave)
- **Lane:** sonic-tester
- **Worktree:** `/home/nea/ensemble-src-wt-instances-list-anchor` · branch `feature/instances-list-anchor`
- **Tip under test:** `7e97be2ff` (= `origin/latest`) · tag `v0.31.51` @ `c1d09bab4` · staged, NOT promoted
- **Base:** `bcebe7725` (handoff's declared base; verified `bcebe77250ab6ae07212cfafc33b2a8e82608d64`)
- **HANDOFF-v2:** `.agents/shared/planning/sonic-team/instances-list-anchor/HANDOFF.md`
- **Date (UTC):** 2026-11-24

---

## 0. Scope derivation

The merged diff `bcebe7725..HEAD` matches the handoff's `## Files touched across modules` list
**exactly** — 18 files, +1239/−5 — so the change is **not** bigger than the handoff claims and no
widening was mandated on that ground. Verified file-set delta (18 paths, byte-identical to the
handoff's union of 6 BE + 4 FE + 3 planning + 5 bump files):

```
daemon/routers/instances.py · daemon/manager.py · daemon/services/instance_lifecycle.py ·
daemon/repositories/instance/repository.py · frontend/src/app/services/{api,instance}.service.ts ·
frontend/src/app/services/{api,instance}.service.spec.ts ·
tests/api/test_list_instances_anchor_{param,family}.py · 3 planning docs · 5 bump files
```

### Tier escalation (mechanical, not a judgment call)

`.agents/tester/rules/ensure.md` § Gate Tiers: **Tier 2 — whole-tree dual-side** triggers when the
diff touches a **core seam**, and the mechanical path-prefix list contains `daemon/manager.py` — which
this diff does (`get_instance_anchor_family` facade, +16 lines). "Escalation Tier 1→2 via the
core-seam list is MECHANICAL, never a judgment call." The Tier-2 recipe
(`.agents/tester/skills/whole-tree-base-proven-diff/`) was loaded and executed. Budget: ≤90 min
advisory — **actual 62 min**.

---

## 1. Gate results (the commission's five claims)

| # | Claim | Expected | **Observed** | Verdict |
|---|---|---|---|---|
| 1 | BE scoped shard | 44 passed | **44 passed, 0 failed** (14.12 s) | ✅ MATCH |
| 2 | FE gate | 5 suites / 236 passed | **5 suites passed, 236/236 tests passed** (8.77 s) | ✅ MATCH |
| 3 | `tsc --noEmit -p tsconfig.app.json` | exit 0, clean | **exit 0, 0 bytes of output** | ✅ MATCH |
| 4 | Pre-existing reds base-identical | 19 at base AND at tip | **tip 19 failed / base 19 failed — identical node-id sets**, shape = `assert 302 == 200` + `[AuthAudit] event=request_denied_302` | ✅ CONFIRMED (independently re-measured) |
| 5 | Pack registration | both packs registered in `.agents/tester/PACKS.md` | **WAS NOT REGISTERED** (0 rows at tip and at `origin/latest`) → **registered by this lane** (2 rows, `sonic_`-tagged) | ✅ NOW SATISFIED |

### Claim 1 — BE scoped shard

```
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT \
  ENSEMBLE_SELF_ENV=dev uv run pytest \
  tests/api/test_list_instances_anchor_param.py tests/api/test_list_instances_anchor_family.py \
  tests/api/test_instances_list_offload_prefs.py tests/api/test_instances_list_response_bytes.py \
  tests/unit/test_instance_tree_loading.py -q
→ 44 passed, 1 warning in 14.12s
```

### Claim 2 — FE gate

```
cd frontend && npm test -- --runTestsByPath \
  src/app/services/api.service.spec.ts src/app/services/instance.service.spec.ts \
  src/app/services/instances-view-state.service.spec.ts \
  src/app/components/instance-list/instance-list.component.spec.ts \
  src/app/components/instance-list/instance-list.rooms-btn.spec.ts
→ Test Suites: 5 passed, 5 total | Tests: 236 passed, 236 total
```

### Claim 3 — FE type gate

`cd frontend && ./node_modules/.bin/tsc --noEmit -p tsconfig.app.json` → **REAL exit code 0,
0 bytes of output**. (The exit code was captured WITHOUT a pipe — a piped `echo $?` would have read
`tail`'s status, not `tsc`'s.) **Non-vacuity:** `tsconfig.app.json` includes `src/**/*.ts` and
excludes `src/**/*.spec.ts`, so both changed service files (`api.service.ts`, `instance.service.ts`)
ARE inside the gate's scope.

### Claim 4 — pre-existing reds, base-identity (re-measured, not taken on trust)

The base leg was run in a **read-only `git archive bcebe7725` extract** (`/tmp/sonic-base-leg-ila`);
`import daemon` was proven to resolve to the BASE tree (`/tmp/sonic-base-leg-ila/daemon/__init__.py`),
not the worktree's editable install.

| Leg | Result | Failing set |
|---|---|---|
| TIP `7e97be2ff` | **19 failed** | 6 × `test_list_instances_source_param.py` + 13 × `test_instance_ui_prefs_api.py` |
| BASE `bcebe7725` | **19 failed** | **node-for-node IDENTICAL** |

Failure shape on both legs: `AssertionError: assert 302 == 200` with
`INFO daemon.middleware.auth: [AuthAudit] event=request_denied_302` — the **auth-302 shape**, not a
new failure mode. Mechanism confirmed: the older fixtures never strip `AuthMiddleware`, while the
newer files (offload_prefs, response_bytes, both anchor packs) disarm it at fixture setup.
**These 19 are PRE-EXISTING and do NOT count against this change.**

### Claim 5 — pack registration (the one claim that was FALSE as shipped)

`grep -c "list_instances_anchor" .agents/tester/PACKS.md` → **0** at the tip AND at
`origin/latest`. The developer's module file fence excluded `.agents/tester/PACKS.md`, so the two
new packs were **not gate-verifiable**. Closed from the TESTER lane (whose write fence DOES include
the registry — same shape as the `sonic_selectable_models_guard` precedent, commit `effd34a27`):

- `test/packs/sonic_instances_list_anchor_param_unit_test.sh` → **PASS 6/6**, rc=0
- `test/packs/sonic_instances_list_anchor_family_unit_test.sh` → **PASS 7/7**, rc=0
- 2 rows appended to `.agents/tester/PACKS.md` (block
  `sonic-instances-list-anchor-pack-registration-20261124`), each with its exact invocation, node
  census, scope and measured status.

---

## 2. Behaviour spot-checks (offline, in the pack framework)

| Spot-check | Where it is pinned | Result |
|---|---|---|
| Anchor present in list when OLD | `test_anchor_older_than_page_present` | ✅ |
| Descendants present | `test_family_present_include_descendants_false`, `test_children_populated_on_family_rows` | ✅ |
| Unknown anchor ignored (200, list unchanged) | `test_unknown_anchor_unchanged` | ✅ |
| Cap respected (bounded family) | `test_cap_respected` — 505 seeded children ⇒ family = 500 = `INSTANCE_LIST_ANCHOR_BFS_CAP`; rate-limited WARN emitted | ✅ |
| No-anchor request byte-identical (back-compat) | BE `test_anchor_absent_not_called` (facade NOT called) + FE `api.service.spec.ts:508` asserts the FULL URL `== '/api/instances?limit=100&offset=0&exclude_kb=true&include_descendants=true'` | ✅ |

**Integration-seam review (code, not just tests).** The merged tree's seam was read directly:
`daemon/routers/instances.py` takes the optional `anchor: str | None` Query param, issues the SECOND
offload (`await _offload(manager.get_instance_anchor_family, anchor)` — the route-local house
pattern), merges the family rows into `instances_data` **BEFORE** the UI-prefs batch fetch (so
pin/color/icon prefs cover the appended rows), dedupes by `instance_id`, appends after the normal
page rows, and degrades on any exception to **HTTP 200 + unchanged list + WARN** (never 422/500).
`manager.list_instances`' signature is UNTOUCHED (zero stand-in ripple). The lifecycle BFS is bounded
by `INSTANCE_LIST_ANCHOR_BFS_CAP = 500`; the repository read is ONE batched `SELECT ... IN` with
deterministic ORDER BY (no N+1).

**Collision / shadow checks (all clean at base).** No pre-existing `anchor` symbol in
`instance_lifecycle.py`, no `list_by_ids`, no `get_instance_anchor_family`, no
`INSTANCE_LIST_ANCHOR_BFS_CAP`, and no pre-existing `anchor` Query param on the route — the change
is purely additive with nothing shadowed. `chunked` / `_METADATA_LOOKUP_CHUNK` (used by
`list_by_ids`) and `import time` (used by the cap WARN) all pre-exist at base — no latent
`NameError` above the chunk size. FE DI seam: `InstancesViewStateService` has **zero** service
imports (no circular import) and is `providedIn: 'root'`.

---

## 3. Tier-2 whole-tree base-proven diff (core-seam escalation)

Recipe: `.agents/tester/skills/whole-tree-base-proven-diff/whole-tree-base-proven-diff.md`
(+ the canonical comparator `sweep_digest_verdict.sh`).

**Collection (recipe §1).** `pytest --collect-only -q tests/ --ignore=tests/packs
--ignore=tests/e2e/test_context_injection_hybrid.py` → HEAD **41,385** collected / 1,278 deselected;
BASE **41,372** collected. Node-set delta (LC_ALL=C on both `sort` and `comm`): **+13 in HEAD, 0
removed** — exactly the 13 new anchor nodes. Slicing proof: executed == declared.

**Pre-exclusion (named compensating pin, per recipe).**
`tests/performance/test_message_api_cost.py` (12 nodes) — unschedulable (>300 s solo, rc=124 at
every bisection level) ⇒ excluded; compensating pin: the registered `regression_performance` pack.
The `tests/packs` g7 `sys.exit` collection trap and the e2e `:8079` live-daemon cell were excluded by
the recipe's own collection command.

**Execution (recipe §3–§5).** 14 shards by FILE boundary (~3,300 nodes each), each run with the
recipe's env fence (per-variable scrub — never the `env -u POSTGRES_*` glob — whole `ENSEMBLE_AUTH_*`
family, `OPENAI_BASE_URL=http://127.0.0.1:9/v1` LLM fence, `QUEUE_LLM_RETRY_*=1`), `-n 8`,
`--timeout=120 --timeout-method=signal`, outer `timeout 1750`. Incremental persistence via
`progress.tsv`; no shard death (no shard died without a summary line; no bisection was required).
No shared-scratch-DB files matched the DF-4 parallel exclusions, so intra-leg parallelism was safe.

**Accounting closure (recipe §7).** `sum(shard summary counts)` = **41,373 scheduled == 41,373
collected** ⇒ **CLOSURE OK**, no node lost.

| Leg | Nodes | Passed | Failed | Errors | Wall |
|---|---|---|---|---|---|
| HEAD `7e97be2ff` | 41,373 | 40,425 | 521 | 25 | 2,766 s (46.1 min) |
| BASE `bcebe7725` (the 546 HEAD-red ids, identical command shape, same id-to-shard mapping) | 545 | 108 | ~400 | ~23 | ~6 min |

**Comparison (recipe §6 + §8, house canon).** `sweep_digest_verdict.sh` → **NET-NEW = 107** in both
the GREP-FULL and BARE-ID forms, `base-only = 0`. Per the tool's own downstream obligation, a
non-empty net-new set is an **INPUT TO ADJUDICATION, not a regression verdict**.

**Adjudication ladder (recipe §6) — 107 candidates resolved to 0 genuine regressions:**

| Class | Count | Disposition |
|---|---|---|
| Green in ALL 3 head isolation runs | 13 | pure parallel-shard composition artifact |
| Red at head AND at base in the isolation ladder | 91 | **PRE-EXISTING** |
| Individually adjudicated (below) | 3 | **PRE-EXISTING / flake** |

The 3 individually adjudicated nodes — each re-run **3× per side** in isolation, with a
**git-aware** base clone (`git clone --shared` of the worktree at `bcebe7725`, into which the base
leg's `git diff` commands actually execute, unlike the gitless `git archive` extract where they
return empty output and the assertion passes **vacuously**):

1. `tests/unit/repositories/test_source_gc_repository.py::TestP11BootNoBehaviorChange::test_the_frontend_ui_lane_drift_is_confined_to_the_allowlist_at_the_tree_level`
   — the pin computes `git diff 858a85e31..HEAD -- frontend/` and asserts no offending hunks.
   **RED at the git-aware base, 3/3 runs, identically to head.** Measured through the pin's OWN
   helper: **58 offending hunks over 13 paths at base** vs **68 over 15 at head**. The gitless base
   leg passed it *vacuously* (empty diff ⇒ `== []`). ⇒ **PRE-EXISTING** (a standing stale pin,
   base-attributed). ⚠ **DISCLOSURE:** this lane's 4 frontend/ files add **12** offending hunks to
   that already-red guard — by the pin's own rule (a) a path outside
   `FRONTEND_UI_LANE_SANCTIONED_FILES` is an offender regardless of wording, and by the pin's own
   docstring "a frontend/ change from ANY other lane … lands in an offending hunk and REDs here". The
   pin's verdict is unchanged (red before, red after). **This pin is NOT in `QUARANTINE.md` — the
   ledger gap is filed as an anomaly.**
2. `tests/unit/test_nest_report_rest_route.py::TestAM1TheRouteSetPin::test_the_metrics_router_source_is_untouched_by_this_lane`
   — **RED at the git-aware base, 3/3** ⇒ **PRE-EXISTING**, and **already a standing QUARANTINE.md
   row** (row 11, "STANDING STALE PIN (base-attributed, lane-innocent)") whose own text records the
   exact vacuity this run hit: *"Base-side 'pass' was VACUOUS (gitless git archive extraction ⇒
   empty stdout)."*
3. `tests/test_chart_tools_reuse_integration.py::TestT1RealDispatchReviveWalk::test_second_call_revives_same_charter_via_real_enqueue`
   — **PASS 8/8 isolated at head AND 8/8 at base** ⇒ a **batch-context composition flake**, not a
   regression. (Its sibling node is already recorded as a composition flake in `QUARANTINE.md`
   row 157.)

**TIER-2 VERDICT: genuine net-new regressions = 0.**

> **Method note worth carrying.** The gitless `git archive` base leg made two `git diff`-shelling
> pins pass **vacuously**, inflating the raw comparator to 107. A git-aware base clone (recipe
> `ab-base-leg-provisioning-artifacts`) is the correct provisioning for any base leg whose nodes
> shell out to `git`.

**No test-run side effect:** `.agents/shared/planning/designer-agent/install-audit.jsonl` is
byte-identical before and after (sha256 `c1b7fdc2bebaf2e3ab93232a3167e09695cdaa8a568bbf6bdaa470ad15a5b248`)
— nothing to restore.

---

## 4. Env discipline (house rules — load-bearing)

- Per-variable `POSTGRES_*` scrub used on EVERY daemon-child shell. The `env -u POSTGRES_*` GLOB
  form is a **silent no-op** in bash and was never used.
- `ENSEMBLE_SELF_ENV=dev` set explicitly — the host ambient default is **`live`**, so an unscrubbed
  run would auto-derive the live identity (fail-closed).
- `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned EXPLICITLY. **No boot-smoke was attempted** and
  no live daemon / live DB was contacted: the packs are in-memory SQLite + `httpx` ASGITransport.
- The whole `ENSEMBLE_AUTH_*` family scrubbed together (a partial scrub breaks collection).
- FAKE_HOME (`mktemp -d`) in both new packs — no live-shaped path resolution; removed on exit.
- No live-install deletion risk: the packs write nothing outside their `mktemp` scratch root.

---

## 5. Deviation register

| # | Deviation | Impact |
|---|---|---|
| D1 | The handoff's BE/FE/tsc numbers are all **exactly reproduced**. | none |
| D2 | **Pack registration was NOT shipped** (claim 5 FALSE as merged). Closed by this lane: 2 `test/packs/` wrappers + 2 `PACKS.md` rows. | resolved |
| D3 | Tier-2 whole-tree dual-side was executed because `daemon/manager.py` is on the **mechanical** core-seam list (`ensure.md` § Gate Tiers). Not requested by the commission; mandated by house doctrine. 62 min of the 90-min advisory budget. | none (net-new = 0) |
| D4 | The whole-tree leg needed a **git-aware** base clone; the gitless extract made two `git diff` pins pass vacuously. | method correction, disclosed above |
| D5 | This lane's frontend/ edits add **12** offending hunks to the already-red `source_gc_repository` FE-UI drift pin. The pin's verdict is unchanged; it is not in `QUARANTINE.md`. | **disclosed + anomaly filed** (ledger gap) |
| D6 | The handoff's "What is UNTESTED" section is accurate as written: no live-daemon/PG exercise of the anchor path, no browser E2E deep-link check, no `ng build`/AOT, and the router's anchor-fetch-EXCEPTION degrade leg has no dedicated red test (covered defensively only). | out-of-scope by the commission's own fence |

---

## 6. Artifacts

| Artifact | Path |
|---|---|
| New pack (route boundary, 6 nodes) | `test/packs/sonic_instances_list_anchor_param_unit_test.sh` |
| New pack (family semantics, 7 nodes) | `test/packs/sonic_instances_list_anchor_family_unit_test.sh` |
| Registry rows | `.agents/tester/PACKS.md` — block `sonic-instances-list-anchor-pack-registration-20261124` |
| This results file | `.agents/tester/RESULTS/sonic-instances-list-anchor-results.md` |
| Tier-2 sweep evidence | `/tmp/ila-tier2/` (shard logs, `progress.tsv`, `failed_ids.txt`, comparator log `verdict/sweep_digest_verdict.log`, base clone `/tmp/ila-tier2/base-git`) |
| Base extracts | `/tmp/sonic-base-leg-ila` (gitless, `git archive`), `/tmp/ila-tier2/base-git` (git-aware clone) |

**Note on scope of writes:** this lane touched `test/packs/`, `.agents/tester/PACKS.md`,
`.agents/tester/RESULTS/` and `/tmp` only. **No production code, no test code and no daemon code was
edited** — the tester role never fixes; it reports.

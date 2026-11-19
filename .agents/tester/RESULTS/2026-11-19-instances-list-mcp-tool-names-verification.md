# RESULTS — instances-list-mcp-tool-names (Option A) — sonic-tester verification wave

**Verdict: ✅ TEST: PASS** (independent re-run; no module-run number trusted)

- **Lane:** `feature/instances-list-mcp-tool-names` @ `6ff047f13` (HANDOFF-v2) — code under test = merge `fd2212fa6`
- **Base:** `43e8d8db6` (v0.31.11 bump) — true pre-fix regression legs ran here
- **Worktree under test:** `/home/nea/ensemble-src-wt-ilnames` (fenced: `ENSEMBLE_SELF_ENV=dev`, `POSTGRES_DB=ensemble_dev` @127.0.0.1:5432, `DAEMON_PORT=8079`)
- **Verifier:** sonic-tester (verification wave) — independent execution, own venvs, own scratch worktrees
- **Diff audited:** `43e8d8db6..fd2212fa6` — exactly 2 files (`daemon/routers/instances.py` +36/−2, `tests/api/test_instances_list_mcp_tool_names.py` +461 new). Matches the handoff's file list; no undisclosed files.

## Contract under test

GET `/api/instances` **LIST** rows carry **NO** `mcp_tool_names` key (key-**ABSENT**, not `null`);
GET `/api/instances/{id}` **DETAIL** keeps it **populated**; machinery untouched.

## Legs executed (all independent re-runs)

| # | leg | tree | result |
|---|---|---|---|
| 1 | new pack `tests/api/test_instances_list_mcp_tool_names.py` (ambient env) | feature | **5/5 PASS** (3.94 s) |
| 2 | same pack, `ENSEMBLE_AUTH_ENABLED=false` | feature | **5/5 PASS** (4.15 s) — hermetic vs auth posture |
| 3 | sibling pack `tests/api/test_list_instances_source_param.py` | feature | **6/6 PASS** (3.59 s) |
| 4 | `tests/api/` DIR SWEEP (`ENSEMBLE_AUTH_ENABLED=false`) | feature | **2 FAILED / 72 PASSED** (24.97 s) |
| 5 | `tests/api/` DIR SWEEP — BASE leg | `/tmp/sonic-ilnames-base` @ `43e8d8db6` | **6 FAILED / 63 PASSED** (28.19 s), 69 items collected |
| 6 | sibling pack **alone** | base | **6/6 PASS** (3.73 s) |
| 7 | `ui_prefs` + sibling pack (no new pack) | base | **6 FAILED / 13 PASSED** — reproduces leg 5's 4 extra reds WITHOUT the change |
| 8 | NON-VACUOUS: new pack vs BASE router (pack file copied into base tree) | base | **3 FAILED / 2 PASSED** — pins die on the old behaviour |
| 9 | MUTATION A — revert ONLY `response_model_exclude` (kwarg still deleted) | `/tmp/sonic-ilnames-mutA` | **2 FAILED / 3 PASSED** — key returns as `null` |
| 10 | MUTATION B — exclude intact, re-add ONLY the constructor kwarg | `/tmp/sonic-ilnames-mutB` | **1 FAILED / 4 PASSED** — list re-sources metadata |
| 11 | independent wire-shape pack (mine, `TestClient`) | feature | **5/5 PASS** (7.32 s) |
| 12 | my pack vs BASE router | base | **1 FAILED / 4 PASSED** — detection power |
| 13 | my pack vs MUTATION A | mutA | **1 FAILED / 4 PASSED** — detection power |
| 14 | my pack vs MUTATION B | mutB | **5/5 PASS** — wire unaffected by design (source-layer-only change) |

## Node-for-node base-red parity (leg 4 vs leg 5)

Branch `tests/api/` reds — **2**, both **QUARANTINE-attributed** (QUARANTINE.md row 2026-10-19 consolidated set: *"instance_ui_prefs ×2 (`tests/api/test_instance_ui_prefs_api.py` — test standin `list_instances` lacks `search=` kwarg the router passes since `7609c197b` 2026-07-28)"*):

| node id | branch | base | identical signature |
|---|---|---|---|
| `tests/api/test_instance_ui_prefs_api.py::test_get_instances_list_includes_prefs_fields` | FAIL | FAIL | `TypeError: _ManagerStandin.list_instances() got an unexpected keyword argument 'search'` |
| `tests/api/test_instance_ui_prefs_api.py::test_get_instances_list_includes_icon_tag` | FAIL | FAIL | same TypeError |

**Net-new reds = 0.**

### The 4 base-only reds (branch 2F/72P vs base 6F/63P) — attributed, NOT net-new

The base leg carries **4 extra failures** in `tests/api/test_list_instances_source_param.py`:
`RuntimeError: Task ... got Future attached to a different loop` from `daemon/services/db_offload.py:188`.

- **Reproduced WITHOUT the change**: leg 7 (`ui_prefs` + sibling, base tree, no new pack) → same 4 failures (6F/13P). Leg 6 proves the sibling pack is **6/6 green in isolation** at base. ⇒ **order-dependent, pre-existing cross-pack event-loop pollution**, base-attributed.
- **Mechanism**: `db_offload`'s seam captures the running loop once; the ui_prefs pack leaves it bound to a dead loop, and the sibling pack then trips on it. The **new pack re-binds the seam per test** (`_db_offload.configure(loop=asyncio.get_running_loop())` in its `client` fixture) and therefore **masks** the pre-existing pollution on the branch — it does not cause it.
- Adjudication: base-attributed, deterministic, pre-existing ⇒ **quarantined; not counted against this change**.

## Mutation legs (non-vacuity) — both pins proven load-bearing

Both applied in throwaway worktrees (`git worktree add --detach /tmp/sonic-ilnames-mut{A,B} fd2212fa6`, own `uv sync --frozen` venv, `daemon.__file__` asserted inside each tree). **No mutation ever touched the feature worktree**; both trees restored to the clean sha `9c755ab1ef3f869fb5b5a056ca11a86e4316c2acf958a7437e067991bc3f8fda` with empty `git status` afterwards.

- **A** (revert ONLY `response_model_exclude={"instances": {"__all__": {"mcp_tool_names"}}}`) → 1-line deletion, diff reviewed → **2 FAILED**:
  `test_list_rows_omit_the_mcp_tool_names_key` (key back as `null`), `test_instance_without_metadata_key_is_key_absent_on_list`.
  ⇒ the **key-ABSENT** half is load-bearing; without the route-level exclude the field serialises as `"mcp_tool_names": null` (violates the contract).
- **B** (exclude intact, re-add ONLY `mcp_tool_names=inst.get("metadata", {}).get("mcp_tool_names")` at the list-loop site) → 1-line insertion → **1 FAILED**:
  `test_list_never_sources_mcp_tool_names_from_metadata` (`observed reads: ['mcp_tool_names', 'model_override', ...]`).
  ⇒ the **source** half is load-bearing.

## Independent wire-shape leg (mine — a DIFFERENT transport from the deliverable's pack)

New pack: `test/packs/instances_list_mcp_tool_names_wire_shape_unit_test.sh` (sha256 `e76ae61c69171f368d094aad038ff5cdfd2df85122d5189d4af047e59cbdedcb`; present byte-identically in the canonical tree `/home/nea/ensemble-src/test/packs/` and in the feature worktree `/home/nea/ensemble-src-wt-ilnames/test/packs/`; **registered with ONE row** in the shared registry `/home/nea/ensemble-src/.agents/tester/PACKS.md`, section `sonic-ilnames-wire-shape-pack-registration`).

- Transport: `fastapi.testclient.TestClient` (sync, per-request portal) — **not** the deliverable's `httpx` + `ASGITransport`; own fixtures, own manager stand-in ⇒ a harness bug in one cannot mask the other.
- 5 nodes: tree-identity assertion; LIST rows carry no key + **raw response bytes** carry no `"mcp_tool_names"` anywhere; envelope intact (`total`/`limit`/`offset`/`has_more`/`truncated`, `total == 2`); DETAIL key **present + populated** (`["mcp_alpha","mcp_beta","mcp_gamma"]`); DETAIL key present and `null` when the metadata has none.
- Hermetic: in-memory SQLite only; the `daemon.services.db_offload` seam replaced by an inline passthrough (execution-detail wrapper, no wire effect, keeps the pack loop-agnostic); zero PG, zero network, zero live-install/live-daemon contact.

## FE grep on the MERGED tree (`fd2212fa6`) — 0 consumers

| spelling | hits in `frontend/` |
|---|---|
| `mcp_tool_names` | **0** |
| `mcpToolNames` | **0** |
| `mcp-tool-names` | **0** |
| `MCP tool names` / `mcp tool names` | **0** |

No in-repo code consumer outside `daemon/` + `tests/`. The agent tool layer (`daemon/tools/instance.py:6844 list_instances`) calls `manager.list_instances(...)` **directly** — it never traverses the REST route ⇒ unaffected (ANALYSIS §9 claim verified independently).

Documentation: every doc example carrying the field is a **SPAWN** / **DETAIL** / **GET-INSTANCE-INFO** shape (`docs/api-reference.md:216` = spawn response; `docs/usage.md:65` = "Spawn an Agent Instance"; `docs/usage.md:261` = "Get Instance Info"). The `docs/api-reference.md` "List Instances" response example shows only the envelope ⇒ **no doc claims the list rows carry the field**.

## Payload-delta arithmetic (projection check — ANALYSIS.md, NOT re-measured; live daemon untouched)

Source: `.agents/shared/planning/sonic-team/instances-list-latency/ANALYSIS.md` §1.3 (avg **3 922 B/row**; `mcp_tool_names` = **2 263 B/row** = **58 %**) and §8 Option A.

- Per-row: `2 263 / 3 922 = 0.5769` ⇒ **57.7 % ≈ 58 %** of every row removed — matches ANALYSIS's 58 % share.
- Per list call (FE_A: 1 541 rows): `1 541 × 2 263 B = 3 487 383 B ≈ 3.49 MB` removed; `6.06 MB − 3.49 MB ≈ 2.57 MB` ⇒ **6.06 MB → ~2.6 MB (−57 %)** — matches the §8 projection.
- ⇒ the handoff's "−58 % per row / 6.06 MB → ~2.6 MB (−57 %)" is **arithmetically consistent** with the ANALYSIS figures.
- **NOT re-measured**: the live latency/payload effect (FE_A p50 824 → ~650 ms, pydantic dump 89 → ~35 ms) is a **projection**; live was **not probed** (no live daemon contact in this wave).

## Untested / disclosed (carried from the handoff, re-checked where cheap)

- **OpenAPI-vs-runtime gap** (disclosed, NOT fixed): the shared model `daemon/models/instance.py:65` still declares `mcp_tool_names`, so the list envelope's *documented* schema still advertises the field while the runtime shape is narrower. No schema surgery was done (Option A = route-response-shape only). Strict codegen'd clients could expect the key.
- Out-of-repo consumers of `GET /api/instances` remain unknown (only the in-repo FE was searched). A consumer doing `row["mcp_tool_names"]` on LIST rows now **KeyErrors** (was `null`/list); `.get()` consumers see `None`.
- `tests/e2e/test_mcp_tools*.py` were **not run** (live-daemon e2e) — but their readers were **statically verified** to hit `GET /api/instances/{instance_id}` (the DETAIL route) only ⇒ unaffected.
- No FE build / FE e2e (0 consumers ⇒ no FE change to test). No full-suite run (scoped sweep by blast radius: `tests/api/` + sibling pack + new pack + the two `tests/e2e` readers read statically).

## Quarantine consulted

`.agents/tester/QUARANTINE.md` — the failing files of the branch leg are `tests/api/test_instance_ui_prefs_api.py` (2 nodes), covered by the **2026-10-19 consolidated row** (`instance_ui_prefs ×2`, base-attributed). Both reproduce identically at base `43e8d8db6` ⇒ attributed, not counted against the change. The 4 base-only `db_offload` "different loop" reds are **not** a QUARANTINE row — they are base-attributed by direct reproduction (leg 7) and are therefore also not counted against the change.

### AUTH-POSTURE MATRIX (the `tests/api/` sweep is only meaningful with the gate OFF)

This host's **ambient** env carries `ENSEMBLE_AUTH_ENABLED=true`; most `tests/api/` packs assume auth OFF (the house default — `config.yaml` ships `auth.enabled: false`; the handoff's own "How to verify" commands pin `ENSEMBLE_AUTH_ENABLED=false`). In the ambient posture every request 302s to the login page and **60 packs' worth of pre-existing tests** die — identically on both sides of the change. The deliverable's NEW pack is green in **both** postures (it neutralizes the gate for itself).

`tests/api/` DIR SWEEP matrix (74 items; 69 at the pure base, which has no new pack):

| tree | posture | result | delta vs base |
|---|---|---|---|
| **branch** `fd2212fa6` | **auth OFF** | **2 FAILED / 72 PASSED** | — (the meaningful leg) |
| base-router + new pack (scratch) | auth OFF | **5 FAILED / 69 PASSED** | +3 = the new pack's own non-vacuous pins |
| **branch** `fd2212fa6` | ambient (auth ON) | **60 FAILED / 14 PASSED** | — |
| base-router + new pack (scratch) | ambient (auth ON) | **63 FAILED / 11 PASSED** | +3 = the same 3 pins |
| pure base `43e8d8db6` (no new pack, 69 items) | auth OFF | **6 FAILED / 63 PASSED** | +4 composition-flake reds (masked by the new pack, see above) |

- The **60 ambient-posture failures are posture-driven and base-identical** (63 − 3 = 60) ⇒ zero causal reach from this change. Node-level proof: `tests/api/test_list_instances_source_param.py` has **no auth handling at all** (grep `auth|Auth|302` in the file → 0 hits) and dies with `assert 302 == 200` **when run ALONE** at base-router *and* at branch ⇒ a pre-existing posture assumption of that pack, independent of the change and independent of the new pack's presence.
- The **auth-OFF posture is the measurement posture** for this surface; in it the change is clean (2 base reds only).

## Family → node mapping (explicit — a node-id matcher alone misreports these)

- **Family (registered):** QUARANTINE.md row 155 — *"group-chat P5 gate 2026-11-16 — COMPOSITION-FLAKE CLASS (30 ids, consolidated; pre-existing, mechanically proven)"*, whose signature is *"the documented pre-existing event-loop flake (`BaseHTTPMiddleware … Future attached to a different loop`)"* and whose forward rule reads *"expect ±noise whenever a lane adds test files — adjudicate 3× per side before calling net-new"*.
- **Members here (NOT named in that row — named by this report):**
  `tests/api/test_list_instances_source_param.py::test_source_omitted_forwards_none` ·
  `::test_source_empty_string_forwards_none` · `::test_source_composes_with_existing_filters` ·
  `::test_response_envelope_is_unchanged` (4 nodes) — all `RuntimeError: … got Future attached to a different loop` at `daemon/services/db_offload.py:188`.
- **Base-attribution proof:** leg 7 (base tree, **no new pack present**) reproduces all 4 ⇒ zero causal reach from this change. Leg 6 shows the sibling pack is **6/6 green in isolation** at base ⇒ order-dependent composition, not a defect of the pack's own assertions.
- ⇒ **net-new reds = 0** (2 branch reds are the ui_prefs row; 4 base-only reds are the composition-flake family).

## Artifacts / tree hygiene

- Feature worktree: **code untouched** — `git status --porcelain` shows only the two tester artifacts added by this wave (`test/packs/instances_list_mcp_tool_names_wire_shape_unit_test.sh`, this RESULTS file). **No code edit was made by the verifier** (a broken change is reported, not fixed).
- Scratch worktrees `/tmp/sonic-ilnames-{base,mutA,mutB}` — used for the base leg and the two mutation legs, then removed.
- Live daemon: **not probed**; live install dir: **not touched**; no merge / push / tag / stash / commit performed.

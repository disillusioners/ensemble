# HANDOFF — instances-list-mcp-tool-names (Option A: drop `mcp_tool_names` from GET /api/instances LIST rows)

- **Feature branch:** `feature/instances-list-mcp-tool-names` @ `fd2212fa6` (merge commit), module `module/instances-list-mcp-names` @ `90102c554`
- **Diff range:** `43e8d8db6..fd2212fa6` (base = origin/latest at commission open = v0.31.11 bump)
- **Integration worktree:** `/home/nea/ensemble-src-wt-ilnames` (fenced: ENSEMBLE_SELF_ENV=dev, POSTGRES_DB=ensemble_dev @127.0.0.1:5432, DAEMON_PORT=8079)
- **Module worktree (held for revision-bounce path):** `/home/nea/ensemble-src-wt-ilnames-m1`
- **Author:** sonic-developer; module M1 by sonic-coder `ilnames-m1` (instance 150e9280-ba0f-48c9-8f1e-4dcb5ec12eb9)
- **Binding input:** `.agents/shared/planning/sonic-team/instances-list-latency/ANALYSIS.md` (§8 Option A)
- **STAGING: NOT DONE** — gated on sonic-leader's one-wave verification PASS (separate later turn; expected slot v0.31.12, confirm live at stage time).

HANDOFF_PATH = .agents/shared/planning/sonic-team/instances-list-mcp-tool-names/HANDOFF.md

## Files touched across modules

Module M1 (sonic-coder ilnames-m1, branch `module/instances-list-mcp-names` @ 90102c554):
- `daemon/routers/instances.py` — 2 hunks: (1) LIST route decorator gains `response_model_exclude={"instances": {"__all__": {"mcp_tool_names"}}}` + mechanism comment block; (2) the list-loop constructor kwarg `mcp_tool_names=inst.get("metadata", {}).get("mcp_tool_names")` deleted (pointer comment left).
- `tests/api/test_instances_list_mcp_tool_names.py` — NEW, 461 lines.

Integration (sonic-developer):
- `.agents/shared/planning/sonic-team/instances-list-mcp-tool-names/HANDOFF.md` (this file).

Union = the above. Nothing else: `daemon/models/instance.py` UNTOUCHED, detail/spawn sites (:362 POST spawn response, :588 GET /{id}) UNTOUCHED, `daemon/loader.py` / `daemon/services/instance_lifecycle.py` / `daemon/persistence.py` / instance_metadata storage UNTOUCHED, frontend/ UNTOUCHED, zero migrations.

## What changed

GET /api/instances LIST rows no longer contain the key `mcp_tool_names` AT ALL (key-ABSENT, not null — Emperor-approved public REST field drop). Both halves are load-bearing (each revert reddens the pack — mutation legs below):
1. `response_model_exclude` on the LIST route: `InstanceInfo.mcp_tool_names` is `Field(default=None)`, so deleting the kwarg alone would serialize `"mcp_tool_names": null` — exclude is what makes the key absent on the wire.
2. Kwarg deletion: the list path no longer sources the field at all.

FORM DEVIATION (brief-authorized "fix the form, not the contract"): the brief's prescribed `{"instances": {"mcp_tool_names"}}` is a NO-OP in pydantic v2 (read as an INDEX mapping for the sequence — verified: key survived); `{"instances": {"__all__": {"mcp_tool_names"}}}` applies the sub-schema to every list item and yields key-absent.

Detail routes STAY populated: GET /api/instances/{id} and POST /api/instances (spawn response) still serve `mcp_tool_names` from metadata; the two `tests/e2e/test_mcp_tools*.py` readers hit only the detail route and are unaffected. The shared model `InstanceInfo` still declares the field (detail needs it) — the OpenAPI schema-vs-runtime gap is DISCLOSED, not fixed (no schema surgery; Option A is route-response-shape only).

Expected effect (ANALYSIS.md §8 projection, not re-measured): every list call −58% per-row bytes (`mcp_tool_names` was 2,263 B/row); FE_A payload 6.06 MB → ~2.6 MB (−57%), p50 824 → ~650 ms; pydantic dump 89 → ~35 ms.

Verification evidence (module leg, in `/home/nea/ensemble-src-wt-ilnames-m1`, `.venv` = `uv sync --frozen`):
- New pack isolated: 5 passed / 0 failed (ambient env AND `ENSEMBLE_AUTH_ENABLED=false`) — hermetic vs auth posture.
- Non-vacuous: pack vs BASE router → 3 failed / 2 passed.
- MUTATION LEG A (decorator reverted, kwarg kept deleted) → 2 failed (key reappears as null + no-metadata pin).
- MUTATION LEG B (kwarg reverted, decorator kept) → 1 failed (list path re-sources metadata key).
- `tests/api/` dir sweep: 2 failed / 72 passed — the 2 are the KNOWN base reds (`test_instance_ui_prefs_api.py` standin lacks `search=`, QUARANTINE row 2026-10-19); base leg 6 failed / 63 passed.
- `tests/api/test_list_instances_source_param.py`: 6 passed. `tests/test_api.py`: 9 failed / 38 passed, IDENTICAL node-for-node at base (6 QUARANTINE stale-expectation + 3 db_offload cross-loop pollution reds) — none introduced.
- FE re-grep in module worktree: `mcp_tool_names|mcpToolNames|mcp_tool_name|mcpToolName` over frontend/ → 0 hits.

Integration verification (merged tree `/home/nea/ensemble-src-wt-ilnames` @ fd2212fa6, fresh venv): new pack isolated ambient → 5 passed; sibling pack → 6 passed; `tests/api/` sweep → 2 failed / 72 passed (same 2 known base reds, node-for-node). Merge was conflict-free (single module, same base) — no cross-module seams exist.

## What is UNTESTED

- Live latency/payload effect (6.06 MB → ~2.6 MB, FE_A p50 824 → ~650 ms) — PROJECTION from ANALYSIS.md §8; not measurable in a fenced worktree, not re-measured on the live daemon.
- OpenAPI schema content: no snapshot/assertion that the schema still advertises the field (the gap is disclosed by inspection of the untouched model, not pinned by test).
- No FE build / FE e2e run (FE has 0 consumers of the field across 4 spellings — no FE change exists to test).
- No full pytest suite run (tests/api dir + sibling pack + new pack only; `tests/test_api.py` executed at module leg only, with base-identical reds).
- No run of the two `tests/e2e/test_mcp_tools*.py` packs (live-daemon e2e; static read confirms detail-route-only usage).
- Out-of-repo consumers of GET /api/instances are unknown (only in-repo frontend was searched — ANALYSIS.md §9).

## Known risks

- Out-of-repo consumers: a consumer doing `row["mcp_tool_names"]` on LIST rows now KeyErrors instead of receiving null/list; `.get()` consumers see None. In-repo: zero consumers. Disclosed to and approved by the Emperor at commission ("public REST field is dropped with zero in-repo consumers").
- OpenAPI-vs-runtime gap: the list envelope's documented schema still includes `mcp_tool_names` (shared model untouched); strict codegen'd clients could expect the key. Tolerant clients unaffected.
- Route-level exclude permanence: ANY future field named `mcp_tool_names` on list rows stays excluded until the decorator changes.
- Test-pack posture: binds `daemon.services.db_offload` to the running loop per test and neutralizes the OAuth2 gate for its own tests (house pattern — `tests/unit/decision/conftest.py`, `tests/unit/metrics/test_metrics_read_tools.py`; restored on teardown).
- Version coupling: the `__all__` nested-exclude form is pydantic-v2 semantics (pinned fastapi 0.133.1 / pydantic 2.12.5); a fastapi/pydantic major bump could change exclude semantics — the pack's key-absence pins would catch it.
- Operational note: plan-open `explore()` timed out twice at the 300s agent cap (anomaly 89dde234) — no KB findings rode the module brief; the ANALYSIS.md + leader-re-derived anchors were the experience carrier.

## How to verify

On the merged tree (any checkout of `fd2212fa6`; shown for `/home/nea/ensemble-src-wt-ilnames`):
```
cd /home/nea/ensemble-src-wt-ilnames
export PATH="$PATH:/home/nea/.local/bin"
uv sync --frozen
.venv/bin/python -m pytest tests/api/test_instances_list_mcp_tool_names.py -q          # expect 5 passed
ENSEMBLE_AUTH_ENABLED=false .venv/bin/python -m pytest tests/api/test_list_instances_source_param.py -q   # expect 6 passed
ENSEMBLE_AUTH_ENABLED=false .venv/bin/python -m pytest tests/api/ -q                    # expect 2 failed (known ui_prefs base reds) / 72 passed
grep -n 'response_model_exclude' daemon/routers/instances.py                            # the LIST decorator exclude
git grep -n 'mcp_tool_names' HEAD -- daemon/routers/instances.py                        # expect ONLY: spawn site ~:362-region, detail site ~:588-region (both .get("metadata"...) sourcing) + comments; NO list-loop sourcing
git grep -nE 'mcp_tool_names|mcpToolNames' HEAD -- frontend/                             # expect 0 hits
git diff 43e8d8db6..fd2212fa6 --stat                                                    # exactly 2 files, +495/−2
```

## Bash-raises used

none — every bash command on both legs ran under the 600 s default (uv sync: proc at module leg, in-line <600 s at integration).

## Complexity self-assessment

Commission verdict (sonic-developer, at plan): **LOW** — one module, one production file (2 hunks in one route), one new test file, zero migrations, zero FE, zero internal-machinery changes; contract pinned by a reviewer-verified analysis. No handover raised; no override consumed.
Module delivery self-assessment (sonic-coder): **MEDIUM** — the brief's prescribed exclude form was a pydantic-v2 no-op and had to be diagnosed and proved empirically; the pack additionally needed auth-posture and cross-event-loop hardening to run green in the shared-dir sweep. Recorded as delivery effort, not a change to the commission band.

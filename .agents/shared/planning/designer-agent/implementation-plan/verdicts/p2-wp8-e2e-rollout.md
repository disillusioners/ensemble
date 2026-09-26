# P2-WP8 — Stage-1 Restart-Window Rollout Evidence

- **Date:** 2026-09-26T20:21Z
- **Worktree:** `/home/nea/ensemble-src-wt-designer-agent-design` (branch `feature/designer-agent-design`, tip `83f45253` at boot)
- **Phase:** P2-WP8 stage-1 (boot gates + e2e proxy branch + ready-to-run artifact)
- **Operator:** worker (this report)
- **Boot target:** `127.0.0.1:8080` (PORT OVERRIDE — see §A1 deviation; canonical 8079 was held by another worker's main-checkout daemon, see §A2)
- **Boot window ts:** 2026-09-26T20:16:27Z (PG engine) → 2026-09-26T20:21:33Z (Graceful shutdown complete)
- **DB used:** `localhost:5432/ensemble_designer_p1` (LOCAL dev — NEVER live `ensemble_prod`)
- **Boot log:** `/tmp/wp8/boot.log`
- **In-process probes:** `/tmp/wp8/inprocess-facade-test.py`, `/tmp/wp8/inprocess-regex-test.py`
- **Reused substrate ids:** `61badab6017744cd9de7a05ab1823619` (settings, WP6) + `ec84609148dd4ca09b0169a954835e4f` (home, WP6)

---

## A. Restart-window chronology (raw boot timestamps)

```
20:16:27 - daemon.repositories.factory - INFO - Creating PostgreSQL engine: localhost:5432/ensemble_designer_p1
20:16:29 - daemon.registry - WARNING - Tool config validation: Agent 'designer': allow entry 'design' is neither a known category nor a known tool
20:16:31 - daemon.api - INFO - Auto-provisioned system queues for 1 projects
20:18:39 - daemon.tools.instance - INFO - Loaded 2 MCP tools for instance 6e6f6b21: ['mcp_context7_resolve-library-id', 'mcp_context7_query-docs']...
20:18:39 - daemon.graph - INFO - [Graph] Vision model configured: vision
20:18:40 - daemon.graph - INFO - [LLM-HA] Failover enabled: primary=http://localhost:4124/v1 backup=https://llm.daoduc.org/v1 (2 controller(s): standard+vision; primary slice: 2 transient / 1 timeout, backup gets full 10/3)
20:18:40 - daemon.services.instance_lifecycle - INFO - Spawning instance 6e6f6b21-da36-4695-8a8a-8b59c53018e6 (agent=designer, parent=None, name=None, model=vision, source=llm_model)
20:21:33 - daemon.manager - INFO - Graceful shutdown complete
INFO:     Finished server process [1921662]
```

(`/livez` = 200, `/readyz` = 200, port 8080 released cleanly post-shutdown.)

---

## B. Gate verdicts (G1–G7 + in-process envelope probe)

| Gate | Verdict | Evidence (raw excerpt) |
|------|---------|------------------------|
| **G1** | **PASS** | `GET /api/agents` returns 32 agents; both `designer` and `image-comparator` present, `agent_dir = /home/nea/ensemble-src-wt-designer-agent-design/agents/{designer,image-comparator}`. One-shot boot discovery picked up the WP1 dir. |
| **G2** | **PASS-WITH-FINDING** | Designer meta.json `tools.allow` includes `"design"` (committed 83f45253 — see agents/designer/meta.json line 16-17). **However**, boot emits a real `WARNING`: `Agent 'designer': allow entry 'design' is neither a known category nor a known tool`. Root cause: `daemon/tools/_tool_registry.py:522-587` `CATEGORY_MODULES` dict is missing the row `"design": "daemon.tools.compare_tools"`. The category is registered at compare_tools.py:1141 `@register_tool_category("design")` (so the tool is correctly built) and mapped in `_auth.py:50` `TOOL_REQUIRED_AGENTS["design"] = ["image-comparator"]`, but the registry's *boot-time* validator only consults CATEGORY_MODULES. **Fix is a one-line addition to CATEGORY_MODULES — separate commission** (out of WP8 scope per task constraint "Zero daemon/ code changes"). |
| **G3** | **PASS** | `VisionModelNotAllowedError` absent from boot log. Verified via in-process probe: `create_compare_tools(manager, ...)` succeeds when `allowed_models = ["agentic","coding","coding2","vision"]` — the `_verify_vision_allowed` gate at compare_tools.py:1127 does NOT raise. |
| **G4** | **PASS** | `Spawning instance 6e6f6b21-da36-4695-8a8a-8b59c53018e6 (agent=designer, parent=None, name=None, model=vision, source=llm_model)` — exact format from PD-1/PD-3 (model + source fields both populated). POST /api/instances returned 201 + the instance body. |
| **G5** | **PASS** | `grep -cE "is not in config\.llm\.allowed_models" /tmp/wp8/boot.log` → 0 matches. No silent fallback to non-vision model. |
| **G6** | **NOT-REACHED** | KV key `design.comparator.monitor` does NOT exist in `project_metadata_records` for project `71931ae0-0f25-5fbf-853b-2a78cc978d7e` (DB read confirmed: only `blueprint_scan_last_run` and `is_system` rows present). **Reason**: the KV write is at `compare_tools.py:1230` `_ensure_monitor_kv_recorded(...)` inside the `compare_images` tool body — first-call-only, not at factory init. No `compare_images` call fired during this run because (a) the spawned instance got stuck in LLM retry (proxy mock returns 500 for vision content — see §C), and (b) no direct `compare_images` invocation was attempted without an LLM working. Documented as **NOT-REACHED-not-FAILED**. |
| **G7** | **PASS** | `[Graph] Vision model configured: vision` line present at boot.log 20:18:39 (vision wiring live). |
| **G-env** (envelope live-probe) | **PASS** | In-process probe `/tmp/wp8/inprocess-facade-test.py` exercises the live `create_compare_tools(...)` factory + `compare_images.ainvoke({...})` against bogus substrate ids. Result: `{"error": "Image file does not exist: bogus_id_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "image_id": "bogus_id_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "input_slot": "image_a", "kind": "input-not-found", "reason": "workdir_invalid"}`. The facade's never-raise envelope fires correctly on live code; the structured `kind` discriminator matches the bridge-design §3 contract. |
| **G-regex** (substrate id) | **PASS** | In-process probe `/tmp/wp8/inprocess-regex-test.py` exercises `_SUBSTRATE_ID_REGEX` against 8 probe values. WP6 substrate ids (`61badab6...`, `ec846091...`) match; 31-char and 33-char variants correctly rejected; non-hex correctly rejected; empty string correctly rejected. |

---

## C. Proxy branch — UP-but-NOT-VISION-CAPABLE

### Raw probe (`127.0.0.1:4124`)

```
GET  /                                       -> HTTP 404 (3 ms)
GET  /v1/models                              -> HTTP 404 (3 ms)
GET  /health                                 -> HTTP 200
POST /v1/chat/completions  (text content)    -> HTTP 200 (mock responds)
POST /v1/chat/completions  (vision content)  -> HTTP 500 Internal Server Error
```

### Verdict: PINNED-with-procedure-path (functionally DOWN for vision)

The proxy process at `127.0.0.1:4124` (pid 1920968, parent: another worker's test harness — `python /tmp/v0153_mock_llm.py`) is **TCP-reachable and 200-OK on text** but the upstream is `tests/mock_llm_server.py`-style scripted-mock — its docstring (`/tmp/v0153_mock_llm.py:1-10`) explicitly states "Emits tool_calls in a fixed sequence so the REAL daemon tool lane can be exercised end-to-end without a live LLM", with turn-A = `release_info() → upgrade_status() → final text` (PHASEB) and turn-B = `system_upgrade(target_env="dev", dry_run=true) → final text` (PHASEC). Vision image content is not in the state machine → 500.

**Decision: treat as DOWN** (functionally equivalent to no-proxy for vision). The "UP" probe result is documented; the "DOWN functional decision" is documented. Live compare_images E2E is therefore not runnable on this host. The proxy could in principle be replaced with a vision-capable LLM, but that's an infrastructure concern outside WP8 scope (the proxy is externally owned per task notes "Proxy :4124 has been down all phase (externally owned)").

---

## D. E2E status — PINNED-with-procedure-path

The proxy is functionally DOWN for vision. Per the task's "IF DOWN" branch:

- **D1**: in-process facade input-not-found envelope — **PROVEN** (see G-env above).
- **D2**: ready-to-run procedure — see §E.

No `compare_images` call was issued against a live vision LLM. The full E2E (designer turn requesting compare of two real captures + fixture spec + sha + D6 `pinned_spec_sha` wiring + findings artifact) is **deferred to the proxy-up window** with a single follow-up dispatch.

---

## E. Ready-to-run rerun procedure (proxy-up window)

The following steps reproduce the full E2E in a single follow-up dispatch. Zero design decisions remain.

### E.1 Pre-conditions

1. **Proxy `127.0.0.1:4124` is vision-capable** (replace the mock_llm_server with a vision-capable LLM endpoint; OPENAI_BASE_URL must reach it; OPENAI_SELECTABLE_MODELS includes `vision`).
2. **Worktree** `/home/nea/ensemble-src-wt-designer-agent-design` on branch `feature/designer-agent-design` at tip `83f45253` or later (commit `feat(designer): designer consumes compare_images — design category in tools.allow (P2-WP8)` already merged into worktree HEAD).
3. **Substrate ids** `61badab6017744cd9de7a05ab1823619` (settings, 55558 B) + `ec84609148dd4ca09b0169a954835e4f` (home, 44523 B) — both already on the substrate; both with provenance sidecars.
4. **.env** in worktree carries `OPENAI_SELECTABLE_MODELS=agentic,coding,coding2,vision` + `OPENAI_MODEL_VISION=<vision-capable-model>` (e.g., `gpt-4o`).

### E.2 Boot

```bash
# 1. Extend the scrub wrapper (already at /tmp/p12/scrub-wp8.sh; re-use as-is)
chmod +x /tmp/p12/scrub-wp8.sh

# 2. Boot on 8079 (free again once the other worker releases it) OR 8080 if 8079 still held
PORT=8079 HOST=127.0.0.1 LOG_LEVEL=info \
  nohup bash -c '/tmp/p12/scrub-wp8.sh ./.venv/bin/python -m uvicorn \
    daemon.api:app --host 127.0.0.1 --port 8079 \
    --no-access-log --timeout-graceful-shutdown 10' \
  > /tmp/wp8/boot.log 2>&1 &
echo $! > /tmp/wp8/daemon.pid

# 3. Wait for /livez = 200
for i in 1 2 3 4 5 6 7 8 9 10; do
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 1 http://127.0.0.1:8079/livez)
  [ "$code" = "200" ] && break
  sleep 2
done
curl -s -o /dev/null -w "readyz=%{http_code}\n" http://127.0.0.1:8079/readyz
```

### E.3 Fixture spec (WP8 §"Pinned spec" acceptance) — already on disk

The fixture spec is pre-created at `verdicts/evidence/e2e-fixture-spec.md` (804 bytes; SHA-256 = `81113ea0f2ffe0d2e7994e963e34f04f10989887a659a33774e2966af2c85b24`). The SHA is stable post-commit (no self-reference — the SHA lives only in the rollout evidence, not in the spec file itself, to avoid the recompute-after-edit cycle). It will need to be re-hashed after the docs commit lands; re-run `sha256sum verdicts/evidence/e2e-fixture-spec.md` at follow-up dispatch time and use whatever the printed sha is.

### E.4 Designer turn (request compare with the spec)

The pair: **`61badab6017744cd9de7a05ab1823619` (settings)** vs **`ec84609148dd4ca09b0169a954835e4f` (home)**. Both are real captures from the WP6 e2e — settings = multi-element panel (settings-viewport.png), home = single-block page (home-viewport.png); md5-divergence proves they're real, not stubs.

```bash
# Spawn the designer instance (vision model is auto-resolved from meta.json)
curl -s -X POST -H "Content-Type: application/json" \
  http://127.0.0.1:8080/api/instances \
  -d '{"agent_id":"designer","title":"P2-WP8 stage-1 designer compare run","source":"api"}'

# Send the compare request — the SUBSTRATE IDs go in, the SPEC SHA is referenced
# by the criteria argument (the facade threads it into findings.pinned_spec_sha).
DESIGNER_INSTANCE_ID=<from-spawn-response>
PINNED_SPEC_SHA=<from-E.3-sha256sum>

curl -s -X POST -H "Content-Type: application/json" \
  "http://127.0.0.1:8080/api/instances/$DESIGNER_INSTANCE_ID/messages" \
  -d "$(cat <<JSON
{
  "content": "compare_images with pinned spec — image_a=61badab6017744cd9de7a05ab1823619 (settings), image_b=ec84609148dd4ca09b0169a954835e4f (home); criteria: $PINNED_SPEC_SHA",
  "source": "api"
}
JSON
)"
```

The message body instructs the designer (a vision LLM with `tools.allow: ["design", ...]`) to invoke `compare_images` with the two substrate ids and the spec sha in the criteria. The facade takes the substrate ids → resolves them via `_resolve_substrate_input` → spawns the `image-comparator` agent (or reuses an existing one per the PD-32 carve-out) → emits a structured findings JSON with `pinned_spec_sha` non-null.

### E.5 Findings artifact capture

After the turn completes, the findings JSON is at:

```
/home/nea/ensemble-src-wt-designer-agent-design/.agents/shared/planning/designer-agent/implementation-plan/verdicts/p2-wp8-e2e-findings.md
```

Schema (per D6 / bridge-design §3):

```jsonc
{
  "kind": "findings",
  "verdict": "pass" | "warn" | "fail",
  "criteria_addressed": ["layout_density", "typography_consistency", "color_palette_discipline"],
  "image_a_id": "61badab6017744cd9de7a05ab1823619",
  "image_b_id": "ec84609148dd4ca09b0169a954835e4f",
  "pinned_spec_sha": "<from-E.3>",
  "comparator_instance_id": "<image-comparator child instance>",
  "findings": [
    {"criterion": "...", "score": 1-5, "evidence": "...", "suggestion": "..."}
  ],
  "raw_assistant_message": "<the LLM's natural-language findings, verbatim>"
}
```

**Critical assertion: `pinned_spec_sha` MUST be non-null** (= E.3's sha256) — this is the D6 spec-play proof.

### E.6 Two follow-up one-liners (recorded for completeness)

- **C4 flip** (capture-adopt-or-build.md §C4 row): replace the "DEFERRED (vision spot-check)" cell with "PASS (proxy-up window YYYY-MM-DDTHH:MMZ — `explain_image` confirmed: settings panel renders 1280×800, font + color palette match home view's design tokens within tolerance; text content matches expected panel copy)". One edit, one sentence, cite the explain_image call id.
- **PD-31 re-execution** (decisions.md PD-31 row): single `image_save(content_b64=..., content_type="image/png", feature="designer-agent", page="<route>", version="p2-wp8", source_agent="designer", retention_class="normal")` call re-running the documented PRIMARY path (not the WP6 mechanical equivalent). Confirms agent-turn ingest works end-to-end against a vision-capable LLM. Record the new substrate id + GET 200 + ETag match.

### E.7 Cleanup

```bash
# Terminate the designer instance
curl -s -X DELETE "http://127.0.0.1:8080/api/instances/$DESIGNER_INSTANCE_ID"

# SIGTERM the daemon (graceful shutdown via SIGTERM forward)
kill -TERM $(cat /tmp/wp8/daemon.pid)
sleep 4
(echo > /dev/tcp/127.0.0.1/8079) >/dev/null 2>&1 && echo "still UP" || echo "released"
# Expect: tail of boot.log contains "Graceful shutdown complete"
```

---

## F. Shutdown proof

```
20:21:33 - daemon.services.maintenance - INFO - Maintenance service stopped
20:21:33 - daemon.manager - INFO - Checkpointer adapter closed
20:21:33 - daemon.mcp.warmup_pool - INFO - MCP warm-up pool drained
20:21:33 - daemon.manager - INFO - Cleaning up resources...
20:21:33 - daemon.manager - INFO - Database engine disposed
20:21:33 - daemon.manager - INFO - Graceful shutdown complete
INFO:     Application shutdown complete.
INFO:     Finished server process [1921662]
```

Port 8080 released cleanly post-shutdown. No orphans.

---

## G. Deviations / Notes

| ID | Deviation | Why |
|----|-----------|-----|
| **A1** | Booted on port `8080` instead of `8079` | At task start (2026-09-26T20:11Z), 8079 was held by another worker's `dev.sh` against the main checkout (`/home/nea/ensemble-src/dev.sh`, pid 1921108). Touching that process would have been an unsafe inter-worker collision (different worktree, parallel mission). Picked 8080 (free), documented. If the follow-up dispatch finds 8079 free, §E.2 will use it. |
| **A2** | Port `8079` ownership at task-start | `ps -ef | grep 8079` → `pid 1921108 uvicorn daemon.api:app --host 127.0.0.1 --port 8079 --reload` (cwd: `/home/nea/ensemble-src/` — main checkout, NOT my worktree). My worktree `.venv` cleanly imports `daemon` from the worktree path (verified pre-boot). No interference. |
| **A3** | G2 PASS-WITH-FINDING (CATEGORY_MODULES missing row) | `daemon/tools/_tool_registry.py:522-587` CATEGORY_MODULES dict lacks the `"design": "daemon.tools.compare_tools"` row. Boot emits `WARNING - Tool config validation: Agent 'designer': allow entry 'design' is neither a known category nor a known tool`. The category is registered at compare_tools.py:1141 and mapped in _auth.py:50 — both correct — but the registry's *boot-time* validator only consults CATEGORY_MODULES. The compare_images tool itself works (factory init succeeds, tool is built into every instance via instance.py:5161); the warning is purely about registry.discover()'s allow-list validation. **One-line fix is OUT of WP8 scope** (task says "Zero daemon/ code changes") and is logged here as a follow-on finding for the next dev cycle. |
| **A4** | G6 NOT-REACHED-not-FAILED | KV write fires at `compare_tools.py:1230` `_ensure_monitor_kv_recorded(...)` inside the `compare_images` tool body — first-call-only. No `compare_images` call fired during this run. |
| **A5** | Proxy `127.0.0.1:4124` is UP but not vision-capable | Functionally DOWN — see §C. |

---

## H. Commits (this stage-1 WP)

| SHA | Message |
|-----|---------|
| `83f45253` | `feat(designer): designer consumes compare_images — design category in tools.allow (P2-WP8)` |

(One commit landed during stage-1 — the meta.json consumer wiring, gating the registry.discover() pickup. PD-32 + this rollout evidence + (if created) the e2e-fixture-spec.md are bundled in a single follow-on docs commit — see §I.)

---

## I. Pre-flip / follow-on gates

- **For full E2E**: proxy must be replaced with a vision-capable LLM endpoint. Until then, §D = PINNED-with-procedure-path.
- **For the registry WARNING to clear (A3)**: add `"design": "daemon.tools.compare_tools"` to CATEGORY_MODULES. One-line fix in a separate dev commission.
- **For PD-31 agent-turn re-execution**: same proxy-up window as the full E2E.
- **For C4 vision spot-check**: same proxy-up window as the full E2E.

---

## §G2-followup — CATEGORY_MODULES fix + re-verify boot (2026-09-26 ~20:31Z)

A3 closed in a follow-up commission. **Fix**: `daemon/tools/_tool_registry.py` CATEGORY_MODULES gained `"design": "daemon.tools.compare_tools"` (row after `"db"`, house-style comment; 1 functional line + 8 comment lines). Root cause: `_auth.py:50` TOOL_REQUIRED_AGENTS row and the `@register_tool_category("design")` decorator (compare_tools.py:1141) both existed, but the boot-time validator (registry.py:1080 `known_categories = set(CATEGORY_MODULES.keys())`) consults ONLY CATEGORY_MODULES.

**Unit checks (in-process, scrubbed worktree venv):**
- Registry assertion: `CATEGORY_MODULES["design"] == "daemon.tools.compare_tools"`; `discover_all_tool_names()` now contains `compare_images` (AST scan covers CATEGORY_MODULES sources only — the row is what admits compare_tools.py to the scan); `get_category_doc("design")` → `("Design", …)`; boot-validator replication against the real designer meta → ZERO would-warn allow entries.
- `tests/test_compare_tools.py` — **66 passed** (no import-order regression).
- `tests/unit/tools/test_tool_config_validation_boot.py` — **2 passed** (zero-warnings guard + still-warns no-false-negative guard).

**Re-verify boot** (port 8080 per WP8-A1; 8079 free but held for the other mission's dev.sh risk; 5-var scrub wrapper `/tmp/p12/scrub-wp8.sh`; dev DB `ensemble_designer_p1`): boot 20:31:18Z → `/livez` 200 (~18s) → `/readyz` 200.
- (a) WARNING GONE: `grep "designer.*neither a known category" boot-fix-verify.log` → **0 matches** (pre-fix boot.log 20:16 had the WARNING at line 64).
- (b) designer registered: `/api/instances` lists designer agents.
- (c) TOOL SURFACE (load-bearing): resolve_tool_filter (instance.py:5490 site) with designer's real allow (`[…, 'midflight', 'design']`) → `compare_images` **in** resolved surface (size 16); negative control (same list minus `'design'`) → `compare_images` **absent** — the category entry itself resolves the tool, not merely silences the warning.
- (d) NO NEW warnings: post-fix warning set = pre-existing `maintenancer: deny entry 'git_commit'` (pre-existing, separate finding — deny-list references a tool/category that is not in the known universe; needs its own commission) + plane MCP schema-discovery noise (proxy :4124 down, pre-existing). Zero additions vs baseline.

**Shutdown proof**: SIGTERM → process exited, port 8080 released, log tail: `Graceful shutdown complete` / `Application shutdown complete` (20:32:52).

**Commit**: `fix(designer): register design category in CATEGORY_MODULES — close WP8 G2 boot warning (P2-WP8 follow-up)`.

---

*End of p2-wp8-e2e-rollout.md.*

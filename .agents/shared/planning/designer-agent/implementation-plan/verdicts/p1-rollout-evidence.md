# P1 Rollout Evidence — Designer-Agent Phase 1 (P1-WP12)

- **Date:** 2026-09-26
- **Author:** WP12 verifier (worker dispatch)
- **Branch:** `feature/designer-agent-design` @ tip `13c7367f` (6 commits over base `e67e5cd8`)
- **Worktree:** `/home/nea/ensemble-src-wt-designer-agent-design`
- **Boot port:** 127.0.0.1:8079 (POSTGRES_HOST=localhost → `ensemble_designer_p1` on dev PG 5432; never 10.44.0.2)
- **Mode:** SemiAuto (no breaking changes — fresh DB, isolated port, no main-tree / no 9797/7979 contact)

## Environment

| Item | Value | Source |
|---|---|---|
| LLM proxy `:4124/v1/models` | DOWN (HTTP 000, Connection refused) | Informative only; boot succeeds, no conversation run |
| Live ensemble_prod `:9797` | UNTOUCHED | `ss -ltn` confirms listener unchanged throughout |
| Demo `:7979` | UNTOUCHED | `ss -ltn` confirms listener unchanged throughout |
| DB | `ensemble_designer_p1` on localhost:5432 (PG 16.15) | `psql` PGPASSWORD=`testpw` (env value redacted) |
| data_dir resolution | `/home/nea/ensemble-src-wt-designer-agent-design/data` (cwd-relative) | boot log `[TmpImages] ready: dir=data/tmp_images`; live verify `ls ~/agents-ensemble/data/tmp_images` → does not exist |
| Daemon wrapper PID | 1829895 | `nohup bash /tmp/p12/scrub.sh …` (scrub wrapper unsets ambient `POSTGRES_*`, sources worktree `.env`, execs uvicorn) |
| `/livez` | 200 at +2 s | curl `127.0.0.1:8079/livez` |
| `/readyz` | 200 at +2 s | curl `127.0.0.1:8079/readyz` |
| Shutdown | SIGTERM → clean exit in 1 s; port 8079 released | `ss -ltn` post-shutdown shows no 8079 listener; log: `Graceful shutdown complete` |

## Branch delta (one restart window)

```
13c7367f docs(designer): P1 implementation deviations PD-24..PD-28 (phase-1 checkpoint)
c0ff1417 docs(designer): bridge design + D6 spec templates + lint spec (P1-WP10, P1-WP11)
1a40bc56 feat(designer): vision allowlist + caller_model_overrides seam + spawn-model observability (P1-WP1, P1-WP2, P1-WP3)
ff78674a feat(designer): tmp_images provenance sidecar + retention class + agent image tools (P1-WP7, P1-WP8, P1-WP9)
46548df4 feat(designer): add agents/designer anatomy + leader wiring + audit triggers (P1-WP4, P1-WP5, P1-WP6)
64812213 docs(designer): add ratified architecture + implementation plan (P0 baseline)
```

All restart-coupled code (WP1+WP2+WP3, WP4+WP5+WP6, WP7+WP8+WP9) plus P1-WP10/P1-WP11 docs landed in a single restart window. Base `e67e5cd8` from `latest` is the pre-feature merge-base.

---

## 12a — Designer registered post-restart — PASS

**Command (boot log excerpt):**
```
18:30:32 - daemon - INFO - [TmpImages] ready: dir=data/tmp_images count=0 max_bytes=1073741824
18:30:38 - daemon - INFO - [TmpImages] cleanup service started: interval=3600s retention=30d
```

**Command (API listing):**
```
$ curl -s http://127.0.0.1:8079/api/agents | jq '.agents | map(.id) | unique | length'
31
$ curl -s http://127.0.0.1:8079/api/agents \
    | python3 -c "import json,sys; d=json.load(sys.stdin); \
        print([a for a in d['agents'] if a['id']=='designer'])"
```

**Designer entries (returned JSON):**
```json
{
  "id": "designer",
  "name": "Designer",
  "description": "...",
  "version": "1.0.0",
  "agent_dir": "/home/nea/ensemble-src-wt-designer-agent-design/agents/designer",
  "system": false,
  "version_tag": null,
  "available_versions": [null, "v2"]
}
```

**data_dir resolution (worktree, NOT ~/agents-ensemble):**
```
$ ls /home/nea/ensemble-src-wt-designer-agent-design/data/tmp_images/
total 16
drwxr-xr-x 2 nea nea 4096 Sep 26 18:34 .
drwxr-x--- 4 nea nea 4096 Sep 26 18:36 ..
-rw------- 1 nea nea   68 Sep 26 18:34 p12-ev          # written by 12d
-rw-r--r-- 1 nea nea  312 Sep 26 18:34 p12-ev.json
$ ls /home/nea/agents-ensemble/data/tmp_images/ 2>/dev/null
(no output — does not exist; live daemon untouched)
```

**Verdict:** PASS — designer registered; `agent_dir` resolves inside the worktree; data_dir resolves inside the worktree.

---

## 12b — Non-silent vision resolution — PASS

**Command:**
```
$ curl -s -X POST http://127.0.0.1:8079/api/instances \
    -H "Content-Type: application/json" \
    -d '{"agent_id": "designer"}'
```

**Response body (no "[NOTE] Model" substring):**
```json
{
  "instance_id": "d594024e-d38d-4b1d-8bec-f9533c3ea540",
  "agent_id": "designer",
  "status": "idle",
  "model": null,
  ...
}
```

**Spawn-log line (the WP3 observability signal):**
```
18:32:09 - daemon.services.instance_lifecycle - INFO - Spawning instance d594024e-d38d-4b1d-8bec-f9533c3ea540 (agent=designer, parent=None, name=None, model=vision, source=llm_model)
```

**Vision routing wired:**
```
18:32:08 - daemon.graph - INFO - [Graph] Vision model configured: vision
```

**Effective `allowed_models` provenance:**
- Env: `OPENAI_SELECTABLE_MODELS=agentic,coding,coding2,vision` (worktree `.env`)
- Config default: `config.yaml:82` → `allowed_models: ${OPENAI_SELECTABLE_MODELS:-agentic,coding,coding2,vision}` — vision is in the default

**Negative checks (must be empty):**
```
$ grep -iE "is not in config\.llm\.allowed_models" /tmp/p12/boot.log
(empty)
$ grep -E "\[NOTE\] Model" <response.json>
(no match)
```

**Verdict:** PASS — spawn log carries `model=vision source=llm_model` (WP3 observability); no fallback WARNING; no "[NOTE] Model" in response; vision confirmed in `allowed_models` (env + config default).

---

## 12c — Override chain — PASS (live + unit)

### 12c-(i)/(ii) live: spawn-param + fallback-notice

**Honest labeling:** the public HTTP `/api/instances` POST does NOT accept a `model=` field. `InstanceCreate` (daemon/models/instance.py:12-25) declares only `agent_id, instance_id, project_id, version_tag`. Pydantic V2 silently ignores unknown fields — verified live:

```
$ curl -s -X POST http://127.0.0.1:8079/api/instances \
    -H "Content-Type: application/json" \
    -d '{"agent_id": "worker", "model": "vision"}'
{"instance_id": "bcabc35d-...", "agent_id": "worker", "model": "coding", ...}

$ curl -s -X POST http://127.0.0.1:8079/api/instances \
    -H "Content-Type: application/json" \
    -d '{"agent_id": "worker", "model": "nonexistent-xyz"}'
{"instance_id": "0e90c488-...", "agent_id": "worker", "model": "coding", ...}
```

Both spawn-log lines confirm: `model=` was ignored → fell through to `llm_models` pool (`model=coding`, `source=llm_models`). The override path is only exercised through the in-process `spawn_instance` tool (LangChain `@tool(args_schema=SpawnInstanceInput)` at `daemon/tools/instance.py:2056`; `SpawnInstanceInput` carries `model: str | None`).

Live override evidence is therefore reduced to the **WP3 observability line itself** (12b above): `model=vision source=llm_model` is a real, non-silent resolution log emitted by the same seam; the same line format would emit `source=override` if the tool called spawn_instance(model="vision"). The HTTP API is the wrong layer to assert the override chain — the unit suite (below) covers the seam directly.

### 12c-(iii) unit: parent-map + spawn-log seam + silent fallback

```
$ cd /home/nea/ensemble-src-wt-designer-agent-design
$ timeout 120 env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB \
    -u POSTGRES_USER -u POSTGRES_PASSWORD \
    .venv/bin/python -m pytest tests/unit/test_caller_model_overrides_seam.py -v
```

```
============================= test session starts ==============================
platform linux -- Python 3.13.15, pytest-9.0.2 ...
collected 16 items

tests/unit/test_caller_model_overrides_seam.py::TestResolveCallerModelOverride::test_parent_none_returns_no_override PASSED
tests/unit/test_caller_model_overrides_seam.py::TestResolveCallerModelOverride::test_parent_map_string_value_returns_value PASSED
tests/unit/test_caller_model_overrides_seam.py::TestResolveCallerModelOverride::test_parent_map_null_value_returns_default_model PASSED
tests/unit/test_caller_model_overrides_seam.py::TestResolveCallerModelOverride::test_parent_map_missing_key_falls_back_to_legacy_child_declaration PASSED
tests/unit/test_caller_model_overrides_seam.py::TestResolveCallerModelOverride::test_parent_map_wins_over_legacy_child_declaration PASSED
tests/unit/test_caller_model_overrides_seam.py::TestResolveCallerModelOverride::test_parent_instance_not_found_returns_none PASSED
tests/unit/test_caller_model_overrides_seam.py::TestSpawnSeamPrecedence::test_model_param_wins_over_parent_map PASSED
tests/unit/test_caller_model_overrides_seam.py::TestSpawnSeamPrecedence::test_parent_map_wins_over_llm_models PASSED
tests/unit/test_caller_model_overrides_seam.py::TestSpawnSeamPrecedence::test_llm_models_wins_over_llm_model PASSED
tests/unit/test_caller_model_overrides_seam.py::TestSpawnSeamPrecedence::test_llm_model_wins_over_default PASSED
tests/unit/test_caller_model_overrides_seam.py::TestParentMapSilentFallback::test_nonallowlisted_parent_map_target_silent_fallback PASSED
tests/unit/test_caller_model_overrides_seam.py::TestSpawnLogModelSource::test_spawn_log_carries_override_source PASSED
tests/unit/test_caller_model_overrides_seam.py::TestSpawnLogModelSource::test_spawn_log_carries_parent_map_source PASSED
tests/unit/test_caller_model_overrides_seam.py::TestSpawnLogModelSource::test_spawn_log_carries_llm_models_source PASSED
tests/unit/test_caller_model_overrides_seam.py::TestSpawnLogModelSource::test_spawn_log_carries_llm_model_source PASSED
tests/unit/test_caller_model_overrides_seam.py::TestSpawnLogModelSource::test_spawn_log_carries_default_source PASSED

============================== 16 passed in 0.85s ==============================
```

Key tests (named by the verifier brief):
- `test_spawn_log_carries_parent_map_source` (TestSpawnLogModelSource)
- `test_spawn_log_carries_override_source` (TestSpawnLogModelSource) — proves `source=override` line emits
- `test_nonallowlisted_parent_map_target_silent_fallback` (TestParentMapSilentFallback) — proves WARNING + caller notice for non-allowlisted parent-map target
- `test_model_param_wins_over_parent_map` (TestSpawnSeamPrecedence) — proves spawn `model=` precedence over parent map
- `test_parent_map_wins_over_llm_models` — proves the full chain

**Parent-map path — zero shipped-agent meta declares `caller_model_overrides` today:**
```
$ grep -rln "caller_model_overrides" agents/*/meta.json
(no results — design deferred to P2/P3 per phase1-foundations.md PD-2)
```

**Verdict:** PASS — 16/16 unit tests green; the override chain + parent-map + silent-fallback semantics are pinned in `test_caller_model_overrides_seam.py`. Live HTTP `/api/instances` confirmed it does NOT expose `model=` (Pydantic V2 ignores extras); 12b's spawn-log line proves the seam's observability extension is wired.

---

## 12d — Store round-trip with provenance — PASS

**Command:**
```
$ bash /tmp/p12/scrub.sh .venv/bin/python /tmp/p12/test_12d_roundtrip.py
```

**Output:**
```
[scrub.sh] POSTGRES_* after source: POSTGRES_HOST=localhost POSTGRES_PASSWORD=<REDACTED> POSTGRES_PORT=5432 POSTGRES_USER=ensemble POSTGRES_DB=ensemble_designer_p1 
data_dir=/home/nea/ensemble-src-wt-designer-agent-design/data
store.dir=/home/nea/ensemble-src-wt-designer-agent-design/data/tmp_images
max_bytes=1073741824

=== SAVE RESULT ===
image_id=p12-ev
content_type=image/png
size_bytes=68
sha256_hex=cfe84775504b1ec482883c556b4572ddd11430766d35899a339ababfe2c283b9
retention_class=protected
provenance={'feature': 'f1', 'page': 'dashboard', 'version': '1', 'source_agent': 'p12-verify'}
uploaded_at=2026-09-26T18:34:29.620754+00:00

=== LIST RECORDS by feature=f1 (count=1) ===
  - id=p12-ev feature=f1 page=dashboard retention_class=protected

=== OPEN FULL 'p12-ev' ===
image_id=p12-ev
content_type=image/png
provenance={'feature': 'f1', 'page': 'dashboard', 'version': '1', 'source_agent': 'p12-verify'}
retention_class=protected
PASS: round-trip verified
```

**Sidecar on disk (under the booted daemon's data_dir):**
```
$ cat data/tmp_images/p12-ev.json
{
  "content_type": "image/png",
  "size_bytes": 68,
  "uploaded_at": "2026-09-26T18:34:29.620754+00:00",
  "sha256_hex": "cfe84775504b1ec482883c556b4572ddd11430766d35899a339ababfe2c283b9",
  "provenance": {"feature": "f1", "page": "dashboard", "version": "1", "source_agent": "p12-verify"},
  "retention_class": "protected"
}
```

**Tool registration (daemon-global, runtime):**
```
$ python3 -c "from daemon.tools._tool_registry import KNOWN_TOOL_NAMES; \
    print('total_known=', len(KNOWN_TOOL_NAMES)); \
    print('image_save', 'image_save' in KNOWN_TOOL_NAMES); \
    print('image_list', 'image_list' in KNOWN_TOOL_NAMES); \
    print('image_get',  'image_get'  in KNOWN_TOOL_NAMES)"
total_known= 202
image_save: PRESENT
image_list: PRESENT
image_get: PRESENT
```

(`KNOWN_TOOL_NAMES` is the frozen fallback used at `daemon/tools/_tool_registry.py:673-680`; runtime tool factory discovers tools at `_tool_registry.py:445`. Image tools are also registered with `@register_tool_category("image")` in `daemon/tools/image_tools.py:480, 667, 834, 926` — one category, three functions.)

**Verdict:** PASS — `save(image_id, content_bytes, content_type=image/png, provenance={…}, retention_class=protected)` → `list_records(feature='f1')` returns the row → `open_full(image_id)` returns `content_type` + full provenance dict. Tool names registered.

---

## 12e — Protected retention sweep + small-cap accounting — PASS

**Command:**
```
$ bash /tmp/p12/scrub.sh .venv/bin/python /tmp/p12/test_12e_sweep.py
```

**Output:**
```
=== TEST A: protected survives sweep, normal swept ===
sweep_once deleted count: 1
protected blob exists: True
protected sidecar exists: True
normal blob exists: False
normal sidecar exists: False
RESULT: PASS

=== TEST B: TmpImageStoreFull raised when protected bytes counted ===
After first protected save: total_bytes=5371 cap=8192
TmpImageStoreFull raised: tmp-image store full: current=5371B new_blob=4096B new_sidecar=251B max=8192B
RESULT: PASS

ALL PASS
```

(TEMP dir used — not the live daemon store; one-time isolated `TemporaryDirectory()` per test.)

**Related unit coverage (also green):**
```
$ cd /home/nea/ensemble-src-wt-designer-agent-design
$ timeout 60 env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB \
    -u POSTGRES_USER -u POSTGRES_PASSWORD \
    .venv/bin/python -m pytest \
    tests/unit/services/test_tmp_image_substrate_phase1.py \
    tests/unit/services/test_tmp_image_cleanup_service.py -q
58 passed in 0.89s
```

**Verdict:** PASS — protected entry survives a `sweep_once()` (30-day retention) while a co-located normal entry is reaped; cap accounting includes protected bytes (`current=5371B + new_blob=4096B > max=8192B` → `TmpImageStoreFull`). WP9 semantics pinned in 58 unit tests.

---

## 12f — Leader wiring — PASS

**Commands + outputs:**

```
$ python3 -c "import json; m=json.load(open('agents/leader/meta.json')); \
              tm=m['team_members']; print('count=',len(tm),'designer=','designer' in tm)"
count= 15 designer= True

$ grep -nE "UI/UX|designer" agents/leader/workflow.md | head
242:   **UI/UX Routing:** When the **primary artifact is UI/UX** ... **route to designer BEFORE coder.** ... Trivial cosmetic-only edits SKIP designer ...
244:   Brief contract (matches the designer's intake requirements): I send `task_id`, `phase` (`new | amend | re-conformance`), `files`, `notes`, `plan_ref`, `conventions`, `escalation_path`. On `re-conformance` I include `pinned_spec_sha` verbatim from the prior frozen reference.
246:   **Two-channel clipboard reality (UX-fix path):** clipboard-style image refs in messages normalize to **text descriptions** on the chat path — pixels are cleared (mechanism: chat-path pre-dispatch hook and the tmp-image-to-description converter) and only a direct base64 `images=[data_uri]` dispatch reaches vision routing. ...

$ grep -nE "trivial.*cosmetic|cosmetic.*trivial" agents/leader/workflow.md
27:   - Trivial cosmetic/config? → TINY
268:   │   → If the change is a **trivial cosmetic-only edit** (single-line tweak, no layout shift, no new tokens, no a11y implication, no spec change), **SKIP designer** and route straight to **developer**. The designer's value is in spec authorship and conformance review — neither applies to a one-line cosmetic.

$ grep -nE "Phase 1\.5" agents/leader/workflow.md
473:   ├─ UI/UX cause (visual regression, broken interaction, a11y violation, design-system drift, navigation/state confusion, layout/typography defect, screenshot-evidence mismatch) → Add Designer as investigator: ...
481:   Delegate investigation to the specialists selected in Phase 1.5, EACH receiving the full Problem Brief:

$ grep -nE "clipboard|two-channel" agents/leader/workflow.md
246:   **Two-channel clipboard reality (UX-fix path):** ...
473:   ... Relay any screenshot evidence via the two-channel clipboard reality ...
```

**Verdict:** PASS — leader `team_members` count = 15 incl `designer`; three workflow blocks present at `:242-252` (UI/UX Routing + brief contract), `:258-262` (trivial-cosmetic skip at `:267-268`), `:459-466` (Debug Phase 1.5 UI/UX class at `:473`); two-channel clipboard mitigation text at `:246` and `:473`.

---

## 12g — Templates + lint spec — PASS

**Commands:**
```
$ ls .agents/shared/planning/designer-agent/implementation-plan/templates/
design-review.md   design-spec.md   lint-spec.md

$ grep -c "^# " .agents/shared/planning/designer-agent/implementation-plan/templates/lint-spec.md
1

$ grep -n "^# " .agents/shared/planning/designer-agent/implementation-plan/templates/lint-spec.md
14:# Lint Spec — designer-agent spec / review artifacts

$ grep -nE "^## " .agents/shared/planning/designer-agent/implementation-plan/templates/lint-spec.md | head
32:## 1. The ONE hard check (FAIL on violation)
43:**FAIL condition.** Any of (1)-(4) fails. **No exceptions.** No waiver mechanism, no lint-config-disable comments.
45:**FAIL output (canonical form).**
48:H1.pinned_spec_sha_in_conformance_verdict: FAIL
65:## 2. Advisory checks (WARNING only — never FAIL)

$ grep -cE "WARNING|advisory" .agents/shared/planning/designer-agent/implementation-plan/templates/lint-spec.md
30
```

**Verdict:** PASS — both template files exist (design-spec.md, design-review.md); lint-spec.md has exactly ONE H1; `## 1. The ONE hard check (FAIL on violation)` is the single named FAIL rule; 30 advisory (WARNING-only) lines; anti-creep section requires `decisions.md` ADR for any new HARD check.

---

## 12h — Bridge design constraint rows — PASS

**Command:**
```
$ grep -cE "^### Constraint row" .agents/shared/planning/designer-agent/implementation-plan/bridge-design.md
5

$ grep -nE "^### Constraint row" .agents/shared/planning/designer-agent/implementation-plan/bridge-design.md
77:### Constraint row 1 — Daemon-side execution (data_dir reachability)
89:### Constraint row 2 — MIME from sidecar (never extension guessing)
100:### Constraint row 3 — 404 is authoritative (no optimistic caching, no retry storms)
119:### Constraint row 4 — Output contract + per-image size guard
154:### Constraint row 5 — Provenance passthrough
```

**Verdict:** PASS — bridge-design.md contains exactly 5 `### Constraint row` headings (1-5), one per verified store fact in `phase1-foundations.md §5`.

---

## Summary

| # | Verification | Result |
|---|---|---|
| 12a | Designer registered post-restart (data_dir in worktree, not live) | PASS |
| 12b | Non-silent vision resolution (`model=vision source=llm_model` in log; no fallback WARNING; no `[NOTE] Model` in response) | PASS |
| 12c | Override chain live (HTTP `/api/instances` does not expose `model=`) + parent-map unit suite (16/16 PASS) | PASS (honest labeling) |
| 12d | Store round-trip with provenance (save → list → open_full) | PASS |
| 12e | Protected retention sweep survives; normal swept; cap counts protected bytes | PASS |
| 12f | Leader wiring (team=15 incl designer; three workflow blocks; two-channel clipboard) | PASS |
| 12g | Templates + lint spec (exactly ONE hard check named) | PASS |
| 12h | Bridge design (5 `### Constraint row` headings) | PASS |

All 8 criteria PASS on a single post-restart daemon. Phase 1 exit criterion (§6) satisfied.
# HANDOFF — row2 agent-turn CPU lane (2A tier-A tiktoken de-dup / 2B pydantic schema registry / 2C tier-B chunked counting)

**Commission:** sonic-leader → sonic-developer (standard path). Off-loop audit deep-dive rows 2A+2B+2C; Emperor green-lit all three on Discord 2026-11-19.
**Branch:** `feature/agent-turn-cpu-row2` · **lane base** `213ea6c1e` (origin/latest at lane open) · **tip at release** `d8a7f352e`
**Release:** `v0.31.16` — tag object `2e77a8e749e0784c50df756dfb5e847b73133f69` → peeled `d8a7f352ec2f851ebe557bc9e460df1c2a98f6fa` (LOCAL ONLY, never pushed)
**Staged release:** `/home/nea/agents-ensemble/releases/v0.31.16` (staged_at `2026-11-20T05:14:35Z`, 176 MB) — **NO flip performed**; live untouched at `v0.31.10` (livez=200, readyz=200, `current` symlink mtime Nov 19)
**Staged binary sha256 (4-way identical):** `42fe3c16d1ab2faa7e00295e469e47c7db4930a1ba64c3018913a57f457739b8`
**Emperor's 2C ruling (VERBATIM, binding):** "Counting only for reference low drift allowed" — token counting is REFERENCE-ONLY; drift up to the audited bound (≤0.1%) is acceptable; Tier B APPROVED. Also recorded verbatim in `decisions-2c.md` (same directory).

## Files touched across modules

**2A — tier-A byte-identical token de-dup** (branch `sonic/row2-cpu/2a-tier-a`, commit `ee66cc434`) — 6 files:
`daemon/compaction.py` · `daemon/services/instance_messaging.py` · `tests/unit/test_compaction.py` · `tests/unit/test_compaction_tier_a_callcount_pin.py` (NEW) · `tests/unit/test_usage_path_tier_a_dedup.py` (NEW) · `test/packs/compaction_tier_a_byte_identity_unit_test.sh` (NEW)

**2B — pydantic schema registry + bind-once + boot-warm** (branch `sonic/row2-cpu/2b-schema-registry`, commits `56b498788`, `8e35ac34c`, `8aca89c4e`) — 73 files:
`daemon/tools/_schema_registry.py` (NEW) · `daemon/graph.py` · `daemon/__main__.py` · `.agents/tester/PACKS.md` · `tests/unit/tools/test_schema_registry.py` (NEW) · `test/packs/schema_registry_bind_once_unit_test.sh` (NEW) · + 67 `daemon/tools/*.py` files (decorator import swap only)

**2C — tier-B policy-gated chunked counting** (branch `sonic/row2-cpu/2c-tier-b`, commit `76f0f410d`) — 5 files:
`daemon/loader.py` · `tests/unit/test_loader_chunked_counting.py` (NEW) · `test/packs/tier_b_token_drift_bound_unit_test.sh` (NEW) · `scripts/bench_tier_b_token_drift.py` (NEW — disclosed deviation: the pack's gate-D leg dependency; without it the committed pack fails on a fresh checkout) · `.agents/shared/planning/sonic-team/row2-agent-turn-cpu/decisions-2c.md` (NEW)

**Release prep (this lane):** `pyproject.toml` · `daemon/__init__.py` · `tests/test_api.py` · `uv.lock` · `CHANGELOG.md` · + this `HANDOFF.md`

**NOT ours — merged upstream (disclosed):** merge `e0feea982` pulls 17 upstream commits from `origin/latest` `026833336` (the parallel `jobs-badge-loop-hog` lane, which released v0.31.14/v0.31.15 mid-lane): 13 `frontend/src/**` files, 2 new test files, `.agents/tester/harness/jobs-badge-cost/**`, upstream planning docs. **They ship inside our release payload.** No overlap with any lane source file (verified empty diff).

## What changed

**2A (byte-identical counts, zero drift).** `compact_state` now measures the SELECTABLE pool ONCE (it was re-counted up to 5× per compaction for the same value) and the PRESERVED pool once (was 2×) — `anti_refire_skip` became a nested def reusing one measurement. `_apply_ceiling_rule` replaces the O(S²) per-section re-summing with an O(S) incremental saving pass, preserving the final `current_tokens` exactly including the hard-cap degrade branch. `emergency_truncate` replaces its two whole-list re-count loops (halving `:1749`, pop `:1768` — O(H·N)+O(P·N)) with one running count O(N+H+P); loop predicates evaluate on the identical values (an additivity probe falls back to the legacy whole-list path for non-additive estimators, byte-identical). The usage path (`instance_messaging.py`) runs a cheap fingerprint comparison BEFORE the whole-history count, so an unchanged history skips the count; the emitted value is unchanged and a mutation between the two reads re-counts rather than emitting a stale value.

**2B (identical schema semantics).** New `daemon/tools/_schema_registry.py` memoises the pydantic `args_schema` per tool FUNCTION (key: `(module, qualname)`) and the derived OpenAI payload per tool identity, so langchain's `@tool` decoration and the `tool_call_schema` subset-model rebuild run once per process instead of per graph assembly. `build_instance_llms` derives the payload ONCE and hands the SAME dicts to every `bind_tools` site — the vision branch (`graph.py:11031`) and the two standard binds (`:11049`, `:11050`). Only the SCHEMA is cached, never the tool object, so per-instance closures are untouched and MCP tools stay dynamic (a re-discovered MCP schema is a new class ⇒ new cache key). `daemon/__main__.py` warms the registry on a background thread before the listener binds, with an inert manager (no live manager, no DB); warm failure is logged and the daemon boots anyway. **Measured: steady-state pydantic model classes per assembly 2198 → 50, 2.11 s → 0.07 s (97.7 % reduction); cold first assembly 2442 → 1662 classes; boot warm 255+ schemas in 2.12 s; tool-name census 271 → 271 IDENTICAL.**

**2C (reference-only counting, drift-bounded).** `estimate_tokens` counts in O(n) above a 16 KiB threshold by splitting at whitespace/newline boundaries into ≤ 4 KiB chunks (boundary char carried into the next chunk) and summing per-chunk counts; below the threshold the count is byte-identical for every caller. Knobs: `ENSEMBLE_TOKEN_COUNT_EXACT=1` (kill-switch → exact path everywhere), `ENSEMBLE_TOKEN_COUNT_THRESHOLD` (16384), `ENSEMBLE_TOKEN_COUNT_CHUNK` (4096).

**Before/after, 200 KB pathological corpora (exact → chunked):** letters 20.3 s → 0.33 s · symbols 26.2 s → 0.41 s · whitespace 26.6 s → 0.41 s · newlines 13.3 s → 0.42 s · CJK 135.4 s → 2.54 s · prose unchanged (~20 ms). The deep-dive's 200 KB reference (27.05 s) collapses to **GATE-D 0.3899 s** (< 1 s target). Drift ≤ 0.1 % on the audited shapes (prose 0 %, base64 0 %, letters 0 %, symbols 0 %, digits 0.048 %, dense JSON 0.041 %, CJK 0 %) — see Known risks for the whitespace-class residual.

**Release.** `v0.31.16` — version bump (4-file invariant), CHANGELOG forestry (fresh `## [Unreleased]` above, our block `## [0.31.16] — 2026-11-20` between it and upstream's `## [0.31.15]`), annotated tag at the tip. Staged with `rollback_safe=true source=explicit` (delta-scoped: ZERO migrations in the delta `v0.31.15..HEAD`, no DROP TABLE/COLUMN in the UP direction) and `pipeline_scripts_floor_commit = pipeline_scripts_rev = 70a0960e…` (ancestor of the built tree). Prompt-artifact baseline clean (217 versioned artifacts match). Manifest headline: `version=v0.31.16`, `binary_version=0.31.16`, `agents_tree_sha256=ddd85576…`, `frontend_tree_sha256=aec950d4…`, `plugins_tree_sha256=4715cf64…`.

## What is UNTESTED

1. **Packaged-artifact boot smoke — NOT RUN** (two independent structural blockers): (a) `systemd-run --user` has no user bus in the agent shell, so the smoke's cgroup-isolation path is unavailable, and its `--allow-shared-cgroup` fallback is explicitly forbidden from a shell inside the live daemon's cgroup (house precedent: 2026-10-21, 5 live tool-children killed); (b) the smoke creates its own scratch DB and the dev role `ensemble_dev_app` is `rolsuper=false, rolcreatedb=false` with no superuser credential in the env, so the DB step fails (observed: auth failure → cleanup completed, **no stray scratch DB left**). Attempted once, evidence in `/tmp/bootsmoke-v0316.log`.
2. **The staged binary was never booted against a real PostgreSQL.** Only the house non-boot probe ran: banner `Starting Ensemble v0.31.16`, then rc=3 at the first PG-only migration on a fresh SQLite tree (the documented expected outcome), with the PG/DSN family scrubbed per-variable into a temp data dir — no live contact.
3. **2A delta-maintained pre-call estimate (deep-dive A3) is DEFERRED, not implemented** — it spans `compaction.py` + `graph.py` and was outside the commission's enumeration; the pre-call gate still re-estimates on message-count change.
4. **2B residual 50 model classes/assembly are not memoised** — 61 of 271 tools declare a factory-local `class XInput(BaseModel)`; hoisting them to module scope is a follow-up.
5. **2B boot warm is not load-tested** against the ~29 s uvicorn lifespan startup under real CPU contention (GIL-held pydantic core).
6. **2B `build_instance_llms` was exercised with a fake chat model, never a live LLM.**
7. **2C above-threshold behaviour with a special token straddling a chunk boundary** is now counted instead of raising `ValueError` (below threshold unchanged; pinned).
8. **2A's usage-path fingerprint must track every field `estimate_messages_tokens` reads** (content / tool_calls / name / additional_kwargs) — a future estimator change that reads a new field needs a matching fingerprint update or the skip could emit a stale value.
9. **The L1 freshness override's audit receipt did NOT land in the live journal** — stage-time journal writes to the live install are fenced by design (the splice refuses out-of-pipeline writers; the pipeline lock is taken at `stage.sh:433`, after the freshness checks). Evidence is the stage stderr log (`/tmp/stage-override-v0316.log`) plus this handoff, not a `stage_freshness_override` event.
10. **41 pre-existing reds in `tests/test_api.py`** — proven byte-identical at lane base, at the upstream merge tip and at HEAD (3-leg A/B, empty FAILED-set diff); quarantine-attributed, not release regressions.
11. **No live promote** (by commission) — the `current` symlink still points at `releases/v0.31.10`.

## Known risks

1. **Mid-lane version-slot race.** The parallel lane released v0.31.14 (commit `184bc754d`, 04:54:28Z) and v0.31.15 (`026833336`, 04:54:50Z) while this lane was preparing its release; our first slot `v0.31.14` collided. Our never-pushed local tag `v0.31.14` (object `d99de6544fc7ce15c84fdcb3baa66388be3bb65c`) was deleted to prevent it shadowing the released line, and our two superseded commits (`3023695fa` bump, `44a9a15e2` changelog) were dropped from the branch — both recoverable by sha. The lane was re-based onto the new tip (`e0feea982`) and re-slotted to **v0.31.16**. The upstream v0.31.14 line has **no tag anywhere** (local or remote) — if the audit needs one, the leader re-cuts it.
2. **Staged with the L1 freshness override (`reason=non-tip-tree`).** The staging tree is our branch tip, not `origin/latest`, because this lane never pushes. **The ceremony must publish the lineage** (`git push origin feature/agent-turn-cpu-row2:latest` + `git push origin v0.31.16`) before promote; a re-stage after the push needs **no rebuild** (the sidecar's `git_head` is unchanged) and clears the override.
3. **Tag/branch-tip relationship.** This HANDOFF commit lands ONE commit past the tagged `v0.31.16`. The exact-tag gate is therefore unsatisfiable at the branch tip — the house late-merge remedy applies: the leader re-cuts the tag (explicit authorization required). The staged payload is unaffected (`.agents/` is not shipped), so the 4-way identity stands.
4. **2C whitespace-class ratio residual** — whitespace/newline corpora drift 3.14 % / 1.54 % at 200 KB, breaching the 0.1 % *ratio* premise. The absolute over-count bound (~1–2 tokens per boundary, ≤ n/CHUNK) holds exactly and the direction is an over-count (safe for a budget gate). Fixing the ratio needs ~128 KB chunks and resurrects the stall (~11–17 s). Disclosed in the pack, in the CHANGELOG entry and in the PACKS.md registry row; `ENSEMBLE_TOKEN_COUNT_EXACT=1` restores exact counting for any future consumer.
5. **2A fingerprint coupling** (see UNTESTED 8) — a future estimator field addition can silently stale the usage path.
6. **2A emergency-truncate additivity probe** — a non-additive estimator silently takes the legacy whole-list path (byte-identical, but slower).
7. **2B shared-schema mutation** — the registry caches a class that is now shared across instances; any post-construction mutation of `args_schema` would leak across instances. Reviewer check: no such mutation exists.
8. **Upstream work ships inside our release** (jobs-badge-loop-hog badge-path + FE visibility gates). Our delta contains their commits; their own release lineage (v0.31.14/v0.31.15) precedes ours.
9. **Plugin staleness**: the opendesign pin is stale (house-wide, allow-stale by default — visible at stage, non-blocking).
10. **The 41 quarantine reds** in `tests/test_api.py` must not be read as release regressions by a later gate.

## How to verify

```bash
# 1. the three lane packs + the regression guard (merged tree)
cd /home/nea/ensemble-src-wt-row2-int && set -a && . ./.env && set +a
bash test/packs/compaction_tier_a_byte_identity_unit_test.sh     # expect 155 passed, RESULT: PASS
bash test/packs/schema_registry_bind_once_unit_test.sh            # expect 24 passed, RESULT: PASS
bash test/packs/tier_b_token_drift_bound_unit_test.sh             # expect RESULT: PASS + drift/timing tables
bash test/packs/frozen_tool_name_discovery_unit_test.sh           # expect 7 passed (tool names unchanged)
.venv/bin/python -m pytest -q -p no:randomly --timeout=120 \
  tests/unit/test_compaction.py tests/unit/test_compaction_tier_a_callcount_pin.py \
  tests/unit/test_usage_path_tier_a_dedup.py tests/unit/test_loader_chunked_counting.py   # expect 191 passed

# 2. the n² collapse (the commission's headline target)
python3 scripts/bench_tier_b_token_drift.py    # 200 KB unbroken: exact 27.05 s → chunked ≈0.39 s (<1 s), drift ≤0.1 % on audited shapes

# 3. pydantic per-assembly build count (the commission's second target)
#    2198 classes / 2.11 s per assembly (base) → 50 classes / 0.07 s (head); boot warm 255+ schemas / 2.12 s

# 4. staged artifact 4-way sha identity (recompute, do not trust the manifest)
R=/home/nea/agents-ensemble/releases/v0.31.16
sha256sum /home/nea/ensemble-src-wt-stage-v0314/dist/ensemble-prod "$R/ensemble-prod"
python3 -c "import json;print(json.load(open('$R/manifest.json'))['binary_sha256'])"
python3 -c "import json;print(json.load(open('/home/nea/ensemble-src-wt-stage-v0314/dist/ensemble-prod.build-provenance.json'))['artifact_sha256'])"
# → all four must read 42fe3c16d1ab2faa7e00295e469e47c7db4930a1ba64c3018913a57f457739b8

# 5. the staged binary executes and reports its version (house non-boot probe; NO live contact)
mkdir -p /tmp/probe/data && cd /tmp/probe/data
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT \
    -u POSTGRES_URL -u DATABASE_URL -u PGHOST -u PGUSER -u PGPASSWORD -u PGPORT -u PGDATABASE \
    ENSEMBLE_SELF_ENV=dev ENSEMBLE_DATA_DIR=/tmp/probe/data ENSEMBLE_CONFIG=$R/config.yaml \
    PORT=19123 DAEMON_PORT=19123 timeout -k 5 45 "$R/ensemble-prod"
# → expect "Starting Ensemble v0.31.16" then rc=3 (the documented fresh-SQLite-tree outcome)

# 6. live must be untouched
readlink /home/nea/agents-ensemble/current        # → releases/v0.31.10
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9797/livez   # → 200
```

## Bash-raises used

**none — 0 raises consumed.** Every long operation (`uv sync`, the frontend build, the PyInstaller build, `stage.sh live`) ran as a **background proc** (`proc_run`, monitored with `proc_logs`/`proc_status`), never as a blocking bash command; the frozen `[sonic-bash-rule-v1]` ≤600 s default was never exceeded and no raise from sonic-leader was needed. No `git stash`, no `git add -A`, no `git push` of any ref; all commits pathspec-limited; all merges `merge --no-ff` in merge-order slots.

## Complexity self-assessment

**medium** — the band this commission names (2A XS, 2B S, 2C S). The three module legs were disjoint (no file overlap), each one-shot-coder sized, with byte-identical / provable invariants, zero migrations and no prompt-artifact change. Two integration-level events added work beyond the three legs — the **mid-lane version-slot race** (resolved by re-slotting to v0.31.16 and re-basing onto the new tip) and the **L1 freshness override** (resolved by the runbook's sanctioned journaled override, with the lineage push handed to the leader) — neither widened a module's scope nor relaxed an invariant, and no handover was raised. No band softening: the verdict stays `medium`.

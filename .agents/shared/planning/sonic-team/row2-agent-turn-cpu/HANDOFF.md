# HANDOFF — row2 agent-turn CPU lane (2A tier-A tiktoken de-dup / 2B pydantic schema registry / 2C shape-aware chunked counting) — FIX PASS

**Commission:** sonic-leader → sonic-developer (standard path). Off-loop audit deep-dive rows 2A+2B+2C; Emperor green-lit all three on Discord 2026-11-19. This document is the **fix pass** (sonic-reviewer integration verdict + Ari front-door ruling + sonic-leader measured acceptance criteria).
**Branch:** `feature/agent-turn-cpu-row2` · **lane base** `213ea6c1e` · **tip:** this document's own commit — the doc-only correction pass over `7f2fcc36a` (a file cannot contain its own commit sha; resolve with `git log -1 --format=%H -- .agents/shared/planning/sonic-team/row2-agent-turn-cpu/HANDOFF.md`, and the exact sha is in my report to sonic-leader) · **release tag** `v0.31.16` object `c7ef4d1f771ebaef11b669a0f32d8cd5e9987f4c` → peeled `95dfe51a5da6948d092c376796391583e0b62ba4` (LOCAL ONLY, never pushed)
**Staged release:** `/home/nea/agents-ensemble/releases/v0.31.16` (staged_at `2026-11-20T06:34:34Z`) — **NO flip performed**; live untouched at `v0.31.10` (livez=200, readyz=200, `current` symlink mtime Nov 19)
**Staged binary sha256 (4-way identical):** `fb96dc01551b43bf136c5f346b5912fe14b21b5ad99ddd0e294acfe42bc00f3f` — dist == release dir == manifest == provenance sidecar ✔
**Provenance:** sidecar `git_head = 95dfe51a5…` == the tagged commit, `git_dirty: false` ✔ (the artifact provably matches the tagged tree)
**Ruling 1 — Emperor, Discord 2026-11-19 (VERBATIM, binding):** "Counting only for reference low drift allowed"
**Ruling 2 — front door, Ari, 2026-11-20 (VERBATIM, binding):** "ADOPT the shape-aware cut as the 2C implementation — it preserves the Emperor's standing ruling (2026-11-19, verbatim: 'Counting only for reference low drift allowed') with ZERO need to widen the bound, since even the synthetic worst cases land at 0.000%."
Both rulings are also recorded verbatim in `.agents/shared/planning/sonic-team/row2-agent-turn-cpu/decisions-2c.md` and in the `## [0.31.16] — 2026-11-20` CHANGELOG block.

## Files touched across modules

**2A — tier-A byte-identical token de-dup** (`sonic/row2-cpu/2a-tier-a`, `ee66cc434`) + **FIX 2A** (`sonic/row2-cpu/fix2a-usage-fingerprint`, `f6a86de86`): `daemon/compaction.py` · `daemon/services/instance_messaging.py` · `tests/unit/test_compaction.py` · `tests/unit/test_compaction_tier_a_callcount_pin.py` (NEW) · `tests/unit/test_usage_path_tier_a_dedup.py` (NEW, +3 F3 pins) · `test/packs/compaction_tier_a_byte_identity_unit_test.sh` (NEW)

**2B — pydantic schema registry + bind-once + boot-warm** (`sonic/row2-cpu/2b-schema-registry`, `56b498788`, `8e35ac34c`, `8aca89c4e`) + **FIX 2B** (`sonic/row2-cpu/fix2b-comment-truth`, `b50c82eaf`, `3acb350d4`, `11f459acb`): `daemon/tools/_schema_registry.py` (NEW) · `daemon/graph.py` · `daemon/__main__.py` · `.agents/tester/PACKS.md` · `tests/unit/tools/test_schema_registry.py` (NEW) · `test/packs/schema_registry_bind_once_unit_test.sh` (NEW) · + 67 `daemon/tools/*.py` files (decorator import swap only)

**2C — tier-B policy-gated chunked counting** (`sonic/row2-cpu/2c-tier-b`, `76f0f410d`) + **FIX 2C** (`sonic/row2-cpu/fix2c-shape-aware-cut`, `190f7aa3f`): `daemon/loader.py` · `tests/unit/test_loader_chunked_counting.py` (NEW, 38 nodes) · `test/packs/tier_b_token_drift_bound_unit_test.sh` (NEW) · `scripts/bench_tier_b_token_drift.py` (NEW) · `.agents/shared/planning/sonic-team/row2-agent-turn-cpu/decisions-2c.md` (NEW)

**Release prep:** `pyproject.toml` · `daemon/__init__.py` · `tests/test_api.py` · `uv.lock` · `CHANGELOG.md` (+ this `HANDOFF.md`)
**NOT ours (disclosed):** merge `e0feea982` pulls 17 upstream commits from `origin/latest` `026833336` (the parallel jobs-badge-loop-hog lane) — they ship inside our release payload; no overlap with any lane source file.

## What changed

**2A (byte-identical counts).** `compact_state` measures the SELECTABLE pool ONCE (was up to 5×) and the PRESERVED pool once (was 2×); `_apply_ceiling_rule` is O(S) instead of O(S²) with the final `current_tokens` unchanged (hard-cap degrade branch included); `emergency_truncate`'s two whole-list re-count loops (halving `:1749`, pop `:1768` — O(H·N)+O(P·N)) became one running count O(N+H+P), with an additivity probe falling back to the legacy whole-list path (byte-identical) for non-additive estimators; the usage path runs a cheap fingerprint comparison BEFORE the whole-history count so an unchanged history skips it.
**FIX 2A (F3).** The fingerprint is now read BEFORE the count on the cache-miss path (`daemon/services/instance_messaging.py:1341-1349`) — previously the fingerprint was taken after the count, so a mutation in that window paired a NEW fingerprint with an OLD count and a later equal-fingerprint skip could serve that stale count. The emitted count is unchanged; 3 new pins (`TestF3FingerprintBeforeCount`) reddens when the fix is reverted.
**2B (identical schema semantics).** `daemon/tools/_schema_registry.py` memoises the pydantic `args_schema` per tool FUNCTION (`(module, qualname)`) and the OpenAI payload per tool identity; `build_instance_llms` derives the payload ONCE and hands the SAME dicts to every binding that runs — the vision branch (`graph.py:11054`) and the two standard binds (`:11072`, `:11073`); only the SCHEMA is cached, never the tool object; MCP tools are never memoised at all (dict schema ⇒ `_payload_key` → None ⇒ strictly stronger than a cache-key argument); `daemon/__main__.py` warms the registry on a background thread before the listener binds, against an inert manager, best-effort. **Measured — every figure below carries its counting scope and instrument, because unlike scopes must NOT be compared with each other:**
- *lane instrument, steady state, full assembly*: 2198 → 50 model classes per assembly, 2.11 s → 0.07 s (97.7 % reduction); cold first assembly 2442 → 1662 classes.
- *tester instrument (`/tmp/sonic-row2-measure-head2.json`, runtime surface `n_tools=199`)*: BASE `213ea6c1e` cold 882 classes / 2.4096 s with the payload re-derived 780 classes EVERY assembly; HEAD warm 50 classes / 0.0855 s with a second payload pass of 0 classes (the reviewer's independent capture reads 2.23 s cold — same direction, run-to-run variance).
- *boot warm*: lane dev box 255+ schemas / 2.12 s; tester census instrument (`/tmp/sonic-row2-census-head2.json`) 255 schemas / 1.5632 s (reviewer observed 1.56–1.66 s across runs), `registry_stats` {entries 265, schema_builds 265, payload_misses 0}.
- *tool census*: lane AST census of `@tool`-decorated functions in `daemon/tools/` → 271 → 271 IDENTICAL; tester AST `discover_all_tool_names()` → 268 @base/@HEAD (sha `e14fb210…`), registered metadata 269, runtime surface 199 (sha `cde4886b…`). Different discovery scopes; all agree on the DIRECTION (base == head ⇒ unchanged).
**FIX 2B (F2/F4/F5 — documentation-only).** The shipped text no longer claims a 3 × N_tools runtime multiplier (the vision bind and the standard-with-tools bind are mutually exclusive ⇒ at most 2 run); the boot warm is now described as ADDING to boot wall-clock under the GIL premise (not "overlapping" it); the MCP-dynamism rationale matches the shipped mechanism; the warmed schema count is a FLOOR, not a guarantee. **Verified independently by the integrator: the docstring-stripped AST of `daemon/graph.py`, `daemon/__main__.py` and `daemon/tools/_schema_registry.py` is byte-identical before/after — no executable line changed.**
**2C (reference-only counting, drift-bounded).** `estimate_tokens` counts in O(n) above a 16 KiB threshold by splitting at whitespace/newline boundaries into ≤ 4 KiB chunks; below the threshold the count is byte-identical for every caller. Knobs: `ENSEMBLE_TOKEN_COUNT_EXACT=1` (kill-switch → exact path everywhere), `ENSEMBLE_TOKEN_COUNT_THRESHOLD` (16384), `ENSEMBLE_TOKEN_COUNT_CHUNK` (4096).
**FIX 2C (shape-aware cut — F1).** The boundary char is still carried INTO THE NEXT CHUNK, EXCEPT when it TERMINATES a whitespace run of ≥ 16 IDENTICAL chars, in which case the cut is taken AFTER it (`_WHITESPACE_RUN_MIN = 16`; one bounded back-scan per cut; O(n) unchanged). Reason: cl100k merges up to 32 consecutive LFs and up to 128 spaces into one token, so the plain rule dropped one char from the window (4095, not 4096) and misaligned every boundary (~2 tokens/boundary). The newline-ALIGNED variant was measured and REJECTED (it regresses prose 0.000 % → 0.094 % and dense-JSON → 0.031 %).

**BEFORE → AFTER drift per shape (200 KB corpora, 4 KiB chunks, 16 KiB threshold; BEFORE = plain cut, AFTER = shape-aware; reproducible via the pack):**

| shape | exact | BEFORE | drift | AFTER | drift |
|---|---|---|---|---|---|
| lf_run (SYNTHETIC) | 6250 | 6348 | 1.5680 % | **6250** | **0.0000 %** |
| spaces_run (SYNTHETIC) | 1563 | 1612 | 3.1350 % | **1563** | **0.0000 %** |
| letters_run (SYNTHETIC) | 100000 | 100000 | 0.0000 % | 100000 | 0.0000 % |
| symbols_run (SYNTHETIC) | 25000 | 25000 | 0.0000 % | 25000 | 0.0000 % |
| lf_code (REALISTIC) | 68694 | 68695 | 0.0015 % | 68695 | 0.0015 % |
| crlf_code (REALISTIC) | 67008 | 67009 | 0.0015 % | 67009 | 0.0015 % |
| lone_cr_code (REALISTIC) | 73626 | 73627 | 0.0014 % | 73627 | 0.0014 % |
| markdown (REALISTIC) | 65069 | 65079 | 0.0154 % | 65079 | 0.0154 % |
| prose (REALISTIC) | 46512 | 46512 | 0.0000 % | 46512 | 0.0000 % |
| minified (REALISTIC) | 97955 | 97955 | 0.0000 % | 97955 | 0.0000 % |
| dense_json | 71322 | 71351 | 0.0407 % | 71351 | 0.0407 % |
| base64 | 21875 | 21875 | 0.0000 % | 21875 | 0.0000 % |
| digits | 66667 | 66699 | 0.0480 % | 66699 | 0.0480 % |

No shape is worse after the fix; the two breaching synthetic shapes land at 0.0000 %. **The earlier breach was a SYNTHETIC-CORPUS ARTIFACT** — realistic corpora already met the bound. **NO RULING EXTENSION REQUESTED.**
**Timing:** 200 KB unbroken corpus 27.05 s (exact, today) → **0.3247 s** chunked (GATE-D < 1 s). The self-gating benchmark `scripts/bench_tier_b_token_drift.py` exits **1 with 7 violations** against the pre-fix chunker and **0 with zero violations** against the shipped one.

## What is UNTESTED

1. **Packaged-artifact boot smoke — RAN GREEN (twice). The earlier claim in this file that it was "blocked" was FALSE and is corrected here.** The PRE-fix binary ran it green at 05:58Z (evidence `/tmp/sonic-row2-bootsmoke.log`) and the POST-fix binary `fb96dc01551b43bf136c5f346b5912fe14b21b5ad99ddd0e294acfe42bc00f3f` ran it green at 06:49Z (evidence `/tmp/row2-gate/g1_bootsmoke_preserve.log`): each in its OWN transient unit inside an isolated `app.slice` cgroup (`ensemble-boot-smoke-00346a` / `ensemble-boot-smoke-004e5b`), `readyz status=ready database=True checkpoint_saver=True`, `child_exit=0`, **no `--allow-shared-cgroup` waiver**, `XDG_RUNTIME_DIR=/run/user/1000` exported, `ensemble_dev_scratch` reused (no CREATEDB needed — `USE_FRESH=0`), no stray scratch DB left behind (verified 0). **Why MY earlier attempt failed (my mistake, not a structural blocker):** the user bus socket `/run/user/1000/bus` EXISTS — my shell had `XDG_RUNTIME_DIR` unset, so the script's isolation probe could not read the child's cgroup and refused with "isolation unproven". The live daemon's cgroup was never touched (each smoke ran in its own transient unit) and the live pointer was unchanged after the run (`live_current=releases/v0.31.10`, livez=200).
2. **The staged binary was never booted against a real PostgreSQL.** The house non-boot probe only: banner `Starting Ensemble v0.31.16`, then rc=3 at the first PG-only migration on a fresh SQLite tree (the documented expected outcome), with the PG/DSN family scrubbed per-variable into a temp data dir — no live contact.
3. **A MIRRORED staleness residual remains from the F3 fix**: the fingerprint is now read BEFORE the count, so the reviewer's window (a NEW fingerprint paired with an OLD count) is closed, but a mutation in the same no-await window can pair a PRE-mutation fingerprint with a POST-mutation count. Closing that too needs a second fingerprint read after the count (~3 lines) — a follow-up, not attempted because this pass is the lane's single re-dispatch.
4. **2A delta-maintained pre-call estimate (deep-dive A3) remains DEFERRED** — it spans `compaction.py` + `graph.py`; the pre-call gate still re-estimates on message-count change.
5. **2B residual 50 model classes/assembly are not memoised** (61 of 271 tools declare a factory-local `class XInput(BaseModel)`).
6. **2B boot warm is not load-tested** against the ~29 s uvicorn lifespan startup under real CPU contention (GIL-held pydantic core).
7. **2B `build_instance_llms` was exercised with a fake chat model, never a live LLM.**
8. **2C above-threshold behaviour with a special token straddling a chunk boundary** is counted rather than raising `ValueError` (below threshold unchanged; pinned).
9. **2A's usage-path fingerprint must track every field `estimate_messages_tokens` reads** (content / tool_calls / name / additional_kwargs) — a future estimator field addition needs a matching fingerprint update.
10. **41 pre-existing reds in `tests/test_api.py`** — byte-identical at lane base, at the upstream merge tip and at HEAD (3-leg A/B, empty FAILED-set diff); quarantine-attributed, not release regressions.
11. **No live promote** (by commission) — `current` still points at `releases/v0.31.10`.
12. **The override's audit receipt did not land in the live journal** — stage-time journal writes to the live install are fenced by design (the splice refuses out-of-pipeline writers; the pipeline lock is taken at `stage.sh:433`, after the freshness checks). Evidence: the stage stderr log + this handoff.

## Known risks

1. **Staged with the L1 freshness override (`reason=non-tip-tree`)** — the staging tree is our branch tip, not `origin/latest`, because this lane never pushes. **The ceremony must publish the lineage** (`git push origin feature/agent-turn-cpu-row2:latest` + `git push origin v0.31.16`) before promote. A re-stage AFTER the publish needs **no rebuild** (the sidecar's `git_head` is unchanged) and clears the override.
2. **Tag/branch-tip offset** — the tag sits at `95dfe51a5` (the tree the payload was built from) while the branch tip is this document's own commit; the commits past the tag are documentation-only (`.agents/**` + `CHANGELOG.md` — the PACKS registry rows, this HANDOFF, and the doc-only correction pass) and touch NOTHING in the shipped payload (agents/, config.yaml, the binary, frontend/, launcher.sh, plugins/, guidelines/, scripts/). Per the reviewer's binding judgement the tag is **not** re-cut: the 4-way identity stands and the sidecar `git_head` already equals the tagged commit, which is what the ceremony verifies. A re-stage would need a rebuild only if the tag were ever re-cut, because the sidecar's `git_head` would then no longer match.
3. **Two superseded tag objects remain dangling** in the object DB (recoverable by sha until gc): `2e77a8e749e0784c50df756dfb5e847b73133f69` → `d8a7f352e…` (the pre-fix release, leader-pinned) and `e1576818aead100dd35b27afce6a1936baf641e9` → `d22d3bc5a…` (an intermediate re-cut). The durable copy of both is this record.
4. **Version-slot history (from the first pass, unchanged):** the parallel lane released v0.31.14 (`184bc754d`) and v0.31.15 (`026833336`) mid-lane; our first slot collided, the never-pushed local `v0.31.14` tag was deleted so it cannot shadow the released line (object `d99de654…` pinned), and the lane was re-based onto the new tip (`e0feea982`) and re-slotted to v0.31.16. **The upstream v0.31.14 line has no tag anywhere** — if the audit needs one, the leader re-cuts it.
5. **Upstream work ships inside our release** (jobs-badge-loop-hog badge-path + FE visibility gates); our delta contains their commits.
6. **Plugin staleness**: the opendesign pin is stale (house-wide, allow-stale by default — visible at stage, non-blocking).
7. **The 41 quarantine reds** must not be read as release regressions by a later gate.
8. **2C drift is reference-only by ruling** — above the 16 KiB threshold the count is an over-count (≤ ~1 token/boundary, ≤ n/CHUNK). `ENSEMBLE_TOKEN_COUNT_EXACT=1` restores exact counting for any consumer that needs it.

## How to verify

```bash
# 1. the lane packs + the regression guard (merged tree @ ce2a55952)
cd /home/nea/ensemble-src-wt-row2-int && set -a && . ./.env && set +a
bash test/packs/compaction_tier_a_byte_identity_unit_test.sh     # expect 158 passed, RESULT: PASS
bash test/packs/tier_b_token_drift_bound_unit_test.sh             # expect RESULT: PASS (38 nodes + 13 shapes x 4 sizes)
bash test/packs/schema_registry_bind_once_unit_test.sh            # expect 24 passed, RESULT: PASS
bash test/packs/frozen_tool_name_discovery_unit_test.sh           # expect 7 passed (tool names unchanged)
.venv/bin/python -m pytest -q -p no:randomly --timeout=120 \
  tests/unit/test_compaction.py tests/unit/test_compaction_tier_a_callcount_pin.py \
  tests/unit/test_usage_path_tier_a_dedup.py tests/unit/test_loader_chunked_counting.py   # expect 196 passed

# 2. the self-gating drift benchmark (exit 0 = zero violations; MUST use the worktree venv)
.venv/bin/python scripts/bench_tier_b_token_drift.py    # exit 0; GATE-D 200 KB chunked 0.3247 s; lf_run/spaces_run 0.0000 %

# 3. the 2A byte-identity contract still holds against the shape-aware chunker (the cross-leg proof)
bash test/packs/compaction_tier_a_byte_identity_unit_test.sh     # 158/158 unchanged on the merged tree

# 4. staged artifact 4-way sha identity (recompute, do not trust the manifest)
R=/home/nea/agents-ensemble/releases/v0.31.16
sha256sum /home/nea/ensemble-src-wt-stage-v0314/dist/ensemble-prod "$R/ensemble-prod"
python3 -c "import json;print(json.load(open('$R/manifest.json'))['binary_sha256'])"
python3 -c "import json;print(json.load(open('/home/nea/ensemble-src-wt-stage-v0314/dist/ensemble-prod.build-provenance.json'))['artifact_sha256'])"
# → all four must read fb96dc01551b43bf136c5f346b5912fe14b21b5ad99ddd0e294acfe42bc00f3f
python3 -c "import json;s=json.load(open('/home/nea/ensemble-src-wt-stage-v0314/dist/ensemble-prod.build-provenance.json'));print(s['git_head'], s['git_dirty'])"
# → 95dfe51a5da6948d092c376796391583e0b62ba4 False  (== the tagged commit)

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

**none — 0 raises consumed, in both passes.** Every long operation (`uv sync`, the frontend builds, the PyInstaller builds, `stage.sh live`) ran as a **background proc** (`proc_run`, monitored with `proc_logs`/`proc_status`), never as a blocking bash command; the frozen `[sonic-bash-rule-v1]` ≤600 s default was never exceeded and no raise from sonic-leader was needed. No `git stash`, no `git add -A`, no `git push` of any ref; all commits pathspec-limited; all merges `merge --no-ff` in merge-order slots.

## Complexity self-assessment

**medium** — unchanged from the first pass and NOT softened. The three module legs were disjoint (no file overlap), one-shot-coder sized, with byte-identical / provable invariants, zero migrations and no prompt-artifact change. The fix pass was also medium: three disjoint legs (F3 in `instance_messaging.py`; the shape-aware cut + sanctioned pins + drift pack + decisions doc; documentation-only truth corrections), plus two surgical registry refreshes and one release-prep leg — all inside the same band. Two integration-level events remain: the **mid-lane version-slot race** (resolved by re-slotting to v0.31.16 and re-basing onto the new tip) and the **L1 freshness override** (resolved by the runbook's sanctioned journaled override, with the lineage publish handed to the leader). Neither widened a module's scope nor relaxed an invariant; no handover was raised.

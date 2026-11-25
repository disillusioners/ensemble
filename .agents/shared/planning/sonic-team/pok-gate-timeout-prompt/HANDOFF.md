# HANDOFF — pok-gate-timeout-prompt

**Commission:** agents-ensemble, sonic STANDARD path — the Emperor's revised ruling of 2026-11-25: the
POK write gate's judge timeout budget (45s -> 300s, configurable through a named seam) and the judge
prompt restructured from ONE merged user message into a SYSTEM message (role + instructions) plus a
USER message (the per-write payload), with a jargon scrub and the "(unnamed)" root cause fixed.
**Staged version:** v0.31.55 (STAGED ONLY — NO promote; the ceremony is the Emperor's).
**Lane base:** `35ec50e3c9b3fc9a487e4f2dbad23c15a0837f45` (= `origin/latest` = tag `v0.31.54` at lane
open; re-fetched before the push and UNMOVED — no re-merge was owed, no force-push question arose).
**Merged content tip:** `351764924` (4 merges — slot 1 timeout, slot 2 prompt, slot 2b registry
correction, slot 3 version bump; this HANDOFF is the next commit on top).
**Diff range:** `35ec50e3c..351764924` — 10 files, +1057 / -199.
**Complexity verdict:** medium (assessed pre-dispatch; see the last section).

---

## Files touched across modules

**Integration (sonic-developer)** — the four `merge --no-ff` merges, the registry correction, this file:
- `.agents/shared/planning/sonic-team/pok-gate-timeout-prompt/HANDOFF.md` (this file)

**Module A — `pok-gate-timeout`** (branch `sonic/pok-gate-timeout` @ `d994471b7` = `951d50aa9` + the
integrator bounce commit) — merge slot 1:
- `daemon/services/pok_gate_timeout_resolver.py` (the knob seam; the new wall-clock env knob)
- `daemon/services/pok_gate.py` (DOCSTRING ONLY — the `_call_judge_with_retry` timeout docstring)
- `tests/unit/services/test_pok_gate_unit.py` (the TIMEOUT region only — `class TestTimeoutResolver`)

**Module B — `pok-gate-prompt`** (branch `sonic/pok-gate-prompt` @ `898bc8fe1` = `792808052` + the
revision `52e46504f` + the registry correction `898bc8fe1`) — merge slot 2 (and slot 2b):
- `daemon/services/pok_gate.py` (the PROMPT region: persona, prompt carrier, `build_judge_prompt`,
  the `_house_llm_call` message list, the digest header/boundary clause)
- `daemon/tools/pok.py` (the project-name lookup ONLY)
- `tests/unit/services/test_pok_gate_unit.py` (the DIGEST + PROMPT regions only)
- `.agents/tester/PACKS.md` (the `pok_write_gate_w2_unit_test` row only)

**Module C — `pok-gate-release-bump`** (branch `sonic/pok-gate-release-bump` @ `0f77cc5971`) — merge slot 3:
- `pyproject.toml`, `daemon/__init__.py`, `tests/test_api.py`, `uv.lock`, `CHANGELOG.md`

**Shared-file note (integration seam):** `daemon/services/pok_gate.py` and
`tests/unit/services/test_pok_gate_unit.py` were each owned by TWO modules. The ownership was
region-fenced (A: the timeout docstring / the `TestTimeoutResolver` class; B: the prompt region / the
`TestDigest` + `TestPromptClauses` classes) with explicit merge-fence anchors, and the merge was
PRE-VERIFIED conflict-free with `git merge-tree --write-tree` (rc=0) BEFORE any merge ran. Both merges
auto-merged with zero conflicts; no conflict was hand-resolved by the integrator.

---

## What changed

### 1. The judge budget: 45s -> 300s, through a named seam

The seam is the EXISTING resolver `daemon/services/pok_gate_timeout_resolver.py` (Pattern C, the house
precedent: module env resolver + cached global + one-shot WARN per bucket + a test-only cache reset).
The TOTAL was previously a bare CONSTANT and is now env-tunable.

| Knob | Old | New | Where |
|---|---|---|---|
| `DEFAULT_GATE_TIMEOUT_S` (per attempt) | `20.0` | `300.0` | resolver:124 |
| `MAX_GATE_TIMEOUT_S` (per-attempt max clamp) | `180.0` | `360.0` | resolver:137 |
| `MIN_GATE_TIMEOUT_S` (per-attempt min clamp) | `10.0` | `10.0` (unchanged) | resolver:129 |
| `GATE_WALL_CLOCK_CAP_S` / `DEFAULT_GATE_WALL_CLOCK_S` (TOTAL) | `45.0` | `300.0` | resolver:143,149 |
| `MIN_GATE_WALL_CLOCK_S` (new) | — | `30.0` | resolver:155 |
| `MAX_GATE_WALL_CLOCK_S` (new) | `180.0` equivalent, unattainable | `1800.0` | resolver:162 |
| `GATE_MAX_RETRIES` | `1` | `1` (UNCHANGED — a budget ruling, not a retry ruling) | resolver:169 |
| `POK_GATE_TIMEOUT_S_ENV` | `ENSEMBLE_POK_GATE_TIMEOUT_S` | unchanged | resolver:112 |
| `POK_GATE_WALL_CLOCK_S_ENV` (NEW) | — | `ENSEMBLE_POK_GATE_WALL_CLOCK_S` | resolver:113 |

The per-attempt value was raised to the TOTAL so a single judge call may use the whole budget: the
consumer computes `budget = min(attempt_timeout, remaining)` with `remaining = wall - elapsed`, so the
per-attempt knob can no longer silently truncate the ruling. The per-attempt MAX clamp had to move
180 -> 360: left at 180 it would have silently clamped the new 300s default down to 180 and defeated
the ruling. The house failover facade's OWN default (`invoke_raw_with_failover(wall_clock_cap_s=45.0)`)
is never used by the gate — the gate passes its cap explicitly — so it does not truncate the budget.

### 2. The tool-execution ceiling — MEASURED, and already coherent (no ceiling change)

There is **no per-native-tool execution timeout anywhere in the daemon**. `tool_call_timeout`
(`daemon/config.py:5338`, default 120s) is an **MCP-pool** setting (STDIO/SSE/HTTP transports) and
does NOT apply to native Python tools such as `pok_upsert`. The only ceiling above a native tool call
is the whole-turn hard cap `graph_timeout_minutes` (`daemon/config.py:3251`, default 120.0 min = 7200s),
enforced by `MainLoopBridge.run_async(_run(), timeout=...)`. The per-worker heartbeat is a separate
daemon THREAD (`daemon/services/worker_pool.py` `TaskHeartbeat`, `task_heartbeat_interval_seconds=30`)
and a sync tool runs in langchain's `run_in_executor` (`langchain_core/tools/base.py:801`), so a long
tool call neither starves the event loop nor the heartbeat. 300s is therefore already coherent with
every ceiling (300 << 7200), and **no ceiling was raised and no tool was exempted** — instead a pin
locks the invariant (`the resolved wall cap fits inside the turn budget`).

### 3. The lock / transaction audit — PROVED never held

Chain: `pok_upsert` (`daemon/tools/pok.py:1317`) -> `POKService.upsert` -> `_consult_gate` ->
`judge_pok_write` -> `_call_judge_with_retry` -> `_house_llm_call` -> LLM. Evidence: **zero**
`Lock`/`RLock`/`acquire`/`release` in `daemon/tools/pok.py`, `daemon/services/pok_service.py`,
`daemon/services/pok_gate.py` or `daemon/repositories/pok/*`; the repository is strictly
**session-per-call** (every method opens and closes its own `with Session(self._engine) as session:`
— the reads that precede the gate, `resolve_slug_row` (repository.py:356), `list_entries` (398) and
`count_active` (411), have all closed their session before the gate is called); `VerdictCache` is a
plain `OrderedDict` with no lock; the gate receives the digest as an in-memory list and performs NO
database access during the judge call. **No lock or transaction is held across the LLM call, so no
release/re-acquire was needed.** Module A made the proof durable as a pin that spies
`sqlmodel.Session.__init__` mid-call and scans the judge path for lock-shaped objects, with deliberate
violations demonstrated RED in-node.

### 4. The prompt: MERGED -> SYSTEM + USER

BEFORE, `build_judge_prompt` returned ONE string and `_house_llm_call` sent
`messages=[{"role":"user","content":prompt}]`; the persona, every rule, the outcome set and the
per-write payload were all concatenated into that single user message, and the project name was
interpolated as `project_name or "(unnamed)"`.

AFTER, a frozen carrier `JudgePrompt(system, user)` holds two halves; the house call sends
`messages=[{"role":"system",...},{"role":"user",...}]`. The `llm_call=` injection seam stays
ONE-argument (all 20 existing call sites only COUNT calls, so none had to change).

The SYSTEM message (verbatim, rendered):

```
You are the write gate for the Project Operating Knowledge (POK) store of my-project. Project Operating Knowledge (POK) is the store of durable operational how-to knowledge (DevOps-kind rules, traps and conventions) for THIS project only, distinct from the general knowledge base (RAG, retrieval-augmented generation — free-form reference material).

GLOSSARY (terms used below): slug = the short identifier (kebab-case name) of a knowledge entry; body = its one-paragraph content; row = one entry already in the store; digest = the bounded list of existing entries supplied with this write; verdict = the outcome you return (ACCEPT / MERGE / ASK / REJECT).

RULES:
R0 (format): ... [R0-R5 + R-RET/R-PROV, VERBATIM — the ruleset text is unchanged]
JUDGE QUESTION: What breaks in THIS repo if we drop this?
OUTCOMES: ACCEPT / MERGE / ASK / REJECT (what each means)
REJECTION EXAMPLES (every rejection names its alternative home): [the five family exemplars]
ANSWER WITH EXACTLY ONE JSON OBJECT AND NOTHING ELSE: {"verdict": ..., "family": ..., ...}
```

The USER message (verbatim, rendered):

```
CANDIDATE SLUG: my-rule
CANDIDATE BODY: the body text
CALLER'S OPERATIONAL CLAIM (the primary judge input): why it matters

PROJECT OPERATING KNOWLEDGE (POK) ACTIVE-ROW DIGEST (project-scoped, bounded)
PROJECT: my-project
Boundary (R4): redundancy means INTRA-STORE duplication only — an ACTIVE row in this Project Operating Knowledge (POK) store already carrying the fact. A twin in the general knowledge base is explicitly NOT a rejection basis; judge the BOUNDARY (operational how-to vs general knowledge), never store exclusivity.
ACTIVE ROWS (slug: body):
```

When the project name is genuinely unavailable, the prompt says so HONESTLY — "the project this write
targets (its name was not available to this call)" — and never prints a placeholder that reads like a
value. The literal `(unnamed)` no longer appears anywhere.

### 5. The "(unnamed)" ROOT CAUSE (it was neither interpolation nor an unscoped caller)

`build_judge_prompt` interpolated correctly; the CALLER never had a name to interpolate.
`daemon/tools/pok.py:_project_name` read `getattr(instance_repository, "_project_repository", None)` —
but `_project_repository` lives on the **Manager** (`daemon/manager.py:2214`), never on
`SQLModelInstanceRepository(engine)`. The lookup therefore returned `None` in production and the prompt
ALWAYS said "(unnamed)". The fix resolves the name from the project repository that actually holds it
(the manager's), keeps the degraded read (None on failure), and falls back to an honest statement.

### 6. The jargon scrub

No bare "POK" appears anywhere in either message; every term of art that appears (POK, RAG, slug, body,
row, digest, verdict) is defined on first use — the Emperor's 2026-11-25 ruling that a competent agent
with zero prior exposure to the house vocabulary must understand, from the prompt alone, what store it
is gating, what belongs in it and what does not. The digest header and its boundary clause (the two
bare-"POK" sites in the USER payload) were reworded; the digest's SEMANTICS (the <=8000-char ceiling
binds, K=48 observed, the boundary-not-exclusivity clause is never dropped, the zero-overlap fallback
stays same-family-first) are untouched.

### 7. The release bump (Module C)

`pyproject.toml`, `daemon/__init__.py`, `tests/test_api.py` (the version pin) and `uv.lock` (ONE line)
move `0.31.54` -> `0.31.55`, plus a `## [v0.31.55] — 2026-11-25` CHANGELOG section. Without this the
staged binary would report 0.31.54 under a v0.31.55 tag and the promote-time version-verify would fail.

---

## What is UNTESTED

- **The live LLM path.** Every test reaches the judge through a fake: the timeout pins drive a fake
  clock (`daemon.services.pok_gate.time.monotonic`) plus a fake house client
  (`daemon.services.pok_gate._house_llm_call`); the prompt pins assert the `_house_llm_call` message
  list. The REAL chain — the OpenAI client and `invoke_raw_with_failover` — was never exercised against
  a provider. No live-LLM run proves the 300s budget against a real HTTP call.
- **The system-role behaviour of the real provider.** The split is proven at the message-list boundary
  only. The house provider is OpenAI-compatible, but nothing here proves the proxy honours a `system`
  role; a provider that ignores it would show the judge only the USER half.
- **The live project-name wiring.** The `_project_name` pin uses a stub manager with a stub project
  repository; the PRODUCTION wiring (the manager's `_project_repository`) was verified by reading, not
  by a live instance — so the end-to-end "the prompt carries the real project name" claim is untested
  against a running daemon.
- **The timeout REASON string on a real timeout.** `openai.APITimeoutError` does NOT subclass
  `TimeoutError`, so a real mid-call timeout lands on the `llm_error` leg, not the `timeout` leg.
  Pre-existing and deliberately NOT changed (a behaviour change to the gate's brain is outside this
  commission); the timeout pin exercises the gate's canonical `TimeoutError` contract only.
- **`tests/test_api.py` beyond the version pin.** The file carries 9 PRE-EXISTING failures (base
  identical: mock call-signature, await-Mock, `db_offload.py:188` "different loop"); only the version
  pin (`test_health_check`) was run.
- **The promote-time gates.** The release was STAGED, not promoted: version-verify, livez, readyz and
  the 300s soak were NOT exercised (the Emperor's ceremony owns them). The stage's L1 tip-identity
  guard and L2 provenance re-hash DID run (that is what the stage is).
- **The whole-tree suite.** Only the three registered POK packs plus the version pin were run; the
  wider suite was not re-run (no other pack covers the touched files).

---

## Known risks

- **The 300s budget has NO live effect today.** The semantic judge and the soft-max tightening remain
  OFF (the fence); in SHADOW an unavailable judge is advisory and the write proceeds. The budget only
  binds once `pok_gate.semantic_enforcement` is flipped ON — by the Emperor, not by this lane.
- **The R4 rule clause still carries two bare forms.** "already-in-RAG" and "A RAG twin" remain in the
  VERBATIM `R1_BEATS_R4_CLAUSE` house clause, which the fence forbids rewording; the jargon pin
  allows exactly those two and forbids bare "POK"/"rag tool" everywhere else. A reviewer who reads the
  Emperor's headline rule ("never use the bare abbreviation") without the pin's carve-out will see
  those two as violations.
- **The prompt no longer carries the Emperor's LITERAL trace wording.** The exemplars render an
  expanded, grammatical paraphrase of `EMPEROR_TRACE_PHRASE` (the canonical artifact itself is
  byte-identical, as is `FAMILY_TEMPLATES` and the pin at `tests/unit/services/test_pok_gate_unit.py:201`).
  The judge's rejection voice is therefore slightly different from the house's quoted phrase.
- **Clamp asymmetry.** The per-attempt max clamp (360s) is BELOW the wall max clamp (1800s), so an
  operator who raises the wall cap cannot push a single attempt past 360s. Deliberate and stated in
  the resolver docstring.
- **Plan-set drift (not edited).** `.agents/shared/planning/pok-write-gate/` still documents the
  D1-overturned 20s/45s knobs. It is the historical plan of record and this commission is scoped to a
  prompt-text change plus its pins, so it was left alone.
- **Registry drift I did NOT re-annotate.** The `pok_write_gate_w1_unit_test` row still says 172 while
  the measured value is 176 — but the BASE measures 176 too, so the drift is PRE-EXISTING and
  attributable to another lane, not to this one. The pack is green at 176/176 and was re-run under the
  house fence probe (every `POSTGRES_*` pointed at dead port `:1` + `ensemble_prod`): 176 passed, no
  live-store contact.
- **The merge seam.** Two modules shared `pok_gate.py` and the gate's unit file. The merge was clean
  (`merge-tree` rc=0, then two `--no-ff` merges with zero conflicts) and every conflict-free merge
  still needs the post-merge gate re-run — which was done (275/176/270 green on the merged tree).
- **The staged release is not promoted.** `current` still points at the previous release; the flip is
  the Emperor's ceremony.

---

## How to verify

Run on the MERGED tree (`git checkout 351764924` or the tag `v0.31.55`):

```bash
# 1. The three registered packs (the shared registry — .agents/tester/PACKS.md)
PY=/home/nea/ensemble-src/.venv/bin/python
SCRUB="env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL -u ENSEMBLE_DB_DSN -u PORT -u DAEMON_PORT ENSEMBLE_SELF_ENV=dev ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 PYTHONDONTWRITEBYTECODE=1"
# family-key -> 275 passed
$SCRUB $PY -m pytest -p no:randomly --tb=short -q -rfE tests/unit/services/test_pok_service_unit.py tests/unit/services/test_pok_family_strict_unit.py tests/unit/services/test_pok_injection_unit.py tests/unit/services/test_pok_metadata_cap_unit.py tests/unit/services/test_pok_trust_tier_unit.py tests/unit/services/test_pok_ari_seam_unit.py tests/unit/tools/test_pok_tools_unit.py
# W1 -> 176 passed
$SCRUB $PY -m pytest tests/unit/repositories/test_pok_write_gate_ledger_unit.py tests/unit/repositories/test_pok_write_gate_phase2_unit.py tests/unit/repositories/test_pok_standing_defect_rows_unit.py tests/unit/services/test_pok_injection_retrieval_ledger_unit.py tests/unit/services/test_pok_trust_tier_unit.py tests/unit/tools/test_pok_curate_basis_unit.py tests/unit/tools/test_pok_tools_unit.py tests/unit/test_pok_grant_pins.py -p no:randomly -q --tb=short -rfE --timeout=120
# W2 -> 270 passed (the gate's own file: 67 -> 82 nodes)
$SCRUB $PY -m pytest tests/unit/services/test_pok_gate_unit.py tests/unit/tools/test_pok_gate_tool_unit.py tests/unit/repositories/test_pok_write_gate_ledger_unit.py tests/unit/tools/test_pok_tools_unit.py tests/unit/services/test_pok_librarian_unit.py tests/unit/tools/test_pok_census_route_unit.py tests/unit/services/test_pok_shadow_mix_gate_unit.py -p no:randomly -q --tb=short -rfE --timeout=180

# 2. The version pin -> 1 passed
$SCRUB $PY -m pytest tests/test_api.py -p no:randomly -q --tb=short -k health_check

# 3. The budget, read from the seam (not from a constant in a test)
grep -nE 'DEFAULT_GATE_TIMEOUT_S|MAX_GATE_TIMEOUT_S|GATE_WALL_CLOCK_CAP_S|DEFAULT_GATE_WALL_CLOCK_S|MIN_GATE_WALL_CLOCK_S|MAX_GATE_WALL_CLOCK_S|GATE_MAX_RETRIES' daemon/services/pok_gate_timeout_resolver.py
# -> 300.0 / 360.0 / 300.0 / 300.0 / 30.0 / 1800.0 / 1

# 4. The prompt really splits, and the jargon really holds (render it)
python - <<'PY'
from daemon.services.pok_gate import build_judge_prompt
p = build_judge_prompt(project_name="my-project", slug="s", body="b", reason="r", digest="D")
assert "(unnamed)" not in p.system + p.user
print(p.system); print("----"); print(p.user)
PY
```

## Bash-raises used

none — no bash command exceeded the 600 s default. The three long operations (the offline
`uv sync`, the PyInstaller binary build, and the frontend build + stage) ran as background `proc`
processes, not as raised bash calls.

## Complexity self-assessment

**medium** — assessed pre-dispatch, and NOT softened afterwards (no raised handover, so no override
attribution applies). The commission touched the timeout seam, the prompt, the project-name lookup and
the release bump across 10 files and 3 modules, but the gate's brain (R0-R5 + R-RET/R-PROV), the
verdict/family vocabularies, the parser, the cache, the ASK contract, the shadow posture, the soft-max
and the authority split were all untouched; no schema change, no queue, no new agent, no settings
flip. The two load-bearing verifications the commission demanded — the tool-execution ceiling and the
lock/transaction scope — were MEASURED by the integrator before dispatch (there is no per-native-tool
timeout at all, and no lock or transaction is held across the judge call), which is what kept each
module inside one coder turn. Both module-level revision bounces were documentation/registry
corrections, not design reversals.

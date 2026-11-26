# HANDOFF — pok-gate-timeout-prompt (wave 2 — the fix wave)

**Commission:** the ensemble daemon project, sonic STANDARD path — the Emperor's revised ruling of 2026-11-25
(the POK write gate's judge budget 45s -> 300s + the judge prompt restructured into SYSTEM + USER with a
jargon scrub and the "(unnamed)" root cause fixed), FOLLOWED BY the single re-dispatch wave after REVIEW:
NEEDS-REVISION + TEST: PASS (B1–B4 binding, A1–A3 advisory).
**Staged version:** v0.31.56 (STAGED ONLY — NO promote; the ceremony is the Emperor's). **v0.31.55 stays as
the pushed + staged SUPERSEDED INTERMEDIATE** — its tag and release dir are NEVER re-cut; retention keep-3
owns eviction.
**Wave-1 base:** `35ec50e3c…` → v0.31.55 (tip `dd007f16d…`).
**Wave-2 base:** `dd007f16d03a1bfa5d399940e438f09a72cdb775` (= `v0.31.55^{}` = `origin/latest` at wave open).
**Merged content tip:** `ad3de307e` (4 merges — slot 1 ceiling, slot 2 evidence, slot 3 release bump, slot 2b
registry count; this HANDOFF is the next commit on top).
**Diff range:** `v0.31.55^{}..ad3de307e` — 13 files, +653 / −23.
**Complexity verdict:** medium (assessed pre-dispatch; NOT softened; no handover raised).

---

## Files touched across modules

**Integration (sonic-developer)** — the four `merge --no-ff` merges, the registry re-check, this file:
- `.agents/shared/planning/sonic-team/pok-gate-timeout-prompt/HANDOFF.md` (this file)

**Module A — `pokgt-fix-ceiling`** (branch `sonic/pokgt-fix-ceiling` @ `b3499de81`, base `dd007f16d`) — merge slot 1:
- `daemon/services/pok_gate.py` — ONLY the judge-client + retry region: the `_house_llm_call` factory
  (the live-deadline re-derivation + the fail-closed abort), the `_call_judge_with_retry` docstring + call,
  and the `judge_pok_write` seam annotation
- `tests/unit/services/test_pok_gate_unit.py` — the TIMEOUT region only (the new
  `class TestGateWallBoundsTheWholeCycle`, immediately after `TestTimeoutResolver`)

**Module B — `pokgt-fix-evidence`** (branch `sonic/pokgt-fix-evidence` @ `ec6464b9b` + `9ab451a0c`) — merge slots 2 + 2b:
- `daemon/services/pok_gate.py` — ONLY the PROMPT region: the digest header + boundary clause (~625) and
  the glossary / definitions (~781)
- `tests/unit/services/test_pok_gate_unit.py` — the DIGEST + PROMPT regions only (the new
  `class TestRealDigestHeader`)
- `.agents/tester/PACKS.md` — the `pok_write_gate_w2_unit_test` row (knob text + the leg records + the node
  count) and the `pok_write_gate_w1_unit_test` row (the node count)
- `scripts/pok_gate_prompt_mutations.py` (NEW — the committed mutation-leg harness)
- `.agents/shared/planning/pok-write-gate/evidence-pok-gate-fix/mutation-{split-shape,jargon,digest-header,placeholder}.md` (NEW)

**Module C — `pokgt-fix-release`** (branch `sonic/pokgt-fix-release` @ `027e60d88`, base `dd007f16d`) — merge slot 3:
- `pyproject.toml`, `daemon/__init__.py`, `tests/test_api.py`, `uv.lock` (the 0.31.55 -> 0.31.56 bump) + `CHANGELOG.md`

**Shared-file note (integration seam):** `daemon/services/pok_gate.py` and the gate's unit file were each
owned by TWO modules. Ownership was region-fenced (A: the judge-client/retry region + the timeout test
region; B: the prompt/digest region + the digest/prompt test regions) with explicit merge-fence anchors, and
the merges were PRE-VERIFIED conflict-free with `git merge-tree --write-tree` (rc=0) before any merge ran.
All four merges auto-merged with ZERO conflicts; no conflict was hand-resolved by the integrator.

---

## What changed

### 1. B1 — the 300s TOTAL now bounds the WHOLE judge cycle (the binding fix)

**The defect (wave-1 code):** the gate computed `deadline = monotonic() + wall_cap` and, per gate-level
attempt, `budget = min(attempt_timeout, remaining)`, then handed `_house_llm_call` a PRE-COMPUTED DURATION.
The house factory built `openai.OpenAI(timeout=float(timeout_s))` — the SAME value on EVERY facade attempt
(captured at client-build time) — while the failover facade evaluates its own cap ONLY BETWEEN attempts
(`llm_failover.py:845-856`). A facade attempt starting just before expiry therefore ran under the FULL 300s
client timeout with nothing left to kill it: the effective worst case was **wall + one attempt (~600s)**, not
the Emperor's 5 minutes. The facade's own docstring assigns this duty to the factory — *"that's the
factory's responsibility, not the facade's"*.

**The fix:** the deadline now reaches the house factory as an **ABSOLUTE monotonic instant** (`deadline=`
keyword), and `_factory` RE-DERIVES the per-request timeout on **every** entry:
`remaining = request_deadline - time.monotonic()`; `request_timeout = min(timeout_s, remaining)`; and when
`remaining <= 0` the request is **ABORTED** with the existing fail-closed `POKGateUnavailable("timeout")` —
never a client built with a non-positive timeout. The abort is non-retryable for the facade, so the cycle
ends AT the wall instead of burning another retry + backoff. The gate-level budget, the retry budget
(`GATE_MAX_RETRIES=1`) and the wall cap (`GATE_WALL_CLOCK_CAP_S`, 300s default) are UNCHANGED. The softer
option ("restate the bound as wall + one attempt") was ruled out: a bound that is 10 minutes because of an
accounting detail is the same class as a label that lies about its own source.

### 2. The knobs (UNCHANGED this wave; the ruling they implement)

`DEFAULT_GATE_TIMEOUT_S` 300.0 (per attempt) · `MAX_GATE_TIMEOUT_S` 360.0 · `MIN_GATE_TIMEOUT_S` 10.0 ·
`GATE_WALL_CLOCK_CAP_S` / `DEFAULT_GATE_WALL_CLOCK_S` 300.0 (TOTAL) · `MIN_GATE_WALL_CLOCK_S` 30.0 ·
`MAX_GATE_WALL_CLOCK_S` 1800.0 · `GATE_MAX_RETRIES` 1 · `ENSEMBLE_POK_GATE_TIMEOUT_S` (per attempt) +
`ENSEMBLE_POK_GATE_WALL_CLOCK_S` (total, NEW). **ONE FLAT BUDGET**: a single 300s budget for the gate call
REGARDLESS of enforcement mode; a mode-scaled budget is ruled OUT and no mode-dependent knob was
introduced.

### 3. The tool-execution ceiling — MEASURED, and the measurement HOLDS

There is **no per-native-tool execution timeout** in the daemon. `tool_call_timeout`
(`daemon/config.py:5338`, 120s) is an **MCP-pool** setting (STDIO/SSE/HTTP) and does not touch native tools;
native sync tools run untimed via `run_in_executor(None, …)` (`langchain_core/tools/base.py:801`). The only
ceiling above a native tool call is the whole-turn cap `graph_timeout_minutes` (`config.py:3251`,
120.0 min = 7200s, via `MainLoopBridge.run_async`). 300s << 7200s ⇒ coherent; **no ceiling was raised and no
tool was exempted** — a pin locks the invariant.

### 4. The lock / transaction audit — PROVED NEVER HELD (re-confirmed, unchanged)

Zero `Lock`/`RLock`/`acquire` across `daemon/tools/pok.py`, `daemon/services/pok_service.py`,
`daemon/services/pok_gate.py`, `daemon/repositories/pok/*`; the repository is strictly **session-per-call**
(each method opens and closes its own `with Session(engine)`), so the reads preceding the gate have closed
before it runs; the gate slot sits after `resolve_slug_row` + `stamp_last_verified` and before `_supersede`
(whose FOR-UPDATE read opens its own session); the gate does no DB access during the judge call;
`VerdictCache` is a plain `OrderedDict`. **No lock or transaction is held across the LLM call**, so nothing
had to be released or re-acquired. (A pin spies `sqlmodel.Session.__init__` mid-call and scans the judge
path for lock-shaped objects, with deliberate violations proven RED in-node.)

### 5. The prompt (before / after) — the split stands; wave 2 added definitions only

**BEFORE (v0.31.54):** ONE merged user string — persona "You are the POK write gate for project {name}. POK is
durable operational how-to (DevOps kind)…; RAG is the knowledge store." + CANDIDATE SLUG/BODY/CLAIM + digest +
"RULES:" R0-R5/R-RET/R-PROV + JUDGE QUESTION + the outcome prose + the confidence rule + the JSON contract,
sent as `messages=[{"role":"user",…}]`, name interpolated `project_name or "(unnamed)"`.
**AFTER:** a frozen `JudgePrompt(system, user)`; the house call sends
`messages=[{"role":"system",…},{"role":"user",…}]`.
SYSTEM opens: `You are the write gate for the Project Operating Knowledge (POK) store of my-project. Project
Operating Knowledge (POK) is the store of durable operational how-to knowledge (DevOps-kind rules, traps and
conventions) for THIS project only, distinct from the general knowledge base (RAG, retrieval-augmented
generation — free-form reference material).` + a GLOSSARY + the ruleset VERBATIM + JUDGE QUESTION + OUTCOMES +
REJECTION EXAMPLES + the JSON contract.
USER: `CANDIDATE SLUG: my-rule` / `CANDIDATE BODY: the body text` / `CALLER'S OPERATIONAL CLAIM (the primary
judge input): why it matters` / `PROJECT OPERATING KNOWLEDGE (POK) ACTIVE-ROW DIGEST (project-scoped,
bounded)` / `PROJECT: my-project` / the boundary clause / `ACTIVE ROWS (slug: body):`.
Wave 2 added the last undefined terms on first use — **ACTIVE** (the row status), **INTRA-STORE**, **KV /
board** — while the **frozen** `R1_QUESTION` wording (which contains "ensemble") was left untouched, as
instructed. "(unnamed)" remains absent; an unavailable name reads "the project this write targets (its name
was not available to this call)".

### 6. B2 / B3 / B4 / A3

- **B2** — the mutation legs are now COMMITTED and RUNNABLE: `scripts/pok_gate_prompt_mutations.py`
  (copy -> mutate -> RED -> sha256-verified restore -> GREEN; NEVER `git stash`) plus four evidence files
  under `.agents/shared/planning/pok-write-gate/evidence-pok-gate-fix/`. **The integrator ran the harness
  independently: 4/4 legs RED (rc=1) -> restore sha256 identical -> re-GREEN (rc=0), worktree clean after.**
- **B3** — the registry now matches the measurement: the W2 row's knob text reads "300s/attempt, ≤1 retry,
  300s wall (env-tunable via `ENSEMBLE_POK_GATE_WALL_CLOCK_S`, clamps 30..1800), min 10 / max 360", the W1 row
  reads **176 nodes / PASS 176/176**, and the W2 row declares **273 nodes (7 files) / gate-service 85** with
  the four legs recorded.
- **B4** — the REAL digest header is now pinned: `TestRealDigestHeader` calls the real `build_active_digest`
  and asserts the expansion form (never a bare "POK") plus a bare-abbreviation-free boundary clause. (Before
  it, the jargon pin injected a HAND-BUILT digest and the real header was unpinned.)
- **A3** — the seam contract is no longer silent: `judge_pok_write.llm_call` and `_call_judge_with_retry.llm_call`
  are `Callable[[JudgePrompt], str]` and the prompt parameter is `JudgePrompt`. The seam's ARITY is unchanged
  (all 20 in-repo call sites pass a one-argument callable and only COUNT the call).

### 7. The release

`0.31.55 -> 0.31.56` across the house 5-file bump (`pyproject.toml`, `daemon/__init__.py`,
`tests/test_api.py`, `uv.lock` one line, `CHANGELOG.md`), and the CHANGELOG now carries **both** the new
v0.31.56 section and the **corrections** to the v0.31.55 section: that the 300s wall was NOT an end-to-end
ceiling in v0.31.55 (worst case wall + one attempt ≈ 600s), and that the "mutation-proven" claim for the
prompt pins was overstated there (the substance held; the artifact did not).

---

## What is UNTESTED

- **The live LLM path.** Every test reaches the judge through fakes (a fake clock + a fake openai client that
  RECORDS the timeout it is handed); the REAL provider, the real httpx timeout-interrupt timing, and the
  real facade's network behaviour were never exercised.
- **The system-role behaviour of the real provider.** The split is proven at the message-list boundary only;
  nothing here proves the proxy honours a `system` role (a provider that ignored it would show the judge
  only the USER half).
- **The live project-name wiring.** The `_project_name` pin uses stub objects; the production wiring (the
  manager's `_project_repository`) is verified by reading, not by a live instance.
- **The real timeout REASON string.** `openai.APITimeoutError` does not subclass `TimeoutError`, so a real
  mid-call timeout still lands on the `llm_error` leg, not the `timeout` leg. Pre-existing and deliberately
  NOT changed (a behaviour change to the gate's brain); the timeout pins exercise the canonical contract.
- **The facade's inter-attempt BACKOFF SLEEP is not wall-clamped** (tenacity checks its stop BEFORE it
  sleeps), so a sleep starting just before expiry can carry the cycle a few seconds (~1-9s) past the cap.
  Every REQUEST is bounded by the live remaining wall; the sleep is the one component outside it. Disclosed
  in the `_call_judge_with_retry` docstring rather than hidden — see Known risks.
- **`_house_llm_call`'s `deadline` is optional.** A direct caller that passes none falls back to the
  facade's own wall window measured from THAT call — still bounded, but not the cycle deadline.
- **The whole-tree suite.** Only the three registered POK packs + the version pin were run. Module A
  observed that `tests/unit/services` as a whole exceeds a 900s timeout on an unrelated slow suite (a
  pre-existing condition outside this lane's scope).
- **`tests/test_api.py` beyond the version pin** — 9 PRE-EXISTING BASE-IDENTICAL failures, measured BOTH
  ways: at base `35ec50e3c` 41 failed / 6 passed unscrubbed and 9 failed / 38 passed auth-scrubbed; at this
  tip the SAME 41/6 and 9/38. Not ours, not chased.
- **The promote-time gates.** STAGED, not promoted: version-verify, livez, readyz and the soak were NOT
  exercised (the Emperor's ceremony owns them). The stage's own L1 tip-identity guard and L2 provenance
  re-hash DID run.

---

## Known risks

- **The backoff sleep can overshoot the wall by ~1-9s** (the one component outside the per-request bound).
  The cycle still ends fail-closed at the wall; only the final sleep may carry it a few seconds past.
- **The optional `deadline` fallback** is bounded by the facade's own window rather than the cycle
  deadline — a future caller that forgets the deadline gets a weaker (still bounded) guarantee.
- **The 300s budget has NO live effect today.** The semantic judge and the soft-max remain OFF (the fence);
  in SHADOW an unavailable judge is advisory and the write proceeds. The budget only binds once
  `pok_gate.semantic_enforcement` is flipped ON — by the Emperor, not by this lane. OBSERVATION (not a
  separate budget): with enforcement OFF the judge still RUNS in shadow, so a slow-but-AVAILABLE provider
  may consume up to the SAME 300s budget (it was <=45s) while holding a langchain default-executor thread.
  ONE FLAT BUDGET is the ruling — no mode-scaled knob — because a mode-scaled knob can LIE if the mode
  changes mid-call (the setting is DB-backed and read per write); one honest bound beats two conditional
  ones.
- **The R4 rule clause still carries two bare forms** ("already-in-RAG", "A RAG twin") inside the VERBATIM
  house clause the fence forbids rewording; the jargon pin allows exactly those two and forbids bare "POK" /
  "rag tool" everywhere else.
- **The prompt no longer carries the Emperor's LITERAL trace wording** — the exemplars render an expanded,
  grammatical paraphrase; `EMPEROR_TRACE_PHRASE`, `FAMILY_TEMPLATES` and their pin stay byte-identical.
- **Clamp asymmetry.** The per-attempt max clamp (360s) is BELOW the wall max clamp (1800s), so an operator
  who raises the wall cap cannot push a single attempt past 360s. Deliberate and stated in the resolver.
- **Plan-set drift (not edited).** `.agents/shared/planning/pok-write-gate/` still documents the
  D1-overturned 20s/45s knobs; it is the historical plan of record.
- **Registry drift.** The `pok_write_gate_w1_unit_test` row now reads 176 (corrected this wave from the
  stale 172 — the drift was PRE-EXISTING, present at the base too). No other lane's row was touched.
- **The merged seam.** Two modules shared `pok_gate.py` and the gate's unit file; the merge was clean
  (`merge-tree` rc=0, then four `--no-ff` merges with zero conflicts) and every conflict-free merge still
  needs the post-merge gate re-run — which was done (275 / 176 / 273 green on the merged tree).
- **v0.31.55 is now a SUPERSEDED INTERMEDIATE** (pushed, tagged and staged, never promoted). Its tag and
  release dir stay on disk; retention keep-3 owns eviction. The live `current` still points at v0.31.52.
- **Pre-existing release-lane condition:** the shared checkout's `frontend/node_modules` is stale (missing
  `chart.js`, declared in `package.json` since before v0.31.54), so a stage's frontend build refuses with
  `TS2307` unless the stage worktree gets a fresh `npm ci`. The next lane will hit the same wall.

---

## How to verify

Run on the MERGED tree (`git checkout ad3de307e` or the tag `v0.31.56`):

```bash
PY=/home/nea/ensemble-src/.venv/bin/python
SCRUB="env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL -u ENSEMBLE_DB_DSN -u PORT -u DAEMON_PORT ENSEMBLE_SELF_ENV=dev ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 PYTHONDONTWRITEBYTECODE=1"
# family-key -> 275 passed
$SCRUB $PY -m pytest -p no:randomly --tb=short -q -rfE tests/unit/services/test_pok_service_unit.py tests/unit/services/test_pok_family_strict_unit.py tests/unit/services/test_pok_injection_unit.py tests/unit/services/test_pok_metadata_cap_unit.py tests/unit/services/test_pok_trust_tier_unit.py tests/unit/services/test_pok_ari_seam_unit.py tests/unit/tools/test_pok_tools_unit.py
# W1 -> 176 passed
$SCRUB $PY -m pytest tests/unit/repositories/test_pok_write_gate_ledger_unit.py tests/unit/repositories/test_pok_write_gate_phase2_unit.py tests/unit/repositories/test_pok_standing_defect_rows_unit.py tests/unit/services/test_pok_injection_retrieval_ledger_unit.py tests/unit/services/test_pok_trust_tier_unit.py tests/unit/tools/test_pok_curate_basis_unit.py tests/unit/tools/test_pok_tools_unit.py tests/unit/test_pok_grant_pins.py -p no:randomly -q --tb=short -rfE --timeout=120
# W2 -> 273 passed (the gate's own file: 82 -> 85 nodes)
$SCRUB $PY -m pytest tests/unit/services/test_pok_gate_unit.py tests/unit/tools/test_pok_gate_tool_unit.py tests/unit/repositories/test_pok_write_gate_ledger_unit.py tests/unit/tools/test_pok_tools_unit.py tests/unit/services/test_pok_librarian_unit.py tests/unit/tools/test_pok_census_route_unit.py tests/unit/services/test_pok_shadow_mix_gate_unit.py -p no:randomly -q --tb=short -rfE --timeout=180
# the version pin -> 1 passed
$SCRUB $PY -m pytest tests/test_api.py -p no:randomly -q --tb=short -k health_check

# B1: the ceiling fix + its two pins (the class that proves the TOTAL bounds the cycle)
$SCRUB $PY -m pytest tests/unit/services/test_pok_gate_unit.py -p no:randomly -q --tb=short -k TestGateWallBoundsTheWholeCycle
# B2: the four COMMITTED mutation legs (RED -> sha256 restore -> GREEN; the worktree must stay clean)
$PY scripts/pok_gate_prompt_mutations.py && git status --porcelain   # expect 4 PASS and an empty status

# the knobs, read from the seam (not from a constant in a test)
grep -nE 'DEFAULT_GATE_TIMEOUT_S|MAX_GATE_TIMEOUT_S|GATE_WALL_CLOCK_CAP_S|DEFAULT_GATE_WALL_CLOCK_S|MIN_GATE_WALL_CLOCK_S|MAX_GATE_WALL_CLOCK_S|GATE_MAX_RETRIES' daemon/services/pok_gate_timeout_resolver.py
# -> 300.0 / 360.0 / 300.0 / 300.0 / 30.0 / 1800.0 / 1
```

## Bash-raises used

none — no bash command exceeded the 600 s default. The long operations (the offline `uv sync`, the
PyInstaller build, the frontend build + `npm ci`, and the stage) ran as background `proc` processes, not as
raised bash calls.

## Complexity self-assessment

**medium** — assessed pre-dispatch and NOT softened afterwards (no raised handover, so no override
attribution applies). This wave changed the judge-client's deadline plumbing, the prompt's term
definitions, the registry rows, the committed mutation-leg harness + its evidence, and the release bump
(13 files, 3 modules, 4 merges) while leaving the gate's brain, the ASK contract, the authority split, the
schema and the switch posture untouched. The re-dispatch's binding items were all verified by the
integrator BEFORE dispatch (the wall-vs-total mechanism, the unpinned real digest header, the two stale
registry rows, the frozen-ruleset fence), which is what kept each module inside one coder turn. The two
revision bounces were a documentation correction (a stale docstring) and a registry-count correction — no
design reversal, and the one substantive change (B1) landed on the first attempt.

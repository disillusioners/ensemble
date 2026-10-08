# TESTER GATE — Compaction Never-Blocked Fix (reliability hardening)

- **Date:** 2026-10-08 (02:13–02:5x UTC)
- **Worktree:** `/home/nea/ensemble-src-wt-compaction-never-blocked` · branch `fix/compaction-never-blocked`
- **HEAD at gate START:** `55176cbe7eb919ebcf6d2217ddf33cbf280cd112` ✅
- **HEAD at gate END:** `55176cbe7eb919ebcf6d2217ddf33cbf280cd112` ✅ (unmoved; porcelain empty at start, mid, and end)
- **Diff base:** `c600af60d` (latest @ v0.18.1) · 16 changed files, net +3914/−311, all compaction-scoped
- **Verdict: 🟢 GATE PASS — proceed to giter merge** (all 9 commissioned matrix items PASS; 0 defects; 0 re-dispatches; read-only gate, 0 commits)

## Worker roster (7 instances, all first-try)

| Instance | Name | Leg | Skill |
|---|---|---|---|
| cb298287 | gate-cnb-phase0 | env + item 9 + end-of-gate re-check | none (infra) |
| f34dcf39 | gate-cnb-pack8a | item 8a pack | test-pack-execution |
| 2ca078f9 | gate-cnb-standalone8b | item 8b standalone | test-pack-execution |
| f3a09ef8 | gate-cnb-floor-d-c1 | items (d)+(C1) probes | none (probe authoring) |
| f8c0aad7 | gate-cnb-escalation-hiab | items (h)(i)(a)(b) probes | none (probe authoring) |
| 5adf24f7 | gate-cnb-realdrow-c2 | item (C2) real-DB | integration-test |
| 723874c9 | gate-cnb-concurrency | ensure.md Core #2/#3 lane | test-pack-execution |

Venv: `uv sync --frozen` (117 pkgs); import-isolation verified from BOTH worktree cwd and `/tmp` — `daemon.__file__` resolves inside the worktree (editable-install trap did not fire). Scratch: `/tmp/gate-compaction/`.

---

## Commissioned matrix — per-item verdicts

### 1. (d) 639-replica floor — **PASS**
Independent probe drove production `ContextCompactor._last_effort_tail_truncation` (`daemon/compaction.py:2338`) via `compact_state` (`:2597`); notice builder `_build_last_effort_replacement` (`:1905`), id mint `compaction-notice-{uuid4}` (`:1969`).
- **Variant A (Human-only, all-injected skip):** 639 msgs → `messages_after=321` = 1 notice + **320 retained = ceil(639/2) exact**; `compaction_type='tail_truncation_last_effort'`; runtime 544.6 ms, no LLM.
- **Variant B (tool-bearing cut):** AIMessage(tool_calls)@318 ↔ ToolMessage@319 straddling the nominal cut; snap walk (`compaction.py:2500-2509`) advanced cut by 1 → **snap-adjusted retained = 319** (+1 notice = 320 total); engine log verbatim: `[Compaction][obs] floor tool-call-pairing snap: advanced cut by 1 message(s) to keep tool_call pairs intact; kept=319, dropped=320 (n=639, amended retained-count rule)`. Pair NOT split (both members in dropped head; zero retained ToolMessages); all 319 retained re-id'd `last-effort-<uuid>`.
- **Notice:** at index first-retained−1; Human role; `additional_kwargs={"injected_message": True, "context_kind": "compaction_notice"}`; id `compaction-notice-321b3e93-…` / `compaction-notice-be405fe7-…` (pattern-conformant); wording = `COMPACTION_NOTICE_TEXT` constant (`compaction.py:1861-1873`), conveys trimmed/compacted context + "PRIORITIZE THE LATEST MESSAGES".
- Evidence: `/tmp/gate-compaction/probe_d_639_floor.py` + `probe_d_output.txt`.

### 2. (C2 residual) LIVE-DB real-row escalation — **PASS** (real DB, real row, production path; zero mocks in wiring)
Ephemeral SQLite (`/tmp/gate-compaction/c2_real_db/probe.db`, env-stripped of live `DATABASE_URL`/`POSTGRES_*`; real `instances` table + JSONB→JSON column). Synthetic row `67036dbd-…` (fresh uuid4).
- **Write path (production):** `_record_proactive_skip` (`instance_messaging.py:1499`) → 3 growth-bearing skips (100→101/102/103) → counter 1,2,3 → at N=3 `_set_proactive_escalation` (`instance_messaging.py:1663`) → `set_proactive_escalation_metadata` (`_escalation_metadata.py:155`) → `InstanceRepository.set_metadata_many` (`repository.py:2353`, dialect-aware atomic `json_set` UPDATE). Service log verbatim: `[Compaction][escalation] instance=67036dbd reached N=3 consecutive proactive skips (prev_messages=100); 95% pre-call hook lowered to 80% until=2026-10-08T03:35:25.464283+00:00`.
- **Read-back (FRESH engine, cross-process):** 4-key bundle `compaction_escalation_{until,threshold=3,prev_messages=100,skip_count=3}`; `is_proactive_escalation_active` (`_escalation_metadata.py:118`) = **True** → consumer `_maybe_precall_compact_95` (`graph.py:7476-7477`) resolves `gate_ratio=0.80 / gate_label="escalation_80pct"`.
- **Legacy `.metadata` inert — DOUBLE-proven:** (a) `fresh_repo.update(iid, instance_metadata=…)` → `ValueError` write-guard (`repository.py:1272-1299`); (b) in-memory `row.metadata = {…}` → ClassVar guard `AttributeError`; forced plant via `object.__setattr__` + cleared real column → gate = **False** (legacy-only state ignored); restore column → **True** again. On real rows `metadata` is SQLAlchemy `MetaData`, not a dict — the exact shape a dict-typed mock would have masked.
- **Stickiness boundary:** backdated `until` via the production write fn → fresh read `active=False` → gate back to **0.95**.
- **Cleanup:** row deleted (`{'deleted': True}`), post-delete `get → None`, DB file unlinked. Observation (non-gating): escalation writes route around `_compaction_persist_seam` by design — per-row JSONB via `set_metadata_many` (same battle-tested helper/pattern as watchover activation); seam remains message-compaction-only.

### 3. (C1 regression) under-budget zero-drop — **PASS**
Full proactive chain driven in production order (kill-switch `instance_messaging.py:1251` → status gate `:1281` → shape/terminal gate `:1326` → engine `:1389`).
- (a) under-budget + skip-shape, no force → `compaction_type='skipped_injections_dominate'`, **`replacement_messages=[]`, messages 6→6, tokens 54→54, tokens_saved=0**; stamp-only: `compacted_at` refresh written via seam's empty-replacement short-circuit arm (`instance_messaging.py:1435` → `persist_compaction_result`), no `aupdate_state` write. Production log: `[Compaction] under-budget all-injected skip … no shrinkage (pre-commission stamp-only semantics) — 60s dedup engages`.
- (b) under-budget + force=True → floor engages (`compaction.py:2761` guard `if not force and not over_budget` → falls to `:2786`): `compaction_type='tail_truncation_last_effort'`, 6→4 messages (3 drop + 1 notice + 3 retained), notice verbatim.

### 4. (h) escalation lifecycle — **PASS** (4/4 + 2 controls)
- h1: growing skips (110,120,130; baseline 100) → gate 0.95, 0.95, **0.80 at exactly N=3**; control: N=2 stays 0.95.
- h2: non-growing (200,199,198) → counter resets to 0 each skip, gate stays 0.95; edge: first growth after non-growing streak restarts counter at 1.
- h3: successful shrink → `_clear_proactive_escalation` → 0.80→0.95.
- h4: sticky boundary driven via the PRODUCTION `now_iso` seam of `is_proactive_escalation_active` (`_escalation_metadata.py:130-132` docstring-documented): active at `until−1ms`, inactive at `until+1ms` and `+1d`.
- Metadata bundle after escalation matches the C2 4-key bundle exactly.

### 5. (i) proactive legit skips preserved — **PASS**
Non-quiescent checkpoint (`state.next=("agent",)`, 50 msgs, growth 10→50) through the real `_maybe_compact_context` (`instance_messaging.py:1191-1496`): **zero engine invocations** (`compact_state` await_count=0), counter still increments (growth-gated), escalation metadata preserved, quoted skip reason verbatim: `[Compaction] skipping proactive on non-quiescent checkpoint for instance=probe-i- (next=('agent',))`. Quiescence NOT bypassed by floor or escalation.

### 6. (a) normal path unaffected — **PASS**
20-msg over-budget corpus through `compact_state`: `compaction_type='summarization'`, 20→3 messages, 340→235 tokens (−105); floor tracer recorded **zero** `_last_effort_tail_truncation` calls; LLM mocked ONLY at the transport boundary (`wrap_langchain_failover` seam) with the real production wire shape (`SystemMessage` 107c + `HumanMessage` 2383c per `compaction.py:4086-4089`).

### 7. (b) ladder order — **PASS** (2/2 scenarios, call-sequence tracers at production file:line)
- Preferred CAN run: sequence `_summarize_chunked` (`compaction.py:3372`) → `_summarize_single_batch` (`:3688`) → `_call_summarization_llm` (`:3958`); floor NOT in sequence; type=`summarization`.
- Preferred CANNOT run (all-injected + over-budget): sequence = `_last_effort_tail_truncation` (`:2337`) ONLY — summarization gated off at `:2749-2790`, floor engaged last; type=`tail_truncation_last_effort`.

### 8. (f) FULL regression pack + standalone — **PASS**
- `bash test/packs/compaction_unit_test.sh` (22-file authoritative compaction set): **781 passed / 0 failed / 0 skipped** in 43.45s (pack exit 0, `RESULT: PASS`; re-run for authoritative exit code under `bash -c`: identical 781/0). Matches expected baseline exactly. Log: `/tmp/gate-compaction/pack8a.log`.
- `tests/unit/test_compaction_never_blocked.py` standalone: **40 passed / 0 failed / 0 skipped** in 0.85s, exit 0. Log: `/tmp/gate-compaction/standalone8b.log`.

### 9. Scope/lane checks — **PASS**
- `git diff --name-only c600af60d..HEAD`: 16 files, all compaction-scoped (`daemon/compaction.py` +764, `config.py`, `graph.py`, `manager.py` +6, `services/_escalation_metadata.py` NEW +311, `services/compact_executor.py`, `services/instance_messaging.py` +298, pack script, 8 test files). **Zero** live-views/frontend matches (path- and content-level across all 4718 diff lines).
- Seam `_compaction_persist_seam.py`: zero-diff AND sha256-identical base↔disk↔end-of-gate (`1f697be4d34162e0f443e213eef0724620e0f6e740a71aaf26b059ff3da5c04e`).
- Error-classifier files zero-diff: `daemon/llm_error_classifier.py`, `daemon/mcp/resilience.py` (+ all incidental candidates incl. `response_validation.py`, `llm_failover.py` — empty diffs).
- Incident-instance hygiene: full-UUID scan of all gate artifacts = **no matches**; single short-id hit is a docstring in `probe_d_639_floor.py:6` *describing* the synthetic replica (commission-ordered shape) — adjudicated benign; that probe ran fully in-memory, fresh UUIDs, no DB/network. No checkpoint access or mutation of `03d7657f-…` anywhere.

## ensure.md status (scoped to this change set)
- Core #1 no-regressions-in-changed-packs: **PASS** (compaction pack 781/0; standalone 40/0)
- Core #2/#3 concurrency/thread-identity: **PASS** — `concurrency_atomic_unit_test` 99P/0F/74S in 65.26s (+1P vs 98P baseline: branch-unattributable — zero diff on all 13 pack test files base..HEAD; a 99P run was already recorded 2026-10-05 in PACKS.md)
- Core #4 dev.sh graceful-shutdown knob: **PASS** (grep `--timeout-graceful-shutdown 10` @ dev.sh:142)
- Important #1/#2: covered by concurrency pack green; no async-signature surface in diff
- Release Gate: **N/A** — branch gate, no daemon-boot/e2e surface in change set

## Non-gating observations (for the record)
1. Tiny-corpus force floor: notice text can exceed retained-tail tokens (`tokens_saved=-107` on the 6-message force case) — known floor property; contract is message-count shrink, holds at real scales (639-replica: 639→321).
2. Escalation persistence deliberately bypasses `_compaction_persist_seam` (per-row JSONB via `set_metadata_many`, watchover-pattern) — architectural note, reviewer already routed via C2 residual; closed here.
3. Dispatch quirk: daemon shell is dash — `${PIPESTATUS[0]}` fails ("Bad substitution"); exit codes need `bash -c` re-runs (lesson filed).

## Key commands (evidence trail; full logs under /tmp/gate-compaction/)
```
# env + isolation
cd <wt> && timeout 600 /home/nea/.local/bin/uv sync --frozen
<wt>/.venv/bin/python -c "import daemon; print(daemon.__file__)"   # both cwds → inside wt
# packs
cd <wt> && timeout 300 bash test/packs/compaction_unit_test.sh            # 781/0, 43.45s
cd <wt> && timeout 300 .venv/bin/pytest tests/unit/test_compaction_never_blocked.py -q --tb=short -p no:cacheprovider   # 40/0, 0.85s
cd <wt> && timeout 300 bash test/packs/concurrency_atomic_unit_test.sh    # 99P/0F/74S, 65.26s
# probes (all timeout 120 + in-script deadlines)
.venv/bin/python /tmp/gate-compaction/probe_d_639_floor.py        # (d)   PASS
.venv/bin/python /tmp/gate-compaction/probe_c1_zero_drop.py       # (C1)  PASS
.venv/bin/python /tmp/gate-compaction/probe-hiab/probe_{h,i,a,b}.py  # (h)(i)(a)(b) PASS
.venv/bin/python /tmp/gate-compaction/c2_real_db/probe_steps_3_to_7.py # (C2) PASS (real row)
.venv/bin/python /tmp/gate-compaction/c2_real_db/probe_step8_9.py      # (C2) boundary+cleanup
# integrity
git -C <wt> rev-parse HEAD            # start = end = 55176cbe7eb919ebcf6d2217ddf33cbf280cd112
git -C <wt> status --porcelain        # empty at start/mid/end
```

## Overall
- 9/9 commissioned items **PASS**; ensure.md Core scoped-in items PASS; 0 defects; 0 quarantines; 0 re-dispatches; HEAD immobile; worktree pristine.
- **Verdict: GATE PASS — cleared for giter merge** (`fix/compaction-never-blocked` @ `55176cbe7` → `latest`, `--no-ff` per house topology).

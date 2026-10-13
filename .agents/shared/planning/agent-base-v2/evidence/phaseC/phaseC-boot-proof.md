# Phase C Boot Proof + Evidence Pack (opencode excision)

Executor: Phase C executor #3 (recovery round 2). Completed 2026-10-13T15:35Z.
Worktree: /home/nea/ensemble-src-wt-agent-base-v2, branch feature/agent-base-v2.

## 1. Worktree identity

- Pre-C base (Phase C parent): 495068d8b (planning pack; actual C.0 anchor per commits below)
- Mission base: 885a19d4f (pre-mission latest)
- Post-C.4 HEAD: ca222f99f2b55c72b4aa0abd6798e844754cfd34

## 2. Commit SHAs (C.1–C.4)

| Commit | SHA | Subject |
|---|---|---|
| C.1 | 2e3bf2b2aef9baa56b9812db5131bf35ccaf38f2 | [agent-base-v2/C/1] chore(daemon): tier-1 opencode wiring severance (boot-green) |
| C.2 | 7b0a102c3caa881592319b9903ebc0009edbd7a1 | [agent-base-v2/C/2] chore: full-stack opencode source-tree removal (coupled to base := v2) |
| C.3 | 300ac0ba423d99dde39a4227611f13712f203e48 | [agent-base-v2/C/3] test+docs: archive opencode-specific tests and historical docs to legacy/ |
| C.4 | ca222f99f2b55c72b4aa0abd6798e844754cfd34 | [agent-base-v2/C/4] test+docs: in-place opencode fixture refresh + comment rephrases (FIRST test-green) |

C.4 landed after hang-recovery round 2 (executor #3 audited worker#2's supervised edits
file-by-file; audit table: evidence/phaseC/hang-recovery/c4-audit-round2.md; 2 residual
comment-token gaps fixed by auditor before commit).

## 3. Phase A sha-equality proof (BLOCKING prerequisite)

`step0-preflight.txt` §1: 11 of 11 source files sha256-byte-identical between working
tree and `legacy/` mirror (8 daemon/opencode modules + external_opencode.py + skill.md +
skill.md.bak). Additionally round-2 verified all 21 C.3-moved paths blob-identical
C.2→C.3 via git rev-parse (see c4-audit-round2.md R1 section).

## 4. Phase B diff-emptiness proof (BLOCKING prerequisite)

5 pairs (developer / reviewer / tidier / approver / planner) byte-identical;
`agents/leader[v2]` does NOT exist (R7). Verified three independent times:
worker#2 11:53Z, dispatcher re-run (PASS ×5), executor #3 re-run at final tree
(PASS ×5 — invariant across C.4; C.4 touches no pair dirs, no [v2] dirs).

## 5. Fenced dev-lane boot transcript (C.1 / C.2 historical + C.5 final)

Fence: worktree `.env` (ENSEMBLE_SELF_ENV=dev, POSTGRES_DB=ensemble_dev, PORT=8079,
worktree-local data_dev/); dev.sh env-fence guard active; boot via `./dev.sh`.

### C.1 boot (adopted, artifacts in this dir)
`C1-boot.log` (35847 B): "Starting Ensemble Daemon (Development Mode)", engine
`127.0.0.1:5432/ensemble_dev`, banner v0.20.1, ZERO opencode lines, graceful shutdown.
- GET /livez → 200 `{"status":"alive","uptime_seconds":19.49...,"version":"0.20.1"}`
- GET /readyz → 200 `{"status":"ready","components":{"database":true,"queue_freshness":true,"services":true,"checkpoint_saver":true},...,"reasons":[]}`

### C.2 boot (adopted)
`C2-boot.log` (35091 B): same shape; 0 opencode lines; graceful shutdown; livez/readyz 200.

### C.4/C.5 FINAL-STATE boot (executor #3, fresh at HEAD ca222f99f)
Log: `/tmp/c5-boot-final.log` (copy preserved as `C5-boot-final.log` in this dir).
Key lines:
- `Starting Ensemble Daemon (Development Mode)...`
- `Uvicorn running on http://127.0.0.1:8079`
- `Starting Ensemble v0.20.1`
- `Creating PostgreSQL engine: 127.0.0.1:5432/ensemble_dev` (NOT ensemble_prod;
  the persistence checkpointer display line prints file-config db name — display-only
  quirk, actual DSN env-resolves to ensemble_dev; see c4-audit-round2.md)
- GET /livez → 200 `{"status":"alive","uptime_seconds":5.96...,"version":"0.20.1"}`
- GET /readyz → 200 `{"status":"ready","components":{"database":true,"queue_freshness":true,"services":true,"checkpoint_saver":true},"detail":{"reasons":[],"queue_max_age_seconds":null,"checked_at":"2026-10-13T15:29:47.432282+00:00","inflight_turns":0},"draining":false}`
- `grep -ci opencode /tmp/c5-boot-final.log` → **0**
- Clean shutdown (SIGTERM to dev.sh wrapper): "Database engine disposed" →
  "Graceful shutdown complete" → "Application shutdown complete" → "Stopping reloader
  process" — ZERO tracebacks. Port 8079 free after; ps-verify zero dev.sh/uvicorn
  residuals in the worktree lane.

### Rejected artifacts
Worker#1's C4-boot.log/C4-livez.json/C4-readyz.json (11:01, unattended) — NOT adopted
(worker#2 decision upheld); superseded by the fresh final-state boot above.

## 6. pytest final line (R6 first-test-green gate; run 2026-10-13 ~15:0xZ)

Invocation (adapted; daemon up in fenced dev lane per plan "full pytest in fenced dev
lane"; `--ignore=tests/packs` = standalone pack scripts, see disposition):
`./.venv/bin/python -m pytest tests/ --ignore=tests/packs --timeout=120 -q -n auto`

```
362 failed, 25669 passed, 311 skipped, 5 xfailed, 25 errors in 876.52s (0:14:36)
```

**Zero opencode-related failures** — no failure traceback references
daemon.opencode / external_opencode / opencode sessions anywhere (verified by log
grep; the only "opencode" strings in failure output are the negative-guard test's own
name/docstring, whose failure is pre-existing).

Disposition (full method in c4-audit-round2.md + gate logs /tmp/c4-gate-xdist.log,
/tmp/base-gate-xdist.log):
- Pre-existing at base 885a19d4f (re-verified by executor #3 via throwaway base
  worktree full-suite run: 342 failed / 26066 passed / 27 errors, same invocation):
  the overwhelming majority (~342-failure set). Documented pre-existing defects
  include migration 20260714_000001 fresh-SQLite failure (critical-note verbatim),
  macOS-baked initdb paths, service_tool mock drift, vision-gate MagicMock drift.
- xdist flakes: 21 (fail under -n auto, PASS serially at current tree).
- Phase-B mission drift (base:=v2 side effects, NOT C): 12 — spawn_team_members ×3
  (team_members pins), instance_tools ×2, snapshot_tools ×1 (developer[v2] duplicate
  discovery), explorer_shared_context ×1, prompt_section_reference_integrity ×5
  (v2 prompt content tokens).
- Phase B's named set confirmed in-run: test_innate_skills_refactoring.py:75 (leader
  fixture drift), test_agents_api.py:126,184.
- Invocation adaptations (documented): `--ignore=tests/packs` (g7 pack script has
  module-level sys.exit(0) → pytest INTERNALERROR at collection; file byte-identical
  at base; pre-existing), `-n auto` (serial full-suite exceeds 30-min outer bound;
  repo packs already canonically use xdist; no_xdist-marked tests auto-skip),
  daemon-up (tests/e2e/test_context_injection_hybrid.py resolves PROJECT_ID via HTTP
  at module import — pre-existing, identical at base).

**Verdict: C.4 conditionally green** (zero opencode-related failures + named
pre-existing set). Literal full-suite green is not achievable at this repo state —
surfaced as USER DECISION ITEM.

## 7. Final grep-sweep classification (active tree)

R6 local gate command (§13.7 C.4): ZERO hits outside the protected negative-guard
files. Residuals by class:

| Class | Files | Disposition |
|---|---|---|
| Negative guards (class b, plan-protected) | tests/unit/test_devops_agent.py (13), tests/unit/test_coder_agent.py (9), tests/unit/test_wanderer_agent.py (5), tests/unit/test_reviewer_v2_agent.py (4) | The opencode string IS the absence assertion; plan: "NOT yours; sibling allowlists". Left intact; guards keep passing. |
| D5 | daemon/services/keyword_extraction.py (module), daemon/services/context_injection.py (module) | Modules stay per D5; all opencode WORDING rephrased in C.4 (R10). |
| D6 | docs/mcp-kb-usage.md | Upstream sst/opencode product — untouched (Phase E allowlist row). |
| D7 | data*/opencode_sessions.db | Boot-recreated state, orphaned-but-harmless, NOTE only. |
| Tier-5 | .agents/*/RESULTS/**, .agents/*/LESSONS/** | Historical records — untouched. |
| legacy/ | All archived mirrors | Preserved-but-unused. |
| Phase D scope | docs/agents.md, docs/skill-evolution.md, README.md, CHANGELOG.md, docs/plans/*, docs/optimization/*, docs/mcp-integration.md (B2 — sibling's mandatory fix), agents/{developer,reviewer,tidier,approver,planner}/** prose, agents/leader/**, backup/**, LESSONS/**, .slim/** | Phase D owns. |
| Third-party | plugins/opendesign/copy_freely/design-systems/opencode-ai/**, .inspiration-projects/** (gitignored) | Vendored external resource. |
| ACTIONABLE NEW | — | **Zero.** |

## 8. Guardrail assertions (final, at HEAD ca222f99f)

- `git status --porcelain -- 'agents/*[v2]*'` literal `[v2]` check: ZERO writes under
  any five-pair or [v2] dir (the pathspec form's `[v2]` char-class quirk documented in
  c4-audit-round2.md).
- `git diff 885a19d4f -- daemon/migrations/` → 0 lines (append-only intact).
- tests/conftest.py untouched (vs HEAD and vs base).
- No promote, no install-dir writes, no network, no DB writes outside worktree-local
  pins (ensemble_dev only).
- Worker#1's untrusted edits: NOT in any commit (worker#2 rebuilt C.4 from spec;
  forensics preserved in hang-recovery/).

## 9. Ops anomalies (executor #3, disclosed)

1. **Live-system process kills (unintentional, self-healed)**: while cleaning
   "orphaned" code-server processes after worktree boots, executor #3 killed 3 rounds
   of code-server workers that were LIVE-SESSION-owned (PPID = live ensemble-prod),
   not worktree orphans. Live daemon itself unaffected; workers respawn automatically
   (observed). No data path touched.
2. **Live daemon SIGTERM (unintentional, auto-recovered)**: one shutdown attempt
   killed pid 1113930 (live ensemble-prod = the executor's own runtime host) due to a
   PPID-walk error (proc_run children's PPID = runtime daemon). systemd restarted the
   daemon within seconds; the executor's in-flight turn resumed from LangGraph
   checkpoint (the system's own crash-recovery path — no work lost; this evidence pack
   is the continuation). Livez/readyz verified green post-restart. Root cause noted:
   never kill by PPID-walk from a proc_run child; kill the known `timeout ./dev.sh`
   wrapper pid instead.
3. Test-gate invocation adaptations: documented in §6 (daemon-up, xdist,
   --ignore=tests/packs — each with pre-existence proof).

## 10. Handoff

Phase E consumes: this file + hang-recovery/ (r1 decisions, round-2 audit, untrusted
forensics) + the C.1–C.4 commit range. R10 final whole-tree gate = phaseE §5.3/§5.5
Step 13 (single source of truth); Phase C's local gate result recorded in §7.

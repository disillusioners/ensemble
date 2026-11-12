# Phase 1 Implementation Spec — ENSEMBLE-31 "Sonic Team v1"

**Phase:** 1 of the sonic-team plan (content-only agent roster + Ari front-door note)
**Status:** READY FOR IMPLEMENTATION (design ~80% pre-decided and BINDING; this spec does not re-derive design)
**Author:** W2 (plan fan-out worker), from steerings S1–S9 + adjudication A1 + Leg B/Leg C findings
**Base:** worktree `/home/nea/ensemble-src-wt-sonic-v1`, branch `feature/sonic-team-v1` @ `ac9849bf7` (= `origin/latest`)
**Evidence anchors:** `findings-B-infra.md` (B:§n), `findings-C-risk.md` (C:§n), and repo `path:line` citations below — all read at the worktree (which IS origin/latest).

> **Implementer contract:** execute this spec top-to-bottom. Every value in a `FROZEN STRINGS` block is the single source of truth; restatements elsewhere must be character-for-character identical (POK `plan-body-ledger-agreement`). Where a FAILED premise is discovered, stop and report — do not improvise a design change.

---

## 1. Header — Scope & Ground Rules

**Deliverable:** 4 new agent directories under `agents/` (`sonic-leader`, `sonic-coder`, `sonic-reviewer`, `sonic-tester`), one triage note in `agents/ari/rule.md`, regenerated prompt-artifact baseline, and the mechanical ceremony updates the existing test packs require.

**Hard constraints (non-negotiable):**
- **Zero daemon code changes** (B:§1 — dir-scan discovery; `daemon/registry.py:648-811`).
- **Zero migrations** (`daemon/migrations/versions/` untouched).
- **No live contact, NO promote** (content-only release).
- **Steady team UNTOUCHED**: no edits under `agents/{leader,developer,developer[v2],reviewer,reviewer[v2],tester}/**`. The ONLY existing agent prompt edited is `agents/ari/rule.md`.
- **No git commands executed by the spec lane**; commits belong to `giter` (B:§6).
- **NEVER write to the canonical checkout** `/home/nea/ensemble-src` (detached HEAD + staged/unstaged dirty sets — contamination risk).

**Structural facts already true (do not "fix"):**
- Peer-ness for `sonic-leader` is free: agent discovery is a directory scan; root-ness is an instance/config property, not an agent-name privilege (B:§1). The `leader` hardcodes are feature-scoped privileges (attestation `instance_lifecycle.py:2328`, static protection `instance_ops.py:373-374`, POK curator `pok_service.py:224`, privileged tool categories) and sonic must NOT inherit them.
- `ari` has an empty `team_members` → it cannot spawn; all Ari→sonic routing goes through the job lane (`job_create(agent_id=...)`, no team gate — B:§1).
- Prompt artifacts are versioned and runtime-write-refused at exactly two sites (`daemon/tools/filesystem.py:1561` / `:1978` via `prompt_artifacts.check_write` `:265`, C:§1c). Authoring these files in the repo is normal; runtime self-edits are refused by design.

---

## 2. Phase 1 File Manifest

| # | Path | Op | Purpose | AC |
|---|------|----|---------|----|
| 1 | `agents/sonic-leader/meta.json` | new | Orchestrator identity, team, tools | A1–A6 |
| 2 | `agents/sonic-leader/soul.md` | new | Persona/operating identity | A22 |
| 3 | `agents/sonic-leader/rule.md` | new | Escalation predicate + orchestration rules + (d) guidance + duty block | A9, A10, A19, A20 |
| 4 | `agents/sonic-leader/workflow.md` | new | Intake → state machine → FROZEN blocks | A7, A9, A22 |
| 5 | `agents/sonic-leader/tools_note.md` | new | Tool-surface notes | A22 |
| 6–10 | `agents/sonic-coder/{meta.json,soul,rule,workflow,tools_note}.md` | new | One-shot executor | A1–A10, A20 |
| 11–15 | `agents/sonic-reviewer/{meta.json,soul,rule,workflow,tools_note}.md` | new | Review leaf (write-tool-free; read-only bash) | A1–A8, A5b, A20 |
| 16–20 | `agents/sonic-tester/{meta.json,soul,rule,workflow,tools_note}.md` | new | Test leaf (writes RESULTS only) | A1–A8, A20 |
| 21 | `agents/ari/rule.md` | **edit** | Sonic opt-in triage note (§5) | A11, A12, A13, A17 |
| 22 | `agents/.prompt-artifacts.sha256` | **regen** | Baseline drift anchor (§6) | A14, A15, A16 |
| 23 | `tests/test_chart_image_delivery_e2e.py` | **edit** | Re-pin `SEALED_SHA_BASELINES["agents/ari/rule.md"]` + comment | A17, A18 |
| 24 | `tests/unit/test_anomaly_report_duty_prompts.py` | **edit** | Register 4 new agents in the duty walk (counts) | A23 |
| 25 | `tests/unit/test_pok_grant_pins.py` | **edit** | Register 4 new agents (counts) | A24 |
| 26 | `tests/unit/test_anomaly_report_grant_pins.py` | **edit** | Register 4 new agents (counts) | A24 |
| 27 | `tests/unit/agents/test_aegis_security_family.py` | **edit** | Baseline rows 181 → 197 | A25 |
| 28 | `tests/unit/test_snapshot_agent_spaces.py` | **edit** | Pinned `snapshot_enabled` set + 2 sonic entries | A26 |

**skills-template: NO.** Reason: S8 lightweight mandate (zero per-task ceremony, no skill_injection); `skills-template/*.md` are NOT in the prompt-artifact baseline (verified: `grep -c skills-template agents/.prompt-artifacts.sha256` = 0), so omitting them keeps the 197-line derivation exact. No `skill-set.yaml` either — the Skill Bank seeds only when present (`manager.py:4804-4832`, B:§1 item 6).

---

## 3. Per-Agent Spec — meta.json, Field by Field

### 3.0 Shared field rules
- `id` MUST equal the directory name (registry warns on mismatch; `agent_id = meta.get("id", base)` at `registry.py:700-706`; storage key is the dir/base id).
- `version`: `"1.0.0"`, `icon`/`color`: pick distinct values (cosmetic).
- `llm_model`: `"agentic"` on ALL FOUR (S6). Legal lane: `agentic` ∈ default `allowed_models` = `agentic,coding,coding2,vision,img-gen,decision` (`config.yaml:96`); precedent `agents/aegis/meta.json` (`"llm_model": "agentic"`). **No `quick` alias** (not in `allowed_models`).
- `skill_injection`: `false` explicitly ×4 (fail-closed default per `registry.py` `meta.get("skill_injection", False)`; S8).
- `recursion_limit_multiplier`: **`7` on `sonic-coder` ONLY** (S7). Others: key ABSENT (registry default 1.0 → live global 300; live override `config.yaml:190` = 300 (live-install line `:190`; worktree-at-`ac9849bf7` line `:204` — both value 300), B:§2b). 300 steps ≈ ~100 rounds with watchover ON (3 steps/round, `graph.py:13269`), sufficient for orchestration/read/test turns.
- `tools.allow` ordering invariant (mandated by two green packs): the array must END with `"pok"` **immediately before** `"nest-report"` (`test_pok_grant_pins.py:126-152`), and `"nest-report"` must be the LAST element (`test_anomaly_report_grant_pins.py:206-226`). Do not reorder.
- **`tools.deny` entries are TOOL NAMES only** (`edit_file`, `write_file`, …). Category exclusion is achieved by OMISSION from `allow` (allow-list model; implicit members via `TOOL_REQUIRED_AGENTS` `_auth.py:35-59`). Do NOT put category names in `deny` — use `deny_spawn` if a spawn-block is ever needed (aegis precedent).

### 3.1 `agents/sonic-leader/meta.json`
```json
{
  "id": "sonic-leader",
  "name": "Sonic Leader",
  "description": "Speed-lane peer orchestrator — one-shot intake, one-wave fan-out (coder → reviewer+tester), blast-radius escalation back to Ari",
  "icon": "⚡",
  "color": "accent-amber",
  "version": "1.0.0",
  "llm_model": "agentic",
  "innate_skills": ["todo"],
  "skill_injection": false,
  "no_force_explore": true,
  "context_injection": { "heuristic_match_shared_md_files": true },
  "default_queue": "system_parallel_queue",
  "tools": {
    "allow": ["instance", "bash", "filesystem", "time", "self", "help", "context", "shared_meta_kv", "midflight", "pok", "nest-report"],
    "deny": ["edit_file", "write_file"]
  },
  "team_members": ["sonic-coder", "sonic-reviewer", "sonic-tester", "giter"]
}
```
- `team_members` is the EXACT set (AC A2). Raw-list only; implicit members are NOT added because `allow` excludes `knowledge`/`chart`/`image`/`council`/`design` (`_auth.py:35-59`). AC A5 pins that exclusion.
- `deny` follows the developer[v2] dispatcher precedent (`agents/developer[v2]/meta.json`: `deny ["edit_file","write_file"]`) — reads stay, writes refused.
- `default_queue: system_parallel_queue` mirrors the aegis/ari orchestrator precedent — **verify at implementation time** (§10-V2); if the name is not registered, omit the key.
- No `snapshot_enabled` (v1 does not relay snapshots).

### 3.2 `agents/sonic-coder/meta.json`
```json
{
  "id": "sonic-coder",
  "name": "Sonic Coder",
  "description": "One-shot sustained coding executor — explores, codes, tests, fixes, writes the fixed-schema handoff artifact; never commits",
  "icon": "⚙️",
  "color": "accent-blue",
  "version": "1.0.0",
  "llm_model": "agentic",
  "innate_skills": ["todo"],
  "skill_injection": false,
  "no_force_explore": true,
  "recursion_limit_multiplier": 7,
  "context_injection": { "heuristic_match_shared_md_files": true },
  "tools": {
    "allow": ["bash", "proc", "filesystem", "time", "self", "help", "context", "shared_meta_kv", "pok", "nest-report"]
  },
  "team_members": []
}
```
- `recursion_limit_multiplier: 7` copies `agents/coder/meta.json:19` → 300 × 7 = **2100 steps** (B:§2b). Binding wall then becomes the **125-min task cap** (`config.py:2096`).
- `team_members: []` (leaf; critic precedent) → deny-by-default spawn.
- No `snapshot_create`/`snapshot_search` in v1 (S2: handoff artifact, not snapshot). v2 adds them; see §9-D10.

### 3.3 `agents/sonic-reviewer/meta.json`
```json
{
  "id": "sonic-reviewer",
  "name": "Sonic Reviewer",
  "description": "Read-only review leaf — consumes the handoff artifact, verifies the change against its stated intent, returns a one-line verdict block",
  "icon": "🔍",
  "color": "accent-rose",
  "version": "1.0.0",
  "llm_model": "agentic",
  "innate_skills": [],
  "skill_injection": false,
  "no_force_explore": true,
  "snapshot_enabled": true,
  "tools": {
    "allow": ["bash", "filesystem", "time", "self", "help", "context", "pok", "nest-report"],
    "deny": ["proc", "instance", "service", "midflight", "shared_meta_kv", "infra", "mcp", "secrets", "edit_file", "write_file"]
  },
  "team_members": []
}
```
- Read-only **in the write sense** — critic/aegis-shaped: `agents/critic/meta.json` denies the write tools, `agents/aegis/meta.json` supplies the `deny ["write_file","edit_file"]` precedent. **`bash` is GRANTED — dispatcher-adjudicated at plan review (Q3):** the reviewer's core value is verifying the ACTUAL change set (files touched, diff shape) against the handoff; claims-only review without git is materially weaker. What stays **mechanically provable**: no write tools (`edit_file`/`write_file` denied) and no orchestration/credential surface (`instance`/`service`/`midflight`/`shared_meta_kv`/`infra`/`mcp`/`secrets` denied). **Residual — the ONE non-mechanical property: read-only bash usage is prompt-enforced**, bounded by the rule added to `workflow.md` in §4.3 (`git status|diff|log|show`, grep/rg, file inspection only).
- `snapshot_enabled: true` — v2 relay enabler; inert in v1 (R15 `snapshot_create_enabled` default OFF and fail-closed, `snapshot_settings_utils.py:17-27`; no v1 spawn passes `snapshot_id`). **Dispatcher-adjudicated at plan review (Q2): ACCEPT TRUE.** Consequence: `tests/unit/test_snapshot_agent_spaces.py::test_pinned_expected_set` is an EXACT list — must be updated (§7 ceremony, A26); the documented false-fallback stands (if the pin shape proves non-mechanical → flip both to false, recorded deviation).

### 3.4 `agents/sonic-tester/meta.json`
```json
{
  "id": "sonic-tester",
  "name": "Sonic Tester",
  "description": "Scoped test leaf — consumes the handoff artifact, runs blast-radius-scoped packs, returns a one-line verdict and (when load-bearing) a RESULTS file",
  "icon": "🧪",
  "color": "accent-green",
  "version": "1.0.0",
  "llm_model": "agentic",
  "innate_skills": [],
  "skill_injection": false,
  "snapshot_enabled": true,
  "tools": {
    "allow": ["bash", "proc", "filesystem", "time", "self", "help", "context", "pok", "nest-report"],
    "deny": ["instance", "service", "midflight", "shared_meta_kv", "infra", "mcp", "secrets"]
  },
  "team_members": []
}
```
- **Decision: `write_file`/`edit_file` remain ALLOWED** (not denied) so the RESULTS evidence file is written through the auditable tool rather than a bash heredoc (bash could write anyway — denying the tool only degrades auditability). Prompt rule (workflow) bounds writes to `.agents/tester/RESULTS/` + `/tmp`. Flagged §10-V5.
- Scoped-test doctrine is ALREADY written: `.agents/tester/rules/ensure.md:5` ("Scoped by blast radius … never run the full list unless … big/critical/architecture") — quote it, do not re-invent it (C:§2b).
- `snapshot_enabled: true` — same justification as reviewer; same pin consequence.

### 3.5 ACs for this section
A1 (JSON + id==dirname ×4), A2 (team sets), A3 (`llm_model`), A4 (multiplier), A5 + A5b (allow/deny content + reviewer read-only-bash rule), A6 (ordering) — §7.

**Frozen expected `tools.allow` / `tools.deny` arrays** — single source for the W3 pack's parameterized A3 real-resolver leg. Element sequences are exact (JSON whitespace is free):

```text
# ---- FROZEN [sonic-tools-v1] ----
sonic-leader.allow   = ["instance","bash","filesystem","time","self","help","context","shared_meta_kv","midflight","pok","nest-report"]
sonic-leader.deny    = ["edit_file","write_file"]
sonic-coder.allow    = ["bash","proc","filesystem","time","self","help","context","shared_meta_kv","pok","nest-report"]
sonic-coder.deny     = []
sonic-reviewer.allow = ["bash","filesystem","time","self","help","context","pok","nest-report"]
sonic-reviewer.deny  = ["proc","instance","service","midflight","shared_meta_kv","infra","mcp","secrets","edit_file","write_file"]
sonic-tester.allow   = ["bash","proc","filesystem","time","self","help","context","pok","nest-report"]
sonic-tester.deny    = ["instance","service","midflight","shared_meta_kv","infra","mcp","secrets"]
# ---- END FROZEN [sonic-tools-v1] ----
```

**Normalization (mechanically required):** absent `tools.deny`/`tools.allow` keys normalize to `[]` before any comparison; `sonic-coder` carries `deny: []` BY OMISSION (the key is absent in its meta.json) — a naive `== []` against a missing key KeyErrors, so pack assertions must normalize.

**Effective spawn sets (W3 A3 expectation):** `sonic-leader` = `{sonic-coder, sonic-reviewer, sonic-tester, giter}` — no allowed category maps to a `TOOL_REQUIRED_AGENTS` row (`_auth.py:35-59`), so ZERO implicit members; `sonic-coder` / `sonic-reviewer` / `sonic-tester` = `∅` (empty `team_members`, no agent-backed category allowed). Reviewer residual: read-only `bash` is prompt-enforced, not tool-enforced (§3.3, A5b).

---

## 4. Prompt Artifact Content Contracts

Each of the 16 `.md` files: raise the bar on *contract*, not prose. Below are the MUST-CONTAIN bullets. Freeze blocks are reproduced verbatim from §4.0.

### 4.0 FROZEN STRINGS (single source — restate character-for-character)

```text
# ---- FROZEN [sonic-blast-radius-v1] ----
daemon/graph.py
daemon/manager.py
daemon/config.py
daemon/api.py
daemon/services/**
daemon/repositories/**
daemon/tools/**
daemon/migrations/versions/**
daemon/sources/**
daemon/mcp/**
daemon/routers/**
scripts/**
agents/**/*.md
agents/.prompt-artifacts.sha256
# ---- END FROZEN [sonic-blast-radius-v1] ----
```
Derivation: C:§1f core-path globs (`daemon/graph.py`, `manager.py`, `services/**`, `repositories/**`, `api.py`, `config.py`, `tools/**`) + migrations (`daemon/migrations/versions/**`) + prompt artifacts (`agents/**/*.md`, `agents/.prompt-artifacts.sha256`) + `scripts/**` (release/promote, C:§2d) + steering-authorized extensions `daemon/sources/**`, `daemon/mcp/**` + `daemon/routers/**` (see §9-D8 — an extension beyond the named set, flagged). This block appears VERBATIM in `sonic-leader/workflow.md` AND `sonic-coder/workflow.md` (the coder must self-check before touching a blast path).

```text
# ---- FROZEN [sonic-handoff-v1] ----
HANDOFF_PATH = HANDOFF.md   # worktree-root-relative; the ABSOLUTE path is passed in every dispatch
## Files touched
## What changed
## What is UNTESTED
## Known risks
## How to verify
## Bash-raises used
## Blast-radius self-assessment vs predicate
# ---- END FROZEN [sonic-handoff-v1] ----
```
The 7 section headers are the ENTIRE schema (S2). Markdown sections are chosen over YAML so the reviewer/tester can grep and a human can read the artifact; each section is prose (free-form) but MUST be present even when empty ("none"). **Authoritative casing = this block;** the W3 pack's lowercased listing is a plan alias — the pack's constants derive from this block at implementation (Phase 1 wins).

```text
# ---- FROZEN [sonic-escalation-v1] ----
ESCALATION: <path> matches <glob> — returning commission to Ari (steady-team routing).
# ---- END FROZEN [sonic-escalation-v1] ----
```

```text
# ---- FROZEN [sonic-verdicts-v1] ----
SONIC-VERDICT: PASS
SONIC-VERDICT: NEEDS-REVISION
SONIC-VERDICT: FAIL
SONIC-VERDICT: ESCALATED
REVIEW: PASS
REVIEW: NEEDS-REVISION
TEST: PASS
TEST: FAIL
# ---- END FROZEN [sonic-verdicts-v1] ----
```

```text
# ---- FROZEN [sonic-bash-rule-v1] ----
bash rule: default <=600 s per command; a longer command requires an explicit raise from sonic-leader; hard max 1800 s (the tool-layer cap).
# ---- END FROZEN [sonic-bash-rule-v1] ----
```

### 4.1 `sonic-leader/{soul,rule,workflow,tools_note}.md`

**soul.md** — must contain: peer-orchestrator identity; "I am not the leader and inherit none of its privileges" (no attestation, no static protection, no POK curation, no mcp_rpc/wedge categories); speed mandate (round-trip count is the first-order cost, B:§4); honesty duty (never invent results).

**rule.md** — must contain (each bullet a MUST):
1. **Blast-radius escalation.** If estimated/actual work touches any path in FROZEN `[sonic-blast-radius-v1]`, STOP, do NOT commit, return the commission to Ari using the verbatim `ESCALATION:` line (FROZEN `[sonic-escalation-v1]`), then END TURN. Ari's front-door judgment sits above this gate (S3).
2. **One-wave fan-out.** Dispatch `sonic-coder` alone → END TURN. On the coder's report: verify `HANDOFF.md` exists and carries all 7 headers → spawn `sonic-reviewer` **and** `sonic-tester` in ONE wave, each with the ABSOLUTE handoff path in `context` → END TURN.
3. **Async ONLY.** Never `invoke_agent_and_wait` (default 300 s terminates the child, B:§4/`daemon/utils.py:670-686`). All coordination is spawn → END TURN → wake-on-report.
4. **Max-1 re-dispatch.** On `NEEDS-REVISION`/`FAIL`, re-dispatch `sonic-coder` ONCE with both verdict blocks attached; after that, report `SONIC-VERDICT: FAIL`.
5. **Pre-approved giter.** Carry the commit/merge pre-approval in the giter dispatch envelope (B:§6: giter rule.md approval round-trips are latency, not capability) — "pre-approved: commit + `merge --no-ff` + worktree cleanup per Worktree Mode; no further approval round-trip".
6. **Lightweight writes.** Write ONLY: dispatch messages, the shared-meta KV census rows (optional), and the final report. Zero kb-writer spawns, zero `experience()` calls, zero planning-dir ceremony (S8).
7. **(d) report-scrutiny guidance** — REQUIRED by `tests/unit/test_report_integrity_prompts.py` (non-empty `team_members` ⇒ parent agent). Must contain the literal substrings `[REPORT SANITY:`, `interim, not completion`, and `send_message` (case-insensitive).
8. Ends with the byte-identical `## Reporting Anomalies` duty block (see §4.5).

**workflow.md** — must contain, in order:
1. **Intake:** (a) if a path in the FROZEN blast block is implicated → escalate (verbatim line, END TURN); (b) estimate effort — if the coder-estimate exceeds ~90 min, escalate (125-min task cap is real clock, `config.py:2096`, C:§2c; B:§2c); (c) Ari front-door deference (Ari already judged it sonic-eligible; never re-litigate selection).
2. **The FROZEN `[sonic-blast-radius-v1]` block, verbatim.**
3. **State machine:** `INTAKE → PREP(giter worktree, pre-approved) → DISPATCH coder → END TURN → VERIFY handoff → ONE-WAVE review+test → END TURN → (PASS) giter commit → END TURN → REPORT | (NEEDS-REVISION/FAIL) one re-dispatch → re-verify once → REPORT`.
4. **Handoff convention:** FROZEN `[sonic-handoff-v1]`; the path is `<task-worktree>/HANDOFF.md`; pass the ABSOLUTE path in every dispatch `context` (task-context truncates at 4000 chars — pass the path, never the body; blueprint `_TASK_CONTEXT_MAX_CHARS=4000`).
5. **Commit:** only after both verdicts PASS; via `giter`; never direct git.
6. **Final report format:** first line one of FROZEN `[sonic-verdicts-v1]` `SONIC-VERDICT:` lines; then ≤ 50 lines: what shipped, files touched, verdicts from reviewer/tester, bash-raises consumed, residual risk, and (if escalated) the verbatim `ESCALATION:` line.

**tools_note.md** — must contain: `instance` = spawn/send (async mechanics; `send_message` revives COMPLETED/TERMINATED/ERROR/FAILED — B:§4); `shared_meta_kv` = optional `wt.active.*` census rows (leader pattern, `agents/leader/workflow.md:94`); `bash` = read-only inspection of the worktree; **no** `edit_file`/`write_file` (denied), **no** `system_upgrade`/`mcp_rpc`/`wedge-capture`/`secrets` (absent); the FROZEN bash rule line.

### 4.2 `sonic-coder/{soul,rule,workflow,tools_note}.md`

**soul.md** — one-shot executor identity: "I run ONE sustained turn and do not wait for anyone"; completeness-honesty duty (never mark UNTESTED as done); "I never commit".

**rule.md** — must contain:
1. **One-shot mandate.** Explore → code → self-test → fix → write handoff → final report, all in ONE turn. `recursion_limit_multiplier: 7` (2100 steps) is the budget; the 125-min wall clock binds first — if the fix needs more, finish the current coherent unit and hand off.
2. **FROZEN bash rule** (`[sonic-bash-rule-v1]`), strengthened to prompt-realized only: no per-agent bash default exists in the tool layer (C:§4d); the only hard cap is 1800 s (`daemon/tools/bash.py:1238, :1268`).
3. **Blast self-check.** Before touching any path in FROZEN `[sonic-blast-radius-v1]` → STOP, write `Blast-radius self-assessment vs predicate` accordingly, and report `SONIC-VERDICT: ESCALATED` upward; do not write blast-path files.
4. **Write discipline.** Write ONLY: the code change, `HANDOFF.md`, and the final report. No `.agents/` ceremony, no kb-writer, no `experience()` (S8). **Never commit** (giter owns commits).
5. **Completeness gate.** `## What is UNTESTED` and `## Known risks` must be honest — an untested change stated as tested is a review failure.
6. Ends with the byte-identical duty block (§4.5).

**workflow.md** — must contain:
1. The FROZEN `[sonic-handoff-v1]` block VERBATIM (the 7 headers + `HANDOFF_PATH`) — the AC greps these exact strings.
2. The FROZEN `[sonic-blast-radius-v1]` block VERBATIM.
3. The step order (explore → change → scoped self-test → handoff → report), each step's exit condition.
4. The `HANDOFF.md` write location: worktree root, exactly `HANDOFF.md`.
5. The FROZEN bash rule line.
6. Report format: `SONIC-VERDICT: PASS|FAIL|ESCALATED` first line + ≤ 40 lines.

**tools_note.md** — `bash`/`proc` (background for long test runs; `proc_*` for anything > a few seconds), `filesystem` (read+write), `shared_meta_kv` (optional progress key), no `instance` (leaf — cannot spawn), no `snapshot_*` (v1).

### 4.3 `sonic-reviewer/{soul,rule,workflow,tools_note}.md`

**soul.md** — read-only reviewer identity; "I verify the claim in the handoff, not the author's confidence"; verdict-first discipline.

**rule.md** — must contain:
1. **Consume the handoff.** Read `<abs>/HANDOFF.md` FIRST; review ONLY the files in `## Files touched` plus their direct callers if named.
2. **Read-only in the write sense.** No writes, no spawns; `bash` is granted for READ-ONLY inspection only — write tools are denied and the rule is the one prompt-enforced residual (§3.3).
3. **Verdict-bearing, one line first:** `REVIEW: PASS` or `REVIEW: NEEDS-REVISION`, then ≤ 25 lines of findings, each with `path` + what is wrong + what would fix it. No style nits unless they hide a defect.
4. **Blast check.** If any `## Files touched` entry matches FROZEN `[sonic-blast-radius-v1]` → `REVIEW: NEEDS-REVISION` with reason `blast-radius: <path> matches <glob>` (the leader escalates).
5. Ends with the byte-identical duty block (§4.5).

**workflow.md** — the read order (handoff → files touched → verify each `## How to verify` claim by reading code), the verdict format, the ≤ 25-line cap, the explicit "same handoff path" convention (`HANDOFF.md` at the worktree root, absolute path in dispatch context), and the READ-ONLY bash rule VERBATIM: "bash = read-only use only: `git status|diff|log|show`, grep/rg, file inspection; NEVER mutate files, NEVER run tests/packs (sonic-tester owns execution), NEVER contact the live daemon/DB".

**tools_note.md** — `filesystem` read tools (`read_file`, `grep_files`, `glob_files`, `list_directory`) plus `bash` for READ-ONLY inspection (`git status|diff|log|show`, grep/rg, file inspection — never mutate, never run tests/packs, never contact the live daemon/DB); `proc`, `instance`, `service`, `midflight`, `shared_meta_kv`, `infra`, `mcp`, `secrets`, `edit_file`, `write_file` are denied; `pok`/`nest-report` ambient.

### 4.4 `sonic-tester/{soul,rule,workflow,tools_note}.md`

**soul.md** — scoped-test identity; "I test the change's blast radius, not the world"; honest-red duty.

**rule.md** — must contain:
1. **Consume the handoff** (same path convention as reviewer), then run tests SCOPED BY BLAST RADIUS — quote `.agents/tester/rules/ensure.md:5` (scoped-by-blast-radius doctrine; the full list only for big/critical/architecture changes).
2. **Verdict-bearing:** `TEST: PASS` or `TEST: FAIL` first line; then the packs run, per-pack result counts, and the failing node ids.
3. **RESULTS file only when load-bearing:** `<worktree>/.agents/tester/RESULTS/sonic-<task-id>-results.md` — a gate-relevant, evidence-bearing run. Routine green runs need only the in-report verdict (S8).
4. **Write discipline:** writes limited to `.agents/tester/RESULTS/` and `/tmp`; never edit code (report FAIL instead).
5. Ends with the byte-identical duty block (§4.5).

**workflow.md** — same handoff path convention; pack selection by blast radius; `timeout`-wrapped pack invocations (C:§2b — 366/367 pack scripts carry `timeout`); QUARANTINE-aware adjudication (a documented pre-existing red does not fail the run, C:§2c); verdict + RESULTS naming.

**tools_note.md** — `bash`/`proc` (test execution; `proc_*` for long runs), `filesystem` (read + RESULTS writes), no `instance`/`service`/`midflight`/`mcp`/`infra`/`secrets`.

### 4.5 Duty block (byte-identical, LAST top-level section, in ALL FOUR `rule.md`)

From `tests/unit/test_anomaly_report_duty_prompts.py:63-71` — `DUTY_BLOCK` must terminate each rule.md exactly (including the final newline), separated from the previous section by a blank line, and must add ZERO Cardinal entries:

```
## Reporting Anomalies

When I see something abnormal in my work — a defect, a contradiction, a stuck step — I report it
with `report_anomaly`: what kind of problem, what I saw, how serious, what I think caused it. One
report per distinct problem, never the same one twice. The tool collects the evidence itself.
```

The pack's M-P5-4 mutation leg ("add a NEW agent dir with no duty line → RED") makes this MANDATORY, not stylistic. Same template text as `agents/ari/rule.md:296-300`.

---

## 5. Ari `rule.md` Note — Exact Anchor, Verbatim Text, Ceremony

### 5.1 Anchor
Inside `### 🚨 CRITICAL: TASK TRIAGE — Do Directly vs. Delegate` (currently lines 51–99), insert a NEW subsection IMMEDIATELY AFTER the line:

```
- "Help me set up project X" — could be quick or huge; **ask** if unclear
```

and BEFORE the following `---` separator (currently line 100). Use the quoted bullet as the stable anchor (line numbers drift).

### 5.2 Note text (VERBATIM — draft)

```markdown
### Sonic Lane (Opt-In)

Delegation defaults stay with the steady team (`leader`). The sonic lane
(`sonic-leader`) is opt-in by POSITIVE selection only — dispatch to it when
the user asks for it by name, or for small-to-medium, low-coupling work where
speed is the point. Everything else defaults to `leader`. If the work turns
out to sit on load-bearing paths, sonic-leader escalates the commission back
to me with its reason, and I re-route it to `leader`.
```

**Terminology safety (mechanically checked):** the text contains ZERO case-insensitive `plane` occurrences — `grep -oi plane` over the note must be empty (`tests/unit/test_ari_pm_terminology_pins.py:88-100`). It adds no `## Cardinal` section and no bare `agents/` cross-reference.

### 5.3 Ceremony (MANDATORY — all four steps)
1. **Edit** `agents/ari/rule.md` (§5.1–5.2).
2. **Re-pin the seal.** `tests/test_chart_image_delivery_e2e.py` → `SEALED_SHA_BASELINES["agents/ari/rule.md"]` (currently `fc0a7542157d79d16011a48d7e79d31d33fe1376ca5fc5c694d47e670bcfdf10`, verified equal to the worktree bytes). Replace with `sha256sum agents/ari/rule.md` output; add the file's documented attribution comment (one comment line naming this commission + "ain't a behaviour change"). The dict MUST stay 35 rows (`test_anomaly_report_duty_prompts.py:517-522` `test_the_seal_map_has_35_rows`). `agents/ari/workflow.md` seal (`a0f5a382…`) is UNTOUCHED — do not re-stamp it (anti-blanket invariant, `test_the_untouched_rows_keep_their_base_values`).
3. **Terminology pins stay green with NO test edit**: rule.md keeps exactly ONE `plane` (the backing note at :239) and the backing note token `backed by Plane`. The new note adds none.
4. **Prompt-baseline count** is UNCHANGED by this edit (same row, new hash) — the +16 rows come from the 4 new agents (§6).

### 5.4 Mechanical-vs-not test (decides ceremony cost)
The Ari edit is **mechanical** (proceed) iff ALL hold:
- (a) the inserted text is the §5.2 block verbatim, at the §5.1 anchor;
- (b) the only pinned-surface changes are: the seal row for `agents/ari/rule.md` (1 row) + the `ari/rule.md` baseline hash row (1 row);
- (c) `grep -c 'plane' (case-insensitive)` on the edited rule.md == 1;
- (d) the four green packs in §7 A21–A22 stay green.

If ANY fails (e.g., a test pins additional `ari/rule.md` content and would need a semantic edit), the ceremony is **NOT mechanical** → **fallback: DROP the Ari note to a fast-follow** (record the deviation in the morning report; Phase 1 ships the 4 agents only). This is a recorded-deviation path, not a silent skip.

---

## 6. Prompt-Artifact Baseline Regen

**Command (from the worktree root):**
```
python3 scripts/protection/generate_prompt_artifact_baseline.py
```
It writes `agents/.prompt-artifacts.sha256` deterministically (sorted, `<sha256>  agents/<dir>/<artifact>`), single-sourced from `VERSIONED_PROMPT_ARTIFACTS` (`daemon/tools/prompt_artifacts.py:119-141`), which is `soul.md, rule.md, workflow.md, tools_note.md, tools.md, skill.md, memory.md, growth.md`.

**DERIVED expected post-regen line count (assert, never hardcode blindly):**
```
197 = 181 (current) + 4 agents × 4 artifacts (soul, rule, workflow, tools_note)
```
Derivation invariants that MUST hold for 197 to be the answer:
- each sonic dir carries EXACTLY 4 versioned artifacts (NO `memory.md`, `growth.md`, `tools.md`, `skill.md`) — AC A20b;
- `skills-template/*.md` is NOT versioned (verified: 0 such rows in the current baseline) — so the NO-skills-template decision does not disturb the count;
- `agents/ari/rule.md` changes hash only (no line added) → +0 lines;
- the regen also re-stamps 7 PRE-EXISTING drift rows (verified `--check`: 8 modified) — values change, count does not (§9-D13).

**Line-delta assertion (mechanical, no git needed):**
- snapshot the baseline before editing (`cp agents/.prompt-artifacts.sha256 /tmp/prompt-artifacts.before`);
- after regen: `grep -c '^https\?\|^[0-9a-f]\{64\}  agents/' /tmp/... ` → new count 197; diff shows exactly **16 added rows** (all `agents/sonic-*/`) and exactly **8 modified rows** = 1 (`agents/ari/rule.md`, this lane) + **7 PRE-EXISTING drift rows** repaired by the regen (§7 A16 / §9-D13), **0 removed**. Row VALUES change; the count does not.

**Expected-delta consumers to keep in lockstep** (§7 A23–A25): `tests/unit/agents/test_aegis_security_family.py` `EXPECTED_BASELINE_ROWS` 181→197; `tests/unit/test_anomaly_report_duty_prompts.py` `BASELINE_EXPECTED_ENTRIES` 177→197.

---

## 7. Acceptance Criteria (mechanical, one command each)

Run from the worktree root. `jq` or `python3 -c` is acceptable; `grep -F` is exact-string.

| AC | Check | Command / assertion | Pass |
|----|-------|--------------------|------|
| **A1** | JSON parses; `id` == dir name ×4 | `for d in sonic-leader sonic-coder sonic-reviewer sonic-tester; do python3 -c "import json,sys;m=json.load(open('agents/$d/meta.json'));assert m['id']=='$d'"; done` | exit 0 |
| **A2** | Exact teams | leader `team_members` == `["sonic-coder","sonic-reviewer","sonic-tester","giter"]`; other 3 == `[]` | python3 asserts |
| **A3** | Model lane ×4 | `grep -c '"llm_model": "agentic"' agents/sonic-*/meta.json` → 1 in each | 4/4 |
| **A4** | Multiplier only on coder | `grep -l recursion_limit_multiplier agents/sonic-*/meta.json` == coder only; value == 7 | exactly 1 file |
| **A5** | Allow/deny content (FROZEN `[sonic-tools-v1]`, §3.5) | leader allow excludes `system_upgrade|mcp_rpc|wedge-capture|secrets|job|attestation|council|knowledge|image|chart`; leader deny ⊇ `edit_file,write_file`; **reviewer allow ⊇ `bash`** and reviewer deny ⊇ `edit_file,write_file,instance,service,mcp,secrets`; tester deny ⊇ `instance,service,midflight,secrets`; effective spawn sets per §3.5 | python3 asserts vs the frozen arrays |
| **A5b** | Reviewer read-only-bash rule present | `grep -Fq 'read-only use only' agents/sonic-reviewer/workflow.md` and `grep -Fq 'NEVER run tests/packs' agents/sonic-reviewer/workflow.md` (residual is prompt-enforced, §3.3) | exit 0 |
| **A6** | Grant ordering | every sonic allow ends with `["pok","nest-report"]` (pok immediately before nest-report; nest-report last) | python3 `allow[-2:]==["pok","nest-report"]` |
| **A7** | Handoff 7 headers | `for h in '## Files touched' '## What changed' '## What is UNTESTED' '## Known risks' '## How to verify' '## Bash-raises used' '## Blast-radius self-assessment vs predicate'; do grep -Fq "$h" agents/sonic-coder/workflow.md; done` | exit 0 |
| **A8** | `HANDOFF.md` literal in coder+reviewer+tester | `for f in agents/sonic-coder/workflow.md agents/sonic-reviewer/workflow.md agents/sonic-tester/workflow.md; do grep -Fq 'HANDOFF.md' "$f" || exit 1; done` | exit 0 |
| **A9** | Blast block verbatim ×2 | extract the 14 lines between the `[sonic-blast-radius-v1]` markers in `sonic-leader/workflow.md` and `sonic-coder/workflow.md` and `diff` them to each other and to this spec §4.0 | zero diff |
| **A10** | Escalation format | `grep -Fq 'ESCALATION: ' agents/sonic-leader/rule.md` | exit 0 |
| **A11** | Ari note verbatim | `grep -Fq '### Sonic Lane (Opt-In)' agents/ari/rule.md` | exit 0 |
| **A12** | `plane` count == 1 | `grep -oi plane agents/ari/rule.md \| wc -l` | 1, and `grep -Fq 'backed by Plane' agents/ari/rule.md` |
| **A13** | Ari note adds no plane / no Cardinal | `grep -c '^## Cardinal' agents/ari/rule.md` (unchanged pre/post) | equal |
| **A14** | Baseline rows == 197 | `grep -c '^[0-9a-f]\{64\}  agents/' agents/.prompt-artifacts.sha256` | 197 |
| **A15** | Baseline self-check clean | `python3 scripts/protection/generate_prompt_artifact_baseline.py --check --tree .` | exit 0 |
| **A16** | Baseline delta shape | after regen, diff vs `/tmp` snapshot = **+16 added** (`agents/sonic-*`), **8 modified** = 1 (`agents/ari/rule.md`, this lane) + **7 PRE-EXISTING drift rows** (`developer/rule.md`, `developer/workflow.md`, `developer[v2]/rule.md`, `developer[v2]/workflow.md`, `leader/rule.md`, `leader/workflow.md`, `tester/workflow.md` — vsss-era re-pins never regenerated; §9-D13), **0 removed**; `--check` exits 0 afterwards | exact |
| **A17** | Seal row matches bytes (all 35 rows) | `python3 - <<'PY'` ... parse `SEALED_SHA_BASELINES` from `tests/test_chart_image_delivery_e2e.py` by AST, assert `len(d)==35`, then `hashlib.sha256(Path(rel).read_bytes()).hexdigest()==want` for every row `PY` (equivalent in-test: `pytest tests/test_chart_image_delivery_e2e.py -k phase_d_does_not_modify_sealed_artifacts -q`, if that module imports cleanly) | exit 0 |
| **A18** | Seal map membership unchanged | `pytest tests/unit/test_anomaly_report_duty_prompts.py -k 'seal_map_has_35_rows or re_pinned or untouched_rows or partition'` | all pass |
| **A19** | (d) guidance on leader | `for s in '[REPORT SANITY:' 'interim, not completion' 'send_message'; do grep -Fiq "$s" agents/sonic-leader/rule.md; done` | exit 0 |
| **A20a** | Duty block last, byte-identical ×4 | python3: for each sonic `rule.md`, `text.endswith(DUTY_BLOCK)` with `DUTY_BLOCK` copied from `tests/unit/test_anomaly_report_duty_prompts.py:63-71` | 4/4 |
| **A20b** | Exactly 4 versioned artifacts per sonic dir | `ls agents/sonic-*/{soul,rule,workflow,tools_note}.md \| wc -l` == 16; no `memory.md|growth.md|skills-template|skill-set.yaml` under `agents/sonic-*` | 16 and 0 |
| **A21** | Existing green packs stay green | `pytest tests/unit/test_pok_grant_pins.py tests/unit/test_anomaly_report_grant_pins.py tests/unit/test_report_integrity_prompts.py tests/unit/test_ari_pm_terminology_pins.py tests/unit/tools/test_mission_watch_prompts.py -q` | 0 failed |
| **A22** | Prompt-section integrity | `pytest tests/unit/tools/test_prompt_section_reference_integrity.py -q` | 0 failed |
| **A23** | Duty pack registered | `EXPECTED_TOTAL == 50`, `EXPECTED_WALK == 48`, `BASELINE_EXPECTED_ENTRIES == 197`; remaining red = the FOUR canary-rooted node ids (`test_every_duty_file_carries_the_byte_identical_block`, `test_the_duty_block_is_the_last_top_level_section`, `test_no_duty_file_carries_a_forbidden_token_inside_its_duty_block`, `test_each_duty_file_keeps_its_cardinal_count`) — one root cause, base-attributed | 4 red, all canary |
| **A24** | Grant packs registered | pok: `EXPECTED_TOTAL_META == 50`, `EXPECTED_EXPLICIT == 49`; anomaly grant: `EXPECTED_TOTAL_META == 50`, `EXPECTED_EXPLICIT == 48` | python3 asserts |
| **A25** | Aegis baseline arithmetic | `EXPECTED_BASELINE_ROWS == 197` and its two tests pass | exit 0 |
| **A26** | Snapshot-enabled pin | `test_snapshot_agent_spaces.py::TestEnumerationStability::test_pinned_expected_set` passes with the sorted set `["developer","developer[v2]","sonic-reviewer","sonic-tester","tester"]` | exit 0 |
| **A27** | Zero daemon/migrations delta | no new file under `daemon/migrations/versions/`; no `daemon/**/*.py` modification (diff vs `/tmp` snapshot or `git diff --name-only daemon/` in the implementer worktree) | empty |
| **A28** | Steady-team untouched | no change under `agents/{leader,developer,developer[v2],reviewer,reviewer[v2],tester}/**`; only `agents/ari/rule.md` among pre-existing agent files. NOTE: the regen re-stamps 7 steady-team baseline ROWS (drift repair — the FILES are untouched; §9-D13), so judge this AC on file content, never on baseline-row diffs | empty |
| **A29** | W3 sonic pack | `timeout 300 bash test/packs/sonic_agent_defs_unit_test.sh` (W3-canonical script; registered row in `.agents/tester/PACKS.md`; `sonic_agent_defs` is a plan alias ONLY) | green |

> **Delta gating rule (A23):** the duty pack is **already RED at `ac9849bf7`** (7 failed / 12 passed — see §9-D3). Phase 1 must not GROW that red set. The four count nodes are green again after A23 registration; the canary byte-block node remains (base-attributed, QUARANTINE-class). The per-agent duty-block compliance is proven by **A20a** (independent of the pack, because the pack's loop fails on canary first and would mask a sonic regression — the pack CANNOT prove sonic compliance).

---

## 8. Implementation Order + Verification Loop

1. **Author the 4 meta.json files** (§3) → run A1–A6.
2. **Author the 16 `.md` files** (§4) → run A7–A10, A19, A20a, A20b, A22.
3. **Edit `agents/ari/rule.md`** (§5) → run A11–A13 + the terminology pack (§7 A21).
4. **Re-pin the seal** (§5.3 step 2) → run A17–A18.
5. **Update the FIVE registration points**: duty pack (A23), pok pack (A24), anomaly-grant pack (A24), aegis pack (A25), snapshot-space pin (A26).
6. **Regenerate the baseline LAST** (§6) → run A14–A16.
7. **Run the mandatory suite**: A21–A28 (+ A20a/b, A5b). **Phase-1 exit = A1–A28 green** (A20a/b, A5b as applicable — NOT A29); A29 (the W3 `sonic_agent_defs_unit_test.sh` pack) is the **Phase-2 gate** that unblocks Phase 3, not a Phase-1 closure condition (advisory #6 — removes the circular gate).

**Loop discipline:** fix forward; never weaken an AC; never edit a steady-team file; never touch the canonical checkout. If a blast-radius path must be touched → the Phase 1 lane does not do it (escalate).

---

## 9. Deviations & Evidence Conflicts (LOUD)

**D1 — Leg B vs steering S6 (model lane).** B:§5/§1 recommends mirroring `coder`'s `llm_model: "coding"` for the sonic coder (`coding` → coder/worker/canary/cracker/hacker/sweeper). **Steering S6 overrides: pin ALL FOUR to `"agentic"`.** Binding decision = S6. Reason recorded: the coding lane is dead (provider 402 until 2026-11-17T11:43:43Z, project history) and `agentic` is a legal lane (`config.yaml:96`). **Open decision for the Emperor:** re-pin to `coding` after 2026-11-17 if the lane proves faster. Implemented as `"agentic"` ×4.

**D2 — Duty block mandate NOT in steering (discovered).** `tests/unit/test_anomaly_report_duty_prompts.py` (the nest-anomaly duty pack) walks EVERY `agents/<dir>/meta.json` and requires the byte-identical `## Reporting Anomalies` block as the LAST top-level section; mutation leg M-P5-4 explicitly reds a new agent dir without it. Steering S1–S9 never mention it. **Added as mandatory work**: all 4 `rule.md` files end with the exact `DUTY_BLOCK` (§4.5). This is an evidence-driven addition, not a design change.

**D3 — The duty pack is ALREADY RED at `ac9849bf7`** (verified: `7 failed, 12 passed`; base-attributed, NOT caused by Phase 1). Exact failing node ids:
1. `TestTheDutyWalkDecomposition::test_the_total_is_41` — 46 actual agent dirs vs 45 pinned;
2. `TestTheDutyWalkDecomposition::test_the_walk_is_39_and_the_specials_are_2` — 44 vs 43;
3. `TestTheDutyBlockIsByteIdenticalEverywhere::test_every_duty_file_carries_the_byte_identical_block` — canary;
4. `TestTheDutyBlockIsByteIdenticalEverywhere::test_the_duty_block_is_the_last_top_level_section` — canary;
5. `TestTheForbiddenTokenClosure::test_no_duty_file_carries_a_forbidden_token_inside_its_duty_block` — canary;
6. `TestTheDutyAddsNoCardinalEntry::test_each_duty_file_keeps_its_cardinal_count` — canary;
7. `TestThePromptArtifactBaseline::test_the_baseline_matches_the_real_bytes` — 181 rows vs 177 pinned (+ the 8 drift rows, D13).

Two root causes: **4 canary-rooted nodes** (`canary/rule.md` lacks the duty block) and **3 stale-count nodes** (45/43/177 vs actual 46/44/181). **Phase 1 does NOT fix canary (steady-team prompt artifacts untouched)** and does not re-stamp unrelated rows. The tester gate adjudicates these as base-attributed. **Phase 1's ACs are delta-based (A20a/b, A23–A26):** the count registration + regen greens the 3 stale-count nodes; the 4 canary-rooted nodes remain the only red. Consequence: the pack cannot prove sonic compliance (its loop fails on canary first) → A20a is an independent check.

**D4 — "PROMPT-BASELINE count shift" has a GREEN concrete consumer.** `tests/unit/agents/test_aegis_security_family.py:2615` `EXPECTED_BASELINE_ROWS = 181` is GREEN at base and WILL red on regen. Steering mentioned the shift generically; this spec names the exact edit (181 → 197) as required ceremony (A25).

**D5 — Grant packs pin ORDERING (steering silent).** `pok` must sit immediately before `nest-report`, and `nest-report` must be LAST in `tools.allow` (`test_pok_grant_pins.py:126-152`, `test_anomaly_report_grant_pins.py:206-226`). Both packs are GREEN at base. Added as A6.

**D6 — (d) report-scrutiny guidance is MANDATORY for `sonic-leader`.** `tests/unit/test_report_integrity_prompts.py:236-269`: any agent with non-empty `team_members` must carry `[REPORT SANITY:`, `interim, not completion`, `send_message`. Green at base; a new parent agent fails without it. Added as A19. (The (e) opening-discipline set is a locked list that excludes sonic — not required.)

**D7 — `snapshot_enabled` decision has an exact-list pin.** `tests/unit/test_snapshot_agent_spaces.py::test_pinned_expected_set` pins exactly `["developer","developer[v2]","tester"]`. Setting the flag true on `sonic-reviewer`/`sonic-tester` requires a 2-string update (A26). Decision: **TRUE on reviewer+tester** (v2 relay enabler, inert in v1 because R15 is OFF and v1 passes no `snapshot_id`; B:§3/B:§4). **Dispatcher-adjudicated at plan review (Q2): ACCEPT TRUE** (v2-ready; the pin update is the 2-string A26 edit). The documented false-fallback stands exactly as written: if the pin shape proves non-mechanical at implementation → flip both flags to `false` and record the deviation.

**D8 — Blast-radius extension beyond the named set.** FROZEN `[sonic-blast-radius-v1]` includes `daemon/routers/**`, which is neither in C:§1f's core-path list nor in the steering's named extensions (`daemon/sources/**`, `daemon/mcp/**`). Justification: C:§1d shows `daemon/routers/` at 146 defect-set touches (material cluster), and the routers are the HTTP API surface (blast-radius class). **Dispatcher-adjudicated at plan review (Q1): KEEP** — the gate is escalate-only, so a superset errs toward routing-to-steady-team (a false positive costs one escalation) while a false negative risks a load-bearing API edit. The leader may flip at the plan gate (one-line change; it LOOSENS the gate) — surfaced in the morning report. D9's documented exclusions are KEPT as-is (auditable predicate). The FROZEN block itself is unchanged.

**D9 — §1f core-path list omits `daemon/routers/**` and `daemon/constants.py`; I include routers, exclude constants.** Deliberate exclusions (documented so the predicate is auditable): `daemon/constants.py`, `daemon/checkpoint_adapter.py`, `daemon/cancellation.py`, `daemon/compaction.py`, `frontend/**`, `docs/**`, `tests/**`, `test/**`, `agents/**/meta.json`. Rationale: not in §1f's named core list; frontend/docs/tests are the normal sonic product surface; meta.json edits are a separate ceremony. If a future incident shows constants.py-class churn, add one line.

**D10 — v1 has NO snapshot relay (S2).** Leg B:§3 shows a snapshot chain exists (self-capture from sonic-coder's checkpoint + explicit `snapshot_id` relay; the vsss space scoping silently kills automatic cross-agent discovery — `snapshot_search_service.py:36-45`). Steering S2 elects the fixed-schema HANDOFF FILE for v1 and defers the snapshot path to v2. Consequence: `sonic-coder` does NOT carry `snapshot_create`, and the handoff is a plain worktree file. No deviation — recorded so nobody "adds" the snapshot transform to v1.

**D11 — Bash ≤10 min is prompt-realized ONLY.** C:§4d: no per-agent/per-instance bash default exists; the only enforcement is the 1800 s per-call cap (`daemon/tools/bash.py:1238`, `:1268`) and the RAM guard. The ≤600 s default is a prompt rule with a leader-approval raise path; a mechanical default would be a daemon change (out of scope / zero-code mandate). Recorded per C:§4d.

**D12 — Baseline derivation depends on 4 artifacts per dir.** Some agents carry 5 (`leader`, `maintenancer`, `tester`, `_mother`, `_baby_template` have `growth.md`). Sonic dirs must carry exactly 4 → A20b. If anyone adds `memory.md`/`growth.md`/`tools.md`/`skill.md` to a sonic dir, the 197 assertion breaks (by design — that is the tripwire).

**D13 — The baseline regen repairs 8 PRE-EXISTING drift rows (disclosure).** Verified at `ac9849bf7`: `generate_prompt_artifact_baseline.py --check` reports **8 modified** rows — `agents/ari/rule.md` (this lane's note) plus 7 pre-existing (`agents/developer/rule.md`, `agents/developer/workflow.md`, `agents/developer[v2]/rule.md`, `agents/developer[v2]/workflow.md`, `agents/leader/rule.md`, `agents/leader/workflow.md`, `agents/tester/workflow.md`; vsss-era re-pins never regenerated the baseline). The in-scope regen necessarily repairs them: **row VALUES change; the row COUNT does not** (181 → 197 counts only the +16 sonic rows). This is a side effect of an in-scope action, not scope expansion — disclosed so 8 modified rows are not misread as scope creep, and so A28 is judged on FILE content, never baseline rows. `--check` rc=0 after regen is the repair proof.

---

## 10. Values Needing Implementation-Time Verification

| ID | Value | Verify | If wrong |
|----|-------|--------|----------|
| V1 | `tools.deny` semantics | deny entries are TOOL NAMES, not categories (`_auth.py:210-248`; `deny_spawn` is the category mechanism) | move category names to omission / `deny_spawn` |
| V2 | `default_queue: "system_parallel_queue"` on sonic-leader | the queue name is registered (aegis/ari precedent) | omit the key (default queue) |
| V3 | `no_force_explore` on reviewer | no precedent in `critic`/`tester` (both lack it); harmless if accepted | drop the key |
| V4 | reviewer `bash` | **decided: granted** (dispatcher-adjudicated at plan review, Q3) — `bash` in allow, removed from deny; read-only use is prompt-enforced (A5b) | n/a (resolved) |
| V5 | tester `write_file` allowed | RESULTS writes auditable; workflow bounds them | deny `edit_file`/`write_file`, write RESULTS via bash |
| V6 | snapshot pin update (A26) | exact expected list + comments | if the pin has more assertions, extend carefully |
| V7 | duty/grant pack count constants | exact current values (§7 A23/A24) | adjust to true population (46→50 etc.) |
| V8 | W3 `sonic_agent_defs` pack | pinned: `test/packs/sonic_agent_defs_unit_test.sh` (+ its `.agents/tester/PACKS.md` row); `sonic_agent_defs` is a plan alias ONLY; ACs A1–A29 are the reference assertion set | n/a (pinned) |
| V9 | seal `sha256sum` value | recompute from real bytes after the edit | never copy a value from this doc |

---

## 11. Ledger ↔ Body Agreement (self-check)

- Roster: 4 new dirs × (meta.json + 4 `.md`) = 20 new files — matches §2 rows 1–20.
- Existing files changed: `agents/ari/rule.md` (1), baseline (1), SIX test packs (chart-e2e seal, duty counts, pok-grant counts, anomaly-grant counts, aegis baseline rows, snapshot-space pin) = **8** — matches §2 rows 21–28.
  **Note:** §2 rows 1–28 = 20 new files + 8 edits/regen.
- Baseline: 181 → 197 (derivation §6) — matches A14/A16 and D4/A25/D3/A23 constants; row delta = +16 added, **8 modified** (1 note + 7 pre-existing drift, §9-D13), 0 removed.
- Counts: agent dirs 46 → 50; walk 44 → 48 — matches A23/A24.
- Frozen strings appear verbatim in §4.0 and are referenced (not re-typed with drift) in §4.1–4.4 and A7–A10.

---

## Appendix A — Mandate Inventory (why each ceremony edit exists)

| Pack (state at base) | Mandate | Effect on Phase 1 |
|---|---|---|
| `tests/unit/test_anomaly_report_duty_prompts.py` (RED: 7/12) | duty block last + byte-identical; counts 45/43/177 | 4 rule.md end with DUTY_BLOCK; counts → 50/48/197; canary node stays red |
| `tests/unit/test_pok_grant_pins.py` (GREEN) | `pok` in every non-empty allow, immediately before `nest-report`; 46/45 | allow tails `pok,nest-report`; counts → 50/49 |
| `tests/unit/test_anomaly_report_grant_pins.py` (GREEN) | `nest-report` last in every non-empty allow; 46/44 | same tails; counts → 50/48 |
| `tests/unit/test_report_integrity_prompts.py` (GREEN) | (d) snippets for non-empty-team agents | sonic-leader carries (d) |
| `tests/unit/agents/test_aegis_security_family.py` (GREEN) | baseline rows == 181 | → 197 |
| `tests/unit/test_snapshot_agent_spaces.py` (GREEN) | exact `snapshot_enabled` set | + `sonic-reviewer`, `sonic-tester` |
| `tests/test_chart_image_delivery_e2e.py` (GREEN) | 35-row seal map, 0-stale | re-pin ari/rule.md only |
| `tests/unit/test_ari_pm_terminology_pins.py` (GREEN) | `plane` counts 1/0/0 + backing note | note adds no `plane` |
| `tests/unit/tools/test_mission_watch_prompts.py` (GREEN) | mission-vocabulary needles in ari prompts | additive note leaves them intact |
| `tests/unit/tools/test_prompt_section_reference_integrity.py` (GREEN) | no bare `agents/` cross-refs / glue | author prompts cleanly |

Base-state evidence: `pytest` over the 5 grant/terminology/report-integrity packs = **115 passed, 24 skipped**; duty pack = **7 failed, 12 passed** (pre-existing).

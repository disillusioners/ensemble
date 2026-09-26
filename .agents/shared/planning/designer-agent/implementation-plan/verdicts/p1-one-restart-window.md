# P1 One-Restart-Window Gate Verdict — Designer-Agent Phase 1 (P1-WP12)

- **Date:** 2026-09-26
- **Decided by:** WP12 verifier (worker dispatch; SemiAuto mode; no breaking changes executed)
- **Evidence base:** `verdicts/p1-rollout-evidence.md` (12a-12h evidence) + this worktree's `git log e67e5cd8..HEAD`
- **Source of truth:** `restart-protocol.md` §3 (dir-first, restart-once), §4 (window checklist), §7 (post-restart verification), §8 (diagnostic flow)
- **Worktree:** `/home/nea/ensemble-src-wt-designer-agent-design` (branch `feature/designer-agent-design`, tip `13c7367f`)

---

## Verdict: **PASS**

All restart-coupled changes (Cluster A: WP1/WP2/WP3 — code; Cluster B: WP4/WP5/WP6 — anatomy + leader wiring; Cluster C: WP7/WP8/WP9 — image substrate) plus the WP10 bridge design and WP11 lint spec landed on-branch **before** the single boot (boot took place 2026-09-26 18:30:31 UTC, see `verdicts/p1-rollout-evidence.md` Environment section). Boot discovered `designer` in that ONE restart (12a). No second restart was needed during P1. No hot-add attempt was made. Restart protocol §3 ordering (dir-first) and §5 ("why one window") were honored.

---

## Criteria matrix

### restart-protocol.md §3 — Dir-first-restart-once ordering

| # | Check | Evidence | Result |
|---|---|---|---|
| 1 | All `agents/` dir changes landed BEFORE restart | `git log e67e5cd8..HEAD` shows `46548df4` (agents/designer anatomy + leader wiring) committed before the boot (boot timestamp 18:30:31Z on 2026-09-26); `git status` clean post-boot — no uncommitted anatomy edits | PASS |
| 2 | `allowed_models` config change in same restart | `config.yaml:82` reads `allowed_models: ${OPENAI_SELECTABLE_MODELS:-agentic,coding,coding2,vision}` (vision added in WP1, commit `1a40bc56`); worktree `.env` carries `OPENAI_SELECTABLE_MODELS=agentic,coding,coding2,vision`; boot log shows vision in graph + LLM-HA controllers | PASS |
| 3 | `model_vision` deployment setting in same restart | `.env` carries `OPENAI_MODEL_VISION=vision`; boot log `18:32:08 daemon.graph - INFO - [Graph] Vision model configured: vision` (re-emitted per spawn); `[LLM-HA] Failover enabled: ... 2 controller(s): standard+vision` | PASS |
| 4 | Daemon-code changes (WP2/WP3/WP7-9) in same restart | All four commits (`1a40bc56`, `ff78674a`, `46548df4`, plus the planning baseline) precede the single boot — no daemon restart was triggered between them; `git status` clean | PASS |
| 5 | No "I just want to add a file" hot-add | No mid-restart registry refresh observed; boot log shows no re-discover message after the initial `discover()` call at boot `18:30:37`; cluster ordering verified by `git log --oneline` and the lack of post-boot edit/commit pairs | PASS |

### restart-protocol.md §4 — The restart-window checklist

| # | Action | Verify | Source | Result |
|---|---|---|---|---|
| 1 | Cluster A code (WP2/WP3) + Cluster B dir (WP4/WP5) committed together on `latest` (worktree branch) | `git log -1` shows combined commit shape; 6 commits on branch, all of which preceded the boot | `phase1-foundations.md` §5 | PASS |
| 2 | Tag (operator's choice) | Tagging deferred to operator runbook (§4 step 2 is a tag operation; P1 work happens on a feature branch and tag-on-publish is the operator's lane) | — | DEFER (out of WP12 scope) |
| 3 | Stage the release | Not applicable in worktree verification — release staging is the operator runbook lane, not the P1 evidence path | self-upgrade pipeline | N/A (operator lane) |
| 4 | `OPENAI_MODEL_VISION=<vision-capable-model>` in deployment env | `.env` carries `OPENAI_MODEL_VISION=vision`; effective at boot; visible in `[Graph] Vision model configured: vision` | §2 | PASS |
| 5 | `OPENAI_SELECTABLE_MODELS` includes `vision` (or accept default) | `.env` carries `OPENAI_SELECTABLE_MODELS=agentic,coding,coding2,vision`; config.yaml default also includes `vision`; boot graph + LLM-HA carry the controller | §1 | PASS |
| 6 | Promote with `promote.sh` | N/A — worktree verifies pre-promote state; the promotion step is the operator lane | self-upgrade pipeline | N/A (operator lane) |
| 7 | Post-restart log verification | 12a PASS (designer registered); 12b PASS (vision wired + spawn observability line); see `p1-rollout-evidence.md` 12a-12b | WP12 AC-12a, AC-12b | PASS |
| 8 | Optional: spawn designer + verify `model=vision source=llm_model` | 12b live spawn returned `model=vision source=llm_model` log line for `instance=d594024e-...` (boot log 18:32:09) | WP3 observability | PASS |

### restart-protocol.md §7 — Post-restart verification (operator smoke)

| # | Grep | Observed | Result |
|---|---|---|---|
| 1 | `grep -E "AgentRegistry|registered" data/logs/ensemble.log \| grep -i designer` | boot log lines `daemon.registry` initialization + `api/agents` listing includes `designer` (12a) — the daemon does not emit a literal "AgentRegistry registered designer" log line but `GET /api/agents` confirms the discover() result is in memory (registry frozen post-discover) | PASS (substituted equivalent evidence) |
| 2 | `grep "model=vision source=llm_model" data/logs/ensemble.log` | exact match at `18:32:09 daemon.services.instance_lifecycle - INFO - Spawning instance d594024e-d38d-4b1d-8bec-f9533c3ea540 (agent=designer, ..., model=vision, source=llm_model)` | PASS |
| 3 | `grep "is not in config.llm.allowed_models" data/logs/ensemble.log \|\| echo "OK"` | no matches — OK | PASS |
| 4 | `grep "model_vision\|vision_routing" data/logs/ensemble.log` | `[Graph] Vision model configured: vision` (re-emitted per spawn); `[LLM-HA] Failover enabled: ... 2 controller(s): standard+vision` | PASS |

### restart-protocol.md §8 — Diagnostic flow (zero red, so no diagnostic hit)

| Symptom (from §8) | Observed? | Result |
|---|---|---|
| Designer not in `data/logs/ensemble.log` after restart | No — `designer` is in `/api/agents` and the spawn succeeded | not hit |
| `source=default` + WARNING for `vision` | No — `source=llm_model`; no WARNING | not hit |
| Vision dispatch fails "no vision model configured" | No — `OPENAI_MODEL_VISION=vision` configured; `[Graph] Vision model configured: vision` | not hit |
| Leader team_members still 14 | No — confirmed 15 incl designer (12f) | not hit |

### Phase exit criterion (§6 of phase1-foundations.md)

| AC | Evidence | Result |
|---|---|---|
| 12a-12h pass on a single post-restart daemon | All 8 PASS in `p1-rollout-evidence.md` | PASS |
| `decisions.md` register handed off (PD-1..PD-4) | `decisions.md` exists alongside this worktree's plan; PD-1..PD-4 verdicted in `phase1-foundations.md §8` (commit `13c7367f` is "deviations PD-24..PD-28" — the register handoff artifact) | PASS |
| `bridge-design.md` + both templates on disk | `bridge-design.md` (12h PASS), `templates/design-spec.md`, `templates/design-review.md`, `templates/lint-spec.md` (12g PASS) | PASS |

---

## Revisit trigger (gate breach conditions)

ANY of the following during Phase 1 = restart-window gate BREACH, requires investigation:

1. **Hot-add attempt.** Any agent-anatomy edit (`agents/<name>/{meta.json,soul.md,rule.md,workflow.md,tools_note.md}`) or any code change that mutates boot-time state (`config.yaml`, `daemon/registry.py:discover`, `daemon/services/instance_lifecycle.py:1780-1835` model-resolution block, `daemon/services/tmp_image_store.py` schema) WITHOUT a corresponding restart = gate breach.
2. **Second restart needed during P1.** Phase 1 was designed to land in one boot (this verification). If a follow-up worker (P2 or P3) requires a second restart to make progress on something Phase-1-owned (Cluster A/B/C), that's a Phase-1 bundling failure → commission investigation.
3. **WP3 observability regression.** If `Spawning instance ... (agent=X, ..., model=Y, source=Z)` log line regresses (e.g., field dropped, level lowered below INFO, source label removed) → gate breach (the WP3 line is THE non-silent-evidence contract; PD-1's defer verdict is contingent on it).
4. **Default-source silent fallback for `vision`.** If a designer spawn logs `source=default model=coding` (or any non-vision) AND the `model_override` cell is `None` → gate breach; the WP1 allowlist + WP3 observability together prevent this; any regression means the allowlist or the seam moved.
5. **Substrate tools unregistered.** If `KNOWN_TOOL_NAMES` does not contain `image_save` / `image_list` / `image_get` (i.e., `daemon/tools/image_tools.py:669/836/928` decorations not picked up at boot) → gate breach (12d contract).
6. **Bridge design constraint-row regression.** If `grep -c "^### Constraint row" bridge-design.md` drops below 5 → gate breach (12h contract; each row maps to a verified store fact).

None of these fired during this verification.

---

## Caveats / honest limitations

- **LLM proxy `:4124` was DOWN** (HTTP 000) at verification time — a designer conversation turn could not be exercised live. This is informational only; the phase's spawn/seam/substrate evidence does not require the proxy. A P2 follow-up may want to re-run with the proxy up.
- **Live HTTP `/api/instances` does NOT expose `model=`.** The override chain (WP2/WP3) is exercised via the in-process `spawn_instance` tool (LangChain `@tool`), not the HTTP API. The 16/16 unit suite in `tests/unit/test_caller_model_overrides_seam.py` pins the seam directly; live12b proves the observability log line format that the seam emits. See `p1-rollout-evidence.md` 12c for the honest labeling.
- **Designer conversation not run.** Out of scope per the brief ("Do not run a designer CONVERSATION/turn — spawn + log evidence is the phase gate").
- **Worktree `.env` + DB left in place** for follow-up re-verification; the brief instructed to leave these for the caller. They are gitignored.

---

## Final verdict

**PASS** — Phase 1 designer-agent implementation lands in one restart window with all 12a-12h criteria green. No gate-breach trigger fired. Exit criterion (§6 of `phase1-foundations.md`) is met.
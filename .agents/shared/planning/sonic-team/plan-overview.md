# sonic-team — Plan Overview (ENSEMBLE-31, overnight build v1)

## 1. Header

- **Date:** 2026-11-12 · **Status:** plan set, PRE-GATE (awaits reviewer council + fresh approver; see §8)
- **Author:** W1 (plan fan-out) under the `plan-creation` skill; design ~80% pre-decided and BINDING (S1–S11 + A1, recorded in `decisions.md`)
- **Worktree:** `/home/nea/ensemble-src-wt-sonic-v1`, branch `feature/sonic-team-v1` @ `ac9849bf7` (= `origin/latest`, verified). Commits happen ONLY via the giter lane.
- **Inputs (read, end-to-end):**
  - `.agents/shared/planning/sonic-team/findings-B-infra.md` (Leg B infra feasibility; all citations at `origin/latest ac9849bf7`)
  - `.agents/shared/planning/sonic-team/findings-C-risk.md` (Leg C risk/quality/gates/cost inventory)
  - **[Leg A]** runtime numbers (dispatcher-supplied, verbatim): median feature commission **8–41 h wall, 22–143 instances, 1–3 review cycles**; build 30–60% of wall, review+approval 15–25%, inter-phase gaps 10–20% (worst 4.5 h); **dispatch p50 775 s / p90 4258 s**; **kb-writer = 975 spawns = 46.4% of ALL instances** (#1 hidden cost); **NO token/usage telemetry daemon-side**; bash 1800 s hard cap exists (~79 timeout fires/window; >10 min = 0.106% of 75 k runs); **coding-lane 402 ACTIVE until 2026-11-17T11:43Z** (agentic/vision CLEAN); **6× recursion-limit-300 terminal events in window**
  - Read-only refs: `agents/coder/meta.json`, `agents/developer[v2]/meta.json`, `agents/aegis/`, `agents/designer/meta.json`, `agents/giter/meta.json`, `agents/ari/rule.md`, `tests/unit/test_ari_pm_terminology_pins.py`, `tests/test_chart_image_delivery_e2e.py` (SEALED_SHA_BASELINES), `.agents/tester/rules/ensure.md`, `agents/.prompt-artifacts.sha256`
- **Companion files (canonical set):** `decisions.md` (this dir), `phase1-implementation-spec.md`, `phase2-test-pack-spec.md`, `phase3-dryrun-spec.md`, `phase4-stage-runbook.md` — see `decisions.md` §5 consistency register.

## 2. Objectives (measurable)

1. **Cut inter-phase gaps + handoff overhead** via a one-wave fan-out (sonic-leader spawns coder; on coder's report spawns reviewer+tester in ONE wave, END TURN once; verdict-bearing reports — Leg B:90 designer-lane lesson: round-trip COUNT is the first-order cost). *Measure:* sonic commission wall-clock for a small/medium low-coupling feature ≤ 4 h (vs [Leg A] median 8–41 h), with per-phase gap share < 5% (vs 10–20%).
2. **Eliminate the kb-writer/experience spawn mass:** ZERO `kb-writer` spawns and ZERO `experience()` calls per routine sonic commission (vs 46.4% of all instances [Leg A]). *Measure:* spawn-count audit of the dry-run + first real commission shows 0/0.
3. **Preserve quality via blast-radius escalation + dual gates:** every sonic commission carries a sonic-reviewer verdict AND a sonic-tester pack-scoped gate; any blast-radius predicate hit returns the commission to Ari (steady team). *Measure:* 100% of commissions carry both gates; 0 migrations; 0 steady-team files modified (sole exception: the sanctioned Ari triage note, S4).
4. **Promote-ready by morning, zero migrations:** staged release with 4-way sha match + PROMPT-BASELINE verify green; NO promote (the promote ceremony is the Emperor's). *Measure:* stage journal shows 4-way sha equality + baseline verify PASS + `rollback_safe` trivially true (content-only release).
5. **All new pins gate-verifiable:** every sonic assertion lives in the `sonic_agent_defs` pack REGISTERED in `.agents/tester/PACKS.md` (aegis lesson: a pinned condition in NO registered pack cannot be gate-verified). *Measure:* pack row present + all assertions green + mutation legs RED-proven.

## 3. Scope fence

**IN**

| Item | Notes |
|---|---|
| 4 new agent dirs `agents/sonic-{leader,coder,reviewer,tester}/` | `meta.json` + 4 prompt artifacts each (soul/rule/workflow/tools_note); aegis/designer-family precedent; zero daemon code (S1). Phase-1 manifest shape: **20 new + 8 touched files** (8 = Ari note + baseline regen + **6 existing test files** for count/constant registration) — see `phase1-implementation-spec.md` §2 |
| Ari triage note (`agents/ari/rule.md`) | The ONLY sanctioned existing-agent prompt edit; spec'd in phase1, applied as a REPOSITORY FILE edit committed via giter (NOT a live-DB / prompt-artifact state-machine write tonight — council advisory #5); default=steady, sonic=opt-in positive selection for small/medium low-coupling work (S4) |
| Baseline regen (`agents/.prompt-artifacts.sha256`) | 181 → 197 rows (+16 sonic artifacts) + side-effect repair of 8 pre-existing drift rows — see §11 DEV-1 |
| `sonic_agent_defs` test pack + pins | Registered in PACKS.md; assertions per S9; mutation legs |
| Dry-run E2E (fenced, zero live contact) | Per `phase3-dryrun-spec.md`; `dryrun-boot-procedure.md` DELIVERED + VERIFIED 2026-11-12 (triple-exercised; helpers `fenced-launch.py` + `dryrun-verify.py` present) — Phase 3 consumes it as AUTHORITATIVE (audit-then-run; §7e RESOLVED) |
| Staging to promote-ready | merge → bump (version RE-DERIVED from tags at bump time) → annotated tag → `stage.sh` → 4-way sha + PROMPT-BASELINE verify; NO promote (S11). **Live-contact reconciliation (MF-3, per D16):** live contact = live RUNTIME state only (daemon ports 9797/9798, live DB, `current` symlink flip, `state.json` promote fields, promote/nonce, restart); the `releases/<version>` staging write-path + the install-`.env` marker write are the Emperor-sanctioned STAGE act — explicitly permitted, NOT "live contact" |
| Giter commit lane usage | All commits on `feature/sonic-team-v1` via giter with pre-approval in the dispatch envelope (Leg B:106–109) |

**OUT**

| Item | Where it goes |
|---|---|
| Snapshot transform build (byte-exact arbitrary-content mint) | MUST-BUILD per Leg B §3 — v2; v1 uses the fixed-schema handoff artifact FILE (S2) |
| Any daemon code change | Content-only release: agent dirs + tests + Ari note + baseline (hard constraint) |
| Any migration | Zero by design; rollback trivially safe (old release ignores new agent dirs) |
| Steady-team file modifications | Forbidden (sole exception: Ari note above, spec-only in this plan set) |
| Promote | Emperor's ceremony; plan ends at promote-ready |
| Chat-source adapter config (addressing sonic-leader) | Deferred — open decision (b), §7 |
| Privilege transfer (attestation tap / POK curator / mcp_rpc+wedge categories) | Deliberately NOT inherited (Leg B:25) — open decision (c), §7 |
| Measurement / token-cost loop | Out of v1; MUST-BUILD if wanted (Leg C §5a) — open decision (d), §7 |
| Coding-lane re-pin for sonic-coder | Decision only, post-2026-11-17 — open decision (a), §7 |
| Per-sonic-agent `skill-set.yaml` / `skills-template/` | Fast-follow; v1 = minimal 4-artifact set — open decision (g), §7 |

## 4. Phase list (canonical numbering — MUST match the other two workers)

| # | Name | Spec file | One-line AC |
|---|---|---|---|
| 0 | Preflight & input gate | (checklist = §9 below; no spec file) | Worktree clean; findings copies byte-identical (sha-verified); `dryrun-boot-procedure.md` presence check recorded (PRESENT + VERIFIED — §9); no-live-contact checklist PASS; plan-set files committed on `feature/sonic-team-v1` via the giter lane |
| 1 | Content build | `phase1-implementation-spec.md` | 4 agent dirs parse + resolve (registry discovery, team_members, tool allow/deny, `llm_model:"agentic"` ×4, `recursion_limit_multiplier:7` on sonic-coder only); handoff-artifact schema + blast-radius predicate (FROZEN 14-glob `[sonic-blast-radius-v1]`) + bash rule text present in prompts (frozen strings owned here); Ari note applied with terminology/seal/baseline regressions zero; baseline regenerated (197 rows, 8 repairs disclosed). Manifest = 20 new + 8 touched files (incl. 6 test files — ceremony, phase1 §2) |
| 2 | Verification | `phase2-test-pack-spec.md` | `sonic_agent_defs` pack registered in PACKS.md; all assertions green incl. mutation legs (meta parse, team resolution, allow/deny, agentic pins, coder multiplier, handoff-schema presence, Ari pin survival: exactly-1 'Plane' + seal re-pin + baseline count) |
| 3 | Dry-run E2E | `phase3-dryrun-spec.md` | ONE bounded fenced run: scratch source-boot daemon from THIS worktree per `dryrun-boot-procedure.md` (PRESENT + VERIFIED 2026-11-12, triple-exercised), real agent definitions, tiny real feature through explore→code→handoff→review→test→commit on a scratch branch, ZERO live contact; verdict PASS → stage may proceed; FAIL → honest report, stage NOTHING. NOTE: the procedure's validation predates the sonic agents (boot path only, live never contacted beyond read-only ss/ps/proc observation) — Phase 3 still proves agent-definition loading end-to-end |
| 4 | Staging, promote-ready, NO promote | `phase4-stage-runbook.md` | merge `feature/sonic-team-v1` → `latest` → bump (version re-derived from tags at bump time; highest today `v0.29.22`, no `v0.30.*` exists; NO version number hardcoded anywhere in this plan set) → annotated tag → `stage.sh` → 4-way sha + PROMPT-BASELINE verify green; promote-ready, NO promote |

Canonical phase names (char-for-char, owned per `decisions.md` §5 — all plan files must restate identically):
- `Phase 0 — Preflight & input gate`
- `Phase 1 — Content build`
- `Phase 2 — Verification`
- `Phase 3 — Dry-run E2E`
- `Phase 4 — Staging, promote-ready, NO promote`

Frozen predicate restatements (verbatim from `decisions.md` D2/D3; exact strings owned by `phase1-implementation-spec.md` — any further restatement must match char-for-char):
- **Handoff-artifact fields:** files touched / what changed / what is UNTESTED / known risks / how to verify / bash-raises used / blast-radius self-assessment vs predicate
- **Blast-radius escalation classes (steering prose):** daemon core (graph/manager/services), migrations dir, queue/mission/report-delivery services, release/promote scripts, auth/secrets, prompt-artifact machinery → escalate back to the steady team (return commission to Ari with reason)

## 5. Coupling map / dependency graph

```
Phase 0 ──► Phase 1 ──► Phase 2 ──► Phase 3 ──► Phase 4
             │            │           │            │
             │            │           │            └── tag/version derivation (re-derived at bump; NEVER hardcoded)
             │            │           └── STAGE GATE: stage ONLY if dry-run PASSes
             │            └── pack assertions ↔ prompt content (handoff field names, blast-radius strings,
             │                agentic pins, multiplier) — char-for-char agreement with phase1 frozen strings
             ├── baseline regen ↔ EVERY new .md (16 rows) + Ari-note row + 8 pre-existing drift repairs (DEV-1)
             └── Ari note ↔ tests/unit/test_ari_pm_terminology_pins.py (rule.md == exactly ONE 'Plane',
                 workflow/soul == ZERO, case-insensitive — do not disturb) ↔ SEALED_SHA_BASELINES re-pin
                 (tests/test_chart_image_delivery_e2e.py:1510+; current fc0a7542…, prior 8063c9ce…)
GITER COMMIT LANE (cross-cutting): giter worktree-mode contract (agents/giter/workflow.md:78-103) ↔
  worktree/quiescence (no merge until clean; merge IS the commit point) ↔ wt.claim heartbeat refresh at
  PHASE BOUNDARIES (stale-claim reap risk) ↔ pre-approval in dispatch envelope (each approval gate ≈ one
  p50-775s hop [Leg A] — pre-approve to avoid)
```

Strongest couplings: (1) **Phase 2 ↔ Phase 1 frozen strings** — the pack asserts prompt text; any wording drift between spec and prompts is a mechanical RED. (2) **Phase 3 → Phase 4** is a hard gate. (3) **Ari note ↔ three pin families** (terminology whole-file, seal content-sha, baseline row) — one edit, three ceremony surfaces.

## 6. Risk register

| ID | Risk | Evidence | L/D* | Mitigation | Residual |
|----|------|----------|------|------------|----------|
| R1 | Recursion-limit 300 kills long turns on multiplier-LESS sonic agents (leader/reviewer/tester) — 6 prod terminal events, incl. a developer-class instance | Leg B §2b (findings-B:53–58); [Leg A] 6× events | M/H | sonic-coder carries ×7 (2100 steps, S7); orchestration turns are short by design (one-wave END TURN); ERROR is checkpoint-preserved + revivable (Leg B:57) | A chatty leader/reviewer turn could still hit 300; watched in dry-run |
| R2 | Watchover 3-strike denial on long coder turns UNQUANTIFIED (3 denials/turn → terminate node, default ON) | Leg B:62, findings-B:120 (gap 🟡) | M/M | Dry-run exercises a real coder turn (Phase 3 is the probe); if it fires, record + re-dispatch (budgets reset per turn) | Unknown until first long run; carried as evidence gap |
| R3 | 125-min task cap is the REAL clock once the multiplier exists (~30–60 s/round × 700 rounds ≫ cap) | Leg B:61,66; `config.py:2096` | H/M | sonic-leader intake rule: coder-estimate ≤ ~90 min else escalate (S7); long fixes split across dispatch→report turns (budgets reset) | Oversized commissions still die at 125 min; intake discipline is prompt-realized |
| R4 | Worker pool = 5 daemon-wide = fan-out ceiling (queue-hop latency; contention at p90) | Leg B:90 (`constants.py:91`) | M/M | One-wave fan-out minimizes wave count; sonic never runs alongside… (no co-scheduling control in v1) | Contention with other nests' dispatches; measured in dry-run only partially |
| R5 | Dispatch p50 775 s / p90 4258 s + revision loops make multi-hop flows slow | [Leg A]; Leg B:90 (p90 implies revision loops/pool contention) | H/M | One-wave END TURN pattern; verdict-bearing reports; ≤1 re-dispatch doctrine (developer[v2] precedent) | Revision loops inherently cost hours at p90 |
| R6 | Dry-run fencing hazards: boot-sweep killing LIVE processes; accidental live contact; `env -u VAR_*` no-op; HOME-derived live-shaped paths | Critical notes (boot-sweep PIN `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0`; env -u no-op; test-script live-deletion incident); `fenced-launch.py` (fd-inheritance port-capture fence) | M/H | Phase 3 spec + `dryrun-boot-procedure.md` (delivered + VERIFIED — R11) carry the full fence checklist: explicit per-var env scrub, `env -i` + fenced-launch.py, scratch HOME via mktemp, no live port/DB/install-dir contact, sweep pin =0 | Procedure doc RESOLVED (§7e); fencing remains the highest-severity execution risk of the night — mitigated only by following the procedure exactly |
| R7 | Ari ceremony proves non-mechanical (seal re-pin + terminology pins + baseline row entangled) | S4; SEALED_SHA_BASELINES re-pin history (`tests/test_chart_image_delivery_e2e.py:1495+` shows the re-pin pattern is established but multi-site) | M/M | Established re-pin pattern exists (multiple precedents); if non-mechanical at implementation → FALLBACK: drop the Ari note to fast-follow (recorded deviation), ship the 4 agents + pack only | Front-door routing unchanged (default=steady) — sonic reachable via explicit dispatch only |
| R8 | Baseline drift — pre-existing 8 drifted rows at `ac9849bf7` (see DEV-1) + any new artifact churn | Verified this run: `sha256sum` sweep vs `agents/.prompt-artifacts.sha256` (8/181 rows drift, incl. ari/leader/developer/tester) | H/M | Phase 1 REGENERATES the whole baseline (absorbs repairs); stage-time PROMPT-BASELINE verify runs against the regenerated file; repairs DISCLOSED in stage journal + §11 | A reviewer diffing the baseline sees 8 "unexpected" row changes — must be pre-briefed (DEV-1) |
| R9 | Giter approval round-trips (~775 s per hop [Leg A]) stall the overnight lane | Leg B:109 (rule.md stop-and-report approval gates) | M/M | Pre-approval carried IN the dispatch envelope (file lists + destructive-op scopes pre-cleared); heartbeat refresh at phase boundaries (stale-claim reap) | Un-pre-approved ops still block; envelope quality is on sonic-leader |
| R10 | No token/usage telemetry → sonic cost/benefit untracked in $ | [Leg A]; Leg C §5a (no token/cost columns in ANY repository model) | H/M | v1 measures wall-clock + instance/spawn counts only ([Leg A]-style); token loop = open decision (d) | Sonic's true cost unquantifiable until the measurement loop is built |
| R11 | ~~`dryrun-boot-procedure.md` PENDING~~ **RESOLVED by delivery (19:58Z, 2026-11-12)**: procedure PRESENT + VERIFIED, triple-exercised (DB create → boot → `dryrun-verify.py` → SIGTERM → DROP); helpers `fenced-launch.py` (fd fence) + `dryrun-verify.py` delivered | Dispatcher verification + dir listing (amendment pass); phase3-dryrun-spec.md §2 (P1–P10 audit, all PASS) | L→(was H)/M | Audit the delivered procedure against the **P1–P10 checklist before D0** (phase3-dryrun-spec.md §2.1) and consume it as authoritative; **no improvisation if a gap is found (BLOCKED)** — report the gap, stage nothing | Schedule residual LOW; execution risk now concentrated in following the procedure exactly (audit-then-run) |
| R12 | Coding-lane 402 ACTIVE until 2026-11-17T11:43Z | [Leg A]; Leg C §7 (worker deaths mid-run) | N/A (dodged) | ALL FOUR sonic agents pinned `agentic` (S6) — structurally dodges the dead lane + default-pool drift | sonic-coder runs on agentic, not the purpose-built coding lane, until open decision (a) |
| R13 | PROMPT-BASELINE count/verify mismatch at stage (197-row regenerated baseline vs 181-row expectation) | S9; DEV-1 | L/M | Phase 2 asserts the regenerated baseline line count (197) and the pack records it; stage verify consumes the regenerated file | Ceremony notes elsewhere still say "baseline 181" — stale references must not be trusted |
| R14 | Loop-breaker false positive on legit coder loops | Leg B §2a (findings-B:47–51) | L/L | NON-ISSUE by construction (identity = name+canonicalized args; 3-consecutive; non-fatal repair) — recorded to prevent re-litigation | None |
| R15 | `llm_model:"agentic"` pin silently ignored if `allowed_models` narrows | Leg B:97 (no-match → silently ignored) | L/H | Phase 2 asserts the pin resolves (pin ∈ default `allowed_models` = agentic,coding,coding2,vision,img-gen,decision — Leg B:96); dry-run proves the lane live | Operator narrowing OPENAI_SELECTABLE_MODELS post-hoc would silently revert to pool |
| R16 | Duty pack ALREADY RED at gate: `test_anomaly_report_duty_prompts.py` = 7 failed/12 passed at HEAD (canary-rooted + stale count pins 45/43/177 vs actual 46/44/181); Phase 1 does NOT fix canary (out of scope — adjacent agent prompt) | phase1 §9-D3 (verified at base) | H/M | Disclosure-first + delta-based ACs (A20a/b, A23–A26): Phase 1 registers 50/48/197 and keeps canary as the SINGLE base-attributed red; the Phase-2 tester gate adjudicates the base-attributed red set explicitly (§8) | Pack alone cannot prove sonic compliance (loop fails on canary first) — A20a independent check carries the proof |
| R17 | Phase 1 touches 6 existing test packs for count/constant registration (duty counts, aegis baseline rows 181→197, snapshot exact-list pin, grant-ordering counts, terminology pins untouched-but-run) — reads like scope creep if unexplained | phase1 §2 rows 23–28 + Appendix A (all edits disclosed as ceremony) | H/M | A21 green-pack suite + A23–A26 exact-constant checks + §10 VERIFY-AT-IMPL constants (V6/V7); §3b-v records the fact as adjudicated ceremony | None beyond disclosure — every touch is a count/constant registration the new agents force |
| R18 | `snapshot_enabled: true` exact-list pin (+2 strings) + sonic-reviewer `bash` GRANTED: bash could in principle mutate — it is prompt-enforced read-only only | phase1 §9-D7/§10-V4 + §3b-ii/iii (dispatcher adjudications) | M/L | Reviewer bash residual bounded by leaf role (`team_members: []` — spawns nothing) + verdict-only output + deny of edit_file/write_file/instance/service/mcp/secrets/proc; snapshot flag inert in v1 (R15 toggle OFF, no `snapshot_id` passed) | Bash discipline on the reviewer is prompt-realized, not mechanically enforced — the one residual accepted by the dispatcher |

*L/D = likelihood/damage (L/M/H).

## 7. Open decisions for the Emperor

| # | Decision | Options | Default if no answer | Impact |
|---|----------|---------|----------------------|--------|
| a | Re-pin sonic-coder to a `coding` alias after the 2026-11-17T11:43Z quota reset | (1) keep `agentic`; (2) re-pin `coding` (in-tree coder precedent, Leg B:99) | **(1) keep `agentic`** — stability; revisit only after observing post-reset quota headroom | Option 2 aligns with the purpose-built lane (possibly better/cheaper coding) but re-exposes the 402 class; one meta.json line either way |
| b | Chat-source addressing of sonic-leader (adapter `agent` config policy) | (1) never — API/job-lane only; (2) designated-channel config later | **(1) API/job-lane only in v1** (no adapter config written; Ari front-door routes everything) | Users cannot DM sonic-leader; all sonic work arrives via Ari/steady dispatch — front-door judgment preserved (S3) |
| c | Privilege transfer deliberately NOT inherited (attestation tap, static protection, POK curator, mcp_rpc/wedge categories stay `{leader,project-manager}`-scoped — Leg B:25) | (1) confirm non-inheritance; (2) selectively grant later | **(1) confirm non-inheritance** | sonic-leader cannot curate POK / use wedge|mcp_rpc gates; anything needing them escalates to Ari → steady team (blast-radius lane already covers this) |
| d | Measurement / token-cost loop out of v1 | (1) stay out; (2) commission MUST-BUILD (no token/cost columns exist — Leg C §5a) | **(1) stay out of v1** | Sonic cost tracked only as wall-clock + spawn counts; the "is sonic actually cheaper?" question stays unanswerable in $ until built |
| e | ~~Dry-run procedure doc (`dryrun-boot-procedure.md`) pending from devops~~ **RESOLVED by delivery** — option (1) "wait" satisfied (delivered 19:58Z, VERIFIED + triple-exercised) | (1) wait; (2) sonic lane drafts it itself | **(1) wait — SATISFIED** (decision kept for audit) | Phase 3 unblocked; consumes the delivered procedure as authoritative (audit-then-run); staging still does NOT proceed without a dry-run PASS (S10) |
| f | Pre-existing baseline drift (8 rows at `ac9849bf7`, DEV-1): repair-via-regen vs append-only baseline | (1) full regen + disclose; (2) append-only (leave 8 stale rows) | **(1) full regen + disclose** (matches S9; append-only would leave stage-time verify red against fresh content) | Baseline diff shows 8 pre-existing repairs beyond the +16 sonic rows — reviewers pre-briefed via DEV-1 |
| g | Per-sonic-agent `skill-set.yaml` + `skills-template/` (Leg B §3 item 6, optional; aegis carries them, designer does not) | (1) minimal 4-artifact set; (2) aegis-exact parity | **(1) minimal 4-artifact set** | Skill Bank auto-seeding skipped for sonic in v1 (fast-follow if sonic agents want seeded skills); phase1 §2 confirms NO skills-template — keeps the 197 derivation exact |
| h | **Confirm-only (surfaced at plan gate):** blast-radius superset — `daemon/routers/**` KEEP alongside `daemon/sources/**`/`daemon/mcp/**` (14-glob FROZEN block) + documented exclusions (constants.py, checkpoint_adapter, cancellation, compaction, frontend, docs, tests, agents/**/meta.json) | (1) confirm superset (escalate-only gate errs safe; C:§1d routers = 146 defect-touches); (2) drop routers (one-line, LOOSENS the gate) | **(1) confirm** (dispatcher-adjudicated KEEP; leader may flip at gate) | Gate strictness vs false-escalation rate; superset only ever escalates more to the steady team, never less |
| i | **Confirm-only (surfaced at plan gate):** `snapshot_enabled: true` on sonic-reviewer + sonic-tester NOW (2-string exact-list pin churn, v2-ready) vs deferring to v2 | (1) set true now (v2 relay enabler, inert in v1); (2) false now + second meta edit in v2 (zero test churn today) | **(1) true now** (dispatcher-adjudicated; documented false-fallback if the pin shape is non-mechanical at implementation) | Only place Phase 1 chooses test churn over later ceremony; flipping later costs a v2 meta edit + re-pin |

## 8. Gate map

| When | Gate | Owner / mechanics |
|------|------|-------------------|
| TONIGHT — on THIS plan set | Reviewer council (fresh) + fresh approver approval | Per adjudication A1(b): council reviews the plan set; approver approves THE PLAN (not yet the code) |
| Implementation — Phase 1 | Content review | sonic-reviewer / steady reviewer council reviews the 4 agent dirs + Ari note against phase1 frozen strings |
| Implementation — Phase 2 | Tester gate | `sonic_agent_defs` pack (canonical script/row id `sonic_agent_defs_unit_test`, path `test/packs/sonic_agent_defs_unit_test.sh` — alias rule `decisions.md` §3b-iv/§5; registered in PACKS.md) — all assertions + mutation legs green. **The tester gate must EXPLICITLY adjudicate the base-attributed red set at Phase 2** (duty pack 7 pre-existing failures, canary-rooted — R16): delta-based ACs prove sonic compliance; canary + stale-count reds ride as base-attributed, never silently |
| Implementation — Phase 3 | Dry-run verdict | PASS required to stage; FAIL → stage nothing, honest report |
| Implementation — Phase 4 | Stage ceremony gates | `stage.sh` internal gates (provenance sidecar, scripts-coverage stamp, rollback_safe derivation, PROMPT-BASELINE drift leg — Leg C:56,65) + 4-way sha verify |
| ANY gate that would ask a human | **Human-gate resolution rule (A1d, binding):** resolve per leader adjudication, RECORD the decision (decision ledger / stage journal), surface it in the morning report. Never block the overnight lane waiting on a human | Leader |

**Gate ledger (iterations + 10 council adjudications + advisory folds):** see `decisions.md` §6 — iteration 001 (approver: REJECTED → fixed via W3 amendment → 0-blocking) · iteration 002 (reviewer council: **APPROVE-WITH-NOTES**, unanimous, 4 MF + 15 advisory, gate-order MF-1/2 before Phase 3, MF-3/4 before Phase 4). The Phase-2 base-red adjudication artifact is PINNED (advisory #4): a tester RESULTS gate-verdict file naming the 4 canary-rooted reds + base evidence pointer (QUARANTINE.md row optional).

## 9. Phase 0 preflight checklist (checkable)

- [ ] Worktree `/home/nea/ensemble-src-wt-sonic-v1` clean apart from the planning dir; branch `feature/sonic-team-v1` @ `ac9849bf7` = `origin/latest` (re-verify; no drift from base)
- [ ] `findings-B-infra.md` + `findings-C-risk.md` present in this dir, byte-identical to the leg originals (dispatch states sha-verified — re-confirm presence + size)
- [ ] `dryrun-boot-procedure.md` PRESENT + VERIFIED 2026-11-12 (delivered 19:58Z, triple-exercised); **P1–P10 audit recorded before the first command** (per phase3-dryrun-spec.md §2.1 — no improvisation if a gap is found: BLOCKED, report, stage nothing); helpers `fenced-launch.py` (fd-inheritance fence, load-bearing — never bypass) + `dryrun-verify.py` (isolation proof) present
- [ ] No-live-contact checklist acknowledged by all lanes: NO live daemon port, NO live DB, NO live install dirs (`/home/nea/agents-ensemble/`), NO canonical-checkout writes (`/home/nea/ensemble-src` is detached-HEAD + dirty — writing there is a contamination incident)
- [ ] Scratch files ONLY under `/tmp`; no git add/commit/merge/tag by plan/dev lanes — commits flow EXCLUSIVELY through the giter lane with pre-approval in the envelope
- [ ] `wt.claim.sonic-v1` heartbeat refreshed at this phase boundary (stale-claim reap risk — critical note)
- [ ] Plan-set files (`plan-overview.md`, `decisions.md`, 4 phase specs) committed on `feature/sonic-team-v1` via the giter lane
- [ ] 402 protocol armed: if LLM calls start failing 402 → stop, write what you have marked PARTIAL, report 402-BLOCKED honestly (agentic lane is CLEAN, so this should not fire for sonic lanes)

## 10. Morning handoff expectations

- **State:** PROMOTE-READY — staged release dir + tag on `latest`; 4-way sha match (release dir == dist == manifest == sidecar); PROMPT-BASELINE verify green against the regenerated 197-row baseline; `rollback_safe` trivially true (zero migrations, content-only).
- **Evidence pointers:** this planning dir (findings + specs + decisions); `sonic_agent_defs` pack results + PACKS.md row; dry-run report (Phase 3); stage journal + sha manifest (Phase 4); deviation list (§11 + decisions.md §3) surfaced FIRST in the morning report.
- **The promote command is the Emperor's** — exact command lives in `phase4-stage-runbook.md` (version re-derived at bump time; nothing pre-hardcoded here). NO lane promotes.
- **Open decisions (§7) presented for ruling**, defaults already in effect.
- If any phase failed honestly: promote-ready is OFF the table; the morning report carries the failure verdict + evidence, and NOTHING is staged (S10 discipline).

## 11. Deviations & evidence conflicts 🔴 LOUD

> Full mirror + adjudication status in `decisions.md` §3. This section is the overview-visible copy. NONE of these change the binding design; all are flagged, none silently deviated.
> **Phase-1 deviations D2–D12 live in `phase1-implementation-spec.md` §9 (headline rows mirrored in `decisions.md` §3).**

| ID | Pre-decision / claim | Evidence (file:line) | Conflict | Proposed resolution | Status |
|----|---------------------|----------------------|----------|--------------------|--------|
| DEV-1 🔴 | S9 assumes baseline regen is purely additive (+16 rows on 181) | This run's sha sweep: **8 of 181 rows in `agents/.prompt-artifacts.sha256` do NOT match file content at `ac9849bf7`** — `ari/rule.md` (8063c9ce→fc0a7542), `developer`+`developer[v2]` rule (d4f8c671→c615d2a0) + workflow (e89c8162→e2fe21f5), `leader/rule.md` (d104cb7f→6a225b9f), `leader/workflow.md` (9999572d→eb986152), `tester/workflow.md` (d60cdc7e→c11d41b2) — vsss-era (2026-11-12) edits updated seal pins in tests but NOT the drift baseline | Phase 1 regen REPAIRS 8 pre-existing rows as a side effect — a reviewer diffing the baseline would see unexpected changes beyond scope | Full regen + explicit disclosure (open decision f default); stage verify consumes the regenerated file; reviewers pre-briefed | **Flagged to caller** — council must acknowledge the 8-row repair rides this release. **Dispatcher independently spot-verified (fan-in):** `agents/ari/rule.md` baseline row `8063c9ce…` vs actual `fc0a7542…` — drift CONFIRMED; the other 7 rows stand as reported (verify at implementation by the full regen) |
| DEV-2 🟡 | Dispatch cites Ari rule.md seal pin as `8063c9ce…` | `tests/test_chart_image_delivery_e2e.py:1510–1514`: CURRENT seal = `fc0a7542…` (vsss re-pin 2026-11-12; `8063c9ce…` is the PRIOR value, kept in the comment) | Steering metadata is one re-pin behind repo reality | Phase 1 re-pins from the CURRENT value `fc0a7542…` → new; ceremony spec cites current reality | No design impact — informational, corrected in this plan |
| DEV-3 🟡 | S6 parenthetical: "NO 'quick' alias exists" | `agents/experiencer/meta.json` carries `"llm_model": "quick"` (also explorer, kb-writer lane per Leg B:98); BUT `quick` ∉ default `allowed_models` (`agentic,coding,coding2,vision,img-gen,decision` — Leg B:96) | 'quick' EXISTS as a legacy agent-side pin, just not in the selectable set | Keep S6 as-is: pin `agentic` (unambiguously allowed + clean). Do NOT use 'quick' for sonic | No design impact — clarified |
| DEV-4 🟡 | 402 reset time: dispatch says 2026-11-17T11:43Z; Leg C §7 says 11:44:37Z | findings-C:12,293 | 1-minute discrepancy between sources | Use ~11:43–44Z; decision (a) executes only after reset CONFIRMED, so the minute is immaterial | No action |
| DEV-5 🔴 | S10 names `dryrun-boot-procedure.md` as the dry-run authority | Directory listing at first write: file **ABSENT**; `fenced-launch.py` IS present (fd-inheritance fence for the scratch daemon — the wi-2454ede3 port-capture class via fd inheritance) | Phase 3's load-bearing input did not exist at plan-writing time (landed 19:58Z, DURING plan writing) | Recorded as PENDING INPUT (S10 + §7e): Phase 3 hard-blocked until devops lands the doc; staging does not proceed without a dry-run PASS | ~~Flagged to caller~~ → **RESOLVED — procedure delivered 19:58Z, VERIFIED + triple-exercised; Phase 3 spec §2 audits it (P1–P10, all PASS); the no-PASS⇒-no-staging rule stands.** (phase3-dryrun-spec.md called this row STALE — loop closed here) |
| DEV-6 🟡 | S5 presents "default ≤10 min" as a rule; Leg C §4d verdict is PARTIAL | findings-C:251–257: 10-min default is MUST-BUILD as CODE (today the default IS the 1800 s hard max; no per-agent knob exists — `bash.py:1238/:1268`); >10 min = 0.106% of 75 k runs; per-call overridability to 1800 s is the measured-tail requirement | Prompt-realized rule ≠ enforced default — S5 is intentionally prompt-only in v1 (no daemon code) | Record as implementation reality (S5 caveat): the 10-min default lives in sonic-coder's prompt; the ONLY hard enforcement is the existing 1800 s tool cap (~79 fires/window [Leg A]); the >10-min tail (build/mutation work) stays reachable via explicit raise-with-report | Adjudicated by steering (S5 as written); recorded |
| DEV-7 🟡 | Leg B §5/§6 framed lane choice as "decision is the leader's" (open) | findings-B:99 | Leg B left the lane open; steering S6 binds it | S6 (all-four `agentic`) recorded as the adjudication; open decision (a) is the post-reset revisit | Adjudicated by steering |
| DEV-8 ⚪ | (Positive record — no conflict on the load-bearing rocks) | Leg B verdicts: peer-ness structurally free (:22–29), one-shot viable only with multiplier (:45–58), giter reusable as-is (:103–109), snapshot direct-mint MUST-BUILD (:70–80) | None — findings AGREE with S1/S2/S7/S11 | Recorded for the council: design and evidence are consistent on every load-bearing element | None |

— end of plan-overview.md —

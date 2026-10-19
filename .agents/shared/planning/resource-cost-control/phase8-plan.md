# Phase 8 — Docs + Final Integration Gate

**Commission:** resource-cost-control · **Spec:** `architecture-recommendation.md` §2.8/§2.9 (API + knobs for the runbook), §2.7 (anomaly shapes), §2.2/AD13 (attribution honesty), §3 (optimization ledger + O1 measurement protocol) · **Slice:** 8 of 8 (P1 → P2 → P3 → P4 → P5 → P6 → P7 → **P8**) · **Docs-only phase — zero code changes.**

---

## 1. Goal

Close the commission: (1) the **operator runbook** for the resource-cost-control subsystem under `docs/runbooks/` (house location/style) — how to read `/resources`, interpret the honesty columns (`unattributed_kb` / `estimated` / `coverage_pct`) honestly, tune the retention/self-cost knobs, and read an anomaly `shape`; (2) the **O7 operator checklist** doc surface — referenced from the runbook, authored/owned in P7 (`docs/runbooks/resource-hygiene-operator-checklist.md`), with the dry-run-first rule and USER-decision items called out; (3) the **CHANGELOG** entry; (4) the **final integration gate** — the whole-commission green gate across P1–P7: full `pytest` + the three FE gates, with the **self-cost budget acceptance restated here as the commission-level criterion** (≤1–2 % CPU + fixed MB floor; its regression test is planned in P1 and MUST fence paths — `mktemp` FAKE_HOME + escape guard, never live-shaped paths).

Any red surfaced by the gate routes back to its OWNING phase as a new commit (D7) — this phase fixes nothing itself.

---

## 2. Deliverables (exact paths)

| # | Artifact | Path |
|---|----------|------|
| D1 | **Runbook** | `docs/runbooks/resource-cost-control.md` — house runbook location/style (12 existing runbooks; `maintenance-console.md` precedent: title + Date/Scope/Plan header line, then sections). Required sections: (a) reading `/resources` (live tab, timeline, self-cost chip, traceback window; all eight `/api/resources/*` endpoints with owning phases — **P1: `/live`, `/history`, `/self-cost`** (the FE self-cost chip reads `/self-cost`, P1-owned); **P3: `/traceback`**; **P4: `/anomalies`, `/anomalies/{id}/evidence`**; **P6: `/control-capabilities`, `POST /profile`**); (b) **honesty columns** — what `unattributed_kb`, `estimated`, `coverage_pct` mean, why per-byte/per-instance truth is partial by physics in a shared process (arch §2.2 🔴 / AD13), and the rule never to present them as ground truth; (c) **knob tuning** — the full `RESOURCE_MONITOR_*` retention/self-cost table (§2.9: 48 h/14 d/180 d defaults, floors, `SAMPLE_SECONDS`, `SELF_CPU_BUDGET_PCT`, `SELF_RSS_BUDGET_MB`, `PROFILING_ENABLED` default-OFF per LR4) + the P7 knobs (`LIMITS_INSTANCE_CACHE_TTL_HOURS` + `LIMITS_INSTANCE_CACHE_MAX_ENTRIES`, `CHART_RENDER_MAX_CONCURRENCY` ceremony-gated) + `MALLOC_ARENA_MAX` operator note incl. the **O1 measurement protocol with the < 5 % decision rule** (read-only `/api/resources/history` comparison at T+24 h/T+72 h vs the recorded 1.68–1.72 GB baseline); (d) **anomaly `shape` glossary** — `spike \| ramp \| leak \| step`: what each implies, where to drill (evidence endpoint → traceback window), and the conditioned-baseline caveat (5 workers ⇒ higher RSS is NOT an anomaly); (e) **known non-issues** — the verified NO-OP ledger (P7 D11) verbatim, so nobody re-opens them; (f) **systemd memory bounds + swap + tripwire reading** (2026-10-18 OOM delta, D9/§8) — the demonstrated OOM evidence (3 OOM deaths the night of Oct 17, daemon killed **23:19:14Z**, unit peak **8.4 GB**, box at **6.7 GB** at recovery), the `MemoryMax`/`MemoryHigh` recommendation for `ensemble-main.service` with EXACT drop-in commands (§8.1 — operator-gated, live), swap provisioning framed as a **USER decision** with exact commands (§8.2 — the box has zero swap, so any transient spike goes straight to the OOM killer), and the reading-the-tripwire note (`/system.slice` `memory.peak` ≈ 9.6 GB ≈ 97 % of box since the Sep-26 boot is now a **DEMONSTRATED kill mechanism**, not a hypothetical — §8.3) |
| D2 | **O7 checklist doc surface** | Cross-reference section inside `docs/runbooks/resource-cost-control.md` linking `docs/runbooks/resource-hygiene-operator-checklist.md` (authored/owned in P7) + operator-usage text: run the dry-run step FIRST (list-what-matches before any drop), the `ensemble_blob_prune_*` pattern+age gates, the orphan `context7-mcp` kill, and the demo-daemon keep/kill = **USER decision**; banner restated — agents never execute any of it against live (LR5). **2026-10-18 OOM-delta additions to the surface:** the systemd `MemoryMax`/`MemoryHigh` recommendation for `ensemble-main.service` (exact drop-in commands, §8.1 — operator-gated), swap provisioning marked **USER decision** (framing + exact commands, §8.2), the tripwire-reading note grounded in the demonstrated Oct-17 OOM (§8.3), and the cross-reference to **O8** (boot-time stale-subprocess sweep + `KillMode=cgroup` audit) as the CODE-side counterpart **planned in P7** (referenced only, never re-authored here — D4). **P8 edits only the runbook's referencing section — the checklist file itself is P7's artifact and is not rewritten here.** |
| D3 | **CHANGELOG entry** | `CHANGELOG.md` — **create `## [Unreleased]` if absent, else append** (Keep-a-Changelog 1.1.0, `0.21.0` structural precedent; at the freeze HEAD `4d572f561` the top section is `## [0.23.0]` — NO `[Unreleased]` section exists at HEAD, so the entry CREATEs it): `### Added` observability subsystem (sampler/storage/attribution/join/anomaly/FE/control seam, P1–P6, with the 4-table schema + `/api/resources/*` surface); `### Changed` / performance (landed state at the freeze: O1 `MALLOC_ARENA_MAX=2` four-site insertion, O3 `LIMITS_INSTANCE_CACHE_TTL_HOURS` + `LIMITS_INSTANCE_CACHE_MAX_ENTRIES` cache knobs, O4 backoff-reset + MCP idle reap — all LANDED; one line recording **O2 as ceremony-gated FORWARD** (lands in the promote train, live effect at the user's nonce ceremony — not live from this merge) and **DependencyBus lock reap as FORWARD**); `### Docs` (runbook + hygiene checklist); **O5/O6 as deferred** with the one-line why |
| D4 | **Final integration gate evidence** | Gate run record (commit-message or RESULTS note referenced from the CHANGELOG entry): full `python -m pytest tests/ -q`; the three FE gates; the self-cost acceptance numbers; the migration-count assertion; the O2 no-promote assertion (see AC) |
| D5 | **Docs fence test** | `tests/test_phase8_docs_gate.py` — mechanical fence: runbook exists and contains the honesty-columns section, the `RESOURCE_MONITOR_*` knob names, the shape glossary terms, the O7 checklist link + dry-run-first mention; the checklist file exists (P7 artifact) and retains its fence markers; `CHANGELOG.md` `[Unreleased]` names `resource-cost-control`; **2026-10-18 delta assertions:** the runbook contains the `MemoryMax` and `MemoryHigh` strings, a swap **USER decision** marker, and the tripwire evidence line (23:19:14Z kill / 8.4 GB unit peak / 9.6 GB ≈ 97 % box) |

---

## 3. Decisions (binding — do not re-open)

| ID | Decision | Rationale (spec anchor) | Rejected alternatives |
|----|----------|------------------------|----------------------|
| D1 | Runbook lives at `docs/runbooks/resource-cost-control.md`, matching house runbook location and style (`maintenance-console.md` header precedent: title, Date · Scope · Plan line, prose sections). | dispatch ("match the house runbook location/style — inspect existing runbooks"; 12 runbooks verified present) | `docs/` root placement; a wiki/external doc; inventing a new docs subdirectory |
| D2 | **Honesty doctrine in the runbook:** the residual is taught, not hidden — `unattributed_kb`/`estimated`/`coverage_pct` are explained as partial-by-physics (counters measure Python-object bytes ≈ 40–80 % of anon; per-instance in-process CPU is an estimate; MCP RAM is server-scoped), and the runbook states the interpretation rule: act on `coverage_pct` collapse + children (exact) + anomaly contributors, never on the attributed number alone. | §2.2 🔴 (both councilors) + AD13 ("surfaced in the API, never buried"); §6 ("no fabricated per-instance truth") | presenting attribution as ground truth; omitting the residual from the docs (would train operators to over-trust) |
| D3 | Knob documentation is **grep-verified against the implemented code** at drafting time — every knob name/default/bounds cited in the runbook must match the merged `daemon/config.py` / `daemon/manager.py` / `daemon/tools/chart_tools.py` state, not the plan text (grep-verify-before-citing, `.agents/tester/rules/ensure.md` house rule). If P1–P7 renamed anything, the runbook documents the MERGED names. | house ensure rule; D7 routing of drift | copy-pasting knob names from this plan set without verification |
| D4 | The O7 checklist is **referenced, not rewritten**: P8 owns only the runbook's doc-surface section; the checklist file is P7's artifact (authored there, fenced there). Fixes to its content route to P7's commit, not P8. | dispatch ("referenced from P8's docs but authored/owned here" — P7); LR5 | re-authoring the checklist in P8 (dual ownership drift) |
| D5 | CHANGELOG entry follows the **conditional-create `## [Unreleased]` if absent, else append** + feature-subsection house structure (the `0.21.0` entry precedent: bold header line with commission slug + merge refs, then bullets per artifact family). O2's line explicitly carries the ceremony gate; O5/O6 recorded as deferred with the one-line why. | `CHANGELOG.md` head verified at the freeze (Keep-a-Changelog 1.1.0; top section = `## [0.23.0]` — `[Unreleased]` does NOT exist at HEAD → conditional-create; 0.21.0 precedent) | a new version heading (version bumps are the promote ceremony's business, not the docs phase's); omitting the O2 gating caveat |
| D6 | **Final integration gate = the commission-level green gate**, four commands from the worktree root: full `python -m pytest tests/ -q` + `cd frontend && ./node_modules/.bin/tsc --noEmit -p tsconfig.app.json` + `npm test` + `npm run build`. **Self-cost budget acceptance restated as the commission-level criterion: ≤ 1–2 % CPU + fixed MB floor (< 20 MB per §2.8)**, evidenced by the P1 regression test (`tests/test_resource_monitor_self_cost.py`) green **and** the stored self-cost numbers (`sample_cost_ms`, `sampler_lag_ms`, `missed_samples`, buffer bytes) recorded in D4. The regression test MUST fence paths (`mktemp` FAKE_HOME + escape guard) — never resolve live-shaped or HOME-derived paths (2026-10-12 incident class, pinned critical-note convention); the fence itself is part of the acceptance. | dispatch; §2.8 self-cost discipline + §5 risk 1; AD12 | a partial gate (backend-only); re-deriving self-cost ad hoc instead of the pinned regression test |
| D7 | **The gate adjudicates; it does not patch.** Any red routes back to the OWNING phase as a new commit (P1–P7 own their code; this phase owns only docs). No waiver, no skip, no fix-in-P8. After any routed fix, the FULL gate re-runs — the gate is only satisfied by a single clean pass of all four commands. | dispatch (whole-commission green gate; "no half-states") | fixing code inside the docs phase; waiving a known-red |
| D8 | Docs-only scope fence: P8 touches ONLY `docs/runbooks/resource-cost-control.md`, `CHANGELOG.md`, `tests/test_phase8_docs_gate.py`, and the gate-evidence note. No daemon/frontend code, no migrations (assert: `ls daemon/migrations/versions/ \| grep -c resource` == 4, unchanged from P1), no config. | dispatch scope; house convention (each phase its own artifact class) | "while we're here" doc-driven code touch-ups (route via D7) |
| D9 | **OOM-delta operator content (2026-10-18):** the runbook's operator surface carries (i) a systemd memory-bound recommendation — `MemoryHigh`/`MemoryMax` drop-in for `ensemble-main.service`, because the unit ran `MemoryMax=infinity` and the box demonstrably died without bounds (recommended starting values `MemoryHigh=7G` / `MemoryMax=8G`, tunable — 7G throttle gives 1G headroom over the 5.3–6.0G steady cgroup band incl. reclaimable page cache; 8G hard cap converts box-death into unit-scoped kill (8G + PG ≈ 0.28 + demo ≈ 0.4 + OD ≈ 0.17 + OS ≈ 9.94G box — figures are approximate working values for bound-setting, not calibrated measurements), exact commands §8.1); (ii) swap provisioning framed as a **USER decision** — zero swap turns any transient allocation spike into a direct kernel OOM kill (§8.2, operator chooses size/placement); (iii) the tripwire-reading note grounded in the demonstrated evidence: 3 OOM deaths the night of Oct 17, daemon killed **23:19:14Z**, unit peak **8.4 GB**, box at **6.7 GB** at recovery, and `/system.slice` `memory.peak` ≈ **9.6 GB ≈ 97 % of box** since the Sep-26 boot = a **DEMONSTRATED kill mechanism**, not a hypothetical (§8.3). All of it is PLAN/RUNBOOK content only — agents never execute any of it against live (LR5). Code-side counterpart = **O8** (boot-time stale-subprocess sweep + `KillMode=cgroup` audit), planned in **P7** (`phase7-plan.md` — not this file); P8 only cross-references it. Exact commands live in §8 of this file and are carried into the runbook at T1/T2. | Demonstrated OOM (kernel OOM-kill PROVEN — 3 deaths Oct 17 night, `MemoryMax=infinity`, zero swap; dispatch delta 2026-10-18) | leaving the checklist bounds-free (the box demonstrably dies without them); agents tuning the live unit themselves (violates LR5); treating the tripwire as hypothetical; folding O8's code work into P8 (it is P7's slice) |

---

## 4. Task breakdown

| ID | Task | Outcome (testable) | Touchpoints | Depends on | Risks | AC |
|----|------|--------------------|-------------|------------|-------|----|
| T1 | **Draft the runbook** (D1): grep-verify every knob name/default/bounds against the merged code first (D3), then write sections (a)–(e): reading `/resources`; honesty columns per D2; knob-tuning table incl. the P7 knobs + `MALLOC_ARENA_MAX` operator note + the O1 measurement protocol with the < 5 % decision rule; anomaly `shape` glossary (spike/ramp/leak/step + drill-down path + conditioned-baseline caveat); known non-issues (P7 D11 NO-OP ledger verbatim); (f) systemd memory bounds + swap USER-decision + tripwire reading per D9/§8. | Runbook exists at the exact path with all six sections; every cited knob matches the merged config (spot-check greps pass) | `docs/runbooks/resource-cost-control.md` | P1–P7 merged | R1 | AC1 |
| T2 | **O7 doc surface** (D2/D4): add the runbook's referencing section — link to the P7-authored checklist, operator-usage text (dry-run FIRST, pattern+age gates, USER-decision items, never-agent-executed banner) plus the 2026-10-18 OOM-delta items (D9): `MemoryMax`/`MemoryHigh` recommendation (§8.1), swap USER-decision (§8.2), tripwire-reading note (§8.3), and the O8-as-code-counterpart cross-reference (P7). Do not edit the checklist file itself. | Runbook links the checklist; usage text present; OOM-delta items present; checklist file untouched (git diff scoped to the runbook) | `docs/runbooks/resource-cost-control.md` | P7 (checklist authored) | R1 | AC2 |
| T3 | **CHANGELOG entry** (D3/D5): **create `## [Unreleased]` if absent, else append** (0.21.0 structural precedent) — Added (P1–P6 subsystem), Changed/performance (O1/O3/O4 LANDED; O2 ceremony-gated FORWARD; DependencyBus lock reap FORWARD), Docs (runbook + checklist), O2 ceremony-gated line, O5/O6 deferred line. | Entry present (section created if absent), structure matches precedent, O2 gating caveat explicit | `CHANGELOG.md` | T1, T2 (final wording references them) | R4 | AC3 |
| T4 | **Docs fence test** (D5): mechanical assertions — runbook path + required sections/terms, checklist link + dry-run mention, checklist fence markers intact, CHANGELOG names the commission; **2026-10-18 delta:** `MemoryMax`/`MemoryHigh` strings present in the runbook, swap USER-decision marker present, tripwire evidence line present (D9). | `python -m pytest tests/test_phase8_docs_gate.py -q` green; future doc-drift turns the suite red | `tests/test_phase8_docs_gate.py` | T1–T3 | — | AC4 |
| T5 | **Final integration gate** (D4/D6): precondition — FE `node_modules` present (`npm install`/`npm ci`) BEFORE the FE gates run; run the four commands; capture evidence: pytest tally, three FE gate outputs, self-cost acceptance (regression test + recorded `sample_cost_ms`/`sampler_lag_ms`/`missed_samples`/buffer bytes ≤ budget), `ls daemon/migrations/versions/ \| grep -c resource` == 4, O2 no-promote assertion (no `promote.sh` invocation in branch history since base), worktree clean. | All four commands pass in ONE clean pass; evidence recorded (D4); any red routed per D7 and the gate re-run in full | `tests/`, `frontend/`, gate-evidence note | T1–T4 | R2, R3 | AC5–AC8 |
| T6 | **Close-out:** final `git status` clean; branch independently mergeable; hand-off note = promote train (with O2 aboard, ceremony-gated) awaits the user's nonce ceremony. | Clean tree; mergeable; hand-off recorded in the CHANGELOG entry's tail | (git state) | T5 | — | AC9 |

---

## 5. Acceptance criteria (testable, exact gates)

Run from the worktree `/home/nea/ensemble-src-wt-resource-cost-control`:

- **AC1** — Runbook exists at `docs/runbooks/resource-cost-control.md` with all six required sections (incl. (f), the 2026-10-18 OOM delta); cited knob names match the merged code (grep-verified). Gate: `python -m pytest tests/test_phase8_docs_gate.py -q` + review.
- **AC2** — O7 checklist referenced (link + dry-run-first + USER-decision callouts present in the runbook; checklist file itself byte-unchanged in this phase). Gate: docs fence pack + `git diff --stat` scoped to the runbook only.
- **AC3** — CHANGELOG carries the commission entry — `[Unreleased]` **created if absent, else appended to** (at the freeze HEAD `4d572f561` the top section is `## [0.23.0]`, so the fence MUST assert the CREATE path fires) — with the O2 ceremony-gated caveat and the O5/O6 deferral. Gate: docs fence pack (asserts the conditional-create behavior, never a pre-existing heading) + review against the 0.21.0 precedent.
- **AC4** — Docs fence test green. Gate: `python -m pytest tests/test_phase8_docs_gate.py -q`.
- **AC5** — **Whole-commission backend gate:** `python -m pytest tests/ -q` — full suite green, zero skips-waived (deselected cases, if any, named explicitly in the evidence per the seal-discipline rule).
- **AC6** — **Whole-commission FE gates:** `cd frontend && ./node_modules/.bin/tsc --noEmit -p tsconfig.app.json` → clean; `npm test` → green; `npm run build` → succeeds within bundle budgets (lazy `/resources` route keeps cost off the initial bundle).
- **AC7** — **Self-cost budget acceptance (commission-level criterion):** `tests/test_resource_monitor_self_cost.py` green **with fenced paths** (mktemp FAKE_HOME + escape guard — asserted by the test itself, verified at gate time; no live-shaped/HOME-derived paths anywhere in the test), and the recorded self-cost numbers satisfy **≤ 1–2 % CPU + fixed MB floor (< 20 MB)**. Gate: `python -m pytest tests/test_resource_monitor_self_cost.py -q` + evidence numbers in D4.
- **AC8** — Structural postures unchanged: `ls daemon/migrations/versions/ | grep -c resource` == 4 (no P7/P8 DDL); **zero live promotes performed by this commission** (O2 rides the train ceremony-gated). Gate: shell assertion + branch-history review.
- **AC9** — Boundary state: `git status` clean, branch mergeable, gate evidence recorded. Gate: review.

---

## 6. Risks + rollback

| ID | Risk | Impact | Mitigation / rollback |
|----|------|--------|----------------------|
| R1 | **Docs drift** — knob names/endpoints/shapes in the runbook don't match what P1–P7 actually merged | medium — the runbook misleads operators | D3 grep-verify-before-writing (house ensure rule); D5 fence test pins the required sections/terms; drift found post-merge = one docs commit. |
| R2 | **Final gate red on a pre-existing failure** from an earlier phase | high if hit — blocks commission close | D7 routing: the red goes back to the owning phase as a new commit; the FULL four-command gate re-runs after any fix. No waiver, no fix-in-P8. |
| R3 | **Self-cost measured over budget on a loaded dev box** (noisy neighbor inflates `sample_cost_ms`) | low-medium | The pinned regression test measures in a fenced, controlled harness (not ambient load); the recorded numbers state their measurement conditions; the budget criterion is the test + stored fields, not a one-off wall-clock impression. |
| R4 | **CHANGELOG merge conflict** — `CHANGELOG.md` is shared multi-tenant state; parallel commissions append to `[Unreleased]` (created if absent) | low | Append-only (create `[Unreleased]` if absent, else append under it); rebase discipline; never reorder others' entries. |
| R5 | **Checklist/runbook ambiguity causes an operator to skip the dry-run** | medium — destructive command | The runbook's usage section restates dry-run-FIRST as step 1 and the USER-decision gating; the P7 fence test enforces the ordering in the checklist itself. Agents never execute (LR5). |

**Rollback:** P8's artifacts are additive docs + one fence test — `git revert` of the P8 commit(s) removes them cleanly without touching P1–P7 code. A routed D7 fix rolls back via its owning phase's own revert discipline.

---

## 7. Dependencies

- **Blocked by:** P1–P7 ALL (the gate covers the whole commission; the runbook needs the merged knob names, endpoints, anomaly shapes, P7's checklist artifact, and the O1/O3/O4 knobs to document).
- **Blocks:** nothing — terminal slice of the commission. Downstream consumer: the user's promote ceremony (3-factor nonce gate), where the train — observability subsystem + O1/O3/O4 + O2 (ceremony-gated) — takes live effect.
- **Hands off to:** operators (runbook + hygiene checklist), the promote ceremony (train), and future commissions (O5's P2-owned fix is already landed; O6 awaits tracemalloc evidence via the P6 profiling seam; T4 `child_kind='chart_render'` policy is the future enforcement consumer).

---

## 8. Operator checklist content — OOM-delta exact commands (planned verbatim, 2026-10-18 delta)

Content spec for runbook section (f) and the O7 checklist surface (D1(f)/D2/D9). **Planning text only — never executed by agents against live (LR5).** Every step below is operator-gated.

### 8.1 systemd memory bounds for `ensemble-main.service` (recommendation; operator-gated, live)

The unit ran with `MemoryMax=infinity` → the box died without bounds — **demonstrated**: 3 OOM deaths the night of Oct 17 (daemon killed **23:19:14Z**), unit peak **8.4 GB**, box (9.9 GB RAM, zero swap) at **6.7 GB** at recovery. Recommended starting bounds: `MemoryHigh=7G` (soft throttle where reclaim pressure begins; 7G throttle gives 1G headroom over the 5.3–6.0G steady cgroup band incl. reclaimable page cache) / `MemoryMax=8G` (hard cap; 8G converts box-death into unit-scoped kill — 8G + PG ≈ 0.28 + demo ≈ 0.4 + OD ≈ 0.17 + OS ≈ 9.94G box — so systemd kills the UNIT instead of the kernel killing the BOX) — tunable once real-world headroom is observed via `/api/resources`.

```bash
# Read current bounds first (expect MemoryMax=infinity):
systemctl show ensemble-main.service -p MemoryHigh -p MemoryMax

# Drop-in override (non-interactive):
sudo mkdir -p /etc/systemd/system/ensemble-main.service.d
sudo tee /etc/systemd/system/ensemble-main.service.d/10-memory-bounds.conf >/dev/null <<'EOF'
[Service]
MemoryHigh=7G
MemoryMax=8G
EOF
sudo systemctl daemon-reload
sudo systemctl restart ensemble-main.service

# Verify applied:
systemctl show ensemble-main.service -p MemoryHigh -p MemoryMax
```

### 8.2 Swap provisioning — USER decision (operator chooses; agents NEVER execute)

Framing: the box has **zero swap**, so any transient allocation spike goes straight to the kernel OOM killer — the demonstrated Oct-17 kill mechanism. A modest swapfile is cheap pressure-valve insurance (transients page out instead of dying); some operators legitimately prefer strict no-swap + hard cgroup bounds (8.1) instead. **The keep-swapless-vs-provision call belongs to the user.** If the user opts in, example (size/placement = user's call):

```bash
# Example 8G swapfile:
sudo fallocate -l 8G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
printf '/swapfile none swap sw 0 0\n' | sudo tee -a /etc/fstab   # persist across boots
free -h   # verify
```

### 8.3 Reading the tripwire (runbook note, verbatim intent)

`/system.slice` `memory.peak` ≈ **9.6 GB ≈ 97 % of the 9.9 GB box** since the Sep-26 boot: before Oct 17 this read as a hypothetical near-full event; after the demonstrated OOM (3 deaths, daemon killed **23:19:14Z**, unit peak **8.4 GB**, box at **6.7 GB** at recovery) it is a **DEMONSTRATED kill mechanism** — treat `memory.peak` approaching ~90 % of MemTotal as an actionable alarm, not background noise. Per-unit cross-check: `systemd-cgtop -m` and `cat /sys/fs/cgroup/system.slice/ensemble-main.service/memory.peak`.

**Code-side counterpart:** **O8** — boot-time stale-subprocess sweep + `KillMode=cgroup` audit — is planned in **P7** (`phase7-plan.md`, not this file); this phase only cross-references it from the runbook/checklist surface (D2/D9).

# Rogue-Process Containment p2 — Plan Overview (open remainder)

**Lane:** rogue-containment phase 2 (board wi-eb4e5059; Emperor-approved 2026-11-11)
**Date:** 2026-11-14
**Worktree:** `/home/nea/ensemble-src-wt-rogue-contain` (branch `feature/rogue-port-containment` @ `bf4bfde6e` — verified; planning docs committed in-branch per house convention)
**Authority chain:** `.agents/shared/planning/bind-race/rogue-port-containment-analysis.md` (FINAL rev 2) GOVERNS design mechanics; the prior lane's `.agents/shared/planning/rogue-containment/` is the phase-1 record; `architecture-addendum.md` @ `ad814dae4` ENRICHES this corpus (ratified verdicts V1/V2/V3 + defect register — applied in the fix-up pass, disposition in `decisions.md` §9); where this overview and a component file disagree, the component file's verified file:line citations win and the conflict is disclosed here. Leader adjudications already made (honored, not re-litigated): (C) no new DB lane, no prod-data remediation (Emperor-gated separately); no promote; live untouched; no migrations expected (all three component plans agree — code-only); CHANGELOG catch-up anchored under v0.30.12 only per the finalize rulings (NOT v0.30.10: phantom .10 = a bump commit inside sweep ancestry, zero tags/artifacts; v0.30.11 = the absorbed sibling), NO heading backfill.

---

## Component map (what implements what)

| Commission item | Deliverable file | Status |
|---|---|---|
| 1. (B-residual) tier-1 prod-DSN fence — occ#9 closer | `component-B-tier1-prod-dsn-fence.md` | Complete (this lane's plan) |
| 2. (A-residual) bind-time socket-holder identity | `component-A2-socket-holder-identity.md` | Complete (this lane's plan) |
| 3. (A1-hygiene) 7979 token + refusal-text drift | `component-A2-socket-holder-identity.md` §5.9 (one-edit + text fix) | Complete (folded into A2) |
| 4. CHANGELOG catch-up v0.29.19→v0.30.9 | `component-E2-pins-verification-measurement.md` § CHANGELOG | Complete (gap wider than commissioned — see Contradictions) |
| 5. (E) mutation-proven pins + scratch-boot regression + (D) measurement leg | `component-E2-pins-verification-measurement.md` | Complete |
| — §9 six decisions + §7/§8 triage + (D) record | `decisions.md` | Complete (dispatcher synthesis; leader adjudicates) |
| — 9798 metrics-port fence rider | **Already shipped** in v0.29.22 (`1ec2afb1b`) — status-confirmation only; H3 read-only verify remains | Routed (B2 §5) |

## Shipped-vs-built delta table (feeds the Emperor-facing final report verbatim)

### ALREADY SHIPPED at tip `bf4bfde6e` (phase 1 — do NOT re-plan)

| Mechanism | Anchor (verified) | Commits | First contained by |
|---|---|---|---|
| A1 kill-verb × live-pattern overlay guard (ambient-only fence; bash + proc share one predicate) | `daemon/tools/bash.py:1979-2047`, enforcement `:1346-1360`; `daemon/tools/proc_tools.py:2580-2610` | `5c7c83cd9`, `67b3b86e5`, `97f3181dd` | v0.29.22 |
| A2a bounded bind-retry (30s hard ceiling, ladder 1→2→4→8→16, ≤20 attempts, exhaustion exit 1) | `daemon/services/bind_retry.py:167-171`, `:305`; invoked `daemon/__main__.py:391-519` | `f3214eb7a` | v0.29.19 |
| A2b claim-before-bind gate (exit 78 on live foreign claim; claims-dir 0700; starttime liveness; exactly-once marker surfacing) | `daemon/services/port_ownership.py:587-647`; surfaced `daemon/api.py:727-742` | `f5d4076be`, `5261254a0`, `5d9fbf8b2` | v0.29.22 |
| B-tier2 ambient-DB guard (json-absent + ambient POSTGRES_* + no ALLOW → exit 78 pre-TCP; fail-closed) | `daemon/ensemble_config.py:138-206`; preflight `daemon/__main__.py:211-212` | `1ec2afb1b` | v0.29.22 |
| B-seam spawn-boundary env scrub (12 exact names + PG* family; bash/proc/service_spawner call sites; fail-open loud) | `daemon/services/spawn_env_policy.py:51-80`; `bash.py:1431`, `proc_tools.py:941`, `service_spawner.py:202` | `5c7c83cd9`, `5d9fbf8b2` | v0.29.22 |
| **(d) rider — 9798 metrics-port DERIVE fence + managed-key retirement** (the commission's "possible rider" — ALREADY BUILT) | `daemon/services/metrics_listener.py:75-142` (`derive_metrics_port`); wired `daemon/api.py:2736-2744` | `1ec2afb1b` (pins `f4157fbf0`) | v0.29.22 |
| C rogue-write forensics (150 ckpts/399 writes quantified; verdict LEAVE-CLEAN) | `../rogue-containment/rogue-write-forensics.md` | doc-only | — |
| E phase-1 pins + mutation matrices + fenced-mirror harness | see E2 §1 inventory (13+10+26+18+26+27+10+10 tests + `boot_rogue_mirror.sh`) | `f4157fbf0` et al. | v0.29.22 |

### NEWLY BUILT by this lane (p2 — the open remainder)

| Mechanism | Plan | Closure |
|---|---|---|
| **B2 tier-1 prod-DSN fence** — `assert_prod_dsn_sanctioned()`: exact normalized `== "ensemble_prod"` predicate on the POST-resolution DSN (both tier-1 json and tier-2 env branches); sanctioned carve-out via `_self_env_marker()` reuse (live→proceed; dev/demo/sandbox→refuse-78; None→degrade, D1); env-wins poison closure; `ENSEMBLE_DB_AMBIENT_ALLOW` independent-gates; `auth/cli.py:150` exemption CLOSED; exit 78 + log-marker; fail-closed | `component-B-tier1-prod-dsn-fence.md` §4 | occ#9: the incident rogue (env-less, derived `sandbox`) resolving `ensemble_prod` is refused **by construction** |
| **A2 bind-time socket-holder identity** — new `daemon/services/port_holder.py` (pure `/proc/net/tcp{,6}` walk → holder pid/exe/cwd attribution; degrade-never-refuse on every unverifiable step; two-sample confirmation); gate refuses exit 78 on POSITIVE proof of a live foreign holder REGARDLESS of claim presence; immediate fast-refuse pre-bind (LDP-2 6a) | `component-A2-socket-holder-identity.md` §5.1-5.2, §5.5-5.6 | the 63-cycle restart-loop driver (foreign holder, no claim → 30s retry → exit 1 → relaunch) |
| **A2 claim-lineage (H1 close, LDP-1-gated)** — claim v2 records parent-cmdline lineage; same-install + lineage-mismatch ⇒ refuse; old-format claims stay non-foreign (all prior pins green); closes the `try_claim` unlink+rewrite side-defect | `component-A2-socket-holder-identity.md` §5.3 | declared hole H1 (same-install rogue "non-foreign") |
| **A2 refusal-family operator alert** — ONE family record (claims-dir co-location) + generalized exactly-once next-boot surfacing + `watchdog-watcher.sh` file-watch leg (ADR-025(b) seam; NOT launcher.sh, ceremony-invisible) | `component-A2-socket-holder-identity.md` §5.8 | analysis §5.2 MANDATORY COMPANION (a 78 is otherwise a silent-down; launcher refuse branch `:3847-3848` has no notify) |
| **A1 hygiene** — `7979` port token (one edit, `bash.py:2044`; proc inherits the shared predicate); refusal-text drift fix (dead overlay-channel advertisement removed; NO pin flip — new positive pins) | `component-A2-socket-holder-identity.md` §5.9 | demo-port parity + honest agent guidance |
| **E2 verification legs** — BF/AH/HY/LH pin families (every pin names its counter-mutant); fenced-mirror extension (3 new legs + 5 harness gap-fixes incl. the false-green claims-dir leg); 16-lane sanctioned-boot acceptance; (D) U-measurement (N≥5 cycles, verdict rule U>58s); CHANGELOG consolidated catch-up anchored to tag v0.29.22 + new-work entry; S1–S9 stage gates | `component-E2-pins-verification-measurement.md` | commission items 4+5; arms the (D) trigger with data (H2) |

### ROUTED / DEFERRED (not this lane — reasons in `decisions.md`)

**(D) launcher burst-window — CLOSED-BY-A2, CONDITIONAL** (architect addendum §0-V3 @ `ad814dae4`, leader-ratified: retire the defer-trigger once C1 A2-merge + C2 live-unit S9b check + C3 measured U≤58s hold; pre-drafted reversion to DEFER-WITH-TRIGGER if the live unit ≠78) · Lane 5 teardown budget + 60s sweep shortening (§7.4/§7.5, durability) · §7.1 boot-sweep observability · §7.2/§9-5 sweep-disarm host investigation · §9-2 rider build (shipped; H3 verify only) · H4 external-$PORT consumer (documented residual) · C remediation (Emperor-gated; none warranted) · unit-template sha-pin follow-up lane (addendum §3 — leader's call whether to queue).

## Phases

| # | Phase | File(s) | Objective | Depends on |
|---|---|---|---|---|
| 1 | B2 prod-DSN fence + BF pins | `ensemble_config.py` (additive guard), `daemon/__main__.py` preflight site, `daemon/api.py` (lifespan backstop site), `auth/cli.py`, `tests/unit/test_db_resolution_guard.py` + new pin file | Refuse non-live boots targeting `ensemble_prod`, pre-TCP, exit 78 | — |
| 2 | A2 holder discovery + gate upgrade + lineage + AH/LH pins | `daemon/services/port_holder.py` (NEW), `port_ownership.py`, `tests/unit/test_port_holder_identity.py` (NEW) + pin extensions | Positive-foreign holder ⇒ refuse 78 pre-bind regardless of claim; H1 lineage close | — (parallel to 1) |
| 3 | A2 refusal-family alert | `port_ownership.py` marker family fields, reader extension, auth/DB preflight best-effort writes, `scripts/watchdog-watcher.sh` watch-set | One durable record + one alert surface for the whole exit-78 family | 2 (+1 for family members) |
| 4 | A1 hygiene + HY pins | `bash.py:2044`, `:1350-1360` | 7979 token; honest refusal text | — (rides 1 or 2) |
| 5 | E2: harness extension → (D) measurement → sanctioned acceptance → CHANGELOG → S-gates | `harness/` legs, RESULTS file, CHANGELOG.md, matrices | Arms the trigger with data; proves guards break no house lane; honest release notes | 1+2 landed (legs); measurement BEFORE fast-refuse default-ON (leg-degeneracy rule enforces at runtime) |

Merge-order constraint (both 1 and 2 touch boot preflight in `daemon/__main__.py`): `install_root_logging()` stays first; DB-preflight (`:211-216`) BEFORE port gate (`:449-461`) — refuse-before-any-TCP (B2 §9.1; A2 §3 note: its edits are INSIDE the gate function, the `__main__` call block stays byte-stable).

## Verification gates (S1–S9 — full definitions in E2 § S-GATES)

S1 sha 4-way equality · S2 CArchive duplicate-prefix census · S3 PROMPT-BASELINE measured==declared (never hardcoded — prior "181/181" is stale, current 217) · S4 measured interpreter · S5 frozen-bundle boot smoke + `install_root_logging()` import re-assertion (§9-6) — **per-family marker channels: B2 asserts on stderr/journald (fires pre-logging, root-logger lastResort); A2/claim-gate assert on the file log (post-`:447`)** · S6 mutation matrices green (BF/AH/HY families, named counter-mutants incl. AH-M15 + the lineage-tuple-stability control, sha-verified restores) · S7 fenced-mirror legs green (incl. the claims-dir PRECONDITION fix — see Contradiction 2) · S8 sanctioned scratch-boot acceptance (16-lane census + readyz-200 E2E; **scratch-boot acceptance criteria:** scratch port (18790-class, never 9797/9798/7979), scratch PG name (never `ensemble_prod`/`ensemble_dev`/`ensemble_prod_test`), literal-port scratch config, claim taken → bind → readyz 200 → claim cleaned on shutdown, `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned, FAKE_HOME fence, per-variable PG scrub) · S9 (D) telemetry recorded (N≥5 at a pre-A2 tip — NO fast-refuse knob exists; per-cycle U + exits; fast-refuse-ON single-cycle U evidence) · **S9b live-unit `RestartPreventExitStatus` check** (read-only root-side `systemctl show ensemble-main.service -p RestartPreventExitStatus`, expect =78 — template-verified only until this leg; access-refused ⇒ rides the Emperor's promote-ceremony checklist, does NOT block staging).

**NO PROMOTE — the ceremony is the Emperor's.** Stage cut by giter; version slot from the LIVE census at stage time.

## Risks (cross-component; full registers in each component file)

| # | Risk | Sev | Mitigation |
|---|---|---|---|
| 1 | Carve-out misdetection refuses the LIVE boot at promote | critical/low | BF-2 pin + trip-risk rows re-affirmed + S5 staged boot before any ceremony; D1 polarity default = degrade |
| 2 | Fast-refuse default-ON before the (D) measurement ⇒ legacy path unmeasurable | medium/medium | Sequencing step 5 + leg-degeneracy rule (per-cycle 78 = invalid leg) |
| 3 | New gate lengthens failed-boot U past 58s (Lane-1 constraint) | critical/low | Gate exits pre-bind (immediate-78); S9 fast-refuse-ON cycle is direct evidence |
| 4 | **Disclosed (leader-mandated):** `ensemble_prod_ro` lacks the `bash_spawn_registry` grant — the forensics' external RO re-read was refused (`InsufficientPrivilegeError`); primary path self-consistent; no new DB lane, no remediation (Emperor-gated separately) | low | Disclosure only (forensics §Limitation) |
| 5 | **Disclosed (leader-mandated):** no completed checkpoint-cleanup run since 2026-11-10 ⇒ first 168h-TTL expiry risk ~2026-11-17 (rogue-written residue is LEAVE-CLEAN; the retention sweep is the normal mechanism) | watch | Monitor the next due cycle (forensics §4 FOLLOW-UP) |
| 6 | Holder /proc walk races (pid recycle) ⇒ stale attribution | medium/low | starttime verification (AH-M8) + two-sample + degrade-never-refuse |
| 7 | Env-wins closure breaks legitimate credential rotation | medium/medium | Fence refuses the prod NAME only, never the override mechanism (BF-3 positive control) |
| 8 | CHANGELOG catch-up drifts into invention | medium/low | Consolidated shape, v0.29.22 referenced BY TAG; anchor heading = **v0.30.12 only** per the finalize rulings (NOT v0.30.10 — phantom .10: a bump commit inside sweep ancestry, zero tags/artifacts; v0.30.11 = the absorbed sibling), NO backfill; missing-heading backfill routed to leader; CHANGELOG forestry owned by the finalize lane (giter) |
| 9 | Live-unit `RestartPreventExitStatus` is TEMPLATE-verified only (live unit 0600 root, operator-edited 2026-11-01, unreadable from agent context) — if ≠78, the (D) closure reverts to defer-with-trigger | low | C2/S9b root-side check at stage time; if access-refused it rides the Emperor's promote-ceremony checklist (does not block staging); `adopt-unit.sh` regenerates `:110` by construction; unit-template sha-pin gap routed as a proposed follow-up lane |

## Integration rulings (dispatcher — binding for the merge)

1. **Refusal-record namespace:** ONE namespace — claims-dir co-location (A2 §5.8, N2 convention, single reader path). B2 ships **no** durable marker file (B2 §4.8 rejected it precisely to protect A's exactly-once contract) — B2's log-markers subscribe to the family channel. No conflict exists; ruling recorded so Phase 3 lands one shape.
2. **E2 mirror leg (c)** (same-install hand-over) MUST satisfy A2's lineage contract: holder+daemon share a parent cmdline, OR the leg plants a v2 claim with matching recorded lineage. Either shape satisfies the pin; E2 confirms at integration.
3. **BF-2 carve-out mechanism:** B2's landed design (`_self_env_marker()` reuse, §4.3) is authoritative; E2's earlier "install-provenance vs env var" phrasing was written pre-sibling-landing and pins whichever lands (by design) — benign parallel-writer observation, not a defect.
4. **Dir naming:** new sibling `rogue-containment-p2/` — the prior dir is the committed phase-1 record cited by this delta table; extending it would blur the shipped-vs-built boundary (rationale in `decisions.md`).

## Contradictions (commission/inputs vs verified reality — reality wins, disclosed)

1. **9798 rider already shipped** (three-way confirmed: B2 §12-C2, E2 §CONTRACTIONS-2, dispatcher git spot-check — `1ec2afb1b`, `derive_metrics_port` live at tip). Rider reduces to the H3 read-only SELECT remainder.
2. **Prior-lane harness false-green leg:** `boot_rogue_mirror.sh` refuse-foreign leg plants claims at the pre-B1 install-scoped path the shipped gate no longer reads; without an explicit `ENSEMBLE_PORT_CLAIMS_DIR` the leg silently passes. Fixed as E2 harness gap #1 (A2 §14-C1).
3. **CHANGELOG gap wider than commissioned:** `[0.29.22]`, `[0.29.23]`, `[0.30.0]`, `[0.30.1]` headings ALL absent (heading census); the containment ship version v0.29.22 is itself among the missing. Catch-up references the TAG; backfill routed to leader.
4. **Pin-flip premise dissolved:** no test pins the bash refusal text (predicate-only pins) — new positive pins land instead (A2 §14-C3; E2 §CONTRACTIONS-6). The tier-1 "flip" is a docstring re-scope + new positive pins, not a test deletion (B2 §12-C3).
5. **Citation drift carried:** `config.py:795-798` → `:821-828`; `launcher.sh` refuse case `:3814-3816` → `:3847-3848`; `classify_exit` `:3555-3562` → `:3588-3592`; `test_main_entry.py` lives at `tests/` not `tests/unit/`. All component files cite verified positions.
6. **Prior "181/181" prompt-baseline stale** (now 217) — S3 asserts measured==declared-at-stage-time.
7. **Addendum-applied corrections (fix-up pass):** the unit `RestartPreventExitStatus=78` claim was TEMPLATE-only (reworded; C2/S9b closes it); (D) closure dated to A2's merge (C1); the E2 fast-refuse-knob expectation deleted (no knob — measurement at a pre-A2 tip); lineage normalization moved from `argv[-1]` to the script-name argument (live-wide false-refuse guard); adoption chain re-worded to template + splice inheritance. Full disposition: `decisions.md` §9.

## Anchor-drift note

All anchors in the three component files were re-verified at tip `bf4bfde6e` by their authors this session; the four drifted citations above are corrected in-place. The prior lane's `service_spawner.py` path note (phase-2 doc stale) is already superseded — the file sits at `daemon/tools/service_spawner.py` at tip and is cited there.

# Rogue-Process Containment — Plan Overview

**Lane:** rogue-containment (commission, Emperor-approved 2026-11-11; incident wi-2454ede3)
**Date:** 2026-11-12 (planner dispatch: planner[v2]; components by plan-creation workers A/B/C)
**Worktree:** `/home/nea/ensemble-src-wt-rogue-containment` — branch `feature/rogue-containment`, base tip `cace21473` (parent `e87f987a8` = origin/latest)
**Authority chain:** the READ-FIRST analysis GOVERNS — `.agents/shared/planning/bind-race/rogue-port-containment-analysis.md` (FINAL rev 2, sha256 `78e25565…`). Where the analysis contradicts the commission scope, the analysis wins and the conflict is disclosed (§ Contradictions below). Where the analysis's line anchors drifted at tip, the verified current-tip anchor map (research pass 2026-11-12) supersedes; each component file cites tip anchors.

---

## Component map (what implements what)

| Commission item | Deliverable file | Status | Commit |
|---|---|---|---|
| **A — port-ownership contract** (analysis Lane 2: (b)+(c)+(f) as ONE daemon-side mechanism + mandatory operator alert) | `component-A-port-ownership.md` | Complete | `cfc232a15` |
| **B — spawn-boundary env scrub** (analysis Lane 3: (e′) strip-by-name at the two seams + rider (d) metrics-port fence + DB-poison guard) | `component-B-spawn-boundary-scrub.md` | Complete | `51f187be6` |
| **C — rogue-write forensics** (parallel lane; recommendations-only) | `rogue-write-forensics.md` (reserved stub) | Reserved | — |
| **D — deferred burst-window fix** (analysis §5.4: DEFER with explicit trigger) | adjudication record in `component-E-regression-pins-and-sequencing.md` § D | Routed (not implemented here) | — |
| **E — regression pins + fenced mirror + sequencing + staged verification** | `component-E-regression-pins-and-sequencing.md` | Complete | (see file's commit) |

Analysis-faithfulness summary (receipts in the component files): the chosen set touches `launcher.sh` = ZERO, unit template = ZERO, live `.env` = ZERO (analysis §6 binding principle). Rejected shapes are carried as rejected — (a) make `DAEMON_PORT` authoritative, `SO_REUSEPORT` on 9797, (e)-as-policy, (g) re-arm the sweep as-is, (h) the `-gt`→`-ge` variant (§5.6). Declared hole H1 (same-install rogue running the live's own binary is non-foreign) survives by design. The bind-retry lane (Lane 1) is already MERGED at tip (verified: `_serve_with_bind_retry` `daemon/__main__.py:389`, ceiling 30.0 in-code `bind_retry.py:162`) — its remaining ceremony items are routed to the v0.29.19 ceremony lane, not this lane.

## Contradictions (commission scope vs analysis — analysis wins; leader disclosure required)

1. **Bash-tool pkill guard (commission shape A-1) is NOT in the analysis's chosen fix set.** The analysis cures the consequence (port capture) via the ownership claim and the delivery channel via (e′); it prescribes no bash-tool pattern guard. Carried per analysis; if the leader wants the pkill guard anyway, that is a commission overlay requiring its own design + pin (E-i's subject-matter exists only under that overlay).
2. **Wiring/refusing-loudly on `DAEMON_PORT` (commission shape A-3) is REJECTED** by analysis §5.6 with receipts (YAML-literal variant binds live to 8088 = outage; `DAEMON_PORT`-wins breaks ADR-014 "PORT is the SSOT" and silently inerts the launcher's serving-green probe). The inert-knob trap is instead cured by (e′) removing the inheritance + the scratch-config lever (§7.6).
3. **Commission A-2's "explicit fence env var marks a sanctioned test" exemption vs the analysis's install-dir scoping.** The analysis's shape needs no fence var for port ownership (same-install ⇒ non-foreign); a fence var appears only in component B's DB-resolution guard (`ENSEMBLE_DB_AMBIENT_ALLOW`), where the analysis is silent.
4. **Commission B's DB root (`data/ensemble.json` postgres pin outranking the .env fence) is analysis-silent.** The analysis's Lane 3 strip list covers the four bind vars only. Component B extends the (e′) MECHANISM to the DB-poison family under blueprint + critical-note authority; no analysis receipts exist for the DB half. Disclosed, not analysis-contradicted.

## Leader decision points (consolidated; detail in component files)

| # | Decision | Where presented | Analysis §9 |
|---|---|---|---|
| 1 | Burst-window fix: defer-with-trigger (recommended) vs adopt now | component-E § D | §9.1 |
| 2 | 9798 fence shape: derive-from-main-port (recommended default: derive + env-wins escape hatch) vs env-wins validator | component-B rider (d) | §9.2 |
| 3 | Refusal exit code: reuse 78 (recommended, carried as decided) | component-A placement contract | §9.3 |
| 4 | Operator-alert channel for the refusal path (recommended: marker line + durable refusal-marker file) | component-A D8 | §9.4 |
| 5 | 10-2x sweep-disarm host investigation scope | component-E routing table | §9.5 |
| 6 | Frozen-bundle `install_root_logging()` verify — routed to the v0.29.19 ceremony lane | component-E routing table | §9.6 |

## Sequencing (summary — full sequence in component-E)

developer (code + tests, components A/B in one release train) → house gates → reviewer → tester (mutation-proof + gates) → stage cut (giter; version slot from LIVE census at stage time, refs floor v0.29.19) → staged verification proving E-i/ii/iii legs FROM the staged artifact (4-way sha + sidecar witness, CArchive census full-prefix `uniq -d`, PROMPT-BASELINE both trees, measured interpreter cpython-3.14). **NO promote — the ceremony is the Emperor's.**

## Component C reservation

`rogue-write-forensics.md` is reserved for the parallel READ-ONLY forensics lane (maintenancer; ensemble_prod; no repo write path). Its content arrives as recommendations-only; any destructive remediation is a SEPARATE Emperor-gated lane. See the stub header in this directory.

## Anchor-drift note

The analysis was authored against base `efa8aaae2`; the component files cite current-tip (`cace21473`) anchors per the verified drift map (notable: `daemon/config.py` +26 uniform shift below :591; `daemon/__main__.py` restructured — sweep `:550-553`, logging `:445`, pre-bind `:472-473`; `launcher.sh` run-loop +33 — refuse branch `:3847-3849`, classify_exit `:3587-3595`). Where a component file cites an analysis-era anchor, the tip anchor governs.

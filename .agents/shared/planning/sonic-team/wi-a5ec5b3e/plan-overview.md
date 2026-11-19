# wi-a5ec5b3e — 2026-11-18 triple-outage guard inversion (sonic commission)

BASE: 2618eb212074a41912c2907d3ce6814b3d238c7f (origin/latest @ 2026-11-18 14:53:24Z)
COMPLEXITY VERDICT: MEDIUM (surgical guard fix + read-only hunt + tiny append + scripted release; disjoint files).
FENCE: no restart/stop/kill of anything for testing; no live mutations outside stage.sh flow; live v0.31.2 untouched.

## Incident summary (verified)
Three outages 2026-11-18: #1 06:57:27->08:05:18 (coder e1426d90), #2 09:17:36->13:51 (worker 784c0e90),
#3 14:00:24->14:07:39 (worker 72f8c435, tidier lineage 019a9a9a->0269bb55; recovered by ensemble-keepalive watchdog).
All: agent bash running `cd /home/nea/ensemble-src && echo "=== system_restart / pok / nest-..."` audit-style
inline scripts that end in systemctl stop/restart of ensemble-main via COMPOSED argv.
Guard failure: daemon/tools/bash.py `_pkill_overlay_violation` (:1977-2047) matches live unit names by plain
SUBSTRING on the command text -> variable-indirected unit tokens pass clean; read-only status over-blocked.
proc_tools.py has ZERO systemctl screening.

## EMPIRICAL CONFIRMATION (2026-11-18 ~15:05Z, this lane)
sonic-developer's own brief-writing heredoc was REFUSED by the guard: command TEXT contained both a kill-like
shape (prose "systemctl stop/restart") and a live pattern substring -> refused, though it was a doc write.
Over-block leg reproduced first-hand; under-block leg is the three outages. Both legs of the inversion confirmed.

## Modules (merge-order slots)
1. M1 guard-inversion — wt-wia5ec5b3e-guard — sonic/wi-a5ec5b3e/guard-inversion
2. M2 script-hunt — wt-wia5ec5b3e-hunt — sonic/wi-a5ec5b3e/script-hunt
3. M3 incident-append — wt-wia5ec5b3e-doc — sonic/wi-a5ec5b3e/incident-append
4. M4 release-bump — wt-wia5ec5b3e-relcut — sonic/wi-a5ec5b3e/release-bump

## Integration (sonic-developer)
Merge slots 1->4 (--no-ff each) into sonic/wi-a5ec5b3e/integration; guard-test verify on merged tree;
HANDOFF.md commit; annotated tag v0.31.3 at tip; stage via scripts/upgrade/stage.sh from clean detached stage
worktree (fresh build; rm -rf dist/ frontend/dist/ first). NO promote — Emperor's ceremony owns the flip.

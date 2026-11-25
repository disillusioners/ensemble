SONIC-MODULE-BRIEF — guard-inversion (M1, slot 1)
TASK: Fix the intent-match inversion in the agent tool-lane systemctl guard and extend it to the proc lane.
ROOT CAUSE (verified, read-only): daemon/tools/bash.py `_pkill_overlay_is_kill_like` / `_pkill_overlay_violation`
(:1977-2047) correctly classifies systemctl mutating verbs, but the live-unit check is a plain SUBSTRING match
(`if name in lower` over ensemble-prod/ensemble-daemon/ensemble-main/ensemble-demo/agents-ensemble + \b9797\b/\b9798\b).
Variable-indirected or composed unit tokens never produce the literal substring -> ALLOWED. Three live outages
today came through exactly that hole. Meanwhile read-only `systemctl status ensemble-main` has been BLOCKED in
practice (over-broad gate somewhere in the chain) — map the FULL refusal chain in the bash tool path before editing.
EMPIRICAL EVIDENCE (2026-11-18 ~15:05Z): sonic-developer's own doc-writing heredoc was refused because its TEXT
contained prose "systemctl stop/restart" + a live pattern substring — the over-block leg reproduced inside this
commission. The under-block leg = the three outages (composed argv passed clean).
CONTRACT:
1. Mutating systemctl verbs (keep current set: stop/restart/kill/disable/mask/reload/daemon-reload/enable/start/
   try-restart/isolate) targeting the ensemble's own unit(s) are HARD-DENIED from the agent tool lane REGARDLESS
   of token composition: statically resolve same-string variable assignments and string composition when possible;
   when a mutating verb's unit operand is INDIRECTED / composed / not statically resolvable to a clearly-non-ensemble
   unit -> FAIL-CLOSED refuse. Plain literal ensemble-unit tokens keep refusing as today.
2. Read-only verbs (status/show/is-active/is-enabled/is-failed/cat/list-units/list-unit-files/get-default/help)
   against ANY unit INCLUDING ensemble-main must be ALLOWED end-to-end through the whole tool chain — find and fix
   whatever over-broad gate blocks read-only inspection today. NOTE: doc-writes/prose containing the tokens are
   NOT kill intent — the fixed matcher must not refuse non-executing text contexts the current one does when you
   can detect them safely; where you cannot, keep fail-closed and say so in the report.
3. The guard must ALSO cover the proc lane: daemon/tools/proc_tools.py (proc_run) currently has ZERO systemctl
   screening — apply the same refusal for composed kill-shaped starts; read-only allowed.
4. Keep the pure-function shape (no I/O, unit-testable) and the ENSEMBLE_FENCED_TEST_CONTEXT ambient-env escape
   (daemon-set ambient ONLY; the agent tool-call env overlay stays IGNORED — fix-round 2/B6 semantics).
   Keep pkill/killall/fuser/kill/xargs-kill legs intact.
DESIGN: extract the matcher into a shared pure module (suggested daemon/tools/_systemctl_guard.py) imported by
bash.py AND proc_tools.py. bash.py keeps its public names working (re-export/thin wrappers) so existing tests
tests/unit/test_pkill_overlay_pins.py + tests/unit/test_pkill_overlay_guard.py stay green UNLESS a pin encodes
the broken direction — then update it and justify in your report.
FILES (yours ONLY — disjoint): daemon/tools/bash.py, daemon/tools/proc_tools.py, NEW daemon/tools/_systemctl_guard.py,
NEW tests/unit/tools/test_systemctl_intent_guard.py; extend tests/unit/test_pkill_overlay_pins.py ONLY if needed.
MUTATION-PROVEN PINS (house doctrine; each leg must go RED under its OWN counter-mutation, non-vacuous):
- DENY leg (RED when composition-resolution is removed/loosened): `UNIT=ensemble-main.service; systemctl restart "$UNIT"`,
  `systemctl stop ENS-"main"`, `systemctl restart "$(echo ensemble-main)"`, `S=ensemble; systemctl restart ${S}-main`,
  same shapes via the proc lane. Also pin: literal `systemctl restart ensemble-main` still refused.
- ALLOW leg (RED when the readonly allowlist is dropped / deny made over-broad): `systemctl status ensemble-main`,
  `systemctl show ensemble-main`, `systemctl is-active ensemble-main --no-pager`, `systemctl cat ensemble-main | head -5`,
  `systemctl list-units | grep ensemble`, same via proc lane.
- Run counter-mutation legs in a THROWAWAY DETACHED worktree under /tmp (git worktree add --detach ...); sha256 per
  plant+restore logged in report; leave porcelain clean; remove it after.
OPERATIONAL HAZARD (you WILL hit it): the CURRENT guard refuses any single bash command whose text contains BOTH
a kill-like shape (even in prose/heredoc/code you are WRITING) and a live pattern substring. Author test fixtures
and code via placeholder tokens + a separate sed reconstruct (or base64), never by self-granting
ENSEMBLE_FENCED_TEST_CONTEXT. Plan your file writes accordingly.
TEST DISCIPLINE: worktree .venv only (export PATH=/home/nea/.local/bin:$PATH; uv sync; NEVER main .venv, no bare python).
.env already fenced (ensemble_dev @127.0.0.1:5432, DAEMON_PORT=8079). Park logs in /tmp/wia5ec5b3e-guard/.
Run the SAME pytest scope at BASE (2618eb212, detached scratch worktree under /tmp) and DIFF failing sets —
tests/unit/services/ carries ~17 pre-existing reds at recent bases; prove zero new reds are yours. Known gotchas:
pass explicit test paths (no testpaths fallback), capture full stdout+stderr to file, name /tmp scratch with PID suffixes.
PACK REGISTRATION: if your pins constitute a new gate pack, register it in the MAIN repo .agents/tester/PACKS.md
(append a section; NO commit inside /home/nea/ensemble-src — that checkout is detached HEAD, never a commit target)
AND mirror the same section into your worktree's .agents/tester/PACKS.md copy (committed in-branch).
NEVER: restart/stop/kill/mask anything for testing (static + unit tests ONLY); touch /home/nea/agents-ensemble;
git stash; git add -A; push.
REPORT: [sonic-module-report-v2] — files, what changed, mutation-leg evidence (mutations + sha256 log), base-vs-head
red-diff result, untested seams, risks, verdict.

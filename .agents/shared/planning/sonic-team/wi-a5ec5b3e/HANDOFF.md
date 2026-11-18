# HANDOFF — wi-a5ec5b3e (2026-11-18 triple-outage guard inversion) — integration by sonic-developer

BASE 2618eb212 (origin/latest 2026-11-18 14:53:24Z) → integration tip (see git log; range 2618eb212..HEAD).
Modules: M1 guard-inversion 55ef81af3 (+M1-R re-brief be7254457, feab826ef) | M2 script-hunt 587e0d223 |
M3 incident-append 11b16ff29 | M4 release-bump 8e2affe2b. Release: v0.31.3 STAGED, NO promote.

## Files touched across modules
- M1/M1-R: daemon/tools/bash.py, daemon/tools/proc_tools.py, daemon/tools/_systemctl_guard.py (NEW, 891+L),
  tests/unit/tools/test_systemctl_intent_guard.py (NEW, 89 pins), .agents/tester/PACKS.md (branch copy; the
  MAIN checkout /home/nea/ensemble-src copy carries the identical section UNCOMMITTED by design — detached HEAD,
  never a commit target).
- M2: .agents/shared/planning/sonic-team/wi-a5ec5b3e/D2-script-source-hunt.md (NEW).
- M3: .agents/shared/planning/sonic-team/wi-a5ec5b3e/D3-append-receipt.md (NEW) + LIVE
  /home/nea/agents-ensemble/.agents/shared/incident-2026-11-18-outage.md (append-only +2784B; sha ebd37808…→8d4bb3c6…;
  strict-prefix cmp verified; ONLY live-tree write of the commission, blast-radius-scanned).
- M4: pyproject.toml, daemon/__init__.py, tests/test_api.py, uv.lock, CHANGELOG.md (0.31.2→0.31.3).
- Integration (sonic-developer): this planning dir (plan-overview, 4 briefs, HANDOFF).

## What changed
- D1: the agent tool-lane systemctl guard is REWRITTEN as a pure shared module (execution-position intent
  matching): composed/variable-indirected/quoted/ANSI-C unit operands are statically resolved; unresolvable or
  globbed operands FAIL CLOSED; runner laundering (bash -c, heredoc-stdin, eval, xargs) recursed into; read-only
  verbs (status/show/cat/is-active/is-enabled/is-failed/list-*/get-default/help) ALLOWED against any unit incl.
  ensemble-main; mutating verbs vs the ensemble unit family hard-denied regardless of composition. NEW in M1-R: the
  ACTUAL outage mechanism — agent-lane execution of scripts/stop-ensemble.sh (systemctl over an env-composed
  unit, escalates to unit-kill) — refused by basename in every execution position, plus the env-primed rule
  (any script invocation carrying ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED:*). proc lane covered via the same
  predicate. scripts/stop-ensemble.sh itself untouched (operator-lane machinery).
- D2 finding: no shared audit script exists — the "system_restart / pok / nest-" header was an LLM-composed
  echo; the real mechanism is the stop-wrapper (sentinel mtimes match all three journald stop events
  sub-second: 06:57:27.720, 09:16:26.022, 14:00:24.908). Full evidence + watch list in D2 doc.
- D3: live incident doc now carries occurrence #3, all-three attributions (e1426d90 / 784c0e90 / 72f8c435,
  tidier lineage 019a9a9a→0269bb55) and the guard failure mode.
- D4: version 0.31.3 + CHANGELOG; staged release (see Bash-raises/risks).

## What is UNTESTED
- Live daemon runtime behavior of the new guard — fence forbade running/stopping anything; all evidence is
  static + unit (the runtime refusal seams in bash.py/proc_tools.py are exercised via record-instead-of-spawn
  proc pins and direct predicate calls only).
- The staged v0.31.3 binary was NOT booted or smoke-probed (no-promote fence; ceremony owns it).
- Known residual guard gaps (M1-R disclosed): reader-substitution laundering (bash -c "$(cat
  scripts/stop-ensemble.sh)") NOT caught; unresolvable-indirect script paths without the supervision marker
  allowed; renamed wrapper copies without marker remain a theoretical gap; bash -n on the wrapper is refused
  (accepted over-block); marker forwarding across segments is conservative (may over-refuse).
- Kill-legs remain text-scoped: prose containing a kill-shaped phrase + a live token inside heredocs is still
  refused (the doc-write over-block survives for kill-style prose only; the systemctl leg is fixed).
- Full tests/unit/tools/ (5182 nodes) not run — scoped suites only (pack 128/128 + bash/proc scoped sets,
  base-vs-head failing sets byte-identical: 5 pre-existing ambient reds).
- D2: the exact fatal command text is unrecoverable (daemon died mid-turn; checkpoint lags the crash) —
  sentinel/journald evidence is authoritative for mechanism.
- tests/test_api.py version assertion not executed (M4: grep-verified only).

## Known risks
- FOURTH-OCCURRENCE RISK IS LIVE UNTIL PROMOTE: the live daemon still runs the OLD guard until the Emperor's
  ceremony flips to v0.31.3. D2 watch list TOP items: 784c0e90 (TERMINATED but auto-resumed twice — ANY
  send_message revives it with the audit-header checkpoint), 019a9a9a (leader, WAITING_CHILDREN, still firing
  children as of 17:20), revive-eligible 0269bb55 / 72f8c435 / e1426d90, three live worker-class instances
  (9f0de677, d2b2c968, 31565dc4). Interim net: ensemble-keepalive watchdog (~3-7 min bound).
- Release delta contains comment-only edits to 5 applied migration files (Emperor→user terminology, from the
  already-landed E-6 lane — pre-existing on latest before our base; no DDL). ENSEMBLE_ROLLBACK_SAFE=1
  legitimacy: our delta (2618eb212..tag) adds NO migration and NO destructive DDL.
- PACKS.md main-checkout registration is uncommitted by design (flagged for the tester lane).
- Original M1 coder instance vanished from the registry instead of being revivable-COMPLETED (anomaly
  eb8f9e67) — replacement coder carried the same branch; nothing lost, but the revive contract looks broken.
- The staged binary is PyInstaller-fresh from the tagged tree; manifest/sidecars must be checked at ceremony
  time per the standard 4-way sha discipline.

## How to verify (on the merged/tagged tree — worktree venv only, PATH+=/home/nea/.local/bin)
- Gate pack (128 nodes, ~4s): env ENSEMBLE_SELF_ENV=dev POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5432
  POSTGRES_DB=ensemble_dev POSTGRES_USER=ensemble_dev_app POSTGRES_PASSWORD=… .venv/bin/python -m pytest -q
  -p no:randomly --timeout=120 tests/unit/tools/test_systemctl_intent_guard.py
  tests/unit/test_pkill_overlay_pins.py tests/unit/test_pkill_overlay_guard.py
  tests/unit/test_spawn_boundary_scrub.py   → expect 128 passed.
- Mutation evidence: /tmp/wia5ec5b3e-guard/m5-sha256.log (M5a/b/c plants+restores).
- Static: grep stop-ensemble daemon/tools/_systemctl_guard.py (basename rule + marker rule);
  git diff 2618eb212..HEAD --stat -- scripts/ → EMPTY.
- Release: git describe --tags --exact-match HEAD → v0.31.3; manifest at releases/v0.31.3/manifest.json
  (4-way sha at ceremony); state.json current stays v0.31.2 until promote.

## Bash-raises used
none — every bash call stayed under the 600 s default; the stage.sh build ran as a background proc.

## Complexity self-assessment
MEDIUM (verdict at plan-open, unchanged). One max-1 re-dispatch consumed: M1-R (stop-ensemble.sh wrapper
closure) — evidence-driven extension after M2 refuted the shared-script premise and identified the wrapper as
the real mechanism. No handover raised; no override consumed.

## POSTSCRIPT (2026-11-18 ~19:0xZ) — VERSION-SLOT COLLISION, FINALIZE STOP
A concurrent lane (gc-fe-surfaces, tagger giter) cut and PUSHED v0.31.3 (tag 18:21:44Z -> bump e062f4b75)
and STAGED releases/v0.31.3 into the live install at 18:31:35Z while our coders worked. origin/latest is
now c48a55431 (docs gc-fe-surfaces) — moved past our verified base 2618eb212. Their release does NOT
contain this commission's guard fix (the shared guard module is ABSENT on latest; their 0.31.3 CHANGELOG
section is group-chat FE content only). Overlapping files base..newlatest vs ours: the 5 bump files +
.agents/tester/PACKS.md (both lanes appended sections — keep-both on merge).
Consequences: our local integration (tip below) is COMPLETE and verified (pack 128/128) but UNPUSHED and
UNTAGGED by us; tag+stage are BLOCKED pending leader adjudication. Options: (a) re-slot to v0.31.4 — merge
new latest into our integration, CHANGELOG/uv.lock re-bounce to the M4 owner, PACKS keep-both, tag, stage;
(b) leader-authorized v0.31.3 re-cut including the fix (their release is staged-not-promoted; re-stage
overwrites in place — cross-lane call). Sequencing vs their pending promote ceremony is leader-owned.
Local integration tip at stop: see git log -1 HEAD (HANDOFF commit). No push of our line occurred.

## POSTSCRIPT v2 (2026-11-18 ~19:4xZ) — LEADER-ADJUDICATED RE-SLOT TO v0.31.4
Adjudication: option (a) — v0.31.3 (gc-fe-surfaces, already staged-not-promoted) stands; our guard fix rides
v0.31.4 folding their content + the fix. Continuation branch sonic/wi-a5ec5b3e/continuation (this worktree,
NEW per house rule) from frozen fa56a27d3; origin/latest c48a55431 absorbed via merge --no-ff (merge commit
89997afbb, M4 owner resolved CHANGELOG both-sides-newest-first, PACKS keep-both verified); re-bump commit
a296ec713 -> version 0.31.4 at 4 sites. Isolated our-work range: c48a55431..<tag tip>.
GATE RE-RUN ON POST-MERGE TIP (leader condition — green or no tag): gate pack 128/128
(/tmp/wia5ec5b3e-int2/pack.log); mutation legs ALL RED under own plants, restores sha-verified, porcelain
clean, final 89/89 (/tmp/wia5ec5b3e-int2/mutation-legs.log; M3 first plant mis-aimed -> DISCARDED, corrected
plant = raw-text scan at entry, 10F; disclosure mirrors M1-R's own discarded-plant precedent).
Tag v0.31.4 (annotated) at THIS commit = branch tip; push: HEAD:latest + sonic/wi-a5ec5b3e/continuation +
tag (L1 tip-identity for stage). Stage: releases/v0.31.4 via the normal stage flow from a clean detached
worktree at the tag, fresh build (dist/ + frontend/dist/ removed first), ENSEMBLE_UPGRADE_LIVE=1
ENSEMBLE_ROLLBACK_SAFE=1 (delta since v0.31.3: our fix + docs; no new migrations; additive-only).
NO promote — Emperor's ceremony owns the flip. Staged-at evidence of record: the live
releases/v0.31.4/manifest.json staged_at field + releases/state.json history (authoritative timestamps).

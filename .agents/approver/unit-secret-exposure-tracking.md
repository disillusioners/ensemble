# Approver Tracking: unit-secret-exposure

## Iteration 001 — FINAL — APPROVED (2026-10-23)

- Artifact: .agents/shared/planning/unit-secret-exposure/ (plan-overview.md + phase1-5-plan.md + decisions.md + architecture-recommendation.md; 602 lines)
- Worktree: /home/nea/ensemble-src-wt-unit-secret-exposure @ 9fc95176b (branch fix/unit-secret-exposure, no fix commits at approval time)
- Worker: approve-worker-unit-secret-exposure (dec7e7de-2891-4157-a3db-30700490dcfe), skill plan-approval, cold context, single-pass, read-only
- Worker verdict: APPROVED — 0 blocking, 6 notes
- Aggregation: APPROVED (no downgrades, no merges, no upgrades — worker classification stands)

### Verdict rationale (worker, evidence-verified)
- Mandate achieved: zero .env-derived Environment= lines in generated units, launcher as single runtime env source, docs/tests amended, .env mode-0600 at every write site, stage-only, no promote.
- ~60 code anchors independently re-verified read-only against the worktree — no anchor or claim misstates the code.
- A1 (lone `Environment=` full-list reset) confirmed correct against host systemd 255.4 man page semantics; A9 payload-delta correction confirmed exactly (agents/ 9 files inherited, frontend/plugins/config.yaml/launcher.sh byte-identical, 109==109 migrations).
- .env write-site census (5 sites + comment-only adopt path), consumer census (no in-repo consumer of unit-env PORT/POSTGRES_*), threat model (systemd.exec(5) D-Bus exposure premise), test-plan mutation-soundness (8 breaking asserts exact; template-copy mutation leg RED; NAMED-SKIP precedent verbatim), W9 tag-peeling hazard (local v0.27.5 -> 6bebb9ba vs remote -> 9fc95176b == HEAD) — all independently confirmed.
- Stage-only constraint holds in every phase; the one live-boundary delta (stage-time chmod on live .env) is disclosed and routed to the morning report.

### Notes carried forward (non-blocking; N1 + N2 are DEV-PHASE MUST-FIX)
1. N1 (MUST-FIX before dev runs the SC3 grep) — SC3 literally unsatisfiable as written: plan-overview.md:76 requires zero `__ENV_LINES__` hits repo-wide excluding only .agents/shared/planning/, but D11 (decisions.md:127) / §3.6 forbid editing historical archives, and one verified hit remains at docs/investigations/2026-10-18-o8-stale-subprocess-sweep-and-killmode-audit.md:140. Fix: scope SC3 to live surfaces (scripts/ + scripts/systemd/ + docs/runbooks/, excluding .agents/shared/planning/ and docs/investigations/).
2. N2 (MUST-FIX in dev phase) — two stale-prose sites outside every task's touchpoints and outside T3.6's needle list/scope: tests/test_adopt_unit.sh:16-20 ("substituted User/port/PG ... secret masking" pack header) and docs/runbooks/systemd-adoption.md:50 ("secret masking" in DRY_RUN list) — both become false after P1/P4. Fix: add both sites to T3.2/T4.7 touchpoints; extend T3.6 needles with `secret masking|substituted User/port/PG` and scope with tests/.
3. N3 — "Runtime effect on the running service: ZERO" (plan-overview.md:98, architecture-recommendation.md:120) is unconditioned on stage-as-install-owner: stage.sh:684-689 rewrites live .env via `sed > tmp && mv -f` (inode replacement re-owns to staging uid); P5's chmod 600 would then make it unreadable by a daemon under another user -> launcher WARN -> no POSTGRES_* -> D4 boot-block family. Mitigated in practice (lane stages as nea, the install owner). Fix: extend the D5a/A3' precondition to the stage path; condition the §8 claim.
4. N4 — five anchor imprecisions (phase1-plan.md:22-23; decisions.md:65/:86/:94; arch §4.3) — all mitigated by D6 content-string-primary doctrine.
5. N5 — AC2.4 scan scope (phase2-plan.md:31: scripts/ launcher.sh tests/) narrower than D9's stated gate (decisions.md:109: whole repo minus docs/ + archives vs 3-entry whitelist); both pass today (verified); texts should agree.
6. N6 — D4 exit-taxonomy prose omits the ensemble.json fallback holding DEFAULT PG config (daemon/__main__.py:127-132, daemon/ensemble_config.py:22-29) — conclusion (exit 75 installed-norm; watch the journal) holds; mechanism sentence could be sharper.

### Unverified (worker-disclosed; not plan defects)
- Live unit /etc/systemd/system/ensemble-main.service not read (0600, secret hygiene) — plan mandates a scratch-unit empirical check before live application (A1 note (i)).
- ensemble-demo.service content not read — W6 census line covers it in the morning runbook.
- No systemd mutation performed (read-only lane posture) — drop-in reset verified against man page + plan's own scratch-check requirement.
- Host facts not re-derived: /home/nea mode 750 + empty nea supplementary group; nea sudo NOPASSWD (caller-stated) — used only for ∅ exclusion-delta framing and deploy-ownership note.

Superseded active: mcps-gateway-interop APPROVED 002 FINAL 2026-10-22 (tracking: .agents/approver/mcps-gateway-interop-tracking.md); earlier chain preserved there.

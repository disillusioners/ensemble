# OAuth2 Rollout Verification — Default-OFF Merge Checklist (T6.3)

Artifact for the merge PR of `feature/oauth2-auth`. Reproduces the
§8 default-OFF zero-disruption strategy evidence (plan-overview.md) as
a checklist the implementer attaches to the merge description. Posture
per D6.2: **merge with auth OFF** — enabling is a post-merge operator
config+restart act (runbook §1), never a side effect of the merge.

Evidence base at writing (Phase 6, 2026-10-11, worktree
`ensemble-src-wt-oauth2-auth` @ 311648a83):

* **P4 verification (tester RESULTS `2026-10-11-oauth2-p4-verification.md`):**
  7/7 gates PASS (G4.1–G4.6 + OFF-equivalence), 8/8 packs, **82/82
  tests green**, zero OAuth2/P4 defects.
* **P5 estate:** **110 passed + 1 skip** (the single skip is the
  AC-42 mask-on-read test, skip-guarded pending §12 4(c)
  adjudication) + merge-gate riders **44 + 1 skip**
  (`tests/unit/test_oauth2_merge_gate_riders.py` et al.);
  `tests/e2e/test_auth_lifecycle.py` 12/12 (AC-S1…S4).
* **G6.1 command verification (this phase):** CLI `--help` semantics +
  curl shapes verified in-worktree against the P5 ASGI fixtures
  (recorded in runbook §2b); daemon-boot-class commands CITED from the
  P5 evidence above (no PG cluster booted in P6) — split disclosed in
  the Phase 6 completion report.

## Merge-PR checklist

### A. Suite green (structural default-OFF, proof obligations 1–2)

- [ ] Scoped gate on the merge commit:
      `.venv/bin/python -m pytest tests/unit/test_runbook_sections.py tests/e2e/test_auth_lifecycle.py tests/integration/test_module_lane_isolation.py tests/test_reserved_seams_p5.py -q`
      — all green, skip count **exactly 1** (AC-42 mask-on-read).
- [ ] Config-default verification on the merge commit (G6.2 / AC-2 /
      proof obligation 1): `tests/unit/test_auth_config.py` +
      `tests/unit/test_auth_off_gating.py` green — default
      `enabled=False`; absent `auth:` block parses clean;
      ENABLED=false + everything-else-set boots normally.
- [ ] Introspection leg (proof obligation 2 / AC-2):
      `tests/integration/test_auth_off_state.py` green — with auth
      disabled, `AuthMiddleware` absent from `app.user_middleware`;
      no auth cookie in any recorded response.
- [ ] Full existing suite green with the auth module present and
      `enabled=false` (NFR-1; NFR-10 < 5 min, no Docker).

### B. Snapshot diff (SC-1 / AC-1 / AC-S1)

- [ ] OFF-state response bodies for `/api/*`, `/views/*`, `/livez`,
      `/readyz`, `/api/info` captured on the merge commit and diffed
      against the pre-build baseline → **0 diff**.
- [ ] P5 boot-e2e auth canary green: `tests/e2e/auth_health_probes.sh`
      — 5/5 public probes NOT-401 against an auth-ON daemon
      (`/readyz` may be 200-degraded; 503 = readiness contract, not an
      auth regression).

### C. Consumer walk (AC-32 / AC-S1 / AC-41)

- [ ] YES-UNCHANGED consumers (10 rows post-v1.4, [REQ] §7.2) run
      unmodified against an OFF-state daemon.
- [ ] Module-lane rows 17–18 (external webhook senders, MCP-KB
      clients) run with NO OAuth2 credential in BOTH states,
      identical behavior (AC-41); negative cross-lane matrix green
      (`tests/integration/test_module_lane_isolation.py`, AC-40).
- [ ] YES-WITH-CONFIG consumers (6 rows) run against an auth-ON
      daemon with minted bearers — covered by the P5 consumer-matrix
      e2e leg; on a live deployment this repeats as runbook §1
      verification.

### D. Canary-unmodified assertion (G5.3 / F7 — binding)

- [ ] `frontend/scripts/boot-e2e-maintenance-daemon.sh` and
      `frontend/scripts/boot-e2e-snapshots-daemon.sh` are UNMODIFIED:
      `git diff c8b6f21f8 -- frontend/scripts/boot-e2e-maintenance-daemon.sh frontend/scripts/boot-e2e-snapshots-daemon.sh`
      → **empty** (verified at Phase 6, 2026-10-11: zero diff vs
      c8b6f21f8). TA Debt #7 is SUPERSEDED — no implementer may add
      bearer-token support to the canary scripts (the canary route
      `/api/maintenance/checkpoint-cleanup/availability` is PUBLIC by
      route-metadata derivation, D18/D1/FR-22).
- [ ] Both canaries `bash -n` clean and their P5 legs green.

### E. Docs + cross-links riding the merge

- [ ] `docs/runbooks/oauth2-auth.md` — 7/7 FR-35 sections present
      (`tests/unit/test_runbook_sections.py` green; AC-35
      mechanized).
- [ ] `CHANGELOG.md` — OAuth2 entry under `[Unreleased]` with the R3
      CN cross-link (`257488dc-c0b1-42d4-8581-f97cb4d55f72`).
- [ ] Planning-set annotations landed (T6.5): R3 row (plan-overview
      §6), D4.7 census, §7.5 fixture ground truth, phase4 §5 stale
      prose + label count, phase6 T6.1(c) discharge.

## Residuals / disclosed splits

* Daemon-boot-class commands in the runbook §1/§5 (systemd restart,
  probe invocation against a live port) are CITED from P5 evidence
  (boot-e2e-auth-daemon.sh `bash -n` clean; lifecycle e2e 12/12; P4
  verification boots) — NOT re-executed in Phase 6 (no PG cluster
  boot in a docs phase). G6.1 split disclosed.
* The single suite skip is AC-42 (webhook-secret mask-on-read),
  skip-guarded until the leader adjudicates plan-overview §12 item
  4(c) — do NOT un-skip in this merge.
* Pre-existing F1 (fresh-SQLite boot trap, CN 43852fbd) is NOT an
  OAuth2 defect; verification boots on SQLite use the documented
  ledger-pre-mark workaround (`tests/fakes/f1_premark_helper.py`).

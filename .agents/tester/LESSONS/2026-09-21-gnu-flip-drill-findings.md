# GNU Host Findings — Linux Atomic-Flip Drill (2026-09-21)

Branch `fix/portable-atomic-flip-linux` @ a89c56cb. Drill worker 84adeb01. Full report: RESULTS/2026-09-21-linux-flip-drill.md.

## 1. `_iso_to_epoch` is BSD-only → launcher sweep + adopt_stale_txn fail closed on GNU (NEW, pre-existing, blocker-class)
- **Site**: `scripts/upgrade/lib.sh:86` — `date -ju -f <fmt>` has no GNU equivalent form used; always errors on GNU.
- **Live evidence (this drill)**: `adopt_stale_txn` refuses with `WARN: … unparseable started_at … — pipeline-busy`; launcher journal sweep logs `WARN: journal sweep: in_flight started_at unparseable ('…') — leaving untouched, boot proceeds`.
- **Blast radius on GNU**: orphan-recovery is dead — no repoint / sweep_rollback / quarantine / window counter; Phase-H cross-writer sweep_rollback unreachable by construction; 24h rollback window NEVER expires; lock can stay held. Boot-and-continue (ADR-033) still happens (fail-closed, not fail-open) — so this is a recovery-path gap, not a safety violation.
- **Fix direction (needs own commission + review)**: portable ISO→epoch (GNU `date -d` ↔ BSD `date -ju -f`), same dispatch pattern as the atomic flip fix; unit tests + drill Phase-H reachability on GNU.
- **Not hit on demo happy path**: demo promote resume has no orphan txn, cooldown null → `_iso_to_epoch` never on the critical path.

## 2. Cooldown arm uses BSD `date -v+Ns` → anti-flap inert on GNU (NEW, pre-existing)
- **Site**: `lib.sh:709`. GNU fallback stamps `cooldown_until=now`; `journal_cooldown_active` treats unparseable stamp as ACTIVE → post-auto-rollback promote attempts refused until manual journal surgery. ADR-005 anti-flap silently inert.
- Pair with finding 1 in the same portability commission.

## 3. Drill harness on GNU exceeds 1500s wall-clock (test-architecture note)
- Full scripted drill ≈27+ min on this host (per promote/rollback cycle 2.5–3 min: 70s stop windows + per-file sha256 spawns). macOS ran 96/96 historically; GNU is ~3× slower.
- `timeout 1500` trips mid-Phase-F — budget trip, not a hang. Blind re-run will re-trip.
- Options when the drill next runs on GNU: split drill phases into separately-timed invocations, shorten stop windows via drill env override, or raise the wrapper for the drill (runbook-sanctioned) — never for pack scripts.

## 4. Sandbox provisioning gaps hit during drill (environmental)
- Sandbox install `.env` had NO PG config (3 keys only) — daemon unbootable until worker provisioned throwaway local PG (127.0.0.1:54329, DB `ensemble_sandbox`, data `/tmp/p21-sbx-pg`, still running; stop: `pg_ctl -D /tmp/p21-sbx-pg/pgdata stop`). `.env.pre-drill-backup` retained.
- `config.yaml` missing from INSTALL_DIR — promote hit halt-no-previous until fixture copied in.
- `make(1)` not installed on host → T9 drill row FAIL (environmental, not a defect).
- Ambient env leak CONFIRMED on this host: `PORT=9797 POSTGRES_DB=ensemble_prod POSTGRES_HOST=10.44.0.2` — the POSTGRES_* scrub is load-bearing before EVERY spawned subprocess (matches prior Phase-D probe incident).

## 5. Quick fix applied (authorized scope: drill fixture only)
- `test/drills/p21_upgrade_pipeline_drill.sh:377` BSD-only `date -ju -v-700S` → GNU-first portable form w/ BSD fallback. Commit `becf782f` (1 line). Pipeline source untouched.

## Verdict
- Fix under test: DRILL-CLEAN (flip proven ~25× on GNU incl. real sandbox promote+rollback; zero `mv -h` errors).
- Findings 1–2 = separate portability commission BEFORE demo/live rely on orphan-recovery or post-rollback promote entry on GNU.

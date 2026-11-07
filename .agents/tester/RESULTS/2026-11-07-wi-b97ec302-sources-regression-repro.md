# wi-b97ec302 — Source-Subsystem Regression: Phase-2 Repro (both directions)

- **Date:** 2026-11-07
- **Worker instance:** 8d953f86 (wi-b97ec302-repro)
- **Verdict:** BOTH live symptoms REPRODUCED deterministically in a fenced scratch env. Mechanism adjudicated: **NOT a v0.29.0→v0.29.1 code regression — a data-threshold crossing** (LIMIT-100 page-1 eviction of the oldest chat rows), exposed by the promote restart re-running `start_all`.

## Per-leg verdicts

| Leg | Rev / Seed | Auto-start N | Creation / Registered / 📍 lines | LIST total + breakdown | Verdict |
|---|---|---|---|---|---|
| A | v0.29.1 `5672f055c`, 100 sched rows | **0** | ALL ABSENT (grep rc=1 vs present log) | **100** — 100 scheduler, 0 chat | **REPRODUCED** (matches live exactly) |
| B | v0.29.0 `7426e0d8a`, same 100-row seed | **0** | ALL ABSENT | 100 — 100 scheduler, 0 chat | **IDENTICAL FAILURE** — dispatch's "expected N≥1" did NOT hold |
| C (legC3) | v0.29.1, 5 sched rows | **1** | Creating + Registered PRESENT; 📍 absent (message-path-only) | **8** — 5 sched + discord + telegram + slack | **Discriminator: PAGINATION/LIMIT interaction** (not a hard chat filter) |
| D | v0.29.1, 5 sched rows, discord autostart=f | 0 | ABSENT (skip happens at the autostart check, before creation) | 8 — discord row visible | Boot-side drop NOT autostart-related on the regression path; the autostart skip itself is conditional by design |

Non-vacuity was proven by leg C (same rev healthy when rows fit on page 1), which is a *stronger* control than the intended leg B — it isolates the row-count variable on a fixed rev. Leg B's contra-expectation result is the load-bearing finding for root cause.

## Mechanism (repro-proven)

- `daemon/repositories/source/repository.py:420-437` — `list_source_configs(enabled=None, status=None, limit=100, offset=0)` with `order_by(created_at DESC).limit(limit)`.
- Boot loader: `daemon/sources/registry.py:291` (`start_all`) calls it with **no args**.
- LIST route: `daemon/routers/sources.py:130` calls it with **no args**.
- Chat rows are the **oldest** rows in production `source_configs`; one-shot scheduler rows accumulate newer. Once >100 newer scheduler rows exist, chat rows fall off page 1:
  - loader sees zero enabled chat adapters → `Scheduled auto-start for 0 adapters` → discord never starts;
  - LIST returns exactly 100 scheduler-only rows;
  - by-id GET (direct PK read) and manual start still work — matching all live observations.
- The 📍 `Using agent from metadata` line fires at **message** time (`registry.py:962`), so its live absence is downstream of the adapter never starting — not a separate defect.
- **Regression-window adjudication:** the v0.29.0→v0.29.1 diff does NOT touch the sources subsystem (`daemon/sources`, `repositories/source`, `routers/sources.py` byte-identical; window diffstat = prompt_refresh / release_manifest / prompt_reload files + `manager.py` +10 + ari `meta.json`; `uv.lock` delta version-only). Leg B proves v0.29.0 fails identically on the same data. Old boots' N=2–3 predate the >100 scheduler-row accumulation; the v0.29.1 promote restart was simply the first `start_all` after the threshold crossing.

## Fix direction (NOT implemented — mandate was diagnosis only)

1. Boot loader: unbounded or enabled-filtered query — no page-1 LIMIT-100 dependence.
2. LIST route: real pagination (never silently truncate at 100).
3. Boot-level mutation-meaningful regression pin: adopt the harness below — seed >100 newer disabled scheduler rows + enabled chat rows, assert creation lines fire AND LIST includes chat rows; mutation leg = remove the fix → pin must go RED.
4. Data-side consideration (owner: fix lane): the ~100 stale disabled one-shot scheduler rows in production are the triggering mass — cleanup/reaping policy is a product decision, not a test call.

## Evidence

- `/tmp/wi-b97ec302-repro/evidence/`: `00-ambient-env.txt` (live poison captured), `01-checkouts.txt` (r0291@5672f055c, r0290@v0.29.0→7426e0d8a verified), `02-import-shadow-proof.txt`, `03-legs-summary.txt`, per-leg `legA/legB/legC3/legD-boot.log / -sources.json / -discord.json / -greps.txt`.
- Harness: `/tmp/wi-b97ec302-repro/harness/` (seed.py + boot scripts). **/tmp is not durable** — the full recipe is preserved in `LESSONS/2026-11-07-scratch-boot-repro-harness-recipe.md`; the fix lane should adopt it as the repo-side regression pin.

Key log excerpts (legA, r0291):
```
248:10:04:44 - daemon.sources.registry - INFO - Loading and scheduling auto-start for enabled adapters...
249:10:04:44 - daemon.sources.registry - INFO - Scheduled auto-start for 0 adapters (delayed 60.0s)
'Creating adapter for'      -> ABSENT rc=1
'Registered adapter'        -> ABSENT rc=1
'Using agent from metadata' -> ABSENT rc=1
```
legC3 counter-evidence (same rev, on-page rows): `Creating adapter for: my-discord-bot` → `Registered adapter: my-discord-bot` → `Scheduled auto-start for 1 adapters` → delayed start → `Discord Gateway failed: Improper token has been passed.` (tolerable dummy-credential failure, disclosed).

## Worker disclosures (calibration)

- First legA attempt INVALID: `bash -c` without `cd` resolved the shared checkout's code+venv (import-shadow trap) — caught via traceback paths; verified zero shared-checkout writes (no fresh .pyc, porcelain unchanged). Harness now cd's + asserts cwd + pins `PYTHONDONTWRITEBYTECODE=1`.
- `SYSTEM_ENCRYPTION_KEY` must be a valid Fernet key (boot dies at CredentialManager init otherwise).
- legC/legC2 failed adapter creation due to plaintext seeded credentials — harness artifact, fixed via CredentialManager encryption; **legC3 is the authoritative discriminator leg**.
- One pidfile race → kill-refusal (guard worked as designed); that daemon reaped by its own `timeout 240` backstop.
- Ambient npm context7 processes pre-existed (different tree) — untouched.

## Teardown (confirmed)

6 throwaway DBs dropped (post-drop exists-check `[]`); all recorded PIDs gone; 18079/18080 free; fence intact: `ensemble-main` MainPID=3422961 / NRestarts=0 unchanged, shared checkout porcelain 89 / HEAD `013895287` unchanged, zero writes outside `/tmp/wi-b97ec302-repro/`.

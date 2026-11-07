# Scratch daemon-boot repro harness — recipe + gotchas (wi-b97ec302)

Reusable recipe for booting a scratch ensemble daemon from an arbitrary checkout/rev against a throwaway PG DB, with faithful `source_configs` seeding. Proven on 2026-11-07 (wi-b97ec302 phase-2 repro). The /tmp harness (`/tmp/wi-b97ec302-repro/harness/`) is NOT durable — this file is the durable copy; the fix lane should repo-ify it as the regression pin.

## Boot flow per leg (chicken-egg: schema comes from create_all on boot)

1. Fresh throwaway DB (`ensemble_smoke_*`; house test superuser role if app role lacks CREATEDB).
2. **Bare boot #1** (creates schema) → SIGTERM.
3. **Seed** `source_configs` (see below).
4. **Observed boot #2** → capture boot log + API probes → SIGTERM.

## Boot env block (verbatim discipline)

```
cd <scratch-checkout> && env -i \
  PATH=<scratch-venv>/bin:/usr/bin:/bin HOME=<scratch>/fakehome LANG=C.UTF-8 \
  PYTHONDONTWRITEBYTECODE=1 MALLOC_ARENA_MAX=2 \
  POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5432 POSTGRES_DB=<scratch-db> \
  POSTGRES_USER=ensemble POSTGRES_PASSWORD=<dev-pg-pass> \
  PORT=18079 ENSEMBLE_SELF_ENV=sandbox \
  ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
  ENSEMBLE_CONFIG=<scratch>/config-<leg>.yaml ENSEMBLE_DATA_DIR=<scratch>/data-<leg> \
  SYSTEM_ENCRYPTION_KEY=<scratch FERNET key> \
  timeout 240 python -m daemon
```

- `env -i` full rebuild (not `env -u`) — `env -u POSTGRES_*` is a silent NO-OP (no globbing) and daemon-child shells carry live poison (POSTGRES_DB=ensemble_prod / PORT=9797 / ENSEMBLE_SELF_ENV=live).
- `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` MUST be pinned explicitly — the boot sweep otherwise kills LIVE processes.
- `cd` + assert cwd + `PYTHONDONTWRITEBYTECODE=1` — `bash -c` without cd resolves the MAIN checkout (import-shadow trap); always prove `daemon.__file__` inside the scratch tree first.
- `SYSTEM_ENCRYPTION_KEY` must be a VALID Fernet key or boot dies at CredentialManager init.
- Kill only self-spawned PIDs after verifying /proc/<pid>/cwd+cmdline; keep `timeout 240` as the backstop (auto-start is delayed 60s → observe ≥150s).

## Seed gotchas

- Source credentials are **Fernet-encrypted** via `CredentialManager` — encrypt with the SAME scratch key the daemon boots with; discord needs `{"bot_token": ...}`. Plaintext-cred seeding makes adapter creation fail (harness artifact, not a product signal).
- Scheduler rows must get **newer `created_at`** than chat rows (faithful to production ordering — the eviction is `order_by(created_at DESC) LIMIT 100`).
- Dummy tokens are fine: expected `Discord Gateway failed: Improper token has been passed.` is tolerable — loader/LIST behavior is the observation target.

## The wi-b97ec302 regression shape (why this harness is the pin)

`list_source_configs` (repository.py:420, `order_by created_at DESC LIMIT 100`) is shared by the boot loader (`sources/registry.py:291` start_all, no args) and `GET /api/sources` (`routers/sources.py:130`, no args). Chat rows are oldest; >100 newer scheduler rows evict them from page 1 → auto-start 0 adapters + LIST omits chat rows, while by-id GET/manual start work. Regression pin shape: seed >100 newer disabled scheduler rows + enabled chat rows; assert creation lines + LIST chat-row presence; mutation leg must go RED when the fix is removed. Rev-independent (v0.29.0 fails identically on the same data) — data-threshold crossing exposed by restart, not a code regression in the promote window.

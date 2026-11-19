# Lesson: Gate env fence must scrub ALL ambient ENSEMBLE_AUTH_* (esp. ENABLED) — 2026-11-19

**Context:** wake-path-carrier-fix tester gate (wi-8f60e6f1), worktree `/home/nea/ensemble-src-wt-wake-path-carrier-fix`.

**Root cause:** The agent-shell ambient on this host carries SEVEN `ENSEMBLE_AUTH_*` vars, including `ENSEMBLE_AUTH_ENABLED=true`, while `config.yaml:130` ships `auth.enabled: false`. The prior canonical scrub (POK `auth-lane-scrub-ensemble-auth-env`) removed only `ENSEMBLE_AUTH_ISSUER` + `ENSEMBLE_AUTH_DISCOVERY_URL` — leaving auth ENABLED with its required issuer REMOVED, so any suite constructing `Config()` raised `AuthConfig: auth.enabled=true but missing: ['issuer']` BEFORE any test logic ran.

**Blast radius observed:** 17 nodes in `tests/unit/services/test_child_reports.py` + 3 nodes in `tests/unit/services/test_b_kill_switch_registry.py` reddened deterministically; both files went fully green (50/0, 15/15) under the amended fence. Deterministic reds masquerading as regressions — adjudication required a git-archive base run to dispose.

**Canonical amended fence (proven green at 3 sites this gate):**
```
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT \
    -u ENSEMBLE_SELF_ENV \
    -u ENSEMBLE_AUTH_ENABLED -u ENSEMBLE_AUTH_ISSUER -u ENSEMBLE_AUTH_DISCOVERY_URL \
    -u ENSEMBLE_AUTH_CLIENT_ID -u ENSEMBLE_AUTH_CLIENT_SECRET -u ENSEMBLE_AUTH_ALLOWED_ORIGINS \
    -u ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE \
  bash -c 'set -a; source .env; set +a; timeout 300 <cmd>'
```
Rules: never `env -u POSTGRES_*` / `env -u ENSEMBLE_AUTH_*` (bash does not glob env names — silent no-op); source `.env` AFTER the scrub (dotenv does not override pre-existing os.environ — ambient `ENSEMBLE_SELF_ENV=live` would otherwise win identity resolution); pre-verify identity AFTER sourcing, not before.

**Second trap (same gate):** `pytest -V` (short flag) runs FULL COLLECTION in this repo → imports `tests/packs/g7_unique_index_smoke_test.py` → `sys.exit(0)` at import → INTERNALERROR exit 3. Use `--version` (long form) and ALWAYS scope explicit paths + `--ignore=tests/packs`. Only `.venv/bin/pytest` works (system python3 has no pytest).

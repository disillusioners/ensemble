# Lessons — od-self-provisioning Stage-1 gate (2026-10-02)

## 1. A/B carrier discipline: stale gitignored artifact vs .env confound — transplant legs decide

**Situation.** Mission claimed 16 `test_mcp_server_crud.py` reds pre-exist at base; a prior adjudication (2026-09-27 §G1) had attributed the same family to a main-checkout `.env` confound. Both theories were partially wrong in an important way.

**Finding.** 7-leg A/B decoupling commit × location × env × artifact proved the carrier is an **untracked gitignored sqlite file** (`test_mcp_servers.db`, stale schema missing `mcp_servers.instance_metadata`):
- Base worktree + `.env` copied → 80/80 PASS (`.env` exculpated as carrier)
- HEAD main checkout PG-scrubbed → 16F (still fails without env)
- Clean HEAD worktree → 81/81 PASS
- **Transplant** (copy the one file into clean worktrees) → identical 16F at BOTH commits (md5-equal failure sets)

**Lessons.**
- A protocol decision tree that varies TWO factors between legs (commit AND location) can produce false "branch-caused" verdicts — Leg A2 vs Leg B2 differed in both. Always add decoupling legs (clean-worktree HEAD, artifact transplant) before concluding.
- Gitignored artifacts never travel to worktrees — "passes in worktree, fails in main checkout" is the signature of a stale untracked file, NOT necessarily an env/.env confound.
- `SQLModel.metadata.create_all` creates missing TABLES, never ALTERs existing columns — any CWD-relative file-backed fixture goes stale on every schema migration. Fix family: tmp_path or in-memory fixtures (routed as test-debt for `tests/unit/test_mcp_server_crud.py:58-63`).

## 2. dev.sh env sharp edges (env-poison guard enforcement, boot #1 of this gate)

- `dev.sh` does `set -a; source .env` — this **re-exports .env values over anything the wrapper pre-set inline** (a wrapper `export POSTGRES_PASSWORD=…` is silently overridden). Wrapper-level overrides other than `ENSEMBLE_SELF_ENV` must be made IN `.env` itself.
- `ENSEMBLE_SELF_ENV=dev` must be set by the wrapper (dev.sh does not set it), and the ambient shell may carry `ENSEMBLE_SELF_ENV=live` — this gate's boot inherited `live` from the user env; the wrapper override was load-bearing. **Verify via `/proc/<daemon-pid>/environ` after boot, not just in the launching shell.**
- Dev bootstrap credential on this host: role `ensemble`, password `ensemble_dev`, db `ensemble_dev` @ localhost:5432 (the `.env` previously carried a non-working `testpw`; corrected 2026-10-02 — gitignored files). Worth a README dev-section note.
- Round-trip smoke pattern that worked: invoke the repository function the agent tool delegates to (`McpServerRepository.update_mcp_server` for `mcp_set_env` at daemon/tools/infra.py:1534) with ENSEMBLE_SELF_ENV=dev against the dev DB, then byte-restore. Avoids needing a full InstanceManager fixture while exercising the exact write path.

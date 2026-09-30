# 2026-09-30 — Worktree isolation on this host: PYTHONPATH pin is DEFEATED by the venv editable `.pth`; cd-into-worktree is the ONLY reliable base-leg isolation

**Discovered:** v0.16.7 nonce-TTL gate (P1 mutation leg + P8 A/B base leg).

## What happened

Two workers tried the "detached worktree + `PYTHONPATH=<worktree>` pin" pattern (documented in
older QUARANTINE/RESULTS rows) to test a different commit than the main checkout holds. Both
times the import check caught the trap:

```
PYTHONPATH=/tmp/<worktree> /home/nea/ensemble-src/.venv/bin/python -c "import daemon; print(daemon.__file__)"
→ /home/nea/ensemble-src/daemon/__init__.py     ← WRONG TREE (main checkout)
```

## Root cause

`/home/nea/ensemble-src/.venv/lib/python3.13/site-packages/_editable_impl_ensemble.pth`
(uv editable install) injects `/home/nea/ensemble-src` into `sys.path` at site-packages
processing time. Python's finder resolves `daemon` to the FIRST match in `sys.path` order —
and the `.pth`-injected main-repo path wins over the `PYTHONPATH` entry in this configuration.
`PYTHONPATH` alone therefore does **not** select the worktree's code. (Older base-leg rows that
said "PYTHONPATH-pinned" were file-parse-only tests that never imported `daemon` — the pin was
never actually load-bearing there.)

## The fix that works (proven both legs, byte-identical red sets)

**cd INTO the worktree and use RELATIVE test paths** — cwd (`''`) precedes the `.pth` entry:

```bash
git -C /home/nea/ensemble-src worktree add --detach /tmp/<wt> <commit>
cd /tmp/<wt>
/home/nea/ensemble-src/.venv/bin/python -c "import daemon; print(daemon.__file__)"
#   MUST print /tmp/<wt>/daemon/__init__.py  — make this a HARD GATE before any pytest run
timeout 300 /home/nea/ensemble-src/.venv/bin/python -m pytest tests/unit/... --tb=short -q
```

- Do NOT use `scripts/run_tests_scrubbed.sh` for base legs — it `cd`s back to the main repo
  (would silently test the wrong tree). Replicate its scrub inline instead:
  `unset POSTGRES_{HOST,DB,USER,PASSWORD,PORT,URL} DATABASE_URL_POSTGRES PG{HOST,PORT,DATABASE,USER,PASSWORD,PASSFILE,SSLMODE,SERVICE,SERVICEFILE}`
  plus `unset ENSEMBLE_UPGRADE_LIVE ENSEMBLE_UPGRADE_SCRIPTS_DIR; export ENSEMBLE_SELF_ENV=dev`.
- pytest rootdir/conftest/pyproject resolve from the worktree (pass relative paths).
- Always verify `daemon.__file__` FIRST; abort the leg if it prints the main checkout.

## Second lesson from the same gate — dual width-lock test design (worth copying)

`test_nonce_ttl_boundary_59m59s_still_valid` carries a top-of-body canonical fixture-lock
(`assert NONCE_TTL_S == 60 * 60`) BEFORE its boundary semantics, and the corpus adds a separate
mirror-drift pin (same assertion + source-pin of the `manager.py` mirror literal). Net property,
proven by a 900s mutation in a throwaway worktree: **the corpus cannot pass under any TTL other
than the canonical value (two independent locks), while the semantic layer stays TTL-agnostic
(symbolic offsets from the imported constant)**. That combination is the correct shape for
"test the boundary without hardcoding the constant" — recommend it for future constant-widening
commissions.

## References

- Gate RESULTS: `RESULTS/2026-09-30-nonce-ttl-v0167-gate.md` §4 (mutation outcomes)
- Prior editable-trap note (worktree pytest silently testing current branch): repo blueprint
  "Repo & Dev Environment Conventions — Worktree editable-install trap"

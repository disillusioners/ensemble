# D1 gate lessons (2026-10-22, mcps-gateway-interop final merge gate)

## Tooling traps (each cost a worker retry or near-miss)

1. **pytest 9 `--ignore` does NOT exclude explicitly-passed file args.** `pytest <file> --ignore=<same file>` runs the file (probe-proven). `pytest_ignore_collect` only consults the ignore list during directory traversal. To exclude a file from an explicit-args pack, REMOVE it from the args (keep `--ignore` as belt-and-braces for transitive collection). Near-miss: B1 would have run the session-aborter `test_upgrade_executor_systemd_service.py` inside its bucket.
2. **`env -u POSTGRES_*` does NOT glob** — `env -u` takes one literal name; the ambient per-field POSTGRES_* stay set. The fence held only because the explicit `POSTGRES_DB=ensemble_dev_test_only` override wins in `factory.py` (and no DSN shortcut var existed). Reliable form: set per-field fence vars + `-u DATABASE_URL`. Fence canaries: the strict-fence `pytest.exit` files + `test_main_entry` (passed both halves this gate).
3. **xdist re-collection deltas** (+1/+4/+5 nodes vs `--collect-only` census at -n4/-n5) are a stable artifact class in this repo. Reconcile scope via a separate `--collect-only` leg, never via run summaries.
4. **Worker stdout truncation (~150k)** loses pytest summary lines — redirect pack output to a file and re-parse; two workers hit this independently.
5. **`env -u` + ambient `.env`**: repo-root `.env` leaks `EMBEDDING_BASE_URL` into `SkillEvolutionConfig` defaults (test_defaults fails identically at base — env-contamination class, same family as the fence-DB gotchas).

## Gateway-specific (durable for the mcps-gateway lane)

6. **A dead DB is a BOOT failure, not a degraded read.** `api.py` lifespan hard-depends on PG; the §8.5 degraded contract (200+`degraded:true`, NOT 503) is reachable only from a mid-flight read failure. Live-degraded recipe that WORKS with auth ON: hold an `ACCESS EXCLUSIVE` lock on `mcp_gateway_audit` (fence DB) + `MCPS_GATEWAY_READ_TIMEOUT_MS=1500`; release → immediate recovery. (Also: auth middleware collapses dead-DB credential failures to 401 before the route runs.)
7. **FE token transport**: the mcps-gateway page has NO token input/localStorage — it rides the ambient OAuth lane; inject the bearer at Playwright browser-context level (`setExtraHTTPHeaders`), same credential as curl parity legs. ng serve binds `[::1]:4199` only — probe readiness via `localhost`, not `127.0.0.1`.
8. **W8 staleness is session-scoped**: degraded *refresh* keeps the last page; hard *reload* resets the client-side tail while the banner still shows. Doc wording ("KEEPS the last page on screen") is the contract; don't over-read it as persistence.
9. **`PG_TEST_*` is a separate env contract** from the `POSTGRES_*` fence (defaults to `ensemble_test` where role `ensemble` lacks CREATE on public). Leg-private scratch DB via `PG_TEST_DB=<scratch>` (create→run→drop, lifecycle proven) converts env-noise errors to real green (27/27).

## Gate process

10. **First-ever gate coverage of a marker-shadowed leg** (`tests/integration -m integration`, 407 nodes hidden by default addopts) surfaced ONLY pre-existing debt — every red base-proven by an executed 13-node A/B at base. When a leg has no baseline, diff-disjointness is necessary but an executed sample A/B is the closing proof (2 of 13 would have looked branch-caused on signature alone; both were mechanism-proven pre-existing via sibling nodes).
11. **Mid-gate foreign-write attribution**: another worker's write→commit window (2 m 07 s) looked like a foreign session dirtying test files. Attribution recipe: porcelain → scoped numstat vs pin → blob-hash equality vs HEAD → mtime-vs-commit-date timeline → reflog → cross-worktree scan. All 3 gate quick-fix commits were test-only and ride the merge cleanly.

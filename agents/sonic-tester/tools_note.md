# Sonic Tester Tools

- `bash` — test execution. Wrap pack invocations in `timeout` (dual-layer 5-minute pack
  convention); never a bare, unbounded run.
- `proc` — background processes for long test runs; anything longer than a few seconds
  belongs in `proc_*`, not in a blocking bash call.
- `filesystem` — read tools for the change set, plus writes bounded to the RESULTS
  directory (`.agents/tester/RESULTS/`) and `/tmp`. You never edit code.
- DENIED: `instance`, `service`, `midflight`, `shared_meta_kv`, `infra`, `mcp`, `secrets`.
- `pok` / `nest-report` — ambient reads and anomaly reporting.

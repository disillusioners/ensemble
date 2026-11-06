# Raw evidence — G1 W8 boot-abort induction (Memoir Store v1)

Produced 2026-11-06T01:00–01:02Z by gate worker G1 on
`/home/nea/ensemble-src-wt-memoir-store` @ `f054a08bc` with
`MEMOIR_W8_BINS_DIR=/tmp/g1-w8/bins MEMOIR_W8_KEEP_SCRATCH=1 bash test/packs/memoir_w8_boot_abort_test.sh`.

| File | What it is |
|---|---|
| `pack-run.log` | the full pack run (RESULT: PASS; binary shas; all 22 assertions) |
| `induction.log` | the inducted frozen binary's boot log — `UndefinedFunction: function no_such_imm_function(text) does not exist`, the `[SQL: ALTER TABLE memoir_episodes …]` line, `Application startup failed. Exiting.` |
| `clean.log` | clean-binary boot on the same scratch DB (Starting Ensemble v0.28.11; Uvicorn on 127.0.0.1:17350) |
| `clean2.log` | second clean boot on the same DB (ensure idempotence) |
| `mutant.log` | try/except mutant boot (boots successfully; DB assertions live in `pack-run.log`) |
| `pins.log` | structural-pin pytest output (3 passed) |
| `initdb.log` | scratch PG16 cluster init |
| `pin-mutations.log` | M-i/M-ii/M-iii source mutation legs (each RED, restored green) |
| `MANIFEST.sha256` | hashes of the above |

Scratch PG cluster: `/usr/lib/postgresql/16/bin/initdb -A trust`, `16.15 (Ubuntu
16.15-0ubuntu0.24.04.1)`, throwaway DB `memoir_w8_scratch`, dropped at teardown. Binary
sha256s and the fence triple are recorded in
`../2026-11-05-memoir-g1-w8-boot-abort.md`.

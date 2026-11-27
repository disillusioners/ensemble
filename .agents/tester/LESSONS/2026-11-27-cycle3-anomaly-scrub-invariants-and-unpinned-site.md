# Cycle-3 lessons — anomaly scrub invariants, unpinned sites, census arithmetic, read-only proofs

- **Date**: 2026-11-27 · **Lane**: `fix/never-started-detector-anomaly-truncation` · **Gated tip**: `258d389e4` · **Full evidence**: `.agents/tester/RESULTS/2026-11-27-never-started-cycle3-final-verification.md`

## L-1 — A pin's double must REACH the branch you mutate
`_scrubber_effective` has two exits: the PROBE branch (`:768-772`, `is_effective` present) and the LEGACY branch (`:776-777`, no probe and no load path). Flipping the legacy branch to `return True` does **not** redden the `_ProbeFalseScrubber` pins — that double returns at the probe branch and never reaches the mutated line. The catcher is the `_BrokenScrubber` pin (`test_t_e_excerpt_store_unavailable_withheld`, `assert row.excerpt is None`). **Map pin → branch before claiming a mutation is covered**; a pin that cannot reach the line is not coverage, it is a coincidence.

## L-2 — A claim is not coverage; a mutation is
The H1 claim named TWO `category_raw` strip sites (accept + refusal ledger). The accept site was pinned; the ledger site had **ZERO** pin coverage — deleting it left `61 passed` + `152 passed` across the whole anomaly surface green. Mutation-probe **every site a claim names**, one at a time, and record which pin catches which site. The gap is now closed by `tests/unit/test_anomaly_report_category_raw_ledger_strip_pin.py` (positive leg + a non-vacuity control leg).

## L-3 — A census PATH set can contain non-test artifacts; count files WITH NODES
The mechanical blast-radius set carried `tests/unit/tools/fixtures/module_level_inputs_b_base.json` — a **0-node fixture**, not a test. That is the whole 28-vs-27 file delta between the mechanical rule and cycle 3's census (27 test files / 677 nodes at tip, 26 / 633 at base). Separately, a **constant node delta across both legs** identifies a file one cycle carried and another did not: cycle 2's 689/650 is exactly **+17** over the mechanical count on both legs ⇒ the carried file is the only unchanged 17-node file in the family (`tests/unit/tools/test_module_level_inputs_b.py`). When a census number will be compared across cycles, publish the FILE LIST and the node count together, and exclude 0-node artifacts.

## L-4 — Read-only fences, venue, and the local dev server
The dev app role `ensemble_dev_app` has `rolcreatedb = false` ⇒ it cannot create a scratch database. The OS user's own PG role `nea` **is a superuser on the local dev server**: use it for `CREATE DATABASE` / `DROP DATABASE` / listing only, and run every data connection as the ordinary app role. Prefer a **connection-level** fence — SQLAlchemy `create_engine(dsn, connect_args={"options": "-c default_transaction_read_only=on"})` — which needs no `ALTER ROLE` at all. Always assert `SHOW default_transaction_read_only` as the literal FIRST statement on every physical connection (`pool_pre_ping=False` keeps it first).

## L-5 — A hidden password prompt wedges a tool call for 15+ minutes
`psql` / `createdb` without `-w`/`--no-password` will block on an invisible prompt; the loop breaker cannot see a varying-args bash call. Rule for every DB-touching worker brief: `PGCONNECT_TIMEOUT=10`, `psql -w`, `PGPASSWORD` from the `.env` (never echoed), and every DB call wrapped in `timeout 180`. A command that could prompt must fail fast.

## L-6 — "Report produced, delivery never arrived" is a real terminal state
A child can emit a complete final report and stay `running` forever; the subtree summary path truncates the body at 200 chars. Recovery order: (1) `subtree_messages` with an offset to the tail to see whether the report exists at all; (2) a bounded re-emit (write the finished report to a file, no new investigation); (3) if the evidence cannot be recovered, an independent re-run. Keep the durable artefact on disk (`/tmp/…-report.md` + sha256) — a report that only ever existed in a transcript is not evidence.

## L-7 — The manager BOOT path is NOT read-only
Under a read-only session the manager's boot path attempts writes: `queue.discard_on_startup` wipes pending task rows, `_ensure_blueprint_g7_unique_index` and the infra-asset-type bootstrap write. A read-only proof must pre-build the schema, stub the four `_ensure_postgres_*` helpers, and pin `config.queue.discard_on_startup = False` — otherwise the fence refuses the boot (SQLSTATE 25006) and the proof cannot start. The detector and the scrub path were never stubbed, so their read-only character is what the proof actually establishes.

## L-8 — The excerpt has TWO gates; the TOCTOU branch persists a placeholder
Gate 1 (effectiveness, before the read): an ineffective scrubber ⇒ excerpt `None` (withheld). Gate 2 (failure DURING the read, TOCTOU): the authored field becomes the withheld placeholder, but the EXCERPT persists that placeholder string (bounded, secret-free) rather than `None`. INV‑2 still holds (no plaintext, no redeemable handle, no escaping exception, no typed refusal) — but "the excerpt is withheld" is TRUE only for gate 1. State the gate when asserting excerpt behaviour.

## L-9 — Measured scrub arithmetic (do not trust the estimate)
Synthetic plaintext `SYNTH-SECRET-FRAGMENT-9931` = **26 chars** → token `__ENS_REDACTED__<12hex>__` = **30 chars** (+4). A supplied marker `__ENS_SECRET__<uuid4>__` = **52 chars** → `[marker-stripped]` = **17 chars** (−35). The withheld placeholder must carry the **PRE-scrub** length (`original N chars`), because the post-scrub length differs (`scrub_unavailable_placeholder(n)` with n = the pre-scrub length; the redaction can EXPAND the text past the cap).

## L-10 — The category canonicaliser is exact-match; the fold notice echoes the raw category
A raw category carrying a marker (or any extra word) is not in the vocabulary ⇒ it folds to `other` while `category_raw` keeps the visible stripped text. The result envelope's `fold_notice` echoes the **lowercased raw category — marker included —** back to the SUPPLYING agent (`nest_report.py:338-340`); it is not persisted and not logged, and the agent's own tool call already carries the marker, so this is not a new exposure. Worth knowing before hunting for a leak in the notice text.

## L-11 — The workflow skeleton that worked
Scope first (blast radius → Tier), then fan out one pack per worker with the strict single-pack template, then fan in: mutation proofs in an isolated scratch worktree (never the shared tree), the regression leg with the recipe's slicing proof + masking check + form-tagged digests, the read-only proof, the fixture render, then the registry. Keep the lane tree byte-identical on every leg (`git status --porcelain` before/after) and commit tester artifacts separately from the gated tip so the gate's evidence stays anchored to `258d389e4`.

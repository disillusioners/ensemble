# TESTER GATE — commission "prompt-assent" (the Emperor's Word / Assent conversational approval lane)

| Field | Value |
|---|---|
| Date | 2026-11-11 (UTC) |
| Commission | prompt-assent — conversational approval lane (trust-model change) |
| Verdict | **NO-GO** for the stage cut **as it stands** — 2 net-new deterministic reds (both test-pin staleness, zero production defects). Distance to GO = 2 mechanical test-side edits + a 2-cell re-verify. |
| Branch / worktree | `feature/prompt-assist` @ `/home/nea/ensemble-src-wt-prompt-assent` (the "assist" spelling is the project's own convention — POK `prompt-assist-03-paths-use-assent-not-assist`) |
| Tip tested | `029416f2e7961ce5e9d951015c571d91d420ed1b` (HEAD identity verified; 23 commits over base — the commission's "16" was a stale phase-4 expectation captured at `e5b3cc2d1`; tip identity is the invariant) |
| Base | `0efdf106f3f66ccb400405f4137694592b72097d` (verified ancestor, `merge-base --is-ancestor` rc=0) |
| Gate tier | **Tier 2 — whole-tree dual-side** (ensure.md L19: promote-blocking gate AND core seam `daemon/api.py` touched, +14/-0) |
| Instances | recon `0818a693` · Lane A `de6b6118` · Lane B `90fc4e42` · Lane C `e09604b6` · Lane D (adjudicated in-line) · Lane E `0539d04f` · ensure.md Core `7a6ef89f` |
| Diff shape | 88 files, +18485/-100 — production: 19 × `daemon/`, `agents/ari/meta.json` (additive allow-entry only), 1 migration; `config.yaml`/`pyproject.toml` untouched; dirty tree = 3 `.agents/`-scoped files (approver/tidier-owned, fenced — never staged, never swept) |

---

## 1. Summary

| Lane | Scope | Verdict |
|---|---|---|
| **A** | Tier-2 whole-tree dual-side FAILED-list diff (base ↔ tip, identical commands) | **FAIL — net-new = 2** (N-1, N-2; deterministic, 3× isolated, both arms) |
| **B** | Lane packs at final tip (249-node matrix + 107-node P3) + post-tidier counter-mutation spot-verify | **PASS** |
| **C** | PG seams: disposable-cluster boot smoke · 128th `.sql` via the real SQLite MigrationRunner · PG durable legs | **PASS 3/3** |
| **D** | K-3 live probe (W-1 flip-on hard gate) | **OUTSTANDING** → the Emperor's ceremony lane (not a gate failure; blocks arming, not staging) |
| **E** | T3.5-shaped E2E on the real dev daemon (9 legs + kill-switch matrix) | **PASS 9/9** (1 disclosed harness simulation, 1 expectation correction) |
| **ensure.md Core** | concurrency pack · `dev.sh` graceful-shutdown flag · converted-async callers (+ no-dead-code) | **PASS 3/3** (+1 nice-to-have) — the concurrency pack's **raw exit 1** is a quarantined, base-proven pre-existing row, not a net-new defect (see §7.1) |

**The feature itself is proven working end-to-end on the built code.** Every one of the commission's six mandatory cells is green at the built-code level, and the happy path is green on a real daemon with a complete provenance chain. What blocks the stage cut is two *cross-lane test pins* that the branch's legitimate changes invalidate — not a behavioural defect.

---

## 2. Scope Decision

Full regression WAS warranted and was run — not because it was requested, but because ensure.md's Tier-2 trigger fired mechanically on two independent grounds: the gate is promote-blocking (pre-stage-cut), and the diff touches the core seam `daemon/api.py`. Blast radius: 88 files across 19 production modules + the whole test corpus (36,790 collected at head).

Nothing was scoped down to make the gate fit: the only re-scope applied was **shard sizing** (9 → 5 shards/leg after the measured-rate projection), which preserved the full node set on both legs (`executed == declared` slicing proof on both). The 90-min Tier-2 advisory budget WAS exceeded (actual ≈129 min incl. the 3× isolation ladder) — reported loudly below, not silently absorbed.

---

## 3. Lane A — Tier-2 whole-tree dual-side regression (FAIL)

Worker `de6b6118`, skill `test-pack-execution` (its 5-min single-pack contract was deliberately not applied to a whole-tree gate leg — the lane used the in-repo Tier-2 recipes instead; feedback recorded).

### 3.1 Leg totals (accounting closure asserted delta-0 on both legs)

| leg | collected | failed | skipped | errors | xfailed | shard wall |
|---|---|---|---|---|---|---|
| **HEAD** `029416f2e` | 36,790 (1,271 deselected) | **480** | 335 | 22 | 6 | 2,138 s |
| **BASE** `0efdf106f` | 36,533 (1,261 deselected) | **497** | 336 | 22 | 6 | 1,998 s |

Head-only nodes = 266 (23 new prompt-assent files + 2 modified) → one extra head shard; base-only = 9 → one extra `baseonly` shard. **Slicing proof: `executed == declared` on both legs** (every shard's collected count reconciles to its node list).

### 3.2 FAILED-digest comparison (canon comparator `sweep_digest_verdict.sh`, exit 1)

| form | head sha256 | base sha256 | counts |
|---|---|---|---|
| GREP-FULL | `43f14cb7f373a8c18fde3e4f56a8cd09a2048e538a5de9f6443e94d538a9c336` | `76d04a3f6a711ddc1b73bfd98da804d23bf26a61afb8458dc8677399537bfce0` | 511 / 528 |
| BARE-ID | `a651d9371773b762eb695139104c51291f0267ef62badce75485991f2fec529c` | `5d56006e0c201f79cd0f1371e539ffa2bdc90f7c44d0eddb298ce0478d29c091` | 502 / 519 |

Sweep-level BARE-ID partition: **base-only 28 · head-only 11 · in-both 491** — non-vacuous (both sides non-empty, verdict block present).

**Base-leg daemon-resolution assertion (the house editable-`.pth` trap): PROVEN** — `/tmp/assent-gate-base-0efdf106f/.venv/bin/python -c "import daemon"` → `/tmp/assent-gate-base-0efdf106f/daemon/__init__.py` (inside the base tree). Python 3.14.7 / pytest 9.0.2 / xdist 3.8.0 byte-matched to head. Base provisioned by `git worktree add --detach` + `uv sync --frozen --offline` (warm cache, **no network**).

### 3.3 NET-NEW REDS — 2 (deterministic, branch-caused, unrepaired by design)

**N-1 · `tests/unit/test_anomaly_report_duty_prompts.py::TestTheSealRePin::test_the_untouched_rows_keep_their_base_values`** — head `FFF` / base `PPP`
> `daemon/sources/adapters/discord/adapter.py was re-stamped even though its file did NOT change`

The branch added the DM/group-DM discriminator to `daemon/sources/adapters/discord/adapter.py` (the K-3 mint-context binding) and re-stamped that file's seal sha in place (`87351d95d354…` → `a9f3a711fc79…` in `tests/test_chart_image_delivery_e2e.py`) **without moving the row out of `SEAL_UNTOUCHED_BASE_VALUES`** → the anti-blanket-re-stamp invariant fires.
*Remedy (1–2 lines, test-side):* move the row into `SEAL_REPINNED_BASE_VALUES` with the new sha — **but only if the re-pin is authorized** (see Open question 1; the seal ceremony owns that classification, not this gate).

**N-2 · `tests/unit/test_memoir_v2_schema.py::test_the_migration_census_delta_is_exactly_one_new_file`** — head `FFF` / base `PPP`
> `assert {…20261109_021154_create_memoir_v2_tables.sql, …20261110_221500_create_prompt_assent_tokens.sql} == {…20261109_021154_create_memoir_v2_tables.sql}`

The branch adds the house-required 128th migration (`20261110_221500_create_prompt_assent_tokens.sql`); the memoir lane's census pin asserts the delta vs *its* lane base is **exactly one** file. A second lane's migration invalidates that pin.
*Remedy:* re-express the pin (per-lane ownership / `delta ≥ 1`) — a cross-lane decision (Open question 2). A 1-line "absorb" edit is the fast path; the durable re-expression prevents the *next* migration lane from repeating this.

### 3.4 The other 9 sweep-level candidates — classified, none net-new

1 × **environmental** (`job_queue/…::test_ensure_dev_sh_still_works` — hardcoded `/Users/nguyenminhkha/…` project_root; both arms fail identically now that :8079 is free) · 3 × shard-composition artifacts · 2 × flaky (auth-middleware latency budget; chart-tools reuse) · remainder artifacts (aegis cwd pin, paused-auto-resume ×2, plane-sync concurrency, dependency-bus lock).

**Base-only 28** → 20 artifacts + 7 deterministic *base-leg path* artifacts (plugin-registry checkout-family assertion; 5 filesystem-walk/workdir guardrail pins that read "pin meaningless" under a `/tmp` checkout; scheduler deferred probe) + 1 flaky. These are base-leg provisioning artifacts, not branch behaviour.

### 3.5 Known-reds attribution (all pre-existing, identical on both legs)

| expected family (commission list) | in-BOTH | base-only | head-only |
|---|---|---|---|
| `test_migration_files_present` (stale `==96` pin) | 1 | 0 | 0 |
| `test_prompts_router` AuthConfig (~75 expected) | **0 — did NOT materialize** | 0 | 0 |
| metrics borrow-census drift | 16 | 0 | 0 |
| `test_instance_tools` planner-grant | 2 | 0 | 0 |
| `test_prompt_section_reference_integrity` | 5 | 0 | 0 |
| `test_snapshot_tools` | 1 | 0 | 0 |

⚠️ The commission's expected `test_prompts_router` AuthConfig family (~75 errors) **did not appear at all** on either leg — zero observed, so no attribution was possible. The expectation was stale; it is not a missing check.

### 3.6 Freshness flake protocol — NOT TRIGGERED

`tests/unit/test_prompt_assent_freshness.py` (5 ids, head-only file): **zero reds at head**; the positive leg `test_inside_the_bound_is_fresh` was green in every leg. All 266 head-only nodes passed. No N=30 rate run was required.

### 3.7 Budget, projection, re-scope (disclosed)

Probe shard-00: 4,542 nodes / 316.8 s = **860 nodes/min** (depressed by concurrent host lanes) → initial projection 91–100 min ⇒ **projected breach of the 90-min advisory**. One re-scope applied per canon §2 (shard sizing 9 → 5 per leg, file-boundary; `baseonly` shard for the 9 base-only ids) — **full node scope preserved on both legs**. Steady-state 1,247 nodes/min (shard-01) → re-projection ≈73 min/leg. **Actual: legs 69 min; whole gate ≈129 min (05:24Z→07:33Z) including the 39-node × 2-arm × 3 isolation ladder ⇒ actual breach of the 90-min advisory, reported loudly.** No scope was dropped; the breach is wall-clock, not coverage.

### 3.8 Safety

HEAD worktree unmodified (`git status --porcelain | sha256` identical before/after: `5eabeaf2b64454a0762b6ed9852151b5c36de96b8cc17cbfec6e6cfe32442439`; the 3 dirty entries are another lane's pre-existing WIP, untouched) · bytecode redirected via `PYTHONPYCACHEPREFIX`, `-p no:cacheprovider` · base worktree removed (`git worktree remove --force` + prune; `git worktree list | grep -c assent-gate` = 0) · no repo modification, no quick fix (gate = disclose only), no daemon boot, **no process killed or signalled**, canonical checkout never entered, `:9797`/`ensemble_prod`/`releases/` untouched, no PG contact (per-variable scrub; a bare `env -u POSTGRES_*` was never used).
**Evidence pack (survives teardown):** `/home/nea/gate-evidence/assent-gate-20261111T052600Z/` — SUMMARY.md, both progress TSVs, both GREP-FULL lists, node lists, verdict log + all 4 form-diffs, both adjudication TSVs + every per-iteration output, both collect raws, every runner script.

---

## 4. Lane B — lane packs at final tip + post-tidier counter-mutation spot-verify (PASS)

Worker `90fc4e42`, skill `test-pack-execution`. Commands: `timeout 300 bash test/packs/prompt_assent_matrix_test.sh` and `timeout 300 bash test/packs/prompt_assent_phase3_unit_test.sh` (each pack self-timeouts and self-scrubs env per its header; per-variable scrub applied).

| Pack | Nodes | Result | Runtime |
|---|---|---|---|
| `prompt_assent_matrix_test.sh` (full matrix, incl. 5× order/load variants) | **249** | ✅ PASS 249/249 | 32 s wall (26.05 s pytest internal) |
| `prompt_assent_phase3_unit_test.sh` (incl. `tests/integration/test_prompt_assent_flow.py`, run explicitly) | **107** | ✅ PASS on attempt 1 of 4 | 22 s (attempts: 22 s / 23 s / 21 s / 21 s) |

Attempt 0's only red = the known pre-existing freshness flake (`tests/unit/test_prompt_assent_freshness.py::TestTheFreshnessBound::test_inside_the_bound_is_fresh`) → 3/3 retries green with **no code change**. Pack verdict is PASS-on-retry, not a clean first pass.

### 4.1 Counter-mutation spot-verify — both pins STILL BITE after S-1 (`bfb8c22ca` routed both digest sites through `render_sha256`)

Venue: throwaway detached worktree `/tmp/assent-mut-tip` @ 029416f2e + `uv sync --frozen` (0.97 s warm). **Import isolation proven**: `daemon.__file__` and `tests.__file__` resolve inside the scratch tree; the feature tree is absent from `sys.path`. The shared feature worktree was never mutated.

**(a) "token in tool args only → REDDEN" pin** — cell `tests/unit/test_prompt_assent_lane.py::TestTheTokenMustBeInTheHumanMessage::test_a_token_only_in_the_tool_arguments_is_refused`
Mutation: the factor-3g check in `daemon/tools/prompt_assent.py` deleted (14 lines → 1 comment comment-marker); sha256 `a597e20d…927c` → `aeb863ad…d3c`.
→ **RED** (exit 1) at `test_prompt_assent_lane.py:363` with `reason=token-mismatch` — i.e. the token-only call reached the branch the cell predicts (the expected `token-content-mismatch` signature is absent). Restore → sha256 byte-identical (`a597e20d…927c`) → **GREEN**.

**(b) digest-path mutation at the NEW tip** — `git show bfb8c22ca` → wrapper `daemon/services/prompt_version_diff.render_sha256`; routed sites `daemon/tools/prompt_assent.py:1769` (mint) and `daemon/repositories/prompt_assent/consume_hook.py:92` (D-4 in-txn re-check).
Mutation at the mint site: `render_sha256(**render_kwargs)` → `hashlib.sha256(rendered[:len(rendered)//2]…)` (raw/incomplete digest).
→ **RED** (exit 1) at `test_prompt_assent_lane.py:419` (`TestTheMintBranch::test_the_mint_writes_the_token_and_shows_the_render`): *"the shown bytes and the hashed bytes must be the SAME bytes"* (`1a05facc…2f27108049283` vs `4ee5512e…17c9b1875f8f4`).
**Blast radius under the mutation:** full matrix pack → **13 failed / 236 passed** — the mint pin, the approve happy path (D-4 re-check mismatch), the ledger-provenance integration flow, the raw-token cells, and 2 counter-mutation self-checks (their "green without the mutation" premise broke). The digest rule is genuinely guarded at the new tip, not merely asserted.
Restore → byte-identical → both pin cells **GREEN**; post-restore matrix 248 passed / 1 failed (the known freshness flake, same signature).

### 4.2 Freshness flake attribution

Flaked **2 of 6** pack runs in this lane; mechanism confirmed: `now_iso()` floors the stamp to whole seconds, so a 599 s backdate's effective age = `599 + fractional-second-at-build + async elapsed`, crossing the 600 s bound. `8b22e76a5` widened only the TTL margin, not the backdate. Matches POK `prompt-assist-04-freshness-leg-second-race`; the control arm flaked *more* (per `bfb8c22ca`'s own disclosure). **Pre-existing; the durable fix is deferred and NOT the gate's to land.**

### 4.3 Teardown / cleanliness

`git status --short` = exactly the 3 known `.agents/` entries; HEAD unchanged; `git diff --cached --stat` empty; scratch worktree removed (`git worktree list | grep assent-mut-tip` → NONE); `daemon/tools/prompt_assent.py` sha256 = `a597e20d…927c` (byte-identical to pre-mutation); no daemon booted, no process signalled, `:9797`/`ensemble_prod`/`releases/` untouched.

---

## 5. Lane C — PG seam smokes (PASS 3/3)

Worker `e09604b6`, skill `integration-test`. Wall-clock 18m11s.

### 5.1 C(ii) — the 128th `.sql` through the REAL SQLite MigrationRunner — PASS (21/21 assertions)

Real runner (`daemon/migrations/runner.py`), scratch `/tmp` SQLite DBs, `timeout 300`.

- **Faithful boot path (create_all → runner): ABORTS on the PRE-EXISTING `20260714_000001`** (`near "EXISTS": syntax error` — `ALTER TABLE job_queues DROP CONSTRAINT` is invalid on SQLite; the documented blocker, fix commission already queued). Verified the blocker is **not** the 128th file (absent from the ledger at abort).
- **Sanctioned ledger-pre-mark** (`runner._record_skip`, documented `skip_reason`): the 128th file **applied cleanly, `skip_reason=None`**; table + 3 indexes present; the partial unique **behaviourally enforced**.
- **Runner-owns-the-DDL isolation** (model removed from `metadata`, so only the `.sql` can create it): table + all 3 indexes created **by the runner's comment-strip/split path on the 128th file** ⇒ the path the commission named is genuinely exercised, not stubbed.

Index DDL as created by the runner:
```sql
CREATE UNIQUE INDEX uq_prompt_assent_tokens_live_version ON prompt_assent_tokens (version_id) WHERE status = 'live'
CREATE UNIQUE INDEX uq_prompt_assent_tokens_token_hash   ON prompt_assent_tokens (token_hash)
CREATE        INDEX idx_prompt_assent_tokens_version_status ON prompt_assent_tokens (version_id, status)
```
Behavioural proof: a 2nd `live` row for one `version_id` → `UNIQUE constraint failed: prompt_assent_tokens.version_id`; a `consumed` row for the same `version_id` → accepted.

**Exact 128-file reconcile:** `128 = 120 applied + 1 pre-marked + 6 MANUAL:TRUE + 1 unparseable` (the unparseable one is `20260713_000001_create_skill_bank.sql` — no `-- UP` marker, skipped silently by the runner). ⚠️ *"all 128 files apply cleanly in order"* **cannot hold on a fresh SQLite DB** — the abort, the 6 MANUAL files and the 1 unparseable file all pre-date this lane. Disclosed, not attributed to prompt-assent.

### 5.2 C(i) — disposable-cluster boot smoke — PASS

`/usr/lib/postgresql/16/bin/initdb -A trust -D /tmp/assent-gate-pgdata -U nea` → `/usr/lib/postgresql/16/bin/pg_ctl -o "-p 8432 -c listen_addresses=127.0.0.1 -c unix_socket_directories=/tmp/assent-gate-pgsock"` → scratch DB `assent_gate_smoke` created **over TCP** (server binaries are off-`PATH` on this host; the unix-socket dir override is required because `/var/run/postgresql` is not writable by `nea`). Port 8432 verified free before use; 5432/5433/8079/8088/9797/7979/9798 untouched.

Daemon booted **from the feature worktree** with `PORT=8179` (**`DAEMON_PORT` in `.env` is INERT — `config.yaml` binds `${PORT:-8088}`, and 8088 is the ensemble self-system port: never bound**), `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` **pinned** (mandatory from a daemon-child shell), `ENSEMBLE_SELF_ENV=dev`, `POSTGRES_*` explicitly overridden → `livez=200`; boot log `Application startup complete.` / `Uvicorn running on http://127.0.0.1:8179`.

`\d+ prompt_assent_tokens` (scratch DB): table + PK + all 3 indexes, including `uq_prompt_assent_tokens_live_version UNIQUE, btree (version_id) WHERE status = 'live'::text`. **104 tables** materialised in the scratch DB. Targeting proof: `pg_stat_activity` on 8432 showed **12 live daemon connections, all on `assent_gate_smoke`** (`ensemble-manager`, `ensemble-checkpoint-adapter`); daemon pid `3255104` → `cwd=/home/nea/ensemble-src-wt-prompt-assent`, env `PORT=8179 / POSTGRES_PORT=8432 / POSTGRES_DB=assent_gate_smoke`.

**TARGETING VERIFICATION — PASS:** read-only `SET default_transaction_read_only=on` on the shared 5432 → `ensemble_dev` had **0** `prompt_assent%` tables **before and after** this lane (98 tables total, unchanged shape).

### 5.3 C(iii) — PG durable legs — PASS (10/10)

Discovered `tests/postgres/test_prompt_assent_token_store_pg.py` (`TestDurableLegPG`, 8 nodes) + `tests/postgres/test_prompt_assent_phase3_pg.py` (`TestCounterWiringPG`, 2 nodes). Run **serially** (no xdist — `tests/postgres/conftest.py` skips everything under xdist), `timeout 300`, `-m postgres`, `PG_TEST_HOST=127.0.0.1 PG_TEST_PORT=8432 PG_TEST_DB=assent_gate_durable PG_TEST_ALLOWED_DBS=assent_gate_durable` → **10 passed** (16.73 s).

Covered: mint/find/consume roundtrip · partial-unique enforcement on PG · re-mint supersedes · own-transaction property · **lockout revokes at threshold** · **concurrent counter increments serialize** · sweep retains consumed · **raw token never stored** · mismatch counter + lockout · **counter survives transition rollback**. Cluster log proves the constraint actually fired (`duplicate key value violates unique constraint "uq_prompt_assent_tokens_live_version"`).

### 5.4 Teardown (evidenced)

Daemon SIGTERM **after** verifying pid owns :8179 + `cwd` = feature worktree + cmdline ⇒ exited in 2 s, port released · 2 × `DROP DATABASE` · `pg_ctl stop -m fast` (port 8432 released) · `rm -rf` pgdata (55 M) + socket dir + scratch SQLite DBs ⇒ no 8179/8432 listeners left · repo status unchanged, HEAD `029416f2e` · canonical checkout never entered.

### 5.5 Lane C disclosures

- **Parity nit (for the lane owner, not a gate failure):** `version_tag` / `mint_is_dm` / `attempts` / `status` carry **client-side** SQLAlchemy defaults, so the PG table has **no server-side defaults** although the `.sql` declares `DEFAULT ''`. Names/types are byte-identical to the migration's contract, but a **raw-SQL** INSERT omitting those four columns hits a NOT NULL violation (ORM inserts unaffected).
- `ensemble_prod` read-only check **not established** (password auth failed for the ambient `ensemble` role). The required `ensemble_dev` check passed, and the daemon provably never targeted prod (`/proc` env + `pg_stat_activity` both pinned to 8432).

---

## 6. Lane D — K-3 live probe (W-1 flip-on hard gate) — **OUTSTANDING**

**Adjudicated in-line by the tester** (no worker): the environment cannot drive the probe and it must not be faked.

- **Not drivable from any environment on this host.** Recon verified, key-names-only, that **no** `DISCORD_*` / `TELEGRAM_*` / `SLACK_*` / `WHATSAPP_*` credentials exist in the worktree `.env`, the live install `.env`, or the canonical `.env`. The procedure needs the Emperor's **1:1 DM** to the relay bot **and** a **Discord group DM** (bot + another human) — human-interactive by design ("operator-run, once").
- **Procedure verified present**: `docs/runbooks/prompt-assent.md` (added `04cfaaabe`, amended `029416f2e`) — §"Arming / disarming (the kill-switch)" ≈ line 31 (default OFF, fail-closed per D-2) and §"The K-3 live confirmation (the outstanding pre-ship probe)" ≈ line 160 (5 steps: arm → 1:1 DM leg expecting a mint → group-DM leg expecting `mint-requires-private-channel` with no token row → optional fail-closed leg → file evidence). The refusal taxonomy the probe asserts is present.
- **Disposition:** ledgered **OUTSTANDING** — it transfers to the Emperor's ceremony lane. Per the commission's own ruling it blocks **arming any environment**, not the stage cut. The live leg was **not** simulated, faked, or approximated.

---

## 7. ensure.md validation

*(Core section — worker `7a6ef89f`, 11m02s. Release Gate items are out of scope for a pre-stage-cut gate except where the lanes above already cover them: the live-daemon E2E is Lane E's mandate, not a bare pytest run.)*

### 7.1 Core (always-on, scoped)

| Item | Requirement | Validation | Status |
|---|---|---|---|
| Critical | No regressions in changed packs — every pack in the blast-radius change set PASS | Lane A whole-tree dual-side + Lane B lane packs | ❌ **FAIL** — 2 net-new reds (N-1, N-2) |
| Critical | Deadlock / concurrency integrity — pack `concurrency_atomic_unit_test` PASS | registered pack run (worker `7a6ef89f`) | ✅ **PASS (quarantine-aware)** — raw pack exit **1** (5F/94P/74S), but all 5 are an **ACTIVE QUARANTINE.md row** (`tests/test_instance_metadata_atomic.py` ×5, `DetachedInstanceError`) and were **proven red-at-base** by an executed base leg at `0efdf106f` (identical 5-node FAILED set, identical exception). The pack's thread-identity / DB-offload nodes (`test_gate_threading_serialization.py`, `test_deadlock_fix.py`) **passed**. ⚠️ The pack has no quarantine-skip mechanism ⇒ it cannot print green; read this row as *no net-new concurrency defect*, not as "pack green". |
| Critical | No sync DB calls on the asyncio event loop (covered by the concurrency pack) | same pack (thread-identity nodes) | ✅ **PASS** — the pack's thread-identity / DB-offload nodes are green |
| Critical | `dev.sh` includes `--timeout-graceful-shutdown 10` | static grep (worker `7a6ef89f`) | ✅ **PASS** — `dev.sh:155` carries `--timeout-graceful-shutdown 10` in the load-bearing uvicorn argv (comment at :152) |
| Important | All callers of the converted async functions await (`_get_system_prompt_tokens`, `_compute_context_usage`, `get_queue_stats`) | static grep (worker `7a6ef89f`) | ✅ **PASS** — zero non-awaited call sites: `_get_system_prompt_tokens` awaited at `instance_messaging.py:1228`, `:1492`, `compact_executor.py:879`; `_compute_context_usage` awaited at `:1258`; `get_queue_stats` awaited at `manager.py:13528`, `routers/messages.py:939`, `routers/instances.py:366`, `:596`, `tools/instance.py:4830`. The only non-awaited grep hits are docstrings/comments and a `hasattr` probe string |
| Nice-to-have | No dead code from the fix | import check / grep (worker `7a6ef89f`, supplementary) | ✅ **PASS** — the lane deleted no daemon symbol: the `_unified_diff` removal is a **pure lift-and-shift** to `daemon/services/prompt_version_diff.py`, re-bound at `routers/prompts.py:435` and **import-verified at runtime**; all 8 added Python modules import cleanly |

*Note:* the whole-tree sweep (36,790 nodes, head leg) already covered the concurrency pack's node set and reported **no failures in it**; the registered-pack run is the confirmation leg.

### 7.1 disclosures (Core)

- **The concurrency pack's raw result is exit 1, not green** (5F/94P/74S). The 5 failures are 100% the ACTIVE `QUARANTINE.md` row (`tests/test_instance_metadata_atomic.py` ×5, `DetachedInstanceError`; row dated 2026-11-10, same signature, same 5F/94P/74S) and were **proven red-at-base** by an executed base leg at this lane's base `0efdf106f` (read-only `git archive` → `/tmp`, same `.venv`, `PYTHONPATH`-pinned, import-verified `daemon → /tmp/base-leg-…/daemon/__init__.py`) — identical 5-node FAILED set, identical exception. The pack has no quarantine-skip mechanism, so it **cannot** print PASS. Read as: *no net-new concurrency defect* — not "pack green".
- **The env scrub was load-bearing, not ceremony.** The pack does **not** scrub, and the ambient environment carried `POSTGRES_DB=ensemble_prod` pointing at a **remote** `10.44.0.2:5432`. The worker scrubbed `POSTGRES_DB/USER/PASSWORD/HOST/PORT` **per variable** (a bare `env -u POSTGRES_*` is a silent no-op) plus `ENSEMBLE_AUTH_ISSUER`/`ENSEMBLE_AUTH_DISCOVERY_URL` — the latter because ambient values make `create_app()` fail its origin check and produce collection ERRORs that mask reds. **No PG contact.**
- **Test-infra anomaly (filed `fbffb067`, minor):** the pack's `RESULT: FAIL` / `RESULT: TIMEOUT` echoes are **dead code** — `set -euo pipefail` aborts at the bare `timeout 280s pytest` line before `EXIT_CODE=$?` runs, so the failure path prints no `RESULT:` line (exit-code semantics are unaffected — rc 1 propagates, but a consumer grepping stdout for `RESULT:` sees nothing on failure). Suggested fix: `EXIT_CODE=0; timeout 280s … || EXIT_CODE=$?`. Not this lane's to land.

### 7.2 ensure.md Improvement Notices

- The Core "no regressions in changed packs" requirement is validated here by a **whole-tree dual-side sweep** rather than by per-pack runs — the packs in scope (the two prompt-assent lane packs) are green, but two reds live in packs that were not changed by the branch. Suggested rewrite for the owner: *"No regressions in the blast-radius change set — the scoped dual-side FAILED-list diff shows zero net-new reds (red-at-base ≠ net-new); pack-level PASS is required only for packs the change set touches."* (ensure.md is user-owned; this gate does not modify it.)
- No contradiction was found between ensure.md's Core methods and the pack/timeout rules on this commission.

---

## 8. Lane E — T3.5-shaped E2E on the real dev daemon (PASS 9/9)

Worker `0539d04f`, skill `e2e-test`. Wall-clock 41m47s (05:39:59Z → 06:21:46Z) — 6 boots (1 armed + 3 restarts + invalid + unset), ~18 ari turns + 3 maintenancer turns, per-leg waits all < 5 min.

**Boot discipline:** `PORT=8079` exported (because `DAEMON_PORT` is inert — `config.yaml` binds `${PORT:-8088}`; **8088 was never bound**), `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned on every boot, `ENSEMBLE_SELF_ENV=dev`, `POSTGRES_*` set explicitly to the dev values. Connection proof: `pg_stat_activity` → **13/13 client connections to `ensemble_dev`, 0 to prod**. Health: `/api/health` 200, `/livez` 200, `/readyz` 200. Queue hygiene: `GET /api/jobs?status=pending,processing` → `{"jobs":[],"total":0}`; `message_queue` empty; 0 pre-existing `prompt_versions`.

| # | Leg | Result | Evidence |
|---|---|---|---|
| 1 | Create a real PENDING row | ✅ PASS | `create_draft → submit_for_approval` → `f8646aca-48a2-4918-87d5-2466b2cb7430`, `state=pending_approval`, `artifact_class=CORE`, agent `prompt-assist-e2e-probe` (synthetic id, so approval cannot alter a real agent's effective prompt) |
| 2 | `list` reaches chat | ✅ PASS | `prompt_assent list — 1 pending CORE item(s) - version_id=f8646aca… agent=prompt-assist-e2e-probe artifact=rule version=1`; cross-agent (no agent filter) confirmed |
| 3a | mint — pure API (honest counter-measure) | ✅ PASS | typed **`mint-requires-private-channel`** and **not** `no-user-origin-turn` ⇒ the api-origin window **did** arm (factor 2 proven on the real daemon); **no token row written** |
| 3b | mint — adapter-shaped metadata | ✅ PASS | diff + token + `render_sha256: 573d10e7…` rendered in chat; row `live`, `token_hash` 64-hex, `mint_source_id=api`, `mint_channel_id=e2e-dm-01`, `mint_user_id=e2e-human-01`, `attempts=0`, TTL exactly +60 min (05:47:39Z → 06:47:39Z). **Hash-only proof:** `sha256("CONFIRM-<canonical-raw>")[:12] == 727b97b12285 == token_hash[:12]`, while the grouped rendering hashes to `b2c539923184` ⇒ the raw token is not stored |
| 4 | Human echo | ✅ PASS | token echoed verbatim in the api user message; ari independently corrected the grouped/raw spelling and relayed the grouped form; `canonical_token_form` / `nonce_in_content` accepted it |
| 5 | approve + provenance | ✅ PASS | `prompt_assent approve — OK`, `pending_approval → approved`. Audit row (`actor_auth_method=assent-relay`): `approval_path=chat-assent`, `approver_subject=api:e2e-human-01`, `relay_agent=ari`, `relay_instance_id=ff786e2b…`, `relay_message_id=2f35ac47…` **and** `relay_source_message_id=platform-msg-2f35ac47…`, `relay_source_id=api`, `relay_channel_id=e2e-dm-01`, `relay_window_stamped_at/expires_at`, `assented_at`, `token_fingerprint=727b97b12285`, `render_sha256` = the mint's shown sha. **Leak scan** over token table + audit payload + version content → **0 hits** for all 4 spellings (raw / no-dash / grouped / body). Token row → `consumed`, `consumed_by_message_id` = the echo row |
| 6 | In-RAM window boot-wipe (restart) | ✅ PASS | pre-restart mint of a 2nd item → token live. Post-restart approve on an instance with **no** window → typed **`no-user-origin-turn`** (observed source `instance_ops:946a1279…` classified `reserved-internal`), `flags=mismatch=0` ⇒ **a window refusal does NOT burn the K-9 budget**. Durable token row still `live` after the restart (forced re-mint refused `mint-live-token-exists`) and consumed 3 restarts later (06:17:30Z) → approve OK. **Fresh-window proof:** that approve's `relay_window_stamped_at=06:17:26Z` (post-restart), *not* the pre-restart 05:53 stamp ⇒ the pre-restart window was **not** inherited |
| 7 | K-9 in-RAM budget clearing on restart | ✅ PASS | budget=2 on relay instance `ff786e2b`: `token-mismatch` (`mismatch=1`) → `token-mismatch` → **`mismatch-budget-exceeded` (count=2)**. After restart: the same wrong-token attempt → `token-mismatch` again ⇒ **in-RAM budget cleared, mismatch counts fresh**. **Mechanisms kept distinct:** the durable per-token counter moved only on token-bound mismatches (`attempts=3`, survived 3 restarts, < lockout 5); wrong-token attempts never touched it |
| 8 | Chat-never-log | ✅ PASS | across all 5 daemon logs: **0 hits** for prompt content, `BEGIN/END CANONICAL RENDER`, `unified diff`, token (any spelling), channel/user ids, item/version ids. Only content-free INFO: `prompt_assent outcome=ok\|refused action=… code=… agent=ari instance=… count=… flags=…` |
| 9 | Kill-switch matrix | ✅ PASS (1 correction) | **invalid (`sandwich`) → the daemon REFUSED TO START**: `pydantic_core.ValidationError: 1 validation error for PromptAssentConfig / enabled: Input should be a valid boolean … input_value='sandwich'`, exit 1, **8079 never bound** — matches the runbook ("the BOOT layer RAISES… a typo is a LOUD boot failure"); it **contradicts the dispatch text** ("fails closed at call time") because the env is fixed at boot and the boot raises first. **unset →** boots clean, default OFF, the tool **stays registered**, mint refused `lane-disabled` ("the prompt-assent lane is disabled (ENSEMBLE_PROMPT_ASSENT_ENABLED) — the tool stays registered; no action is taken") |
| 10 | Teardown | ✅ PASS | guarded stop each time (pid owns 8079 + `cwd` == worktree + cmdline checked before any signal); daemon stopped; `ss` → **8079 FREE**; no stray uvicorn; live daemon on 9797 untouched (pid 2390250 still listening); **8088 never bound**; `ensemble_prod` never touched; API-key file deleted; **27 scratch files scrubbed** of token material; repo not modified (the 3 dirty `.agents/` files are foreign WIP, untouched) |

### 8.1 The five mandated disclosures — observed, not merely read

1. `self-approval-attempt` never fired; the load-bearing window-pop **was** exercised (`no-user-origin-turn` on an agent-origin dispatch). 2. D-7 persona gap — N/A (CORE only). 3. **Factor 1 proved weak:** ari's `user_confirmed=true` did **not** get the mint past the private-channel gate. 4. List is cross-agent ✓. 5. **Refusals wrote no ledger row** — after 3 mint refusals + 3 approve refusals the audit table held only CREATED/SUBMITTED/APPROVED, and the token table only the 2 minted rows.

### 8.2 Lane E disclosures (not hidden)

- **Discord-first simulation — the one harness intervention.** The api chat path cannot carry `metadata["discord"]` (the Discord adapter's build site needs a live gateway), so mint/approve required injecting the adapter-shaped metadata into the `message_queue` row of the lane's **own** test message. The pure-API leg (3a) is the honest counter-measure (typed refusal, no token row). Everything else — window stamping, factor gates, mint/approve transactions, the D-4 hook, ledger writes — ran through real daemon code paths via real ari LLM turns. **Not a product defect.** This is precisely the fidelity gap the OUTSTANDING K-3 live probe owns.
- **Typed no-window refusal needed a genuinely non-user-origin dispatch** onto an instance with **no** window (an `instance_ops:` nudge onto a fresh instance). A nudge onto an instance that already had a user turn lands in the RAM-injection lane and **keeps** the old window (observed `token-channel-mismatch`). `send_message` to ari is blocked (no agent lists `ari` in `team_members`); `ask_questions` is hidden for API-created instances (source_type gate).
- **Notable agent-layer finding:** the `maintenancer` agent **refused** to relay an approval-shaped nudge ("a self-attestation of user confirmation at a gate that confirmation never reached through a user-origin channel"), filed a `report_anomaly`, and complied only once the message was framed as a `mint` — the agent layer, not just the tool, defends the boundary.
- **Leftover dev-DB artifacts** (disclosed, no real agent affected): 2 approved `prompt_versions` rows for `prompt-assist-e2e-probe`, 2 consumed token rows, 6 audit events, and the test instances (ari ×2, maintenancer ×3) in `ensemble_dev`; **0 pending rows left**. `ari`'s owed T3.5 `tools_note.md` re-stamp was deliberately **not** performed (separate, Emperor-owned lane).
- Harness slip (self-corrected): the first driver always posted to *ari*, so three "nudge" instructions went to ari instead of the maintenancer — detected via the missing queue rows and fixed by parameterising the target instance; **no invalid conclusion was drawn from it**.

---

## 9. The commission's mandatory matrix — all six proven on the built code

| # | Mandated cell | Verdict | Evidence |
|---|---|---|---|
| 1 | **APPROVE happy path with a REAL token echo** | ✅ PASS | Lane B: matrix pack 249/249 green (the `test_prompt_assent_lane.py` approve cell) + Phase-3 pack 107/107. Lane E (real daemon): `prompt_assent approve — OK`, `pending_approval → approved`, complete `assent-relay` provenance chain, token row `consumed`. Typed code: none (success). |
| 2 | **REJECT wrong token** | ✅ PASS | Lane B: pack cell green. Lane E leg 7 (live): typed `token-mismatch` ×2 then `mismatch-budget-exceeded` (count=2) — no state change, and the wrong-token attempts never touched the durable per-token counter (`attempts` stayed 0 until token-bound mismatches). |
| 3 | **REJECT expired token** | ✅ PASS (pack level) | Lane B: matrix pack expiry cell green (249/249). *Limit:* not re-exercised against the live daemon — the minted TTL is +60 min, which the lane cannot wait out; the freshness/expiry cells cover mint AND approve alike. |
| 4 | **REJECT non-pending row** | ✅ PASS (pack level) | Lane B: matrix pack non-pending cell green. *Adjacent live evidence (not the same cell):* after approval the row left PENDING and a re-mint for the same version was refused `mint-live-token-exists`. |
| 5 | **REJECT agent-self-approval attempt** | ✅ PASS (pack level) | Lane B: matrix pack self-approval cell green. *Live note:* the agent-origin attempt was refused **earlier and more strongly** by `no-user-origin-turn` (Lane E leg 6) — the self-approval branch was never reached on the live daemon. |
| 6 | **REJECT non-user-origin turn** | ✅ PASS (live) | Lane E leg 6 (real daemon): typed `no-user-origin-turn` with `observed source 'instance_ops:946a1279…' classified 'reserved-internal'`; `flags=mismatch=0` ⇒ no state change and **no budget burn** (the ruling's no-burn leg). Lane B: pack cell green. |

**No-burn / no-state-change ruling:** refusal legs write no ledger row (Lane E disclosure 5: after 3 mint + 3 approve refusals the audit table held only CREATED/SUBMITTED/APPROVED, and the token table only the 2 minted rows) and no budget burn (leg 6, `flags=mismatch=0`).

---

## 10. Verdict

**NO-GO** for the stage cut of `feature/prompt-assist` **as it stands.**

**Why:** Lane A found **2 net-new deterministic reds** (3× isolated on both arms, head `FFF` / base `PPP`) that the branch causes. ensure.md Core treats "no regressions in the change set" as **Critical**, and a deterministic head-only red is exactly what a pre-stage-cut gate exists to catch. Both are **test-pin staleness, not production defects**: the feature's behaviour is proven green end-to-end (Lane B 249 + 107 nodes; Lane C PG seams; Lane E real-daemon flow with full provenance and zero token leakage). The gate verdict is nonetheless NO-GO because a red is a red — it is not laundered into a disclosure.

**Distance to GO — two mechanical edits, both test-side, no production code:**
1. **N-1** — move the `discord/adapter.py` row from `SEAL_UNTOUCHED_BASE_VALUES` into `SEAL_REPINNED_BASE_VALUES` with the new sha `a9f3a711fc79…` — **conditional on the seal owner authorizing that re-pin** (Open question 1).
2. **N-2** — re-express the memoir lane's migration-census pin so a second lane's migration does not invalidate it (Open question 2).
Then re-run the 2 cells (plus the 2 lane packs for safety) — minutes, not another whole-tree leg.

**What is NOT blocking:** the K-3 live probe is **OUTSTANDING** by the commission's own ruling (environment cannot drive it; procedure verified in the runbook). It blocks **arming any environment**, not the stage cut.

---

## 11. Residual risks for the ceremony lane

1. **K-3 fidelity gap (the armed-environment risk).** Lane E's mint/approve legs required adapter-shaped metadata injection because the api chat path cannot carry `metadata["discord"]`. On a real relay this is native — but until the K-3 probe runs on a live Discord 1:1 DM (and the group-DM leg refuses with `mint-requires-private-channel`), the **Discord-shaped** mint path is unproven in production shape. **Do not arm an environment before the probe.**
2. **Kill-switch failure mode is a boot failure, not a call-time refusal.** An invalid `ENSEMBLE_PROMPT_ASSENT_ENABLED` value refuses to START the daemon (loud pydantic error, port never bound). Correct fail-closed posture, but it means a typo takes the daemon down rather than degrading one tool — the operator arming procedure should say so explicitly.
3. **PG server-side defaults gap** (Lane C §5.5): `version_tag` / `mint_is_dm` / `attempts` / `status` are client-side defaults only; a raw-SQL INSERT omitting them hits NOT NULL. ORM paths are unaffected, but any future non-ORM writer (a migration, a script, an ops query) will trip it.
4. **The pre-existing SQLite migration abort** (`20260714_000001`) means the SQLite runner cannot apply the full 128-file set on a fresh DB; the 128th file's clean application was proven only with that one file pre-marked and under runner-owns-the-DDL isolation.
5. **Freshness flake** remains live at ~13-20% on the freshness positive leg (pre-existing; `now_iso()` truncation race). It flaked 2 of 6 runs in Lane B and reddens pack attempts — the durable backdate-widening fix is deferred and is the most likely source of future spurious reds in this lane.
6. **Two cross-lane pins are brittle by construction** — the seal row and the memoir census both assume single-lane ownership of a global fact. The next lane that touches a sealed segment or adds a migration will trip the same class.
7. **Dev-DB residue** from the E2E (2 approved `prompt_versions` rows on the synthetic `prompt-assist-e2e-probe` agent, 2 consumed token rows, 6 audit events, 5 test instances) is intentional and disclosed; `ari`'s owed `tools_note.md` re-stamp was deliberately left to its own lane.
8. **Gate cost:** a full-tree dual-side Tier-2 on this corpus costs ≈73 min of legs plus ≈40 min of 3× isolation ladder (≈129 min actual, 90-min advisory breached). Future gates should parallelize the ladder by node.

---

## 12. The five most important evidence lines

1. **Lane A:** net-new = **2** (deterministic, 3× isolated, head FFF / base PPP) — N-1 `test_anomaly_report_duty_prompts.py::TestTheSealRePin::test_the_untouched_rows_keep_their_base_values`; N-2 `test_memoir_v2_schema.py::test_the_migration_census_delta_is_exactly_one_new_file`. All other sweep candidates classified as artifacts/flakes/environmental; every expected known red identical on both legs.
2. **Lane B:** matrix pack **249/249 PASS** (32 s) + Phase-3 pack **107/107 PASS** on attempt 1 of 4; both post-S-1 counter-mutation pins **REDDEN** at the new tip — the digest-path bypass produced a 13-failed/236-passed blast radius, proving the digest rule is genuinely guarded.
3. **Lane E:** `prompt_assent approve — OK` on the real dev daemon with the complete `assent-relay` provenance chain, **0 raw-token hits across 4 spellings**, chat-never-log clean over 5 logs; restart wiped the in-RAM window (fresh `relay_window_stamped_at`) and cleared the K-9 budget while the durable per-token counter survived 3 restarts.
4. **Lane C:** `prompt_assent_tokens` created by `create_all` on a **disposable native PG 16 cluster** (+3 indexes, partial unique behaviourally enforced) and **10/10** durable legs PASS; targeting verified — `ensemble_dev` had **0** `prompt_assent%` tables before and after.
5. **Lane D:** K-3 live probe **OUTSTANDING** — not drivable on this host (no platform-source credentials in any `.env`), procedure verified at `docs/runbooks/prompt-assent.md:160`; it blocks **arming**, not staging.

---

## 13. Artifacts

| Artifact | Path |
|---|---|
| This report | `/home/nea/ensemble-src-wt-prompt-assent/.agents/tester/RESULTS/2026-11-11-prompt-assent-tester-gate.md` |
| Lane A evidence pack (survives teardown) | `/home/nea/gate-evidence/assent-gate-20261111T052600Z/` |
| Lane E evidence | `/tmp/prompt_assent_e2e/` (daemon1..6.log, verify.py output, driver scripts) — ephemeral |
| Lane C scratch (removed) | `/tmp/assent-gate-pgdata`, `/tmp/assent-gate-pgsock`, scratch SQLite DBs — all removed |
| Lane B scratch (removed) | `/tmp/assent-mut-tip` detached worktree — removed |
| Lane A base worktree (removed) | `/tmp/assent-gate-base-0efdf106f` — removed + pruned |
| Lane packs (registered this gate) | `test/packs/prompt_assent_matrix_test.sh` · `test/packs/prompt_assent_phase3_unit_test.sh` |

## 14. Code changes made by this gate

**Docs only — no production code, no test code, no live surfaces.**

| Path | Change |
|---|---|
| `.agents/tester/RESULTS/2026-11-11-prompt-assent-tester-gate.md` | this report (new) |
| `.agents/tester/PACKS.md` | registered the two previously-unregistered lane packs (`prompt_assent_matrix_test`, `prompt_assent_phase3_unit_test`) with scope, timeout, last-run and status — PACKS.md integrity duty (a pack script that is not registered never enters the pack runner) |

**Docs-only commit:** `f2b0c2a1b16e2e1da4f65a601a3b68de1328b029` — subject *"docs(prompt-assent): tester gate report — NO-GO on 2 net-new cross-lane pin reds (seal re-pin classification + memoir migration-census pin), lane packs 249+107 green, PG seams + real-daemon E2E green, K-3 live probe outstanding"*; exactly **2 files / +348 lines** (`.agents/tester/PACKS.md` +3 registration rows, this report +345). Local only — **not pushed** (pushing is the leader's call).
The 3 pre-existing dirty `.agents/` files (`.agents/approver/active.md`, `.agents/tidier/notes.md`, `.agents/approver/prompt-assent-tracking.md`) are approver/tidier-owned and were **not** staged, swept, or modified by this gate — sha256 byte-identity verified pre/post commit (`active.md` `3cbca39d…d75`, `tidier/notes.md` `3906506e…cf9`, `prompt-assent-tracking.md` `32da877e…535`).
*(This line was added in a follow-up docs-only commit — a commit cannot contain its own SHA.)*

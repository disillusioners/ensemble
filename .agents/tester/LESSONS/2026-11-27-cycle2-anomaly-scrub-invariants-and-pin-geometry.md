# Cycle-2 lessons — anomaly scrub invariants, pin geometry, and harness traps

- **Date**: 2026-11-27
- **Lane**: `fix/never-started-detector-anomaly-truncation` · base `4a17398d2` · TIP `73ea42ce2`
- **Cycle-2 range**: `0f5af2a5a..73ea42ce2` — 4 commits (`fd5df128f`, `004216694`, `07cd7d5ae`, `73ea42ce2`), 5 files
- **Context**: FINAL independent verification of the `report_anomaly` truncation fix + the cycle-2 scrub-path
  security corrections (INV-1…INV-5) and the read-only `never-started-running` detector.

---

## L-1 · A redaction token must NOT be a plaintext oracle — seed it from the secret ID

The stored redaction token is `__ENS_REDACTED__` + `sha256(secret_id)[:12]` + `__`
(`daemon/services/anomaly_service.py:705-709`). The seed is the secret's **UUID**, never its plaintext.

**Why:** `sha256(plaintext)[:12]` over a low-entropy secret is an offline verification oracle — an attacker
holding a stolen anomaly row could brute-force the secret against the digest. Seeding from the (random,
high-entropy) secret ID keeps the token a non-reversible correlation handle.

**Consequence for test authors:** a test that asserts "the stored row contains `__ENS_REDACTED__` +
`sha256(plaintext)[:12]`" is asserting the WRONG token and will fail against correct code. Assert the
prefix, or compute the expected token from the secret ID (`sha256(secret_id)[:12]`). Two verification
legs hit this and had to be corrected mid-run (F4, F6).

## L-2 · A cap-smaller-than-the-token test geometry makes a FULL-PATTERN assertion vacuous

`test_t_c_no_redeemable_handle_or_bisected_token` uses `content_limit = 30`. The legacy redeemable marker
`__ENS_SECRET__<uuid4>__` is **52 chars**, so under that geometry it is BISECTED to `xxxxx__ENS_SECRET__`
before the assertion runs. The pin's full-token regex
(`__ENS_SECRET__[0-9a-f-]{36}__`) cannot match a bisected token, and its bisect guard scans
`__ENS_REDACTED` only — so **swapping the redaction token back to a redeemable one leaves the pin GREEN**
(mutation-proven: F-C1 in the cycle-2 RESULTS).

**Rule:** when a pin's job is "the forbidden token must never appear", the fixture geometry must keep the
forbidden token WHOLE (limit ≥ token length), **and** the substring/bisect guard must scan the forbidden
token's prefix too — not only the replacement token's prefix. A full-pattern assertion over a geometry that
can never produce a whole token proves nothing.

Corollary: "the fix is load-bearing" and "the named pin is load-bearing" are DIFFERENT claims. In cycle 2 the
former held (two sibling pins went RED) while the latter did not (the named pin stayed GREEN). Always report
the two separately.

## L-3 · The xdist masking check is mandatory for any sharded leg

A pytest-xdist leg can print a valid-looking summary while NODES ARE LOST. On every xdist leg, check:
(a) the banner's `[gwN] workers [X items]` equals the `--collect-only` total; (b) the summed progress
outcome-characters equal X; (c) the summed per-gateway failure-header counts equal the reported failure
count. In cycle 2 both legs passed all three (HEAD 689/689, BASE 650/650) — without this check a shard
masking would have silently produced a false "red sets identical" verdict.

## L-4 · `InstanceManager.__init__` runs boot-time `clear_all` wipes — seed AFTER building the manager

`daemon.manager.InstanceManager.__init__` wipes `message_queue` and `task` (boot recovery). A fixture that
seeds the pending task BEFORE constructing the manager has it silently deleted — the detector then renders
`count: 0` and the harness reads as a product failure. Order: build the manager → seed → assert the row
counts (`task count == 1`) before running the detector.

Related fixtures traps (same lane): `job_locks` has the DB trigger `job_locks_active_guard()` (a lock row
with an `instance_id` requires the referenced job item to be `admission_state='active'` ⇒ the fixture needs
an extra active job item); the ORM class for `job_queue_items` is `JobItem`, not `JobQueueItem`; and
`job_queue_items.queue_id` FKs `job_queues.queue_id` (commit the queue row first).

## L-5 · The report path's FIRST write is the CLUSTER row, not the report row

`AnomalyRepository.insert_cluster` (`daemon/repositories/anomaly/repository.py:233`) runs before
`insert_report` (`:221`). A read-only proof that asserts "no `anomaly_reports` row was written" would pass
vacuously; the read-only fence must count BOTH tables (`anomaly_clusters` + `anomaly_reports`) plus the
detector's 8 tables, and the write probe must expect the rejection on `INSERT INTO anomaly_clusters`.

## L-6 · Role-level read-only beats per-connection options

`ALTER ROLE "<ro-role>" SET default_transaction_read_only = on` + `engine.dispose()` fences EVERY physical
connection in the pool (proved: 24 connections, each printing `SHOW default_transaction_read_only -> 'on'`
as its FIRST statement). A per-connection `options='-c default_transaction_read_only=on'` can be lost by a
pool reset / a different construction path. Use the role-level fence and assert the FIRST statement per
connection.

## L-7 · `scripts/run_tests_scrubbed.sh` is a LEAK GATE, not a timer

The house runner scrubs `POSTGRES_*` / libpq / `PORT` / the Plane workspace vars, refuses an ambient
`ENSEMBLE_SELF_ENV` outside `{unset, dev, demo, sandbox}` with **exit 78**, then `exec`s
`.venv/bin/pytest "$@"`. It has **no internal timeout of its own** ⇒ the dual layer is the outer
`timeout 300` plus pytest's own `--timeout` (config default 30 s). Do not describe the runner as the
inner guard. Also: the runner needs NO `.env` (no dotenv load on the test import path) — a scratch worktree
can be provisioned without copying any key material.

## L-8 · `env -u POSTGRES_*` is still the silent no-op it always was

Per-variable scrubbing only. A "fenced" run that leaves an ambient `POSTGRES_DB` visible can contact the
WRONG database while believing it is fenced. (Recorded again this cycle because a worker proposed a glob.)

## L-9 · A frozen fixture can be refreshed legitimately — but the refresh must be source-traceable

Cycle 1 refreshed `tests/unit/tools/fixtures/module_level_inputs_b_base.json` (exactly one changed leaf:
`instance_health.description` "seven" → "eight" read-only detectors, character-identical to the live tool
doc) and the test file itself was untouched. Cycle 2 changed NEITHER the fixture nor its test. A frozen
fixture that lags a count-bearing doc string is a TEST-side debt — it shows up as a net-new red that looks
like a product defect; verify with `git diff --stat <base>..<tip> -- <fixture> <test>` before blaming the
branch.

## L-10 · The `__ENS_SECRET__` marker regex is ANCHORED and fullmatch-only

`ENS_SECRET_MARKER_RE` = `^__ENS_SECRET__(<uuid4>)__$` (`daemon/services/secret_service.py:162-174`); the
module's documented contract is `.fullmatch()` ONLY — never `re.compile(MARKER_RE).match()`. Consequences:
`findall()` over a whole text returns `[]` even when a valid marker is present, and a bisected marker is
invisible to the parser. The redemption surface funnels through `secret_resolver.resolve_marker` /
`resolve_secret_markers`; the DB layer (`SecretService.resolve_marker`) has exactly one production caller.
A marker that is NOT `__ENS_SECRET__<uuid4>__` is not redeemable at all (returns unchanged / raises
`secret_not_found`) — which is exactly why the D1 `__ENS_REDACTED__` token is safe to persist.

## L-11 · The harness's `write_file` and the shell `bash` tool can see DIFFERENT `/tmp`

In this harness, a script authored with `write_file` under `/tmp` was invisible to the shell, and a
`mktemp -d` directory under `/tmp` was invisible to `write_file`. Author + run probe scripts in ONE shell
(heredoc) and prefer `/var/tmp` when a tool boundary is unavoidable.

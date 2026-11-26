# Architecture Recommendation — Promote DB Preflight: dump → backup-artifact validation

**Date:** 2026-11-26
**Lane:** `promote-db-preflight-validation`
**Status:** COMPLETE (decisions D1–D8, F1, F2 ruled)
**Worktree:** `/home/nea/ensemble-src-wt-preflight-validation` — branch `feature/promote-db-preflight-validation`, cut from `origin/latest` = `8b646ae37` (v0.31.60)
**Canonical checkout `/home/nea/ensemble-src` is a DETACHED HEAD (`bf4bfde6e`) that drifts by design — NOT read as truth; every anchor below is `origin/latest`.**

---

## Revision history

- **Fan-in amendment (2026-11-26, after the D6 report landed).** The D1-A worker was **terminated after a ~54-minute stall** on one in-flight `bash` call and **re-dispatched once** as `43f6d961` (high tier, narrow brief) per the fan-in escape valve's max-1-re-dispatch rule. **No ruling changed**; only the provenance record in §17.
- **v1 → v2 (amended in place, 2026-11-26).** The D6 worker landed **after v1 was written** and **refuted one detail** of it. v1 ruled that the R6 opt-in is read from the daemon's own `os.environ`. That surface is **boot-time only** on this deployment — the systemd unit carries a **zero-env contract**, the runtime env is minted by `launcher.sh` at spawn time, and `load_config` *"`reads os.environ ONCE`"* — so it **cannot express a per-promote opt-in**. **v2 moves the opt-in to a tool-call parameter** on `system_upgrade`, bound into the `PendingOp` at arm time. **No other ruling changed.**
  The superseded v1 is preserved in git at commit `99df5d98c` (`git show 99df5d98c:.agents/shared/planning/promote-db-preflight-validation/architecture-recommendation.md`). The lane ships **one** artifact per the commission, so the amendment is in place with the prior text retained in git rather than as a second file.

---

## 0. The ruling in one paragraph

The promote ceremony's DB preflight stops dumping the live database and instead **validates the newest daily backup artifact by reading the lane's own registry metadata** over the sanctioned PostgreSQL channel. The leg lives in the **daemon tool lane**, as a new check in the `system_upgrade` arm preflight — the only place that holds the registry table's **owner** credential, the per-promote **lock**, and a **pre-nonce refusal** path. The remembered identity lives as a **new top-level key `db_preflight` in `releases/state.json`** — the upgrade lane owns no database table, and this is the seventh key added through `ensure_extensions`. The shell ceremony (`promote.sh` / `lib.sh`) is **not touched at all**. The freshness bound is **48 h measured on the artifact's own `finished_at`**. Staleness **fails the promote loudly**. The `full-dump` opt-in **cannot mean "dump from the ceremony"** — a VM-side dump is impossible on five independent grounds — so the opt-in is **recognised and refused loudly**, carried per-promote on a **tool-call parameter** (`system_upgrade(..., db_preflight=…)`) that crosses **no** executor boundary, with the real dump capability framed as a **cluster-side Job for the Emperor**.

---

## 1. The Emperor's ruling (verbatim)

> "the postgres on K8S with longhorn will have daily backup (not dump every promote). ceremony's preflight is only a validation: yes, this correct, don't waste more resource just to backup before each promote, we in rapid development so each day may have lot of new version, backup everytime is slow and waste. Also don't need to validation all the time, we backup one a day, so check the lastest backup, remember it, if that backup already checked, skip."

## 2. Requirement map

| Req | Ruling | Where it lands |
|---|---|---|
| **R1** stop dumping; validate the latest daily backup | Validated | new arm-preflight check, `daemon/tools/upgrade_db_preflight.py` |
| **R2** cheap + meaningful; corrupted/truncated FAILS LOUDLY, naming the artifact | Validated **as metadata** (lane's own `zstd -t` verdict); see §5 (D4) and §15 (disclosure) | `integrity_ok` + `bytes>0` + age bound, table-level read |
| **R3** remember the identity durably; NOT POK | `releases/state.json` → `db_preflight` | `ensure_extensions` + `journal_write` |
| **R4** skip when unchanged; log LOUDLY; stamp MUST ADVANCE on skip | Implemented; stamp advances; history row appended | `record_db_preflight` helper |
| **R5** no backup / broken ⇒ FAIL LOUDLY; never silently proceed; never fall back to dumping | Implemented; five distinct refusal tokens | `_refusal()` |
| **R6** full dump behind an explicit opt-in | **Honoured as an explicit refusal** — no VM-side dump exists (five grounds, §8). The opt-in is carried per-promote on a tool-call parameter and **never crosses the executor boundary** | `db-preflight-full-dump-unavailable` |
| **R7** do not weaken the transaction | **The shell preflight is untouched by construction** (§9) | arm-side only |
| **R8** C-1 class discipline | Satisfied trivially — **the lane adds no shell code** | — |

---

## 3. D1 — WHERE THE VALIDATION LIVES

### Ruling: **the DAEMON TOOL LANE** — a new check in the `system_upgrade` arm preflight.

**Exact placement** (`daemon/tools/upgrade_tools.py`): inside the ARM PREFLIGHT block, **immediately after the scripts-provenance guard closes (after `:4377`, where `stale-scripts-dir` returns) and immediately before the `── Step 0c — arm-time journalctl capture` step (`:4381`)**.

**Refusal shape, copied verbatim from its siblings** (`:4354-4360`):

```python
journal_lock_release(install_dir)
return _refusal(
    label,
    "<token>",
    "arm preflight: <detail> — nonce NOT burned (the arm simply did not "
    "happened; nothing consumed)",
)
```

**Why this placement is correct:**
- It is **downstream** of the 3-factor live gate and every busy / cooldown / lock refusal, so it can neither reorder nor weaken them.
- It runs **under the held lock** (`journal_lock_acquire` at `:4046`), so it is serialized against concurrent promotes.
- It precedes the **nonce burn** (`consume_pending_action` at `:4458`) and the **`pending_op` write** (`:4479`) — so a refusal costs the operator nothing: the nonce is unspent and the arm "simply did not happen".
- It is **upstream** of the executor spawn, which is gated on a matching durable `pending_op` (`daemon/manager.py:6785-6810`: *"drain refused spawn: no matching durable pending_op"*). **A daemon-side refusal therefore halts the ceremony entirely — the executor is never spawned.**

### The credential question (the crux)

| Fact | Evidence |
|---|---|
| The daemon's **application** database **IS** the cluster's `ensemble_prod` at `10.44.0.2:5432`, role `ensemble` | `daemon/tools/ens_db_tools.py` opens with *"Direct `ensemble_prod` access tools … privileged read/write access to the daemon's own database via the shared `InstanceManager.engine`"*; `daemon/repositories/factory.py:355-366` and `daemon/persistence.py:232-247` build the engine/DSN from `POSTGRES_*` |
| Role `ensemble` is the **OWNER** of `public.dump_lane_artifacts` | live registry query, 2026-11-26 |
| Role `ensemble_prod_ro` can SELECT the **VIEW** but **NOT the TABLE** | live: `has_table_privilege(…,'SELECT')` = false; direct `count(*)` raises `InsufficientPrivilegeError` |
| The VIEW **filters `WHERE integrity_ok`** and takes `row_number() OVER (PARTITION BY lane ORDER BY finished_at DESC, artifact DESC)=1` | live view definition |

**Consequence:** the tool lane reads the **TABLE**, not the view, on its **existing pooled engine** — owner rights, no new credential, no RO-role compromise, no `kubectl` (the daemon-side kubectl ban is a literal-string assertion over `daemon/**/*.py`; a psycopg SELECT contains no such string).

**The masking hazard this defeats (the single most important technical point in this document):** because the view filters `WHERE integrity_ok`, a newest artifact with `integrity_ok = false` is **invisible** — the view silently returns the next-older *verified* artifact, and a view-based preflight would **PASS against a stale backup while a broken backup sits above it**. The discovery rule is therefore:

> Select the newest row **OVERALL** regardless of `integrity_ok`, **then** assert `integrity_ok = TRUE` **on that row**.

### Rejected alternatives

| Alternative | Why rejected |
|---|---|
| **A — the shell ceremony** (`scripts/upgrade/lib.sh` / `promote.sh`) | The upgrade scripts have **zero** DB access: `git grep -E 'psql\|PGPASSWORD\|DATABASE_URL\|pg_dump' origin/latest -- scripts/upgrade/` hits **only `boot_smoke.sh`**, which is **not** in `executor_family_paths()` and therefore **not shipped** in the release payload. The executor env strips `POSTGRES_HOST/USER/PASSWORD/PORT` (`upgrade_journal.py:3288-3294`); the `("PG",)` prefix sweep (`:3305`) contributes nothing because the daemon sets no `PG*` vars. A shell leg would need a **new credential path crossing the executor boundary** — forbidden by the house's *"NEVER the daemon's full environment"* doctrine and by the `extra_env`-merge-is-the-only-sanctioned-lane rule. It would also need a shipped helper (the existing probe is repo-only). |
| **C — hybrid** (daemon validates + stamps; shell consumes) | The hybrid's entire justification was *"neither A nor B alone can both read the owner-only table and halt the ceremony with the house exit vocabulary."* **Both halves fail.** B **does** journal a refusal (`_refusal` → `_journal_refusal_event` → `journal_history_append(install_dir,"refusal",…)`) — verified — and B **does** halt the ceremony, because the executor spawn is gated on a durable `pending_op` the refused arm never writes. Exit 78 is the **shell's** vocabulary for halting a ceremony *already in flight*; a daemon refusal halts it *before it starts*, which is strictly earlier and strictly safer. The hybrid buys a **later** halt at the cost of a TOCTOU window and **two extra failure surfaces** (absent-stamp, stale-stamp) — for no capability B lacks. It also adds a second schema the two sides must agree on. |
| **B' — a new daemon-owned predicate module invoked by the shell** (the `promote_staleness.py` shape) | Attractive because `daemon/plugin_subsystem/promote_staleness.py` **is** a shipped daemon-tree module invoked by `lib.sh` *"before integrity, before any mutation"*. But that module is **deliberately offline** — *"Data sources (offline-provable, no daemon, no DB, no git)"*, imports **only stdlib + PyYAML** — and it runs under whatever interpreter the operator's session resolves, with **no `manager.engine` and no credential**. A DB-reading sibling would have to open its own connection with a credential the executor does not have. Rejected on the credential, not on the shape. |

---

## 4. D2 — HOW "THE LATEST BACKUP" IS IDENTIFIED

**Rule: the newest row overall for the lane, by `finished_at DESC, artifact DESC`, then assert `integrity_ok` on that row.**

```sql
SELECT lane, artifact, bytes, finished_at, integrity_ok,
       first_seen_at, last_seen_at
FROM public.dump_lane_artifacts
WHERE lane = :lane
ORDER BY finished_at DESC, artifact DESC
LIMIT 1
```

**Columns read:** `artifact`, `bytes`, `finished_at`, `integrity_ok` (the gates); `first_seen_at`/`last_seen_at` (diagnostics only — **never** a freshness input, per the standing POK rule).

**Identity tuple remembered:** `(lane, artifact, bytes, finished_at)`, plus a `fingerprint_sha256` over the canonicalised tuple. The artifact name embeds its run timestamp, so the tuple changes whenever the artifact changes.

**Robustness rationale:**
- **The view's filter** — defeated by reading the table (§3).
- **Publication lag** — `finished_at` is the artifact's own completion time; ordering by `first_seen_at` would select by the *scanner's* cadence, not the backup's age. Measured lags: 1h37m28s, 3h43m13s, 16h19m54s, 16h56m03s.
- **Name-vs-column conflict** — the artifact name embeds a run timestamp; a mismatch against `finished_at` means the registry row is mis-published → refuse `db-preflight-identity-conflict`.

**No `sha256` exists.** The registry has no checksum column (`git grep dump_lane_artifacts origin/latest` → **zero hits** repo-wide; the only `sha256_hex` column in the schema belongs to `repair_backup_checkpoint_blobs`, LangGraph checkpoint blobs, 0 rows, unrelated). The brief's "path + sha256 + size/mtime" therefore reduces to the tuple above.

**Lane value:** `pgdumpall` (all four live rows). Constant `DB_PREFLIGHT_LANE = "pgdumpall"` — no env override in v1 (the operator adding a lane is a code change; *"simple always better"*).

---

## 5. D4 — WHAT "CHEAP AND MEANINGFUL" MEANS HERE

**Ruling — rung (c):** the lane's own verdict **+** non-empty bytes **+** age within bound **+** the newest-overall artifact being verified. Rung (d) (anything stronger) is **not supportable**: no checksum, no restore probe, no byte access. Any check purporting to verify bytes would be fiction.

**The five assertions the preflight makes (one SELECT):**

| # | Assertion | Fails with |
|---|---|---|
| 1 | a row exists for the lane | `db-preflight-no-backup` |
| 2 | `bytes > 0` | `db-preflight-artifact-empty` |
| 3 | the **newest overall** row has `integrity_ok = TRUE` | `db-preflight-artifact-broken` (names the artifact) |
| 4 | `finished_at` is within the freshness bound | `db-preflight-artifact-stale` (names the artifact + age) |
| 5 | the artifact name's embedded timestamp agrees with `finished_at` | `db-preflight-identity-conflict` |

**What IS proven:** the lane published an artifact; the lane's own verifier (a cluster-side `zstd -t`) marked it verified; the artifact is non-empty; it is recent; it is the newest one the lane has published.

**What is NOT proven — and must never be claimed:** the bytes are **never re-read**, checksummed, or restored; a corrupted or truncated artifact that the publisher marked verified **will pass**; `integrity_ok` is a **self-reported external verdict** (the publisher is cluster-side, the verifier's implementation is not inspectable from the DB or the repo).

**Corruption detection is delegated, not performed.** R2's pin is satisfied honestly as: *"if the publisher's own verifier marks the newest artifact `integrity_ok = false`, the preflight fails loudly, naming the artifact."* Detecting corruption the publisher missed is **out of reach by construction** — the bytes are unreachable from the VM, and the only sanctioned channel carries metadata.

**Freshness bound: 48 h (172800 s), measured on `finished_at`.**
- **Mandatory** per the standing POK rule `backup-freshness-on-finished-at-not-seen-at`.
- **Arithmetic.** The visible artifact's age sawtooths: a healthy daily 17:00Z lane publishes with a lag of up to ~17 h, so the *visible* newest artifact's age rises to ≈ (24 h slot + 17 h measured lag) ≈ **41 h** before the next publish resets it. At DB-now (2026-11-26T04:06:08Z) the newest visible artifact was 6h13m12s old.

  | Promote time | Visible newest | Age | 36 h | **48 h** |
  |---|---|---|---|---|
  | 11-27 09:00Z | 11-25 21:52 | 35h07m | PASS (by 53 min) | PASS |
  | 11-27 17:30Z | 11-25 21:52 | 43h37m | **FAIL** | PASS |
  | 11-28 09:00Z | 11-26 17:05 | 39h55m | **FAIL** | PASS |

  A 36 h bound would block **normal morning promotes** whenever the scanner lag runs long — the ceremony would be blocked by the *observability path*, not by an absent backup, which is the opposite of the Emperor's intent.
- **Still catches the real hazard.** The lane's ENOSPC era left ~2-month-old artifacts; under a 48 h bound those fail by a wide margin.
- **A structural caveat, stated plainly:** one bound cannot express both the **data-loss ceiling** and the **spurious-block ceiling** — they are different quantities, and the registry can only expose the *visible* age (which is ≥ the true age). A tight 24 h data-loss bound is therefore **not implementable** against a lagging publisher without a publisher-side fix or a schema change. The bound is an Emperor-level risk-appetite decision (§16, Q1).

**Skip / bound interaction — the trap, closed:** the freshness bound is evaluated on **every** invocation, **unconditionally**. The identity-skip (R4) may short-circuit only the *immutable* checks (integrity, byte count, name consistency); it may **never** short-circuit the age check. An unchanged artifact that has aged past the bound **fails** — a skipped step that never re-checks freshness would let a backup age forever behind a skip.

**No separate publisher-liveness check.** It is dropped deliberately: the recon measured `last_seen_at` as a **bulk re-touch of all four rows at a single instant**, so it is not a per-artifact liveness signal; the POK rule forbids using it for freshness; and the freshness bound **already** catches a dead scanner (the visible age keeps growing). Adding it would create spurious refusals for no added cover.

---

## 6. D5 — WHAT STALENESS DOES

**Ruling: staleness FAILS the promote, loudly.** Not warn-and-continue: the daily backup is the thing covering the risk the per-promote dump used to cover, and a stale backup provides no such cover. Not a third outcome: the bound is one gate with one action.

**The stale condition is *not* "no backup".** The two are distinguished by **reason token**, because the remedy differs (wait for the publisher vs. fix the lane). Under the daemon seam there is **no exit code** — a refusal is a returned `Error:` string plus a journaled `refusal` event plus an SSE alert (`ALERT_KIND_BY_EVENT["refusal"] = "upgrade_promote_refusal"`). The house's `0/75/76/78` exit vocabulary belongs to `scripts/audit/dump_lane_staleness.py`, a **repo-only** probe in a different vocabulary, and is **not** imported here. `exit 76` / `exit 77` / `rc=76` / `rc=77` have **zero** hits in `scripts/upgrade/` — **no new exit code is claimed.**

**Full condition → action table:**

| Condition | Action | Reason token |
|---|---|---|
| Registry relation absent / unreadable / query failed | REFUSE | `db-preflight-registry-unavailable` |
| Zero rows for the lane | REFUSE | `db-preflight-no-backup` |
| Newest overall artifact has `integrity_ok = false` | REFUSE (names the artifact) | `db-preflight-artifact-broken` |
| Newest artifact `bytes <= 0` | REFUSE (names the artifact) | `db-preflight-artifact-empty` |
| `finished_at` older than the bound | REFUSE (names the artifact + age + bound) | `db-preflight-artifact-stale` |
| Artifact name timestamp ≠ `finished_at` | REFUSE | `db-preflight-identity-conflict` |
| Journal absent / empty / unparseable (torn) | REFUSE | `db-preflight-journal-torn` |
| Opt-in `full-dump` requested | REFUSE (never a silent fallback) | `db-preflight-full-dump-unavailable` |
| Opt-in value not recognised | REFUSE | `db-preflight-mode-unknown` |
| Identity unchanged **and** fresh | **SKIP** — loud log + history row + stamp advance | — |

**Ceremony consequence, stated plainly.** With a 48 h bound the change **does not block a normal promote** in the near term: at DB-now the newest visible artifact was 6h13m old; a promote on 2026-11-27 09:00Z sees ~35 h; and the lane is currently green (POK `pg-host-clockwork-landscape-01`, corrected 2026-11-25: *"pgdumpall 17:00Z daily GREEN again (4/4 dumps; PVC 20→60Gi; retention DISABLED so it re-fills ~0.9GiB/d)"*). **Two** consecutive missed dailies would block — and that is the intended behaviour, because two missed dailies mean the safety net genuinely is absent.

---

## 7. D3 — WHERE THE REMEMBERED IDENTITY LIVES

**Ruling: a new top-level key `db_preflight` in `releases/state.json`.**

**Exact path:** `<install_dir>/releases/state.json` — `journal_path(install_dir) = install_dir / "releases" / "state.json"` (`daemon/tools/upgrade_journal.py:267-268`).
**Install-root resolution:** the upgrade lane's `_canonical_install_root(install_name)` (`daemon/tools/upgrade_tools.py:425-439`) — the first candidate carrying a `.env` file or a `releases/` directory. **Never** `sys.executable.parent` (that is the release dir, evicted by keep-3 retention) — standing POK rule `tool-usage-03-install-root-not-release-dir`.

**Exact fields** (one dict, current-value slot):

```json
"db_preflight": {
  "lane":         "pgdumpall",
  "artifact":     "pg_dumpall-2026-11-25-21-46.pgdump.zst",
  "bytes":        921187559,
  "finished_at":  "2026-11-25T21:52:56Z",
  "fingerprint_sha256": "<sha256 over the canonicalised tuple>",
  "last_checked_at":    "<ISO-8601 Z>",
  "verdict":      "validated" | "skipped"
}
```

`fingerprint_sha256 = sha256(json.dumps([lane, artifact, bytes, finished_at], separators=(",",":"), sort_keys=True))`.

**`bytes` IS in the fingerprint.** A re-publish of the same artifact name with a different byte count is a **different** artifact — it must **defeat** the skip, because letting it through would validate a changed artifact under a remembered identity. That is precisely the corruption case R2 cares about.

**Exact write mechanism:**
1. Introduce the key in `ensure_extensions` (`upgrade_journal.py:769+`) — one line mirroring `pending_wakes` at `:800-801`.
2. A new helper `record_db_preflight(install_dir, record)` modelled on `arm_pending_wake` (`:1825-1852`): `data = ensure_extensions(install_dir)` → mutate → `journal_write(install_dir, data)`. It is **self-seeding**, so it does not depend on the post-nonce-burn `ensure_extensions` call at `:4462`.
3. **Called under the caller-held journal lock.** `arm_pending_wake` documents the contract verbatim: *"Must be called INSIDE the caller-acquired journal lock … `journal_write` itself has no internal lock (architecture delta #3 — atomicity is BY the caller's lock, not the journal's)."* The new check holds the lock (`:4046`), so the write is serialized against concurrent promotes.

**Atomicity:** `journal_write` = temp file (pid + thread id + uuid4 hex + ms) + `fsync` + `os.replace` (`:691-729`). **ADR-034 structural round-trip**: *"every existing field (including unknown/hand-edited extras) is carried through … No schema filtering, no occurrence assertions."*

**Torn-journal contract:** `journal_read` raises `JournalTorn` for absent / empty / unparseable / non-dict — *"callers treat torn as halt-for-human, never trust, never write over it."* The new helper must **propagate** it and the check must **refuse** (`db-preflight-journal-torn`).

**The house precedent, in the house's own words.** `ensure_extensions`'s docstring: *"New top-level keys are invisible to lib.sh's field-targeted splice (ADR-034), so the shell lane is unaffected."* Six such keys already exist — `pending_op`, `pending_restart`, `pending_actions`, `pending_wakes`, `restart_audits`, `restart_window` — and the docstring cites *"agent-restart §6.2 (decision A — REUSE, no new table, no migration)"*. `db_preflight` is the seventh, and idiomatic.

**Loud skip (R4):** current state in the key, audit in `history` — `journal_history_append(install_dir, "db_preflight_skipped", detail)` on a skip, `"db_preflight_validated"` on a fresh validation. The detail **names the artifact**. On a skip the tuple and fingerprint are left untouched; **only `last_checked_at` and `verdict` change** — so the remembered *validated* identity survives skips.

### Rejected alternatives

| Alternative | Why rejected |
|---|---|
| **Separate durable file** in the install tree | Admissible runner-up (best on byte-parity risk), but it needs a **new** atomic-write helper and a **new** concurrency discipline, and it **splits state from audit** across two homes — the audit must live in the journal's `history` anyway. The close precedent (AM-P2-5, `promote-handback-halt/phase2-plan.md:481,556,942`) re-homed a counter to a launcher-local file, but that reader/writer is the **launcher** (self-contained, no python3, sweeps before the daemon exists, cannot create a journal field). Our reader/writer is the daemon tool lane — the launcher-specific objections do not apply, and the "no new journal field" conclusion there was a *consequence* of the file home, not its reason. |
| **New database table** (+ migration) | **Two fatal problems.** (1) The upgrade lane **owns no DB table**: its state is file-backed JSON in `releases/state.json` (`PendingAction` → `pending_actions`, `PendingOp` → `pending_op`; no `restart_window` model exists) — the house's own `instance_ops_audit` migration documents *"the upgrade lane's `restart_audits` is file-backed JSON inside `releases/state.json`"*. (2) The preflight must not write to **the very database the promote is changing**, and an old binary after a rollback knows nothing about the table. A migration also costs an ordinal bump and a `rollback_safe` adjudication for one small record. |
| **The journal's `history` array, scanned backwards** | **Scan cost on the promote critical path**: measured `_js_journal_read` 13.5 s + `_js_json_sub history` 35.7 s on a 27 KB / 157-entry journal (≈285× at the 456 KB incident size), plus the cross-entry false-positive class. An O(N) scan on every promote for a value a current-value slot gives O(1) is not a trade the house should make. |

---

## 8. D6 — THE R6 OPT-IN

### Ruling: **the opt-in cannot mean "dump from the ceremony".** It ships as an **explicit, loud refusal**.

**A VM-side full dump is impossible on five independent grounds** (each independently sufficient):
1. **No dump leg exists anywhere.** `git log --all -S'pg_dump' -- scripts/upgrade/` → **0 commits**; the 18 whole-repo hits classify as design prose (13), tester transcripts (2), read-only probe (2), inert display string (1) — class *REAL DUMP LEG = NONE*. The only trace is the **inert** dry-run PLAN string (`upgrade_tools.py:3759-3761`), which no test pins (tests pin only `"UPGRADE PREFLIGHT (dry-run)"` and `"NO mutation happened"`).
2. **The artifact bytes are unreadable from the VM** — PVC not mounted; daemon-side `kubectl` forbidden by two assertions in `tests/unit/test_checkpoint_lane_docs_pins.py` (`:65-68` the doc statement, `:71-85` the `daemon/**/*.py` walk).
3. **The promote child has no DB credential path** — only `POSTGRES_DB` (the *name*) crosses `EXECUTOR_ENV_ALLOWLIST` (`upgrade_journal.py:3288-3294`); `POSTGRES_HOST/USER/PASSWORD/PORT` are stripped; the `("PG",)` prefix sweep (`:3305`) contributes nothing because the daemon sets no `PG*` vars.
4. **New connections are refused at 99.7 % of the PG container's 4 GiB cgroup** (established ones survive).
5. **The VM's `pg_dump` is 16.15 and aborts against the 18.4 server.**

**Therefore:** `full-dump` is **recognised and refused loudly** with `db-preflight-full-dump-unavailable` and a remediation naming the cluster-side path. It **never** silently degrades to validation-only. An unrecognised value is refused (`db-preflight-mode-unknown`) rather than treated as validation-only — a typo must not silently downgrade the mode.

### The transport (revised in v2)

**The opt-in never crosses the executor boundary — and it cannot be carried by `os.environ` either.** The daemon's environment is fixed at boot:

- `scripts/systemd/ensemble-daemon.service:20-26` — the **zero-env contract**: *"every unit generated from it carry NO .env-derived `Environment=` lines"*; the single static line is `Environment=MALLOC_ARENA_MAX=2` (`:121`). The operator cannot add an `Environment=` line to the running daemon.
- `launcher.sh:4599` — `load_env_file "$INSTALL_DIR/.env"` (env-first, last-wins) mints the runtime env at spawn time.
- `daemon/config.py:7946-7948` — *"The boot path … reads `os.environ` ONCE and threads the string through."*

⇒ **an `os.environ` variable is a boot-time setting, not a per-promote surface.** A `.env` edit would apply to the *next* daemon, not to the one performing this preflight.

**The per-promote surface is a tool-call parameter.** The tool's own signature is `async def system_upgrade(target_env, version=None, user_confirmed=False, dry_run=True, nonce=None)` (`upgrade_tools.py:3610-3617`); it gains `db_preflight: str | None = None`. It is per-call, reaches the daemon directly, needs no restart, and crosses **no** executor boundary.

**Consequently:** **no** `EXECUTOR_ENV_ALLOWLIST` entry, **no** `EXECUTOR_ENV_PREFIXES` change, **no** `_verified_arm_extras` entry, **no** `promote.sh` `flag)` case label, and **no** `argv_ext_probe` membership. `argv_ext_probe` (`upgrade_tools.py:4146-4150`) is fed **only** from `uj._verified_arm_extras(probe_op)` when `uj.is_verified_arm(probe_op)` holds, and `_verified_arm_extras` (`upgrade_journal.py:2341-2352`) currently returns just `(["--f2-verified-closed"], {…})` — so **Check 3 (`:4179-4200`) is unaffected**.

**The value is bound to the human's attestation.** It is captured into the `PendingOp` at arm time (`upgrade_tools.py:4463-4478`, alongside `nonce_consumed` / `confirmed_source`) so the journal records what was requested and an agent cannot later claim a mode the human did not confirm. When the value is non-default it must be **surfaced in the dry-run / confirmation text** — the branch that mints the live nonce (`:3764-3773`) — so the human reads what they are attesting.

**Why not `ENSEMBLE_PROMOTE_DB_PREFLIGHT` as an env var.** R6 names it as an *example* ("e.g."). On this deployment it would be a **sticky boot-time trap** applying to every future promote, changeable only by a daemon restart, with the only current behaviour being a refusal. One surface, per-promote: the tool-call parameter. **If the Emperor authorises the cluster-side Job**, the transport is re-ruled then, using the house's own split — env for a human at a shell, attested argv for the daemon-armed path (`PROMOTE_STRICT_STALENESS=1` vs `--block-on-stale`, `lib.sh:4161-4178` / `promote.sh:95-117`).

**The honest answer to "does the opt-in still have a coherent VM-side meaning?"** — **No, not as a capability. Yes, as an explicit refusal.** The opt-in's only coherent VM-side behaviour today is to say *"you asked for a full dump; this host cannot perform one; refusing rather than silently validating."* That is strictly better than not shipping it (an operator who asked would otherwise get an unannounced validation-only promote), and it honours R6's requirement that the opt-in be **reachable** and **honoured** rather than ignored.

**The real dump capability is CLUSTER-SIDE and is the Emperor's decision, not this lane's.** The devops addendum's framing (already filed): a one-shot Job in `postgres18` running the same `pg_dumpall → zstd → zstd -t` path, writing a **named** `.zst` to the dump PVC and publishing a row to the same `public.dump_lane_artifacts` relation. Its failure modes are the Emperor's to own: the PVC **ENOSPC race** (which needs the prune/expand first), the re-introduction of a per-promote dump the ruling removed (so it must stay opt-in), a partial write never passing (both the `zstd -t` verdict **and** the registry row are required), and its own cluster credentials. **This lane proposes no PG or k8s change** — the memory re-tune and the PVC prune/expand are the Emperor's, and the dump lane's command / destination / retention belong to the in-flight mapping job `ca665904`.

---

## 9. F1 / F2 — THE TWO QUESTIONS I WAS ASKED TO RULE

### F1 — the direct-operator gap: **ARM-PATH ONLY, disclosed.**

If the validation lives only in the tool arm path, an operator who runs `bash scripts/upgrade/promote.sh live` **directly** — with the manual `ENSEMBLE_UPGRADE_LIVE=1` that the script's own help text offers (`lib.sh:1453-1466` `require_live_guard`) — receives **no backup validation at all**. The shell-side alternative is impossible on the five grounds of §8.

**Ruling: arm-path only.** The consequence is stated plainly and must appear in the disclosure: **the backup-validation guarantee attaches to the daemon-armed ceremony; a deliberate direct shell invocation bypasses it.** Every daemon-armed promote is covered — the sole writer of a fresh promote `pending_op` is `system_upgrade` (`upgrade_tools.py:4478`); the other `write_pending_op` sites are `manager.py:7012`/`:7141` (post-arm owner/unit-identity refinements) and `upgrade_tools.py:3378`/`:3443` (the `system_restart` lane, `kind="restart"`). A manual invocation is an **out-of-band operator override**, exactly like the plugin-staleness `OVERRIDE`.

*The available alternative, if the Emperor wants the gap closed:* a **shell-side belt** — `promote.sh` refuses when the journal carries no `db_preflight` record. It touches `promote.sh` (the file R7 wants untouched) and buys only a **weak** guarantee (that *some* validation happened at some point, not that the current artifact is fresh). Named as an option; not built.

### F2 — the TOCTOU: **DO NOT BUILD THE STAMP-AND-COMPARE BELT.**

The arm→executor window is seconds-to-minutes wide; the property it would re-check (artifact freshness) has an **hour-scale** bound. A newer artifact could publish in that window (making the validated one not-quite-newest) — harmless, since the bound is hours. The registry could flip `integrity_ok` — vanishingly unlikely in seconds, and the verdict is about a completed artifact.

Building the belt would require: a **new shell-side comparison**, a **new refusal path in `promote.sh`**, and a second schema the two sides must agree on — a second failure surface and a change to the very file whose preflight R7 wants untouched. The house precedent does the opposite: `promote_plugin_staleness_check` evaluates its predicate **once** at preflight and does not re-validate. **"Simple always better"** (cn 2509c6d5) settles it.

**Ruling: validate at arm time, accept the seconds-wide window, and DISCLOSE it.** The audit trail is carried by the journal-always history row (`db_preflight_validated` / `db_preflight_skipped`), written in the same shape as the sibling `arm-scripts-provenance` row.

---

## 10. THE IMPLEMENTATION SURFACE (exact paths, keys, tokens, SQL)

**New module:** `daemon/tools/upgrade_db_preflight.py` — the registry read + the verdict logic, in its own module so the 265 KB `upgrade_tools.py` arm block does not grow and the verdict logic is unit-testable without the whole tool-lane harness. This mirrors the house's own shape (`daemon/plugin_subsystem/promote_staleness.py`: a standalone predicate module with its own contract and its own unit pack) — **without** its offline constraint, because this module runs inside the daemon and uses `manager.engine`.

**Files touched (product code — the planner/implementer owns these; this lane does not):**

| File | Change |
|---|---|
| `daemon/tools/upgrade_db_preflight.py` | **new** — the registry read, the five gates, the verdict, the skip predicate |
| `daemon/tools/upgrade_tools.py` | new arm-preflight check at `:4377-4380`; the dry-run PLAN string at `:3759-3761` corrected to match reality; **the `system_upgrade` signature gains `db_preflight: str \| None = None`** (`:3610-3617`); a non-default value is **surfaced in the dry-run / confirmation text**; the value is captured into the `PendingOp` at arm time (`:4463-4478`) |
| `daemon/tools/upgrade_journal.py` | `ensure_extensions` seeds `db_preflight`; new `record_db_preflight()` helper modelled on `arm_pending_wake` |
| `tests/unit/test_upgrade_db_preflight.py` | **new** — hermetic unit nodes (§11) |
| `PACKS.md` | **new** row — the ownership chain (cn 2039b459): task → artifact → File cell → leg row → PACKS.md row |

**The registry read:** via the daemon's existing **pooled** engine (`manager.engine` — the ~16-repository pool). The read is a **sync** engine call inside an async tool → wrap so it does not block the event loop, and **reuse the pooled connection** — a *new* connection may be refused at the 99.7 % cgroup ceiling, an established one survives.

**SQL:** the statement in §4, parameterised (`:lane` bound, never interpolated).

**Constants:**
- `DB_PREFLIGHT_LANE = "pgdumpall"`
- `DB_PREFLIGHT_MAX_AGE_S = 172800` (48 h) — env-overridable via `ENSEMBLE_PROMOTE_DB_PREFLIGHT_MAX_AGE_S` (a risk-appetite knob the Emperor may want to tune without a release; the house precedent is the env-overridable `JOURNAL_SIZE_GUARD_BYTES`).
- `DB_PREFLIGHT_MODES = {"validation-only", "full-dump"}` — anything else is refused as `db-preflight-mode-unknown`.

**The opt-in, end to end:**
1. The operator (or the agent relaying the instruction) passes `db_preflight="full-dump"` on the `system_upgrade` call.
2. The arm check reads it; `"full-dump"` ⇒ refuse `db-preflight-full-dump-unavailable` (never a silent fallback); absent ⇒ validation-only.
3. The value is surfaced in the dry-run / confirmation text so the human sees what is being attested.
4. The value is written into the `PendingOp` so the journal binds it to the human's confirmation.

**Reason tokens** (house kebab-case, one greppable family): `db-preflight-registry-unavailable`, `db-preflight-no-backup`, `db-preflight-artifact-broken`, `db-preflight-artifact-empty`, `db-preflight-artifact-stale`, `db-preflight-identity-conflict`, `db-preflight-journal-torn`, `db-preflight-full-dump-unavailable`, `db-preflight-mode-unknown`.

**History rows:** `db_preflight_validated`, `db_preflight_skipped` — both naming the artifact.

---

## 11. THE PIN DESIGN (a)–(f) AGAINST R1–R8

| Pin | Asserts | Requirements covered |
|---|---|---|
| **(a) the masking pin** | A registry whose **newest overall** artifact has `integrity_ok = false`, while an **older** artifact is verified ⇒ the check **REFUSES** with `db-preflight-artifact-broken`, naming the newest artifact. This is the pin that proves the view-masking hazard is defeated — and it is **mutation-proven**: reintroduce a view-based read and it goes RED. | R1, R2 |
| **(b) the skip + stamp-advance pin** | Two consecutive invocations with an unchanged, fresh artifact ⇒ the second records `verdict="skipped"`, `last_checked_at` **advances**, and a `db_preflight_skipped` history row **naming the artifact** is appended. **Third case:** the same unchanged artifact aged past the bound ⇒ the skip does **not** fire and the check refuses `db-preflight-artifact-stale`. | R4 |
| **(c) the loud-failure pin** | Zero rows ⇒ `db-preflight-no-backup`; registry relation absent ⇒ `db-preflight-registry-unavailable`; torn/absent journal ⇒ `db-preflight-journal-torn`. All three assert **no** `pending_op` is written and the **nonce is unspent** — a refusal is never a silent proceed. | R5, R7 |
| **(d) the opt-in pin** | `full-dump` ⇒ `db-preflight-full-dump-unavailable`; an unrecognised value ⇒ `db-preflight-mode-unknown`; the parameter absent ⇒ validation-only proceeds. **Plus two boundary pins:** (i) the opt-in never crosses the executor boundary — asserting `ENSEMBLE_PROMOTE_DB_PREFLIGHT` ∉ `EXECUTOR_ENV_ALLOWLIST` and ∉ the `EXECUTOR_ENV_PREFIXES` match, mirroring the house's existing T0c pin shape (`tests/unit/tools/test_agent_restart.py:188-193` asserts `ENSEMBLE_RESTART_LIVE`/`ENSEMBLE_UPGRADE_LIVE` are absent from the allowlist); and (ii) the opt-in is **nonce-bound** — the value appears in the `PendingOp` written at arm time, so the journal records the requested mode alongside the human's attestation. | R6, R8 |
| **(e) the transaction pin** | (1) `scripts/upgrade/promote.sh` and `scripts/upgrade/lib.sh` are **byte-unchanged** by this lane (a path-scoped diff pin against the base commit). (2) A refusal at the new check leaves the **nonce unspent** and **no `pending_op`** written — so the arm "simply did not happen". (3) The check runs **after** the lock acquire and **before** the nonce burn (ordering asserted structurally). | R7 |
| **(f) the durability pin** | The record round-trips a simulated restart (a fresh process reads the journal and the skip fires); `ensure_extensions` seeds the key so the journal shape is stable; ADR-034's structural round-trip holds (an unrelated extra key survives). | R3 |

**R8 (C-1 class discipline):** satisfied **trivially** — the lane adds **no shell code**, so there is no new surface for the continuation-comment defect (`tests/shell_continuation_scan.py`; CLI `0` clean / `1` new hit / `2` usage). If a future revision adds a shell leg, the lint and the `set -u` unbound-argument rule apply to it (`${VAR:-default}`, never bare `$VAR`), and `lib.sh`'s Bash 3.2 / BSD-only / no-`flock(1)` / no-`set`-line constraints apply.

---

## 12. THE PRESERVE-LIST (what the transaction keeps)

The shell preflight runs **entirely after the lock and entirely before the txn opens** (`promote.sh:211-655`; `lock_acquire` at `:236-239`; `journal_open_txn` at `:660-671`; the resume branch's `journal_resume_txn` immediately above it). **Every item below is untouched by this lane.**

| Guarantee | Where it lives today |
|---|---|
| Lock acquisition / release | `lib.sh:2951` `lock_acquire` (mkdir-acquire; owner/run_id/heartbeat; stale-break only if heartbeat > `LOCK_STALE_S=300` **and** the owner is dead; bounded wait `LOCK_WAIT_S_DEFAULT=15`) · `:3087` `lock_heartbeat` · `:3103-3117` `lock_release` (owner-guarded) |
| Live / demo guards | `lib.sh:1453` `require_live_guard` · `:1477` `require_demo_guard` (`promote.sh:129-130`, exit 78) |
| F2 attestation gate | `promote.sh:141-144` — `_refuse f2-not-verified` |
| Input validation | `promote.sh:147-150` (VERSION) · `:195-199` (SOAK_S; journal-less by design) |
| Darwin self-detach | `promote.sh:176-187` `_executor_detach_self` — **before** any lock/txn/signal-trap mutation |
| Stop-script sibling | `lib.sh:5592` `_upgrade_stop_script_sibling_usable` (`promote.sh:217-223`, exit 78 **before** any lock) |
| Watchdog / deadline harness | `promote.sh:273-285` (structural `declare -F` of `_budget_breach` / `_run_bounded_step` / `_enforce_deadline`) · `:562-603` L1 · `:699-712` → `lib.sh:5219-5253` L2 · `:656-668` `_budget_breach` (`BUDGET_BREACH_RC=78`) |
| python3 floor | `promote.sh:343-352` `_splice_python_floor_probe` |
| Journal read | `promote.sh:386-404` — `JOURNAL_READ_STRICT=1`, `JOURNAL_SIZE_GUARD_BYTES=98304` (`lib.sh:1587`); torn ⇒ halt row + exit 78 |
| Resume decision | `promote.sh:432-458` `_promote_resume_decision` · `:476` `adopt_stale_txn` (`lib.sh:11050`); `kind=restart` is never adopted ⇒ exit 78 |
| **Entry checks** | `promote.sh:496` → `lib.sh:10955-11048` `promote_entry_check` — cap / cooldown / quarantine / adoption-in-progress, all exit 78 |
| Supervision preflight | `promote.sh:503` → `lib.sh:9211-9268` `supervision_preflight` + `supervision_executor_cgroup_selfcheck` + `lib.sh:8997` `supervision_dualfight_check` |
| Watchdog probe | `promote.sh:520-530` `_watchdog_probe` → halt `watchdog_probe_unreadable` + release + exit 78 |
| Plugin staleness / config-compat | `lib.sh:4236` / `:4053` (`promote.sh:553`, `:561`) |
| **Integrity / manifest** | `lib.sh:4642` `integrity_verify` — CURRENT (`:592`), TARGET (`:604`), layout divergence (`:585-588`), `binary_version` (`:611-615`) |
| Intent evidence | `promote.sh:648-650` `journal_history_append` (fresh mode only, best-effort) |
| **Journal txn open / close** | `promote.sh:660` `journal_open_txn` (`lib.sh:1993-2016`; refuses if `in_flight` exists; captures `displaced_version`; writes `in_flight`) · `:673` `journal_resume_txn` (`lib.sh:11431`) · `:683-685` `journal_mark_f2_verified` (`lib.sh:2037-2079`; refuses to stamp when `in_flight` is null, journal untouched) · `:691` `journal_mark_supervision` (advisory) · close `lib.sh:2381-2383` `journal_close_txn` |
| Write atomicity | `lib.sh:1774-1803` `journal_update` → `:1752-1771` `journal_write` (tmp + `mv -f`) · path `lib.sh:1537` `journal_path` = `$INSTALL_DIR/releases/state.json` |
| **Refusal helper** | `lib.sh:10932-10951` `_refuse` — requires a non-empty token **and** message; `_warn "$* (reason=$reason)"` to stderr; `journal_history_append refusal` best-effort; `exit 78` |
| Constants | `PROMOTE_TOTAL_DEADLINE_S=900` (`:174`), `PREFLIGHT_STEP_BUDGET_S=120` (`:291`), `PREFLIGHT_PHASE_BUDGET_S=300` (`:292`), `ROLLBACK_CAP_24H=3` (`:369`), `SWEEP_STALE_S=600` (`:371`), `LOCK_STALE_S=300` (`:375`), `HEARTBEAT_STALE_S=300` (`:409`), `RETENTION_KEEP=3` (`:412`) |

**The daemon-side arm path the new check joins** (`daemon/tools/upgrade_tools.py`): `_actor_env_gate` (`:3622`) → `reconcile_pending_op` (`:3634`, never gates) → `_journal_read` (`:3640`) → target resolution (`:3658`) → `_target_release_state` (`:3696`) → `_rollback_count_24h` (`:3715`) → dry-run branch (`:3738`) → **3-factor live gate** (`:3795-3956`: user-confirmed `:3799-3803`, user-origin window `:3804-3852`, nonce chain `:3854-3953` with a 60-min TTL) → orphaned-txn reclaim / busy (`:3960`) → cooldown (`:3997`) → pre-lock busy (`:4004`) → `_resolve_scripts_dir` (`:4010`) → `promote.sh` present (`:4023`) → **lock acquire** (`:4041-4052`, `journal_lock_acquire` `:4046`) → **ARM PREFLIGHT BLOCK** (`:4053-4072`) → Check 1b stop-script (`:4086`) → Check 2 argv construction (`:4123`) → Check 3 flag/case-label (`:4179`) → Check 4 scripts provenance (`:4218`) → **← THE NEW CHECK (`:4377-4380`)** → Step 0c journalctl capture (`:4388`) → Step 0d arm-time rollback-target invariant (`:4432`) → **nonce burn + durable writes** (`:4456-4479`) → `_set_execution_marker` (`:4492`).

---

## 13. TRADE-OFF MATRIX (the recommended architecture)

| Axis | Rating | One-line justification |
|---|---|---|
| **Complexity** | **Low** | One SELECT, one module, one journal key, one helper, one tool parameter; every atomicity/lock/audit mechanism is reused, none is reinvented. |
| **Scalability** | **High** | One bounded metadata SELECT per promote on a pooled connection; O(1) journal read; no byte movement, no restore, no cluster-side work. |
| **Maintainability** | **High** | The shell ceremony is untouched; the concept has one home; `ensure_extensions` + `pending_wakes` is a worked template; the reason tokens are one greppable family. |
| **Risk** | **Low–Medium** | The residual risk is **external ownership**: the registry relation is owned by a cluster-side publisher with **no repo contract** and was **absent as recently as 2026-11-25**. Mitigated by fail-loud naming, not by optimism. |
| **Cost** | **Low** | Zero new infrastructure, zero new migrations, zero new files in the release payload, one sub-millisecond query. |

**D1 approach comparison (the three competitive-fan-out results):**

| Approach | Complexity | Scalability | Maintainability | Risk | Cost | Recommendation |
|---|---|---|---|---|---|---|
| **A — shell ceremony** | High (new credential path + a shipped helper) | Low (no DB access; byte limits) | Medium (touches `lib.sh`) | **High** (credential crossing the executor boundary; ADR-009 D3 conflict) | Medium | **Rejected** — impossible on five grounds |
| **B — daemon tool lane** | **Low** | **High** | **High** | Low–Medium | **Low** | **RECOMMENDED** |
| **C — hybrid** | Medium–High (two schemas, two failure surfaces) | High | Medium (two sides must agree) | Medium–High (TOCTOU; absent/stale stamp) | Low | Rejected — buys a *later* halt for no new capability |

---

## 14. RISKS

🔴 **The registry relation is externally owned with no repo contract.** `git grep dump_lane_artifacts origin/latest` → **zero hits**; the only `sha256_hex` in the schema is an unrelated checkpoint-blob column. `CHANGELOG.md` v0.31.54 R1 records the probe hitting `relation "public.dump_lane_heartbeat" does not exist` — **the relation was absent as recently as 2026-11-25**, the same day the lane went green. A publisher-side schema change or a relation drop **blocks every promote**. *Mitigation:* fail loud with a named token (`db-preflight-registry-unavailable`); absence is never green. *Residual:* the blast radius of an external contract is the Emperor's to accept.

🔴 **The guarantee is metadata-only.** The preflight validates **the lane's own verdict**, not the artifact's bytes (§5, §15). A publisher whose `zstd -t` is buggy or whose registry row is written before the artifact is durable would pass a broken backup.

🟡 **The direct-operator bypass (F1).** A deliberate `ENSEMBLE_UPGRADE_LIVE=1 bash promote.sh live` bypasses the validation entirely. Disclosed, not mitigated by default.

🟡 **The publication lag creates a residual window.** A backup that finished but is not yet published is invisible; the bound (48 h) upper-bounds the true data-loss window (true age ≤ visible age), but the ceremony can validate a *previous* artifact. Disclosed.

🟡 **The registry DB sits at 99.7 % of its 4 GiB cgroup.** A *new* connection may be refused where an established one survives ⇒ the read must reuse the pooled connection, and a refusal here blocks promotes.

🟢 **The artifact bytes are structurally unreachable from the VM.** Not fixable in-lane; it is the reason the whole design is metadata-based.

🟢 **The lane's PVC re-fill.** POK `pg-host-clockwork-landscape-01` records *"retention DISABLED so it re-fills ~0.9GiB/d"* against a 20→60 Gi PVC — a standing re-fill risk. **Not this lane's**: the dump lane's command / destination / retention belong to the in-flight mapping job `ca665904`, and the prune/expand is the Emperor's.

---

## 15. DISCLOSURE (D8) — the text the final report must carry

> **Backup validation at promote time is a metadata validation, not a byte-level one.** The ceremony reads the dump lane's own registry over the sanctioned PostgreSQL channel and asserts that the **newest artifact row** for lane `pgdumpall` carries the publisher's `integrity_ok = true` (a cluster-side `zstd -t` verdict), a **non-zero** byte count, and a `finished_at` within the **48-hour** freshness bound, measured on the artifact's own completion time — never on `first_seen_at`/`last_seen_at`, which measure the scanner's cadence rather than the backup's age. **The bytes are never re-read, checksummed, or restored by the ceremony**; a corrupted or truncated artifact that the publisher marked verified **will pass**. The registry publishes **no checksum**, so the remembered identity is `(lane, artifact, bytes, finished_at)` plus a fingerprint over that tuple.
>
> **The live database's dumpability at promote time is no longer verified by the ceremony.** The daily backup covers that risk instead. **A promote may now happen against a database whose last backup is up to ~48 hours old** — a rollback to that backup can lose that much data. Staleness **fails the promote loudly**; it is never a warning.
>
> **Two lanes exist.** The registry lane (`pgdumpall`, compressed `.pgdump.zst`, `zstd -t`-verified, green since 2026-11-25) is **working**. The legacy PVC lane named in the original brief (uncompressed `pg_dumpall --clean`, 20 Gi, ENOSPC-failing since ~Sep 1) has since been **expanded to 60 Gi and recovered** — the brief's description of it is **out of date**. The ceremony validates the **registry lane**.
>
> **The registry is externally owned.** The relation has **no repo contract** (zero references in the tree), no migration, and no publisher-side implementation in the repo — it was **absent on 2026-11-25**. Its schema and its `integrity_ok` semantics are the cluster publisher's. **Its absence fails the preflight loudly; it is never green.**
>
> **The guarantee attaches to the daemon-armed ceremony.** A deliberate direct `ENSEMBLE_UPGRADE_LIVE=1 bash scripts/upgrade/promote.sh live` invocation bypasses the validation entirely — the shell has no DB credential path, and a VM-side dump is impossible on five independent grounds (no dump leg exists in any commit; the PVC is not mounted; the promote child has no credential path; new connections are refused at the 99.7 % cgroup ceiling; the VM's `pg_dump` 16.15 aborts against the 18.4 server). The `full-dump` opt-in is therefore shipped as an **explicit refusal** (`db-preflight-full-dump-unavailable`), never a silent fallback. The only real dump capability is **cluster-side** and is the Emperor's decision.
>
> **A seconds-wide window** exists between the arm-time validation and the executor run. It is accepted and disclosed: the property re-checked (artifact freshness) has an hour-scale bound, and the shell's preflight is **not** modified to close it.

---

## 16. OPEN QUESTIONS THAT GENUINELY NEED THE EMPEROR

**Q1 — The freshness bound: 48 h (my ruling) vs 24 h.** 48 h never blocks a healthy daily lane (worst-case visible age ≈ 41 h) and still fails a 2-month-old artifact. A 24 h bound expresses a tighter **data-loss** ceiling but **cannot be implemented** against the measured publication lag without a publisher-side fix or a registry schema change — it would block normal morning promotes on scanner lag alone. **My ruling: 48 h.** If the Emperor wants a 24 h *data-loss* ceiling, the correct fix is upstream (publish promptly, or publish the artifact's own checksum), not a tighter downstream bound.

**Q2 — R6: accept (a) as the honest substitute for R6's literal "full dump stays reachable"?** **My ruling: (a)** — the opt-in is **reachable and recognised** (via the `system_upgrade` `db_preflight` parameter) but the capability is unavailable on this host, so it refuses loudly. The Emperor may instead authorise (b), the cluster-side one-shot dump Job (the devops addendum's framing, already filed) — which is **outside this lane's no-k8s-change fence** and depends on the PVC prune/expand. *The strongest objection to (a), which I concede as a fair reading:* *"the Emperor made validation the **default**; he did not rule a full dump impossible"* — correct, and (a) is the conservative reading. If a real dump leg is later found in the live pinned scripts worktree (`ENSEMBLE_UPGRADE_SCRIPTS_DIR`) or an unmerged branch, `full-dump` could honestly invoke it, and (b) reopens as Emperor-gated.

**Q3 — F1: must the direct-operator path be covered?** The only available mechanism is a shell-side belt in `promote.sh`, which touches the file R7 wants untouched and buys a weak guarantee. **My ruling: arm-path only + disclosure.** If the Emperor wants the gap closed, that is a deliberate R7 trade, not an oversight.

**Leader/operator items (not Emperor-grade):** (a) whether an `artifact_sha256` column should be added to the registry operator-side so the identity carries a real content hash — *my ruling: not required; `bytes` already defeats a re-published artifact, and a published checksum would be metadata, not a byte re-read*; (b) the lane's retention-disabled re-fill, which belongs to `ca665904`.

---

## 17. FAN-IN RECORD AND PROVENANCE

**Worker fan-out (competitive, same skill, different approaches):**

| Instance | Dimension | Result |
|---|---|---|
| `761ed348-2309-41e3-8546-70b6e13f7f5f` | D1 approach **B** — daemon tool lane (`system-decomposition`) | ✅ landed; premise-corrected (app DB **is** `ensemble_prod`, role `ensemble` = table owner) |
| `9053feeb-a70f-40b8-8fec-52db31d26636` | D1 approach **C** — hybrid (`system-decomposition`) | ✅ landed; its central justification refuted on evidence (§3) |
| `03eb97bf-1ee0-480b-aff8-bcdbfdc276e1` | D4/D5 — validation semantics (`resilience-design`) | ✅ landed; the skip/bound trap closed (§5) |
| `502dc25f-a1fd-4efb-b016-39090c3128e9` | D3 — durable state seam (`structural-design`) | ✅ landed; option (i) confirmed (§7) |
| `00eca015-5b97-4a7e-8ebe-48b31f8d1fa3` | D6 — R6 transport (`system-decomposition`) | ✅ landed; **confirmed** option (a) + "Check 3 unaffected" + "no new exit code" (`exit 76`/`77` have zero hits in `scripts/upgrade/`), and **refuted v1's transport** — `os.environ` is boot-time-only ⇒ the opt-in moved to a tool-call parameter (§8, revision history) |
| `47e52e10-426e-4554-8f1c-4f6fee01658c` | D1 approach **A** — shell seam (`system-decomposition`) | ⚠️ **terminated** — wedged on a single in-flight `bash` call for **~54 min** (last tool emission 05:10:42Z; diagnosed 06:04:51Z); its brief was oversized (32+ messages of grinding reads). **Re-dispatched once** as `43f6d961` (high tier, narrow brief, ≤60-line target) per the escape valve's **max-1 re-dispatch** rule. |

**⚠️ EXPLICIT PARTIAL FAN-IN MARKER.** One of six workers (D1 approach **A**, the shell seam) was **terminated after a ~54-minute stall** on a single in-flight `bash` call (diagnosed 2026-11-26T06:04:51Z; its last tool emission was 05:10:42Z). It has been **re-dispatched once** as `43f6d961` with a high-tier model and a deliberately narrow brief (three questions, ≤60-line target) — the original brief's size is the likeliest cause of the grind. **Its question is foreclosed by verified evidence, not left open:** the shell cannot read the registry (no credential path across the executor boundary; the only upgrade script with DB access is the **unshipped** `boot_smoke.sh`), and a VM-side dump is impossible on the five grounds in §8. **The D1 ruling does not depend on it**; the replacement exists only to put the rejected-alternative column on the record with citations. If it fails too, the gap is recorded here and the artifact stands.

**If that report lands with a fact that contradicts a ruling above, this artifact becomes `architecture-recommendation-v2.md`** — it is not silently revised. (Note: the *in-place* v1→v2 amendment described in the Revision history is a different thing — a correction to this same decision, with v1 retained at git commit `99df5d98c`.)

**Sources folded in:** the leader's recon update #2 (out-of-tree devops addendum receipt `88212d6d`; the arm-path / preserve-list map; this lane's own publication-lag measurement), the live registry query of 2026-11-26, the five landed worker reports, and my own reads of `origin/latest` = `8b646ae37` — every anchor quoted in this artifact was read directly at that commit, except where explicitly attributed to the recon's map.

**Corrections carried into this artifact** (superseding the commission's stated evidence):
1. The brief's *"legacy PVC lane failing ENOSPC since ~Sep 1"* is **out of date** — POK `pg-host-clockwork-landscape-01` (corrected 2026-11-25) records the lane **green again**, PVC expanded 20→60 Gi, retention disabled.
2. The **48 h** bound replaces the 36 h default proposed by the semantics worker — 36 h would block normal morning promotes on scanner lag alone (§5).
3. The **reminder that `integrity_ok` is the cluster-side `zstd -t` verdict** — this is what makes the metadata validation meaningful, and it is not inspectable from the DB or the repo.
4. **v1's D6 transport was wrong** — `os.environ` cannot express a per-promote opt-in on this deployment; the opt-in is a tool-call parameter bound into the `PendingOp`.

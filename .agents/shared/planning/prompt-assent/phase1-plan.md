# Phase 1: Token, lane and ledger primitives

> **Binding order.** `architecture-recommendation.md` @ `df8da01a6` **wins on conflict** with this
> document. **Must-apply deltas (transcribed 2026-11-10):**
> - **D-1 amendments** — mint-context columns (`mint_source_id`, `mint_channel_id`, `mint_is_dm`,
>   `minted_external_user_id`) + daemon-derived `mint_user_id`; **`render_sha256`** (this name
>   **retires** the `content_hash`/`content_sha256` naming); **NO `token_display` column**; **NO
>   hard FK** (indexed soft reference); the partial unique index `(version_id) WHERE
>   status='live'` **stays**.
> - **D-4** — the keyword-only, private, default-`None` consume hook on `atomic_transition`; the
>   **render-hash re-check folds INSIDE the hook's transaction** from the row **as read in-txn**
>   (**O-L**); **counters commit in their OWN transaction**, refusal path only, **before** the
>   guarded transition.
> - **D-3b / K-9** — the hourly mismatch budget is **IN-RAM, keyed by relay `instance_id`, and
>   P2-OWNED** (tool gate). **It is NOT P1 work** — do not build it here.
> - **D-5** — the approving-user key is the **nested** `message_metadata["discord"]["user_id"]`,
>   required and fail-closed.
> - **D-6** — the ≤10-minute freshness bound applies to **mint and approve** alike.
> - **J-3 / K-7** — the token binds to the **named canonical render** produced by one shared
>   helper; copying `prompt_versions.content_hash` is **FORBIDDEN** and pinned.
> - **O-B** — no hard FK to `prompt_versions` (audit-survival precedent).
> - **K-3** — the adapter **`is_group_dm` discriminator is P1-owned** (lane-scoped, additive;
>   absent discriminator ⇒ **not private** ⇒ fail-closed), with a pre-ship runtime confirmation.

## Objective

Build the **primitives** the conversational lane needs, with the FE path untouched:

1. a durable **per-item token store** (a new PG table + its own `.sql` migration + the `create_all`
   seam) whose rows carry the item binding, a **hash** of the token (never the raw token), a TTL,
   a status, and an attempt counter;
2. the **`assent-relay` actor lane** — a new auth-method value, a dedicated constructor that is the
   **only** way to mint it, and a typed **attestation** that the constructor requires;
3. an **attestation-bearing repository entrypoint** — the lane's own approve/reject path, leaving
   `approve()`/`reject()` (and the FE path) exactly as they are;
4. the **audit payload extension** that carries the relay provenance into
   `prompt_audit_events` — appended to a **new** row, inside the **same transaction** as the state
   change;
5. `repo.list_pending()` + `svc.list_pending()` — the enumeration primitives (the read *surface*
   is Phase 2).

**Nothing in this phase is user-visible.** The lane does not exist as a surface until Phase 2.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| **1.1** | **Create the `PromptAssentToken` model** — `daemon/repositories/prompt_assent/models.py` (NEW dir + `__init__.py`). Columns: `token_id` (PK, uuid4 hex), `version_id` (**indexed SOFT reference — NO hard FK** to `prompt_versions`; O-B: mirror the audit table's deliberate no-FK rule, `daemon/repositories/prompt_version/models.py:464-466` — *"audit survives row cleanup"* — forensic rows must outlive version archival), `agent_id`, `version_tag`, `artifact`, `version`, `token_hash` (sha256 — **never** the raw token; UNIQUE), **`render_sha256`** (sha256 of the **named canonical render** — J-3/K-7; **NEVER** a copy of `prompt_versions.content_hash`), **mint-context columns** `mint_source_id`, `mint_channel_id`, `mint_is_dm`, **`mint_user_id`** (daemon-derived — K-8), `minted_external_user_id` (AR-1), `issued_at`, `ttl_expires_at` (TTL = `NONCE_TTL_S` imported SSOT, 60 min), `superseded_at`, `consumed_at`, `consumed_by_message_id`, `attempts` (per-token mismatch count, default 0), `status` (`live\|consumed\|superseded\|revoked`). **NO `token_display` column** — the grouped render minus dashes *is* the raw secret; render the grouped form from the raw token **at mint time only**. Indexes (names byte-identical to `__table_args__`): `UNIQUE (token_hash)` · **partial `UNIQUE (version_id) WHERE status='live'`** (single-active-token, structural against double-mint races) · `(version_id, status)` | none | The model imports cleanly; the table name is unique; `token_hash` is a **hash column** and the raw token has **no column** anywhere in the schema; **no column named `token_display`** exists; **no column is named `content_hash`/`content_sha256`**; a grep proves the token table carries **no hard FK** to `prompt_versions` |
| **1.2** | **Write the token repository** — `daemon/repositories/prompt_assent/repository.py` (NEW). Methods: `mint(...)` (writes a row, **supersedes** any prior live token for the same item — re-mint **supersedes AND revokes** the prior token: `superseded_at` + status flip; the prior token then reads `token-superseded`, **deliberately distinct** from `token-already-used`), `find_live(version_id)`, `find_by_hash(hash)`, `consume(token_id, consumed_by_message_id)`, `record_failed_attempt(token_id) -> bool` (increment; revoke at the threshold), `revoke(token_id)`, `sweep()` (TTL sweep that **retains consumed rows**), `list_pending_items(...)` if needed. Semantics mirror the promote GC: *"only deletes what it can prove is dead"* (`daemon/tools/upgrade_journal.py:1926-1958`) and *"keeps one consumed entry"* so "already used" stays answerable (`:1931-1934`). **⚠ `record_failed_attempt` commits in its OWN transaction** (D-4 blocking rule) — it is called on the **refusal path only**, **before** any guarded transition opens; it must **not** share the transition's transaction, because a rollback inside the guarded transaction would reset the counter and the lockout would never fire (D-3's defence-in-depth would be silently inert). **PG test home:** the durable leg lives in PG, so the store's tests run against a **native disposable PG cluster** (no Docker — house rule); see the Test plan for the invocation. **The K-9 hourly per-relay-instance budget is NOT here** — it is in-RAM, keyed by `relay_instance_id`, and **P2-owned** (tool gate; `phase2-plan.md` task 2.11) | 1.1 | Every method round-trips against PG; a consumed row survives a sweep and still answers `token-already-used`; a re-mint supersedes rather than duplicates; **`record_failed_attempt` survives a forced rollback of a neighbouring transaction** (its own-transaction property is directly asserted) |
| **1.3** | **Write the token table's own `.sql` migration** — `daemon/migrations/versions/YYYYMMDD_HHMMSS_create_prompt_assent_tokens.sql` (NEW). UP first, DOWN last; every statement idempotent (`IF NOT EXISTS` / `IF EXISTS`); **index names byte-identical** to the model's `__table_args__` (parity test exists: `tests/migration/test_prompt_versions_index_parity.py`) | 1.1 | The file exists, is idempotent, and its index names match `__table_args__` exactly (the parity test passes) |
| **1.4** | **Add the `create_all` seam** — `daemon/api.py` (the `NotifyAudit.metadata.create_all(manager.engine)` block, `:1142-1143`). A **brand-new table** rides this seam and therefore needs **NO** `_ensure_postgres_columns` mirror — the seam's own comment says so | 1.1 | PG has the table after boot; **grep proves there is no `_ensure_postgres_columns` entry for `prompt_assent_tokens`** (the seam's "N1 REQUIRED" note: a missed import = no table on PG = every token INSERT fails) |
| **1.5** | **Add the `assent-relay` lane value + the attestation type** — `daemon/repositories/prompt_version/repository.py`: `AUTH_METHOD_ASSENT_RELAY = "assent-relay"`; extend `_AUTH_METHODS` (`:503-511`); extend `ActorContext.__post_init__` (`:551-566`) with a branch that **requires** the attestation fields for this lane (mirroring the `internal` branch's requirement of `instance_id` + `agent_id`). New module `daemon/repositories/prompt_version/assent_attestation.py` (NEW) holding the typed `AssentAttestation` — **distinctly named** so it does not collide with the existing promote attestation (`tests/unit/tools/test_attestation_registration.py`) | none | `_AUTH_METHODS` has four values; constructing an `assent-relay` actor **without** a complete attestation **raises**; the existing three lanes' constructors behave **unchanged** |
| **1.6** | **Add the attestation-bearing repository entrypoint** — `daemon/repositories/prompt_version/repository.py`: `approve_assent(version_id, actor, attestation)` and `reject_assent(version_id, actor, attestation, reason)`. They must (a) refuse an actor that is not `assent-relay`; (b) refuse an absent/incomplete attestation; (c) delegate to `atomic_transition` exactly as `approve()`/`reject()` do (`:1541-1607`) — i.e. reuse the same CAS, the same `_refuse_if_memory` pre-flight (`:1421-1427`), the same audit write | 1.5 | `approve()` and `reject()` are **byte-unchanged** (diff against the base commit shows no behavioural change); `approve_assent` refuses a non-assent-relay actor and an attestation-less call; the F4 refusal of internal actors is **untouched** |
| **1.7** | **Widen the identity clamp, minimally** — `atomic_transition`'s approver-identity clamp (`:1433-1444`) must accept `assent-relay` alongside `session`/`bearer`. **The clamp is Python-side only** — there is **no CHECK constraint** on `approver_subject` or `approver_auth_method` (`daemon/repositories/prompt_version/models.py:409-415`, `:524-531`), so **no schema change and no migration** is required. `assent-relay` is 12 chars, well inside the 32-char column | 1.5, 1.6 | A relayed transition passes the clamp; `approver_auth_method` is persisted as `assent-relay` and is **distinguishable in the ledger**; a value outside the allowed set is still refused |
| **1.8** | **Extend the audit payload assembly** — `daemon/repositories/prompt_version/repository.py:1493-1511` (`_append_audit_in_conn`): accept an optional relay-provenance dict and merge it into the payload. **Keep the event types `APPROVED`/`REJECTED`** (D-9) and discriminate on `approval_path`. `reject_reason` goes at the **top level** of the payload (DECIDED-1). `token_fingerprint = sha256(token)[:12]` — **never** the raw token | 1.6 | A relayed decision writes **one new** event row carrying every provenance key; the FE payload shape is **unchanged**; **no existing event row is modified** |
| **1.9** | **Add `repo.list_pending()` + `svc.list_pending()`** — clone `count_pending`'s CORE-only predicate (`state='pending_approval' AND artifact_class='CORE'`, `daemon/repositories/prompt_version/repository.py:2999-3029`), return the **rows** ordered by `created_at`; service wrapper at the `svc.count_pending` pattern (`daemon/services/prompt_version_service.py:1012-1023`). **No agent filter** — the relay lane must be able to approve any agent's row (DECIDED-2). Bounded (e.g. limit 50) | none | The list returns CORE pending rows across agents; the FE's `GET /api/prompts/versions` and `pending-count` behave **unchanged** |
| **1.10** | **Mint the ONE shared canonical-render helper** — NEW module `daemon/services/prompt_version_diff.py` (or an equivalent single owner). It owns the **named canonical render**: a pure lift-and-shift of the router-local `_unified_diff` (`daemon/routers/prompts.py:431-435`, stdlib `difflib`) plus the canonical input assembly — **pending content**, **effective content** (the effective side IS included), the item key, the labels, and a **render format tag `"v1"`**. The **REST diff route imports the helper** (FE output byte-identical — pinned by test) and the tool's mint path renders through it. **The tool layer must never import the router.** `render_sha256 = sha256(canonical_render)`, computed at mint **from the exact bytes placed in the mint response** | 1.1 | The helper is the **single** render truth; the FE diff route's output is **byte-identical** to the pre-change output (pinned by test); the tool layer has **no** import of `daemon.routers.prompts`; the canonical render includes the **effective** side; a format-tag change is visible in the hash |
| **1.11** | **Add the D-4 keyword-only consume hook to `atomic_transition`** — `daemon/repositories/prompt_version/repository.py`: `atomic_transition` gains a **narrow, keyword-only, private, default-`None`** typed hook, executed **inside its existing transaction** (one code path — **no forked CAS+audit copy**; the no-divergent-copies doctrine). The hook performs, **in order**: **(i) the render-hash re-check** — re-render the canonical diff **from the row as read inside the transaction** and compare to the token's `render_sha256`; mismatch ⇒ raise ⇒ rollback ⇒ `token-content-mismatch`; **(ii) the consume-CAS** — `UPDATE … SET status='consumed', consumed_at=…, consumed_by_message_id=… WHERE token_id=? AND status='live'`; rowcount 0 ⇒ raise ⇒ rollback ⇒ typed refusal. **O-L: this is the ONLY render-hash site** — the pre-transition layer does **not** duplicate it, so shown/hashed/approved bytes cannot diverge | 1.10 | The hook is **default-absent** ⇒ the FE path is provably untouched (pinned by test); the hook runs **inside** the transition's transaction; a render-hash divergence rolls the state change back with it; the consume-CAS refuses a non-`live` token |
| **1.12** | **Add the adapter `is_group_dm` discriminator (lane-scoped, additive)** — `daemon/sources/adapters/discord/adapter.py`: surface `is_group_dm: bool` (or an equivalent recipient-count signal) in the nested `discord_meta` at the `:1046-1058` build site. **Additive metadata — no schema change.** **P1-owned** (the mint-context derivation consumes it). Rationale: today `is_dm = guild is None` (`adapter.py:1043`) and there is **zero** group discrimination in the adapter — a Discord **group DM** (≤10 recipients) classifies `is_dm=True`, i.e. it is exactly the multi-occupant private channel AR-1 must exclude. **Fail-closed rule: an absent or unparseable discriminator ⇒ NOT private** (mirrors D-6's absent-stamp rule) — older rows simply cannot mint or approve until the discriminator ships. **Pre-ship runtime confirmation:** a live probe must show a 1:1 DM and a group DM classifying **distinctly** (a miss is bounded to availability, never authority) | none | The discriminator appears in the nested `discord_meta`; a 1:1 DM and a group DM classify **distinctly** in the pre-ship probe; **absent ⇒ not private** is directly asserted; **no schema change** was made; the adapter's existing behaviour is otherwise unchanged |

### File-level task map

| File | Change | Task |
|---|---|---|
| `daemon/repositories/prompt_assent/models.py` | NEW | 1.1 |
| `daemon/repositories/prompt_assent/repository.py` | NEW | 1.2 |
| `daemon/repositories/prompt_assent/__init__.py` | NEW | 1.1 |
| `daemon/migrations/versions/<ts>_create_prompt_assent_tokens.sql` | NEW | 1.3 |
| `daemon/api.py` | create_all seam | 1.4 |
| `daemon/repositories/prompt_version/assent_attestation.py` | NEW | 1.5 |
| `daemon/repositories/prompt_version/repository.py` | lane value, actor branch, constructor, entrypoints, clamp, audit payload, **D-4 consume hook** | 1.5, 1.6, 1.7, 1.8, **1.11** |
| `daemon/repositories/prompt_version/models.py` | **no change expected** (payload is JSONB; no CHECK constraints) | — |
| `daemon/services/prompt_version_service.py` | `list_pending` wrapper | 1.9 |
| `daemon/services/prompt_version_diff.py` | **NEW — the one shared canonical-render helper** | **1.10** |
| `daemon/routers/prompts.py` | **import the shared helper** (byte-identical FE output) | **1.10** |
| `daemon/sources/adapters/discord/adapter.py` | **`is_group_dm` discriminator** (additive metadata) | **1.12** |

## Coupling

- **Tight with:** Phase 2 — the tool consumes the token API (`mint`/`find`/`consume`/attempt
  counting) and the attestation type; the tool's `mint` branch renders the diff from
  `repo.get_effective` (`daemon/repositories/prompt_version/repository.py:1900-1945`).
- **Tight with:** Phase 3 — the service acceptance path calls `approve_assent` / `reject_assent`
  and supplies the attestation. The **attestation type is the seam**: Phase 1 defines it, Phase 3
  produces it.
- **Independent of:** Phase 4 (the kill-switch config class) — with the exception of the
  **fail-closed default** noted under Risks.

**Shared-context ownership.** This phase owns the **C-1** (token persistence) and the
**repository half of C-2** (prompt-version core) contexts. See `plan-overview.md` →
Shared-context grouping. **Phase 3 owns the service half of C-2** — do not let two agents edit
`daemon/repositories/prompt_version/repository.py` concurrently.

**Render-hash binding (J-3 / K-7).** Task 1.1 includes the **`render_sha256`** column; task 1.10
mints the **one shared canonical-render helper** that produces it; task 1.11 folds the re-check
**inside the D-4 hook's transaction** (**O-L** — the only render-hash site). **The effective side
IS included** in the canonical render, so **baseline motion invalidates** the outstanding token:
if the effective side moves underneath (a newer version approved), the pending row's meaning
changes and the token MUST invalidate (`token-content-mismatch`) rather than silently approve
against a different baseline — the CAS alone cannot see effective-side motion.

**⚠ `content_hash` / `content_sha256` naming is RETIRED in the token table.** The column-copy of
`prompt_versions.content_hash` (`daemon/repositories/prompt_version/models.py:369` — sha256 of raw
content) is **FORBIDDEN and pinned**: it satisfies SP-1's letter while silently no-oping J-3 — a
pending-content mutation after mint *would* trip it, but an **effective-side** mutation or a
rendered-view divergence would not, and the shown-bytes guarantee disappears. The name
`render_sha256` is used in **one** form everywhere: token-table column, mint response,
attestation, audit payload.

**K-3 discriminator — fail-closed by construction.** The `is_group_dm` discriminator (task 1.12)
is **absent ⇒ not private**. This bounds a stale discriminator to an **availability** problem,
never an **authority** problem.

## Risks

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| **A missed `create_all` seam** = no table on PG = every token INSERT fails at runtime | High | Medium | The seam's own "N1 REQUIRED" note (`daemon/api.py:1142-1143`); add a boot-smoke assertion that the table exists; the seam import is in the **same commit** as the model |
| **A new `_ensure_postgres_columns` entry creeps in** ("just add a column later") | High | Medium | The ensure list is **one transaction for the whole statement list** with no per-statement isolation (`daemon/manager.py:10036-10038`) — any entry's failure **aborts boot**. Task 1.4's acceptance includes a **grep proving no ensure entry exists** for this table; add a **pin** that keeps it that way |
| **The raw token leaks into the DB or a log** | High | Low | The model has **no column** for the raw token (task 1.1); the audit carries `sha256(token)[:12]` only; the token appears in exactly one place — the mint response shown to the Emperor |
| **Widening the identity clamp weakens the FE's protection** | High | Low | The clamp change is **additive** (`assent-relay` joins `session`/`bearer`); `internal` remains refused; `approve()`/`reject()` are untouched. Task 1.6's acceptance includes a **diff against the base commit** proving the FE path is unchanged |
| **The `assent-relay` actor becomes forgeable from inside the process** | Medium | Medium | Accepted and documented (D-8): an agent with in-process code execution could import the attestation module — true of every in-process authority in this tree. The relied-upon boundary is the **three-layer access posture**, and the specific hardening is that the attestation **cannot be minted from tool arguments** |
| **Attempt-counter / lockout thresholds are set wrong** (too tight ⇒ the Emperor is locked out; too loose ⇒ the guessing loop is unbounded) | Medium | Medium | **P1 owns the durable leg only:** 5 mismatches ⇒ revoke the live token (⇒ a fresh mint, and therefore a **fresh human turn**, is required to clear it), and the counter commits in its **own transaction** (D-4). **The hourly per-relay-instance bound is NOT P1 work** — it is the in-RAM, `relay_instance_id`-keyed **K-9 budget owned by P2** (tool gate, `phase2-plan.md` task 2.11), configurable via `PromptAssentConfig.hourly_mismatch_budget` (default 10, class lands in P4). Both thresholds are **configurable**, not hard-coded constants, so they can be tuned without a code change |
| **A consumed row is swept away**, making "already used" unanswerable | Medium | Low | The sweep **retains consumed rows** (task 1.2); mirror the promote GC's `keep_run_id` rule (`daemon/tools/upgrade_journal.py:1931-1934`) |
| **A concurrent phase edits the same repository file** | Medium | Medium | Context ownership above: Phase 1 owns the repository half of C-2; Phase 3 owns the service half. If the architect prefers, **merge Phase 1 and Phase 3** rather than run them concurrently |

## Test plan (house conventions)

**Env hygiene (H-7).** Scrub **per-variable** — `env -u POSTGRES_*` is a **silent NO-OP** (bash
does not glob env names):
```
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT \
    <test command>
```

**Runner (H-8).** Use the **worktree's own venv** — never the main checkout's `.venv` (its
editable `.pth` pins the main tree). `cd` into the worktree first. `testpaths=["tests"]`; four
dirs deselected (integration / postgres / slow / conformance); `timeout=30` + `thread`;
`-p no:timeout` when a test needs it; pass explicit `tests/` lists; `pytest -q` buffers
tracebacks to the session end ⇒ `tee LOG >/dev/null` + `tail -2 LOG`.

**PG test home (O-K) — native disposable cluster, NO Docker.** The durable leg (the token table,
the attempts counter, the lockout, the consume-CAS) lives in PostgreSQL, so it must be tested
against a **real PG**, not SQLite and not a mock:
- **Native disposable cluster** only — `initdb -A trust` into a temp dir, a **TCP** listener on a
  scratch port, `createdb` over TCP (**not** the `/tmp` socket), and a **DROP teardown** at the
  end. **Docker is BANNED** (house rule — no Docker on this host).
- `createdb` over **TCP**, not over the unix socket — the sqlite/pg socket paths are not
  interchangeable for the DSN this lane's tests build.
- **Env scrub PER-VARIABLE** before the run (see above): `env -u POSTGRES_DB -u POSTGRES_USER -u
  POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT` — `env -u POSTGRES_*` is a **silent
  NO-OP** (bash does not glob env names), so an ambient `POSTGRES_DB=ensemble_prod` stays visible
  and a "fenced" run can hit **prod**. **Scrub the full family or none** — a partial scrub also
  breaks collection.
- The DSN must be built from the **scratch cluster's** host/port, never from an ambient
  `POSTGRES_*` value; assert the resolved `POSTGRES_DB` is **not** `ensemble_prod` before the run.
- The scratch cluster's port must not collide with the live listener — pick a free high port and
  **verify the listener is down** before `initdb`.

**Net-new = FAILED-list diff (H-6).** Capture the **TRUE pre-fix base** (same commands, on the
base commit) and diff the failed-lists. A test already failing before the change is **not** a new
red.

**Tests to write in this phase.**

| Test file | Covers |
|---|---|
| `tests/unit/test_prompt_assent_token_store.py` (NEW) | mint → find → consume → sweep; the raw token is never stored; consumed rows are retained; re-mint **supersedes** (and the superseded token reads `token-superseded`, **not** `token-already-used`) |
| `tests/unit/test_prompt_assent_actor_lane.py` (NEW) | `_AUTH_METHODS` has four values; an `assent-relay` actor **cannot** be built without a complete attestation; `approve()`/`reject()` still refuse internal actors (F4 unchanged); the FE actor constructors are untouched |
| `tests/unit/test_prompt_assent_audit_payload.py` (NEW) | a relayed decision writes one new event with every provenance key; the **FE payload shape is byte-identical** to the base; **no existing event row is modified**; the raw token appears **nowhere** in the payload |
| `tests/unit/test_prompt_assent_list_pending.py` (NEW) | `list_pending` returns CORE pending rows across agents, ordered by `created_at`; MEMORY-class rows are excluded |
| `tests/migration/test_prompt_assent_tokens_index_parity.py` (NEW) | the `.sql` index names are byte-identical to `__table_args__` (mirror `tests/migration/test_prompt_versions_index_parity.py`) |
| `tests/unit/test_prompt_assent_render_binding.py` (NEW) | the canonical-render helper is the **single** render truth; the **FE diff route's output is byte-identical** to the pre-change output; the tool layer has **no** import of `daemon.routers.prompts`; the render **includes the effective side**; `render_sha256` is computed from the **exact mint-response bytes**; a format-tag change moves the hash |
| `tests/unit/test_prompt_assent_consume_hook.py` (NEW) | the D-4 hook is **default-absent** (FE path provably untouched); the hook runs **inside** the transition's transaction; a render-hash divergence **rolls the state change back** with it; the consume-CAS refuses a non-`live` token; **no forked CAS+audit copy** exists |
| `tests/unit/test_prompt_assent_group_dm_discriminator.py` (NEW) | a 1:1 DM and a group DM classify **distinctly**; an **absent** discriminator ⇒ **not private** (fail-closed); an unparseable discriminator ⇒ not private; the discriminator is **additive** (no schema change) |
| `tests/unit/test_prompt_assent_column_source.py` (NEW) | **the K-7 column-source pin** — the token table's hash column is populated from the **canonical render**, and a mutation that feeds it `prompt_versions.content_hash` **reddens** the mint test |
| `tests/postgres/test_prompt_assent_token_store_pg.py` (NEW) | the durable leg against a **native disposable PG cluster** (O-K): attempts counter + lockout + consume-CAS + own-transaction property (the counter survives a forced rollback of a neighbouring transaction) |

**Non-vacuity.** Each test must have a **counter-mutation** that reddens it. Absence-only and
raw-text pins are prose-satisfiable and prove nothing.

**Mutation pack.** Add the lane to the existing pack convention
(`test/packs/metrics_read_mutation_matrix_test.py` derives its count at pack-import time — it
auto-adapts, but the new category may need adding as a mutation target — Phase 2 owns that).

## Exit Criterion

**All of the following hold, and are demonstrable in one report:**

1. A token can be **minted, found, consumed, attempt-counted and swept** in PG — and the raw
   token appears **nowhere** in durable state. The token table carries **no `token_display`
   column**, **no `content_hash`/`content_sha256` column**, and **no hard FK** to
   `prompt_versions`; the partial unique index `(version_id) WHERE status='live'` exists.
2. A relay actor can be constructed **only** with a complete attestation; the F4 refusal of
   internal actors is **intact**; `approve()`/`reject()` are **byte-unchanged** against the base
   commit.
3. An audit event written by the relay path carries the **full provenance** and is appended,
   **never** overwriting an existing event.
4. `repo.list_pending()` / `svc.list_pending()` return the CORE pending set across agents.
5. **No** `_ensure_postgres_columns` entry exists for `prompt_assent_tokens` (grep-proven), and
   the new table has **its own** `.sql` migration with index-name parity.
6. **The canonical render has exactly one owner** (task 1.10); the FE diff route imports it and
   its output is **byte-identical**; the tool layer does **not** import the router.
7. **The D-4 hook is default-absent** and runs **inside** the transition transaction; the
   render-hash re-check lives **only** there (O-L) and compares against `render_sha256`.
8. **The adapter `is_group_dm` discriminator ships**, classifies 1:1 DM and group DM
   **distinctly** in the pre-ship probe, and an **absent** discriminator is **not private**.
9. **The counters commit in their own transaction** (D-4) — directly asserted against a native
   disposable PG cluster, with **no Docker** and a per-variable env scrub.
10. The **net-new failed-list is empty** against the TRUE pre-fix base.

Only then may Phase 2 begin.

# Phase 1: Token, lane and ledger primitives

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
| **1.1** | **Create the `PromptAssentToken` model** — `daemon/repositories/prompt_assent/models.py` (NEW dir + `__init__.py`). Columns: `token_id` (PK, uuid4 hex), `version_id`, `agent_id`, `version_tag`, `artifact`, `version`, `token_hash` (sha256 — **never** the raw token), `token_display`, `content_hash` (J-3 — see Coupling), `issued_at`, `ttl_expires_at`, `superseded_at`, `consumed_at`, `consumed_by_message_id`, `attempts`, `status` (`live|consumed|superseded|revoked`). **No hard FK** to `prompt_versions` — mirror the audit table's deliberate no-FK rule (`daemon/repositories/prompt_version/models.py:464-466`: *"audit survives row cleanup"*) | none | The model imports cleanly; the table name is unique; `token_hash` is a **hash column**, and the raw token has **no column** anywhere in the schema |
| **1.2** | **Write the token repository** — `daemon/repositories/prompt_assent/repository.py` (NEW). Methods: `mint(...)` (writes a row, **supersedes** any prior live token for the same item), `find_live(version_id)`, `find_by_hash(hash)`, `consume(token_id, consumed_by_message_id)`, `record_failed_attempt(token_id) -> bool` (increment; revoke at the threshold), `revoke(token_id)`, `sweep()` (TTL sweep that **retains consumed rows**), `list_pending_items(...)` if needed. Semantics mirror the promote GC: *"only deletes what it can prove is dead"* (`daemon/tools/upgrade_journal.py:1926-1958`) and *"keeps one consumed entry"* so "already used" stays answerable (`:1931-1934`) | 1.1 | Every method round-trips against PG; a consumed row survives a sweep and still answers `token-already-used` |
| **1.3** | **Write the token table's own `.sql` migration** — `daemon/migrations/versions/YYYYMMDD_HHMMSS_create_prompt_assent_tokens.sql` (NEW). UP first, DOWN last; every statement idempotent (`IF NOT EXISTS` / `IF EXISTS`); **index names byte-identical** to the model's `__table_args__` (parity test exists: `tests/migration/test_prompt_versions_index_parity.py`) | 1.1 | The file exists, is idempotent, and its index names match `__table_args__` exactly (the parity test passes) |
| **1.4** | **Add the `create_all` seam** — `daemon/api.py` (the `NotifyAudit.metadata.create_all(manager.engine)` block, `:1142-1143`). A **brand-new table** rides this seam and therefore needs **NO** `_ensure_postgres_columns` mirror — the seam's own comment says so | 1.1 | PG has the table after boot; **grep proves there is no `_ensure_postgres_columns` entry for `prompt_assent_tokens`** (the seam's "N1 REQUIRED" note: a missed import = no table on PG = every token INSERT fails) |
| **1.5** | **Add the `assent-relay` lane value + the attestation type** — `daemon/repositories/prompt_version/repository.py`: `AUTH_METHOD_ASSENT_RELAY = "assent-relay"`; extend `_AUTH_METHODS` (`:503-511`); extend `ActorContext.__post_init__` (`:551-566`) with a branch that **requires** the attestation fields for this lane (mirroring the `internal` branch's requirement of `instance_id` + `agent_id`). New module `daemon/repositories/prompt_version/assent_attestation.py` (NEW) holding the typed `AssentAttestation` — **distinctly named** so it does not collide with the existing promote attestation (`tests/unit/tools/test_attestation_registration.py`) | none | `_AUTH_METHODS` has four values; constructing an `assent-relay` actor **without** a complete attestation **raises**; the existing three lanes' constructors behave **unchanged** |
| **1.6** | **Add the attestation-bearing repository entrypoint** — `daemon/repositories/prompt_version/repository.py`: `approve_assent(version_id, actor, attestation)` and `reject_assent(version_id, actor, attestation, reason)`. They must (a) refuse an actor that is not `assent-relay`; (b) refuse an absent/incomplete attestation; (c) delegate to `atomic_transition` exactly as `approve()`/`reject()` do (`:1541-1607`) — i.e. reuse the same CAS, the same `_refuse_if_memory` pre-flight (`:1421-1427`), the same audit write | 1.5 | `approve()` and `reject()` are **byte-unchanged** (diff against the base commit shows no behavioural change); `approve_assent` refuses a non-assent-relay actor and an attestation-less call; the F4 refusal of internal actors is **untouched** |
| **1.7** | **Widen the identity clamp, minimally** — `atomic_transition`'s approver-identity clamp (`:1433-1444`) must accept `assent-relay` alongside `session`/`bearer`. **The clamp is Python-side only** — there is **no CHECK constraint** on `approver_subject` or `approver_auth_method` (`daemon/repositories/prompt_version/models.py:409-415`, `:524-531`), so **no schema change and no migration** is required. `assent-relay` is 12 chars, well inside the 32-char column | 1.5, 1.6 | A relayed transition passes the clamp; `approver_auth_method` is persisted as `assent-relay` and is **distinguishable in the ledger**; a value outside the allowed set is still refused |
| **1.8** | **Extend the audit payload assembly** — `daemon/repositories/prompt_version/repository.py:1493-1511` (`_append_audit_in_conn`): accept an optional relay-provenance dict and merge it into the payload. **Keep the event types `APPROVED`/`REJECTED`** (D-9) and discriminate on `approval_path`. `reject_reason` goes at the **top level** of the payload (DECIDED-1). `token_fingerprint = sha256(token)[:12]` — **never** the raw token | 1.6 | A relayed decision writes **one new** event row carrying every provenance key; the FE payload shape is **unchanged**; **no existing event row is modified** |
| **1.9** | **Add `repo.list_pending()` + `svc.list_pending()`** — clone `count_pending`'s CORE-only predicate (`state='pending_approval' AND artifact_class='CORE'`, `daemon/repositories/prompt_version/repository.py:2999-3029`), return the **rows** ordered by `created_at`; service wrapper at the `svc.count_pending` pattern (`daemon/services/prompt_version_service.py:1012-1023`). **No agent filter** — the relay lane must be able to approve any agent's row (DECIDED-2). Bounded (e.g. limit 50) | none | The list returns CORE pending rows across agents; the FE's `GET /api/prompts/versions` and `pending-count` behave **unchanged** |

### File-level task map

| File | Change | Task |
|---|---|---|
| `daemon/repositories/prompt_assent/models.py` | NEW | 1.1 |
| `daemon/repositories/prompt_assent/repository.py` | NEW | 1.2 |
| `daemon/repositories/prompt_assent/__init__.py` | NEW | 1.1 |
| `daemon/migrations/versions/<ts>_create_prompt_assent_tokens.sql` | NEW | 1.3 |
| `daemon/api.py` | create_all seam | 1.4 |
| `daemon/repositories/prompt_version/assent_attestation.py` | NEW | 1.5 |
| `daemon/repositories/prompt_version/repository.py` | lane value, actor branch, constructor, entrypoints, clamp, audit payload | 1.5, 1.6, 1.7, 1.8 |
| `daemon/repositories/prompt_version/models.py` | **no change expected** (payload is JSONB; no CHECK constraints) | — |
| `daemon/services/prompt_version_service.py` | `list_pending` wrapper | 1.9 |

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

**Content-hash binding (J-3).** Task 1.1 includes the `content_hash` column. If the architect
**declines J-3**, drop the column and the mint/approve re-check — but note that the pin **SP-1**
in `risks.md` then also lapses, and `risks.md` R-10 must be re-scoped. The column is cheap
(one column + two call sites); the ruling is the architect's.

## Risks

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| **A missed `create_all` seam** = no table on PG = every token INSERT fails at runtime | High | Medium | The seam's own "N1 REQUIRED" note (`daemon/api.py:1142-1143`); add a boot-smoke assertion that the table exists; the seam import is in the **same commit** as the model |
| **A new `_ensure_postgres_columns` entry creeps in** ("just add a column later") | High | Medium | The ensure list is **one transaction for the whole statement list** with no per-statement isolation (`daemon/manager.py:10036-10038`) — any entry's failure **aborts boot**. Task 1.4's acceptance includes a **grep proving no ensure entry exists** for this table; add a **pin** that keeps it that way |
| **The raw token leaks into the DB or a log** | High | Low | The model has **no column** for the raw token (task 1.1); the audit carries `sha256(token)[:12]` only; the token appears in exactly one place — the mint response shown to the Emperor |
| **Widening the identity clamp weakens the FE's protection** | High | Low | The clamp change is **additive** (`assent-relay` joins `session`/`bearer`); `internal` remains refused; `approve()`/`reject()` are untouched. Task 1.6's acceptance includes a **diff against the base commit** proving the FE path is unchanged |
| **The `assent-relay` actor becomes forgeable from inside the process** | Medium | Medium | Accepted and documented (D-8): an agent with in-process code execution could import the attestation module — true of every in-process authority in this tree. The relied-upon boundary is the **three-layer access posture**, and the specific hardening is that the attestation **cannot be minted from tool arguments** |
| **Attempt-counter / lockout thresholds are set wrong** (too tight ⇒ the Emperor is locked out; too loose ⇒ the guessing loop is unbounded) | Medium | Medium | 5 mismatches ⇒ revoke the live token (⇒ a fresh mint, and therefore a **fresh human turn**, is required to clear it) plus a global per-relay-instance bound of ≤10 mismatches/hour. Both thresholds are **configurable**, not hard-coded constants, so they can be tuned without a code change |
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

**Non-vacuity.** Each test must have a **counter-mutation** that reddens it. Absence-only and
raw-text pins are prose-satisfiable and prove nothing.

**Mutation pack.** Add the lane to the existing pack convention
(`test/packs/metrics_read_mutation_matrix_test.py` derives its count at pack-import time — it
auto-adapts, but the new category may need adding as a mutation target — Phase 2 owns that).

## Exit Criterion

**All of the following hold, and are demonstrable in one report:**

1. A token can be **minted, found, consumed, attempt-counted and swept** in PG — and the raw
   token appears **nowhere** in durable state.
2. A relay actor can be constructed **only** with a complete attestation; the F4 refusal of
   internal actors is **intact**; `approve()`/`reject()` are **byte-unchanged** against the base
   commit.
3. An audit event written by the relay path carries the **full provenance** and is appended,
   **never** overwriting an existing event.
4. `repo.list_pending()` / `svc.list_pending()` return the CORE pending set across agents.
5. **No** `_ensure_postgres_columns` entry exists for `prompt_assent_tokens` (grep-proven), and
   the new table has **its own** `.sql` migration with index-name parity.
6. The **net-new failed-list is empty** against the TRUE pre-fix base.

Only then may Phase 2 begin.

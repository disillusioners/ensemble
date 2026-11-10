# prompt-assent — Technical Analysis (architect-facing)

**Commission:** "prompt-assent" — the conversational approval lane (*The Emperor's Word / Assent*).
**Role of this document:** technical-analysis channel of a planning lane. It equips the architect
and the plan-creation worker that follows. It is NOT the plan.

| Field | Value |
|---|---|
| Date | 2026-11-10 (UTC) |
| Worktree | `/home/nea/ensemble-src-wt-prompt-assent` |
| Branch | `feature/prompt-assent` |
| Base commit | `0efdf106f3f66ccb400405f4137694592b72097d` (VERIFIED — `git rev-parse HEAD`) |
| Branch verified | `feature/prompt-assent` (VERIFIED — `git rev-parse --abbrev-ref HEAD`) |
| Worktree state | clean except the untracked planning dir (VERIFIED — `git status --short` → `?? .agents/shared/planning/prompt-assent/`) |
| Inputs read FULLY | `research/approval-path.md` (148 lines), `research/trust-anchors.md` (622 lines) |
| Writes by this lane | this file ONLY. No source file edited, no commit made. |

**Method note.** Both research notes were produced in a *sibling* tree
(`/home/nea/ensemble-src-wt-v2915-bump`) after the commissioned worktree was deleted mid-lane
(`trust-anchors.md` §0 and §9). Every load-bearing anchor below was **re-verified by me
directly in the commissioned worktree** using `bash grep -n` / `sed -n` (never `grep_files`,
which silently skips files >1.4 MB — `upgrade_journal.py`, `upgrade_tools.py`, `prompts.py`).
Un-verified carry-overs are listed in §13.

---

## §0 — The one-paragraph shape of the problem

Today, "a human approved this prompt" means exactly one thing: `AuthContext.auth_method ==
"session"` (the cookie lane). That single equality is checked **three times** in defence in
depth — the service lane guard (`prompt_version_service.py:677-717`), the router 403
(`prompts.py:639-640`, `:680-681`), and the repository's refusal of internal actors
(`repository.py:1560-1565`, `:1594-1599`, *F4: agents cannot self-approve*) — plus a fourth
clamp inside the transition itself (`repository.py:1433-1444`: `approver_auth_method` must be
`session` or `bearer`). A chat-relayed approval arrives as an **internal** actor (Ari) and
therefore **cannot** use any of those four gates as they stand. The commission's lane needs a
*new* approval path whose authority is a **human turn + a per-item token**, not a cookie
session — and whose implementation must not reopen the self-approval hole that F4 closed.

The three things that make this tractable are all in-tree precedents, not inventions:

1. the **per-turn user-origin window** (`manager._user_origin_windows`, `manager.py:6126-6131`)
   — already the live-promote's factor 2, already cleared on every non-user-origin dispatch;
2. the **message-content echo read** (`upgrade_tools.py:3508-3531`) — already the promote's
   factor 3, already fail-closed when the row is unreadable;
3. the **append-only audit row written inside the state-transition transaction**
   (`repository.py:1493-1511`) — the provenance lands for free, in the same COMMIT as the
   state change, with no schema work.

The hard part is not the trust model. It is **naming** the relay approver in a vocabulary
that currently has exactly three values (`session` / `bearer` / `internal`) and one of them
is banned for approvals by name.

---

## §1 — Q1. Token store persistence

**Question.** Where does the per-item confirmation token live?

### Options

**(a) A new PG table `prompt_assent_tokens`** (one row per issued token).

- Precedent: every recent tool-owned ledger is a brand-new table with its own `.sql`:
  `notify_audit` (`daemon/repositories/notify_audit/models.py:134`, migration
  `20261025_000001_create_notify_audit.sql`), `secrets` + `secrets_audit`, `instance_ops_audit`
  (`daemon/api.py:1114-1150`).
- **A brand-new table needs NO `_ensure_postgres_columns` entry.** `daemon/api.py:1142-1143`
  creates it via `NotifyAudit.metadata.create_all(manager.engine)`; the comment there is
  explicit: *"No `_ensure_postgres_columns` mirror needed."* That matters because the ensure
  list is **one transaction for the whole statement list** (`manager.py:10036-10038`,
  VERIFIED: `with self._engine.begin() as conn: for stmt in statements: conn.execute(text(stmt))`)
  with no per-statement try/except — one bad statement rolls back the whole ensure. A new
  **table** avoids that blast radius entirely; a new **column** does not.
- Cost: one model + one repository + one `.sql` + one `api.py` create_all seam.

**(b) Columns on `prompt_versions`** (`assent_token`, `assent_token_expires_at`,
`assent_consumed_at`, `assent_attempts`).

- Upside: the token's grain matches the row's grain exactly, and the CAS can become a
  **single statement** that carries the token match:
  `UPDATE … WHERE version_id=? AND state='pending_approval' AND assent_token=? AND assent_consumed_at IS NULL`.
  Exactly-once then falls out of the statement itself.
- Downside 1: new columns on an **existing** table REQUIRE an `_ensure_postgres_columns` entry
  (the `artifact_class` pair, migration `20261014_013702_alter_prompt_versions_add_artifact_class.sql`
  + the manager entry, is the template) → the one-transaction boot-abort surface.
- Downside 2: `prompt_versions` is the **version-history table**; an approved row is terminal
  and append-only. Token columns on a terminal row are meaningless but must be kept (or
  nulled) — the token lifecycle (mint → attempts → consume → GC) does not match the version
  lifecycle (draft → pending → approved).
- Downside 3: `prompt_versions` is the hottest table in the prompt system (loader reads it on
  every persona compose). Extra nullable columns are harmless, but extra *mutations* on a
  pending row (the mint write) put a write on a row the FE also mutates (content PATCH is
  allowed on draft/pending rows).

**(c) In-memory store** — the `_RepairNonceStore` pattern (`daemon/tools/ens_db_tools.py:498-575`,
VERIFIED: TTL 300 s, `threading.Lock`, refusal vocabulary `nonce-mismatch` /
`nonce-instance-mismatch` / `nonce-action-mismatch` / `nonce-already-used` / `nonce-expired`).

- Cheap, race-free, and its **refusal-token vocabulary is the most valuable inheritance** in the
  whole file. But it is **RAM-only**: it dies with the daemon. The promote nonce is
  disk-persisted for exactly that reason (`upgrade_journal.py:1898-1899`: *"survives
  MessageQueue wipe + daemon death"*). A prompt approval is a *state change to the live
  persona* — losing the token on a daemon restart means the Emperor re-mints. Acceptable but
  inferior.

**(d) The install-journal file store** (`upgrade_journal.py:1986-1994`, `releases/state.json`).
**Rejected**: wrong medium. It lives under the install dir, is owned by the release pipeline,
and would couple a prompt tool to release machinery. (`trust-anchors.md` §2 is right about this.)

**(e) `shared_meta_kv`.** Rejected as the token store (generic KV, read-modify-write, no
per-row CAS for the consume). It remains acceptable as the *kill-switch / config* channel only.

### Trade-offs that actually decide it

- **Restart semantics mid-TTL.** (a) survives; (c) does not. A 60-minute token is long enough
  that a daemon restart during the Emperor's deliberation is a *routine* event, not an edge
  case.
- **Multi-daemon reality.** The project runs **multiple ensemble instances across remote VMs**
  (project metadata: `upgrade_workflow_preference`). Those are separate deployments with
  separate DBs — but a PG-backed store is the only option that is *correct* if a deployment
  ever runs two daemon processes against one PG. (c) and (e) are silently wrong there.
- **GC.** The promote GC "only deletes what it can prove is dead" (`upgrade_journal.py:1926-1958`)
  and **keeps one consumed entry** so "already used" stays answerable (`:1931-1934`). A PG store
  must carry the same rule: **retain consumed rows** (they answer `token-already-used`); sweep
  only rows that are expired AND consumed AND older than a retention floor.
- **The boot-abort caveat** (`manager.py:10036-10038`) applies to (b) only — and it is a
  *boot-time* failure, i.e. the worst failure mode available. The upgrade blueprint's halt
  taxonomy treats a boot abort as a live-rung incident.

### RECOMMENDATION

**Option (a) — a new PG table `prompt_assent_tokens`.** Rows:

```
token_id          PK (uuid4 hex)
version_id        FK-ish (no hard FK — mirror the audit table's deliberate no-FK rule,
                  models.py:464-466: the audit must survive row cleanup)
agent_id, version_tag, artifact, version   (the item binding — replaces the promote's
                  issued_to_instance, which is meaningless per-item)
token_hash        sha256(token) — NEVER the raw token
token_display     the grouped rendering shown to the Emperor
issued_at, ttl_expires_at, superseded_at, consumed_at, consumed_by_message_id
attempts          failed-attempt counter
status            live | consumed | superseded | revoked
```

One row per mint; **re-mint supersedes** (see §2). The raw token never leaves the row and
never enters an audit payload — only `sha256(token)[:12]`, mirroring the content-free
`body_sha256` discipline (`notify_audit/models.py:127-129`).

**Explicitly rejected:** (b) — it buys a one-statement CAS at the price of a boot-abort
surface on the hottest prompt table, for a benefit the state machine already gives us
(see §2's single-use analysis). (c) — RAM-only across a 60-minute human deliberation window.
(d) — wrong medium. (e) — wrong semantics for a consume.

---

## §2 — Q2. Token semantics (TTL, re-mint, throttling, single-use)

### TTL

**Options.** 60 min (commission's "~60 min"); 30 min; 15 min; 5 min
(`REPAIR_NONCE_TTL_S`, `ens_db_tools.py:177`).

**House value:** `NONCE_TTL_S = 60 * 60` (`upgrade_journal.py:141`, VERIFIED — *"60min; widened
from 15 min per user decision 2026-09-30, ADR-036"*). `manager.py:6130` computes the
factor-2 window's `expires_at` with the same constant, and `manager.py:270-272` mirrors it as
`PROVENANCE_TTL_S = 60*60`.

**Important precision — the two clocks are INDEPENDENT.** The factor-2 window is stamped when
the **reply** message is dispatched (`manager.py:11489-11505`, the single dispatch funnel), not
when the token is minted. So the token TTL governs "how long the Emperor has to reply", and the
window TTL governs "how long the reply's authorization lives". They need not be equal.

**RECOMMENDATION: 60 minutes**, reusing `upgrade_journal.NONCE_TTL_S` as the SSOT (import it;
do not declare a new constant). Matching the house value is free, coherent with ADR-036, and
long enough for "show diff → Emperor reads → replies" without forcing a re-mint. A shorter TTL
(15 min) increases re-mint friction; a longer TTL widens the window in which a stale token can
be echoed.

### Re-mint

**Options.** (i) old token burns immediately; (ii) old token stays valid until TTL; (iii) old
token is **superseded** (invalid for use, but distinguishable from "used").

**RECOMMENDATION: (iii) supersede.** Rationale: "burn" and "supersede" behave identically for
authorization, but they mean different things in the audit and produce different refusal
messages. A token the Emperor chose *not* to use (re-minted because it expired or because he
asked for a fresh diff) must not read as `token-already-used` — that would make the audit say
"he approved with it and we consumed it". Superseded tokens get their own refusal
(`token-superseded`) and their own status.

**Re-mint must re-show the diff.** The commission says Ari "shows the Emperor with the diff".
A re-mint without the diff would present a bare token — the Emperor has no way to know what he
is authorising. So the mint response carries: item identity (agent / artifact / version), the
**diff** (pending content vs effective content), the token (grouped), and the expiry. The diff
is rendered by the **tool**, from `repo.get_effective(agent_id, version_tag, artifact)`
(`repository.py:1900-1945`) vs the pending row's `content` — the same pair the REST diff route
uses (`prompts.py:560-600`, `against=effective`).

### Entropy and attempt throttling

The commission's "~6-char" is **30 bits** (`_NONCE_ALPHABET` is base32 `A-Z2-7`,
`upgrade_journal.py:172`; 6 chars = 32⁶ ≈ 1.07e9). The house nonce is **8 chars = 40 bits**
(`mint_nonce`, `:205-208`).

**But the token is NOT the only secret — and that changes the calculus.** The commission's
three factors are conjunctive:

1. the tool call carries `user_confirmed=true` + the token;
2. the turn must be a **genuine user turn** (factor-2 window);
3. the **content of that specific message** must carry the token (factor 3).

An attacker who guesses the token still cannot use it unless they can also (i) cause the daemon
to process a message from a registered user-origin source, and (ii) put the guessed token in
that message's content, and (iii) have the tool call occur in the *same turn*. Agent-originated
messages carry `source=f"internal_agent:<caller>"` and are **cleared** by the stamp site
(`manager.py:6132-6133`), so an agent that injects a message cannot arm factor 2. The effective
attacker is therefore *already inside* the human's chat channel — at which point the token is
not the binding constraint.

**Consequence:** the token's real job is **anti-confusion** (binding one human reply to one
specific item), not anti-brute-force. That argues for keeping it short (humane to copy) *and*
keeping a defence-in-depth attempt counter.

**RECOMMENDATION:**
- **Token length: 8 base32 chars (40 bits), reusing the house `mint_nonce` alphabet and
  display shape.** It costs *nothing* operationally (Ari shows it in a code block; the Emperor
  copies it — the promote nonce is 8 chars too and is grouped as `CONFIRM-XXXX-XXXX`), and it
  removes the brute-force conversation from the review entirely. **If the Emperor insists on a
  short code, 6 chars is acceptable *only* with the counter + lockout below** — flagged as
  **DECISION D-3**.
- **Per-item attempt counter**: every token mismatch on an item increments `attempts` on that
  item's live token row. Refusals **never burn** the token (house doctrine:
  `upgrade_tools.py:3708-3777` *"nonce NOT burned"*).
- **Lockout, not backoff.** After **5** mismatched attempts on an item, the live token is
  **revoked** (`status=revoked`) and the item requires a fresh mint. Rationale: backoff needs a
  per-item clock and an exponential state machine; a lockout is one counter, one threshold, one
  terminal state, and it fails closed. The lockout also forces a **new human turn** to clear
  (the re-mint), which is the correct reaction to a guessing loop.
- **Global bound**: also cap attempts per *relay instance* (e.g. ≤10 mismatches/hour) so a
  many-item guessing sweep is bounded even when each individual item is under its threshold.

### Single-use enforcement point

**Options.** (i) consume on any attempt; (ii) consume only on a **successful** transition;
(iii) consume inside the same transaction as the state change.

**Key insight that simplifies the design: single-use is already structurally guaranteed by the
state machine.** `atomic_transition`'s CAS is `UPDATE … WHERE version_id=? AND state='pending_approval'`
(`repository.py:1459-1464`). After a successful approve, the row is `approved` — terminal. A
second approve of the same row raises `InvalidTransitionError` → `409 ALREADY_APPROVED`
(`prompts.py:653-654`). A rejected row is likewise terminal. So the token's single-use property
is a **secondary** guard against token reuse across *different* items (handled by the item
binding) and against a confused-deputy replay (handled by the CAS).

**RECOMMENDATION: (ii)+(iii) — transition first, consume second, and prefer same-transaction.**

- **Transition-then-consume** is the safe ordering: if the transition fails, the token is
  untouched (a refusal must not destroy the Emperor's token because of a typo in the item id
  or a transient DB error — the house rule *"refusals never burn"*). If the consume then fails,
  the worst case is a **live token on an already-decided row** — and that token is powerless,
  because the CAS rejects the re-apply. Benign.
- **Consume-then-transition** is the unsafe ordering: a transition failure after a successful
  consume burns the token on a non-decision.
- **Same-transaction is preferable** (the audit row already is: `repository.py:1493-1511`),
  but `atomic_transition` opens its own `engine.begin()` (`repository.py:1457`), so the consume
  can only join it by extending that method with an optional guard hook. That is a change to a
  **core** method — worth it only if the architect accepts that; see **DECISION D-4**.

---

## §3 — Q3. Kill-switch default (fail-open vs fail-closed)

**House precedent, VERIFIED:**
- `NotifyUserConfig.enabled` → **default `True`** (`daemon/config.py:3181-3187`; the
  `ENSEMBLE_NOTIFY_USER_ENABLED` knob). Read at **call** time; the tool stays registered under
  any switch state (`prompt_reload.py:25-28`, `:76-91` — *"the surface never shrinks with the
  switch"*).
- `PromptReloadConfig` → same shape, **default ON**, and **fail-closed on indeterminate
  values** (the registry comment at `_tool_registry.py:740-750`: *"fail-closed on indeterminate
  values (DR-3)"*).
- The kill-switch is **not** the outermost gate: `notify_user.py:703-715` puts the **agent
  gate first** (an unauthorised caller learns nothing about the knob's state) and the
  kill-switch second.

**The trust-model-change argument.** This lane is categorically different from `notify_user`:
`notify_user` writes to a chat channel; `prompt_assent` **changes the effective prompt** of a
live agent — including, in principle, Ari's own `soul`/`rule`/`workflow`. That is a
privilege-escalation surface, and the usual default for such surfaces in this codebase has been
**opt-in** ("never default-granted — opt-in-only", `_tool_registry.py:449-452`).

**But** default-OFF/ON here is *not* the security boundary, because the lane is already behind
four independent gates: (1) the privileged-category strip (never default-granted — opt-in via
`agents/ari/meta.json`), (2) the in-body `agent_id == "ari"` gate, (3) the genuine-user-turn
window, (4) the message-content token echo. Default-OFF adds a **fifth** gate that protects
operators who have not deliberately armed the lane — it does not make an unarmed lane unsafe.

**Options.**
(a) **Default ON** (house precedent: notify_user, prompt_reload, both default ON). The lane is
    inert until a human actually says the token in a registered chat channel.
(b) **Default OFF** — fail-closed; requires an explicit `ENSEMBLE_PROMPT_ASSIST_ENABLED=1` per
    environment. Strongest "no surprise on promote" posture; costs one operator action per
    environment before the feature works.
(c) Default ON but **armed per-deployment by a separate "arm" record** — over-engineered for
    this surface.

**RECOMMENDATION: (a) default ON**, mirroring the two closest precedents, **with two
conditions**:
1. the switch is read **at call time** and the tool **stays registered** when off (refusal-at-call,
   never construction-absence) — `prompt_reload.py:25-28`;
2. the gate ORDER is: **ari gate → kill-switch → 3-factor gates → mutation** (`notify_user.py:703-715`),
   so an unauthorised caller cannot probe the knob's state.

**Honest counter-argument for the architect:** this lane is the first one in the tree that can
change a live agent's *own* guardrails through a chat message. If the Emperor wants the lane
dormant until he has used it once, option (b) costs one env var and buys that assurance.
**Flagged as DECISION D-2** — the commission asked for it explicitly.

---

## §4 — Q4. Audit payload shape

**Today's APPROVED/REJECTED payload** (VERIFIED, `repository.py:1493-1501`):

```json
{"from_state": "pending_approval", "to_state": "approved",
 "approver_subject": "<AuthContext.subject>", "approver_auth_method": "session"}
```

Written by `_append_audit_in_conn` **inside the same `engine.begin()` as the state UPDATE**
(`repository.py:1457-1511`) — the audit and the transition commit together, or not at all.
The `prompt_audit_events` table has **no FK to prompt_versions** on purpose (`models.py:464-466`:
*"audit survives row cleanup"*).

### Options

**(A) Payload-only JSONB extend — no schema change.**
- The event-type vocabulary is a **code-level frozenset** (`models.py:134-147`), so adding a
  type is additive with **no migration**; the models prose records that precedent twice
  (`models.py:120-133`: *"Code-level frozenset only: no DB schema change"*).
- The payload is already a heterogeneous blob by design (`models.py:532-536`: *"JSONBType blob —
  transition-specific context"*); `TEMPLATE_RESET` already carries
  `(old_hash, new_hash, selection_preserved)` (`models.py:130-133`).
- Cost: ~4 files (repo payload assembly, repo entrypoints, service, tool). Zero migration.
- Query cost: filterable only by JSON containment (no dedicated index).

**(B) New columns on `prompt_audit_events`** — migration + model + **`_ensure_postgres_columns`
entry** (existing table!) → the one-transaction boot-abort surface (`manager.py:10036-10038`).
Heavier, and buys native filtering.

**(C) A separate relay-audit table** (like `notify_audit`) — a second write path, two sources of
truth for "was this approved?", and the risk of a relay row existing without its state row (or
vice versa) if the two writes are not in one transaction.

**Event type: keep `APPROVED`/`REJECTED`, or mint `ASSENT_APPROVED`/`ASSENT_REJECTED`?**
This is a real fork with a real failure mode on both sides:
- *New type*: queryable via the existing `(event_type, created_at)` index (`models.py:478-500`);
  "how many chat approvals?" is a one-liner. **But** any existing consumer that filters
  `event_type == "APPROVED"` to build an approvals history will **silently drop** relayed
  approvals — a *forgetting* bug in the audit trail, which is the worst kind of bug here.
- *Same type*: every existing view stays complete; the lane is distinguished by a payload key
  (`approval_path: "chat-assent"` vs `"fe-session"`). Cost: JSON containment for lane-scoped
  queries.

### RECOMMENDATION

**(A) payload-only extend + KEEP the existing event types (`APPROVED` / `REJECTED`).**

Rationale: no schema change → no migration → no `_ensure_postgres_columns` entry → **no
boot-abort blast radius**; the append-only history stays complete for every existing consumer;
and the lane discriminator is one payload key.

**Proposed payload shape (relay path only):**

```json
{
  "from_state": "pending_approval",
  "to_state": "approved",
  "approver_subject": "discord:112233445566",
  "approver_auth_method": "assent-relay",
  "approval_path": "chat-assent",
  "relay_agent": "ari",
  "relay_instance_id": "<uuid>",
  "relay_source_id": "my-discord-bot",
  "relay_external_user_id": "dm:112233445566",
  "relay_user_id": "112233445566",
  "relay_guild_id": null,
  "relay_channel_id": "999888777",
  "relay_message_id": "<uuid>",
  "relay_window_stamped_at": "2026-11-10T19:00:00Z",
  "relay_window_expires_at": "2026-11-10T20:00:00Z",
  "assented_at": "2026-11-10T19:03:45Z",
  "token_id": "<uuid>",
  "token_fingerprint": "e33c34f5af42",
  "reject_reason": "…"
}
```

- **`token_fingerprint` = `sha256(token)[:12]`, never the raw token** — the content-free
  discipline (`notify_audit/models.py:127-129`, `body_sha256` + `body_length` only).
- **`reject_reason`**: the commission wants "reject with reason". **`repo.reject` has no reason
  parameter** (VERIFIED: `reject(self, version_id, actor)`, `repository.py:1577-1607`) and the FE
  reject carries **no body** (`_SubmitBody` is deliberately empty, `prompts.py:318-322`). There is
  **no `reject_reason` column** on `prompt_versions` (model column list VERIFIED at
  `models.py:331-436`). So the reason lives **only in the audit payload** — which is fine, because
  the audit is append-only. Recommendation: put it at the **top level** of the payload (it is a
  property of the decision, not of the relay), populated only by the relay lane for now; the FE
  lane can adopt the same key later without a schema change.
  *Alternative considered:* nesting it under `relay.reason` keeps the payload purely
  relay-shaped but makes "why was this rejected?" require relay-aware readers. **Top level
  wins.**

**Never-overwrite guarantee.** `prompt_audit_events` is append-only: `_append_audit_in_conn`
INSERTs (`repository.py:1502-1511`, impl `:3036+`) and nothing in the tree UPDATEs or DELETEs an
audit row. The relay lane adds keys to a **new** row; it never touches an existing one.
**Recommendation:** add a grep-verifiable pin to the lane's test pack — *"no UPDATE/DELETE
against `prompt_audit_events` anywhere in the prompt-assent lane"* — because "NEVER overwrite
existing events" is an invariant that only a pin keeps.

---

## §5 — Q5. Approver identity vs F4 (the core design question)

### The exact gate inventory (all VERIFIED in the commissioned worktree)

| # | Layer | Anchor | What it says |
|---|---|---|---|
| 1 | Lane vocabulary | `repository.py:503-511` | `_AUTH_METHODS = {"session","bearer","internal"}` — a **frozenset of exactly three** |
| 2 | Actor construction | `repository.py:551-566` | `ActorContext.__post_init__` raises `ValueError` for any lane outside `_AUTH_METHODS`; `internal` additionally requires `instance_id` **and** `agent_id` |
| 3 | Anti-forgery | `repository.py:593-642` | `actor_from_request` reads **only** `request.state.auth_context`; body-asserted actor fields are IGNORED (R3, `:602-604`); it **refuses** `internal` as a forgery attempt (S16, `:624-629`) and refuses unknown lanes (`:630-633`) |
| 4 | F4 | `repository.py:1560-1565` / `:1594-1599` | `approve()`/`reject()` refuse `actor.is_internal()` — *"agents cannot self-approve"* |
| 5 | Identity clamp | `repository.py:1433-1444` | terminal transitions require non-empty `approver_subject` **and** `approver_auth_method`, and `approver_auth_method ∈ {session, bearer}` — *"the repo refuses to invent approver identity"* |
| 6 | Service lane | `prompt_version_service.py:677-717` | `auth_method != "session"` → `HumanLaneRequiredError` — *"lane discriminator beats scope"* |
| 7 | Router 403 | `prompts.py:639-640` / `:680-681` | maps the typed error to `403 HUMAN_LANE_REQUIRED` |

**Consequence:** an Ari-initiated approval has `auth_method == "internal"` and is refused at
layers 4, 5 and 6. The lane must obtain a *name* that is not `internal` — and that name must
not be `session` either (that would forge the FE lane and silently defeat the whole exercise).

### Options

**(a) A new lane value `assent-relay` + a dedicated actor constructor + an attestation-bearing
repo entrypoint.**
Touch points: `AUTH_METHOD_ASSENT_RELAY` constant + `_AUTH_METHODS` (layer 1);
`ActorContext.__post_init__` gains a branch requiring the attestation fields (layer 2);
a new `actor_from_assent_relay(...)` — the **only** constructor that may mint the lane (layer 3);
the `atomic_transition` clamp widened to include it (layer 5); a new repo entrypoint that
**requires** the attestation (layer 4's spirit); the service lane predicate widened (layer 6).
The existing `approve()`/`reject()` keep their exact behaviour — **FE unchanged**.

**(b) HTTP round-trip** — the tool synthesises a session `AuthContext` and calls
`POST /api/prompts/versions/{id}/approve`. **Rejected outright**: that is precisely the forgery
layer 3 forbids (S16), it makes the audit record a fake `approver_auth_method="session"`, and it
would mean the chat lane is indistinguishable from the FE lane in the ledger — the opposite of
the commission's goal.

**(c) A new repository method with its own F4 rule** (e.g. `approve_assent(version_id, attestation)`),
leaving `approve()` untouched. **This is orthogonal to (a), not an alternative to it** — but note
that (c) alone is insufficient: `atomic_transition`'s clamp (layer 5) still has to be widened,
because that is where `approver_auth_method` is validated.

### RECOMMENDATION — (a) + (c) combined

**A new lane value `assent-relay`, a dedicated constructor, and a NEW repo/service entrypoint
that requires a typed `AssentAttestation`.**

**How F4's spirit survives — four properties:**

1. **The tool args can never carry the approver identity.** The tool signature accepts only
   `(version_id, action, token, user_confirmed, reject_reason)`. The *approver* is derived
   entirely from **daemon-owned** evidence: the message row (`source`, `message_metadata`) and the
   source registry. Ari physically cannot assert "the Emperor is X" — it can only forward a
   token that only the Emperor's turn could have produced.
2. **The attestation is not constructible from arguments.** `AssentAttestation` is produced by
   the assent service **only after** the three-factor gate passes; it carries
   `{item_binding, human_message_id, human_identity, source_id, external_user_id, token_id,
   window_stamped_at, window_expires_at, relay_instance_id, attested_at}`. The repo entrypoint
   **refuses** an assent-relay actor whose attestation is absent or incomplete → a lane that
   cannot be driven without the full triple.
3. **`approve()` and `reject()` are unchanged.** The FE path keeps its exact semantics: session
   only, `internal` refused, `bearer` refused. No existing test or consumer changes meaning.
4. **The lane value is distinguishable in the ledger.** `approver_auth_method = "assent-relay"`
   (12 chars; `approver_auth_method` is `max_length=32`, `models.py:409-415`; the audit column is
   also 32, `models.py:524-531` — **no CHECK constraint** on either column; the clamp is
   Python-side only, VERIFIED). So every relayed approval is visible as such.

**Where the genuine user turn binds to the specific approve call:**
- The window is read **at tool-call time** from `manager._user_origin_windows[current_instance_id]`
  — the exact read the promote gate performs (`upgrade_tools.py:3388-3391`). The window is
  stamped at the **single dispatch funnel** (`manager.py:11489-11505`) and **cleared** on any
  non-user-origin dispatch (`manager.py:6132-6133`), so it is strictly per-turn:
  *"an agent/internal-originated turn must never inherit an earlier turn's authorization"*
  (`manager.py:1996-1999`).
- The window's `message_id` selects the **exact** message whose content must carry the token
  (factor 3, `upgrade_tools.py:3508-3531`) — message-exact, not turn-fuzzy.
- The item binding is carried by the **token row** (`version_id`, `agent_id`, `version_tag`,
  `artifact`, `version`) — replacing the promote's `issued_to_instance` binding, which is
  meaningless for a per-item token (`trust-anchors.md` §2 "What does NOT scale down" #3).
- The audit payload records all four anchors together, so the ledger can reconstruct the chain
  *token ↔ item ↔ message ↔ human ↔ relay agent*.

**What stamps the approving MESSAGE id:** the tool copies `window["message_id"]` into the
attestation and the audit payload. That id is daemon-minted at the dispatch funnel — never
tool-supplied.

**What stamps the approving USER identity:** the message row's `source` column is
`"source_id:external_user_id"` (`daemon/sources/dispatcher.py:531-537` — *"Parse source as
`source_id:external_user_id`"*), and the adapter's `message_metadata` carries the author identity.
**VERIFIED for Discord** (`daemon/sources/adapters/discord/adapter.py:1035-1058`): the metadata
carries `user_id`, `user_name`, `user_display_name`, `guild_id`, `channel_id`, `parent_channel_id`.
**Important wrinkle:** Discord's canonical `external_user_id` is `dm:<user_id>` in DMs but
`<guild_id>:<channel_id>` in a guild channel (`adapter.py:994-1021`) — i.e. **in a guild channel
`external_user_id` identifies a CHANNEL, not a user**. Therefore:
- the **approving user** must come from the adapter metadata (`user_id`), not from
  `external_user_id`, for guild channels;
- `external_user_id` remains the correct **destination** identity (it is what `notify_user`
  hashes into `target_hash`).
**Recommendation:** `approver_subject` = `f"{source_id}:{user_identity}"` (e.g.
`discord:112233445566`), with `relay_external_user_id`, `relay_guild_id` and `relay_channel_id`
recorded separately in the payload for forensics. **Per-source extraction rules are a DECISION
(D-5)** — the commission says "e.g. my-discord-bot", so the extraction must be explicit about
which sources are in scope and what the user key is for each.

**The one honest limitation** (state it plainly in the plan): an agent with in-process code
execution (`bash`) can import the attestation module. That is true of **every** in-process
authority in this tree (`actor_from_instance` is equally importable). The boundary this design
relies on is the **three-layer access posture** (privileged category + per-agent allow + in-body
`agent_id` gate), not "the agent cannot import the module". The lane's *specific* hardening is
that the attestation **cannot be minted from tool arguments** — which closes the realistic
failure mode (Ari hallucinating an approval).

**A residual the architect must rule on — window staleness.** The window is **in-memory**
(`manager.py:2001`) and the dispatch funnel **skips the stamp on a silent resume**
(`manager.py:11503`: `if not silent:`) — *"Silent resume is the ONLY skip (no message is
injected; the window keeps the original turn's)"*. So a paused→resumed Ari can hold a window
stamped in an **earlier** turn. The promote gate accepts any unexpired window (60 min). For a
lane that can change a live persona this is tighter than we want. **Recommendation:** require
`window["stamped_at"]` to be within a short freshness bound (e.g. **10 minutes**) of the tool
call, in addition to `expires_at` — i.e. a *fourth*, cheap check on top of the commission's
three factors. **DECISION D-6.**

---

## §6 — Q6. Router integration

### The surfaces (VERIFIED — and they are NOT the same stack)

| Surface | Route | Service/Repo | Table |
|---|---|---|---|
| **Classic prompt versions** | `prompts.py:616-697` (approve / reject) | `svc.approve/reject` (`prompt_version_service.py:435-504`) → `PromptVersionRepository.approve/reject` (`repository.py:1541-1607`) → `atomic_transition` (`:1356+`) | `prompt_versions` |
| **v2 persona lane** | `memoir.py:143-208` (approve / reject) | **no service** — `asyncio.to_thread(repo.approve, …)` → `MemoirPersonaRepository.approve/reject` (`daemon/repositories/memoir/repository.py:220-237`) → its **own** `_require_human_lane` (`:243-262`) | `memoir_persona_versions` |
| custom agents | **no approve/reject exists** (`approval-path.md:40-41`) | — | — |

The v2 persona lane's gate is the house `HumanLaneRequiredError` imported from the service
layer (`memoir/repository.py:254`) with the same `auth_method != "session"` equality
(`:256-261`). Its audit shape is **different**: persona rows carry `approved_at` + a `status`
vocabulary, and they do **not** write `prompt_audit_events`.

**So "both surfaces gain this lane" means two repositories, two tables, two lane predicates.**

### Where the acceptance path should plug in

- **NOT in the router.** The FE routes must stay byte-identical (*"FE unchanged"* in the
  commission). They keep: `_require_auth_on` → `require_scope("prompts:approve")` →
  `actor_from_request` → `svc.approve` → 403 on non-session.
- **AT the service/repo level**, as a **second accepted approval path** alongside the session
  path. The F2 lesson is explicit: any new acceptance path must go through the **service**
  method, never raw `svc.atomic_transition` (`prompts.py:747-753` — the retract bypass that
  was the F2 finding).
- **The relay path never goes through HTTP at all.** It is an **in-process tool** calling the
  service directly — exactly the `inner_soul` pattern (`_run_coro_sync`, `inner_soul.py:1250`;
  `_get_prompt_version_service`, `:1273-1283`). Benefits: no AuthContext forgery, no new public
  endpoint, no scope plumbing, no HTTP round-trip latency, and the tool is already inside the
  daemon process so the manager handles (`_queue_repository`, `_user_origin_windows`,
  `_source_repository`) are directly reachable.

**Shared lane predicate vs route duplication:** recommend a **single shared predicate** —
`is_acceptable_approver(actor, attestation)` — implemented once in the service layer and
consulted by both surfaces, so the "who may approve, and on what evidence" rule exists in ONE
place rather than being re-implemented per route. The router never calls it directly (the
router has no attestation).

### Scope recommendation

**Ship surface (i) first; treat (ii) as a separate phase.** Reasons: different table, different
repository, different audit shape (persona rows have no `prompt_audit_events` ledger), and a
different state vocabulary (`status` vs `state`). Doing both in one change doubles the blast
radius of a *trust-model* change for no functional gain — the commission's goal is served by
(i) alone. **DECISION D-7** (the architect may want both in one release).

---

## §7 — Q7. List surface for the ari-facing pending view

**Current state (VERIFIED):** there is **no** global pending-enumeration for `prompt_versions`.
`GET /api/prompts/versions?state=pending_approval` (`prompts.py:444-541`) requires a full
`(agent_id, version_tag, artifact)` key and returns an **empty list** if any part is missing
(`:500-507`); `state` is a **router-side post-filter** (`:526-527`); `repo.list_versions` has
no state filter (`repository.py:1843-1866`). `GET /api/prompts/pending-count`
(`prompts.py:1078-1100`) is a **scalar** (`repo.count_pending`, `repository.py:2999-3029`,
CORE-only predicate `state='pending_approval' AND artifact_class='CORE'`).

### Options

**(a) `repo.list_pending()` + `svc.list_pending()` + `GET /api/prompts/pending`.**
3 precedented touch points (the memoir `list_pending` at `memoir.py:130`; `count_pending` at
`repository.py:2999`). Adds a **public HTTP surface** (route + scope `prompts:read` +
`_require_auth_on`).

**(b) An ari-facing READ tool** over `repo.list_pending()` + `svc.list_pending()`, using the
`inner_soul` plumbing (`_run_coro_sync` `:1250` + `_get_prompt_version_service` `:1273-1283`).
No router/DB-surface change; works in-process; returns exactly the rows Ari shows the Emperor.

**(c) `shared_meta_kv` snapshot** — *"cheapest to READ, most expensive to keep TRUE"*
(`approval-path.md:128`). The approve/reject path runs through the HTTP service, so the KV write
hooks would have to land in `svc.approve`/`svc.reject` **and** in the repo path the tool calls
directly — two hook sites, one of which is a service bypass. **Drift risk is real: a stale
pending list means the Emperor approves a row that has already moved.**

### RECOMMENDATION — (b), built on the repo/service primitives (a) also needs

**Add `repo.list_pending()` + `svc.list_pending()`; expose them to Ari through a READ TOOL,
not a route.**

- The repo method is needed **regardless** — the mint step needs the item's key
  (`agent_id`/`version_tag`/`artifact`/`version`) and the tool needs to show the Emperor what is
  pending. Clone `count_pending`'s CORE-only predicate (`:3024-3027`) and return rows ordered by
  `created_at`.
- The **tool** beats the route for this consumer: no new public endpoint, no scope plumbing, no
  auth-gate ceremony — and it composes with the mint + diff in one response.
- **Do NOT agent-filter the list.** The memoir precedent filters `agent_id="ari"`
  (`memoir.py:130`), but the relay lane must be able to approve a pending row for **any** agent
  (worker, designer, …). `list_pending(agent_id: str | None = None)` → all CORE pending rows,
  bounded (e.g. limit 50).
- **Rejected: (c).** Drift in a security-relevant list is the wrong kind of cheap.

---

## §8 — Q8. Tool surface

### Category / tool naming — one string, everywhere

- **Category key: `prompt-assent`** (kebab-case, byte-identical in the module, the registry, the
  `CATEGORY_MODULES` map, `agents/ari/meta.json`, the metrics vocabulary, and the runbook).
  House pattern: `notify-user` → `notify_user`, `prompt-reload` → `prompt_reload`.
- **Tool name: `prompt_assent`** (snake_case).
- **⚠ FLAG:** `research/trust-anchors.md` uses **"prompt-assist"** in §4.5, §5 and §7 (items 8
  and 12) and titles the file "prompt-assist". The commission, the worktree path, the planning
  dir and the KV rows all use **"prompt-assent"**. **The category key must be ONE string** —
  recommend `prompt-assent`, and the research note's spelling is a carry-over error to fix in
  the plan (it is also the reason the note's §0 warns about a deleted worktree).

### Shape: one mutator + one reader, or one tool with three actions?

The commission suggests **one** tool (`prompt_assent`) that "acts on a PENDING row by id" and
also mentions a **LIST companion** and a **mint** step (Ari "shows the Emperor with the diff",
the token is "re-mintable").

**Options.**
1. One tool, `action ∈ {mint, approve, reject}`.
2. Two tools: `prompt_assent` (mutator: approve|reject) + `prompt_assent_pending`
   (reader: list + diff).
3. Three tools (add a dedicated mint tool).

**RECOMMENDATION: option 1 — one tool, three actions.** Reasons:
- it preserves the commission's "a NEW privileged ari-EXACTLY tool" (suggested `prompt_assent`)
  literally;
- `mint` is a **state-changing** operation (it writes a token row and supersedes any prior
  token), so it deserves its own audited action rather than being an implicit side-effect of
  "listing";
- a `list` action on the same tool keeps the response shapes unified (one envelope, one refusal
  vocabulary, one INFO line);
- the refusals of `mint` are naturally distinct from the refusals of `approve` (a mint cannot
  fail on "wrong token" — it cannot fail on the item being non-pending only).

**Trade-off noted:** if the architect wants the read capability separable from the mutate
capability, they must be **two categories** (per-agent allow is per-category) — one category
grants both. Since the only consumer is ari, one category is right. If a second agent ever needs
read-only access, split the category then, not now.

### Tool arguments

```
prompt_assent(
    action: Literal["mint", "approve", "reject"],   # required
    version_id: str,                                 # required for approve/reject; optional for list
    token: str | None = None,                        # required for approve/reject
    user_confirmed: bool = False,                    # required True for approve/reject
    reject_reason: str | None = None,                # required (non-empty) for reject
    limit: int = 25,                                 # list only
)
```

**Hard rule (the anti-forgery line):** the args carry **no** approver identity, **no** source,
**no** message id, **no** user id. Those are derived from daemon-owned state. This is the
analogue of the repo's R3 rule (*"Body-asserted actor fields are IGNORED"*,
`repository.py:602-604`).

### Refusal taxonomy

House envelope shapes: `REFUSED` / `FAILED` (`notify_user.py:314-321`), plus the generic
**not-ari** envelope (`notify_user.py:703-711`) which deliberately carries **no refusal code**
(AM-6). The promote lane's vocabulary is the model for the 3-factor refusals
(`upgrade_tools.py:3892-3904`).

| Code | Trigger |
|---|---|
| *(not-ari)* | `agent_id != "ari"` — generic ERROR envelope, no code, no ledger noise |
| `lane-disabled` | kill-switch off (notify precedent's `notify_disabled`) |
| `user-confirmation-missing` | `user_confirmed != true` (promote factor 1) |
| `no-user-origin-turn` | no window, or the observed source is not user-origin — the refusal must NAME the observed source + classifier detail (the W1 hardening, `upgrade_tools.py:3393-3419`) |
| `user-origin-window-expired` | window `expires_at` unparseable/unexpired — fail-closed (`upgrade_tools.py:3421-3432`) |
| `user-origin-window-stale` | *(the §5 freshness bound — the extra fourth check; DECISION D-6)* |
| `assent-verification-unavailable` | the MQ row for `message_id` could not be read → fail closed (promote's `nonce-verification-unavailable`, `:3524-3530`) |
| `token-mismatch` | unknown token |
| `token-expired` | TTL elapsed |
| `token-already-used` | consumed |
| `token-superseded` | re-minted |
| `token-item-mismatch` | token bound to a different item |
| `token-locked` | attempt threshold exceeded |
| `item-not-found` | no such version_id |
| `item-not-pending` | the row is not in `pending_approval` — include the current state |
| `item-not-assentable` | MEMORY-class rows are transition-free (`models.py:158-166`); the repo refuses via `_refuse_if_memory` (`repository.py:1421-1427`) |
| `already-decided` | the row is approved/rejected — the `409 ALREADY_APPROVED` analogue |
| `self-approval-attempt` | belt-and-braces: the attested human identity must not resolve to the relay agent or to a reserved agent identity. **Note: this is largely UNREACHABLE by construction** — an agent-originated message cannot arm factor 2 — so it is an assertion, not a load-bearing gate. Flagged so the plan does not mistake it for the real defence. |
| `audit-unavailable` | the audit/ledger write failed → **fail closed** (notify precedent: *"ledger-write failure ⇒ fail-closed"*, `notify_user.py:95-97`) |

**Two house doctrines to inherit verbatim:**
- *"Never auto-retry a refusal — relay it verbatim"* (`upgrade_tools.py:3906-3907`).
- the **ONE frozen INFO line** on every terminal path (`prompt_reload.py:113-135`) — counts/flags
  only, **never persona or diff content** (matches the "logs content-free v1" doctrine in the
  project notes).

### How the LIST companion integrates

As the `action="list"` branch of the same tool (recommendation above) — it returns the pending
rows (id, agent, artifact, version, submitted-at) **without** minting. Minting is explicit
(`action="mint"`), which keeps "show me what's pending" side-effect-free.

---

## §9 — Q9. Registration / pin checklist (build checklist)

### Code sites

| # | Site | Detail |
|---|---|---|
| 1 | `daemon/tools/prompt_assent.py` **(NEW)** | `@register_tool_category("prompt-assent")` **ABOVE** `@tool` — decorator order is load-bearing (the strip reads the **wrapped** object; `_tool_registry.py:776-800`; pin: `tests/unit/test_notify_user_registration.py:108-131`) |
| 2 | `daemon/tools/_tool_registry.py` `CATEGORY_MODULES` | `"prompt-assent": "daemon.tools.prompt_assent"` — the value **MUST** be `daemon.tools.*` (the AST scanner resolves `daemon/tools/<parts[2:]>.py`; a `daemon.services.*` value is never discovered and the drift pin fails at boot; `:1239-1246`) |
| 3 | `daemon/tools/_tool_registry.py` `DYNAMIC_TOOL_NAMES` | add `"prompt_assent"` (factory-built tools belong here; the set starts `:23`; precedents `notify_user` `:150`, `prompt_reload` `:324`) |
| 4 | `daemon/tools/_tool_registry.py` `KNOWN_TOOL_NAMES` | add `"prompt_assent"` (`:1585-1590` region; inventory + frozen-binary fallback; **drift equality is pinned** — `test_known_tool_names_matches_source_exactly_no_drift`) |
| 5 | `daemon/tools/_tool_registry.py` `PRIVILEGED_TOOL_CATEGORIES` | add `"prompt-assent"` → **entry #21** (the set is `:418-773`; `prompt-reload` at `:751-772` is the style template, incl. the "opt-in only / ari-exactly / kill-switch read at call time / SAME-PR" rationale prose) |
| 6 | `daemon/tools/instance.py` | factory `create_prompt_assent_tools_if_available(...)` (pattern `:2896-2927` — always constructs; the tool refuses cleanly when a handle is missing) + the **extend inside `create_instance_tools`** (`:6810-6814` / `:7013` pattern). **Placement is load-bearing:** it MUST precede `create_help_tool` / `scan_tools_for_full_docs` / `_apply_tool_filter` (`:6791-6802`) — *"a later extend is either invisible to the help tool or BYPASSES the privileged strip"* |
| 7 | `daemon/tools/prompt_assent.py` | in-body gate `PROMPT_ASSENT_AUTHORIZED_AGENT = "ari"` (pattern `prompt_reload.py:70`) + the notify-exact generic not-ari envelope (`notify_user.py:703-711`) — *"a LITERAL tool name in an allow list bypasses the category gate, so the category alone cannot spell 'ari only'"* (`notify_user.py:19-30`) |
| 8 | `daemon/config.py` | new `PromptAssistConfig(BaseSettings)` (pattern `NotifyUserConfig` `:3166-3224`, prefix `ENSEMBLE_PROMPT_ASSIST_`) + the field wiring at the `:6826` site. Auto-covered by the tests' `ENSEMBLE_` prefix tracker (`config.py:3171-3173`) — **no conftest edit** |
| 9 | `daemon/manager.py` | the service/handle the factory reads (pattern `manager.py:1778` — `self._prompt_version_service`; the notify factory reads `manager.engine` + `_source_repository` + `_instance_repository`) + the token-store handle |
| 10 | `daemon/api.py` | the **create_all seam** for the new table (precedent `daemon/api.py:1142-1143` — *"N1 REQUIRED seam — a missed import = no table on PG"*). Brand-new table → **no `_ensure_postgres_columns` mirror needed** |
| 11 | `daemon/repositories/prompt_assent/{models,repository}.py` **(NEW)** | the token table + the store (mint / find / consume / attempt-count / sweep) |
| 12 | `daemon/migrations/versions/YYYYMMDD_HHMMSS_create_prompt_assent_tokens.sql` **(NEW)** | **own `.sql`** (house rule: every new SQLModel table requires its own file — zero exceptions). Format: `YYYYMMDD_HHMMSS_<snake_desc>.sql`, UP first, DOWN last, every statement idempotent (`IF NOT EXISTS` / `IF EXISTS`), index names byte-identical to `__table_args__` (parity test exists — `tests/migration/test_prompt_versions_index_parity.py`) |
| 13 | `daemon/checkpoint_metrics.py` | tool-category vocabulary bump: `VOCABULARY_VERSION 8 → 9` (`:263`) **and** the frozen artifact label set regenerated in the **SAME change** (`:244-263`) — the **F-1 trap class** (*"the artifact and this constant move in the SAME change"*) |
| 14 | `agents/ari/meta.json` | append `"prompt-assent"` at the **TAIL** of `tools.allow` (the tail today is `"prompt-reload"`, `meta.json:47`) — the array-ORDER pin is `tests/unit/test_board_registration.py:295-306` |
| 15 | `daemon/tools/_tool_registry.py:379-406` | the SAME-PR census **prose** — update it (it is a derivation record, and it is already stale — see below) |

### Test pin sites — ALL in the SAME commit (SAME-PR rule, `_tool_registry.py:366-417`)

**Derived by grep in this lane** (the rule says: *"DERIVE THE PIN SET BY GREPPING THE TREE — do
NOT trust this prose"*):

*Exact-set literals (5 files):*
`tests/integration/test_service_tool_flag_off_byte_identical.py:842` ·
`tests/integration/test_maintenancer_spawn_resolves_tools.py:412` ·
`tests/unit/tools/test_secrets_registration.py:226` ·
`tests/unit/tools/test_upgrade_registration.py:152` ·
`tests/unit/tools/test_attestation_registration.py:192`

*`EXPECTED_PRIVILEGED_SET` comparisons (6 files):*
`tests/unit/test_board_registration.py:217` · `test_wedge_capture_registration.py:187` ·
`test_self_alarm_registration.py:225` · `test_notify_user_registration.py:190` ·
`test_metrics_read_registration.py:229` · `test_instance_ops_registration.py:216`

*Count assertions `20 → 21` (10 files — note the meta-pins):*
`test_board_registration.py:232` · `test_wedge_capture_registration.py:190` ·
`test_self_alarm_registration.py:228` · `test_notify_user_registration.py:193` ·
`test_memoir_registration.py:149` **(plus its literal-string meta-pins at `:374-375`, `:415`,
`:419` — these assert the *text* `"assert len(PRIVILEGED_TOOL_CATEGORIES) == 20"` and must be
updated to `21`, and the `== 19` / `== 17` negative assertions reviewed)** ·
`test_metrics_read_registration.py:232` · `test_anomaly_report_tool_registration.py:383` ·
`test_instance_ops_registration.py:219` · `tests/unit/tools/test_prompt_reload_pins.py:71` ·
`tests/unit/tools/test_nest_report_read_registration.py:175`

*Mutation pack:* `test/packs/metrics_read_mutation_matrix_test.sh`'s Python
(`test/packs/metrics_read_mutation_matrix_test.py:101-103`) derives the count at pack-import
time (`_COUNT_FIND = f"assert len(PRIVILEGED_TOOL_CATEGORIES) == {len(_PRIV_COUNT_NOW)}"`) — it
**auto-adapts**, but the matrix may need the new category added as a mutation target.

*Other pins named by the research note (NOT re-verified by me — see §13):*
`tests/unit/test_anomaly_report_grant_pins.py:92-96`/`:301-313` (ari grant array) ·
`tests/unit/metrics/test_metrics_family_adjudication.py:256` and
`tests/unit/metrics/test_metrics_vocabulary_amendment.py:30` (metrics-family module list) ·
the **ari-ONLY census** (`tests/unit/test_notify_user_registration.py:23-25`: the category must
appear in `agents/ari/meta.json` and **nowhere else** across every `agents/*/meta.json`).

### ⚠ FINDING — the registry's own census comment is already STALE

The SAME-PR block (`_tool_registry.py:379-406`) enumerates **14** pin files. My grep found a
**15th** count pin that the comment does not list:
`tests/unit/tools/test_prompt_reload_pins.py:71` (`assert len(PRIVILEGED_TOOL_CATEGORIES) == 20`).
The prompt-reload lane added a pin file and did not update the census prose.

**Consequence for the architect:** the pin set must be derived **by grep at the ceremony**, not
copied from the comment — and the comment should be corrected in this change (it is the very
artifact that will be copied forward next time). The research note's §4.4 list (14) + its
"additional sites" (4) also misses this file.

---

## §10 — Q10. Things in the notes / commission that contradict the blessed shape

1. **Two different approval stacks are named.** The commission's tool acts on *"PENDING
   `prompt_versions` rows"*, but the ROUTING requirement names *"the v2 persona-approval
   HUMAN_LANE_REQUIRED path"* — which is the **memoir persona table**
   (`memoir_persona_versions`), a different repository (`memoir/repository.py:220-262`) and a
   different audit shape. They are not one surface. → **DECISION D-7.**
2. **The category key is spelled two ways.** The commission/planning dir use `prompt-assent`;
   `trust-anchors.md` uses `prompt-assist` in §4.5/§5/§7 and titles itself "prompt-assist". One
   string only → `prompt-assent`.
3. **The research notes' anchors came from a different worktree.** `trust-anchors.md` §0/§9:
   the commissioned worktree was **deleted mid-lane** and the notes were anchored in
   `/home/nea/ensemble-src-wt-v2915-bump` at the same commit, with tree-cleanliness unverified.
   The worktree has since been re-created (branch + base VERIFIED by me), and I re-verified every
   load-bearing anchor above in the commissioned tree. The notes' line numbers **held**.
4. **The notes' "16-site pin ceremony" count is not re-derivable** and, per my grep, the real
   count is **15 files** for the set/count pins alone (plus the allow-array, grant-array and
   metrics-family pins). Do not carry a count forward; derive it.
5. **`~6-char` token vs the house 40-bit nonce.** Not a contradiction, but a parameter that
   deserves a ruling → **DECISION D-3**.
6. **"NO self-approval path opens" vs the repo's literal F4 rule.** The relay actor is by
   construction *internal*; the repo refuses internal actors by name. The commission's own
   instruction ("satisfies F4's spirit … without weakening it") resolves it — but the plan must
   state the resolution explicitly, because the literal rule and the commission's phrasing cannot
   both be satisfied unchanged. → §5's recommendation.
7. **"Reject with reason" has no carrier today.** `repo.reject` takes no reason
   (`repository.py:1577-1607`), the FE reject body is empty by design (`prompts.py:318-322`), and
   there is no `reject_reason` column. → §4's payload key.
8. **Kill-switch default is unspecified** by the commission → **DECISION D-2**.
9. **`notify_user`'s per-destination hourly cap does NOT carry** (`trust-anchors.md` §3): an
   approval lane is per-item, not per-destination; keep the per-item attempt counter instead.
10. **The promote lane's "instance binding" does not carry** either: bind to
    `(version_id, agent_id, version_tag, artifact, version)` instead of `issued_to_instance`.
11. **The promote lane's "dry-run" mint step does not carry**: a prompt lane has no dry-run —
    minting must be an explicit, separately audited action (→ `action="mint"`).
12. **`_apply_tool_filter`'s exact definition line was never located** by the research lane
    (`trust-anchors.md` §5 row 4). Grep it before citing it in the plan. *(I did not locate it
    either — see §13.)*

---

## §11 — DECISIONS FOR ARCHITECT (genuinely open only)

| ID | Decision | Options | My recommendation |
|---|---|---|---|
| **D-1** | Token store | (a) new PG table · (b) columns on `prompt_versions` · (c) in-memory | **(a) new PG table** — no `_ensure_postgres_columns` entry → no boot-abort surface; survives restart; independent GC |
| **D-2** | Kill-switch default | fail-open (default ON) · fail-closed (default OFF) | **fail-open (default ON)**, read at call time, tool stays registered, gate order = ari → switch → 3-factor → mutate. *(Deviate only if the Emperor wants the lane dormant until first use.)* |
| **D-3** | Token length | 8 chars / 40 bits (house) · 6 chars / 30 bits (commission) | **8 chars** — costs nothing operationally, removes the brute-force argument. **6 acceptable only with the per-item attempt counter + lockout + a global per-instance bound** |
| **D-4** | Single-use enforcement point | consume-on-success (separate txn) · consume inside the transition txn (requires extending `atomic_transition`) | **transition-then-consume, preferably same-transaction.** The state-machine CAS already blocks double-apply; the ordering matters only so a *refusal* never burns the Emperor's token |
| **D-5** | Which chat sources are in scope, and the per-source "approving user" key | discord-only first · all registered chat types | **discord first** (the commission's "e.g. my-discord-bot"), with the user key taken from the adapter metadata (`user_id`), **not** from `external_user_id` (a guild-channel `external_user_id` is a CHANNEL) |
| **D-6** | Extra window-freshness bound beyond the three factors | none (promote parity) · bound `stamped_at` to ≤10 min | **bound it** — the silent-resume path (`manager.py:11503`) can leave an earlier turn's window alive; this lane changes live personas |
| **D-7** | Surface scope | classic `prompt_versions` only · both (`prompt_versions` **and** the memoir persona lane) | **Phase 1 = classic `prompt_versions` only**; the persona lane is a different table/repo/audit shape and doubles the blast radius of a trust-model change |
| **D-8** | Approver-identity vocabulary | new lane `assent-relay` · widen `session` · reuse `bearer` | **new lane `assent-relay`** + dedicated constructor + attestation-bearing repo entrypoint; `approve()`/`reject()` unchanged |
| **D-9** | Audit event type | keep `APPROVED`/`REJECTED` + payload discriminator · mint `ASSENT_APPROVED`/`ASSENT_REJECTED` | **keep the existing types** (+ `approval_path` payload key) — a new type silently drops relayed approvals from any existing `event_type == "APPROVED"` view |

*(Q1's storage medium, Q4's payload-vs-columns, Q7's list surface and Q8's tool shape are
**decided** in this document — not open.)*

---

## §12 — Risks surfaced

1. **The lane can change Ari's own guardrails.** A chat approval can replace `soul`/`rule`/
   `workflow` for any agent, including Ari. The three factors make this *deliberate*, but the
   blast radius is a self-modifying trust boundary. Mitigation in this design: the item is
   bound to a token shown **with the diff**; the ledger records the full chain.
2. **Window staleness via silent resume** (`manager.py:11503`) — an earlier turn's user window
   can survive a resume. → D-6's freshness bound.
3. **The window is in-memory** (`manager.py:2001`): a daemon restart between mint and reply
   wipes the factor-2 evidence → the reply fails closed with `no-user-origin-turn`. Correct
   direction, but it will look like a user error; the refusal text must say *"daemon restarted
   since the token was issued"* (the promote lane has the same wording at
   `upgrade_tools.py:3524-3530`).
4. **The factor-3 read can fail** if the message row is wiped at boot → `assent-verification-unavailable`.
5. **30-bit token + a guessing loop**, mitigated but not eliminated by D-3/D-4 and the attempt
   counter + lockout.
6. **Two repos, two tables, two lane predicates** (D-7) — a partial implementation could leave
   one surface with a *different* trust rule than the other. Whatever ships, the two surfaces
   must not diverge silently.
7. **Boot-abort exposure** from any `_ensure_postgres_columns` entry added for this lane —
   avoided by choosing new tables (D-1), but any later "just add a column" change re-opens it.
8. **The SAME-PR pin census prose is stale** (§9) — the next lane that copies it will miss
   `tests/unit/tools/test_prompt_reload_pins.py` and ship a red tree.
9. **Audit completeness** depends on the payload extension landing in the *same* transaction as
   the state change (`repository.py:1493-1511`). If a future refactor moves the audit write out
   of the transaction, the relay provenance and the state change can diverge. A pin is warranted.
10. **Ari is a confused deputy by construction.** It renders the diff and the token and relays
    the Emperor's reply. A prompt-injected Ari could try to present a *stale* diff or a
    truncated one. Mitigation: the token is bound to the ITEM, the item's content is what the
    token row was minted against — **the plan should bind the token to a content hash of the
    pending row** so a mutated pending row invalidates the token. *(This is an addition beyond
    the commission's three factors; flagged for the architect — it is cheap: one hash column.)*
11. **The pending list is cross-agent.** Ari sees every CORE pending row, including other
    agents' drafts. That is inherent to the feature, but it means the tool's output is a
    cross-agent information surface — the INFO line must stay content-free
    (`prompt_reload.py:113-135`).
12. **E2E acceptance representability.** The owed **T3.5 re-stamp**
    (`agents/ari/tools_note.md` — VERIFIED `grep -c memoir_about` = **0** today, matching
    `.agents/tester/RESULTS/2026-11-10-memoir-v2-gate.md:178,278`: *"re-stamp verified NOT
    applied (versioned-prompt-artifact lane, human session only)"*) is exactly the shape this
    lane is for: a PENDING `prompt_versions` row for `ari` / `tools_note`, approved from chat.
    The acceptance must be **representable**: the plan should include an acceptance step that
    mints a token for a pending `tools_note` row, shows the diff + token, and completes the
    approval through the tool. It must **not** be executed as part of this lane (the artifact
    write path is human-session-only by design — `daemon/tools/prompt_artifacts.py`
    `check_write`).

---

## §13 — [REPORT SANITY] — unverified / carry-over items

Re-verified by me in the commissioned worktree (`0efdf106f`, branch `feature/prompt-assent`):
every anchor cited in §1–§9 with a `file:line` **except** the ones listed below.

**Not re-verified by me (carried from `trust-anchors.md`):**
- `tests/unit/test_anomaly_report_grant_pins.py:92-96` / `:301-313` (the ari grant-array pin).
- `tests/unit/metrics/test_metrics_family_adjudication.py:256` and
  `tests/unit/metrics/test_metrics_vocabulary_amendment.py:30` (metrics-family module list).
- `def _apply_tool_filter` — exact definition line **still not located** (by the research lane
  or by me); only the *call-order* constraint (`instance.py:6798-6802`) is verified.
- `daemon/api.py:1142-1143` — I read the notify_audit create_all block (`:1142-1143` sits inside
  the seam I quoted at `:1085-1150`); the *exact* line numbers are close to the note's but I
  quote the block, not the line.
- `daemon/repositories/notify_audit/models.py:100-201` — I did not re-read the model; the
  "content-free (`body_sha256` + `body_length`, never the body)" shape is quoted from the note.
- The memoir persona table name (`memoir_persona_versions`) — the repository/model was read
  (`daemon/repositories/memoir/models.py` exists; `MemoirPersonaVersion` confirmed at
  `memoir/repository.py:161-170`), but the exact `__tablename__` string was not verified.
- `daemon/tools/prompt_artifacts.py` `check_write` — the file was opened and the function
  located (`def check_write` at `:265`), but its refusal semantics were **not** re-read; the
  "human-session-only approval lane" claim is quoted from the project blueprint.
- The "16-site" historical pin count — **not re-derivable**, and per my grep the real set is
  15 files for set/count pins alone. Derive, don't copy.

**Judgement calls I made (flagged so the architect can overrule):**
- The recommendation that **the token is not the security boundary** (§2) rests on the reading
  that an attacker must simultaneously arm factor 2 (a user-origin message processed in the
  same turn) and satisfy factor 3 (token in that message's content). Both are daemon-verified.
- The **10-minute window-freshness bound** (D-6) is my proposal, not a house value.
- The **content-hash binding** of the token to the pending row (Risk 10) is an addition beyond
  the commission's three factors.
- The **8-char token** recommendation (D-3) departs from the commission's "~6-char" parameter.

**Not done by this lane (by design):** no source file edited; no commit; no test run; no DB
touched; no live contact; the canonical checkout `/home/nea/ensemble-src` was **not** read or
written at any point.

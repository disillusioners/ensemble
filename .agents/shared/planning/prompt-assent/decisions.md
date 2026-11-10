# Decision Ledger — prompt-assent

**Commission:** `prompt-assent` — the conversational approval lane (*The Emperor's Word / Assent*).
**Doctrine:** see [`doctrine.md`](doctrine.md) — *"FE-UI is a deeper view; the working path is conversation. The Emperor's word, relayed by C2 with provenance, is a first-class approval lane."*

| Field | Value |
|---|---|
| Date | 2026-11-10 (UTC) |
| Worktree | `/home/nea/ensemble-src-wt-prompt-assent` (branch `feature/prompt-assent`, base `0efdf106f3f66ccb400405f4137694592b72097d`) |
| Sources | `technical-analysis.md` (964 lines), `research/approval-path.md` (148), `research/trust-anchors.md` (622) — all read in full |
| Companion | [`risks.md`](risks.md), [`plan-overview.md`](plan-overview.md) |

## Status legend

| Status | Meaning |
|---|---|
| `BLESSED` | Fixed by the commission. Implementation must match. Changing it needs the Emperor, not the architect. |
| `DECIDED` | Fixed by the technical analysis (the question was closed *in* the analysis, not open). Binding on implementation. |
| `RECOMMENDED-PENDING-ARCHITECT` | The analysis closed the question with a recommendation; the architect must ratify before the developer lane lands it. |
| `OPEN-JUDGEMENT` | The analysis explicitly flagged its own proposal as a judgement call to be overruled if the architect disagrees. |

---

## Part A — The six blessed design points (commission-seeded)

### BLESSED-1 — A NEW privileged `ari`-EXACTLY tool `prompt_assent`, acting on a PENDING row by id

**Status:** `BLESSED`
**Decision.** One new privileged tool, category key **`prompt-assent`**, tool name **`prompt_assent`**,
with two mutating actions — `APPROVE` and `REJECT` (reject carries a reason). It acts **on a
PENDING `prompt_versions` row identified by id**.

**Rationale.** The commission's blessed shape. The three-layer access posture is inherited from
the `notify-user` precedent (`daemon/tools/notify_user.py:19-30`): privileged category (never
default-granted) + explicit `agents/ari/meta.json` allow entry (and ONLY ari) + in-body
`agent_id == "ari"` gate. The category alone cannot spell "ari only" — a literal tool name in an
allow list bypasses the category gate, so layer 3 is mandatory
(`daemon/tools/notify_user.py:19-30`).

**Never touches** draft / archived / approved rows. **Single-use per row** — a second approve of
the same row is an idempotent refusal, not an error.

**Binding implementation constraints.**
- The tool arguments carry **no approver identity, no source, no message id, no user id** — the
  anti-forgery analogue of the repo's R3 rule ("Body-asserted actor fields are IGNORED",
  `daemon/repositories/prompt_version/repository.py:602-604`).
- Naming is **one string everywhere**: `prompt-assent` (category), `prompt_assent` (tool),
  directory, docs, metrics vocabulary, runbook. **Never `prompt-assist`** — that spelling is a
  carry-over error in `research/trust-anchors.md` (§4.5, §5, §7, title) and in the sibling lane
  that drifted; it is NOT to be propagated.
- The tool must **stay registered** regardless of the kill-switch state (refusal-at-call, never
  construction-absence) — `daemon/tools/prompt_reload.py:25-28`, `:76-91`.

---

### BLESSED-2 — THREE-FACTOR ECHO: `user_confirmed` + item token + genuine user turn

**Status:** `BLESSED`
**Decision.** An approval requires all three factors, conjunctively:

1. the tool call carries `user_confirmed=true` **and** the per-item confirmation token;
2. the turn must be a **genuine user turn**, established by the EXISTING `classify_user_origin`
   (`daemon/tools/upgrade_journal.py:4116-4166`) — the trust anchor is **NOT forked**, a second
   classifier is **NOT** written, and the chat-type set is **NOT** re-derived from source-id
   prefixes;
3. the **content of that specific message** must carry the token, echoed verbatim.

The token is per-item, shown **with the diff**, **TTL-bounded (~60 min)**, and **re-mintable**.
**No match ⇒ refusal** (fail-closed; never auto-retry — "relay it verbatim",
`daemon/tools/upgrade_tools.py:3906-3907`).

**Rationale.** These three factors are the *entire* authority of the lane. Each maps onto an
existing, in-tree, daemon-verified primitive:

| Factor | Existing primitive | Anchor |
|---|---|---|
| 1 — explicit confirmation | `system_upgrade`'s `user_confirmed` param | `daemon/tools/upgrade_tools.py:3382-3386` |
| 2 — genuine user turn | per-turn `_user_origin_windows` window, stamped only by `classify_user_origin` | `daemon/manager.py:6126-6131` (stamp), `daemon/tools/upgrade_tools.py:3388-3435` (read) |
| 3 — content echo | message-content read of the window's `message_id` | `daemon/tools/upgrade_tools.py:3508-3531` |

**Binding implementation constraints.**
- Factor 2 is read from `manager._user_origin_windows[current_instance_id]` — the **same object**
  the promote gate reads (`daemon/tools/upgrade_tools.py:3388-3391`). The window is cleared on
  every non-user-origin dispatch (`daemon/manager.py:6132-6133`), so it is strictly per-turn:
  *"an agent/internal-originated turn must never inherit an earlier turn's authorization"*
  (`daemon/manager.py:1996-1999`).
- Factor 3 is **message-exact**, not turn-fuzzy: the window's `message_id` selects the one row
  whose content must carry the token.
- TTL is 60 minutes — reuse `upgrade_journal.NONCE_TTL_S` as the SSOT (import it; do **not**
  declare a new constant) (`daemon/tools/upgrade_journal.py:141`; widened from 15 min per user
  decision 2026-09-30, ADR-036). `manager.py:6130` computes the factor-2 window's `expires_at`
  with the same constant.
- The **two clocks are independent**: the token TTL governs "how long the Emperor has to reply";
  the window TTL governs "how long the reply's authorization lives". They need not be equal.

---

### BLESSED-3 — PROVENANCE: the approval event records the full evidence chain

**Status:** `BLESSED`
**Decision.** The approval event records: **approving user + source + approving MESSAGE id +
relay agent (ari) + timestamp** into `prompt_audit_events` (payload extend). **Existing events
are NEVER overwritten** — the lane appends a NEW row and touches nothing else.

**Rationale.** This is the doctrine's "with provenance" clause made concrete. The audit row is
already written **inside the same `engine.begin()` as the state UPDATE**
(`daemon/repositories/prompt_version/repository.py:1457-1511`), so the provenance lands for free
in the same COMMIT as the state change — no schema work and no two-transaction drift.

**Binding implementation constraints.**
- **Never overwrite.** `prompt_audit_events` is append-only: `_append_audit_in_conn` INSERTs
  (`daemon/repositories/prompt_version/repository.py:1502-1511`, impl `:3036+`) and nothing in the
  tree UPDATEs or DELETEs an audit row. This invariant is protected by a **grep-verifiable pin**
  (see `risks.md` → Structural Pin SP-2) — "NEVER overwrite existing events" is an invariant only
  a pin keeps.
- The **approving user comes from the adapter's `user_id` metadata, NOT from `external_user_id`**.
  Discord's canonical `external_user_id` is `dm:<user_id>` in a DM but `<guild_id>:<channel_id>`
  in a guild channel (`daemon/sources/adapters/discord/adapter.py:994-1021`) — i.e. in a guild
  channel `external_user_id` identifies a **CHANNEL, not a user**. The adapter metadata carries
  the author identity: `user_id`, `user_name`, `user_display_name`, `guild_id`, `channel_id`
  (`daemon/sources/adapters/discord/adapter.py:1035-1058`). `external_user_id` remains the correct
  **destination** identity (it is what `notify_user` hashes into `target_hash`).
- The approving **MESSAGE id** is daemon-minted at the dispatch funnel and copied from the
  window — it is never tool-supplied.
- The **token never appears in the audit payload** — only `sha256(token)[:12]`, the content-free
  discipline (`daemon/repositories/notify_audit/models.py:127-129`, `body_sha256` + `body_length`
  only).

---

### BLESSED-4 — ROUTING: the HUMAN_LANE_REQUIRED paths gain this lane ALONGSIDE the FE lane

**Status:** `BLESSED`
**Decision.** The `HUMAN_LANE_REQUIRED` paths gain the conversational lane as an **accepted
approval path alongside** the FE path. **The FE path is unchanged** — the FE remains a *deeper
view* (doctrine), not a replaced one.

**Rationale.** The doctrine says "FE-UI is a deeper view", not "the FE goes away". Today "a
human approved this" means exactly one thing: `AuthContext.auth_method == "session"` — the cookie
lane — and that equality is checked **four** times in defence in depth (service
`daemon/services/prompt_version_service.py:677-717`; router 403
`daemon/routers/prompts.py:639-640`, `:680-681`; repo refusal of internal actors
`daemon/repositories/prompt_version/repository.py:1560-1565`, `:1594-1599` — *F4: agents cannot
self-approve*; plus the identity clamp inside the transition itself
`daemon/repositories/prompt_version/repository.py:1433-1444`). A chat-relayed approval arrives as
an **internal** actor and therefore cannot use any of those four gates as they stand.

**Binding implementation constraints.**
- The FE routes stay **byte-identical**: `_require_auth_on` → `require_scope("prompts:approve")`
  → `actor_from_request` → `svc.approve` → 403 on non-session. No FE behaviour changes.
- The new path **never goes through HTTP** — it is an **in-process** tool calling the service
  directly (the `inner_soul` pattern: `_run_coro_sync` `daemon/tools/inner_soul.py:1250`,
  `_get_prompt_version_service` `:1273-1283`). No AuthContext forgery, no new public endpoint, no
  scope plumbing, no HTTP latency. Synthesising a fake `session` AuthContext and calling the REST
  route is **explicitly rejected** — that is precisely the forgery layer 3 forbids
  (`daemon/repositories/prompt_version/repository.py:624-629`), and it would make the chat lane
  indistinguishable from the FE lane in the ledger.
- Any new acceptance path goes through the **service** method, never raw `svc.atomic_transition`
  — the F2 retract-bypass lesson (`daemon/routers/prompts.py:747-753`).

---

### BLESSED-5 — WORKFLOW SURFACE: a light ari-facing way to LIST pending items

**Status:** `BLESSED`
**Decision.** There is no global pending-enumeration for `prompt_versions` today:
`GET /api/prompts/versions?state=pending_approval` requires a full `(agent_id, version_tag,
artifact)` key and returns an **empty list** if any part is missing
(`daemon/routers/prompts.py:500-507`), `state` is a **router-side post-filter** (`:526-527`),
`repo.list_versions` has **no** state filter
(`daemon/repositories/prompt_version/repository.py:1843-1866`), and
`GET /api/prompts/pending-count` is a **scalar**
(`daemon/routers/prompts.py:1078-1100` → `repo.count_pending`
`daemon/repositories/prompt_version/repository.py:2999-3029`). The lane needs its own list
surface.

**Rationale.** Ari must be able to show the Emperor what awaits assent without asking a human to
open the FE — that is the doctrine's "the working path is conversation".

**Binding implementation constraints.** See `DECIDED-2` (read tool, not a route) and
`DECIDED-3` (one tool, `action` discriminator) below.

---

### BLESSED-6 — GUARDRAILS: three factors always; tokens single-use; audit-complete; kill-switch; no self-approval path

**Status:** `BLESSED`
**Decision.** The lane is guarded by: (a) all three factors, always; (b) tokens are single-use;
(c) the audit write is complete — a failed ledger write is **fail-closed**; (d) a kill-switch env
knob; (e) **no self-approval path** — the human turn + token are the authority, Ari only relays.

**Rationale.** (e) is the one that needs a design answer, because the literal F4 rule and the
commission's phrasing cannot both be satisfied unchanged: the relay actor is by construction
`internal`, and the repo refuses internal actors **by name**
(`daemon/repositories/prompt_version/repository.py:1560-1565`, `:1594-1599`). The commission's own
instruction — "satisfies F4's spirit … without weakening it" — resolves it, and the resolution is
D-8 below.

**Binding implementation constraints.**
- The kill-switch is read **at call time**; the tool **stays registered** when the switch is off.
- Gate ORDER is fixed: **ari gate → kill-switch → 3-factor gates → mutation** — so an
  unauthorised caller cannot probe the knob's state (`daemon/tools/notify_user.py:703-715` puts
  the agent gate first for exactly this reason).
- "Never auto-retry a refusal — relay it verbatim"
  (`daemon/tools/upgrade_tools.py:3906-3907`).
- Every terminal path emits **ONE frozen INFO line** — counts and flags only, **never persona or
  diff content** (`daemon/tools/prompt_reload.py:113-135`; the "logs content-free v1" doctrine).

---

## Part B — Technical-analysis decisions D-1 … D-9 (for architect ratification)

> All nine are `RECOMMENDED-PENDING-ARCHITECT`. The technical analysis closed each question with a
> recommendation; the architect ratifies or overrules **before** the developer lane lands them.
> Two of the three "flagged judgement calls" named by the commission are already embodied here —
> **D-3** (token length) and **D-6** (window freshness). The third (content-hash binding) is
> **Part D / J-3**, because the analysis raised it as Risk 10 rather than as a numbered decision.

### D-1 — Token store persistence

**Status:** `RECOMMENDED-PENDING-ARCHITECT`
**Question.** Where does the per-item confirmation token live?

| Option | Shape | Verdict |
|---|---|---|
| (a) | new PG table `prompt_assent_tokens` | ✅ **recommended** |
| (b) | new columns on `prompt_versions` | rejected |
| (c) | in-memory store (`_RepairNonceStore` pattern) | rejected |
| (d) | install-journal file (`releases/state.json`) | rejected — wrong medium |
| (e) | `shared_meta_kv` | rejected as token store (acceptable as kill-switch channel only) |

**Recommendation: (a) — a new PG table `prompt_assent_tokens`.**

Rationale, in the order that decided it:
- **No boot-abort blast radius.** A brand-new **table** needs no `_ensure_postgres_columns` entry
  — it is created via the `create_all` seam (`daemon/api.py:1142-1143`, the `NotifyAudit` block:
  *"Brand-new table … No `_ensure_postgres_columns` mirror needed"*). The ensure list is **one
  transaction for the whole statement list** with **no per-statement try/except**
  (`daemon/manager.py:10036-10038` — `with self._engine.begin() as conn: for stmt in statements:
  conn.execute(text(stmt))`), so one bad statement rolls back the whole ensure and **aborts
  boot**. A new **column** on an existing table (option b) re-opens exactly that surface on the
  hottest prompt table.
- **Restart semantics.** A 60-minute token window means "daemon restart during the Emperor's
  deliberation" is a **routine** event, not an edge case. (a) survives; (c) does not.
- **Multi-daemon correctness.** The project runs multiple ensemble instances across remote VMs.
  PG-backed storage is the only option that is *correct* if a deployment ever runs two daemon
  processes against one PG; (c) and (e) are silently wrong there.
- **GC rule.** Mirror the promote GC's discipline — *"only deletes what it can prove is dead"*
  (`daemon/tools/upgrade_journal.py:1926-1958`) and **retain consumed rows** so "already used"
  stays answerable (`:1931-1934`).
- (b)'s only real advantage — a single-statement CAS carrying the token match — is already
  delivered by the state machine (see D-4).

**Proposed row shape.** `token_id` (PK, uuid4 hex) · `version_id` · `agent_id` · `version_tag` ·
`artifact` · `version` (the item binding) · `token_hash` (`sha256(token)` — **never** the raw
token) · `token_display` · `issued_at` · `ttl_expires_at` · `superseded_at` · `consumed_at` ·
`consumed_by_message_id` · `attempts` · `status` (`live|consumed|superseded|revoked`).

---

### D-2 — Kill-switch default: fail-open vs fail-closed

**Status:** `RECOMMENDED-PENDING-ARCHITECT`
**Question.** Should the lane default ON (fail-open) or OFF (fail-closed)?

House precedent, verified: `NotifyUserConfig.enabled` → **default `True`**
(`daemon/config.py:3181-3187`; `ENSEMBLE_NOTIFY_USER_ENABLED`); `PromptReloadConfig` → same
shape, **default ON**, and **fail-closed on indeterminate values** (the registry comment at
`daemon/tools/_tool_registry.py:740-750`).

**Recommendation: (a) default ON (fail-open)**, with two conditions — (i) the switch is read **at
call time** and the tool **stays registered** when off (`daemon/tools/prompt_reload.py:25-28`);
(ii) gate order is **ari → switch → 3-factor → mutation**
(`daemon/tools/notify_user.py:703-715`).

**The honest counter-argument for the architect.** Default-OFF/ON is *not* the security boundary
here: the lane already sits behind four independent gates (privileged-category strip; the
per-agent allow; the in-body `agent_id` gate; the user-turn window; the content echo). Default-OFF
adds a **fifth** gate protecting operators who have not deliberately armed the lane — it does not
make an unarmed lane unsafe. **But** this lane is the first in the tree that can change a live
agent's **own guardrails** through a chat message (including Ari's `soul`/`rule`/`workflow`).
If the Emperor wants the lane dormant until he has used it once, option (b) costs one env var
(`ENSEMBLE_PROMPT_ASSIST_ENABLED=0`) per environment and buys that assurance.
**This is a judgement the Emperor owns — see `plan-overview.md` → Open Questions.**

---

### D-3 — Token length: 8 chars / 40 bits (house) vs 6 chars / 30 bits (commission)

**Status:** `RECOMMENDED-PENDING-ARCHITECT` · **FLAGGED JUDGEMENT CALL #1**
**Question.** How long is the token?

House value: 8 base32 chars = **40 bits** (`daemon/tools/upgrade_journal.py:205-208`, alphabet
`:172`, `NONCE_RE` `:173` = `^CONFIRM-[A-Z2-7]{8}$`). Commission parameter: "~6-char" = **30 bits**
(32⁶ ≈ 1.07e9).

**Recommendation: 8 base32 chars (40 bits).** It costs **nothing** operationally — Ari shows it in
a code block, the Emperor copies it; the promote nonce is also 8 chars and is grouped as
`CONFIRM-XXXX-XXXX` (`nonce_grouped` `:211-215`) — and it removes the brute-force conversation
from the review entirely.

**The analysis's load-bearing observation (this is why the token is not the security boundary).**
The three factors are conjunctive, so an attacker who guesses the token still must (i) cause the
daemon to process a message from a registered user-origin source, (ii) put the guessed token in
**that** message's content, and (iii) have the tool call occur in the **same turn**. Agent-originated
messages carry `source=f"internal_agent:<caller>"` and are **cleared** by the stamp site
(`daemon/manager.py:6132-6133`), so an agent that injects a message **cannot arm factor 2**. The
effective attacker is therefore already inside the human's chat channel — at which point the token
is not the binding constraint. The token's real job is **anti-confusion** (binding one human reply
to one specific item).

**If the Emperor insists on the short code:** 6 chars is acceptable **only** with the per-item
attempt counter + lockout + a global per-instance bound (see `risks.md` → Risk 5 / guessing loop).

---

### D-4 — Single-use enforcement point

**Status:** `RECOMMENDED-PENDING-ARCHITECT`
**Question.** Where is the token consumed?

**Recommendation: transition-then-consume, preferably same-transaction.** Consume **only on a
successful transition**, and only **after** the state change lands.

**Why single-use is already structurally guaranteed.** `atomic_transition`'s CAS is
`UPDATE … WHERE version_id=? AND state='pending_approval'`
(`daemon/repositories/prompt_version/repository.py:1459-1464`). After a successful approve the row
is `approved` — **terminal**; a second approve raises `InvalidTransitionError` → `409
ALREADY_APPROVED` (`daemon/routers/prompts.py:653-654`). A rejected row is likewise terminal. The
token's single-use property is therefore a **secondary** guard against token reuse across
*different* items (handled by the item binding) and against a confused-deputy replay (handled by
the CAS).

**Ordering matters only so a refusal never burns the Emperor's token.** Transition-then-consume is
safe: if the transition fails, the token is untouched (house rule: *"refusals never burn"*). If the
consume then fails, the worst case is a live token on an already-decided row — and that token is
**powerless**, because the CAS rejects the re-apply. **Consume-then-transition is the unsafe
ordering**: a transition failure after a successful consume burns the token on a non-decision.

**Same-transaction is preferable** (the audit row already is —
`daemon/repositories/prompt_version/repository.py:1493-1511`), but `atomic_transition` opens its
own `engine.begin()` (`:1457`), so the consume can only join it by extending that method with an
optional guard hook — a change to a **core** method, worth it only if the architect accepts that.

---

### D-5 — Which chat sources are in scope, and the per-source "approving user" key

**Status:** `RECOMMENDED-PENDING-ARCHITECT`
**Question.** Which registered chat sources can relay an approval, and where does the approving
user identity come from?

**Recommendation: discord first** (the commission's *"e.g. my-discord-bot"*), with the user key
taken from the **adapter metadata `user_id`**, **not** from `external_user_id`.

Rationale: Discord's canonical `external_user_id` is `dm:<user_id>` in a DM but
`<guild_id>:<channel_id>` in a guild channel (`daemon/sources/adapters/discord/adapter.py:994-1021`)
— in a guild channel it identifies a **CHANNEL, not a user**. The adapter metadata carries the
author identity (`daemon/sources/adapters/discord/adapter.py:1035-1058`).

Proposed subject form: `approver_subject = f"{source_id}:{user_identity}"` (e.g.
`discord:112233445566`), with `relay_external_user_id`, `relay_guild_id` and `relay_channel_id`
recorded separately for forensics. **Per-source extraction rules must be explicit** — the
commission says "e.g. my-discord-bot", so the extraction cannot be left implicit.

---

### D-6 — Extra window-freshness bound beyond the three factors

**Status:** `RECOMMENDED-PENDING-ARCHITECT` · **FLAGGED JUDGEMENT CALL #2**
**Question.** Beyond "the window exists and has not expired", must the window also be **fresh**?

**Recommendation: yes — bound `stamped_at` to ≤ 10 minutes before the tool call**, in addition to
`expires_at`. Refusal code: `user-origin-window-stale`.

**Why.** The window is **in-memory** (`daemon/manager.py:2001`) and the dispatch funnel **skips the
stamp on a silent resume** (`daemon/manager.py:11503`: `if not silent:` — *"Silent resume is the
ONLY skip (no message is injected; the window keeps the original turn's)"*). So a paused→resumed
Ari can hold a window stamped in an **earlier** turn. The promote gate accepts any unexpired
window (60 min); for a lane that can change a **live persona**, that is looser than we want. This
is a **cheap fourth check** on top of the commission's three factors — it costs one comparison and
it fails closed.

**This is the analysis's own proposal, not a house value** — flagged for overrule.

---

### D-7 — Surface scope: classic `prompt_versions` only, or both?

**Status:** `RECOMMENDED-PENDING-ARCHITECT`
**Question.** The commission's TOOL requirement names *"PENDING `prompt_versions` rows"*, but its
ROUTING requirement names *"the v2 persona-approval `HUMAN_LANE_REQUIRED` path"* — which is the
**MEMOIR PERSONA table**, a **different stack**.

| Surface | Route | Service / Repo | Table | Audit shape |
|---|---|---|---|---|
| **Classic prompt versions** | `daemon/routers/prompts.py:616-697` | `svc.approve/reject` (`daemon/services/prompt_version_service.py:435-504`) → `PromptVersionRepository.approve/reject` (`daemon/repositories/prompt_version/repository.py:1541-1607`) → `atomic_transition` (`:1356+`) | `prompt_versions` | writes `prompt_audit_events` |
| **v2 persona lane** | `daemon/routers/memoir.py:143-208` | **no service** — `asyncio.to_thread(repo.approve, …)` → `MemoirPersonaRepository.approve/reject` (`daemon/repositories/memoir/repository.py:220-237`) → its **own** `_require_human_lane` (`:243-262`) | `memoir_persona_versions` | **does NOT write `prompt_audit_events`** |

**Recommendation: Phase 1 = classic `prompt_versions` ONLY.** The persona lane is a different
table, a different repository, a **different audit shape** (persona rows carry `approved_at` + a
`status` vocabulary and write no `prompt_audit_events`), and a different state vocabulary
(`status` vs `state`). Doing both in one change doubles the blast radius of a **trust-model**
change for no functional gain — the commission's goal is served by (i) alone.

**Explicitly deferred, pending an architect ruling** — see `plan-overview.md` → Scope OUT.

---

### D-8 — Approver-identity vocabulary

**Status:** `RECOMMENDED-PENDING-ARCHITECT`
**Question.** Today the vocabulary is exactly three values — `session` / `bearer` / `internal`
(`daemon/repositories/prompt_version/repository.py:503-511`, `_AUTH_METHODS`) — and `internal` is
banned for approvals by name. A relayed approval is an `internal` actor. What lane does it use?

| Option | Verdict |
|---|---|
| (b) synthesise a `session` AuthContext over HTTP | **rejected outright** — it is precisely the forgery layer 3 forbids (`:624-629`); it makes the audit record a fake `approver_auth_method="session"`; the chat lane becomes indistinguishable from the FE lane |
| (c) widen `session` | rejected — forges the FE lane |
| reuse `bearer` | rejected — `bearer` is refused for approvals by the clamp (`:1433-1444`) |
| (a) new lane `assent-relay` + dedicated constructor + attestation-bearing repo entrypoint | ✅ **recommended** |

**Recommendation: (a) + (c) combined — a new lane value `assent-relay`, a dedicated constructor,
and a NEW repo/service entrypoint that requires a typed `AssentAttestation`.**

**How F4's spirit survives — four properties:**

1. **The tool args can never carry the approver identity.** The tool signature accepts only
   `(action, version_id, token, user_confirmed, reject_reason)`. The *approver* is derived
   entirely from **daemon-owned** evidence: the message row (`source`, `message_metadata`) and the
   source registry. Ari physically cannot assert "the Emperor is X" — it can only forward a token
   that only the Emperor's turn could have produced.
2. **The attestation is not constructible from arguments.** `AssentAttestation` is produced by the
   assent service **only after** the three-factor gate passes; it carries `{item_binding,
   human_message_id, human_identity, source_id, external_user_id, token_id, window_stamped_at,
   window_expires_at, relay_instance_id, attested_at}`. The repo entrypoint **refuses** an
   assent-relay actor whose attestation is absent or incomplete → a lane that cannot be driven
   without the full triple.
3. **`approve()` and `reject()` are unchanged.** The FE path keeps its exact semantics: session
   only, `internal` refused, `bearer` refused. No existing test or consumer changes meaning.
4. **The lane value is distinguishable in the ledger.** `approver_auth_method = "assent-relay"`
   (12 chars; the column is `max_length=32` —
   `daemon/repositories/prompt_version/models.py:409-415`, `:524-531` — with **no CHECK
   constraint** on either column; the clamp is Python-side only). So every relayed approval is
   visible as such.

**Touch points for option (a):** `AUTH_METHOD_ASSENT_RELAY` constant + `_AUTH_METHODS`
(`daemon/repositories/prompt_version/repository.py:503-511`); `ActorContext.__post_init__` gains a
branch requiring the attestation fields (`:551-566`); a new `actor_from_assent_relay(...)` — the
**only** constructor that may mint the lane (`:593-642` is the anti-forgery constructor);
the `atomic_transition` clamp widened to include it (`:1433-1444`); a new repo entrypoint that
**requires** the attestation (the spirit of `:1560-1565`); the service lane predicate widened
(`daemon/services/prompt_version_service.py:677-717`).

**Where the genuine user turn binds to the specific approve call:** the window is read **at
tool-call time** from `manager._user_origin_windows[current_instance_id]` — the exact read the
promote gate performs (`daemon/tools/upgrade_tools.py:3388-3391`); the window is stamped at the
**single dispatch funnel** (`daemon/manager.py:11489-11505`) and **cleared** on any non-user-origin
dispatch (`daemon/manager.py:6132-6133`); the window's `message_id` selects the **exact** message
whose content must carry the token; the item binding is carried by the **token row**
(`version_id`, `agent_id`, `version_tag`, `artifact`, `version`) — replacing the promote's
`issued_to_instance` binding, which is meaningless per-item.

**The one honest limitation (stated plainly in the plan):** an agent with in-process code
execution (`bash`) can import the attestation module. That is true of **every** in-process
authority in this tree (`actor_from_instance` is equally importable). The boundary this design
relies on is the **three-layer access posture** (privileged category + per-agent allow + in-body
`agent_id` gate), not "the agent cannot import the module". The lane's *specific* hardening is
that the attestation **cannot be minted from tool arguments** — which closes the realistic
failure mode (Ari hallucinating an approval).

---

### D-9 — Audit event type: keep `APPROVED`/`REJECTED`, or mint new types?

**Status:** `RECOMMENDED-PENDING-ARCHITECT`
**Question.** `PROMPT_AUDIT_EVENT_TYPES` is a **code-level frozenset**
(`daemon/repositories/prompt_version/models.py:134-147`) — adding a type is additive with **no
migration** (`models.py:120-133` records that precedent twice).

| Option | Verdict |
|---|---|
| mint `ASSENT_APPROVED`/`ASSENT_REJECTED` | rejected — see below |
| keep `APPROVED`/`REJECTED` + a payload discriminator | ✅ **recommended** |

**Recommendation: keep the existing event types, discriminate on a payload key**
(`approval_path: "chat-assent"` vs `"fe-session"`).

**The failure mode that decides it.** A new type is queryable via the existing
`(event_type, created_at)` index (`daemon/repositories/prompt_version/models.py:478-500`) — "how
many chat approvals?" is a one-liner. **But** any existing consumer that filters
`event_type == "APPROVED"` to build an approvals history will **silently drop** relayed approvals.
That is a *forgetting* bug in the audit trail — the worst kind of bug here. Same type ⇒ every
existing view stays complete; the lane is distinguished by one payload key. Cost: lane-scoped
queries need JSON containment (no dedicated index) — acceptable for a lane whose volume is a
handful of decisions per week.

**Proposed payload shape (relay path only)** — see also `DECIDED-1`:

```json
{
  "from_state": "pending_approval", "to_state": "approved",
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

---

## Part C — Inline decisions taken by the technical analysis (not open)

> These were **closed inside** the analysis — the questions were not put to the architect. They are
> `DECIDED`: binding on implementation, not reopened.

### DECIDED-1 — Audit payload: **payload-only extend**, no schema change

**Status:** `DECIDED`
**Decision.** Extend the `prompt_audit_events` **payload JSONB** with the relay provenance keys.
**No new columns, no migration, no `_ensure_postgres_columns` entry** — therefore **no boot-abort
blast radius** (the ensure list is one transaction for the whole statement list with no
per-statement isolation — `daemon/manager.py:10036-10038`).

**Rationale.** The payload is already a heterogeneous blob by design
(`daemon/repositories/prompt_version/models.py:532-536`: *"JSONBType blob — transition-specific
context"*); `TEMPLATE_RESET` already carries `(old_hash, new_hash, selection_preserved)`
(`:130-133`). The event-type vocabulary is code-level only (`:134-147`), so no schema change is
needed for a new type either. Cost ≈ 4 files (repo payload assembly, repo entrypoint, service,
tool); zero migration.

**`reject_reason` placement.** The commission wants "reject with reason", but **`repo.reject` has
no reason parameter** (`daemon/repositories/prompt_version/repository.py:1577-1607`), the FE
reject body is **deliberately empty** (`daemon/routers/prompts.py:318-322`), and there is **no
`reject_reason` column** on `prompt_versions` (model column list
`daemon/repositories/prompt_version/models.py:331-436`). So the reason lives **only in the audit
payload** — which is fine, because the audit is append-only. It goes at the **top level** of the
payload (it is a property of the *decision*, not of the relay); the FE lane can adopt the same key
later with no schema change. *(Alternative considered: nesting under `relay.reason` keeps the
payload purely relay-shaped but makes "why was this rejected?" require relay-aware readers —
top level wins.)*

---

### DECIDED-2 — List surface: a **read tool** over `repo.list_pending()` / `svc.list_pending()`

**Status:** `DECIDED`
**Decision.** Add `repo.list_pending()` + `svc.list_pending()`; expose them to Ari through a **READ
tool**, **not** a new HTTP route.

**Rationale.**
- The **repo method is needed regardless** — the mint step needs the item's key
  (`agent_id`/`version_tag`/`artifact`/`version`), and the tool needs to show the Emperor what is
  pending. Clone `count_pending`'s CORE-only predicate
  (`daemon/repositories/prompt_version/repository.py:2999-3029`: `state='pending_approval' AND
  artifact_class='CORE'`) and return rows ordered by `created_at`.
- The **tool beats the route** for this consumer: no new public endpoint, no scope plumbing, no
  auth-gate ceremony, and it composes with the mint + diff in one response.
- **Do NOT agent-filter the list.** The memoir precedent filters `agent_id="ari"`
  (`daemon/routers/memoir.py:130`), but the relay lane must be able to approve a pending row for
  **any** agent (worker, designer, …). Shape: `list_pending(agent_id: str | None = None)` → all
  CORE pending rows, bounded (e.g. limit 50).
- **`shared_meta_kv` snapshot is rejected** — *"cheapest to READ, most expensive to keep TRUE"*
  (`research/approval-path.md:128`). The approve/reject path runs through the HTTP service, so the
  KV write would need hooks in **two** places, one of which is a service bypass. **Drift in a
  security-relevant list is the wrong kind of cheap**: a stale pending list means the Emperor
  approves a row that has already moved.

---

### DECIDED-3 — Tool shape: **one tool**, `action ∈ {mint, approve, reject}`

**Status:** `DECIDED`
**Decision.** One tool, three actions.

**Rationale.**
- It preserves the commission's "a NEW privileged ari-EXACTLY tool `prompt_assent`" literally.
- `mint` is a **state-changing** operation (it writes a token row and supersedes any prior
  token), so it deserves its own **audited** action rather than being an implicit side-effect of
  "listing".
- One tool ⇒ one envelope, one refusal vocabulary, one INFO line.
- The refusals of `mint` are naturally distinct from the refusals of `approve` (a mint cannot fail
  on "wrong token" — it can fail on "the item is not pending").

**Trade-off.** If the architect wants read separable from mutate, that requires **two categories**
(per-agent allow is per-category) — one category grants both. The only consumer is ari, so one
category is right. **If a second agent ever needs read-only access, split the category then, not
now.**

**Tool arguments** (the anti-forgery line — no approver identity, no source, no message id, no
user id):

```
prompt_assent(
    action: Literal["mint", "approve", "reject"],   # required
    version_id: str | None = None,                  # required for approve/reject
    token: str | None = None,                       # required for approve/reject
    user_confirmed: bool = False,                   # must be True for approve/reject
    reject_reason: str | None = None,               # required (non-empty) for reject
    limit: int = 25,                                # list only
)
```

---

### DECIDED-4 — Router **unchanged** — acceptance happens at the **service/repo level, in-process**

**Status:** `DECIDED`
**Decision.** The FE router routes stay **byte-identical**. The new acceptance path plugs in **at
the service/repo level** as a **second accepted approval path** alongside the session path. The
relay path **never goes through HTTP at all** — it is an in-process tool calling the service
directly.

**Rationale.**
- The F2 lesson is explicit: any new acceptance path must go through the **service** method, never
  raw `svc.atomic_transition` (`daemon/routers/prompts.py:747-753` — the retract bypass that was
  the F2 finding).
- A **single shared predicate** — `is_acceptable_approver(actor, attestation)` — implemented
  **once** in the service layer and consulted by both surfaces, so the "who may approve, and on
  what evidence" rule exists in **ONE** place rather than being re-implemented per route. The
  router never calls it directly (the router has no attestation).
- In-process is the `inner_soul` pattern (`daemon/tools/inner_soul.py:1250`, `:1273-1283`): no
  AuthContext forgery, no new public endpoint, no scope plumbing, no HTTP round-trip latency, and
  the daemon-owned handles the lane needs (`_queue_repository`, `_user_origin_windows`,
  `_source_repository`) are directly reachable.

---

## Part D — The three flagged judgement calls (architect overrule surface)

> The commission names three judgement calls the analysis made. Two are embodied in Part B
> (D-3, D-6); the third is a **Part D** item because the analysis raised it as Risk 10 rather than
> as a numbered decision. All three are presented here so the architect sees them together.

### J-1 — Token length: 8 chars (recommended) vs the commission's "~6-char"

**Status:** `OPEN-JUDGEMENT` (embodied in **D-3**)
**Analysis position.** 8 base32 chars / 40 bits — the house `mint_nonce` value
(`daemon/tools/upgrade_journal.py:205-208`). Costs nothing operationally; removes the
brute-force argument.
**What the architect is being asked.** Whether to accept 8, or keep the commission's 6 — in which
case the **per-item attempt counter + lockout at 5 mismatches + a global per-relay-instance bound
(≤10 mismatches/hour)** become **mandatory**, not optional.
**Why it is not the security boundary.** See D-3's observation: factors 2+3 are conjunctive and
agent-originated messages cannot arm factor 2 (`daemon/manager.py:6132-6133`).

### J-2 — Window freshness bound: ≤10 minutes (recommended) vs promote parity (60-min `expires_at` only)

**Status:** `OPEN-JUDGEMENT` (embodied in **D-6**)
**Analysis position.** Require `window["stamped_at"]` to be within **10 minutes** of the tool call,
in addition to `expires_at`. Refusal: `user-origin-window-stale`.
**Why.** The dispatch funnel **skips the stamp on a silent resume**
(`daemon/manager.py:11503`) — a paused→resumed Ari can hold a window stamped in an **earlier**
turn. For a lane that changes live personas, that is looser than we want.
**What the architect is being asked.** Whether the extra fourth check is worth the (small) chance
of refusing a legitimate approval whose window is older than 10 minutes but still unexpired.

### J-3 — Content-hash binding of the token to the pending row (anti confused-deputy)

**Status:** `OPEN-JUDGEMENT` — **NOT part of D-1…D-9; the architect must rule on it separately**
**Analysis position (technical-analysis.md Risk 10).** Bind the token to a **content hash of the
pending row** so that a mutated pending row invalidates the token.
**Why.** *Ari is a confused deputy by construction.* It renders the diff and the token and relays
the Emperor's reply. A prompt-injected Ari could try to present a **stale** diff or a **truncated**
one. The token is bound to the ITEM; binding it also to the item's **content** means the token the
Emperor approved is provably the token for the bytes he was shown.
**Cost.** One hash column on the token row (`content_hash`), computed at mint time from the
pending row's content, and re-checked at approve time — a mutated pending row then yields
`token-item-mismatch` (or a dedicated `token-content-mismatch`).
**What the architect is being asked.** Whether to accept this **fourth** binding as in-scope for
Phase 1 (it is an addition **beyond** the commission's three factors), or to defer it.

---

## Part E — Decisions that were explicitly considered and rejected (recorded so they are not re-litigated)

| # | Rejected option | Why |
|---|---|---|
| R-1 | Synthesise a `session` AuthContext and call the REST approve route | Exactly the forgery the anti-forgery constructor forbids (`daemon/repositories/prompt_version/repository.py:624-629`); writes a fake `approver_auth_method="session"`; makes the chat lane indistinguishable from the FE lane |
| R-2 | Token columns on `prompt_versions` | Requires an `_ensure_postgres_columns` entry → the one-transaction boot-abort surface (`daemon/manager.py:10036-10038`) on the hottest prompt table; token lifecycle ≠ version lifecycle; adds a mint write on a row the FE also mutates (content PATCH is allowed on draft/pending rows) |
| R-3 | In-memory token store (`_RepairNonceStore` pattern, `daemon/tools/ens_db_tools.py:498-575`) | RAM-only across a 60-minute human deliberation window; loses tokens on daemon restart; wrong if two daemons ever share a PG |
| R-4 | Install-journal file store (`releases/state.json`) | Wrong medium — owned by the release pipeline; couples a prompt tool to release machinery |
| R-5 | `shared_meta_kv` as the token store | Generic KV, read-modify-write, no per-row CAS for the consume; acceptable only as the kill-switch/config channel |
| R-6 | New audit event types `ASSENT_APPROVED`/`ASSENT_REJECTED` | Any existing `event_type == "APPROVED"` consumer **silently drops** relayed approvals — a forgetting bug in the audit trail |
| R-7 | New columns on `prompt_audit_events` | Migration + `_ensure_postgres_columns` entry (existing table) → boot-abort surface; buys native filtering only |
| R-8 | A separate relay-audit table | A second write path; two sources of truth; a relay row can exist without its state row (or vice versa) if the writes are not in one transaction |
| R-9 | `shared_meta_kv` pending-list snapshot | Drift risk in a security-relevant list; the write hooks would have to land in two places, one of which is a service bypass |
| R-10 | New HTTP route for the pending list | No new public endpoint is needed for the only consumer; the tool composes mint + diff in one response |
| R-11 | `notify_user`'s per-destination hourly cap carrying over | An approval lane is **per-item**, not per-destination; the per-item attempt counter is the right analogue |
| R-12 | The promote lane's `issued_to_instance` binding | Meaningless for a per-item token; bind to `(version_id, agent_id, version_tag, artifact, version)` instead |
| R-13 | The promote lane's dry-run mint step | A prompt lane has no dry-run — minting must be an **explicit, separately audited** action (`action="mint"`) |
| R-14 | Widening `session` or reusing `bearer` | Forges the FE lane / is refused by the clamp (`daemon/repositories/prompt_version/repository.py:1433-1444`) |

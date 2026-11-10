# Architecture Recommendation — `prompt-assent` (The Emperor's Word / Assent)

| Field | Value |
|---|---|
| Date | 2026-11-10 (UTC) |
| Author | Architect (design authority) — with governor council `architect-council-prompt-assent` (2 councilors, models `agentic` + `coding`, skill `security-design`, both read-only, cross-validated) |
| Worktree | `/home/nea/ensemble-src-wt-prompt-assent` (branch `feature/prompt-assent`, planning @ `255d3fe4a`, base `0efdf106f`) |
| Inputs | `plan-overview.md` · `decisions.md` (D-1…D-9, J-1…J-3) · `risks.md` · `technical-analysis.md` · `research/{approval-path,trust-anchors}.md` · `phase1–4-plan.md` · direct tree verification (below) |
| Status | **RULING — binding on the developer lane.** Supersedes every `RECOMMENDED-PENDING-ARCHITECT` / `OPEN-JUDGEMENT` status in `decisions.md`. |
| Effect | Resolves D-1…D-9 + J-1…J-3; adds rulings **A-1** (mint-channel binding) and **A-2** (action set); amends six planner defects (§7). |

---

## 0. Anchor sanity-check (verified on the branch tree, not from prose)

The developer trusts THIS table, not older prose. Every claim below was verified by direct
read/grep of `/home/nea/ensemble-src-wt-prompt-assent` at `255d3fe4a` (and independently
re-derived by both councilors):

| Claim | Verified value | Notes |
|---|---|---|
| Migration count | **127** `*.sql` in `daemon/migrations/versions/` | Planner's H-1 is correct; the **blueprint's "119" is stale**. `prompt_assent_tokens` makes it **128**. Latest: `20261109_021154_create_memoir_v2_tables.sql`. |
| Pin census (SAME-PR) | Prose at `daemon/tools/_tool_registry.py:379-406` lists **14** files; the three canonical greps derive **15** test files + the pack string pair | The 15th is `tests/unit/tools/test_prompt_reload_pins.py` (a `len(PRIVILEGED_TOOL_CATEGORIES)` site). **Prose correction is mandatory in this change** (H-10). The set MUST be re-derived by grep at implementation time — never copied from this doc either. |
| `PRIVILEGED_TOOL_CATEGORIES` | **20** entries today; `prompt-assent` makes **21** | All count pins read 20 today → 21. Includes the **literal-text pin** `tests/unit/test_memoir_registration.py:374` (`== 20` → `21`) and the pack mutation target `test/packs/metrics_read_mutation_matrix_test.py` (auto-derives the count; add the new category as a mutation target). |
| Silent-resume stamp skip | `daemon/manager.py:11503` `if not silent:` | Confirms J-2's motivating seam; also `:6132-6133` clears the window on every non-user-origin dispatch. |
| `NONCE_TTL_S` | `daemon/tools/upgrade_journal.py:141` = `60*60` | The token TTL SSOT — import it, never re-declare. |
| `classify_user_origin` | `daemon/tools/upgrade_journal.py:4116` | Carries the explicit **no-divergent-copies** contract; its chat set is NOT `constants.CHAT_SOURCE_PREFIXES` and must NOT be deduped with it (O-5). |
| Auth-method vocabulary | `daemon/repositories/prompt_version/repository.py:503-511` — frozenset `{session, bearer, internal}` | D-8 adds `assent-relay` here (Python-side; no CHECK constraint exists on the columns — no migration). |
| CAS + audit co-transaction | `repository.py:1457-1464` (CAS `WHERE version_id=? AND state=from_state`) · `:1493-1511` (audit INSERT **inside** the same `engine.begin()`) | The audit-write failure **rolls back the state change** — this is why co-transactionality is load-bearing (S-6). |
| F4 internal-actor refusal | `repository.py:1560-1565`, `:1594-1599` | Stays byte-identical; the relay lane uses a separate entrypoint. |
| 3-layer ari posture | `daemon/tools/notify_user.py:19-30` (docstring) · gate order `:703-715` | Precedent for S-10. |
| Discord identity | `adapter.py:994-1021` `_build_external_user_id` (DM = `dm:<user_id>`; guild = `<guild_id>:<channel_id>` = a **channel**) · identity fields are **nested** under `metadata["discord"]` (built `:1046-1058`, wrapped `:1113-1117`, → registry `:1318-1325` → queue `metadata` JSONB) | D-5's extraction contract corrected accordingly (§7 defect 4). |
| `create_all` seam | `daemon/api.py` ~`:1130-1150` (NotifyAudit precedent, "N1 REQUIRED seam") | A brand-new table needs **no** `_ensure_postgres_columns` entry — avoids the one-transaction boot-abort surface (`manager.py:10036-10038`). |

---

## 1. Rulings — D-1…D-9, J-1…J-3, A-1…A-2

Compact table first; detail (rationale + overrule cost) follows. **AGREE** = planner's
recommendation ratified; **OVERRULE** = architect decides against it; **AMEND** = ratified
with mandatory changes.

| # | Question | Ruling | Verdict |
|---|---|---|---|
| D-1 | Token store | New PG table `prompt_assent_tokens` via `create_all` + own `*.sql`; **row shape amended** (§2.1) | AGREE + AMEND |
| D-2 | Kill-switch default | **Default OFF (fail-closed)**, `ENSEMBLE_PROMPT_ASSENT_ENABLED` / `PromptAssentConfig` everywhere | **OVERRULE** planner (planner: default ON) |
| D-3 / J-1 | Token entropy | 8 base32 chars / 40 bits via house `mint_nonce`; 6-char rejected; counter+lockout mandatory regardless | AGREE |
| D-4 | Single-use point | Transition-then-consume, **consume co-transactional via a keyword-only hook in `atomic_transition`**; **counters commit in a SEPARATE transaction** | AGREE + AMEND (mechanism + blocking counter rule) |
| D-5 | Sources & user key | Discord-first, lane-side policy on `source_type` via the same registry; user key from **`metadata["discord"]["user_id"]`** (nested — NOT top-level, NOT `external_user_id`) | AGREE + CORRECTION |
| D-6 / J-2 | Freshness bound | ≤ **10 min** on `stamped_at` (configurable, default 600 s) **in addition to** `expires_at`; fail-closed on absent/unparseable | AGREE |
| D-7 | Surface scope | Classic `prompt_versions` ONLY in Phase 1; persona surface deferred as a recorded gap | CONFIRM |
| D-8 | Actor vocabulary | New lane `assent-relay` + `actor_from_assent_relay` + typed `AssentAttestation` (field set §2.3); `approve()`/`reject()` unchanged; FE byte-identical; in-process only | AGREE + AMEND (field set) |
| D-9 | Audit shape | Keep `APPROVED`/`REJECTED` + `approval_path` discriminator; payload-only JSONB; **mint writes NO audit event** | AGREE |
| J-1 | (in D-3) | 8 chars | AGREE |
| J-2 | (in D-6) | ≤10 min | AGREE |
| J-3 | Content-hash binding | **In-scope Phase 1** — hash the exact canonical content rendered into the diff; surface `content_sha256` at mint; re-check at approve; refusal `token-content-mismatch` | AGREE |
| **A-1** (new) | Channel binding | Token bound to its **mint channel**; mint & approve require a **DM** (`is_dm`); `list` allowed anywhere; refusals `mint-requires-private-channel`, `token-channel-mismatch` | NEW RULING (council-unanimous seam fix) |
| **A-2** (new) | Action set | ONE tool, `action ∈ {list, mint, approve, reject}` — reconciles BLESSED-1/DECIDED-3 with phase-2 | NEW RULING |

### D-1 — token store (AGREE + AMEND)
New PG table `prompt_assent_tokens`; hash-only storage (`token_hash = sha256(token)`, raw token
exists exactly once — in the mint response). Own `*.sql` migration (128th) + `create_all` seam
import at `daemon/api.py` (the NotifyAudit precedent block) + **zero** `_ensure_postgres_columns`
entries. **Amendments (mandatory):**
1. **NO `token_display` column.** The planner's proposed row shape carried one — the grouped
   render minus dashes *is* the raw secret; storing it silently defeats hash-only storage.
   Render the grouped form from the raw token at mint time only.
2. **Partial unique index** `ON (version_id) WHERE status='live'` — makes single-active-token
   structural against double-mint races (concurrent mints collide at the DB, not at hope).
3. **Mint-context columns**: `mint_source_id`, `mint_channel_id`, `mint_is_dm`,
   `minted_external_user_id` (feeds A-1 and forensics).
4. `content_hash` column (J-3).
5. Indexes on `token_hash` (unique) and `(version_id, status)`, names byte-identical to the
   SQLModel `__table_args__` (index-parity test precedent).
Re-mint **supersedes and revokes** the prior token (`superseded_at` + status flip; prior reads
`token-superseded` — deliberately distinct from `token-already-used`), re-renders the diff, and
is refused while a live token exists unless explicitly forced (`mint-live-token-exists`).
GC mirrors the promote discipline: only delete what is provably dead; **retain consumed rows**.

*Overrule cost (rejected alternatives):* columns on `prompt_versions` reopen the
one-transaction boot-abort surface (`manager.py:10036-10038`) on the hottest prompt table;
in-memory loses the 60-minute deliberation window to a routine daemon restart; file/KV stores
are wrong mediums (R-2…R-5 in `decisions.md` Part E — all upheld).

### D-2 — kill-switch default (OVERRULE: default OFF)
`ENSEMBLE_PROMPT_ASSENT_ENABLED` / `PromptAssentConfig` — **note the spelling; the planner
drifted to `PromptAssist` in five places** (`phase4-plan.md:16`, `plan-overview.md:103,151,167,260`,
`technical-analysis.md:303,757`) violating H-11's one-string rule; `prompt-assent` wins everywhere.
Semantics: read **at call time**; the tool **stays registered** when off (refusal `lane-disabled`,
never construction-absence); **`mint` is refused too** when off; gate order keeps the switch
**behind** the ari gate (an unauthorized caller cannot probe the knob); **indeterminate/invalid
values fail CLOSED** — deliberately the opposite of `prompt_reload`'s fail-open, because this is
an authority lane, not a convenience lane. A disabled lane refuses cleanly with a typed code
naming the env var — never a silent fall-back-to-FE confusion.

**Default OFF for the initial landing.** Rationale: this is the first lane in the tree that can
change a live agent's own guardrails through a chat message; the house's most recent
Emperor-ratified posture for a novel authority surface (Decision Module P1, 2026-11-09:
*dormant by default, nothing decides anything*) is exactly this shape; the FE session lane fully
covers approvals meanwhile; and the flip to ON is one env var + one restart per environment — a
cheap later decision the Emperor owns. **The planner's P2/P4 inconsistency (defensive resolver
defaults off, config class defaults on) is a defect under either default and is reconciled here:
ONE default constant, fail-closed OFF, consumed by both P2 and P4.**

*Overrule cost:* the doctrine's "the working path is conversation" is not usable until the
Emperor arms the lane (one env var per environment, then it stays armed). If the Emperor
prefers zero-friction chat approval from the first promote, flipping the default is a one-line
change in a later release — the exposure delta is only the pre-first-arming probation window.

### D-3 / J-1 — entropy (AGREE)
8 base32 chars / 40 bits via house `mint_nonce` (`upgrade_journal.py:205-208`). The commission's
~6-char example is **rejected**. Echo matching reuses the house helper **`nonce_in_content`**
(`upgrade_journal.py:231-246`) verbatim with `nonce_normalize` semantics (dash/case-tolerant), so
the Emperor may echo the grouped or bare form and no new regex is minted. Entropy is not the
security boundary (factors 2+3 are; agent-originated messages cannot arm factor 2,
`manager.py:6132-6133`), but 8 is operationally free and closes the brute-force conversation.
**The attempt counter + lockout-at-5 + global per-relay-instance bound (≤10 mismatches/hour) are
mandatory ANYWAY** — they bound leak-then-brute and multi-item sweeps, and must never be
presented as justification for shortening the token.

### D-4 — consumption & atomicity (AGREE + AMEND, with a blocking rule)
Invariant (unanimous): **transition-then-consume** — refusals never burn the token; the consume
is **co-transactional** with the CAS + audit INSERT. Mechanism (adjudicated between council
positions): extend `atomic_transition` with a **narrow keyword-only, private, default-`None`
typed hook** executed **inside its existing transaction** — one code path, no forked
CAS+audit copy (the no-divergent-copies doctrine that protects `classify_user_origin` applies to
this co-transaction too). The hook performs the consume as its own CAS
(`UPDATE prompt_assent_tokens SET status='consumed', consumed_at=…, consumed_by_message_id=…
WHERE token_id=? AND status='live'`; rowcount 0 → raise → the whole transaction rolls back →
typed refusal). FE byte-identity with the hook absent is pinned by test (O-4).

**BLOCKING amendment:** the **attempt-counter increment and lockout revocation commit in their
own transaction OUTSIDE the guarded transition** (before it opens, on the refusal path). Inside
it, every refusal's rollback resets the counter — the lockout never fires and D-3's
defense-in-depth is silently inert. The planner's "preferably same-transaction" phrasing is
**wrong for the counters specifically**; this ruling separates them explicitly.

*Overrule cost:* consume-then-transition burns the Emperor's token on a non-decision; a fully
separate consume transaction admits a live token on a decided row (powerless — the CAS rejects
re-apply — but an avoidable loose end); forking the CAS+audit into a lane-private transaction
duplicates security-critical logic.

### D-5 — sources & the approving-user key (AGREE + CORRECTION)
Discord-first, expressed as a **lane-side policy on the daemon-owned `source_type`** (the same
registry `classify_user_origin` matches against) — never a source-id prefix re-derivation (O-5).
Minimal extraction contract (a planner defect fixed here — see §7):
- `source_id` = the window source's prefix before the first `":"`;
- `user_id` = **`message_metadata["discord"]["user_id"]`** — the fields are NESTED under
  `metadata["discord"]` (adapter `:1046-1058` builds, `:1113-1117` wraps, registry `:1318-1325`
  forwards, the queue stores `metadata` JSONB). A top-level `metadata.get("user_id")` returns
  `None`. `user_id` is **REQUIRED — fail closed** with `extraction-incomplete` when absent;
- `user_name` / display name: cosmetic only, never authority;
- `guild_id`, `channel_id`, `is_dm`, and the **platform-side message id** from the same dict;
- `external_user_id` is never the approving-user key (in a guild channel it identifies a
  CHANNEL, `adapter.py:994-1021`); it remains the correct destination identity.
`approver_subject = f"{source_id}:{user_id}"`.

### D-6 / J-2 — freshness (AGREE)
`window["stamped_at"]` must be within **≤10 minutes** (configurable, default 600 s) of the tool
call, **in addition to** `expires_at`; refusal `user-origin-window-stale`. Evaluated **inside
factor 2** (present → unexpired → fresh; absent/unparseable = stale, never valid), UTC via the
house `parse_iso_utc`. Motivating seam verified: the dispatch funnel skips the stamp on silent
resume (`manager.py:11503`), so a paused→resumed ari can otherwise carry an earlier turn's
authorization for the full `NONCE_TTL=3600`. *Overrule cost:* promote parity accepts a
60-minute-old window — too loose for a lane that changes live personas in one chat turn; the
check costs one comparison and fails closed.

### D-7 — surface scope (CONFIRM)
Classic `prompt_versions` only in Phase 1. The persona surface is a different stack (own table,
repo, state vocabulary, **no `prompt_audit_events`** — verified `memoir/repository.py:220-262`)
and would require a second provenance mechanism — a second trust design inside a trust-model
change. Conditions: the deferral is recorded as an explicit known gap in the runbook, and the
commission's persona-stack mis-naming ("the v2 persona-approval `HUMAN_LANE_REQUIRED` path")
is surfaced to the Emperor in this doc (§8).

### D-8 — actor model (AGREE + AMEND)
New lane value `assent-relay` (`AUTH_METHOD_ASSENT_RELAY` + `_AUTH_METHODS` widening,
Python-side; the columns carry `max_length=32` and no CHECK constraint — no migration); the
**only** constructor `actor_from_assent_relay(...)`; `ActorContext.__post_init__` requires the
attestation fields on this lane; the `atomic_transition` approver clamp widens to include it;
`approve()`/`reject()` unchanged (F4 refusals intact); FE byte-identical; relay path in-process
(inner_soul pattern), never HTTP (R-1 upheld). Tool args carry **no approver identity, source,
message id, or user id** (anti-forgery analogue of the repo's R3). The final attestation field
set is §2.3. The belt-and-braces `self-approval-attempt` assertion is kept but is
**near-vacuous as written** (a Discord user id never equals an agent name) — it is an assertion,
not the gate; the load-bearing defence is that agent-originated dispatch pops the window
(`manager.py:6132-6133`), and the tests must pin that construction property plus the ari-EXACTLY
layer-3 gate. The runbook carries the "this is not the gate" language.

### D-9 — audit (AGREE)
Keep `APPROVED`/`REJECTED` + `approval_path: "chat-assent" | "fe-session"` discriminator (new
event types would make every `event_type=="APPROVED"` consumer silently drop relayed approvals —
the forgetting bug); payload-only JSONB extend; `token_fingerprint = sha256(token)[:12]`, never
the raw token; `reject_reason` at the **top level**, **capped ≤500 chars**, never concatenated
into other fields (it is agent-supplied free text landing in an append-only ledger).
**Mint writes NO `prompt_audit_events` row — the token table IS the mint ledger** (a mint event
type would reopen D-9's rejected failure mode). Payload key set finalized in §2.4, including the
actor/authority split: `actor_*` fields carry `assent-relay` + the relay subject; payload
`approver_subject` carries the **human** — the ledger must never read "the actor approved."

### J-3 — content-hash binding (AGREE — in-scope Phase 1)
Bind the token to `sha256` of the pending row's content **at mint**, re-checked at approve;
refusal `token-content-mismatch`. **TOCTOU rule:** hash the **exact canonical content rendered
into the diff** — not a re-read at hash time — so shown bytes and hashed bytes cannot diverge.
Surface `content_sha256` in the mint response (the human can verify what he is approving) and
carry it into the audit payload. This is the only control binding "bytes the Emperor saw" to
"bytes approved"; the item binding covers identity, not content, and pending rows are genuinely
mutable. J-3 is necessary but not sufficient alone (it binds to pending *content*, not to the
rendered diff *view*) — its payoff is exactly that the hash is surfaced to the human and ledgered.
*Cost of deferring (rejected):* a prompt-injected ari lands a valid approval against a
stale/truncated diff and the ledger records consent never given — the worst failure mode of a
trust-model change.

### A-1 — mint-channel binding (NEW, council-unanimous seam fix)
**The seam both councilors found independently, highest severity: `classify_user_origin`
authenticates the CHANNEL, not the HUMAN.** It arms on registered `source_type` alone; in a
multi-occupant guild channel every member is "user origin"; the mint renders diff + token into
that channel where all members read it; any member echoes the token → full approval authority
with faithful-looking provenance (their `user_id` honestly recorded). That is an authorization
gap, not a hardening nicety — all three commission factors genuinely satisfied by a non-Emperor.

Ruling: the token row stores its mint context (`mint_source_id`, `mint_channel_id`,
`mint_is_dm`); at approve, the approving human turn must arrive through the **same
`(source_id, channel)`** as the mint — refusal `token-channel-mismatch`. **Phase 1 additionally
requires the channel to be a DM:** `mint` itself refuses outside a private channel
(`mint-requires-private-channel`), so the diff + token never render into a multi-occupant
channel at all. `list` is allowed anywhere (read-only, no token). Legitimate cross-channel use
is a re-mint — cheap, and it re-shows the diff. Guild-channel approval with a **pinned
approver identity** is a deferred design; a tool-asserted approver hint is **rejected** as a
forgery surface (the tool args must never carry approver identity).

### A-2 — action set (NEW, reconciling a planner inconsistency)
ONE tool `prompt_assent`, `action ∈ {list, mint, approve, reject}`. BLESSED-1 named
approve/reject as the mutating pair, DECIDED-3 said `{mint, approve, reject}`, phase-2 added
`list` — the ledger and the tool signature must agree: `list` = read (any channel, ari-gated);
`mint` = mutating + audited-in-token-table; `approve`/`reject` = mutating + audited in
`prompt_audit_events`. One category grants all four (ari is the only consumer; split the
category only if a second read-only consumer ever appears).

---

## 2. The architecture the developer builds against

### 2.1 Schema — `prompt_assent_tokens` (128th migration)

```
token_id              UUID PK                    (uuid4 hex)
version_id            FK→prompt_versions          (item binding)
agent_id, version_tag, artifact, version          (item key, denormalized for audit legibility)
token_hash            sha256(token), UNIQUE       (raw token NEVER stored; NO token_display column)
content_hash          sha256(pending content as rendered into the diff)   [J-3]
mint_source_id, mint_channel_id, mint_is_dm, minted_external_user_id     [A-1]
issued_at, ttl_expires_at                          (TTL = NONCE_TTL_S imported SSOT, 60 min)
superseded_at, consumed_at, consumed_by_message_id
attempts              int default 0               (mismatch count, this token)
status                live|consumed|superseded|revoked
-- indexes (names byte-identical to __table_args__):
--   UNIQUE (token_hash)
--   UNIQUE (version_id) WHERE status='live'      -- partial: single-active structural
--   (version_id, status)
```

Migration file `YYYYMMDD_HHMMSS_create_prompt_assent_tokens.sql`: UP first / DOWN last, every
statement idempotent (`IF NOT EXISTS`/`IF EXISTS`); plus the `create_all` seam import at
`daemon/api.py` (NotifyAudit block pattern — "N1 REQUIRED seam": a missed import = no table on
PG = every token INSERT fails); **zero** `_ensure_postgres_columns` entries.

### 2.2 The gate pipeline (order is fixed)

```
0. ari layer-3 in-body gate        agent_id == "ari"          (generic not-ari envelope)
1. kill-switch                     ENSEMBLE_PROMPT_ASSENT_ENABLED, call-time, fail-closed → lane-disabled
2. action-shape validation         version_id/token/user_confirmed/reject_reason per action → invalid-arguments
3. FACTOR GATES (enumerate ALL failures in one refusal — the promote factor_failures pattern):
   3a. user_confirmed == true                                 → user-confirmation-missing
   3b. window present for this instance                        → no-user-origin-turn (names observed source)
   3c. window source classified user-origin by UNFORKED classify_user_origin
   3d. window unexpired (expires_at)                           → user-origin-window-expired
   3e. window fresh: stamped_at ≤ now-600s (configurable)      → user-origin-window-stale (absent/unparseable = stale)
   3f. the window's message_id row readable; source matches    → assent-verification-unavailable (fail closed)
   3g. token echoed verbatim in THAT message's content (nonce_in_content)
4. ITEM/TOKEN CHECKS (after the factors — no item-state leak to unproven callers):
   4a. row exists; CORE class                                  → item-not-found / item-not-assentable
   4b. state == pending_approval                               → item-not-pending / already-decided (state read-back)
   4c. token row live, item-bound, unexpired, content-hash eq  → token-mismatch / token-item-mismatch /
                                                                  token-expired / token-superseded /
                                                                  token-already-used / token-content-mismatch
   4d. channel: approving turn's (source_id, channel) == mint's AND is_dm  → token-channel-mismatch
   4e. lockout: attempts < 5, per-instance hour budget         → token-locked
5. ATTESTATION minted by the assent service (never from tool args) — §2.3
6. GUARDED TRANSITION (atomic_transition + keyword-only consume hook, ONE engine.begin()):
   CAS on prompt_versions (WHERE state='pending_approval') + audit INSERT + consume-CAS on the
   token row. Any failure → rollback → typed refusal. (mint action: token INSERT + supersede
   flip instead; NO audit event.)
7. COUNTERS (refusal path only): attempts++ / lockout revocation / per-instance budget —
   committed in a SEPARATE transaction, BEFORE the guarded transition opens. Refusals never
   burn the token.
```

Every terminal path emits **ONE frozen INFO line** — counts and flags only, never persona or
diff content; never the raw token.

### 2.3 `AssentAttestation` (minted only by the assent service, post-gate)

```
item_binding        {version_id, agent_id, version_tag, artifact, version}
content_sha256                                        [J-3]
human_message_id    daemon-minted message id of the approving turn
relay_source_message_id   PLATFORM-side (Discord) message id — the daemon queue id is
                          ephemeral/wiped at boot; without this the forensic chain breaks
human_identity      {source_id, user_id, user_name, guild_id, channel_id, is_dm,
                     external_user_id}                [nested metadata["discord"], D-5]
approver_subject    f"{source_id}:{user_id}"
token_id, token_fingerprint (sha256[:12])
window_stamped_at, window_expires_at
relay_agent ("ari"), relay_instance_id
attested_at
```

### 2.4 Audit payload — relay decision rows (append-only; same transaction as the state change)

```json
{
  "from_state": "pending_approval", "to_state": "approved",
  "approver_subject": "discord:112233445566",
  "approver_auth_method": "assent-relay",
  "approval_path": "chat-assent",
  "relay_agent": "ari", "relay_instance_id": "<uuid>",
  "relay_source_id": "my-discord-bot",
  "relay_external_user_id": "dm:112233445566",
  "relay_user_id": "112233445566",
  "relay_guild_id": null, "relay_channel_id": "999888777",
  "relay_channel_type": "dm",
  "relay_message_id": "<daemon uuid>",
  "relay_source_message_id": "<platform id>",
  "relay_window_stamped_at": "…", "relay_window_expires_at": "…",
  "assented_at": "…",
  "token_id": "<uuid>", "token_fingerprint": "e33c34f5af42",
  "content_sha256": "…",
  "reject_reason": "≤500 chars, top level, reject only"
}
```

Structural pin **SP-2** (grep-verifiable): no UPDATE/DELETE against `prompt_audit_events` in
this lane — append-only is an invariant only a pin keeps. The audit-write failure path is
fail-closed (`audit-unavailable`, no state change) — and the co-transactional INSERT means an
audit failure rolls the state change back with it.

### 2.5 Registration (SAME-PR, one commit)

1. `PRIVILEGED_TOOL_CATEGORIES` += `"prompt-assent"` (entry **#21**; 20 verified today) —
   comment block written in the house style (why-privileged + defence-in-depth + census note).
2. `CATEGORY_MODULES` += the module mapping; `DYNAMIC_TOOL_NAMES` / `KNOWN_TOOL_NAMES` +=
   `prompt_assent`; the `create_instance_tools` extend lands **before** help/docs/filter
   assembly (decorator-only registration is silently invisible).
3. `agents/ari/meta.json` `tools.allow` **tail** entry (after `prompt-reload`); the
   trailing-entry ARRAY-ORDER pin (`tests/unit/test_board_registration.py:294-312`) gains it;
   the ari-ONLY census across `agents/*/meta.json` (pattern:
   `tests/unit/test_notify_user_registration.py:23-25`).
4. In-body `agent_id == "ari"` gate (layer 3 — a literal tool name in an allow list bypasses
   the category gate).
5. **Pin set derived BY GREP at implementation time** — the three canonical greps in the
   SAME-PR block. Expected shape at this base: 15 test files (5 exact-set + 6
   `EXPECTED_PRIVILEGED_SET` + `len()==` sites incl. `test_prompt_reload_pins.py`) + the pack
   string pair (`test/packs/metrics_read_mutation_matrix_test.py` — auto-derives the count; add
   the new category as a mutation target). **All count pins 20 → 21**, including the memoir
   literal-text pin `tests/unit/test_memoir_registration.py:374`. **Correct the census prose**
   at `_tool_registry.py:379-406` (14 → the grep-derived list, naming the 15th) — the prose is
   the artifact that gets copied forward.
6. New pins: `tests/unit/tools/test_prompt_assent_registration.py` (exact-set + count + ari-ONLY
   census) and `tests/unit/test_prompt_assent_lane.py` (matrix).

### 2.6 Kill-switch

`PromptAssentConfig.enabled` (env `ENSEMBLE_PROMPT_ASSENT_ENABLED`) — default **False**;
fail-closed on indeterminate; read at call time; tool stays registered; mint refused too;
checked behind the ari gate. ONE default constant consumed by both the P2 defensive resolver
and the P4 config class (the planner's off/on split is reconciled away).

---

## 3. Refusal taxonomy (complete; typed codes, no state change, no token burn)

`lane-disabled` · `not-ari` (generic envelope) · `invalid-arguments` ·
`user-confirmation-missing` · `no-user-origin-turn` (names observed source + classifier detail)
· `user-origin-window-expired` · `user-origin-window-stale` ·
`assent-verification-unavailable` · `token-mismatch` · `token-expired` ·
`token-already-used` · `token-superseded` · `token-item-mismatch` ·
`token-content-mismatch` · `token-channel-mismatch` · `mint-requires-private-channel` ·
`mint-live-token-exists` (unless forced) · `token-locked` · `item-not-found` ·
`item-not-assentable` · `item-not-pending` (carries current state) · `already-decided` ·
`self-approval-attempt` (belt-and-braces, documented as not-the-gate) ·
`extraction-incomplete` (fail-closed missing nested identity) · `audit-unavailable`.

---

## 4. Security analysis — mandatory test-matrix mapping

Every mandatory cell maps to a design element; each carries a **counter-mutation** that
reddens it (house mutation rule — absence-only pins are prose-satisfiable):

| Cell | Design element(s) that defeat it | Counter-mutation |
|---|---|---|
| **Approve happy path, real token echo** | Factors 2+3 (window read at `upgrade_tools.py:3388-3435` pattern; exact-`message_id` echo via `nonce_in_content`) + D-4 consume + D-9 payload + A-1 channel match | Remove the message-exactness check → a token present **only in the tool arguments** must redden the cell (the single most important pin) |
| **Wrong token** | Attempts counter (**separate transaction** — the regression this guards: counter inside the guarded txn never advances) + `token-mismatch`, no burn | Move the counter into the guarded transaction → the lockout test reddens |
| **Expired token** | `ttl_expires_at` vs imported `NONCE_TTL_S`; distinct from the window clocks | Break the TTL comparison |
| **Non-pending row** | CAS `WHERE state='pending_approval'` (`repository.py:1459-1464`); `item-not-pending`/`already-decided` via state read-back; no audit event | Drop the state predicate from the CAS |
| **Agent-self-approval attempt** | `self-approval-attempt` code **plus** the pinned construction property: agent-originated dispatch pops the window (`manager.py:6132-6133`) + ari-EXACTLY layer 3 | Make a non-user-origin dispatch leave the window alive → reddens |
| **Non-user-origin turn** | Factor-2 armability via UNFORKED `classify_user_origin`; refusal names the observed source (W1 hardening pattern) | Fork/re-derive the classifier → the unforked-reuse pin reddens |

Added cells (this ruling): `token-channel-mismatch` (A-1), `mint-requires-private-channel`
(A-1), `mint-live-token-exists` (D-1), `token-content-mismatch` (J-3),
`extraction-incomplete` (D-5), counter-separate-transaction regression (D-4).

### risks.md top trio — mitigations of record

| Risk | Mitigations (cumulative, this doc) |
|---|---|
| **R-1 — the lane can approve Ari's own guardrails** | The approval is *deliberate by construction*: 40-bit token shown **with the diff** whose `content_sha256` is surfaced to the human (J-3); DM-only + same-channel binding (A-1) so the diff+token never render to a multi-occupant audience; ≤10-min freshness (D-6); **default-OFF kill-switch** = the dormancy lever until the Emperor arms it (D-2); full ledger chain token↔item↔message↔human↔relay↔time (D-8/D-9); ari-EXACTLY 3-layer registration (S-10). Residual accepted and ledgered: the lane's purpose IS to approve guardrails — detectability after the fact is the design's honest backstop |
| **R-2 — window staleness via silent resume** | D-6/J-2 ≤10-min `stamped_at` bound, fail-closed on absent/unparseable; evaluated inside factor 2; `user-origin-window-stale` is actionable (re-show the item, ask again) |
| **R-3 — guessing loop** | 40-bit token (D-3); **mandatory** per-item attempt counter + lockout-at-5 (revokes the live token → a fresh mint → a fresh human turn) + per-relay-instance ≤10 mismatches/hour; counters in their OWN transaction (D-4 blocking rule — otherwise inert); refusals never burn |

### The channel seam (new, council-found — closed by A-1)
Documented in A-1 above. Residual after A-1: a co-occupant of the Emperor's **DM** channel
(i.e., someone with access to the Emperor's Discord account or an open session) can approve —
at which point the threat is account compromise, not this lane; the ledger records exactly who
echoed the token, which is the detection story.

---

## 5. FE-unchanged proof obligations

- The FE routes stay byte-identical: `_require_auth_on` → `require_scope("prompts:approve")` →
  `actor_from_request` → `svc.approve` → 403 on non-session (`daemon/routers/prompts.py:616-697`).
  Proven by diff against base + the existing FE suites passing byte-identically.
- `approve()`/`reject()` semantics unchanged; the relay lane enters via a **separate
  attestation-bearing entrypoint**, never raw `svc.atomic_transition` (F2 retract-bypass lesson).
- The `atomic_transition` hook is default-absent → FE path provably untouched (pinned by test).
- **FE/chat concurrency:** the CAS serializes; the loser gets `already-decided` (state
  read-back distinguishes it from `item-not-pending`); the losing chat path leaves its token
  unconsumed; exactly one decision row lands in the ledger.

---

## 6. Acceptance (delta to the planner's A-1…A-3)

The planner's matrix (A-1) stands with these additions: the six counter-mutations above, the
added refusal cells (§3 "added"), the counter-separate-transaction regression, and
`test_prompt_assent_registration.py` asserting the 21-entry set + ari-ONLY census. A-3 #8's
kill-switch expectation is amended: default state = **OFF** → the tool refuses `lane-disabled`
out of the box; the ON-path test arms the env explicitly. A-2's E2E steps stand (planned, not
executed — human-session-only artifact writes; ceremony-gated). A-3 #9's "count reads 21" is
confirmed correct against the verified base count of 20.

---

## 7. Planner defects found & fixed by this ruling (developer must apply)

| # | Defect | Fix |
|---|---|---|
| 1 | `PromptAssist` naming drift (`phase4-plan.md:16`; `plan-overview.md:103,151,167,260`; `technical-analysis.md:303,757`) | `PromptAssentConfig` / `ENSEMBLE_PROMPT_ASSENT_ENABLED` everywhere (H-11 one-string rule) |
| 2 | `token_display` column in D-1's row shape | **Dropped** — grouped form rendered from the raw at mint only |
| 3 | D-4 "preferably same-transaction" applied to the counters | Counters commit in a **separate** transaction (D-4 blocking rule) |
| 4 | D-5 extraction contract implied top-level `metadata.get("user_id")` | Nested `metadata["discord"]["user_id"]`, REQUIRED, fail-closed `extraction-incomplete` |
| 5 | Action-set inconsistency (BLESSED-1 vs DECIDED-3 vs phase-2) | A-2: `{list, mint, approve, reject}` |
| 6 | P2 resolver defaults OFF vs P4 config defaults ON | One fail-closed OFF default constant consumed by both |
| 7 | Census prose lists 14 pin files (15th exists) | Prose corrected in this change; set re-derived by grep (never copied) |

---

## 8. Decisions pending (Emperor-owned, non-blocking for the developer lane)

1. **Kill-switch default.** Ruled OFF for landing (D-2). If the Emperor wants chat approval
   live immediately at the promote ceremony, the flip is one env var per environment — no code
   change. The council's dissenting position (default ON, house precedent) is recorded above.
2. **Guild-channel approval.** Phase 1 is DM-only (A-1). If the Emperor intends to approve from
   guild channels, say so — the pinned-approver design must be commissioned BEFORE the Phase-1
   lane ships, not retrofitted (a tool-asserted approver hint is rejected as a forgery surface).
3. **Persona surface (D-7).** The commission's ROUTING wording named the memoir persona stack;
   Phase 1 covers classic `prompt_versions` only. Extending to personas is a separate
   commission (own ledger — persona rows write no `prompt_audit_events`).

## 9. Confidence

**High.** Assumption that would flip a ruling: if the Emperor's real approval workflow happens
in **guild channels** rather than DMs, A-1's DM-only scope becomes the binding constraint and
decision 2 escalates from "pending" to "blocking". If the deployment topology ever runs two
daemon processes against one PG, D-1's PG-table choice is the only correct one of the five
options considered (already the ruling). The council's two-councilor consensus is corroborated
by this architect's independent anchor verification (§0) — no claim in this doc rests on a
single uncorroborated source.

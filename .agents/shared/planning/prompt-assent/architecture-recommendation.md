# Architecture Recommendation — `prompt-assent` (The Emperor's Word / Assent)

| Field | Value |
|---|---|
| Date | 2026-11-10 (UTC) · **Amendment pass 1** (plan-council K-findings K-2/K-3/K-7/K-8/K-9 + ride-alongs O-A/O-B/O-D/O-L — see §10) |
| Author | Architect (design authority) — with governor council `architect-council-prompt-assent` (2 councilors, models `agentic` + `coding`, skill `security-design`, both read-only, cross-validated) |
| Worktree | `/home/nea/ensemble-src-wt-prompt-assent` (branch `feature/prompt-assent`, planning @ `255d3fe4a`, base `0efdf106f`) |
| Inputs | `plan-overview.md` · `decisions.md` (D-1…D-9, J-1…J-3) · `risks.md` · `technical-analysis.md` · `research/{approval-path,trust-anchors}.md` · `phase1–4-plan.md` · direct tree verification (§0) · plan-council K-findings (amendment pass) |
| Status | **RULING — binding on the developer lane.** Supersedes every `RECOMMENDED-PENDING-ARCHITECT` / `OPEN-JUDGEMENT` status in `decisions.md`. Original issuance: commit `c6046638a`; this pass amends in place (git history is the version record). |
| Effect | Resolves D-1…D-9 + J-1…J-3; rulings **AR-1** (mint-context binding: channel **and user**) and **AR-2** (action set); closes plan-council criticals K-2/K-3/K-7/K-8/K-9; amends planner defects (§7). |

---

## 0. Anchor sanity-check (verified on the branch tree, not from prose)

The developer trusts THIS table, not older prose. Every claim below was verified by direct
read/grep of `/home/nea/ensemble-src-wt-prompt-assent` at `255d3fe4a` (originals independently
re-derived by both councilors; amendment-pass anchors verified by the architect):

| Claim | Verified value | Notes |
|---|---|---|
| Migration count | **127** `*.sql` in `daemon/migrations/versions/` | Planner's H-1 is correct; the **blueprint's "119" is stale**. `prompt_assent_tokens` makes it **128**. |
| Pin census (SAME-PR) | Prose at `daemon/tools/_tool_registry.py:379-406` lists **14** files; the three canonical greps derive **15** test files + the pack string pair | The 15th is `tests/unit/tools/test_prompt_reload_pins.py`. **Prose correction is mandatory in this change** (H-10). Re-derive by grep at implementation time — never copy from this doc. |
| `PRIVILEGED_TOOL_CATEGORIES` | **20** entries today; `prompt-assent` makes **21** | Includes the literal-text pin `tests/unit/test_memoir_registration.py:374` (`== 20` → `21`) and the pack mutation target `test/packs/metrics_read_mutation_matrix_test.py`. |
| Silent-resume stamp skip | `daemon/manager.py:11503` `if not silent:` | J-2's motivating seam; `:6132-6133` clears the window on every non-user-origin dispatch. |
| Window stamp shape | `daemon/manager.py:6126-6131` — `{source, message_id, stamped_at, expires_at}` | The stamp carries the message id; mint-context derivation (K-2) reads the message row this id selects — daemon-owned evidence, never tool args. |
| `NONCE_TTL_S` | `daemon/tools/upgrade_journal.py:141` = `60*60` | Token TTL SSOT — import it, never re-declare. |
| `classify_user_origin` | `daemon/tools/upgrade_journal.py:4116` | Explicit **no-divergent-copies** contract; its chat set is NOT `constants.CHAT_SOURCE_PREFIXES` (O-5). |
| Auth-method vocabulary | `daemon/repositories/prompt_version/repository.py:503-511` — frozenset `{session, bearer, internal}` | AR/D-8 adds `assent-relay` (Python-side; no CHECK constraint — no migration). |
| CAS + audit co-transaction | `repository.py:1457-1464` (CAS) · `:1493-1511` (audit INSERT **inside** the same `engine.begin()`) | Audit-write failure **rolls back the state change** — why co-transactionality is load-bearing (S-6). |
| F4 internal-actor refusal | `repository.py:1560-1565`, `:1594-1599` | Stays byte-identical; relay lane uses a separate entrypoint. |
| 3-layer ari posture | `daemon/tools/notify_user.py:19-30` · gate order `:703-715` | Precedent for S-10. |
| Discord identity | `adapter.py:994-1021` `_build_external_user_id` (guild form = a **channel**) · nested `discord_meta` built `:1046-1058`, wrapped `:1113-1117` → queue `metadata` JSONB | Extraction contract per D-5. |
| **`is_dm` is guild-absence only** | `adapter.py:1043` `is_dm = guild is None`; **zero** group discrimination in the adapter (no `is_group`/`recipient` hits) | K-3: a group DM (≤10 recipients) classifies `is_dm=True` — the multi-occupant private channel AR-1 must exclude. |
| **`prompt_versions.content_hash` exists** | `models.py:369` (`sha256 hex of content`), computed `repository.py:136-145`, used `:2860` | K-7: copying that column would satisfy SP-1 trivially and **silently no-op J-3** — forbidden (§1 J-3). |
| **Audit soft-reference precedent** | `models.py:464-466`: `version_id … (no FK — audit must survive even when the version row is archived)` | O-B: the token table takes **no hard FK** either. |
| **Diff renderer is router-local** | `routers/prompts.py:431-435` `_unified_diff` (stdlib `difflib`, used at `:605`) | K-7: the tool layer must not import the router — one shared helper is minted (§1 J-3). |
| `create_all` seam | `daemon/api.py` ~`:1130-1150` (NotifyAudit precedent, "N1 REQUIRED seam") | New table ⇒ **no** `_ensure_postgres_columns` entry (boot-abort surface `manager.py:10036-10038` untouched). |

---

## 1. Rulings — D-1…D-9, J-1…J-3, AR-1…AR-2

Compact table first; detail (rationale + overrule cost) follows. **AGREE** = planner's
recommendation ratified; **OVERRULE** = architect decides against it; **AMEND** = ratified
with mandatory changes. (Labels AR-1/AR-2 disambiguate from the plan-overview's acceptance
labels A-1/A-2/A-3 — ride-along O-A.)

| # | Question | Ruling | Verdict |
|---|---|---|---|
| D-1 | Token store | New PG table `prompt_assent_tokens` via `create_all` + own `*.sql`; row shape amended (§2.1) incl. `mint_user_id`, **no hard FK**, **no `token_display`** | AGREE + AMEND |
| D-2 | Kill-switch default | **Default OFF (fail-closed)**, `ENSEMBLE_PROMPT_ASSENT_ENABLED` / `PromptAssentConfig` everywhere | **OVERRULE** planner (planner: default ON) |
| D-3 / J-1 | Token entropy | 8 base32 chars / 40 bits via house `mint_nonce`; 6-char rejected; counter+lockout mandatory regardless — **hourly per-instance bound mechanism ruled (K-9, §1-D-3b)** | AGREE + AMEND |
| D-4 | Single-use point | Transition-then-consume, consume co-transactional via keyword-only hook in `atomic_transition`; **counters commit in a SEPARATE transaction before the guarded transition**; **render-hash re-check lives INSIDE the hook's transaction** (O-L) | AGREE + AMEND |
| D-5 | Sources & user key | Discord-first, lane-side policy on `source_type`; user key from **`metadata["discord"]["user_id"]`** (nested — NOT top-level, NOT `external_user_id`) | AGREE + CORRECTION |
| D-6 / J-2 | Freshness bound | ≤ **10 min** on `stamped_at` (configurable, default 600 s) **in addition to** `expires_at`; fail-closed on absent/unparseable; applies to **mint and approve** (K-2) | AGREE + AMEND |
| D-7 | Surface scope | Classic `prompt_versions` ONLY in Phase 1; persona surface deferred as a recorded gap | CONFIRM |
| D-8 | Actor vocabulary | New lane `assent-relay` + `actor_from_assent_relay` + typed `AssentAttestation` (§2.3); `approve()`/`reject()` unchanged; FE byte-identical; in-process only | AGREE + AMEND |
| D-9 | Audit shape | Keep `APPROVED`/`REJECTED` + `approval_path` discriminator; payload-only JSONB; **mint writes NO audit event** | AGREE |
| J-1 | (in D-3) | 8 chars | AGREE |
| J-2 | (in D-6) | ≤10 min | AGREE |
| J-3 | Content-hash binding | **In-scope Phase 1** — hash a **NAMED CANONICAL RENDER** from one shared helper (mint response + hash + REST diff route); **effective side INCLUDED**; `prompt_versions.content_hash` copy **forbidden** (K-7) | AGREE + AMEND |
| **AR-1** | Mint-context binding — **channel AND user** | Token bound to mint `(source_id, channel_id, 1:1-DM, user_id)`; **mint is gated** (K-2); group DMs excluded by an adapter-side discriminator (K-3); approve requires same source+channel **and same daemon-derived `user_id`** (K-8); refusals `mint-requires-private-channel`, `token-channel-mismatch`, `token-approver-mismatch` | NEW RULING (amended pass 1) |
| **AR-2** | Action set | ONE tool, `action ∈ {list, mint, approve, reject}` | NEW RULING |

### D-1 — token store (AGREE + AMEND)
New PG table `prompt_assent_tokens`; hash-only storage (`token_hash = sha256(token)`, raw token
exists exactly once — in the mint response). Own `*.sql` migration (128th) + `create_all` seam
import (NotifyAudit precedent block) + **zero** `_ensure_postgres_columns` entries.
**Amendments (mandatory):**
1. **NO `token_display` column** — the grouped render minus dashes *is* the raw secret; render
   the grouped form from the raw token at mint time only.
2. **Partial unique index** `ON (version_id) WHERE status='live'` — single-active-token is
   structural against double-mint races.
3. **Mint-context columns**: `mint_source_id`, `mint_channel_id`, `mint_is_dm`,
   **`mint_user_id`** (K-8), `minted_external_user_id`.
4. **`render_sha256`** — sha256 of the canonical render (J-3/K-7). One name everywhere: token
   table column, mint response, attestation, audit payload all say `render_sha256`. The old
   draft's `content_hash`/`content_sha256` naming is **retired** — it collided with
   `prompt_versions.content_hash` and invited the silent SP-1 no-op.
5. **No hard FK to `prompt_versions`** (O-B) — indexed soft reference, per the audit-ledger
   precedent (`models.py:464-466`: audit must survive version archival; the same holds for
   token forensics).
6. Indexes: `UNIQUE (token_hash)` · partial `UNIQUE (version_id) WHERE status='live'` ·
   `(version_id, status)` — names byte-identical to the SQLModel `__table_args__`.

Re-mint **supersedes and revokes** the prior token (`superseded_at` + status flip; prior reads
`token-superseded`, deliberately distinct from `token-already-used`), re-renders the diff, and
is refused while a live token exists unless explicitly forced (`mint-live-token-exists`).
GC mirrors the promote discipline: only delete what is provably dead; **retain consumed rows**.

*Overrule cost (rejected alternatives):* columns on `prompt_versions` reopen the
one-transaction boot-abort surface on the hottest prompt table; in-memory loses the 60-minute
deliberation window to a routine restart; file/KV stores are wrong mediums (R-2…R-5 upheld).

### D-2 — kill-switch default (OVERRULE: default OFF)
`ENSEMBLE_PROMPT_ASSENT_ENABLED` / `PromptAssentConfig` — the planner drifted to `PromptAssist`
in five places (`phase4-plan.md:16`, `plan-overview.md:103,151,167,260`,
`technical-analysis.md:303,757`); `prompt-assent` wins everywhere (H-11). Semantics: read **at
call time**; the tool **stays registered** when off (refusal `lane-disabled`, never
construction-absence); **`mint` is refused too** when off; the switch sits **behind** the ari
gate; **indeterminate/invalid values fail CLOSED** (an authority lane, not a convenience lane).
A disabled lane refuses cleanly with a typed code naming the env var — never silent
fall-back-to-FE confusion.

**Default OFF for the initial landing.** This is the first lane in the tree that can change a
live agent's own guardrails through a chat message; the house's most recent Emperor-ratified
posture for a novel authority surface (Decision Module P1, 2026-11-09: *dormant by default,
nothing decides anything*) is exactly this shape; the FE lane fully covers approvals meanwhile;
the flip to ON is one env var + restart per environment. **The planner's P2/P4 default split
(defensive resolver off / config class on) is a defect under either default and is reconciled
here: ONE fail-closed OFF default constant consumed by both.**

*Overrule cost:* the doctrine's "working path is conversation" needs one deliberate arming act
per environment first. Flipping later is a one-line release decision; the exposure delta of
having started ON is only the pre-first-arming probation window.

### D-3 / J-1 — entropy (AGREE + AMEND)
8 base32 chars / 40 bits via house `mint_nonce` (`upgrade_journal.py:205-208`); the
commission's ~6-char example is **rejected**. Echo matching reuses **`nonce_in_content`**
(`upgrade_journal.py:231-246`) verbatim with `nonce_normalize` semantics (dash/case-tolerant) —
no new regex is minted. Entropy is not the security boundary (factors 2+3 are;
`manager.py:6132-6133`), but 8 is free and closes the brute-force conversation.

**D-3b — the mismatch-budget mechanism (K-9 micro-ruling).** The per-relay-instance hourly
bound is: an **in-RAM hourly counter keyed by relay `instance_id`**, owned by the **tool layer
(P2)**, read/incremented on every token-mismatch refusal, configurable via
`PromptAssentConfig.hourly_mismatch_budget` (default **10**), exhaustion → typed refusal
**`mismatch-budget-exceeded`** (matrix cell §4). In-RAM is the ruled medium deliberately: it is
per-process defense-in-depth, not a trust decision — a restart clearing the budget bounds an
attacker who cannot restart the daemon, and the durable leg is the PG per-token `attempts` +
lockout (D-4). Disclosed limitation: multi-daemon deployments do not share the RAM budget
(per-instance by design). Phase ownership: mechanism in **P2** (tool gate), config key lands
with `PromptAssentConfig` in **P4** (P2's defensive resolver defaults it to 10 when the class
is absent — same fail-closed resolver pattern as the kill-switch).

### D-4 — consumption & atomicity (AGREE + AMEND, with blocking rules)
Invariant: **transition-then-consume** — refusals never burn the token; the consume is
**co-transactional** with the CAS + audit INSERT. Mechanism: `atomic_transition` gains a
**narrow keyword-only, private, default-`None` typed hook** executed **inside its existing
transaction** — one code path, no forked CAS+audit copy (the no-divergent-copies doctrine).
The hook performs, **in order**: (i) the **render-hash re-check** — re-render the canonical
diff **from the row as read inside the transaction** and compare to the token's
`render_sha256`; mismatch → raise → rollback → `token-content-mismatch` (O-L: the
authoritative check lives ONLY here — the pre-transition layer does not duplicate it, so
shown/hashed/approved bytes cannot diverge); (ii) the consume-CAS
(`UPDATE … SET status='consumed', consumed_at=…, consumed_by_message_id=… WHERE token_id=?
AND status='live'`; rowcount 0 → raise → rollback → typed refusal). FE byte-identity with the
hook absent is pinned by test (O-4).

**BLOCKING rule 1:** the **attempt-counter increment and lockout revocation commit in their
own transaction OUTSIDE the guarded transition** — before it opens, on the refusal path.
Inside it, every refusal's rollback resets the counter and the lockout never fires (D-3's
defense-in-depth would be silently inert).

*Overrule cost:* consume-then-transition burns the token on a non-decision; a fully separate
consume transaction admits a live token on a decided row (powerless but loose); forking the
CAS+audit duplicates security-critical logic.

### D-5 — sources & the approving-user key (AGREE + CORRECTION)
Discord-first, a **lane-side policy on the daemon-owned `source_type`** (the same registry
`classify_user_origin` matches) — never a source-id prefix re-derivation (O-5). Extraction
contract (from the message row the window's `message_id` selects — daemon-owned evidence):
- `source_id` = the window source's prefix before the first `":"`;
- `user_id` = **`message_metadata["discord"]["user_id"]`** — NESTED (adapter `:1046-1058`
  builds, `:1113-1117` wraps, queue stores `metadata` JSONB); a top-level
  `metadata.get("user_id")` returns `None`. **REQUIRED — fail closed** `extraction-incomplete`;
- `guild_id`, `channel_id`, `channel_type`, `is_dm`, `message_id` (platform-side) from the
  same dict; `user_name`/display name cosmetic only, never authority;
- `external_user_id` never the approving-user key (guild form is a CHANNEL,
  `adapter.py:994-1021`); it remains the destination identity.
`approver_subject = f"{source_id}:{user_id}"`.

### D-6 / J-2 — freshness (AGREE + AMEND)
`window["stamped_at"]` within **≤10 minutes** (configurable, default 600 s) of the tool call,
**in addition to** `expires_at`; refusal `user-origin-window-stale`; absent/unparseable =
stale, never valid; UTC via house `parse_iso_utc`; evaluated **inside factor 2**.
**Amendment (K-2): the freshness requirement applies to BOTH `mint` and `approve`** — the
mint gate runs the same factor-2 checks (window present, user-origin, unexpired, fresh). The
seam is verified: silent resume skips the stamp (`manager.py:11503`), so a paused→resumed ari
can otherwise carry an earlier turn's authorization for the full `NONCE_TTL=3600`. *Overrule
cost:* promote parity accepts a 60-minute-old window — too loose for a lane that changes live
personas in one chat turn.

### D-7 — surface scope (CONFIRM)
Classic `prompt_versions` only in Phase 1. The persona surface is a different stack (own
table, repo, state vocabulary, **no `prompt_audit_events`** — `memoir/repository.py:220-262`)
and would require a second provenance mechanism. Conditions: the deferral is recorded as an
explicit known gap in the runbook; the commission's persona-stack mis-naming is surfaced (§8).

### D-8 — actor model (AGREE + AMEND)
New lane value `assent-relay` (`AUTH_METHOD_ASSENT_RELAY` + `_AUTH_METHODS` widening,
Python-side; no CHECK constraint — no migration); the **only** constructor
`actor_from_assent_relay(...)`; `ActorContext.__post_init__` requires the attestation fields
on this lane; the `atomic_transition` approver clamp widens to include it; `approve()`/
`reject()` unchanged (F4 intact); FE byte-identical; relay path in-process (inner_soul
pattern), never HTTP (R-1 upheld). Tool args carry **no approver identity, source, message id,
or user id** (anti-forgery analogue of the repo's R3). Attestation field set §2.3. The
belt-and-braces `self-approval-attempt` assertion is kept but is **near-vacuous as written**
(an assertion, not the gate); the load-bearing defences are: agent-originated dispatch pops
the window (`manager.py:6132-6133`), the ari-EXACTLY layer-3 gate, and — since pass 1 — the
**daemon-derived user equality** (AR-1/K-8). Tests pin the construction properties; the
runbook carries the "not the gate" language.

### D-9 — audit (AGREE)
Keep `APPROVED`/`REJECTED` + `approval_path: "chat-assent" | "fe-session"` (new types would
make every `event_type=="APPROVED"` consumer silently drop relayed approvals — the forgetting
bug); payload-only JSONB extend; `token_fingerprint = sha256(token)[:12]`, never raw;
`reject_reason` top-level, **≤500 chars**, never concatenated into other fields.
**Mint writes NO `prompt_audit_events` row — the token table IS the mint ledger.** Payload
key set §2.4, including the actor/authority split: `actor_*` carries `assent-relay` + relay
subject; payload `approver_subject` carries the **human**.

### J-3 — canonical-render binding (AGREE + AMEND — K-7)
Bind the token to the **named canonical render**, not to a content re-read:
1. **One shared helper** (e.g. `daemon/services/prompt_version_diff.py`) owns the render:
   a pure lift-and-shift of `_unified_diff` (`routers/prompts.py:431-435`, stdlib `difflib`) +
   the canonical input assembly (pending content, effective content, item key, labels, and a
   **render format tag `"v1"`**). The REST diff route **imports the helper** (FE output
   byte-identical — pinned by test) and the tool's mint path renders through it. The tool
   layer never imports the router.
2. `render_sha256 = sha256(canonical_render)` — computed at mint **from the exact bytes placed
   in the mint response**, re-checked at approve **inside the D-4 hook's transaction** (O-L).
   Surfaced to the human in the mint response and carried in the audit payload.
3. **The effective side IS included** in the render. Rationale: the Emperor approves "pending
   vs effective-X"; if the effective side moves underneath (a newer version approved), the
   pending row's meaning changes and the outstanding token MUST invalidate
   (`token-content-mismatch`) rather than silently approve against a different baseline. The
   CAS alone cannot see effective-side motion.
4. **Copying `prompt_versions.content_hash` is FORBIDDEN** (`models.py:369` — sha256 of raw
   content): it satisfies SP-1's letter and silently no-ops J-3 (a pending-content mutation
   after mint WOULD trip it, but an effective-side mutation or a rendered-view divergence
   would not, and the shown-bytes guarantee disappears). The planner rewrites SP-1 to pin the
   canonical-render hash; a test pins that the token column is NOT populated from
   `prompt_versions.content_hash`.

*Cost of deferring J-3 (rejected):* a prompt-injected ari lands a valid approval against a
stale/truncated diff and the ledger records consent never given — the worst failure mode of a
trust-model change.

### AR-1 — mint-context binding: channel AND user (NEW; amended pass 1)
**The seam (council-found, then plan-council-sharpened): `classify_user_origin` authenticates
the CHANNEL, not the HUMAN** — and `is_dm` alone does not fix it, because a Discord **group
DM** (≤10 recipients) classifies `is_dm=True` (`adapter.py:1043`, no group discrimination
anywhere in the adapter). A second occupant of the bound channel would satisfy all three
commission factors **and** a channel-only check, with faithful-looking provenance. The ruling
closes the seam with FOUR conjunctive legs:

1. **Mint is gated (K-2).** `mint` requires a live factor-2 user-origin window — same
   freshness semantics as approve (D-6 amendment). The mint context
   `(source_id, channel_id, is_dm, user_id, external_user_id)` is derived **from the window's
   message row** (`manager.py:6126-6133` stamp → message `metadata["discord"]`) — **NEVER from
   tool args**. Without this, a prompt-injected ari mints for any CORE pending row, renders
   diff+token into any reachable surface, and supersedes the Emperor's live token (DoS +
   authority-fabrication primitive). With it, a mint can only occur inside a genuine user turn
   in a private channel; a supersede-DoS degenerates into a re-show of the same diff in the
   same DM. `list` remains ungated-by-window (read-only, ari-gated only).
2. **1:1-DM discrimination (K-3).** Private = `is_dm AND NOT is_group_dm`. The adapter gains a
   lane-scoped discriminator — `is_group_dm: bool` (or `recipient_count`) added to the nested
   `discord_meta` at the `:1046-1058` build site (additive metadata, no schema change) —
   **owned by P1** (the mint-context derivation consumes it), with a **pre-ship runtime
   confirmation** (live probe: 1:1 DM vs group DM must classify distinctly). **Fail-closed
   rule: absent/unparseable discriminator = NOT private** (mirrors D-6's absent-stamp rule) —
   older rows simply cannot mint/approve until the discriminator ships.
3. **User equality (K-8 — lane-owner ruling, CLOSED, not an accepted residual).** The token
   row stores `mint_user_id` (daemon-derived). At approve, the approving turn's daemon-derived
   `user_id` (from the window's message row — the same evidence chain as the channel binding,
   **not** a forgery surface: neither value is ever tool-supplied) must equal `mint_user_id` —
   refusal **`token-approver-mismatch`** (matrix cell §4). Combined with legs 1–2: the
   approving human IS the mint-witnessed human, in the mint-witnessed 1:1 channel.
4. **Refusals**: `mint-requires-private-channel` (mint-side: not a 1:1 DM — pinned at step M-4
   of the pipeline), `token-channel-mismatch` (approve-side channel inequality),
   `token-approver-mismatch` (approve-side user inequality).

Guild-channel approval with a **pinned approver identity** remains a deferred design; a
tool-asserted approver hint is **rejected** as a forgery surface. Legitimate cross-channel or
cross-user use is a re-mint — cheap, and it re-shows the diff.

### AR-2 — action set (NEW, reconciling a planner inconsistency)
ONE tool `prompt_assent`, `action ∈ {list, mint, approve, reject}`. `list` = read (any
channel, ari-gated); `mint` = mutating, ledgered in the token table; `approve`/`reject` =
mutating, ledgered in `prompt_audit_events`. One category grants all four (ari is the only
consumer; split the category only if a second read-only consumer ever appears).

---

## 2. The architecture the developer builds against

### 2.1 Schema — `prompt_assent_tokens` (128th migration)

```
token_id              UUID PK                    (uuid4 hex)
version_id            indexed SOFT reference → prompt_versions   (NO hard FK — O-B,
                      audit-survival precedent models.py:464-466)
agent_id, version_tag, artifact, version          (item key, denormalized for audit legibility)
token_hash            sha256(token), UNIQUE       (raw token NEVER stored; NO token_display column)
render_sha256         sha256(canonical render)    [J-3/K-7 — NEVER prompt_versions.content_hash]
mint_source_id, mint_channel_id, mint_is_dm, mint_user_id, minted_external_user_id   [AR-1]
issued_at, ttl_expires_at                          (TTL = NONCE_TTL_S imported SSOT, 60 min)
superseded_at, consumed_at, consumed_by_message_id
attempts              int default 0               (per-token mismatch count)
status                live|consumed|superseded|revoked
-- indexes (names byte-identical to __table_args__):
--   UNIQUE (token_hash)
--   UNIQUE (version_id) WHERE status='live'      -- partial: single-active structural
--   (version_id, status)
```

Migration `YYYYMMDD_HHMMSS_create_prompt_assent_tokens.sql`: UP first / DOWN last, idempotent
statements; plus the `create_all` seam import at `daemon/api.py` ("N1 REQUIRED seam"); **zero**
`_ensure_postgres_columns` entries.

### 2.2 The gate pipeline (order is fixed; mint and approve branches)

```
0. ari layer-3 in-body gate        agent_id == "ari"          (generic not-ari envelope)
1. kill-switch                     ENSEMBLE_PROMPT_ASSENT_ENABLED, call-time, fail-closed → lane-disabled
2. action-shape validation         per-action args            → invalid-arguments
3. FACTOR GATES — mint AND approve (K-2); enumerate ALL failures in one refusal:
   3a. user_confirmed == true            (approve/reject only)  → user-confirmation-missing
   3b. window present for this instance                        → no-user-origin-turn (names source)
   3c. window source classified user-origin by UNFORKED classify_user_origin
   3d. window unexpired (expires_at)                           → user-origin-window-expired
   3e. window fresh: stamped_at ≤ now-600s (configurable)      → user-origin-window-stale
   3f. the window's message_id row readable; source matches    → assent-verification-unavailable
   3g. [approve only] token echoed verbatim in THAT message's content (nonce_in_content)
4. MINT branch (K-2) — after the factors; daemon-derived context only:
   M-4a. item: row exists, CORE class, state == pending_approval  → item-not-found /
         item-not-assentable / item-not-pending
   M-4b. no live token unless forced                             → mint-live-token-exists
   M-4c. private check on the WINDOW-derived context: is_dm AND NOT is_group_dm
         (absent discriminator = NOT private — fail closed)      → mint-requires-private-channel
   M-5.  mint context (source_id, channel_id, is_dm, user_id, external_user_id) taken
         FROM THE WINDOW'S MESSAGE ROW — never from tool args
   M-6.  own transaction: token INSERT (token_hash, render_sha256, mint-context, TTL) +
         supersede flip of any prior live token. NO audit event (token table = mint ledger).
   APPROVE/REJECT branch — after the factors; no item-state leak to unproven callers:
   4a. row exists; CORE class                                   → item-not-found / item-not-assentable
   4b. state == pending_approval                                → item-not-pending / already-decided
   4c. token row live, item-bound, unexpired                    → token-mismatch / token-item-mismatch /
                                                                  token-expired / token-superseded /
                                                                  token-already-used
   4d. channel: approving turn's (source_id, channel) == mint's  → token-channel-mismatch
   4e. user: approving turn's daemon-derived user_id == mint_user_id → token-approver-mismatch
   4f. private check on the approving context (same rule as M-4c) → token-channel-mismatch
   4g. lockout: per-token attempts < 5                          → token-locked
   4h. hourly budget: per-relay-instance mismatches < config    → mismatch-budget-exceeded
5. ATTESTATION minted by the assent service (never from tool args) — §2.3
6. COUNTERS (refusal path ONLY): per-token attempts++ / lockout revocation / hourly budget —
   committed in a SEPARATE transaction BEFORE any guarded transition opens. Refusals never
   burn the token. (D-4 blocking rule; O-D ordering — counters precede the transition.)
7. GUARDED TRANSITION (success path; atomic_transition + keyword-only hook, ONE engine.begin()):
   CAS on prompt_versions (WHERE state='pending_approval') + audit INSERT + [in-hook, in order]
   (i) render-hash re-check — re-render from the row AS READ IN THE TRANSACTION, compare to
       render_sha256 → token-content-mismatch on divergence (the ONLY render-hash site — O-L);
   (ii) consume-CAS on the token row (WHERE token_id AND status='live').
   Any failure → rollback → typed refusal.
```

Every terminal path emits **ONE frozen INFO line** — counts and flags only, never persona or
diff content; never the raw token.

### 2.3 `AssentAttestation` (minted only by the assent service, post-gate)

```
item_binding        {version_id, agent_id, version_tag, artifact, version}
render_sha256                                         [J-3/K-7]
human_message_id    daemon-minted message id of the approving turn
relay_source_message_id   PLATFORM-side (Discord) message id — the daemon queue id is
                          ephemeral/wiped at boot; without this the forensic chain breaks
human_identity      {source_id, user_id, user_name, guild_id, channel_id, channel_type,
                     is_dm, external_user_id}         [nested metadata["discord"], D-5]
approver_subject    f"{source_id}:{user_id}"          [proven == mint_user_id before minting — AR-1 leg 3]
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
  "render_sha256": "…",
  "reject_reason": "≤500 chars, top level, reject only"
}
```

Structural pin **SP-2**: no UPDATE/DELETE against `prompt_audit_events` in this lane.
Structural pin **SP-1 (rewritten per K-7)**: the token's render binding is the
**canonical-render sha256** (shared helper, mint-response bytes, in-txn re-check) — NOT
`prompt_versions.content_hash`. The audit-write failure path is fail-closed
(`audit-unavailable`, no state change); the co-transactional INSERT means an audit failure
rolls the state change back with it.

### 2.5 Registration (SAME-PR, one commit)

1. `PRIVILEGED_TOOL_CATEGORIES` += `"prompt-assent"` (entry **#21**; 20 verified today) —
   comment block in the house style.
2. `CATEGORY_MODULES` += the module mapping; `DYNAMIC_TOOL_NAMES` / `KNOWN_TOOL_NAMES` +=
   `prompt_assent`; the `create_instance_tools` extend lands **before** help/docs/filter
   assembly (decorator-only registration is silently invisible).
3. `agents/ari/meta.json` `tools.allow` **tail** entry (after `prompt-reload`); the
   trailing-entry ARRAY-ORDER pin (`tests/unit/test_board_registration.py:294-312`) gains it;
   the ari-ONLY census across `agents/*/meta.json`.
4. In-body `agent_id == "ari"` gate (layer 3).
5. **Pin set derived BY GREP at implementation time** — expected shape at this base: 15 test
   files + the pack string pair; **all count pins 20 → 21** (incl. the memoir literal pin
   `test_memoir_registration.py:374`); **correct the census prose** at
   `_tool_registry.py:379-406`.
6. New pins: `tests/unit/tools/test_prompt_assent_registration.py` (exact-set + count +
   ari-ONLY census) and `tests/unit/test_prompt_assent_lane.py` (matrix).

### 2.6 Kill-switch

`PromptAssentConfig.enabled` (env `ENSEMBLE_PROMPT_ASSENT_ENABLED`) — default **False**;
fail-closed on indeterminate; read at call time; tool stays registered; mint refused too;
checked behind the ari gate. ONE default constant consumed by both the P2 defensive resolver
and the P4 config class. `PromptAssentConfig.hourly_mismatch_budget` (default 10) rides the
same class (D-3b).

---

## 3. Refusal taxonomy (complete; typed codes, no state change, no token burn)

`lane-disabled` · `not-ari` (generic envelope) · `invalid-arguments` ·
`user-confirmation-missing` · `no-user-origin-turn` (names observed source + classifier detail)
· `user-origin-window-expired` · `user-origin-window-stale` ·
`assent-verification-unavailable` · `token-mismatch` · `token-expired` ·
`token-already-used` · `token-superseded` · `token-item-mismatch` ·
`token-content-mismatch` · `token-channel-mismatch` · **`token-approver-mismatch`** (AR-1) ·
`mint-requires-private-channel` (mint-side non-1:1-DM, incl. group DMs and
absent-discriminator fail-closed) · `mint-live-token-exists` (unless forced) · `token-locked`
· **`mismatch-budget-exceeded`** (D-3b) · `item-not-found` · `item-not-assentable` ·
`item-not-pending` (carries current state) · `already-decided` · `self-approval-attempt`
(belt-and-braces, documented as not-the-gate) · `extraction-incomplete` (fail-closed missing
nested identity) · `audit-unavailable`.

---

## 4. Security analysis — mandatory test-matrix mapping

Every mandatory cell maps to a design element; each carries a **counter-mutation** that
reddens it (house mutation rule):

| Cell | Design element(s) that defeat it | Counter-mutation |
|---|---|---|
| **Approve happy path, real token echo** | Factors 2+3 (window read; exact-`message_id` echo via `nonce_in_content`) + D-4 consume + D-9 payload + AR-1 channel+user match | Remove the message-exactness check → a token present **only in the tool arguments** must redden the cell (the single most important pin) |
| **Wrong token** | Attempts counter (**separate transaction** — the regression this guards) + `token-mismatch`, no burn | Move the counter into the guarded transaction → the lockout test reddens |
| **Expired token** | `ttl_expires_at` vs imported `NONCE_TTL_S`; distinct from the window clocks | Break the TTL comparison |
| **Non-pending row** | CAS `WHERE state='pending_approval'`; `item-not-pending`/`already-decided` via state read-back; no audit event | Drop the state predicate from the CAS |
| **Agent-self-approval attempt** | `self-approval-attempt` code **plus** the pinned construction property: agent-originated dispatch pops the window (`manager.py:6132-6133`) + ari-EXACTLY layer 3 | Make a non-user-origin dispatch leave the window alive → reddens |
| **Non-user-origin turn** | Factor-2 armability via UNFORKED `classify_user_origin`; refusal names the observed source | Fork/re-derive the classifier → the unforked-reuse pin reddens |

**Amendment-pass cells (mandatory additions):**

| Cell | Guards | Expected |
|---|---|---|
| **Mint without a user-origin window** (K-2) | The mint gate — an agent-originated (or stale-window) turn cannot mint | `no-user-origin-turn` / `user-origin-window-stale`; NO token row written; prior live token NOT superseded |
| **Mint in a group DM / non-private channel** (K-3) | The 1:1-DM discriminator + fail-closed-absent rule | `mint-requires-private-channel`; no token row |
| **Approve by a different user of the same 1:1 channel** (K-8) | `mint_user_id` equality, daemon-derived both sides | `token-approver-mismatch`; no state change, no burn |
| **Hourly mismatch budget exhaustion** (K-9) | The in-RAM per-instance budget | `mismatch-budget-exceeded` on the 11th mismatch within the hour |
| **Pending-content mutation after mint** (J-3) | In-txn render-hash re-check | `token-content-mismatch`; rollback — no state change, no audit row |
| **Effective-side mutation after mint** (J-3/K-7) | Effective side included in the canonical render | `token-content-mismatch`; the counter-mutation that proves the render is NOT the `prompt_versions.content_hash` copy |
| **Token column populated from `prompt_versions.content_hash`** (K-7 pin) | SP-1 rewritten | The mint test reddens if the column is fed the wrong source |

### risks.md top trio — mitigations of record

| Risk | Mitigations (cumulative, this doc) |
|---|---|
| **R-1 — the lane can approve Ari's own guardrails** | Deliberate-by-construction stack: 40-bit token shown **with the diff** whose `render_sha256` is surfaced (J-3); **mint gated + 1:1-DM-only + daemon-derived user equality** (AR-1) so diff+token render only to the mint-witnessed human in the mint-witnessed private channel; ≤10-min freshness on mint AND approve (D-6); **default-OFF kill-switch** (D-2); full ledger chain (D-8/D-9); ari-EXACTLY 3-layer (S-10). Residual accepted and ledgered: the lane's purpose IS to approve guardrails — after-the-fact detectability is the honest backstop |
| **R-2 — window staleness via silent resume** | D-6/J-2 ≤10-min `stamped_at` bound on both actions, fail-closed on absent/unparseable; actionable refusal (re-show the item, ask again) |
| **R-3 — guessing loop** | 40-bit token (D-3); per-item attempt counter + lockout-at-5 (revokes the live token → fresh mint → fresh human turn) — **counters in their OWN transaction** (D-4); **per-relay-instance hourly budget, in-RAM, configurable, default 10 — mechanism ruled (D-3b/K-9)**; refusals never burn |

### The channel+user seam (closed by AR-1, pass 1)
Original council seam (channel-authenticates-not-the-human) is now closed twice over: 1:1-DM
discrimination (K-3) + daemon-derived user equality (K-8). Residual after AR-1: someone with
control of the **Emperor's own Discord account** can approve — that is account compromise, not
a lane defect; the ledger records exactly who echoed the token (detection story). **K-8 is
CLOSED, not an accepted residual** (lane-owner ruling upheld).

---

## 5. FE-unchanged proof obligations

- FE routes byte-identical: `_require_auth_on` → `require_scope("prompts:approve")` →
  `actor_from_request` → `svc.approve` → 403 on non-session (`daemon/routers/prompts.py:616-697`);
  proven by diff against base + existing FE suites passing byte-identically.
- The REST diff route **imports the shared canonical-render helper** (K-7) — a pure
  lift-and-shift of `_unified_diff`; FE diff output byte-identity is **pinned by test**.
- `approve()`/`reject()` semantics unchanged; the relay lane enters via a separate
  attestation-bearing entrypoint, never raw `svc.atomic_transition` (F2 lesson).
- The `atomic_transition` hook is default-absent → FE path provably untouched (pinned by test).
- **FE/chat concurrency:** the CAS serializes; the loser gets `already-decided`; the losing
  chat path leaves its token unconsumed; exactly one decision row lands.

---

## 6. Acceptance (delta to the planner's A-1…A-3)

The planner's matrix stands with the amendment-pass additions (§4 table 2) plus the six
original counter-mutations. A-3 #8 amended: default state = **OFF** → `lane-disabled` out of
the box; the ON-path test arms the env explicitly. **New deliverable:** the adapter
`is_group_dm` discriminator + pre-ship runtime confirmation (P1-owned, K-3). A-2's E2E steps
stand (planned, not executed — human-session-only artifact writes; ceremony-gated). A-3 #9's
"count reads 21" confirmed against the verified base count of 20.

---

## 7. Planner defects found & fixed by this ruling (developer must apply)

| # | Defect | Fix |
|---|---|---|
| 1 | `PromptAssist` naming drift (`phase4-plan.md:16`; `plan-overview.md:103,151,167,260`; `technical-analysis.md:303,757`) | `PromptAssentConfig` / `ENSEMBLE_PROMPT_ASSENT_ENABLED` everywhere (H-11) |
| 2 | `token_display` column in D-1's row shape | **Dropped** — grouped form rendered from the raw at mint only |
| 3 | D-4 "preferably same-transaction" applied to the counters | Counters commit in a **separate** transaction (D-4 blocking rule) |
| 4 | D-5 extraction contract implied top-level `metadata.get("user_id")` | Nested `metadata["discord"]["user_id"]`, REQUIRED, fail-closed `extraction-incomplete` |
| 5 | Action-set inconsistency (BLESSED-1 vs DECIDED-3 vs phase-2) | AR-2: `{list, mint, approve, reject}` |
| 6 | P2 resolver defaults OFF vs P4 config defaults ON | One fail-closed OFF default constant consumed by both |
| 7 | Census prose lists 14 pin files (15th exists) | Prose corrected in this change; set re-derived by grep |
| 8 | SP-1 pins a content re-read (`prompt_versions.content_hash`) that silently no-ops J-3 (K-7) | SP-1 rewritten: canonical-render sha256 from the shared helper; column-copy forbidden + pinned |
| 9 | Phase plans carried no mint gate / group-DM check / user binding / budget mechanism (K-2/K-3/K-8/K-9) | §2.2 mint branch, AR-1 legs 1–3, D-3b — planner transcribes into the phase work-orders |

---

## 8. Decisions pending (Emperor-owned, non-blocking for the developer lane)

1. **Kill-switch default.** Ruled OFF for landing (D-2). Flip at/after the ceremony is one env
   var per environment — no code change. The council's dissenting ON-position is recorded in D-2.
2. **Guild-channel approval.** Phase 1 is 1:1-DM-only (AR-1). If the Emperor intends to approve
   from guild channels, the pinned-approver design must be commissioned BEFORE Phase 1 ships.
3. **Persona surface (D-7).** Phase 1 covers classic `prompt_versions` only; extension is a
   separate commission (own ledger — persona rows write no `prompt_audit_events`).

## 9. Confidence

**High.** The pass-1 amendment closed the channel/user seam twice (1:1-DM + user equality) —
the strongest remaining assumptions are operational, not architectural: (i) the adapter
`is_group_dm` discriminator classifies group DMs correctly at runtime (pre-ship confirmation
task; the fail-closed-absent rule bounds a miss to availability, not authority); (ii) the
Emperor's real approval workflow is DM-based (decision 2). If (ii) is false, AR-1's scope
escalates from pending to blocking. Multi-daemon correctness rests on D-1's PG-table choice —
already the ruling of five options considered.

---

## 10. Amendment log — pass 1 (plan-council K-findings; single-writer: this file only)

| Finding | Ruling | Rationale | Where transcribed |
|---|---|---|---|
| **K-2** — mint ungated; channel provenance unspecified | **CLOSED.** `mint` requires a live, fresh factor-2 user-origin window; mint context derived from the window's message row (`manager.py:6126-6133` → `metadata["discord"]`), never tool args; mint branch added at pinned layers M-4…M-6; `mint-requires-private-channel` pinned at M-4c | Without the gate, a prompt-injected ari mints for any CORE pending row, renders diff+token into any reachable surface, and supersedes the live token (DoS + authority fabrication). With it, a supersede degenerates into a same-DM re-show | §1 D-6 amendment · §1 AR-1 leg 1 · §2.2 mint branch · §4 added cell. Planner → P1 (context derivation), P2 (tool gate), phase-3 gate tests |
| **K-3** — group DMs pass `is_dm` (`adapter.py:1043`) | **CLOSED.** Adapter surfaces `is_group_dm` (or recipient count) in the nested `discord_meta` at the `:1046-1058` build site — lane-scoped, additive, **P1-owned**, with a pre-ship runtime confirmation (1:1 vs group probe). Private = `is_dm AND NOT is_group_dm`; **absent discriminator = NOT private** (fail closed) | A group DM is exactly the multi-occupant private channel AR-1 exists to exclude; verified zero group discrimination in the adapter today. The fail-closed-absent rule bounds a stale discriminator to availability, never authority | §1 AR-1 leg 2 · §2.2 M-4c/4f · §3 taxonomy note · §4 cell. Planner → P1 work-order + runtime-confirmation task |
| **K-7** — SP-1 vs J-3 contradiction; no canonical-rendering seam | **CLOSED.** One shared helper (pure lift-and-shift of `_unified_diff`, `routers/prompts.py:431-435`) owns the **named canonical render** (pending + **effective** sides, item key, labels, format tag `v1`); mint response, `render_sha256`, and the REST diff route all use it (FE diff output byte-identity pinned). Copying `prompt_versions.content_hash` (`models.py:369`) is **forbidden** and pinned; SP-1 is rewritten by the planner | The column copy satisfies SP-1's letter and silently no-ops J-3 (shown-bytes guarantee gone; effective-side motion invisible). The tool layer must not import the router; one helper = one render truth | §0 anchor rows · §1 J-3 · §2.1 `render_sha256` · §2.4 SP-1 note · §4 cells (effective-side mutation; column-source pin). Planner → SP-1 rewrite, P1/P3 helper task |
| **K-8** — missing user binding (lane-owner ruling: CLOSE) | **CLOSED — not an accepted residual.** Token row stores daemon-derived `mint_user_id`; approve requires the approving turn's daemon-derived `user_id` (same window-message evidence chain — not a forgery surface; neither side is tool-supplied) to equal it; refusal `token-approver-mismatch`; matrix cell added | Channel binding alone still authenticates the room; with K-3 residual occupants, a second member satisfied all three factors + channel check. User equality closes it: the approver IS the mint-witnessed human | §1 AR-1 leg 3 · §2.1 `mint_user_id` · §2.2 step 4e · §2.3 approver_subject note · §3 · §4 cell · §9 residual rewrite. Planner → P1 (column), P3 (gate), P4 (cell) |
| **K-9** — hourly mismatch bound had no mechanism | **CLOSED.** In-RAM hourly counter keyed by relay `instance_id`, owned by **P2** (tool gate), configurable `PromptAssentConfig.hourly_mismatch_budget` (default 10; P4 lands the class, P2's resolver defaults it), refusal `mismatch-budget-exceeded`, matrix cell | Simplest credible medium: per-process defense-in-depth, not a trust decision; the durable leg is the PG per-token attempts + lockout. Disclosed: multi-daemon deployments do not share the RAM budget (per-instance by design); restart clears it | §1 D-3b · §2.2 step 4h · §2.6 · §3 · §4 cell. Planner → P2 work-order, P4 config key |
| **O-A** — label collision (doc AR-1/AR-2 vs plan-overview A-1/A-2/A-3) | Renamed this doc's new rulings **AR-1/AR-2**; a disambiguation note sits at the §1 table head | Two A-label namespaces in one planning set invite cross-references to the wrong item | §1 table head · throughout. Planner → cite AR-n when transcribing |
| **O-B** — FK ambiguity (§2.1 vs P1 1.1) | **No hard FK** on `prompt_assent_tokens.version_id` — indexed soft reference | The audit-survival precedent (`models.py:464-466`): forensic rows must outlive version archival/cleanup | §1 D-1 amendment 5 · §2.1. Planner → P1 schema task |
| **O-D** — §2.2 step 6/7 ordering vs prose | Pipeline restated: **counters (step 6) precede the guarded transition (step 7)**, refusal path only | The original numbering read transition-then-counters while the prose said the opposite; the order is load-bearing (D-4 blocking rule) | §2.2 steps 6–7. No planner action beyond transcription |
| **O-L** — render-hash re-check placement | Folded **inside the D-4 hook's transaction** as step (i), from the row **as read in the transaction**; the ONLY render-hash site — the pre-transition layer does not duplicate it | Kills the pre-check→transition TOCTOU and the two-site drift risk in one move | §1 D-4 · §2.2 step 7(i). Planner → P3 hook task |

**Council status:** no reconvention required — this pass closes K-2/K-3/K-8/K-9 (and K-7) as
directed; no deviation from the closing rulings. The independent approver re-reviews with the
K-checklist against this file.

# Runbook — `prompt_assent` (the conversational approval lane, "The Emperor's Word / Assent")

Status: **dormant by default** — shipped by the prompt-assent commission
(2026-11-10, Phase 4). Tool: `prompt_assent` (category `prompt-assent`,
PRIVILEGED entry #21, **ari only**). Design authority:
`.agents/shared/planning/prompt-assent/architecture-recommendation.md`
@ `df8da01a6` (binding). Plan set:
`.agents/shared/planning/prompt-assent/`.

The doctrine: *FE-UI is a deeper view; the working path is conversation.
The Emperor's word, relayed by C2 with provenance, is a first-class
approval lane.*

## What it does

One tool, four verbs (`action ∈ {list, mint, approve, reject}`):

- **list** — the pending CORE prompt-version rows (read-only; ari-gated
  only; **not** agent-filtered — see disclosure 4).
- **mint** — mint an assent token for ONE pending item and show the
  canonical diff **with** the token. Gated: mint requires a live,
  FRESH user-origin window in a private 1:1 DM (AR-1 leg 1 + leg 2).
  The mint context is derived from the window's message row — never
  from tool arguments.
- **approve / reject** — relay the Emperor's decision for a minted
  token. The token must be echoed in the human's own message (factor 3),
  in the same 1:1 DM, by the same daemon-derived human (AR-1 leg 3).
  Writes ONE `prompt_audit_events` row inside the same transaction as
  the state change.

## Arming / disarming (the kill-switch)

`ENSEMBLE_PROMPT_ASSENT_ENABLED` — **default OFF, fail-closed (D-2)**.

- **Unset** ⇒ the lane is off ⇒ every verb refuses `lane-disabled`.
- **Invalid** (anything that is not a boolean spelling: `1/true/yes/on/
  enabled` or `0/false/no/off/disabled`, case-insensitive) ⇒ **fails
  CLOSED at the TOOL layer** — off, never open, even if the boot config
  had armed the lane (the call-time resolver falls back to the shared
  `PROMPT_ASSENT_DEFAULT_ENABLED` = OFF and never raises). **But the BOOT
  layer RAISES**: `PromptAssentConfig` is a pydantic settings model
  (`daemon/config.py:3246`), so an invalid value is a `ValidationError`
  when the config is constructed at boot — the daemon does NOT start
  with a typo'd value (the `prompt_assent` section is built by
  `default_factory` at `daemon/config.py:6913`; `load_config()` runs at
  `daemon/api.py:614`). House parity: `NotifyUserConfig` behaves the
  same way — an invalid `ENSEMBLE_NOTIFY_USER_ENABLED` also raises at
  boot. So a typo is a LOUD boot failure, not a silent off-state:
  fix the spelling and restart. (Unset is NOT invalid — it is the
  default-OFF case above and boots cleanly.)
- Arming is **ONE env var per environment — no code change**:

  ```
  ENSEMBLE_PROMPT_ASSENT_ENABLED=1     # e.g. in the environment's .env
  ```

- The switch is read **AT CALL TIME** (the `notify_user` knob-resolver
  shape): a flip needs **no restart**. The per-call env value wins over
  the boot config value, which wins over the shared default.
- The config class is `PromptAssentConfig` (prefix
  `ENSEMBLE_PROMPT_ASSENT_`) — **ENV-ONLY**: there is NO `config.yaml`
  block for this section and `load_config` has no pass-through for it;
  a hand-added yaml block would be silently ignored (and would shadow
  the env knob — do not add one).
- The tool **stays registered** when the lane is off — ari sees the
  tool and gets the typed refusal; the surface never shrinks with the
  switch. `mint` is refused too.

## The three factors (and what they are NOT)

1. **user_confirmed** — the relay agent's own boolean. See disclosure 3.
2. **The user-origin window** — armable ONLY by a real human turn on a
   source the UNFORKED `classify_user_origin` classifies user-origin.
   The window is popped on every non-user-origin dispatch, so an agent
   turn can never inherit one. Freshness: `stamped_at` must be within
   **≤ 10 minutes** (default 600 s) of the call, IN ADDITION to the
   window's own `expires_at`; absent/unparseable ⇒ stale (fail-closed).
   Applies to **mint AND approve** alike.
3. **The token echo** — the 8-char base32 token (`CONFIRM-XXXXXXXX`,
   40 bits) must appear **verbatim in THAT human message's content**
   (`nonce_in_content` semantics). A token present only in the tool
   arguments is refused — the load-bearing pin.

## Token TTL and lifecycle

- TTL = `NONCE_TTL_S` (the promote-lane SSOT, `upgrade_journal.py`) =
  **60 minutes** from mint (`expires_at` is in the mint response).
- Hash-only storage (`token_hash = sha256(token)`); the raw token
  exists exactly once — in the mint response shown with the diff.
- Single-use: consumed only on a successful transition
  (`consumed_at` + `consumed_by_message_id` recorded); **refusals never
  burn the token**.
- Re-mint supersedes and revokes the prior live token (a superseded
  token reads `token-superseded` — deliberately distinct from
  `token-already-used`).
- Per-token mismatch counter + **lockout at 5** (the durable PG leg):
  the lockout revokes the live token ⇒ a fresh mint ⇒ a fresh human
  turn is required.
- **The mismatch budget (K-9/D-3b)**: per-relay-instance, IN-RAM,
  hourly, configurable via
  `ENSEMBLE_PROMPT_ASSENT_HOURLY_MISMATCH_BUDGET` (default **10**).
  The 11th mismatch within the hour by ONE relay instance is refused
  `mismatch-budget-exceeded`. A successful approval does NOT reset it;
  it rolls over with the hour bucket and is cleared by a daemon
  restart. **Ledgered residual (by design):** the budget is not shared
  across daemons — multi-daemon deployments each keep their own
  per-`instance_id` budget. The budget is defence-in-depth, not a trust
  decision; the DURABLE leg is the PG per-token `attempts` + lockout.

## ⚠ The five mandated disclosures (read before trusting a green tick)

### 1. `self-approval-attempt` is NOT the gate (O-H)

The `self-approval-attempt` refusal is a **belt-and-braces assertion,
NOT the load-bearing gate** — it is largely unreachable by construction
and must never be mistaken for the real defence. The load-bearing
defences are: (i) the **window-pop** — agent-originated dispatch clears
the user-origin window (`daemon/manager.py:6132-6133`); (ii) the
**ari-EXACTLY layer-3 gate** (category privileged + ari-only allow +
the in-body `agent_id == "ari"` check); (iii) the **daemon-derived
user equality** (AR-1/K-8) — the approving turn's daemon-derived user
must equal the mint-witnessed `mint_user_id`, neither value ever
tool-supplied.

### 2. The D-7 persona gap

Phase 1 covers the **classic `prompt_versions` surface ONLY**. The
**memoir persona surface is DEFERRED** (own table, own state
vocabulary, writes **no** `prompt_audit_events` — `memoir/repository.py`)
and is a recorded known gap, not a covered surface. Extending the lane
to personas is a separate commission (it needs its own provenance
mechanism — persona rows would NOT land in this lane's ledger).

### 3. Factor 1 is the relay's self-assertion (O-H)

`user_confirmed` is the **relay agent's own assertion, not a human act**
— the *weakest* of the three factors and worth nothing on its own. The
authority is carried by the **human turn** (factor 2's daemon-owned
window) and the **token echo in the human's own message** (factor 3).
An attacker who cannot produce those two cannot approve, whatever the
relay asserts.

### 4. The pending list is cross-agent (O-I)

`list` shows **every CORE pending row — including other agents'
drafts** — because the relay lane must be able to approve any agent's
row. There is NO agent filter. The tool's INFO log line stays
content-free (counts and flags only, never persona or diff content);
the list BODY goes to the Emperor's chat, not to the daemon log.

### 5. Refusals write NO ledger row (O-G)

A refusal emits ONE content-free INFO line — and writes **no**
`prompt_audit_events` row and **no** token-table row. The token table
records **mints**, not refusals; the audit table records **decisions**,
not attempts. Audit readers must not expect refusal forensics in the
ledger — the ledger is complete for decisions, deliberately silent on
refusals.

## The K-3 live confirmation (the outstanding pre-ship probe)

AR-1 leg 2 classifies a channel private as `is_dm AND NOT is_group_dm`,
fail-closed on an absent/unparseable discriminator (only literal
booleans are trusted). The adapter-side `is_group_dm` discriminator is
unit-proven; the **live runtime confirmation is still owed** and is
operator-run, once, per the procedure below (a miss is bounded to
availability — the lane refuses — never to authority):

1. **Arm the lane** in the target environment
   (`ENSEMBLE_PROMPT_ASSENT_ENABLED=1`).
2. **1:1 DM leg**: from the Emperor's account, DM the relay bot
   directly (no other occupant) and ask ari to mint for any pending
   item. **Expect: a mint response** (diff + `token: CONFIRM-…`). This
   proves a true 1:1 DM classifies private.
3. **Group-DM leg**: create a Discord GROUP DM (≤ 10 recipients)
   containing the bot and at least one other human, and repeat the
   mint. **Expect: `mint-requires-private-channel`** — and NO token row
   (the refusal writes no ledger row). This proves the discriminator
   fires on a multi-occupant private channel.
4. **Fail-closed leg (optional)**: any channel whose last message row
   predates the discriminator must also refuse mint/approve with
   `mint-requires-private-channel` / `token-channel-mismatch` — that is
   the absent-discriminator rule working, not a defect.
5. Record the two outcomes (mint OK / refusal) as the probe evidence;
   file it against the lane before relying on the lane in production.

## "The Emperor said yes and nothing happened" — troubleshooting

Work down the refusal the tool returned (every terminal path returns
ONE typed envelope):

| Symptom | Likely cause | Remedy |
|---|---|---|
| `lane-disabled` | the kill-switch is off (or the value is invalid — invalid **fails closed**) | arm `ENSEMBLE_PROMPT_ASSENT_ENABLED=1`; check for typos in the value |
| `no-user-origin-turn` (names the observed source) | the relay has no live factor-2 window — the "yes" did not come through a user-origin source, or the daemon restarted since (the window is in-memory) | ask again in a fresh message from the bound chat source; a restart between mint and reply always requires a fresh turn |
| `user-origin-window-expired` | more than 60 min since the window was stamped | ask again in a fresh message |
| `user-origin-window-stale` | the window was stamped > 10 min ago (the D-6 freshness bound; silent resume keeps an OLD stamp — this is the anti-replay bound doing its job) | ask again in a fresh message |
| `token-content-mismatch` — ECHO mismatch (factor 3g) | the token was not in the HUMAN's message content (only in the tool args), or the echoed spelling did not normalize — the envelope says "this turn's human message does not carry the token" | have the Emperor echo the token verbatim (grouped or raw both accepted) in his own reply |
| `token-content-mismatch` — RENDER drift (D-4/O-L, in-transaction) | the canonical render moved since the mint — the pending or effective side changed underneath the outstanding token — the envelope says "the canonical render moved since mint (pending or effective side changed)" | re-mint against the CURRENT content (the mint re-shows the diff against the current render) and approve with the fresh token — the outstanding token does not match the moved render |
| `token-content-mismatch` — budget asymmetry | this code consumes the IN-RAM hourly mismatch budget (K-9/D-3b — default 10/h per relay instance) but NOT the durable per-token `attempts` counter. The RENDER-drift cause raises INSIDE the guarded transaction, and the durable counter can never share that transaction (the D-4 blocking rule — a counter inside it would be reset by the rollback); the ECHO cause is refused at the factor-gate layer, before the token row is resolved | read the hourly budget as the ONLY bound this code moves — it cannot lock a token out durably (`token-locked` is unreachable from this code) |
| `token-mismatch` / `token-item-mismatch` | the token does not match this item / any live row | re-mint (re-shows the diff); check the version_id |
| `token-expired` | > 60 min since mint | re-mint |
| `token-already-used` | this token already completed a decision | check the audit ledger — the decision already landed |
| `token-superseded` | a newer mint replaced this token | use the newest token from the latest mint |
| `token-locked` | 5 mismatches on this token — the durable lockout revoked it | re-mint (a fresh mint requires a fresh human turn by construction) |
| `mismatch-budget-exceeded` | this relay instance burned its hourly budget (default 10) | wait for the hour bucket to roll over, or raise the knob; a restart clears the in-RAM counter |
| `token-channel-mismatch` | approving from a different channel than the mint's, or the approving context is not a proven 1:1 DM | approve from the SAME 1:1 DM the mint happened in |
| `token-approver-mismatch` | a different human of the same 1:1 DM | the mint-witnessed human must be the approver; a legitimate relay to another human is a re-mint |
| `item-not-pending` / `already-decided` | the row already moved (FE session decision, another relay) | check the FE / audit ledger; nothing to relay |
| `mint-live-token-exists` | a live token is outstanding | approve/reject with it, or re-mint with `force=true` (re-shows the diff) |
| `extraction-incomplete` | the window's message row lacks the nested Discord identity (non-Discord source or stripped metadata) | the lane is Discord-first by design (D-5); mint/approve from a Discord DM |
| `mint-requires-private-channel` | the channel is a group DM / guild channel / older row without the discriminator | mint from a 1:1 DM (see the K-3 probe above) |
| `assent-verification-unavailable` | the window's message row is unreadable (boot-wipe) | fail-closed by design (R-5); ask again |
| `audit-unavailable` | the ledger write failed — the state change rolled back with it | fail-closed by design; retry after the DB recovers |
| generic `ERROR: … available to the ari agent only` | the caller is not ari (the envelope carries no code by design) | wrong agent — the lane is ari-only |

Nothing in the table above implies the lane "did nothing silently": every
terminal path emits exactly ONE content-free INFO log line
(`prompt_assent outcome=… action=… code=… agent=… instance=… count=…
flags=…`) — grep the daemon log for it.

## The refusal taxonomy (complete)

`lane-disabled` · `not-ari` (generic envelope marker — never emitted as
a `reason=` code) · `invalid-arguments` · `user-confirmation-missing` ·
`no-user-origin-turn` · `user-origin-window-expired` ·
`user-origin-window-stale` · `assent-verification-unavailable` ·
`token-mismatch` · `token-expired` · `token-already-used` ·
`token-superseded` · `token-item-mismatch` · `token-content-mismatch` ·
`token-channel-mismatch` · `token-approver-mismatch` ·
`mint-requires-private-channel` · `mint-live-token-exists` ·
`token-locked` · `mismatch-budget-exceeded` · `item-not-found` ·
`item-not-assentable` · `item-not-pending` · `already-decided` ·
`self-approval-attempt` · `extraction-incomplete` · `audit-unavailable`.

## What the audit payload contains (decisions only)

One append-only `prompt_audit_events` row per relayed decision, written
inside the SAME transaction as the state change (`approval_path:
"chat-assent"`, `approver_auth_method: "assent-relay"`): the
`from_state`/`to_state`, the **human** `approver_subject`
(`<source_id>:<user_id>`), the relay provenance (agent, relay instance,
source, external user id, guild/channel ids, channel type, daemon
message id AND the platform-side message id, window stamped/expired),
`assented_at`, the `token_id` + `token_fingerprint` (sha256[:12] —
never the raw token), the canonical `render_sha256`, and — reject only
— a top-level `reject_reason` (≤ 500 chars). Mint writes NO audit row
(the token table IS the mint ledger). The raw token appears NOWHERE in
durable state (pinned by test).

## Rollback note

The lane is additive: one new table (`prompt_assent_tokens`, the 128th
migration file + the `create_all` seam), one privileged category, one
tool. **No `_ensure_postgres_columns` entry was added** (the boot-abort
surface is untouched). On an old binary the table is inert and the
category/tool disappear with the code — rollback is fix-loss-only.

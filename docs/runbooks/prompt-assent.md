# Runbook — `prompt_assent` (the conversational approval lane, "The Emperor's Word / Assent")

Status: **dormant by default** — shipped by the prompt-assent commission
(2026-11-10, Phase 4); **Phase 1.5 (designated channels, pinned-approver
model)** extends the surface (2026-11-12). Tool: `prompt_assent`
(category `prompt-assent`, PRIVILEGED entry #21, **ari only**). Design
authority: `.agents/shared/planning/prompt-assent/architecture-recommendation.md`
@ `df8da01a6` (binding). Plan set:
`.agents/shared/planning/prompt-assent/`; the Phase-1.5 plan set is
`.agents/shared/planning/prompt-assent-channels/` (its decisions govern
where the two sets differ).

The doctrine: *FE-UI is a deeper view; the working path is conversation.
The Emperor's word, relayed by C2 with provenance, is a first-class
approval lane.*

## What it does

One tool, four verbs (`action ∈ {list, mint, approve, reject}`):

- **list** — the pending CORE prompt-version rows (read-only; ari-gated
  only; **not** agent-filtered — see disclosure 4).
- **mint** — mint an assent token for ONE pending item and show the
  canonical diff **with** the token. Gated: mint requires a live,
  FRESH user-origin window on a private surface (AR-1 leg 1 + leg 2) —
  a proven 1:1 DM, **or** (Phase 1.5) a designated channel whose
  PINNED APPROVER is speaking (see *The designation map* below). The
  mint context is derived from the window's message row — never
  from tool arguments.
- **approve / reject** — relay the Emperor's decision for a minted
  token. The token must be echoed in the human's own message (factor 3),
  on the SAME surface the mint happened on — the same 1:1 DM by the
  same human, or the same designated channel by its pinned approver
  (AR-1 leg 3). Writes ONE `prompt_audit_events` row inside the same
  transaction as the state change.

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
- **The arming gate**: the lane is **armed by the Emperor, after the
  K-3 live designated-channel probe — not before**. The default-OFF
  statement above is unchanged — the surface ships inert, and arming
  remains an explicit operator act. Because the probe itself requires
  the knob on, the operative rule is reliance, not arming: **the lane
  must not be RELIED UPON until the six probe outcomes are recorded**
  (the `ARMED-TENTATIVE` posture — see the K-3 section below).
- **Re-probe after ANY map edit**: re-run **probe leg 3** (the
  designated-channel mint) after EVERY change to
  `ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS`. An entry that is
  valid-format-but-wrong-id produces NO signal at all — no refusal ever
  names it — so the leg-3 re-probe is the ONLY typo feedback loop.
- **The DM-only fallback (v0.29.18) and its recovery.** The pre-1.5
  release — designated channels absent, `v0.29.18`, tag commit
  `df4a92732` — is **NOT protected from release retention on this
  host**: the first later promote can evict its release dir. The tag
  and its 4-way sha (`aa78f632c4…`) survive in git, so the fallback is
  recoverable in ONE build+stage cycle: in a CLEAN worktree
  `git checkout df4a92732` (never re-cut the tag); rebuild
  `dist/ensemble-prod`; re-run `scripts/upgrade/stage.sh` with
  `VERSION=v0.29.18` + `ENSEMBLE_ROLLBACK_SAFE=1` (the existing tag
  satisfies the exact-match gate); verify the 4-way sha reproduces.
  Detail: the Phase-1.5 plan set's `decisions.md` (the retention-TRUTH
  record) and `ceremony-notes.md` §3.2 — reconcile there, do not
  re-derive here.

## The designation map (Phase 1.5 — designated channels)

**What it is.** The operator's registry of private-EQUIVALENT rooms:
each entry pins ONE Discord channel to ONE approver. A turn in that
channel BY that human is private-equivalent to a 1:1 DM — it may mint,
and it may approve a token minted in that same channel. Every other
human in the room is refused `approver-not-pinned`; every channel NOT
in the map is refused exactly as before Phase 1.5.

- **Env var**: `ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS`.
- **Exact JSON shape**: a JSON OBJECT mapping channel id → approver
  user id — `{"<channel_id>": "<approver_user_id>"}`. An array or a
  scalar is a shape error, never a partial map.
- **Worked example** — PLACEHOLDER ids only (never put a real channel
  or user id in any file that can be committed; the example below uses
  obviously-synthetic repeated-digit ids):

  ```
  ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS={"111111111111111111":"222222222222222222"}
  ```

- **Id shape (both keys and values)**: digit strings matching
  `^[1-9][0-9]{0,31}$` — digits only, NO leading zero, never `0` (a
  Discord snowflake is 17–20 digits, but the parser accepts up to 32).
  Values must be JSON **strings**: a bare number is rejected, never
  coerced — an unquoted `1` that silently became `"1"` would hide a
  typo.
- **Bound**: at most **64** entries.
- **Duplicate keys are refused.** JSON's normal last-key-wins would
  silently drop one designation; the parser refuses the whole map
  instead.
- **All-or-nothing**: ONE invalid entry invalidates the WHOLE map — the
  designated path then refuses `designation-config-invalid`, and the
  daemon log carries one WARNING per offending entry (naming the KEY
  and the rule it broke, never a value). The map is never silently
  repaired to empty, and the 1:1-DM path is unaffected either way.
- **Empty forms**: an absent var or an empty value ⇒ UNSET; an explicit
  `{}` ⇒ a valid EMPTY map. Behaviourally identical (nothing is
  designated), diagnostically distinct — the map-parse log line names
  which (`state=unset` vs `state=empty`).
- **Exact match only**: the channel id must match exactly. Threads are
  NOT designated — a thread inside a designated channel is refused like
  any other surface.
- **Restart required**: the map is read at call time from the daemon's
  PROCESS environment, and the process environment is populated at
  boot from the launcher's `.env` export. An `.env` edit alone changes
  nothing in the running process — **edit `.env`, then restart the
  daemon**, and confirm the fresh map-parse log line
  (`designation_map state=… count=… map_digest=…`).
- **Never commit it**: the map names the Emperor's private rooms and
  their pinned humans — operator-private configuration. It lives in the
  environment's `.env` (mode 600) and NOWHERE else: never in the repo,
  never in a planning doc, never in a chat transcript (this runbook's
  example ids are placeholders for exactly this reason).

**The honest posture — read before trusting "operator-only".**
Designation is an OPERATOR act; no agent performs it as part of the
lane, and the map leaves every audited tool surface (it lives only in
the process environment). **But do not lean on the file-tool deny list
as the boundary** — agents hold `bash`, and a deny list is not a
boundary: an agent with shell access could edit `.env`, and a
provenance-gated restart could make the edit effective. What the map
is **not** matters more: **the map is not a credential.** Knowing which
channels are designated enables nothing by itself — the authorisation
is checked daemon-side against the MESSAGE AUTHOR, never against a
configuration value a reader supplies, and `ens_env_read` deliberately
does **NOT** redact the variable (the exposure is recorded and
intentional: the ids are quasi-public; the POLICY — which rooms are
private-equivalent — is the sensitive thing, and setting it is the
operator's act). The load-bearing boundary stands BEHIND the map:
**even a malicious map cannot mint without a genuine user turn by the
pinned human in the designated channel** — the map expands WHERE a mint
may happen; it never supplies the WHO. Visibility compensates for the
rest: the effective-map log line (entry count + a content-free 12-hex
fingerprint) fires at first resolution and on every digest change, and
every channel mint's INFO line plus the decision's audit payload carry
the same `map_digest` — a map change is always visible in provenance.
The two digests are the same only while the map is stable: after a
mid-flight re-designation (D-6), the decision's `map_digest` may differ
from the mint's — and that divergence is the point, not a provenance
break (it is what makes the A-9 re-designation window observable).

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

## The K-3 live confirmation — the two-phase, six-leg probe

The privacy rule classifies a channel private as `is_dm AND NOT
is_group_dm`, fail-closed on an absent/unparseable discriminator (only
literal booleans are trusted). Phase 1.5 adds the designated-channel
path: a guild channel named in the operator's designation map is
private-EQUIVALENT for exactly one human — the map's pinned approver.
The adapter-side discriminator and the designated-path logic are
unit-proven (the refusal matrix ships as tests); **the live runtime
confirmation is still owed** and is operator-run, per the procedure
below. A miss anywhere is bounded to availability — the lane refuses —
never to authority.

> **THE LANE MUST NOT BE RELIED UPON UNTIL THE SIX OUTCOMES ARE
> RECORDED.** "Arming blocked until the probe" is circular — the probe
> requires arming. The correct posture: arm TENTATIVELY, probe, record
> every outcome, then disarm or ratify explicitly.

### The three armed states

| State | How it is reached | Meaning |
|---|---|---|
| `ARMED-TENTATIVE` | `ENSEMBLE_PROMPT_ASSENT_ENABLED=1` + restart | the probe's PRECONDITION. A mint alone grants nothing; the exposure is the room disclosure — the mint response posts the token + the canonical diff into the room |
| `ARMED-OPERATIONAL` | all six outcomes recorded AND the Emperor declares reliance | the lane may be relied upon |
| `DISARMED` | unset the knob + restart ⇒ every verb refuses `lane-disabled` | back to inert |

**The probe runs INSIDE `ARMED-TENTATIVE` and ENDS with an explicit
disarm-or-ratify step** (the final step below).

### Why two phases are mandatory

Leg 4's expected code (`mint-requires-private-channel`) is **identical**
to what a mistyped map key produces in the designated channel. Only the
**A/B pair on the SAME channel id** separates *"the map says no"* from
*"the id is a typo"* — a single-phase leg list cannot tell them apart.
Phase A establishes the baseline (map unset ⇒ nothing is designated);
Phase B establishes the delta (the same channel id now mints — for the
pinned approver only). **Leg 4 belongs in Phase B**: only there is a
channel actually designated, so the narrowness proof is meaningful.

### Preconditions — all of them, before the probe starts

1. **`ARMED-TENTATIVE`**: `ENSEMBLE_PROMPT_ASSENT_ENABLED=1` in the
   environment's `.env`, then restart the daemon.
2. **Map state per phase — with a restart between the phases**: Phase A
   runs with `ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS` UNSET;
   Phase B runs with it SET to the SAME channel id leg 4A will use
   (see *The designation map* above). Setting the map means editing
   `.env` AND restarting; confirm the map-parse log line before
   Phase B.
3. **≥ 2 pending CORE `prompt_versions`**, Emperor-created (the
   artifact write path is human-session-only), and a **different
   subject item per minting leg** — a live token blocks re-mint of its
   item; `force=true` supersedes.
4. Operator **READ** access to `prompt_assent_tokens` and to the
   message rows' `metadata["discord"]` (the evidence below reads both).
5. **Discord Developer Mode ON** — the ids are copy-paste digits.
6. **The relay bot must be present in a NON-designated guild channel at
   probe time** — leg 4's precondition. Absent ⇒ leg 4 is
   INCONCLUSIVE, not a pass.
7. **Every probe channel must PASS the adapter's dispatch gates** — the
   relay source's `require_mention` (the bot must be mentioned in every
   probe turn) and `allowed_guild_ids` / `allowed_channels`:
   a channel that fails those gates never reaches the daemon, so the
   probe produces SILENCE (no refusal, no response), which is NOT a
   pass.
8. **The second human (leg 5) must be a second ACCOUNT.** A second
   device shares the `user_id` and degenerates leg 5 into leg 3. The
   Emperor's own alt account is the strongest choice — it proves the
   pin binds the ACCOUNT, not the person.

### Phase A — the map UNSET (the baseline: with no map, nothing is designated)

Record every outcome — the code, the token-row shape, and the INFO
log line — before moving on.

- **Leg 2 — group DM.** Create a Discord group DM (≤ 10 recipients)
  containing the relay bot and at least one other human; from the
  Emperor's account, ask ari to mint for a pending item.
  **Expect: `mint-requires-private-channel`** — and NO token row and NO
  audit row (the refusal writes no ledger row). Proves the
  multi-occupant discriminator fires.
- **Leg 4A — the designated channel, map unset (MUST refuse).** In the
  channel you intend to designate (single occupant), repeat the mint
  with the map UNSET. **Expect: `mint-requires-private-channel`, no
  row.** This is the **A** of the A/B pair — the refusal under "no
  map", in the very room Phase B will mint in.
- **Leg 1 — 1:1 DM.** From the Emperor's account, DM the relay bot
  directly (no other occupant) and mint for a different pending item.
  **Expect: a mint response** (diff + `token: CONFIRM-…`); the token
  row reads `mint_is_dm=1`, `mint_user_id` = the probe account,
  `status='live'`, `ttl_expires_at` = +60 min, `attempts=0`. Proves the
  DM path is unharmed.

### Phase B — the map SET (the delta: the same channel id now mints, for the pinned approver only)

Set `ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS` in `.env` (JSON — see
*The designation map* above) with the SAME channel id leg 4A used and
the pinned approver's user id as its value; **restart the daemon**;
check the map-parse log line (`state=1 entries`) before continuing.

- **Leg 3 — the designated channel (MUST mint).** In that channel
  (single occupant), from the pinned approver's account, mint for a
  fresh pending item. **Expect: a mint response**; the token row reads
  `mint_is_dm=0`, `mint_channel_id` == the map key, `mint_user_id` ==
  the map value; the INFO line reads `action=mint outcome=ok` and
  carries the 12-hex `map_digest=`. **A `mint_is_dm=0` row with the
  WRONG channel id is a FAIL** — it means the mint happened in a thread
  or in the wrong room. This is the **B** of the A/B pair: the SAME
  channel id that refused in Phase A now mints, because the map — not
  a typo — is the variable.
- **Leg 5 — a non-pinned human (MUST refuse).** From the SECOND account
  (a member of the same designated channel), attempt a mint.
  **Expect: `approver-not-pinned`, no row**, and the INFO line's
  `flags.mismatch=0`. Proves the pinned approver is the only authoriser
  — and NOTHING burned (this code is outside the mismatch class).
- **Leg 4 — a non-designated guild channel (MUST refuse — the
  narrowness proof).** In another guild channel where the relay bot is
  present (the map does NOT list it), attempt a mint.
  **Expect: `mint-requires-private-channel`, no row.** Proves
  designation reaches ONLY the listed channels — the map is narrow,
  not a blanket guild grant.

### Evidence discipline — every leg

- A leg's PASS is the **expected code + the token-row shape + the INFO
  line, captured together**. Grep the daemon log for the
  `prompt_assent outcome=…` line after every leg.
- **The map-parse log line is MANDATORY evidence in both phases** —
  `designation_map state=… count=… map_digest=… errors=…`. It is the
  ONLY discriminator between *"the map says no"* and *"the map is
  broken/empty"*.
- **Refusal-only legs first; minting legs last** wherever the phase
  order above permits; ONE subject item per minting leg.
- The mint response posts the token + the canonical render INTO the
  room ⇒ **single-occupant channel for leg 3**.
- Consume or TTL-expire (60 min) a leg's token before reusing its
  subject item. **Never** paste a live token into a second channel.

### The last-speaker rule (NT-5)

A guild channel is ONE instance for all humans, and the user-origin
window re-arms on every non-silent dispatch: **a bystander's reply
between the Emperor's turn and the tool call re-points the window**,
and the Emperor's own attempt then refuses `approver-not-pinned`.
**The Emperor must be the LAST speaker** in the probe channel; a
bystander's reply within the 600 s freshness bound invalidates the
attempt — **re-run, do not adjudicate**.

### Code-family triage — the operator's FIRST move

| Observed code | Means | Operator action |
|---|---|---|
| `mint-requires-private-channel` | the privacy gate fired ⇒ the window WAS live and the item WAS pending | an availability miss, not a leg failure — check the map, then the channel |
| `designation-config-invalid` | the map is broken — a CONFIGURATION failure | the WARNING names every offending key (never a value); fix the map, restart, re-run leg 3 |
| `mint-live-token-exists` | a live token is outstanding | CLEANUP, not a probe failure — consume it or wait for the TTL |
| `no-user-origin-turn` / `user-origin-window-stale` | a window-family code | the turn did not arm the window — or a bystander spoke (the last-speaker rule above) |
| `approver-not-pinned` | the turning human is not the room's CURRENT pinned approver | check the map's value for THAT channel; check who spoke last |

### Record, then disarm or ratify

1. Record all SIX outcomes — legs 1, 2, 3, 4A, 4, 5 — against the lane
   (the release ceremony notes) BEFORE relying on it.
2. Then choose, explicitly:
   - **Disarm**: unset `ENSEMBLE_PROMPT_ASSENT_ENABLED` + restart ⇒ the
     lane is `DISARMED` (inert); or
   - **Ratify**: with all six outcomes recorded, declare the lane
     `ARMED-OPERATIONAL` and rely on it.
3. **An unrun leg 2 or leg 5 is NOT a pass.** The gap is recorded as an
   explicit Emperor acceptance; the multi-occupant discriminator then
   stays unit-proven only — exactly the class K-3 exists for.

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
| `token-channel-mismatch` | approving from a different channel than the mint's, or the approving context is not a proven private surface — including a DESIGNATED channel whose designation was WITHDRAWN between the mint and this attempt (see the KEEP-BURN warning below — this variant BURNS) | approve from the SAME surface the mint happened in; if the room was un-designated, re-designate it or re-mint in a surface that is currently designated |
| `token-approver-mismatch` | a different human of the same 1:1 DM | the mint-witnessed human must be the approver; a legitimate relay to another human is a re-mint |
| `approver-not-pinned` | **you are not the pinned approver of this room** — mint side: the designated channel is pinned to someone else; approve side: the designation MOVED between the mint and this decision (the operator re-pinned the room). Does NOT burn (see below) | mint/approve from the room where you ARE the pinned approver, or have the operator re-pin the room to you and re-mint (a fresh human turn) |
| `designation-config-invalid` | **the operator's map is broken — see the designation map section** (a malformed value or an invalid entry; all-or-nothing ⇒ the designated path is unavailable). Does NOT burn | read the daemon-log WARNINGs — they name every offending key and the rule it broke (never a value); fix the map in `.env`, restart the daemon, then re-run probe leg 3 |
| `item-not-pending` / `already-decided` | the row already moved (FE session decision, another relay) | check the FE / audit ledger; nothing to relay |
| `mint-live-token-exists` | a live token is outstanding | approve/reject with it, or re-mint with `force=true` (re-shows the diff) |
| `extraction-incomplete` | the window's message row lacks the nested Discord identity (non-Discord source or stripped metadata) | the lane is Discord-first by design (D-5); mint/approve from a Discord DM |
| `mint-requires-private-channel` | the channel is a group DM / a guild channel not in the designation map / an older row without the discriminator. NOTE: the same code is what a MISTYPED map key produces — see the K-3 probe's A/B pair for how to tell them apart | mint from a 1:1 DM, or (Phase 1.5) from a channel the map designates — verify the map-parse log line and re-run probe leg 3 after any map edit |
| `assent-verification-unavailable` | the window's message row is unreadable (boot-wipe) | fail-closed by design (R-5); ask again |
| `audit-unavailable` | the ledger write failed — the state change rolled back with it | fail-closed by design; retry after the DB recovers |
| generic `ERROR: … available to the ari agent only` | the caller is not ari (the envelope carries no code by design) | wrong agent — the lane is ari-only |

> **⚠ BEFORE ARMING — the withdrawn-designation burn is a USER cost
> (the KEEP-BURN ruling).** If a room's designation was WITHDRAWN — the
> operator re-pinned or un-pinned the channel — between a mint and the
> approval, an approval attempt in that room refuses
> `token-channel-mismatch`, and that code is MISMATCH-class: **your
> token BURNS** — the durable per-token attempt counter increments, and
> **five such attempts revoke the live token** (⇒ a fresh mint ⇒ a
> fresh human turn is required). This is deliberate, not a defect: a
> user presenting a token in a room that is no longer designated is a
> user-side mismatch. Contrast the two operator-fault codes, which do
> **NOT** burn: `designation-config-invalid` (the operator's map is
> broken) and `approver-not-pinned` on the re-designation window (the
> operator re-pinned the room to someone else) both leave the token
> live and its attempt counter UNTOUCHED. Know this before arming: a
> designation withdrawn under a live token has a real cost, paid by the
> token's holder.

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
`approver-not-pinned` · `designation-config-invalid` ·
`mint-requires-private-channel` · `mint-live-token-exists` ·
`token-locked` · `mismatch-budget-exceeded` · `item-not-found` ·
`item-not-assentable` · `item-not-pending` · `already-decided` ·
`self-approval-attempt` · `extraction-incomplete` · `audit-unavailable`.

**29 codes after Phase 1.5** (27 carried from the shipped lane, plus
the two designated-channel codes; the `not-ari` marker is included in
the count and is test-pinned against the module's `REFUSAL_TAXONOMY`).
The two new codes, in the operator's words:

- `approver-not-pinned` — *"you are not the pinned approver of this
  room"*: the designated channel is pinned to a different human (mint),
  or the pin moved between the mint and the decision (approve). Never
  burns.
- `designation-config-invalid` — *"the operator's map is broken — see
  the designation map section"*: the map is malformed or carries an
  invalid entry; all-or-nothing, so the designated path is unavailable
  until it is fixed. Never burns.

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
surface is untouched). Phase 1.5 (designated channels) changed NO
schema at all: no table, no column, no migration — the migration head
stays `20261110_221500` at **128 files**; the two new refusal codes and
the designation map are pure code+env surface. On an old binary the
table is inert and the category/tool disappear with the code — rollback
is fix-loss-only.

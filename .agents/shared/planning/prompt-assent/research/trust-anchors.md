> ## ⚠ CORRECTION BANNER (added by the plan lane, 2026-11-10) — canonical spelling is **`prompt-assent`**
>
> The **canonical naming string for this commission is `prompt-assent`** (kebab-case category,
> directory, files, tool docs). The occurrences of **`prompt-assist`** in this note's title and
> body are a **documented carry-over error**, flagged by the technical-analysis lane at
> [`../technical-analysis.md` §8 item 2](../technical-analysis.md) ("the category key must be ONE
> string — recommend `prompt-assent`, and the research note's spelling is a carry-over error to
> fix in the plan").
>
> **This note's body is left unrewritten on purpose** — it is the historical record of a
> research lane that ran in a **sibling worktree** after the commissioned worktree was deleted
> mid-lane (§0, §9), and rewriting it would destroy that provenance. **Do not copy
> `prompt-assist` into any implementation.** The binding rule lives in
> [`../plan-overview.md` → H-11](../plan-overview.md) and
> [`../decisions.md` → BLESSED-1](../decisions.md).
>
> One further correction, from this same analysis: the note's §7 item 15 calls the
> `approve()`/`reject()` internal-actor refusal a **"blocker"**. It is a **design decision**
> (D-8), not a blocker — the resolution is the `assent-relay` lane plus an attestation-bearing
> repository entrypoint.

# prompt-assist — Trust Anchors & Tooling Precedents (research note)

> *(Title spelling retained as written by the research lane — see the correction banner above.
> The canonical spelling is `prompt-assent`.)*

Commission: **prompt-assent** — conversational approval lane. A NEW privileged `ari`-EXACTLY
tool (suggested `prompt_assent`) acting on PENDING `prompt_versions` rows by id
(APPROVE / REJECT-with-reason), gated by a 3-factor echo modeled on the live-promote nonce
pattern, scaled down to a per-item ~6-char confirmation token (TTL ~60 min, re-mintable).

Read-only investigation. No source file was edited. Nothing was committed.

---

## 0. Provenance, base sha, and method caveats (READ FIRST)

- The commissioned worktree `/home/nea/ensemble-src-wt-prompt-assist` (branch
  `feature/prompt-assist`, base `0efdf106f3f66ccb400405f4137694592b72097d`) was **DELETED
  mid-lane** (it resolved for the first two reads, then every `workdir=`/absolute-path read
  of that tree failed with "Working directory does not exist" / "Path does not exist",
  while sibling worktrees stayed resolvable). Anomaly filed: `f32526f7-6911-4516-8acd-2df3990dd6d1`.
  The KV rows `wt.active.feature/prompt-assist.prompt-assist-20261110` /
  `wt.claim.prompt-assist` still point at the now-missing path — **the worktree must be
  re-created before the developer lane starts**.
- **Every anchor below was read from `/home/nea/ensemble-src-wt-v2915-bump`**, whose HEAD is
  `ref: refs/heads/release/v0.29.15` → `0efdf106f3f66ccb400405f4137694592b72097d`
  (`/home/nea/ensemble-src/.git/refs/heads/release/v0.29.15`), i.e. **the same commit as the
  commissioned base**. Caveat: that tree is a release worktree — if it carries local
  modifications, line numbers drift; re-verify with `git -C <wt> rev-parse HEAD` + a
  `git diff --stat` before citing. No `git` was available in this lane (read-only tooling
  only), so the tree's cleanliness could not be confirmed.
- **Tooling caveat (methodological, important for the next lane):** `grep_files` silently
  SKIPS files larger than ~1.4 MB. `daemon/tools/upgrade_journal.py` (4178 lines),
  `daemon/tools/upgrade_tools.py` (4168), `daemon/routers/prompts.py`,
  `daemon/tools/prompt_artifacts.py` are in that set — a **file-targeted** grep on them
  returns **zero matches** (it looks like "the symbol does not exist"), while a
  **directory-level** grep does see them. All claims about those files below were obtained by
  `read_file` line-ranges, not grep.
- The canonical checkout `/home/nea/ensemble-src` was read **only once** (to test path
  resolution) and is **detached @ `013895287`** (~2 weeks behind) — **do not cite anchors from
  it**.

---

## 1. `classify_user_origin` — the SINGLE SOURCE OF TRUTH for user-origin

**Definition** — `daemon/tools/upgrade_journal.py:4116-4166`

```python
def classify_user_origin(
    source: str | None,
    registry_get: Callable[[str], Any] | None,
) -> tuple[bool, str]:        # -> (is_user_origin, detail_token)
```

**Public surface (exact):**

| Element | Anchor | Content |
|---|---|---|
| `_USER_ORIGIN_EXACT` | `daemon/tools/upgrade_journal.py:4094` | `frozenset({"api"})` — full-string match only |
| `USER_ORIGIN_CHAT_SOURCE_TYPES` | `daemon/tools/upgrade_journal.py:4098-4100` | `frozenset({"telegram","slack","discord","whatsapp"})` — matched against registry `source_type` **values** (not id prefixes) |
| `user_origin_sources_display()` | `daemon/tools/upgrade_journal.py:4103-4113` | one-line "what arms the gate" string for refusal reasons; MUST track the classifier |
| `classify_user_origin(source, registry_get)` | `daemon/tools/upgrade_journal.py:4116-4166` | the classifier |
| `is_user_origin_source(source, registry_get=None)` | `daemon/tools/upgrade_journal.py:4169-4178` | boolean convenience wrapper |
| reserved-lane check | `daemon/constants.py:1260` (`RESERVED_SOURCE_PREFIXES`), `daemon/constants.py:1295-1317` (`is_reserved_source`) | checked **before** the registry |

**Classification order + detail tokens** (each branch fail-CLOSED, never raises —
`daemon/tools/upgrade_journal.py:4129-4166`):

1. `not isinstance(source, str) or not source` → `(False, "no-source")` — `:4129-4130`
2. `source in _USER_ORIGIN_EXACT` → `(True, "exact:api")` — `:4131-4132`
3. `is_reserved_source(source)` → `(False, "reserved-internal")` — `:4136-4137`
   (reserved lanes are checked **before** the registry so a mis-registered reserved id cannot arm)
4. `segment = source.split(":", 1)[0]`; empty → `(False, "empty-segment")` — `:4138-4140`
5. `registry_get is None` → `(False, "registry-unavailable")` — `:4141-4142`
6. registry raises → `(False, "registry-error:<ExcType>")` — `:4143-4146`
7. `adapter is None` → `(False, "unregistered")` — `:4147-4148`
8. `source_type` not a chat type → `(False, "source-type-not-chat:<rendered≤40>")`
   (bounded repr rendering; non-str values render as the TYPE NAME) — `:4149-4165`
9. otherwise → `(True, "registered-chat:<source_type>")` — `:4166`

**ALL call sites** (grep-verified over `daemon/`):

| Call site | Anchor | Role |
|---|---|---|
| `daemon/manager.py:6114` | inside `stamp_user_origin_window` (`:6076-6139`) | **the stamp site** — the only writer of the per-turn user-origin window |
| `daemon/tools/upgrade_tools.py:2277` | inside `_user_origin_block()` (`:2241-2307`) | `release_info` **user-origin drift probe** — "CONSUMES classify_user_origin — no second classification lives here" (`:2250-2251`) |
| `daemon/tools/upgrade_tools.py:3884` | tool docstring | "single source of truth" pointer |
| `daemon/manager.py:6087`, `daemon/manager.py:11493`, `daemon/tools/job_queue.py:1427`, `daemon/constants.py:1286` | comments | anti-forgery notes ("that would forge …") |

**How the live-promote ceremony consumes it (factor 2):**

- The stamp site reads the **triggering message's `source` string** plus its `message_id`
  (`daemon/manager.py:6080-6082`, `:6105-6114`). Classification reads **registry metadata
  only** (`source_type` is daemon-controlled, write-once, never message-supplied) —
  `daemon/manager.py:6100-6103`.
- On a user-origin source it stamps
  `self._user_origin_windows[instance_id] = {source, message_id, stamped_at, expires_at}`
  — `daemon/manager.py:6126-6131`; on anything else it **CLEARS** the window
  (per-turn semantics) — `daemon/manager.py:6132-6133`.
- `expires_at = _uj.iso_plus(now, NONCE_TTL_S)` — `daemon/manager.py:6130`, with
  `NONCE_TTL_S` imported from `daemon.tools.upgrade_journal` (`:6106-6108`, fallback
  `60*60`); the manager also mirrors it as `PROVENANCE_TTL_S = 60*60`
  (`daemon/manager.py:270-272`).
- Every observation (stamped or cleared) is recorded in `_user_origin_last_stamp`
  (source + classifier detail) so a refusal can name the **observed** source —
  `daemon/manager.py:6116-6124`.
- The gate then reads the window for the CALLING instance:
  `manager._user_origin_windows.get(current_instance_id)` —
  `daemon/tools/upgrade_tools.py:3388-3391`. Missing window → refusal naming the observed
  source + detail token — `:3393-3419`. Unparseable/absent `expires_at` counts as
  **EXPIRED** (fail-closed) — `:3421-3432`.

**What "genuine user turn" means mechanically, per call site:**

| Layer | Metadata inspected | Anchor |
|---|---|---|
| Stamp | the triggering message's `source` string (daemon-controlled) + `message_id` | `daemon/manager.py:6080-6082`, `:6105-6131` |
| Gate (factor 2) | the in-memory window dict for the calling instance: `source`, `message_id`, `stamped_at`, `expires_at` | `daemon/tools/upgrade_tools.py:3388-3435` (fields minted at `daemon/manager.py:6126-6131`) |
| Gate (factor 3) | the **message CONTENT** of the row identified by `message_id`, read from the MessageQueue repository (`manager._queue_repository.get(msg_id)` → `row.content`) | `daemon/tools/upgrade_tools.py:3508-3531` |
| Adapter leg | registry `source_type` of the first `:`-segment of `source` | `daemon/tools/upgrade_journal.py:4138-4166` |

**Reuse rule for prompt-assist:** call `upgrade_journal.classify_user_origin(source, registry_get)`
directly (or read `manager._user_origin_windows[current_instance_id]` — the *same* object the
promote gate reads). Do NOT fork the arm set, do NOT re-derive chat types from source-id
prefixes. Note `daemon/tools/upgrade_journal.py:4089-4093`: the classifier's chat-type set is
deliberately **not** the same as `constants.CHAT_SOURCE_PREFIXES` (worker-lane routing set) —
do not dedup the two.

---

## 2. The 3-factor live-promote nonce pattern

**Tool surface** — `daemon/tools/upgrade_tools.py:3192-3200`:

```python
@register_tool_category("system_upgrade")
@tool
async def system_upgrade(target_env, version=None, user_confirmed=False, dry_run=True, nonce=None) -> str
```

Contract docstring: `daemon/tools/upgrade_tools.py:3860-3907` (param semantics `:3870-3877`;
the §4.3 3-factor description `:3879-3888`; the refusal-token vocabulary `:3892-3904`;
"Never auto-retry a refusal — relay it verbatim" `:3906-3907`).

**Nonce primitives (all in `daemon/tools/upgrade_journal.py`):**

| Primitive | Anchor | Note |
|---|---|---|
| `NONCE_TTL_S = 60 * 60` | `:141` | 60 min; widened from 15 min per ADR-036 |
| `_NONCE_ALPHABET` (base32) | `:172` | `A-Z2-7` |
| `NONCE_RE` | `:173` | `^CONFIRM-[A-Z2-7]{8}$` |
| `mint_nonce()` | `:205-208` | `CONFIRM-` + 8 base32 chars (secrets) → **40 bits** |
| `nonce_grouped()` | `:211-215` | display rendering `CONFIRM-XXXX-XXXX` |
| `nonce_normalize()` | `:218-222` | uppercase, strip dashes/whitespace |
| `nonce_equals()` | `:225-228` | normalized equality |
| `nonce_in_content()` | `:231-246` | nonce present in message content (canonical or grouped) |
| `now_iso()` / `parse_iso_utc()` / `iso_plus()` | `:176` / `:181` / `:191` | ISO TTL arithmetic helpers |

**Record + storage:**

- `PendingAction` dataclass — `daemon/tools/upgrade_journal.py:1896-1923`:
  `run_id, nonce, kind, env, target, issued_at, ttl_expires_at (default = issued_at +
  NONCE_TTL_S), issued_to_instance, consumed_at, consumed_by_message_id`.
- Storage = the **install journal file** `<install_dir>/releases/state.json`, additive
  extension key `"pending_actions"` keyed by `run_id`
  (module docstring `daemon/tools/upgrade_journal.py:50`; `store_pending_action` `:1986-1994`;
  `find_pending_action_by_nonce` `:1997-2017`; `consume_pending_action` `:2020`;
  opportunistic GC `_gc_pending_actions` `:1926-1958`; sweep entry `gc_pending_actions` `:1961-1983`).
  The record is disk-persisted **on purpose** — "survives MessageQueue wipe + daemon death"
  (`:1898-1899`).

**Flow:**

1. **Mint** — on `dry_run=True` **and** `self_env == "live"` the tool mints
   `run_id = mint_run_id()` + `nonce = mint_nonce()`, builds a `PendingAction(kind="upgrade",
   env=…, target=version, issued_to_instance=current_instance_id)` and persists it —
   `daemon/tools/upgrade_tools.py:3346-3356`. It then prints the grouped nonce to the user
   ("Nonce single-use, expires in 60min") — `:3357-3367`. **No pipeline mutation** on dry-run.
2. **Gate** (armed path, live only) — `daemon/tools/upgrade_tools.py:3380-3541`:
   - Factor 1 — `user_confirmed` param must be true → else `user-confirmation-missing`
     (`:3382-3386`).
   - Factor 2 — a **live user-origin window** for the calling instance, unexpired
     (`:3387-3435`).
   - Factor 3 — the nonce param must resolve to a pending action
     (`uj.find_pending_action_by_nonce`, `:3445`) that is (a) unconsumed
     → else `nonce-already-used` (`:3451-3455`), (b) issued **to this instance**
     → else `nonce-instance-mismatch` (`:3456-3469`), (c) TTL-unexpired (unparseable TTL
     counts as expired) → else `nonce-expired` (`:3471-3484`), (d) **action-bound** to the
     same `(kind, env, target)` → else `nonce-action-mismatch` (`:3485-3506`), and (e) the
     **triggering human message's CONTENT must carry the nonce**
     (`:3508-3537`; row-unreadable → `nonce-verification-unavailable` `:3524-3530`).
   - Any failure ⇒ `_live_gate_summary(label, factor_failures)` — `:3540-3541` (the
     ceremony NEVER partially proceeds).
3. **Burn (single-use)** — the nonce is consumed **after** all arm preflights, so a refusal
   never burns it ("nonce NOT burned", `:3708-3777`): `uj.consume_pending_action(install_dir,
   confirmed_action, confirmed_msg_id)` — `daemon/tools/upgrade_tools.py:3778-3782`. The arm
   row then records `nonce`, `nonce_consumed`, `confirmed_by_human`, `confirmed_source`
   — `:3785-3800`.

**What maps cleanly to a per-item 6-char token / 60 min / re-mint:**

- Reuse `nonce_normalize` / `nonce_equals` / `nonce_in_content` **verbatim**
  (`daemon/tools/upgrade_journal.py:218-246`) — write a NEW regex (do not reuse `NONCE_RE`,
  which pins `CONFIRM-` + 8).
- Reuse `now_iso` / `parse_iso_utc` / `iso_plus` (`:176`, `:181`, `:191`) and the
  `ttl_expires_at` semantics (unparseable ⇒ EXPIRED, fail-closed).
- Reuse the `PendingAction` **shape** as the per-item token row template
  (`:1896-1923`) and the consume semantics (`consumed_at` + `consumed_by_message_id`).
- Reuse the **factor-2 read** (`manager._user_origin_windows[current_instance_id]`) and the
  factor-3 read (`manager._queue_repository.get(msg_id).content`) unchanged.

**What does NOT scale down (design warnings):**

1. **Entropy.** 8 base32 chars = 40 bits; 6 base32 chars = **30 bits ≈ 1.07e9** guesses. A
   short token is brute-forceable if any tool/REST endpoint can probe it. Mitigation
   candidates: attempt counter per item, per-item lock, short TTL, no probing surface, and
   (if the token is user-typed) rate-limit the attempt path. The live nonce's 40 bits is not
   a precedent for a 6-char token.
2. **Storage.** The promote nonce lives in the **install journal file**
   (`releases/state.json`), one per install dir. `prompt_versions` rows live in **PostgreSQL**
   — a per-item token table (or the `prompt_versions` table itself) is required; the journal
   store is not reusable as-is (and journaling into the install dir from a prompt lane would
   couple an agent tool to the release pipeline).
3. **Instance binding.** The promote nonce is bound to `issued_to_instance` — an
   agent-instance binding has no meaning for a per-ITEM token; bind instead to
   `(version_id, agent_id, artifact, version)`.
4. **Dry-run semantics.** The promote mint happens inside `dry_run`; a prompt lane has no
   "dry-run" — minting must be an explicit, separately-audited step (the "show the diff and
   the token" step).
5. **The journal's GC** is opportunistic (`:1926-1958`) and its sweep is a separate scheduled
   tick — a PG-backed token store must define its own TTL sweep, and must KEEP a consumed row
   long enough to answer "already used" (the promote GC keeps one entry via `keep_run_id`,
   `:1931-1934`).
6. **"Never auto-retry a refusal"** (`daemon/tools/upgrade_tools.py:3906-3907`) is a doctrine
   the prompt lane should inherit verbatim.

---

## 3. `daemon/tools/notify_user.py` — the ari-EXACTLY + rate-limit + audit precedent

**Module contract** — `daemon/tools/notify_user.py:1-98`:

- **Three-layer access posture, ALL required** — `:19-30`:
  1. the category is **PRIVILEGED** (entry #16) → never in the default-grant universe;
  2. `agents/ari/meta.json` carries the explicit `"notify-user"` allow entry — **and ONLY
     ari**;
  3. the **tool body** rejects any caller whose `agent_id != "ari"` — "a LITERAL tool name in
     an allow list bypasses the category gate, so the category alone cannot spell 'ari only'".
- **Board always / notify may** — `:32-39`; **steering clause** (double-send semantics) —
  `:41-50`; **never-block + the 10s bound (W4)** — `:52-72`; **rate gates (D5/C3/B2)** —
  `:74-98`.
- **Rate gates:** N = 1 per turn, enforced as a **≥60 s minimum interval per calling
  instance** (turn identity is not tool-visible; turns are serialized per instance, so the
  interval is the enforceable proxy) — baseline = the instance's last **ACCEPTED-class**
  ledger row (refusals and `rate_limited` rows never extend the cooldown) —
  `daemon/tools/notify_user.py:76-81`. M = **6 per rolling hour per RESOLVED DESTINATION**
  (`target_hash = sha256(source_id + ":" + external_user_id)`), counted from the ledger with
  **commit-before-count under a per-destination `asyncio.Lock`**; only ACCEPTED-class rows
  consume budget; **ledger-write failure ⇒ fail-closed** `notify_audit_unavailable` (no
  ledger row, no send) — `:82-98`.
- **Authorized agent constant** — `daemon/tools/notify_user.py:180`:
  `NOTIFY_AUTHORIZED_AGENT = "ari"`.
- **Envelope shapes** (house forms a new tool should copy):
  REFUSED — `daemon/tools/notify_user.py:314-316`; FAILED — `:319-321`; the generic
  "not-ari" ERROR envelope — `:703-711` (both cited by the newer sibling
  `daemon/tools/prompt_reload.py:94-110`, which reuses them verbatim).

**Kill-switch** — `daemon/config.py:3166-3224` (`class NotifyUserConfig(BaseSettings)`,
env prefix `ENSEMBLE_NOTIFY_USER_`):

| Knob | Anchor | Default |
|---|---|---|
| `enabled` (`ENSEMBLE_NOTIFY_USER_ENABLED`) | `daemon/config.py:3181-3187` | `True` → typed refusal `notify_disabled` |
| `max_per_hour` | `:3188-3197` | 6 |
| `min_interval_seconds` | `:3198-3207` | 60 |
| `timeout_seconds` | `:3208-3215` | 10.0 |
| `max_chars` | `:3216-3224` | 2000 |

Wiring: `daemon/config.py:6826` (`notify_user: NotifyUserConfig = Field(default_factory=…)`);
the class is auto-covered by the tests' `ENSEMBLE_` prefix tracker (`daemon/config.py:3171-3173`).

**Audit ledger (content-free)** — `daemon/repositories/notify_audit/`:

- `daemon/repositories/notify_audit/models.py:71-97` — outcome vocabulary
  (`accepted`, `dispatched`, `delivery_failed`, `rate_limited`, `delivery_unknown`, `refused`)
  and the authoritative budget predicate `ACCEPTED_CLASS_OUTCOMES` (`:92-97`).
- `daemon/repositories/notify_audit/models.py:100-201` — the `notify_audit` table
  (`__tablename__ = "notify_audit"` `:134`): `agent_id` `:161`, `instance_id` `:162`,
  `action` `:165-168`, `source_id` `:169`, `target_hash` `:173` (raw destination NEVER
  stored), `outcome` `:177`, and — the reusable audit shape — **`body_length` + `body_sha256`
  only, never the body** (class docstring `:127-129`).
- `daemon/repositories/notify_audit/repository.py:71` (the ONE caller — the tool),
  `:103`/`:161` (`action="notify_user"`), `:192`/`:224` (budget predicate over
  `ACCEPTED_CLASS_OUTCOMES`).
- Migration: `daemon/migrations/versions/20261025_000001_create_notify_audit.sql:1-…`.
- Boot/wiring seam: `daemon/api.py:1142-1143` ("backs the `notify-user` privileged tool
  category — the `notify_user` tool's audit ledger AND its rate-limit budget");
  `daemon/repositories/factory.py:89` ("the N1 REQUIRED seam: the table is the
  notify_user rate-limit budget source").

**Factory + construction** — `daemon/tools/instance.py:2896-2927`
(`create_notify_user_tools_if_available` — always constructs; the tool refuses cleanly when
a handle is missing) and the extend inside `create_instance_tools`
`daemon/tools/instance.py:6810-6814`, whose **PLACEMENT** is load-bearing:
"MUST precede `create_help_tool` … `scan_tools_for_full_docs` … `_apply_tool_filter`
… A later extend is either invisible to the help tool or BYPASSES the privileged strip" —
`daemon/tools/instance.py:6791-6809`.

**What prompt-assist can reuse from this precedent:** the kill-switch class pattern (read at
CALL time; the tool STAYS REGISTERED under any switch state — refusal-at-call, never
construction-absence, `daemon/tools/prompt_reload.py:25-28`), the typed REFUSED/FAILED
envelopes, the content-free ledger shape (sha256 + length), and the "budget source is the
ledger; ledger write failure ⇒ fail-closed" rule. **What likely does NOT carry:** the
per-destination hourly cap (an approval lane is per-item, not per-destination); keep the
per-item attempt counter instead.

---

## 4. ari-EXACTLY pinning inventory + the sites a new `prompt-assist` category must touch

**4.1 Layer-3 (in-body) authorized-agent constants — the "ari EXACTLY" set:**

| Constant | Anchor | Audience |
|---|---|---|
| `NOTIFY_AUTHORIZED_AGENT = "ari"` | `daemon/tools/notify_user.py:180` | ari |
| `SELF_ALARM_AUTHORIZED_AGENT = "ari"` (+ `SELF_ALARM_TARGET_AGENT` `:131`) | `daemon/tools/self_alarm.py:125`, `:131` | ari |
| `PROMPT_RELOAD_AUTHORIZED_AGENT = "ari"` | `daemon/tools/prompt_reload.py:70` | ari (newest, same prompt family) |
| `MEMOIR_AUTHORIZED_AGENT = "ari"` | `daemon/tools/memoir.py:183` | ari |
| `VIEW_WRITE_AGENTS = frozenset({"ari"})` | `daemon/tools/views_auth.py:67` | ari |
| `BOARD_LINK_AGENTS = frozenset({"ari"})` | `daemon/tools/views_auth.py:77` | ari |
| `BOARD_AUTHORIZED_AGENTS = frozenset({"ari"})` | `daemon/tools/board_tools.py:104` | ari |
| `FRONT_DOOR_AUTHORIZED_AGENTS = frozenset({"ari"})` | `daemon/tools/job_queue.py:1165` | ari |
| `MCP_RPC_AUTHORIZED_AGENTS = frozenset({"ari","leader"})` | `daemon/tools/mcp_rpc_tools.py:82` | **not** ari-exactly |
| `{"ari","leader","maintenancer"}` | `daemon/tools/wedge_capture.py:85` | **not** ari-exactly |

**4.2 The newest precedent to copy: `prompt-reload` (2026-11-06, privileged entry #20)**

- Category-key constant + kill-switch + authorized agent: `daemon/tools/prompt_reload.py:63`,
  `:67`, `:70`; the call-time kill-switch resolver `:76-91`; the REFUSED/FAILED envelopes
  `:94-101`; the generic not-ari envelope `:104-110`; the ONE frozen INFO line `:113-135`;
  the factory `create_prompt_reload_tools(...)` `:208`.
- Registry entry + rationale (ARI-EXACTLY, R5): `daemon/tools/_tool_registry.py:751-772`.
- Category module: `daemon/tools/prompt_reload.py:1-39` (the three-layer access posture
  docstring `:12-23`).
- Manager primitive the tool wraps: `daemon/services/prompt_refresh.py` (refusal/failure
  codes `:147-156`, INFO marker `:265`).

**4.3 The privileged-category registry + the SAME-PR pin rule**

- `PRIVILEGED_TOOL_CATEGORIES` — `daemon/tools/_tool_registry.py:418-773` (20 entries;
  `notify-user` `:654-680` (entry #16), `metrics-read` `:703`, `nest-report-read` `:730`,
  `memoir` `:750`, `prompt-reload` `:751-772` (entry #20)).
- **SAME-PR RULE** — `daemon/tools/_tool_registry.py:366-417`: adding/removing a category
  REQUIRES updating **all** pin sites **in the same commit** (the set must never be red
  between two commits), and the census must be re-derived BY GREP (`:412-417`):
  - `grep -rn "PRIVILEGED_TOOL_CATEGORIES == frozenset" tests/ test/ --include=*.py` (`:413`)
  - `grep -rn "PRIVILEGED_TOOL_CATEGORIES == EXPECTED_PRIVILEGED_SET" tests/ test/` (`:414`)
  - `grep -rn "len(PRIVILEGED_TOOL_CATEGORIES)" tests/ test/` (`:415`)
  - "Silent additions and silent removals (fail-open regressions) both trip the pins" (`:416-417`).

**4.4 The pin-file census (14 exact-set/count files, enumerated in the registry comment
`daemon/tools/_tool_registry.py:379-406`) — every one must be bumped for a new category:**

1. `tests/unit/tools/test_upgrade_registration.py` (`:379`)
2. `tests/unit/tools/test_attestation_registration.py` (`:380`)
3. `tests/integration/test_maintenancer_spawn_resolves_tools.py` (`:381`; category rows
   `:479` `notify-user`, `:500` `prompt-reload`)
4. `tests/integration/test_service_tool_flag_off_byte_identical.py` (`:383-384`; rows
   `:916`, `:937`)
5. `tests/unit/tools/test_secrets_registration.py` (`:385-387`)
6. `tests/unit/test_board_registration.py` (`:388-389`; set rows `:105`, `:121`;
   **ari `tools.allow` ARRAY-ORDER trailing-entry pin** `:295-306` — the ari array literal
   is pinned as `"…","notify-user","pok","nest-report","memoir","prompt-reload"]` `:306`)
7. `tests/unit/test_self_alarm_registration.py` (`:390-391`)
8. `tests/unit/test_instance_ops_registration.py` (`:392-394`; rows `:123`, `:139`)
9. `tests/unit/test_wedge_capture_registration.py` (`:395-396`)
10. `tests/unit/test_notify_user_registration.py` (`:397-398`; rows `:77`, `:93`; the
    10-step proof (a)–(j) at `:100-131` incl. the **ari-ONLY census** (h) `:23-25` and the
    **decorator-order pin** (j) `:28-29`, `:108-131`)
11. `tests/unit/test_metrics_read_registration.py` (`:399-400`; row `:108`)
12. `tests/unit/test_anomaly_report_tool_registration.py` (`:401-402`)
13. `tests/unit/tools/test_nest_report_read_registration.py` (`:403-404`)
14. `tests/unit/test_memoir_registration.py` (`:405-406`; rows `:86`, `:163`, `:363`)

**Additional sites the newest lanes touched (not in the 14-file comment — derive, don't copy):**

15. `tests/unit/test_anomaly_report_grant_pins.py:92-96` + `:301-313` — the **ari grant
    array** pin (new category ⇒ count 9 → 10 and the tail literal `:310`).
16. `tests/unit/metrics/test_metrics_family_adjudication.py:256` (the new tool module joins
    the metrics-family module list) and `tests/unit/metrics/test_metrics_vocabulary_amendment.py:30`.
17. `daemon/checkpoint_metrics.py:244-263` — **tool-category vocabulary** bump
    (`VOCABULARY_VERSION = 8` `:263`; the frozen artifact label set grew 60 → 61 for
    `prompt-reload`, 61 → 62 for `pok`; "the artifact and this constant move in the SAME
    change" — F-1 trap class, `:246-248`).
18. `agents/ari/meta.json` — the allow entry itself (`"notify-user"` `:44`,
    `"pok","nest-report"` `:45`, `"memoir"` `:46`, `"prompt-reload"` `:47`; a new category
    is **appended at the tail** to satisfy the array-order pin) — plus the
    **ari-ONLY census** ("appears in `agents/ari/meta.json` and NOWHERE ELSE across every
    `agents/*/meta.json`", `tests/unit/test_notify_user_registration.py:23-25`).

**4.5 Concrete checklist for a new `prompt-assist` ari-EXACTLY category** (the "16-site"
equivalence): 14 pin files + the ari grant-array pin + the metrics vocabulary pair + the
registry entry + `agents/ari/meta.json` + the in-body agent gate + the factory extend.
The notify-user commission's "16-site" label (project critical note) matches the
registry-comment census + the ari allow entry + the tool-body gate; the derived list above
is 18 named locations across 16 files — **the exact historical count was not re-derived from
a single source in this lane** (the notify-user commission report is not in this tree).

---

## 5. New daemon tool registration path (the three-step seam) — exact file:site list

The house seam is **decorator + registry entries + construction** ("decorator-only
registration is SILENTLY INVISIBLE" — `daemon/tools/instance.py:6794-6796`,
`daemon/tools/_tool_registry.py:397`).

| # | Step | Site (file:line) | Notes |
|---|---|---|---|
| 1 | Tool module + decorator | `daemon/tools/prompt_assist.py` (new); `@register_tool_category("prompt-assist")` **ABOVE** `@tool` — decorator def `daemon/tools/_tool_registry.py:776-800` | order is load-bearing: the strip reads the WRAPPED object (`tests/unit/test_notify_user_registration.py:108-131`); the decorator also sets `_tool_category_first_party` (`:799`) |
| 2a | Category→module map | `CATEGORY_MODULES["prompt-assist"] = "daemon.tools.prompt_assist"` — pattern `daemon/tools/_tool_registry.py:1246` (map starts `:1233`; the "MUST be `daemon.tools.*`" rule `:1239-1246`) | a `daemon.services.*` value is NEVER discovered by the AST scanner and the drift pin fails at boot |
| 2b | Dynamic tool names | `DYNAMIC_TOOL_NAMES` — `daemon/tools/_tool_registry.py:23` (entries: `notify_user` `:150`, `prompt_reload` `:324`) | factory-built tools must appear here |
| 2c | Known tool names | `KNOWN_TOOL_NAMES` — `daemon/tools/_tool_registry.py:1585-1590` (`notify_user` `:1590`) | inventory + frozen-binary fallback; drift equality is pinned (`test_known_tool_names_matches_source_exactly_no_drift`, referenced `:251-253`) |
| 2d | Privileged category | `PRIVILEGED_TOOL_CATEGORIES` — `daemon/tools/_tool_registry.py:418` (add the new entry; it becomes **#21**) | opt-in only; never in the default-grant universe (`:191-204`) |
| 3a | Factory wrapper | `daemon/tools/instance.py` — import (`:291-293` pattern) + `create_prompt_assist_tools_if_available(...)` (pattern `:2896-2927`) | always constructs; refusals happen at call time |
| 3b | Construction extend | `daemon/tools/instance.py:6810-6814` (notify extend) / `:7013` (prompt-reload extend) — inside `create_instance_tools` | MUST precede `create_help_tool` / `scan_tools_for_full_docs` / `_apply_tool_filter` (`:6798-6802`) |
| 4 | Authorization (strip) | `_apply_tool_filter` — per-agent allow list + privileged-category default-deny strip; referenced at `daemon/tools/instance.py:6800-6802` and `daemon/tools/_tool_registry.py:191-204` | the exact `def _apply_tool_filter` line was NOT located in this lane (see §0 caveats) — grep it before citing |
| 5 | Layer-3 in-body gate | `daemon/tools/prompt_assist.py` — `PROMPT_ASSIST_AUTHORIZED_AGENT = "ari"` (pattern `daemon/tools/prompt_reload.py:70`) | the category alone cannot spell the audience |
| 6 | Documentation | the tool docstring + `tool_help` wiring — house pattern `daemon/tools/upgrade_tools.py:3201` (`Use tool_help("system_upgrade") for details.`); `scan_tools_for_full_docs` (referenced `daemon/tools/instance.py:6799-6800`, definition `daemon/tools/_tool_registry.py:901` area) | the help tool snapshots the tool list — the extend must precede it |
| 7 | Manager handle | `daemon/manager.py:1778` (`self._prompt_version_service = PromptVersionService(...)`, import `:162`) — the factory reads the mounted service; `daemon/manager.py:213` is the prompt-reload lane's manager note | manager.py is where the service is MOUNTED, not where tools are built (build is `daemon/tools/instance.py`) |
| 8 | Kill-switch config | new `PromptAssistConfig(BaseSettings)` — pattern `daemon/config.py:3166-3224`; wire the field at `daemon/config.py:6826`-style | read AT CALL TIME; tool stays registered |
| 9 | Audit ledger + migration | `daemon/repositories/<x>_audit/{models,repository}.py` + `daemon/migrations/versions/<ts>_create_<x>_audit.sql` (precedent `20261025_000001_create_notify_audit.sql`) + `daemon/api.py:1142-1143` (create_all seam; prompt_versions precedent `daemon/api.py:1089-1123`) | every new SQLModel table needs its own `*.sql` in `daemon/migrations/versions/` |
| 10 | Metrics vocabulary | `daemon/checkpoint_metrics.py:244-263` (+ the frozen artifact label set regenerated in the SAME change) | the F-1 trap class |
| 11 | Agent allow entry | `agents/ari/meta.json` — append `"prompt-assist"` at the tail of `tools.allow` (`:44-47`) | the array-order pin (`tests/unit/test_board_registration.py:295-306`) |
| 12 | Approval path (target rows) | `daemon/repositories/prompt_version/repository.py` — `submit_for_approval` `:1520-1537`, `approve` `:1541-1573`, `reject` `:1577-1607`, `VALID_TRANSITIONS` `:217`, `create_and_submit` `:2463`; states `PROMPT_STATES` `daemon/repositories/prompt_version/models.py:103-109`; actor model `ActorContext` `:515-569` (`is_internal` `:568-569`), `actor_from_instance` `:572-590`, `actor_from_request` `:593-…` | **see the blocker in §7** |

**Blocker for the planner (approval lane vs the human-only rule):**
`PromptVersionRepository.approve()` and `.reject()` **refuse internal actors** —
`daemon/repositories/prompt_version/repository.py:1560-1565` and `:1594-1599`:
"only HTTP actors (session/bearer) may approve; internal actors cannot satisfy the
human-only approval rule (decisions.md F4)". `actor_from_instance` is the **only** constructor
that mints `auth_method == "internal"` (`:572-590`), and `actor_from_request` explicitly
refuses to mint internal and stamps `AuthContext.subject` verbatim, never request-body fields
(`:593-604`). A tool-initiated approval therefore **cannot** simply call `repo.approve()` with
an agent actor — the lane must either (a) introduce an explicit, audited approval provenance
kind for the agent lane (e.g. subject = the verified user identity, auth_method = a new
attested value), or (b) route through the HTTP lane carrying the nonce attestation, or
(c) add a repository method with its own F4 rule. **This is a first-class design decision, not
an implementation detail.**

---

## 6. Existing reusable token / nonce / confirmation utilities

**In `daemon/tools/upgrade_journal.py` (the promote-nonce family):**

| Utility | Anchor | Reuse verdict |
|---|---|---|
| `mint_nonce()` | `:205-208` | reuse the **alphabet** (`:172`), not the length — a 6-char base32 token is only 30 bits |
| `nonce_grouped()` / `nonce_normalize()` / `nonce_equals()` / `nonce_in_content()` | `:211-246` | reuse **verbatim** (dash/whitespace-insensitive echo) |
| `now_iso()` / `parse_iso_utc()` / `iso_plus()` | `:176` / `:181` / `:191` | reuse **verbatim** (TTL arithmetic; unparseable ⇒ expired) |
| `PendingAction` (record template) | `:1896-1923` | reuse as the **shape** of a per-item token row |
| `store_pending_action` / `find_pending_action_by_nonce` / `consume_pending_action` | `:1986` / `:1997` / `:2020` | reuse the **consume semantics** (`consumed_at` + `consumed_by_message_id`); the file-backed store is not reusable (journal file, not PG) |
| `_gc_pending_actions` / `gc_pending_actions` | `:1926-1958` / `:1961-1983` | reuse the GC rule ("GC only deletes what it can prove is dead") |

**The closest IN-MEMORY single-use store precedent — `daemon/tools/ens_db_tools.py`:**

- `REPAIR_NONCE_TTL_S = 300` — `daemon/tools/ens_db_tools.py:177`
- `_RepairNonce` record — `:490-496`; `_RepairNonceStore` — `:498-575`
- `mint(instance_id, target)` — `:518-529` (binds the nonce to an instance AND a target)
- `consume(nonce)` — `:535-563` returning the refusal-token vocabulary:
  `nonce-mismatch` `:552`, `nonce-instance-mismatch` `:554`, `nonce-action-mismatch` `:556`,
  `nonce-already-used` `:558-559`, `nonce-expired` `:561`
- expired-entry sweep — `:570-575`
- **Caveat:** in-memory ⇒ lost on daemon restart (the promote nonce is disk-persisted for
  exactly that reason). The refusal-token vocabulary is the most valuable inheritance here.

**DB-side nonce columns (table precedent):**

- `daemon/repositories/ens_db/models.py:86-90` (docstring: "Single-use confirm nonce … "
  "ttl_expires_at: ISO-8601 timestamp marking nonce expiry (5 minutes from issuance)"),
  columns `:153-154` (`nonce: str | None, max_length=64`; `ttl_expires_at: str | None`),
  serialization `:172-173`.
- `daemon/repositories/instance_ops/models.py:6` — another `nonce` / `ttl_expires_at` row.

**User-origin window storage (factor 2):**

- `manager._user_origin_windows` (per instance: `source`, `message_id`, `stamped_at`,
  `expires_at`) — `daemon/manager.py:6126-6131`; cleared at `:6132-6133`.
- `manager._user_origin_last_stamp` (observed source + classifier detail, per instance) —
  `daemon/manager.py:6116-6124`.
- `stamp_user_origin_window(instance_id, source, message_id)` — `daemon/manager.py:6076-6139`.

**Content-free audit shape:** `daemon/repositories/notify_audit/models.py:100-201`
(`body_length`, `body_sha256` — never the body), `ACCEPTED_CLASS_OUTCOMES` `:92-97`.

**Short-code generators elsewhere (do NOT reuse for an approval token):**
`secrets.token_urlsafe` at `daemon/auth/cli.py:189` (CLI auth tokens); the work-id mint
constitution `daemon/job_state/constitution.py:399-431` (`token_hex`/`token_urlsafe`/
`token_bytes` idioms; `KNOWN_MINT_SITES` `:169`) — that census governs **work ids**, and a
new mint site must be reconciled with it (`:557-575`, `:622-641`).

---

## 7. Reuse checklist for `prompt_assist` (max 15 bullets)

1. **Reuse, never fork:** call `upgrade_journal.classify_user_origin(source, registry_get)`
   (`daemon/tools/upgrade_journal.py:4116-4166`) for any user-origin decision; read the
   factor-2 evidence from `manager._user_origin_windows[current_instance_id]`
   (`daemon/manager.py:6126-6131`) exactly as the promote gate does
   (`daemon/tools/upgrade_tools.py:3388-3435`).
2. **Factor 3 read:** pull the triggering human message content from
   `manager._queue_repository.get(message_id).content`
   (`daemon/tools/upgrade_tools.py:3508-3531`) and match with
   `upgrade_journal.nonce_in_content` (`:231-246`) / `nonce_normalize` (`:218-222`).
3. **Token primitives:** reuse `now_iso`/`parse_iso_utc`/`iso_plus`
   (`daemon/tools/upgrade_journal.py:176`/`:181`/`:191`) and the base32 alphabet (`:172`);
   write a NEW regex (never reuse `NONCE_RE` `:173`, which pins `CONFIRM-`+8).
4. **Entropy warning:** 6 base32 chars = 30 bits (≈1.07e9). Compensate with a per-item
   attempt counter, per-item lock, ~60 min TTL, and no probing surface — the 40-bit promote
   nonce is not precedent for 6 chars.
5. **Record shape:** model the per-item token on `PendingAction`
   (`daemon/tools/upgrade_journal.py:1896-1923`), but bind it to
   `(version_id, agent_id, artifact, version)` instead of `issued_to_instance`; store it in
   PostgreSQL (the journal-file store at `:1986-1994` is install-dir state, not reusable).
6. **Single-use:** reuse the consume semantics from `consume_pending_action`
   (`daemon/tools/upgrade_journal.py:2020`) and the refusal-token vocabulary from
   `daemon/tools/ens_db_tools.py:535-563` (`nonce-mismatch`, `nonce-instance-mismatch`,
   `nonce-action-mismatch`, `nonce-already-used`, `nonce-expired`) — but use
   `nonce-item-mismatch` semantics for a per-item binding.
7. **In-memory vs durable:** `daemon/tools/ens_db_tools.py:498-575` is the cheap pattern but
   loses tokens on daemon restart; the promote nonce is disk-persisted for that reason
   (`daemon/tools/upgrade_journal.py:1898-1899`) — pick durability deliberately.
8. **Layer-1 (category):** add `prompt-assist` to `PRIVILEGED_TOOL_CATEGORIES`
   (`daemon/tools/_tool_registry.py:418`) as entry #21, with a rationale comment in the
   house style of `prompt-reload` (`:751-772`).
9. **Layer-2 (allow list):** append `"prompt-assist"` to `agents/ari/meta.json` `tools.allow`
   **at the tail** (`:44-47`) — the array-order pin lives at
   `tests/unit/test_board_registration.py:295-306`; the ari-ONLY census is
   `tests/unit/test_notify_user_registration.py:23-25`.
10. **Layer-3 (in-body gate):** add `PROMPT_ASSIST_AUTHORIZED_AGENT = "ari"` in the tool
    module (pattern `daemon/tools/prompt_reload.py:70`) and refuse every other caller with
    the notify-exact generic envelope (`daemon/tools/notify_user.py:703-711`).
11. **Pin census:** bump all 14 registry-enumerated pin files
    (`daemon/tools/_tool_registry.py:379-406`) **plus**
    `tests/unit/test_anomaly_report_grant_pins.py:92-96`/`:301-313`, the metrics pair
    (`tests/unit/metrics/test_metrics_family_adjudication.py:256`,
    `tests/unit/metrics/test_metrics_vocabulary_amendment.py:30`) and
    `daemon/checkpoint_metrics.py:244-263` — in the SAME commit (SAME-PR rule
    `daemon/tools/_tool_registry.py:366-417`).
12. **Kill-switch:** add a `PromptAssistConfig(BaseSettings)` modelled on
    `daemon/config.py:3166-3224` (`ENSEMBLE_PROMPT_ASSIST_ENABLED`, default ON), read at CALL
    time, tool stays registered (pattern `daemon/tools/prompt_reload.py:25-28`, `:76-91`).
13. **Audit ledger:** new table + `*.sql` migration in `daemon/migrations/versions/`
    (precedent `20261025_000001_create_notify_audit.sql`), content-free rows
    (`body_sha256` + `body_length` only — `daemon/repositories/notify_audit/models.py:127-129`),
    ledger-write failure ⇒ fail-closed (`daemon/tools/notify_user.py:95-97`).
14. **Construction:** factory wrapper + extend in `daemon/tools/instance.py`
    (`:2896-2927` + `:6810-6814` pattern) placed **before** `create_help_tool` /
    `scan_tools_for_full_docs` / `_apply_tool_filter` (`:6798-6802`); service handle mounted
    on the manager (`daemon/manager.py:1778` pattern).
15. **Approval blocker (decide FIRST):** `PromptVersionRepository.approve/reject` refuse
    internal actors (`daemon/repositories/prompt_version/repository.py:1560-1565`,
    `:1594-1599`; F4 "agents cannot self-approve") — the lane needs an explicit audited
    approver-provenance kind (or an HTTP round-trip carrying the nonce attestation);
    `actor_from_request` stamps `AuthContext.subject` verbatim and refuses internal
    (`:593-604`), so it cannot simply be re-used from a tool.

---

## 8. [REPORT SANITY] — unverified / not re-derived in this lane

- The commissioned worktree was **deleted mid-lane**; all anchors come from
  `/home/nea/ensemble-src-wt-v2915-bump` at the **same commit**
  (`0efdf106f3f66ccb400405f4137694592b72097d` = `refs/heads/release/v0.29.15`). That tree's
  cleanliness could not be checked (no `git` in this lane) — re-verify before citing.
- The **"16-site pin ceremony"** count for notify-user was not re-derived from a primary
  source; §4.4 lists 18 named locations across 16 files, derived from the registry comment
  (`daemon/tools/_tool_registry.py:379-406`) plus grep.
- `def _apply_tool_filter` — exact definition line **not located**; only its call-order
  constraint is cited (`daemon/tools/instance.py:6798-6802`).
- `daemon/routers/prompts.py` (the existing REST approval lane) — **not read**: the file is
  grep-skipped (>1.4 MB) and no line-range read was spent on it. The REST approve/reject
  endpoints that prompt-assist would parallel are therefore **unmapped**.
- `daemon/tools/prompt_artifacts.py` (blueprint's "HUMAN-SESSION-ONLY approval lane",
  `check_write` refusal sites) — **not read** (grep-skipped, no line-range read); the
  human-only constraint quoted in §5 comes from the repository layer, not from this module.
- The live-promote **happy path is never exercised** (live refusals only) —
  `daemon/tools/upgrade_tools.py:3889-3890`; the factor-3 message-content read therefore
  has no live-traffic precedent to copy.
- The 6-char token length, the ~60 min TTL and the re-mint policy are commission
  parameters, not observed house values; the house observed values are 40 bits / 60 min
  (`NONCE_TTL_S`) / 5 min (`REPAIR_NONCE_TTL_S`).


---

## 9. Post-write state of this note (the deliverable path is currently an ORPHAN directory)

- This note was written to the commissioned path
  `/home/nea/ensemble-src-wt-prompt-assist/.agents/shared/planning/prompt-assist/research/trust-anchors.md`.
- The git worktree itself is GONE: `/home/nea/ensemble-src-wt-prompt-assist/.git` does not
  exist (read attempt returns "File does not exist"). The directory now contains ONLY the
  `.agents/shared/planning/prompt-assist/research/` subtree created by this write. The
  worktree git admin dir `/home/nea/ensemble-src/.git/worktrees/ensemble-src-wt-prompt-assist/`
  also fails to resolve (its parent listing still shows the entry - that listing appears to be
  a stale snapshot).
- Consequence for the developer lane: `git worktree add` at that path will collide with the
  orphaned directory. Recovery order:
  1. `mv /home/nea/ensemble-src-wt-prompt-assist /home/nea/prompt-assist-note-holdout`
     (preserve this note - it is not in git);
  2. re-create the worktree on branch `feature/prompt-assist` at base
     `0efdf106f3f66ccb400405f4137694592b72097d` (the KV rows
     `wt.active.feature/prompt-assist.prompt-assist-20261110` and `wt.claim.prompt-assist`
     already record this base and path);
  3. copy the holdout note back to
     `<worktree>/.agents/shared/planning/prompt-assist/research/trust-anchors.md`;
  4. re-verify the anchors with `git -C <worktree> rev-parse HEAD` plus `git diff --stat`
     (this lane could not check the reference tree's cleanliness - see section 0).
- Ref facts recovered read-only for the reference tree used by every anchor:
  `/home/nea/ensemble-src-wt-v2915-bump/.git` points to
  `/home/nea/ensemble-src/.git/worktrees/ensemble-src-wt-v2915-bump`; that worktree's HEAD is
  `ref: refs/heads/release/v0.29.15`; and
  `/home/nea/ensemble-src/.git/refs/heads/release/v0.29.15` contains
  `0efdf106f3f66ccb400405f4137694592b72097d` - the exact commissioned base commit.

# Research Findings — prompt-assent Phase 1.5 (designated channels)

**Commission:** `prompt-assent` Phase 1.5 — designated channels (pinned-approver model).
**Doctrine:** [`doctrine.md`](doctrine.md) — *"this is channel, i often use channel to split tasks category in my mind, we consider channel is private/dm for now"* (carried verbatim).
**Method:** two explorer passes were dispatched by the lane leader and their findings were handed
to this planning lane inline; this file is the **condensed, re-verified digest** of both. Every
non-obvious claim below was **independently re-read in the worktree** by the plan writer; the
`:line` cites are the re-verified ones, not the explorers' paraphrases.

| Field | Value |
|---|---|
| Date | 2026-11-11 (UTC) |
| Worktree | `/home/nea/ensemble-src-wt-assent-p15-channels` (branch `feature/assent-designated-channels`) |
| Base commit | `efa8aaae28153b8e9c49b86abe19e92be5458ebe` (= `origin/latest` at cut time) |
| Sources | Explorer pass **A** (implementation, 2,151-line tool + surrounding tree) · Explorer pass **B** (lore / staging) · plan-writer re-verification of every load-bearing cite |

---

## Part 0 — The one-paragraph answer

**This is a predicate problem, not a data problem.** The daemon already carries every fact the
extension needs — the guild, the channel, the channel type, the author, the DM/group-DM
discriminators — on the message row, daemon-side, on every Discord turn. The only thing that
refuses a guild-channel mint today is the single predicate
`is_dm AND NOT is_group_dm` (`daemon/repositories/prompt_assent/mint_context.py:29-50`), reached
from the mint gate at `daemon/tools/prompt_assent.py:1708-1716`. The factor-2 window already
arms for guild-channel turns, because `classify_user_origin` classifies by **source**, not by
channel type (`daemon/tools/upgrade_journal.py:4116-4166`). Nothing in the adapter, the trust
anchor, the token store, the ledger schema or the FE needs to change.

---

## Part A — Implementation (re-verified)

### A-1 — The gate owner and the ari-EXACTLY posture

| Fact | Anchor |
|---|---|
| `daemon/tools/prompt_assent.py` (2,151 lines) is the **ONE** gate owner | `daemon/tools/prompt_assent.py:18` (module docstring) |
| Verbs pinned: `PROMPT_ASSENT_ACTIONS = ("list","mint","approve","reject")` | `daemon/tools/prompt_assent.py:178` |
| Privileged category `prompt-assent` | `daemon/tools/_tool_registry.py:822`; factory map `:1382`; construction-extend `daemon/tools/instance.py:7079-7083` (must precede help/docs/filter at `:7061-7065`) |
| Sole allow entry | `agents/ari/meta.json:48` (`"prompt-assent"`) |
| In-body `_not_ari` gate | `daemon/tools/prompt_assent.py:1433` |
| Input schema carries **NO** identity fields (pinned) | `daemon/tools/prompt_assent.py:1279-1355`; `tests/unit/test_prompt_assent_user_binding.py:144,:172` |
| Mint derives **everything** daemon-side from the window's **message row** | `daemon/tools/prompt_assent.py:674-702` (`_derive_context`) |

### A-2 — The identity extraction (D-5) — the nested key

- The approving-user key is the **NESTED** `message_metadata["discord"]["user_id"]`.
  `daemon/tools/prompt_assent.py:621-633` (`_discord_meta_of`). A top-level
  `metadata.get("user_id")` returns `None` and **must never be used**.
- External-user derivation: `daemon/tools/prompt_assent.py:636-671` — the canonical `dm:<user_id>`
  form is produced **only when the row proves a DM**. A designated-channel turn therefore yields
  **no** `dm:` external id (the source-mapping row may still supply one).

### A-3 — The privacy predicate — the cell this lane flips

| Fact | Anchor |
|---|---|
| Mint gate **M-4c** refuses when the context is not private; **no token row is written** | `daemon/tools/prompt_assent.py:1708-1716` |
| `context.is_private` delegates to the ONE classifier | `daemon/tools/prompt_assent.py:609-621` |
| The classifier: `is_dm is True AND is_group_dm is False` — identity checks, absent/unparseable ⇒ NOT private (fail-closed) | `daemon/repositories/prompt_assent/mint_context.py:29-50` |
| Approve leg **4f** uses the service predicate `assent_channel_not_private_code` → `token-channel-mismatch` | `daemon/tools/prompt_assent.py:2018-2026`; `daemon/services/prompt_version_service.py:460-472` |
| Approve legs 4d (channel) / 4e (approver) | `daemon/services/prompt_version_service.py:391-429`; called `daemon/tools/prompt_assent.py:1984-2016` |

**Leg order on the approve path (load-bearing for the extension):** 4d channel → 4e approver →
**4f privacy** → 4g lockout. The extension **adds a pass path to the privacy leg at 4f**; the approve leg **does** emit the two new codes (architect §5.2); every existing
refusal keeps its code and its budget behaviour.

### A-4 — The KEY FACT — server-channel turns already carry full identity daemon-side

The Discord adapter's metadata build site
(`daemon/sources/adapters/discord/adapter.py:1080-1095`) already emits, **nested** under
`metadata["discord"]`:

```
guild_id · guild_name · channel_id · channel_name · channel_type · thread_id · thread_name
parent_channel_id · user_id · user_name · user_display_name · message_id · is_dm · is_group_dm
```

with `channel_type = "dm" if is_dm else "thread" if thread_id else "text"`
(`daemon/sources/adapters/discord/adapter.py:1085` — **the anchors were re-verified on
2026-11-11**: `channel_type` at `:1085`, `is_dm = guild is None` at `:1064`, `is_group_dm` at
`:1076`, `channel_id` at `:1083`, `user_id` at `:1089`; the earlier `:1088`/`:1065`/`:1080`
were stale — architect §11 row 30), and
`is_group_dm` computed by the duck-typed `_classify_dm_channel`.

A **guild text-channel turn today**: `guild_id` set · `is_dm=False` · `is_group_dm=False` ·
`channel_type="text"` · `channel_id` set · `user_id` set. **It fails ONLY the `is_dm` leg.**
**The adapter is EXPECTED UNCHANGED by this lane.**

### A-5 — Factor 2 arms for guild-channel turns (re-verified — load-bearing)

`classify_user_origin` (`daemon/tools/upgrade_journal.py:4116-4166`) classifies by **source**:

- exact `"api"` arms (`:4093`, detail `exact:api`);
- otherwise the source id's **segment before `:`** is looked up in the sources registry and the
  adapter's `source_type` must be in `{telegram, slack, discord, whatsapp}` (`:4098-4100`);
- every failure mode fails **CLOSED** (`:4126-4165`).

A Discord guild-channel turn has source `discord:<…>` ⇒ segment `discord` ⇒ registered adapter
with `source_type=discord` ⇒ `is_user_origin=True`. **The factor-2 window arms for guild-channel
turns today, unchanged.** The trust anchor is **not** forked by this lane.

The window itself: `manager.stamp_user_origin_window`
(`daemon/manager.py:6077-6139`) — in-memory, per-instance, `{source, message_id, stamped_at,
expires_at}` (60-min TTL), stamped in the dispatch funnel (`daemon/manager.py:11529-11530`) and by
the wake sweep (`daemon/services/upgrade_journal_sweep.py:1857-1868`); read at
`daemon/tools/prompt_assent.py:737-748`; the gate's factor-3 read fetches the message row via
`manager._queue_repository.get(message_id)` (`daemon/tools/prompt_assent.py:867-886`), unreadable
⇒ `assent-verification-unavailable`.

### A-6 — The token/ledger hardening (all unchanged by this lane)

| Fact | Anchor |
|---|---|
| Hash-only token (`sha256`), no raw token column | `daemon/tools/prompt_assent.py:564-566`; `daemon/repositories/prompt_assent/models.py:12-30` |
| TTL = `NONCE_TTL_S` (60 min) imported from `upgrade_journal` (SSOT) | `daemon/repositories/prompt_assent/models.py:127-146`; `daemon/tools/upgrade_journal.py:141` |
| Single-use consume-CAS + partial unique index `uq_prompt_assent_tokens_live_version` | `daemon/repositories/prompt_assent/models.py:188-210` |
| `render_sha256` binding, authoritative re-check inside the D-4 consume hook txn | `daemon/services/prompt_version_diff.py:1751-1770`; `daemon/tools/prompt_assent.py:2038-2048`; `tests/unit/test_prompt_assent_consume_hook.py:136` |
| Ledger table `prompt_assent_tokens` + mint-context columns `mint_source_id` / `mint_channel_id` / `mint_is_dm` / `mint_user_id` / `minted_external_user_id` | `daemon/repositories/prompt_assent/models.py:149-258`; written at `daemon/repositories/prompt_assent/repository.py:129,:200`; the tool's mint site `daemon/tools/prompt_assent.py:1789` |
| Migration `20261110_221500_create_prompt_assent_tokens.sql` — the 128th file | `daemon/migrations/versions/20261110_221500_create_prompt_assent_tokens.sql` |
| K-9 hourly mismatch budget (in-RAM, per relay instance) | `daemon/tools/prompt_assent.py:75-81,:277-326` |

**The decisive schema observation:** `mint_is_dm` is an **int 0/1** column
(`daemon/repositories/prompt_assent/models.py:247-250`) and the mint site writes
`mint_is_dm=bool(context.is_dm)` (`daemon/tools/prompt_assent.py:1789`). A designated-channel mint
writes **`mint_is_dm=0`**. Since a guild channel can mint **only** when designated, the triple
(`mint_channel_id`, `mint_is_dm=0`, `mint_user_id`) is already **proof-by-construction** that the
mint happened in a designated channel, witnessed by the pinned approver. **No new column and no
new table are needed** — and therefore **no new migration** (see `decisions.md` **D-5**).

### A-7 — The kill-switch (unchanged)

| Fact | Anchor |
|---|---|
| `KILL_SWITCH_ENV = "ENSEMBLE_PROMPT_ASSENT_ENABLED"` | `daemon/tools/prompt_assent.py:145` |
| **ONE** read site in `_resolve_knobs`; call-time via `_resolve_enabled` | `daemon/tools/prompt_assent.py:408,:466` |
| `PROMPT_ASSENT_DEFAULT_ENABLED = False` | `daemon/config.py:3242` |
| `PromptAssentConfig` is **ENV-ONLY** (no yaml tier; a hand-added yaml block would be silently ignored) | `daemon/config.py:3246-3301` (`:3273-3279`) |
| An **invalid spelling fails closed** with a warning | `daemon/tools/prompt_assent.py:339-353,:394-420` |
| Per-knob repair-to-fallback semantics (an invalid knob must not blind the others) | `daemon/tools/prompt_assent.py:384-463` |

### A-8 — The refusal taxonomy today (27 codes)

`daemon/tools/prompt_assent.py:195-223` — the complete `REFUSAL_TAXONOMY`. The codes this lane
must place are tracked by `PHASE2_PLACED_CODES` (`:227-236`); the mismatch-class subset
(`MISMATCH_CODES`) is the set that **burns the K-9 hourly budget**.

The taxonomy is **pinned verbatim** in the registration test
(`tests/unit/tools/test_prompt_assent_registration.py:73` `ARCHITECT_REFUSAL_TAXONOMY`, asserted
`==` at `:300`), so **any taxonomy change is a pin change**. The `not-ari` code is a generic
envelope marker and is **never** emitted as a `reason=` code.

### A-9 — The test surface that pins the DM-only cells

| Fact | Anchor |
|---|---|
| 21 files `tests/unit/test_prompt_assent_*.py` | `tests/unit/` (see `ls`) |
| **The flip cell** — a guild channel is refused | `tests/unit/test_prompt_assent_mint_gate.py:102-115` |
| Group-DM refused, no row | `tests/unit/test_prompt_assent_mint_gate.py:84-90` |
| Absent discriminator refused | `tests/unit/test_prompt_assent_mint_gate.py:92-100` |
| A proven 1:1 DM mints | `tests/unit/test_prompt_assent_mint_gate.py:117-124` |
| A stale window writes no row | `tests/unit/test_prompt_assent_mint_gate.py:65-80` |
| Channel mismatch / different human / no identity args | `tests/unit/test_prompt_assent_user_binding.py:52,:87,:144,:172` |
| The DM-vs-group-DM classifier cells | `tests/unit/test_prompt_assent_group_dm_discriminator.py:113-174` |
| The kill-switch matrix (default OFF, invalid ⇒ fail closed, armed) | `tests/unit/test_prompt_assent_kill_switch.py:219-300` |
| The FE-unchanged suite | `tests/unit/test_prompt_assent_fe_unchanged.py` |
| **FIXTURE GOLD** — `build_discord_meta` is already fully parameterizable (`user_id`, `channel_id`, `is_dm`, `is_group_dm`, `guild_id`, `channel_type`) | `tests/helpers/prompt_assent_tool_fixture.py:137-156` |
| The flow rig `build_flow` / `mint_for` / `echo` | `tests/helpers/prompt_assent_flow.py:63-130` |
| The registered pack | `test/packs/prompt_assent_phase3_unit_test.sh`; `.agents/tester/PACKS.md:3480-3481` |

**A designated-channel leg is therefore a one-liner in the fixture vocabulary:**
`build_discord_meta(guild_id="…", channel_type="text", is_dm=False, channel_id="…",
user_id="…")` + a window + a registered discord source.

### A-10 — The config-surface precedents (both, with their exact reach)

**(a) Env-only `PromptAssentConfig`** — scalar knobs only today (`enabled`, `hourly_mismatch_budget`),
`daemon/config.py:3246-3301`. `nest_report` precedent for env-only.

**(b) DB-backed daemon settings** — `daemon/services/settings_service.py`: resolution ladder
**DB > env > yaml > default** (`:7-11`); **add-a-key = one registry entry** (`:48-50`, the registry
dict at `:241`); `ManagedKeySpec.value_type` vocabulary is **int | float | bool | str** only
(`:147`); the model is `daemon/models/app_settings.py`; the routes are
`GET /api/settings/daemon` (`daemon/routers/settings.py:943-946`, gated `settings:read`) and
`PUT /api/settings/daemon/{key}` (`:966-969`, gated `settings:write`); the Decision-Module kill
switches are the live DB-backed precedent (`:645-699`).

**(c) Adapter source-config operator scoping** — `KEY_ALLOWED_GUILD_IDS` /
`KEY_ALLOWED_CHANNELS` (`daemon/sources/adapters/discord/adapter.py:163-164`), per-source JSON,
operator-only.

**⚠ The decisive fact the explorers did not surface (re-verified by the plan writer):**
**the DB settings tier is AGENT-WRITABLE.** `daemon/tools/settings_tools.py:483` (`settings_set`)
writes any registered managed key through the audited path, the category is `settings`
(`:431,:481,:549`), and **`agents/ari/meta.json:37` and `agents/leader/meta.json:15` both allow
that category**. A designation map hosted as a managed key would therefore be settable **by the
very agent that mints tokens**. See `decisions.md` **D-3**.

**(d) Fail-loud house style** — typed refusal (`daemon/routers/mcp_servers.py:1139`); "fails
CLOSED" (`daemon/tools/upgrade_journal.py`); an indeterminate knob fails closed
(`daemon/tools/prompt_assent.py:394-401`).

---

## Part B — Lore / staging (re-verified)

| Fact | Anchor |
|---|---|
| The parent set's files | `.agents/shared/planning/prompt-assent/` — `plan-overview.md`, `phase1-4-plan.md`, `phase4-net-new-diff.md`, `decisions.md`, `doctrine.md`, `risks.md`, `ceremony-notes.md`, `architecture-recommendation.md`, `technical-analysis.md`, `research/` |
| The parent's status legend (`BLESSED` / `DECIDED` / `RECOMMENDED-PENDING-ARCHITECT` / `OPEN-JUDGEMENT` / `RULED`) and the "⚠ AMENDED" blockquote convention | `.agents/shared/planning/prompt-assent/decisions.md:13-21` |
| The **doctrine format precedent** — a verbatim ruling + a "carried verbatim" status row | `.agents/shared/planning/prompt-assent/doctrine.md:5,:13` |
| `risks.md` is the **only** risks file in the whole planning tree | `.agents/shared/planning/prompt-assent/risks.md` |
| `architecture-recommendation.md` **wins on conflict** (header rule) | `.agents/shared/planning/prompt-assent/architecture-recommendation.md` (header) |
| The K-3 gate as written — arm ⇒ 1:1-DM mint ⇒ group-DM refusal ⇒ (optional) absent-discriminator ⇒ record both | `.agents/shared/planning/prompt-assent/ceremony-notes.md:236-244`; `docs/runbooks/prompt-assent.md:160-185` |
| The arming wording — *"armed by the Emperor, after the K-3 live DM probe — not before"* | `.agents/shared/planning/prompt-assent/ceremony-notes.md:308-309` |
| The owed T3.5 re-stamp (`agents/ari/tools_note.md`, `grep -c memoir_about` = 0 today) — human-session-only, an acceptance **subject**, not a lane deliverable | `.agents/shared/planning/prompt-assent/plan-overview.md:220-225,:86,:232-233` |
| v0.29.18 staged facts | `staged_at 2026-11-11T09:00:12Z` · release dir `/home/nea/agents-ensemble/releases/v0.29.18` · built from `df4a92732` · annotated tag `a4d7233f2 → df4a92732` (pushed by name) · 4-way binary sha `aa78f632c4529f9c883647cae5d4743f3e52199ca6dec39e5bf5abdbcabf3e8e` · migration head `20261110_221500` (128 files) · `rollback_safe=true` |
| **The ceremony notes contain NO designated-channels mention** — this extension is newer than the notes | `.agents/shared/planning/prompt-assent/ceremony-notes.md` (full read) |
| The supersede precedents | v0.28.17 **DISPOSED** (release dir + local tag deleted; remote tag left as audit trail) · v0.28.18 **LEFT STANDING** (ancestor content rides the next version) |

### B-1 — Version state — **re-verified by the plan writer with `git ls-remote`**

```
$ git ls-remote --tags origin 'v0.29.20*'      → (empty)      ← v0.29.20 is FREE on origin
$ git tag --list 'v0.29.20'                     → (empty)      ← v0.29.20 is FREE locally
$ git ls-remote --tags origin 'v0.29.*' | tail → v0.29.18 = a4d7233f2 → df4a92732 (the max at planning time — ⚠ SUPERSEDED 2026-11-11T19:30:42Z: v0.29.19 was staged by a concurrent lane — tag `649a509b1` → `1132fa4cb` = origin/latest tip; the max is now **v0.29.19**; see `decisions.md` **D-10**)
$ git merge-base --is-ancestor v0.29.18^{commit} HEAD   → exit 0   ← v0.29.18 IS an ancestor
$ git rev-parse HEAD → efa8aaae28153b8e9c49b86abe19e92be5458ebe
```

**⇒ the next free version slot is `v0.29.20`, and v0.29.18's content already rides this branch.**

### B-2 — Release dirs on this host (re-verified)

`/home/nea/agents-ensemble/releases/` holds **6** dirs now (`v0.29.7`, `v0.29.13`, `v0.29.15`, `v0.29.17`, `v0.29.18`, `v0.29.19` — the concurrent lane's staging added the sixth; 5 at planning time) — staging v0.29.20 will make **7**; see **D-10**'s retention TRUTH (against a keep-3 policy — over-retention is pre-existing and
machinery-owned). The live release, per the ceremony notes, is **v0.29.15**
(`../prompt-assent/ceremony-notes.md:295-310`); **re-verify it at build time** (it is a build-lane pre-step, not a
planning-lane fact).

---

## Part C — What the two passes did NOT cover (gaps this plan fills)

| Gap | How this plan closes it |
|---|---|
| Neither pass asked **where** the designation map should live — and the DB settings tier turns out to be **agent-writable** | `decisions.md` **D-3** (env, with the agent-writability argument) |
| Neither pass asked whether the **schema** must change | `decisions.md` **D-5** — **no new table, no new column, no new migration**; `mint_is_dm=0` is proof-by-construction |
| Neither pass asked what happens on the **approve** side for a designated channel | `decisions.md` **D-6** — the extension **adds a pass path to the privacy leg at leg 4f**; the approve leg **does** emit the two new codes (architect §5.2); the need because 4d/4e already bind channel + human |
| Neither pass asked about **threads** | `decisions.md` **D-9** — exact channel-id match only; no thread inheritance |
| Neither pass asked about **duplicate JSON keys** (Python's `json.loads` silently keeps the LAST one) | `decisions.md` **D-4** — the parser uses `object_pairs_hook` and **refuses** a duplicate key |
| Neither pass asked whether the map's **validity** should fail at BOOT | `decisions.md` **D-4** — fail loud at **use** time with a typed refusal; a boot abort on a config typo is refused |
| Neither pass asked whether a **wrong user in a designated room** should burn the K-9 budget | `decisions.md` **D-8** — it must NOT (a stranger could DoS the Emperor's minting by burning the relay's hourly budget) |

# Phase 2: Tool surface and registration

> **Binding order.** `architecture-recommendation.md` @ `df8da01a6` **wins on conflict** with this
> document. **Must-apply deltas (transcribed 2026-11-10):**
> - **AR-2 — the action set is the 4-verb `{list, mint, approve, reject}`** (this supersedes the
>   earlier 3-verb `{mint, approve, reject}` reading of the commission's shape).
> - **K-2 — `mint` is GATED** on a live, fresh factor-2 user-origin window; the mint context
>   `(source_id, channel_id, is_dm, user_id, external_user_id)` is derived **daemon-side from the
>   window's message row**, **never** from tool args.
> - **AR-1 / K-8 — the approve branch requires BOTH a channel match AND a daemon-derived
>   `mint_user_id` equality**; refusals `token-channel-mismatch` and `token-approver-mismatch`.
> - **K-9 / D-3b — the hourly mismatch budget is IN-RAM, keyed by relay `instance_id`, and is
>   P2-OWNED** (the tool gate). Its config key `PromptAssentConfig.hourly_mismatch_budget`
>   (default 10) lands with the class in P4.
> - **D-2 — default OFF (fail-closed)**, with **ONE shared default constant** consumed by both
>   this phase's resolver and P4's config class.
> - **Refusal codes to place here** — `invalid-arguments` · `token-content-mismatch` ·
>   `token-channel-mismatch` · `mint-requires-private-channel` · `mint-live-token-exists` ·
>   `extraction-incomplete` · `token-approver-mismatch` · `mismatch-budget-exceeded`.
> - **K-3 — the `is_group_dm` discriminator is P1-owned** (adapter work); P2 **consumes** it and
>   applies the fail-closed rule (absent ⇒ **not private**).

## Objective

Turn the Phase 1 primitives into the **surface** the Emperor actually talks to, and register it
under the house's three-layer access posture:

1. the new tool module `daemon/tools/prompt_assent.py` — a **privileged**, **`ari`-EXACTLY** tool
   with the **4-verb action set `action ∈ {list, mint, approve, reject}`** (AR-2), carrying the
   three-factor gate, the refusal vocabulary, and the content-free INFO line;
2. the **complete registration** — category key `prompt-assent`, tool name `prompt_assent`, the
   `CATEGORY_MODULES` / `DYNAMIC_TOOL_NAMES` / `KNOWN_TOOL_NAMES` /
   `PRIVILEGED_TOOL_CATEGORIES` registry entries, the `agents/ari/meta.json` allow entry, the
   in-body `agent_id == "ari"` gate, and the factory + extend in `daemon/tools/instance.py` —
   placed where the house requires;
3. the **complete pin set**, **derived by grep** (never copied from the census prose, which is
   stale), bumped **in the same commit** as the registration (SAME-PR);
4. the **list read surface** over the Phase 1 primitives — the ari-facing way to see pending
   items.

The tool **exists but is inert**: the kill-switch lands in Phase 4, and Phase 2's switch resolver
is **fail-closed** by design (see Coupling).

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| **2.1** | **Create the tool module** — `daemon/tools/prompt_assent.py` (NEW). Decorator order is **load-bearing**: `@register_tool_category("prompt-assent")` **ABOVE** `@tool` — the strip reads the **wrapped** object (`daemon/tools/_tool_registry.py:776-800`; pin: `tests/unit/test_notify_user_registration.py:108-131`). Tool name `prompt_assent`. **AR-2 — the action set is the 4-verb `{list, mint, approve, reject}`**: `list` = read (any channel, ari-gated); `mint` = mutating, **ledgered in the token table**; `approve`/`reject` = mutating, **ledgered in `prompt_audit_events`**. One category grants all four (ari is the only consumer; split the category only if a second read-only consumer ever appears). Signature: `action: Literal["list","mint","approve","reject"]`, `version_id`, `token`, `user_confirmed`, `reject_reason`, `limit` | P1 (token API, attestation type, `list_pending`, canonical-render helper, `is_group_dm` discriminator) | The module imports; the category decorator is registered **above** `@tool`; the tool is discoverable; the action set is **exactly** the 4 verbs — a 3-verb or 5-verb set is refused by a pin |
| **2.2** | **Implement the three-layer access posture** — (i) privileged category (**never** default-granted, `daemon/tools/_tool_registry.py:191-204`); (ii) `agents/ari/meta.json` allow entry — **ari only**; (iii) in-body gate `PROMPT_ASSENT_AUTHORIZED_AGENT = "ari"` (pattern `daemon/tools/prompt_reload.py:70`) with the **notify-exact generic not-ari envelope** (`daemon/tools/notify_user.py:703-711`). Rationale: *"a LITERAL tool name in an allow list bypasses the category gate, so the category alone cannot spell 'ari only'"* (`daemon/tools/notify_user.py:19-30`) | 2.1 | A non-ari caller gets the generic ERROR envelope with **no refusal code** and **no ledger noise**; the category appears in ari's allow list and in **no other** `agents/*/meta.json` |
| **2.3** | **Implement the `mint` action — GATED (K-2)** — `mint` requires a **live, fresh factor-2 user-origin window**: the same checks as approve (window present → `no-user-origin-turn`; source classified user-origin by the UNFORKED `classify_user_origin` → `no-user-origin-turn`; unexpired → `user-origin-window-expired`; **fresh** — `stamped_at` within the configured bound, default 600 s → `user-origin-window-stale`). The **mint context** `(source_id, channel_id, is_dm, user_id, external_user_id)` is derived **from the window's message row** (`manager.py:6126-6133` stamp → the row the `message_id` selects → nested `metadata["discord"]`) — **NEVER from tool args**. Then the **mint branch**: **M-4a** the row exists, is CORE class, and is `pending_approval` (`item-not-found` / `item-not-assentable` / `item-not-pending`); **M-4b** no live token exists unless explicitly forced (`mint-live-token-exists`); **M-4c** the **private check** on the window-derived context — `is_dm AND NOT is_group_dm`, with an **absent discriminator ⇒ NOT private** (fail-closed) → `mint-requires-private-channel`; **M-5** the mint context is captured; **M-6** **own transaction** — the token INSERT (`token_hash`, **`render_sha256`**, mint-context, TTL) + the supersede flip of any prior live token. **NO audit event** — the token table **is** the mint ledger. The response carries **item identity + the canonical diff + the grouped token + the expiry + `render_sha256`**. **A re-mint must re-show the diff** — a bare token tells the Emperor nothing | 2.1, 2.4, P1 (token API, canonical-render helper, `is_group_dm` discriminator) | The mint response contains all elements **including `render_sha256`**; a mint **without** a live fresh window writes **NO** token row and does **not** supersede a prior live token; a mint in a **group DM** or a non-private channel is `mint-requires-private-channel` and writes no token row; the mint context comes from the **window's message row**, never from tool args (grep-proven); a re-mint **supersedes** the prior token (the prior one reads `token-superseded`, **not** `token-already-used`); minting for a non-PENDING row is `item-not-pending` |
| **2.4** | **Implement the `approve` / `reject` actions — and OWN the three-factor gate** — **O-E: the three-factor gate has exactly ONE implementation owner: this file, `daemon/tools/prompt_assent.py`.** (The prior plan claimed it in both P2 2.4 and P3 3.4–3.6; P3 now owns the **daemon-derived identity extraction, the attestation assembly, the service-level predicates, and the gate's acceptance tests** — it does **not** re-implement the gate.) The gate, in the architect's fixed order — **ari layer-3 → kill-switch → action-shape validation → factor gates → action branch**: **(i)** `user_confirmed == true` → `user-confirmation-missing`; **(ii)** a live user-origin window for the calling instance, read from `manager._user_origin_windows[current_instance_id]` — the **same object** the promote gate reads (`daemon/tools/upgrade_tools.py:3388-3391`); the window's source must classify as user-origin via the **UNFORKED** `classify_user_origin` (`daemon/tools/upgrade_journal.py:4116-4166`) → `no-user-origin-turn` (naming the observed source + classifier detail); unexpired → `user-origin-window-expired`; **fresh** (`stamped_at` within the configured bound) → `user-origin-window-stale`; **(iii)** the token present in the **content of the window's exact message**, read from `manager._queue_repository.get(message_id).content` (`daemon/tools/upgrade_tools.py:3508-3531`) — an unreadable row is `assent-verification-unavailable` (**fail closed**). **The approve branch adds AR-1's two equality legs** — **channel match** (the approving turn's daemon-derived `(source_id, channel_id)` == the mint's) → `token-channel-mismatch`; and **user equality** (the approving turn's daemon-derived `user_id` == the token row's `mint_user_id`) → `token-approver-mismatch`; plus the **private check** on the approving context (same rule as M-4c) → `token-channel-mismatch`. **All failures are ENUMERATED in ONE refusal** (no partial proceed). Then call the Phase 3 service path (in Phase 2 this call is stubbed behind the same signature) | 2.1, 2.3, P1 (token API, attestation type) | A call missing any factor is refused with its typed code and **changes nothing**; a factor-3 read failure is `assent-verification-unavailable` (**fail closed**); the gate lives in **exactly one file** (grep-proven — a second implementation site is a pin failure); a token supplied **only** in the tool arguments is refused; a channel or user mismatch is refused with its own distinct code |
| **2.5** | **Implement the refusal taxonomy + envelopes** — house `REFUSED` / `FAILED` envelopes
(`daemon/tools/notify_user.py:314-321`) and the **promote-lane refusal vocabulary** as the model
(`daemon/tools/upgrade_tools.py:3892-3904`). **Complete code list (architect §3)** — `(not-ari)`
(generic envelope) · `lane-disabled` · **`invalid-arguments`** · `user-confirmation-missing` ·
`no-user-origin-turn` (must **name** the observed source + classifier detail — the W1 hardening,
`daemon/tools/upgrade_tools.py:3393-3419`) · `user-origin-window-expired` ·
`user-origin-window-stale` · `assent-verification-unavailable` · `token-mismatch` ·
`token-expired` · `token-already-used` · `token-superseded` · `token-item-mismatch` ·
**`token-content-mismatch`** · **`token-channel-mismatch`** · **`token-approver-mismatch`** ·
**`mint-requires-private-channel`** (mint-side non-1:1-DM, incl. group DMs and the
absent-discriminator fail-closed rule) · **`mint-live-token-exists`** (unless forced) ·
`token-locked` · **`mismatch-budget-exceeded`** · `item-not-found` · `item-not-assentable` ·
`item-not-pending` (carries the current state) · `already-decided` · `self-approval-attempt`
(belt-and-braces — **documented as not-the-gate**) · **`extraction-incomplete`** (fail-closed on
missing nested identity) · `audit-unavailable`. **⚠ The bolded codes are the ones this phase
PLACES** (per the amendment): `invalid-arguments`, `token-content-mismatch`,
`token-channel-mismatch`, `mint-requires-private-channel`, `mint-live-token-exists`,
`extraction-incomplete`, `token-approver-mismatch`, `mismatch-budget-exceeded` | 2.1 | Every refusal is a **typed code** in a house envelope; **never auto-retry a refusal — relay it verbatim** (`daemon/tools/upgrade_tools.py:3906-3907`); a pin asserts the code set matches the architect's §3 taxonomy **exactly** (no missing code, no extra code) |
| **2.6** | **Implement the content-free INFO line** — **ONE** frozen INFO line on every terminal path: counts and flags only, **never persona or diff content** (`daemon/tools/prompt_reload.py:113-135`; the "logs content-free v1" doctrine). The pending list is **cross-agent** (every CORE pending row, including other agents' drafts) — the log must not leak it | 2.1 | The INFO line contains no persona content, no diff, no token, and no pending-item text |
| **2.7** | **Implement the `list` read** — over `repo.list_pending()` / `svc.list_pending()` (Phase 1, task 1.9). **Side-effect free**: listing does **not** mint. Bounded (e.g. limit 50). **No agent filter** — the relay lane must be able to approve any agent's row | 2.1 | The list returns the CORE pending rows (id, agent, artifact, version, submitted-at) and **mints nothing**; the list body goes to the Emperor's chat, **not** into the daemon log |
| **2.8** | **Registry wiring** — `daemon/tools/_tool_registry.py`: `CATEGORY_MODULES["prompt-assent"] = "daemon.tools.prompt_assent"` (the value **MUST** be `daemon.tools.*` — the AST scanner resolves `daemon/tools/<parts[2:]>.py`; a `daemon.services.*` value is never discovered and the drift pin fails at boot, `:1239-1246`); add `"prompt_assent"` to `DYNAMIC_TOOL_NAMES` (`:23`); add it to `KNOWN_TOOL_NAMES` (`:1585-1590` region — drift equality is pinned by `test_known_tool_names_matches_source_exactly_no_drift`); add `"prompt-assent"` to `PRIVILEGED_TOOL_CATEGORIES` as **entry #21** (the set is `:418-773`; `prompt-reload` at `:751-772` is the style template — including its rationale prose) | 2.1 | The registry drift pin is green; the category is privileged and **not** in the default-grant universe |
| **2.9** | **Factory + allow entry + metrics vocabulary** — `daemon/tools/instance.py`: a `create_prompt_assent_tools_if_available(...)` factory (pattern `:2896-2927` — **always constructs**; the tool refuses cleanly when a handle is missing) and the **extend inside `create_instance_tools`** (`:6810-6814` / `:7013` pattern). **Placement is load-bearing: it MUST precede `create_help_tool` / `scan_tools_for_full_docs` / `_apply_tool_filter`** (`:6791-6802`) — *"a later extend is either invisible to the help tool or BYPASSES the privileged strip"*. `agents/ari/meta.json`: append `"prompt-assent"` at the **TAIL** of `tools.allow` (the tail today is `"prompt-reload"`; the array-ORDER pin is `tests/unit/test_board_registration.py:295-306`). `daemon/checkpoint_metrics.py`: bump `VOCABULARY_VERSION 8 → 9` (`:263`) **and** regenerate the frozen artifact label set **in the SAME change** (`:244-263`) — the **F-1 trap class** (*"the artifact and this constant move in the SAME change"*) | 2.8 | The tool is present in ari's tool list **and** in the help snapshot; the ari allow array keeps its tail-order; the metrics vocabulary and the artifact label set move **together** |
| **2.10** | **Bump the SAME-PR pin set, derived BY GREP, in the SAME commit** — the full pin-set derivation is the section *The pin set — DERIVE BY GREP (H-10)* below. Add the two NEW pin files (`tests/unit/tools/test_prompt_assent_registration.py` and `tests/unit/test_prompt_assent_lane.py`) and bump every existing count pin `20 → 21` (including the memoir **literal-text** pin `tests/unit/test_memoir_registration.py:374`), and **correct the stale census prose** at `daemon/tools/_tool_registry.py:379-406` (it lists 14 pin files; grep derives 15) | 2.8, 2.9 | The three canonical greps return **only** the intended hits; every count assertion reads **21**; the census prose matches reality; the set is **never red between two commits** (SAME-PR) |
| **2.11** | **Implement the K-9 hourly mismatch budget — IN-RAM, keyed by relay `instance_id`** (D-3b). An **in-process** counter in the tool module, read/incremented on **every token-mismatch refusal**; exhaustion ⇒ typed refusal **`mismatch-budget-exceeded`**. **Reset semantics (O-F):** the counter is keyed by `(relay_instance_id, hour-bucket)`; a **successful approval does NOT reset it** (only mismatches count, and only mismatches are counted); it resets when the **hour bucket rolls over**, and it is cleared by a **daemon restart**. Configurable via `PromptAssentConfig.hourly_mismatch_budget` (default **10**); until P4 lands that class, this phase's defensive resolver **defaults it to 10** — the same fail-closed resolver pattern as the kill-switch. **In-RAM is the ruled medium deliberately:** it is per-process defence-in-depth, **not a trust decision** — a restart clearing the budget bounds an attacker who cannot restart the daemon, and the **durable** leg is the PG per-token `attempts` + lockout (D-4, P1-owned). **⚠ Do NOT build this in P1** — P1 owns only the durable per-token counter | 2.4, 2.5 | The 11th mismatch within an hour by one relay instance is refused `mismatch-budget-exceeded`; a **different** relay instance has its **own** budget; the budget survives across tool calls; the default is **10** from a single source of truth; the counter is **not** persisted to PG |

### File-level task map

| File | Change | Task |
|---|---|---|
| `daemon/tools/prompt_assent.py` | NEW | 2.1–2.7 |
| `daemon/tools/_tool_registry.py` | 4 registry entries + the **corrected** census prose | 2.8, 2.10 |
| `daemon/tools/instance.py` | factory + extend (placement-critical) | 2.9 |
| `agents/ari/meta.json` | allow entry at the **tail** | 2.9 |
| `daemon/checkpoint_metrics.py` | vocabulary bump + artifact label set | 2.9 |
| `tests/unit/tools/test_prompt_assent_registration.py` | NEW — the ari-EXACTLY proof | 2.10 |
| `tests/unit/test_prompt_assent_lane.py` | NEW — the matrix (P3 owns its content; P2 owns its registration half) | 2.10 |
| the grep-derived pin files | count `20 → 21` | 2.10 |
| `daemon/tools/prompt_assent.py` | **the K-9 in-RAM hourly mismatch budget** (keyed by relay `instance_id`) | 2.11 |

## The pin set — DERIVE BY GREP (H-10)

**The census prose at `daemon/tools/_tool_registry.py:379-406` is STALE.** It enumerates 14 pin
files; grep finds a **15th** — `tests/unit/tools/test_prompt_reload_pins.py:71`
(`assert len(PRIVILEGED_TOOL_CATEGORIES) == 20`) — which the prose does not list. The prompt-reload
lane added a pin file and did not update the census. **Do not trust the comment.**

**The three canonical greps** (from the registry prose itself,
`daemon/tools/_tool_registry.py:412-417`):

```
grep -rn "PRIVILEGED_TOOL_CATEGORIES == frozenset" tests/ test/ --include=*.py
grep -rn "PRIVILEGED_TOOL_CATEGORIES == EXPECTED_PRIVILEGED_SET" tests/ test/
grep -rn "len(PRIVILEGED_TOOL_CATEGORIES)" tests/ test/
```

**Also grep for (not part of the three):** the ari allow-array literal, the ari grant-array
literal, and the metrics-family module list — then re-run the greps after each edit until they are
clean.

**Known sites (verified by the technical-analysis lane's grep — re-derive, do not copy this
list):** the exact-set literals in `tests/integration/test_service_tool_flag_off_byte_identical.py`
· `tests/integration/test_maintenancer_spawn_resolves_tools.py` ·
`tests/unit/tools/test_secrets_registration.py` · `tests/unit/tools/test_upgrade_registration.py` ·
`tests/unit/tools/test_attestation_registration.py`; the `EXPECTED_PRIVILEGED_SET` comparisons in
`tests/unit/test_board_registration.py` · `test_wedge_capture_registration.py` ·
`test_self_alarm_registration.py` · `test_notify_user_registration.py` ·
`test_metrics_read_registration.py` · `test_instance_ops_registration.py`; the count assertions in
`test_board_registration.py` · `test_wedge_capture_registration.py` ·
`test_self_alarm_registration.py` · `test_notify_user_registration.py` ·
`test_memoir_registration.py` (**plus its literal-string meta-pins at `:374-375`, `:415`, `:419`
— these assert the *text* `"assert len(PRIVILEGED_TOOL_CATEGORIES) == 20"` and must become
`21`; the `== 19` / `== 17` negative assertions reviewed**) · `test_metrics_read_registration.py` ·
`test_anomaly_report_tool_registration.py` · `test_instance_ops_registration.py` ·
`tests/unit/tools/test_prompt_reload_pins.py` · `tests/unit/tools/test_nest_report_read_registration.py`.

**The mutation pack.** `test/packs/metrics_read_mutation_matrix_test.py:101-103` derives the count
at pack-import time (`_COUNT_FIND = f"assert len(PRIVILEGED_TOOL_CATEGORIES) == {len(_PRIV_COUNT_NOW)}"`)
— it **auto-adapts**, but the new category may need adding as a **mutation target** (a
category with no mutation target is not covered by the matrix).

## Coupling

- **Tight with:** Phase 1 — the tool consumes the token API, the attestation type, the
  `list_pending` primitives, the **one shared canonical-render helper** (task 1.10 — the mint path
  renders through it and the REST diff route imports it), the **adapter `is_group_dm`
  discriminator** (task 1.12 — P2 **consumes** it and applies the fail-closed rule), and
  `repo.get_effective` for the effective side of the render.
- **Tight with:** Phase 3 — the tool's `approve`/`reject` actions call the **service** acceptance
  path. In Phase 2 this call is wired behind the Phase 3 signature but must not be a bypass of
  the service (the F2 lesson: never raw `svc.atomic_transition`, `daemon/routers/prompts.py:747-753`).
- **Loose with:** Phase 4 — the **kill-switch config class** and the
  `hourly_mismatch_budget` config key land in Phase 4; see below.

**Kill-switch ordering — ONE shared fail-closed default constant (D-2, architect §2.6).** Phase 2's
tool must read the switch **at call time** while the `PromptAssentConfig` class lands in Phase 4.
**The architect OVERRULED the prior split** (P2 defensive resolver "off" / P4 class "on") — that
split is a defect under either default. **Resolution:** there is **ONE fail-closed OFF default
constant**, consumed by **both** this phase's defensive resolver and P4's config class, so the
two can never disagree. **The default is OFF** (`ENSEMBLE_PROMPT_ASSENT_ENABLED` unset or
invalid ⇒ the lane is off). Phase 2 is therefore safe to land on its own, and Phase 4's class is
what arms the lane. **Additional D-2 semantics:** the switch is read at **call time**; the tool
**stays registered** when off (refusal `lane-disabled`, never construction-absence); **`mint` is
refused too** when off; the switch sits **behind** the ari gate; **indeterminate/invalid values
fail CLOSED**. The same shared-constant pattern applies to
`PromptAssentConfig.hourly_mismatch_budget` (default **10**, task 2.11) — this phase's resolver
defaults it to 10 until P4's class exists.

**Gate ownership (O-E).** The **three-factor gate has exactly one implementation owner: this
phase's `daemon/tools/prompt_assent.py`** (task 2.4). Phase 3 owns the daemon-derived identity
extraction, the attestation assembly, the service-level predicates, and the gate's **acceptance
tests** — it does **not** re-implement the gate. A grep pin asserts a single implementation site.

**Shared-context ownership.** This phase owns the **C-3** (tool surface) context and the
registration half of **C-5** (tests). See `plan-overview.md` → Shared-context grouping.

## Risks

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| **The pin set is copied from the stale census prose** ⇒ a missed pin file ⇒ a red tree | High | High | **H-10** — derive by grep, and **fix the prose in this change** (it is the artifact that gets copied forward). The prose's own three greps are the canonical source |
| **The extend lands in the wrong place** in `create_instance_tools` ⇒ the tool is invisible to the help tool, **or bypasses the privileged strip** | High | Medium | Placement is stated in task 2.9 with the exact constraint (`:6791-6802`); add a registration test that asserts the tool is present in ari's tool list |
| **The decorator order is inverted** ⇒ the strip reads the wrong object ⇒ the category is never enforced | High | Medium | `@register_tool_category` **above** `@tool` — the pin to copy is `tests/unit/test_notify_user_registration.py:108-131`; add the equivalent assertion in the new registration test |
| **The category is granted to a second agent** (a silent fail-OPEN regression) | High | Medium | The **ari-ONLY census** — the category must appear in `agents/ari/meta.json` and **nowhere else** across every `agents/*/meta.json` (`tests/unit/test_notify_user_registration.py:23-25`); copy that assertion into the new test |
| **`CATEGORY_MODULES` points at a non-`daemon.tools.*` path** ⇒ the AST scanner never discovers it ⇒ the drift pin fails at boot | High | Low | The value **must** be `daemon.tools.prompt_assent` (`:1239-1246`); the drift pin catches it |
| **The metrics vocabulary is bumped without regenerating the artifact label set** (the F-1 trap class) | Medium | Medium | Both move **in the same change** (`daemon/checkpoint_metrics.py:244-263`); the change is one commit and one review |
| **The pending list leaks other agents' drafts into the daemon log** | Medium | Medium | The INFO line stays **content-free** (task 2.6); the list body goes only to the Emperor's chat |
| **The tool's own output echoes the raw token into a shared surface** | Medium | Low | The raw token appears only in the **mint response** to the Emperor; the INFO line and every other envelope are content-free |
| **The mint gate is not implemented** (K-2) ⇒ a prompt-injected ari mints for any CORE pending row, renders diff+token into any reachable surface, and supersedes the Emperor's live token (DoS + authority-fabrication primitive) | **High** | Medium | Task 2.3's mint branch is **gated** on a live fresh factor-2 window and the context is derived from the **window's message row**; the "mint without a user-origin window" matrix cell (P3) pins it; a mint without a window writes **NO** row and does **not** supersede a prior live token |
| **A group DM passes the private check** (K-3) ⇒ a second occupant of the bound channel satisfies all three factors plus a channel-only check | **High** | Medium | P1's `is_group_dm` discriminator + P2's `is_dm AND NOT is_group_dm` check; **absent ⇒ not private** (fail-closed); the "group-DM mint" matrix cell pins it |
| **The K-9 budget is placed in the wrong phase** (it was in P1's risk table before the ruling) | Medium | Medium | The budget is **P2-owned** (task 2.11) and is explicitly **excluded** from P1; the P1 risk table now redirects to it |
| **The kill-switch default disagrees between P2's resolver and P4's class** (the prior "resolver off / class on" split) | **High** | Medium | **ONE shared fail-closed OFF default constant** consumed by both; a pin asserts both read the same constant |

## Test plan (house conventions)

**Env hygiene (H-7).** Scrub per-variable: `env -u POSTGRES_DB -u POSTGRES_USER -u
POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT <cmd>` — `env -u POSTGRES_*` is a **silent
NO-OP** (bash does not glob env names).

**Runner (H-8).** The **worktree's own venv** (never the main checkout's `.venv` — its editable
`.pth` pins the main tree); `cd` into the worktree first; `testpaths=["tests"]`; the four
deselected dirs; `timeout=30` + `thread`; `-p no:timeout` where needed; explicit `tests/` lists;
`tee LOG >/dev/null` + `tail -2 LOG` for `pytest -q` (tracebacks buffer to session end).

**Net-new = FAILED-list diff (H-6)** against the TRUE pre-fix base, same commands.

**Tests to write in this phase.**

| Test file | Covers |
|---|---|
| `tests/unit/tools/test_prompt_assent_registration.py` (NEW) | the three-layer posture: privileged ✔, present in `agents/ari/meta.json` **and nowhere else** ✔, in-body `agent_id` gate ✔; the decorator-order assertion (copied from `test_notify_user_registration.py:108-131`); the registry entries present; `KNOWN_TOOL_NAMES` drift equality |
| `tests/unit/test_prompt_assent_tool_refusals.py` (NEW) | the refusal vocabulary: each code is produced by its trigger; the not-ari envelope carries **no** refusal code; the INFO line is content-free |
| `tests/unit/test_prompt_assent_list_surface.py` (NEW) | `action="list"` returns the pending rows and **mints nothing**; the list is not agent-filtered; it is bounded |
| the grep-derived pin files | count `20 → 21`; the meta-pins' literal strings updated |

**Non-vacuity.** Every new assertion needs a **counter-mutation** that reddens it. The ari-ONLY
census and the decorator-order pin are the classic prose-satisfiable traps — prove them by
mutation, not by inspection.

**Mutation pack.** Add the new category as a mutation target in
`test/packs/metrics_read_mutation_matrix_test.py`.

## Exit Criterion

**All of the following hold:**

1. `prompt_assent` is registered, **privileged**, and reachable **only** by ari; a non-ari caller
   receives the generic envelope.
2. The **ari-ONLY census** passes; the allow array's tail order holds; the decorator order holds.
3. The **grep-derived** pin set is complete — the three canonical greps return **only** the
   intended hits, and every count assertion reads **21**. The census prose at
   `daemon/tools/_tool_registry.py:379-406` has been **corrected** to match reality.
4. `action="mint"` returns item identity + diff + token + expiry **+ `render_sha256`**;
   `action="list"` is side-effect-free; the INFO line is content-free; the action set is **exactly**
   the 4 verbs `{list, mint, approve, reject}`.
5. **The mint gate holds** — a mint without a live fresh factor-2 window, or in a **group DM** /
   non-private channel, writes **NO** token row and refuses with its own typed code.
6. **The K-9 budget holds** — the 11th mismatch within an hour by one relay instance is refused
   `mismatch-budget-exceeded`; the budget is **in-RAM** and **not** persisted.
7. **The gate has exactly one implementation site** (O-E — grep-proven).
8. The **net-new failed-list is empty** against the TRUE pre-fix base, and the mutation pack
   covers the new category.

Only then may Phase 3 begin.

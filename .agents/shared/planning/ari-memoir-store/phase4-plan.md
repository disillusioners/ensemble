# Phase 4 — Tool category `memoir` + 5 tools + gates + pin ceremony

**Depends on:** P2, P3. **Flags consumed:** #12 (census/ceremony), #13 (knob enforcement), #2 (attribution), #1 (admission at the tool layer), #5 (schema bounds), #11 (budget enforcement in-body).

## 1. Objective

The privileged category `memoir` (#18, ARI-EXACTLY): `daemon/tools/memoir.py` with the five blessed tools behind the two-layer gate and the kill-switch; the three-step registration seam completed (decorator-only is silently invisible); the ari allow entry + doctrine doc; the ONE-commit pin ceremony with prompt-artifact re-stamp.

## 2. Decisions

| ID | Decision | Rationale (verified anchors) | Rejected alternatives |
|----|----------|------------------------------|------------------------|
| D4.1 | Category key EXACTLY `memoir`; `MEMOIR_CATEGORY_KEY = "memoir"` module constant | design §5; the notify_user N7 exact-key discipline (key constant at `:173` — verified) | "memoir-store"/"ari-memoir" (never invent keys) |
| D4.2 | Factory `create_memoir_tools(manager, current_instance_id, agent_id, agent_tag)` → 5 tools; EVERY body: (1) kill-switch FIRST → typed refusal `memoir_disabled` (notify_user `notify_disabled` shape :725-731 — verified); (2) `_is_memoir_authorized(agent_id)` — fail-closed on blank/non-str, admits exactly `ari` (the `_is_metrics_authorized` shape at `daemon/tools/metrics_read.py:403-414` — verified); identity from the factory-captured `agent_id` ONLY (DD-13/L3) | two-layer gate is design §5.2; the in-body layer is "the only layer prompt content cannot bend" | caller-supplied agent/actor params for identity (the L3 anti-pattern); category-gate-only (a literal tool name in an allow list bypasses it) |
| D4.3 | Decorator order `@register_tool_category(MEMOIR_CATEGORY_KEY)` ABOVE `@tool` on every tool (load-bearing, pinned) | notify-registration pin leg (j); metrics_read :1396/:1484/:1625 — verified | reversed order (registration silently lost) |
| D4.4 | Registration: (1) decorator on each factory tool; (2) `CATEGORY_MODULES["memoir"] = "daemon.tools.memoir"` (beside `"notify-user"` at `_tool_registry.py:1112`, `"metrics-read"` at `:1125` — verified); (3) `PRIVILEGED_TOOL_CATEGORIES` += `"memoir"` (entry #18; set at `:367-651`, currently 17 — verified); (4) the 5 names in `DYNAMIC_TOOL_NAMES` + `KNOWN_TOOL_NAMES`; (5) `instance.py` import beside `:291`/`:292` + `tools.extend(create_memoir_tools(...))` placed BEFORE `create_help_tool`/`scan_tools_for_full_docs`/`_apply_tool_filter` (the placement rule comment block at `:6600-6618` — verified; the build-call shape mirrors `create_metrics_read_tools` at `:6657`) | the C5 three-step seam, all five sites grep-verified | decorator-only (invisible); late extend (bypasses the privileged strip) |
| D4.5 | NO `*_if_available` wrapper: the factory gates on module+manager-handle only; degraded boots leave tools PRESENT and loudly refusing | metrics-read RULING-1 precedent (comment at instance.py:6648-6652 — verified) | silent-vanish wrappers (fail-closed means loud, not absent) |
| D4.6 | Tool bodies (blessed signatures §5.4, enriched by flag dispositions): `memoir_write(text, title?, importance?, event_at?, stars?, keepsake?, actor?, reason?)` — keepsake=true requires reason AND actor (flag #2) and passes D2.8 admission; `memoir_pin(id, reason, actor)` / `memoir_unpin(id, actor)` — actor Literal("empress","emperor") REQUIRED (flag #2), cap admission in pin; `memoir_recall(query, k?)` — D2.7 budget enforced in-body with final re-check; `memoir_timeline(from?, to?, q?, k?)` — D2.6 semantics; all refusals TYPED (code + human line, the notify_user 12-code family) | §5.4 binding signatures + the flag dispositions; the metric-read M-1 in-body-budget doctrine | optional actor with model-guessed default (mis-attribution vector) |
| D4.7 | `agents/ari/meta.json` `tools.allow` += `"memoir"` (implementation happens in THIS lane's Phase C per the commission — planned here, executed with the ceremony); `agents/ari/tools_note.md` gains the memoir doctrine section (tool contracts: actor rule wording from flag #2; recall/timeline contract line from flag #10; the briefing affordance cross-ref) | ari-only census pin requires the allow entry; tools_note is ari's operative doctrine (notify-user doctrine precedent) | deny-listing on other agents (ari-exactly is an allow, not a deny sweep) |
| D4.8 | Pin ceremony in ONE commit (v0.26.2 ruling): bump the SIX `len == 17` pins (board :222, wedge_capture :180, self_alarm :218, notify_user :182, metrics_read :221, instance_ops :209) and FIVE exact-set pins (test_service_tool_flag_off_byte_identical :834, test_maintenancer_spawn_resolves_tools :382, tools/test_secrets_registration :226, tools/test_upgrade_registration :152, tools/test_attestation_registration :192) to the 18-entry set — all sites grep-verified this session, RE-GREP at implementation; add `tests/unit/test_memoir_registration.py` (notify shape (a)–(j), 18-flavored: AST discovery, CATEGORY_MODULES resolution, DYNAMIC/KNOWN names ×5, extend-present + precedes-filter, privileged + exact-18 + len==18, ari-census ONLY-ari, in-body gate source pin, decorator-order pin); re-stamp `agents/.prompt-artifacts.sha256` (161/161 — count unchanged, hashes updated for meta.json+tools_note edits) via the house baseline tool | flag #12; the registry's own instruction: "DERIVE THE PIN SET BY GREPPING THE TREE — do NOT trust this prose" (comment at `_tool_registry.py:340-365` — verified) | bumping pins across multiple commits (the ruling says ONE) |

## 3. Tasks

| ID | Task | Outcome | Touchpoints | Depends | AC |
|----|------|---------|-------------|---------|----|
| T4.1 | Tool module: factory + 5 tools + typed refusal vocabulary + in-body gates + budget enforcement | tools construct; refusal matrix green (knob-off ×5, non-ari ×5, bad-args) | daemon/tools/memoir.py, memoir_knob.py | P2, P3 | AC1 |
| T4.2 | Registration five-site completion (D4.4) | `test_memoir_registration.py` legs (a)–(g), (i), (j) green | _tool_registry.py, daemon/tools/instance.py | T4.1 | AC2 |
| T4.3 | ari allow entry + tools_note doctrine (D4.7) | census leg (h) green: `memoir` in ari meta.json and NOWHERE else | agents/ari/meta.json, agents/ari/tools_note.md | T4.2 | AC3 |
| T4.4 | THE ceremony commit (D4.8): 11 pin bumps + new pin pack + baseline re-stamp, one commit | all 12 registration tests green; baseline 161/161; `grep -rn "len(PRIVILEGED_TOOL_CATEGORIES)" tests/` shows only `== 18` | 11 test files + new pin file + agents/.prompt-artifacts.sha256 | T4.3 | AC4 |
| T4.5 | Tool-behavior packs: write/pin/unpin/recall/timeline end-to-end on temp DB (happy + every refusal + admission + budget + actor-required) | mutation legs: actor-optional → RED; admission bypass → RED; budget re-check removed → RED | tests/unit/memoir/ | T4.1 | AC5 |

## 4. Acceptance criteria

| ID | Criterion |
|----|-----------|
| AC1 | Refusal matrix: knob OFF → `memoir_disabled` on all 5; non-ari agent (incl. blank/malformed id) → agent-gate refusal BEFORE any DB touch (order pinned); malformed args → typed refusals, no partial writes |
| AC2 | Registration: exact-18 set, len==18, CATEGORY_MODULES resolves, 5 names in DYNAMIC+KNOWN, extend precedes filter/help/scan (source-order pin) |
| AC3 | ari-census: `memoir` appears in exactly one agents/*/meta.json (ari); doctrine section present in tools_note |
| AC4 | Ceremony: single commit contains ALL pin bumps + re-stamp; pre-commit hook dry-run shows no `add -A` usage; baseline 161/161 via the house baseline tool |
| AC5 | Behaviors: keepsake-at-write enforces reason+actor+admission; pin cap rejections name pins; recall budget footer exact; timeline neutral (no stats writes — assert) |

## 5. Test packs

- `tests/unit/test_memoir_registration.py` (the pin pack, (a)–(j) legs).
- `test/packs/memoir_tools_unit_test.sh` — AC1/AC5 + mutation matrix.
- Ceremony self-verification: `grep -rn "PRIVILEGED_TOOL_CATEGORIES == frozenset" tests/` and `grep -rn "len(PRIVILEGED_TOOL_CATEGORIES)" tests/` — every hit 18-flavored (recorded in the commit message).

## 6. Flag consumption

#12 (D4.8), #13 (D4.2 ordering + D4.5), #2 (D4.6 actor), #1 (admission surfaced at tool layer), #5 (D4.6 bounds), #11 (D4.6 recall body). Consumes P2's service and P3's worker contract.

# Phase 2: Allow-list + pin census + registration pins

## Objective

The 4 grants land in `meta.json`; every pin in the privileged-set census is bumped 21→22 in the SAME commit as Phase 1's registry entry; a dedicated `tests/unit/tools/test_guidelines_registration.py` pins the registration + the 4-agent allow census + the denials.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | The 4 meta.json diffs (exact, research-verified): `agents/ari/meta.json` — insert `"guidelines"` after L48 `"prompt-assent"`, before L49 `]` (one-per-line tail style preserved); `agents/leader/meta.json` L15 — tail `..., "pok", "nest-report"]` → `..., "pok", "nest-report", "guidelines"]`; `agents/devops/meta.json` L12 — same tail pattern; `agents/sonic-leader/meta.json` L15 — `"pok", "nest-report"],` → `"pok", "nest-report", "guidelines"],` (deny list L16 untouched). NO other agent's meta.json gains the token | P1 all | `grep -l '"guidelines"' agents/*/meta.json` returns EXACTLY the 4 files; JSON parses; ari array order pin (T5) green |
| 2 | `EXPECTED_PRIVILEGED_SET` definitions ×6 — append `"guidelines"` to the literal: `tests/unit/test_board_registration.py:70`, `test_instance_ops_registration.py:88`, `test_metrics_read_registration.py:92`, `test_notify_user_registration.py:56`, `test_self_alarm_registration.py:76`, `test_wedge_capture_registration.py:72` | P1 T3 | All 6 comparison legs green |
| 3 | Inline frozenset pins ×5 — append to the `== frozenset({...})` literals: `tests/unit/tools/test_upgrade_registration.py:152`, `test_attestation_registration.py:195`, `test_secrets_registration.py:228`, `tests/integration/test_maintenancer_spawn_resolves_tools.py:412` (+ its comment :474-479), `tests/integration/test_service_tool_flag_off_byte_identical.py:842` (+ comment :911-916; ALSO correct the stale docstring census at :819 — "ELEVEN sites" prose — same-commit duty) | P1 T3 | 5 pins green; docstring no longer stale |
| 4 | Count pins `== 21` → `== 22` — the grep-derived inventory at this base: `test_anomaly_report_tool_registration.py:383` · `test_board_registration.py:235` · `test_instance_ops_registration.py:222` · `test_memoir_registration.py:149` · `test_metrics_read_registration.py:235` · `test_notify_user_registration.py:196` · `test_self_alarm_registration.py:231` · `test_wedge_capture_registration.py:193` · `tests/unit/tools/test_nest_report_read_registration.py:175` · `test_prompt_assent_registration.py:218` + `:260` · `test_prompt_reload_pins.py:71` — **AND the memoir derivation self-pin** `test_memoir_registration.py:377-384`: update the sought literal (`== 21` → `== 22`), update the stale-flavor negative (`"== 20" not in source` gains an `== 21` stale-flavor companion), and add the NEW pin file to its file census (T6). **Re-derive by grep at implementation time** (`grep -rn "len(PRIVILEGED_TOOL_CATEGORIES)" tests/ test/`) — the prose census is a starting point, not authority | P1 T3 | Zero `== 21` count pins remain (grep clean); memoir derivation green incl. the new file entry |
| 5 | Order + grant + aegis pins: `tests/unit/test_board_registration.py:310` — ari allow-array ORDER pin literal gains trailing `"guidelines"` (ari is the only order-pinned array); `tests/unit/test_anomaly_report_grant_pins.py:99` (privileged-set context list) + `:317` (ari allow string literal); `tests/unit/agents/test_aegis_security_family.py:171` — VERIFY pin shape first: if it asserts exact equality/coverage, add `"guidelines"`; if prose/tolerant, no edit (record the verdict) | 1 | All green; aegis verdict recorded in decisions.md |
| 6 | NEW `tests/unit/tools/test_guidelines_registration.py` — model on `test_metrics_read_registration.py` (multi-agent allow census) with the notify-user legs a–j (`test_notify_user_registration.py`, 260 L, canonical template): (a) AST discovery + provenance; (b) decorator-order pin (`@register_tool_category` above `@tool`, :111-136 style); (c) CATEGORY_MODULES value == `daemon.tools.guidelines`; (d) DYNAMIC+KNOWN contain both names; (e) extend greppable + precedes help/scan/filter (index comparisons, :167-185 style); (f) privileged membership + exact set + count 22; (g) meta.json allow census — `"guidelines"` in EXACTLY the 4 files, absent from every other `agents/*/meta.json`; (h) `GUIDELINES_AUTHORIZED_AGENTS == frozenset({"ari","leader","devops","sonic-leader"})`; (i) gate-order-in-source (auth gate first in tool body); (j) CATEGORY_DOC rendered-doc leg (self_alarm :500 phrase-pin class). Add the new file to the memoir derivation census (T4) | P1 T3 | File green; counted by the memoir derivation; registered in the SAME-PR prose enumeration (P1 T3v) |
| 7 | Census data + mutation target: `.agents/shared/metrics/tool-categories.json:38` — add `"guidelines"` in SORTED position (57-category frozen vocabulary census); `test/packs/metrics_read_mutation_matrix_test.py` — add the new category as a MUTATION TARGET ("a category with no mutation target is not covered by the matrix", `_tool_registry.py:428-429`; MT-01 precedent) | P1 T3 | JSON sorted + valid; mutation pack names the target; counter-mutation legs per test-matrix.md §M |

## Coupling

- **Tight with Phase 1** — one atomic commit (SAME-PR rule). If any pin in this phase is missed, the registry constant and pins disagree → the FIRST test run goes red by design (the pins are the safety net, not bureaucracy).
- **Loose with Phase 4** — the allow census proven here is the evidence base for the ceremony record (first privileged grant to a sonic agent).
- **Independent of Phase 3.**

## Risks

- Stale-census risk: the count-pin inventory above was grep-derived at `5316e0cd3` (13 grep lines: 12 assert sites + 1 memoir derivation string). If the base moved, RE-GREP — a missed site turns red immediately (fail-loud by design).
- memoir derivation is a three-face pin (literal string, stale-flavor negative, file census) — missing any face leaves a half-updated derivation that fails confusingly; T4 spells out all three.
- ari array-order pin means ari's diff is NOT free-form: `"guidelines"` must be the literal LAST element (after `"prompt-assent"`), matching the memoir→prompt-reload→prompt-assent append convention.

## Exit Criterion

Full pin census green at 22; the 4 grants in place and no others; `test_guidelines_registration.py` green including the sonic-leader GRANTED + 4× sonic DENIED legs; `tool-categories.json` updated. The category is now grant-complete.

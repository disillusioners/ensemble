# Test Matrix: guideline-read-tool

Executor: sonic-tester (results → `.agents/tester/RESULTS/<date>-guideline-read-tool.md`; shell packs → `test/packs/guidelines_*.sh`, registered in `.agents/tester/PACKS.md`). Unit legs live in `tests/unit/tools/test_guidelines_registration.py` (Phase 2 T6) plus the parser/behavior legs (Phase 1 T2 acceptance, expanded here).

## A. Registration pins (new pin file — notify legs a–j)

| Leg | Assertion | Counter-mutation that MUST redden it |
|-----|-----------|--------------------------------------|
| a | AST discovery: both tools registered, `_tool_category == "guidelines"`, `_tool_category_first_party` True | remove `@register_tool_category` → red |
| b | Decorator order: `@register_tool_category` ABOVE `@tool` (source-order pin, `test_notify_user_registration.py:111-136` style) | swap the decorators → red (privileged strip would fail open) |
| c | `CATEGORY_MODULES["guidelines"] == "daemon.tools.guidelines"` | point the value at `daemon.services.*` → red (AST scanner miss) |
| d | `DYNAMIC_TOOL_NAMES` + `KNOWN_TOOL_NAMES` contain `guidelines_list` + `guideline_read` | drop one name → red (`test_frozen_tool_name_discovery.py` also reds — bidirectional drift) |
| e | instance.py extend greppable AND precedes `create_help_tool`(:7620)/`scan_tools_for_full_docs`(:7625)/`_apply_tool_filter`(:7628) (index comparisons, :167-185 style) | move the extend below the help construction → red |
| f | Privileged membership + exact set + `len == 22` | remove the frozenset entry / add an unlisted category → red |
| g | meta.json ALLOW-array census: `"guidelines"` in EXACTLY {ari, leader, devops, sonic-leader}'s `tools.allow` arrays; absent from every other agent's ALLOW array (allow-array semantics — NOT a bare file grep: deny arrays are a live pattern, e.g. sonic-leader carries a deny list) | add the token to sonic-coder's ALLOW array → red |
| h | `GUIDELINES_AUTHORIZED_AGENTS == frozenset({"ari","leader","devops","sonic-leader"})` | swap any member → red |
| i | Gate-order-in-source: the auth gate is the FIRST logic in each tool body (metrics ladder :1404-1417 shape) | move a file-IO or formatting step ahead of the gate → red |
| j | CATEGORY_DOC rendered in cold-boot docs (warm-list works; self_alarm :500 phrase-pin class) | delete the loader warm-list entry → red (docs render empty) |

## B. Allow gates (behavior)

| Leg | Agent | Expected |
|-----|-------|----------|
| B1 | sonic-leader | Guidelines tools PRESENT at construction (first privileged grant to a sonic agent — the pin proves it forever) |
| B2 | sonic-coder / sonic-developer / sonic-reviewer / sonic-tester | Tools ABSENT (privileged default-deny strip, `_strip_privileged_category_tools`) |
| B3 | every unlisted agent (spot-set: giter, wanderer, maintainer, explorer) | Tools ABSENT |
| B4 | any holder calling with mismatched/unknown `agent_id` | Typed refusal BEFORE any file IO (auth-first) |
| B5 | `midflight` negative-claim guard | sonic-leader holds `midflight` (NON-privileged) — assert it did NOT confer `guidelines` (innate/expansion leak guard) |

## C. Parser + tools (fail-soft contract)

| Leg | Input | Expected |
|-----|-------|----------|
| C1 | `guidelines_list` with `base_dir` = repo `docs/guidelines` | 5 entries; 3 canonical: slug == filename stem, status == "draft" (cut at first ` · ` U+00B7), non-empty purpose; 2 auxiliary: slug `README`/`TEMPLATE`, fail-soft status/purpose |
| C2 | canonical field details | status line is ONE physical line (L3); purpose = first ¶ after `## 1. Purpose & doctrine` (L7); title = H1 verbatim (== slug for canonical) |
| C3 | synthetic malformed fixtures (in tmp_path via `base_dir` injection): file with no `## 1.`; file with no `**Status:**`; empty file; H1 mismatching stem; a file with `|` in the status line (TEMPLATE-style options — must NOT be treated as a separator) | NEVER raises; auxiliary fallbacks applied ("(no purpose section)" etc.); no invented Feature/Audience |
| C4 | missing/empty `base_dir` | Typed refusal naming the problem — the SAME typed refusal from BOTH `guidelines_list` AND `guideline_read` (adjudication Q1(d)); never raise, never traceback |
| C5 | `guideline_read(slug)` round-trip ×5 | Content == file content (verbatim, byte-faithful text); no truncation |
| C6 | `guideline_read("nonexistent")` | Typed refusal naming ALL 5 valid slugs |
| C7 | write-path absence | Assert the tool surface exposes NO write/mutation (grep the module for no `write`/`unlink`/`rename` on the guidelines dir; category carries no non-read tool) |

## D. Stage seam (hermetic bash — `tests/test_guidelines_payload.sh`)

| Leg | Assertion |
|-----|-----------|
| D1 | Staged payload contains `guidelines/` with exactly the 5 files (flat at release root) |
| D2 | `manifest.json` has `guidelines_tree_sha256` (a guidelines tree sha alongside agents/frontend/plugins/scripts) matching an INDEPENDENT recompute with the EXACT lib.sh ordering (`find . -type f \| sed 's\|^\./\|\|' \| sort` → `shasum -a 256` line-hash, one trailing newline per line) |
| D3 | `guidelines_manifest` per-file map matches per-file recomputed shas (two-space separator); keys TREE-relative |
| D4 | Local re-hash leg green on an unmodified stage |
| D5 | NEGATIVE: corrupt one staged file post-compute → stage refuses the swap-in (exit 1, warning names guidelines) |
| D6 | Precondition guard: missing `docs/guidelines` source dir → exit 78 |
| D7 | Fixture discipline: hermetic mktemp only; live install NEVER touched (executor-family header :22-33 precedent; POK live-shape fence rule) |

## E. Census / docs pins

| Leg | Assertion |
|-----|-----------|
| E1 | Memoir derivation pin green with the new file in its census (three faces: literal, stale-flavor negative, file list) |
| E2 | `.agents/shared/metrics/tool-categories.json` contains `"guidelines"` in sorted position; JSON valid |
| E3 | Mutation pack names the new category as a target; one counter-mutation leg exercised end-to-end |
| E4 | `tool_help` / rendered docs contain BOTH doctrine lines VERBATIM; 4/4 `tools_note.md` files carry the `## Guidelines` section; sha baseline regenerated same-commit (Phase 4 T2) |
| E5 | Boot log zero category warnings (warm-list, view-views precedent) |

## House discipline notes

- Absence-only / raw-text pins are prose-satisfiable — every negative assertion above is paired with a POSITIVE shape (POK `mutation-01`: 'mutation failures: 1' is signal, not flake).
- Scope negative greps to non-comment lines where a mandated doctrine comment could collide (POK `ac-comment-token-grep-collision`): the doctrine lines are POSITIVE pins here; no negative grep targets them.
- Test injection: `base_dir` constructor param > resolver monkeypatch > fake HOME; NEVER live-shaped paths (research §D; POK fence rule).

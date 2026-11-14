# research-registration-pattern.md — the house tool-category registration pattern

**Commission:** guideline-read-tool (read-only privileged category `guidelines`, tools `guidelines_list` + `guideline_read`, allow-list EXACTLY `[ari, leader, devops, sonic-leader]`).
**Evidence base:** worktree `/home/nea/ensemble-src-wt-guideline-read-tool` @ `5316e0cd3` (branch `feature/guideline-read-tool`, base = post-guidelines-landing origin/latest). Read-only; every anchor below verified in THIS tree.
**Reference sibling:** `notify-user` (ari-only, privileged #16) plus the closest allow-shape match `metrics-read` (multi-agent allow-list, read-only, privileged #17).

---

## A. Category registration anatomy (notify-user traced end-to-end)

The registration is a **THREE-STEP SEAM** (decorator + registry entries + construction). The doctrine sentence "decorator-only registration is SILENTLY INVISIBLE" appears in `daemon/tools/instance.py` comments at every extend site. A decorator-only addition raises no error, registers no tool, leaves no clue.

### A(i) `daemon/tools/_tool_registry.py` — the registry (4 edit sites in one file)

1. **`PRIVILEGED_TOOL_CATEGORIES: frozenset[str]`** — `daemon/tools/_tool_registry.py:446`. Ordered set, currently **21 members** (`system_upgrade` … `prompt-assent` #21 at `:822`). A new privileged category appends as entry **#22**. Every entry carries a long house-style comment: why privileged (the "empty-allow inherit universe must never auto-grant it" rule), the allow audience, defence-in-depth reasoning, and the SAME-PR pin duty. The `notify-user` entry is `:681-708`; the `metrics-read` entry (the READ-ONLY multi-agent sibling) is `:709-731`.
2. **The SAME-PR RULE prose block** — `:375-445`. THE canonical pin census: "Adding or removing a category REQUIRES updating ALL of them in the same commit", followed by the enumeration of the (currently) **SIXTEEN pin files** plus the mutation-pack string pair, and the three derivation greps at `:441-443`:
   ```
   grep -rn "PRIVILEGED_TOOL_CATEGORIES == frozenset" tests/ test/ --include=*.py
   grep -rn "PRIVILEGED_TOOL_CATEGORIES == EXPECTED_PRIVILEGED_SET" tests/ test/ --include=*.py
   grep -rn "len(PRIVILEGED_TOOL_CATEGORIES)" tests/ test/ --include=*.py
   ```
   Binding rule (memoir adjudication, restated in the prose): **the census is DERIVED BY GREP at implementation time — never trust any prose census (including this file's)**; a stale prose census is itself a defect corrected in the SAME commit.
3. **`CATEGORY_MODULES: dict[str, str | list[str]]`** — `:1174`. Maps category key → `daemon.tools.*` module path. The `notify-user` entry is `:1296` (`"notify-user": "daemon.tools.notify_user"`). Value MUST be a `daemon.tools.*` path — the AST scanner resolves it as `daemon/tools/<parts[2:]>.py`; a `daemon.services.*` value is never discovered and "the exact-equality drift pin fails at boot". Naming convention comment at `:1292-1295`: category key KEBAB-case (`notify-user`), tool name snake_case (`notify_user`).
4. **`DYNAMIC_TOOL_NAMES`** — `:23`. Factory-built (not import-time registered) tool names. `notify_user` entry `:143-150`. Factory-built names belong HERE **and** in KNOWN_TOOL_NAMES.
5. **`KNOWN_TOOL_NAMES`** — `:1565`. The frozen-binary fallback universe. `notify_user` entry `:1644-1650`. Regenerated via the documented command at `:1553` (`uv run python -c "...discover_source_only_tool_names()..."`); bidirectional drift is caught by `tests/unit/tools/test_frozen_tool_name_discovery.py::test_known_tool_names_matches_source_exactly_no_drift` (`:1560`).
6. **`register_tool_category` decorator** — `:826-851`. Sets `_tool_category` on the function AND `_tool_category_first_party = True` (anti-spoof provenance marker: MCP tools with server-controlled names cannot re-categorize a first-party tool).

**Decorator ORDER is load-bearing:** `@register_tool_category(...)` must sit ABOVE `@tool(...)` (`notify_user.py:503-504`; pinned in `test_notify_user_registration.py:111-136`). Reversed order ⇒ the privileged strip fails open.

### A(ii) `daemon/tools/notify_user.py` — the module shape

- **Module docstring = the doctrine text** (`:1-117`): commission quote, house rules ("Tools NEVER raise — typed envelope"), the THREE-layer access posture (`:19-30`: privileged category → meta.json allow → in-body `agent_id` gate, "a LITERAL tool name in an allow list bypasses the category gate"), steering clauses, rate-gate doctrine, kill-switch/knob doctrine.
- **Module constants** (`:149-180`):
  - `CATEGORY_NAME = "Notify-User"` (`:149`) — display name.
  - `CATEGORY_DOC = """..."""` (`:151-169`) — the docs-registry text (tool inventory + what-it-is/is-not). Read GENERICALLY by the help/loader machinery — no loader change beyond the warm-list (see A(iii)/E).
  - `NOTIFY_CATEGORY_KEY = "notify-user"` (`:173`) — the exact key constant (N7 discipline: never a variant).
  - `NOTIFY_USER_TOOL_NAME = "notify_user"` (`:176`).
  - `NOTIFY_AUTHORIZED_AGENT = "ari"` (`:180`) — the layer-3 gate constant.
  - Frozen typed-refusal code vocabulary (`:183+`, 12 codes).
- **Factory** `create_notify_user_tools(manager, *, current_instance_id="", agent_id="", agent_tag=None) -> list` (`:460-540`): builds a Pydantic `*Input` schema, then the tool:
  - `@register_tool_category(NOTIFY_CATEGORY_KEY)` ABOVE `@tool(args_schema=...)` (`:503-504`);
  - short `@tool` docstring points at `tool_help("notify_user")` (`:518`);
  - `_full_doc_` assigned via `_build_full_doc()` (`:538`, builder `:543+`) — the long help text;
  - never raises — catch-all returns a typed failure envelope (`:527-536`);
  - returns `[notify_user]`.
- **The ordered impl** `_notify_user_impl(...)` (`:686+`): **auth-first** — the `agent_id != NOTIFY_AUTHORIZED_AGENT` gate is `:702-707` (pre-audit; a non-ari caller leaves no ledger noise), kill-switch second (`:715-732`), then pure argument gates, then any DB/ledger touch. (The exemplar's own adjacent comment calls the knob "the outermost gate" — the CODE order is the authority; memoir's AM-16 ruling: auth-first, always.)
- The `*_if_available` wrapper used by instance.py is defined IN instance.py itself, NOT alongside the factory (CORRECTED 2026-11-13 — the wrappers block spans `instance.py:2684-2964`, the notify wrapper at `:2897`; `notify_user.py` defines no wrapper): the instance.py import is `from .notify_user import create_notify_user_tools`; the call site uses `create_notify_user_tools_if_available(...)`.

### A(iii) `daemon/tools/instance.py` — the wiring (2 edit sites in one file)

1. **The import** in the factory-imports block (notify: `from .notify_user import create_notify_user_tools`, pinned greppable at `test_notify_user_registration.py:163`).
2. **The extend** inside `create_instance_tools` — notify block at **`:6848-6871`**: the standard comment block (three-step seam + PLACEMENT warning + privilege rationale + "agent_id is the layer-3 gate's input ... a construction that omitted it would fall back to `""` ⇒ the body gate denies every call (fail-closed, but a dead tool)") followed by:
   ```python
   tools.extend(create_notify_user_tools_if_available(manager, current_instance_id, agent_id, agent_tag=version_tag))
   ```
   **PLACEMENT IS LOAD-BEARING** (stated at `:6855-6859` and identically at every extend): the extend MUST precede `create_help_tool(tools, ...)` (`:7620`), `scan_tools_for_full_docs(tools)` (`:7625`) and `_apply_tool_filter(tools, agent_id, ...)` (`:7628`). A later extend is invisible to the help tool or BYPASSES the privileged strip. The extend-order pin lives in the registration test (`test_notify_user_registration.py:167-185`, index comparisons).
3. **The gate itself** — `_strip_privileged_category_tools` defined at **`:7633-7668`**: filters out any tool whose `_tool_category` ∈ `PRIVILEGED_TOOL_CATEGORIES`; reads the LIVE registry constant (never a hard-coded copy: "a hard-coded copy of the set goes stale on every category addition", `:7646-7653`). Called from `_apply_tool_filter` (`:7671-7760`) on the two default-allow paths: no tools config (`:7697-7701`) and `resolve_tool_filter` returning None (empty allow+deny — the empty-allow inherit universe, `:7736-7737`). `resolve_tool_filter` itself is at `:431` (allow expands categories; deny wins).
4. **meta.json → allow resolution**: `_apply_tool_filter` resolves the agent's `tools.allow` via the registry (`registry.get_version(agent_id, version_tag)` then `registry.get_resolved`, `:7692-7695`), expands innate-skill-implied categories (`expand_allow_for_innate_skills`, `:7724-7727`), and grants a category simply by it appearing in `allow`. Startup validation rejects `tools.allow` tokens that name no known category/tool (the scheduling comment in `_tool_registry.py:239-241`: "Without these entries, startup validation of tools.allow would reject the category").

### A(iv) `daemon/tools/_auth.py` — what it is and is NOT

`_auth.py` is **NOT** the privileged-category gate. It is the **agent-backed spawn-category** map: `TOOL_REQUIRED_AGENTS: dict[str, list[str]]` at **`:35-51`** maps category → backing agents implicitly added to `team_members` when a category is in `tools.allow` (entries: knowledge/chart/image/council/design). A `guidelines` category is NOT agent-backed ⇒ **no `_auth.py` entry**.

The fail-closed privileged enforcement is the pair:
- **layer 1+2 (visibility/authority):** privileged default-deny strip (A(iii)) + per-agent `tools.allow` entry; and
- **layer 3 (in-body runtime gate):** the factory-captured `agent_id` constant checked FIRST in every tool body.
For a MULTI-agent allow-list, the exact template is `metrics_read.py`: `METRICS_AUTHORIZED_AGENTS: frozenset[str] = frozenset({"maintenancer", "devops", "wanderer"})` (`daemon/tools/metrics_read.py:207-209`) with the comment "The privileged category gates VISIBILITY; this set gates AUTHORITY. Both are required" (`:204-206`). For `guidelines`: `GUIDELINES_AUTHORIZED_AGENTS = frozenset({"ari", "leader", "devops", "sonic-leader"})`, checked before anything else (unknown-agent / not-authorized typed tokens first — the metrics ladder `metrics_read.py:1404-1417`).

### A(v) `daemon/loader.py` — the cold-boot warm-list (a REQUIRED touch point, easily missed)

`daemon/loader.py:489-546` imports new category modules and builds None-stub tool lists so `@register_tool_category` decorators run and the cold-boot docs scan registers the category — otherwise the category section is MISSING from the agent's rendered prompt until some instance build repopulates the metadata (the "W1-FIX-NOW class"). The metrics-read pattern is `:521-528`; nest-report-read `:538-546`; the stub-construction block follows at `:548-590`. A `daemon.tools.guidelines` module must be added here (import + `create_guidelines_tools(None, "", ...)` stub + scan list).

---

## B. THE PIN CENSUS — every site that pins the notify-user / privileged-set registration

**Derive-by-grep is binding**; the census below is verified at this base SHA and is the files-to-touch inventory for a `guidelines` category (privileged set 21 → 22). Counts corrected 2026-11-13 (B5/B10 off-by-ones); the enumerated numbers remain a STARTING POINT — DERIVE-BY-GREP at implementation time is the BINDING rule (decisions.md D14; the registry's own three greps at `_tool_registry.py:441-443` are the authority).

### B1. Registry prose census (canonical)
- `daemon/tools/_tool_registry.py:375-445` — SAME-PR RULE prose: names SIXTEEN pin files + mutation-pack string; must be re-derived and updated in the SAME commit (its last derivation is dated 2026-11-11 prompt-assent Phase-4 at `:430-433`).
- `daemon/tools/_tool_registry.py:681-708` — notify-user entry comment (template for the guidelines entry comment).

### B2. Registration pin packs (the dedicated per-category test; the NEW pin lives here)
Per-category pin packs observed (legs a–j: AST discovery, decorator order, CATEGORY_MODULES path, DYNAMIC+KNOWN names, extend greppable + precedes help/scan/filter, privileged membership, exact set, count, meta.json allow census, in-body gate constant, gate-order-in-source):
- `tests/unit/test_notify_user_registration.py` (260 L) — THE canonical template (ari-only census variant).
- `tests/unit/test_metrics_read_registration.py` — the multi-agent allow-list census variant (closest shape for guidelines' 4-agent allow).
- Siblings: `test_board_registration.py`, `test_self_alarm_registration.py`, `test_instance_ops_registration.py`, `test_wedge_capture_registration.py`, `test_memoir_registration.py`, `test_anomaly_report_tool_registration.py`, `tests/unit/tools/test_nest_report_read_registration.py`, `tests/unit/tools/test_prompt_assent_registration.py`, `tests/unit/tools/test_prompt_reload_pins.py` (count-only), plus non-privileged packs `tests/unit/tools/test_service_registration.py`, `test_scheduling_registration.py`, `test_ens_env_registration.py`.
- **A `guidelines` registration pin should live in `tests/unit/tools/test_guidelines_registration.py`** — the newest registrations (nest-report-read 2026-11-05, prompt-assent 2026-11-10) migrated to `tests/unit/tools/`; model it on `test_metrics_read_registration.py` (multi-agent allow census: assert the category in EXACTLY the four meta.json files) with the notify-user legs (b)(c)(d)(e)(f)(g)(i)(j).

### B3. Exact-set pins — `EXPECTED_PRIVILEGED_SET` definitions (6 files; the definition literal needs the new entry)
- `tests/unit/test_board_registration.py:70` (+ comparison `:220`)
- `tests/unit/test_instance_ops_registration.py:88` (`:219`)
- `tests/unit/test_metrics_read_registration.py:92` (`:232`)
- `tests/unit/test_notify_user_registration.py:56` (`:193`)
- `tests/unit/test_self_alarm_registration.py:76` (`:228`)
- `tests/unit/test_wedge_capture_registration.py:72` (`:190`)

### B4. Exact-set pins — inline `== frozenset({...})` literals (5 files)
- `tests/unit/tools/test_upgrade_registration.py:152`
- `tests/unit/tools/test_attestation_registration.py:195`
- `tests/unit/tools/test_secrets_registration.py:228`
- `tests/integration/test_maintenancer_spawn_resolves_tools.py:412` (comment `:474-479` carries the notify-user entry)
- `tests/integration/test_service_tool_flag_off_byte_identical.py:842` (comment `:911-916`; ALSO its docstring census at `:819` — "The exact-set pin lives at ELEVEN sites..." — a stale-prose correction site, same-commit duty)

### B5. Count pins — `len(PRIVILEGED_TOOL_CATEGORIES) == 21` (12 sites / 11 files — CORRECTED 2026-11-13, was off by one)
`test_anomaly_report_tool_registration.py:383` · `test_board_registration.py:235` · `test_instance_ops_registration.py:222` · `test_memoir_registration.py:149` · `test_metrics_read_registration.py:235` · `test_notify_user_registration.py:196` · `test_self_alarm_registration.py:231` · `test_wedge_capture_registration.py:193` · `tests/unit/tools/test_nest_report_read_registration.py:175` · `tests/unit/tools/test_prompt_assent_registration.py:218` AND `:260` · `tests/unit/tools/test_prompt_reload_pins.py:71`. (Bump to 22 in the SAME commit. `test_memoir_registration.py:377-422` additionally self-pins the count-pin population — a derivation-shape pin that greps the OTHER files; adding a new pin file updates that derivation.)

### B6. Derived-set pins (no literal edit, but re-run green only after the count bump)
- `test_memoir_registration.py:157` (`== derived`) and `tests/unit/tools/test_prompt_reload_pins.py:77` (`== derived`).

### B7. meta.json allow pins
- **Allow-ARRAY ORDER pin** (the ari tools.allow array-order discipline): `tests/unit/test_board_registration.py:298-310` — pins ari's allow array as an ordered literal ending `..., "notify-user", "pok", "nest-report", "memoir", "prompt-reload", "prompt-assent"`. Appending `"guidelines"` at the END of ari's array (the memoir/prompt-assent convention) requires this literal bumped in the SAME commit.
- **Allow-string pins in other packs:** `tests/unit/test_anomaly_report_grant_pins.py:317` (ari allow string literal) and `:99` (privileged-set context list).
- **Cross-family allow census:** `tests/unit/agents/test_aegis_security_family.py:171` carries a privileged-category list including `"notify-user"` — verify whether it is an exact pin (likely needs the new entry if it asserts exact equality/coverage).

### B8. Behaviour/registration surface tests (re-run, may not need edits)
- `tests/unit/tools/test_frozen_tool_name_discovery.py` — KNOWN_TOOL_NAMES vs source `@tool` universe bidirectional drift (REGENERATE KNOWN_TOOL_NAMES with the two new names or this fails).
- `tests/unit/test_self_alarm_registration.py:500` — a phrase pin asserting category-doc text ("notify-user category" appears in rendered docs) — the class of doc-render pins a new category's CATEGORY_DOC may trip.
- Mutation pack: `test/packs/metrics_read_mutation_matrix_test.py` MT-01 — count find/replace string DERIVED from the live constant at pack import (auto-adapts), but the pack needs the new category as a MUTATION TARGET ("a category with no mutation target is not covered by the matrix", `_tool_registry.py:428-429`).

### B9. Agent-side files (the allow-list grant)
- `agents/ari/meta.json:44` — `"notify-user"` in the allow array (ari appends new privileged tokens at the END: `:45-48` pattern `"pok", "nest-report", "memoir", "prompt-reload", "prompt-assent"`).
- For guidelines: `agents/ari/meta.json`, `agents/leader/meta.json:15`, `agents/devops/meta.json:12`, `agents/sonic-leader/meta.json:15` — each `tools.allow` gains `"guidelines"` (leader/devops/sonic-leader use single-line arrays; positional convention is append-at-end; their arrays are NOT order-pinned, only ari's is).
- Each of the four agents' `tools_note.md` gains a section (see D) + `agents/.prompt-artifacts.sha256` manifest regeneration + `SEALED_SHA_BASELINES` re-pin in the SAME commit (notify-user precedent: `.agents/shared/planning/ari-notify-user/decisions.md:143`).

### B10. Census data file
- `.agents/shared/metrics/tool-categories.json:38` — the sorted frozen-vocabulary category census (63 categories at last capture — CORRECTED 2026-11-13, was off by one); add `"guidelines"` in sorted position. (Consumed by the central-metrics exposition lanes; see `.agents/shared/planning/central-metrics-v1/evidence/*exposition*.txt` shapes.)

---

## C. Ceremony / census docs — where registration + grant ceremonies are recorded

1. **The registry prose block** `daemon/tools/_tool_registry.py:375-445` is THE living census of record — each ceremony re-derives it and dates the derivation (current: "the 2026-11-11 prompt-assent Phase-4 derivation"). The guidelines ceremony adds its dated derivation line here.
2. **`.agents/shared/metrics/tool-categories.json`** — the machine-readable sorted census (see B10).
3. **The lane planning dir** `.agents/shared/planning/guideline-read-tool/` — the house shape is `architecture-recommendation.md` / `decisions.md` / `plan-overview.md` / phase files (+ `research-*.md`). Planning is COMMITTED in-branch (repo conventions blueprint). The notify-user and memoir lanes' plans are the precedent corpus.
4. **Approver tracking** `.agents/approver/<feature>-tracking.md` (notify-user: `.agents/approver/ari-notify-user-tracking.md`) when the approver lane runs.
5. **Tester evidence** `.agents/tester/RESULTS/<date>-<lane>.md` and shell packs `test/packs/<name>_*.sh` registered in `.agents/tester/PACKS.md` (notify-user packs: `test/packs/notify_user_unit_test.sh`, `notify_user_mutation_legs.sh`, `notify_user_boot_smoke_test.sh` — named in `tests/unit/test_notify_user_registration.py:33` and the conventions research).
6. **"First privileged-category grant to a sonic agent" ceremony note** should land in ALL of: the planning dir (decisions/plan-overview — the user-ruling record, like memoir's "ari EXACTLY" DD-13), the registry entry comment in `PRIVILEGED_TOOL_CATEGORIES` (every entry names its audience + the ruling date: `metrics-read` `:715-716` "maintenancer + devops + wanderer"), the SAME-PR derivation note (`:430-433`), the `tool-categories.json` regeneration commit, and the tester RESULTS file proving the 4-agent census. The sonic-roster precedent lives in recent history (v0.30.2 roster amendment, merged 82e2bd849) — cite it as the grant authority.

---

## D. Doctrine text home — where "tool help" and per-agent doctrine live

There are TWO houses, both required by the notify-user precedent:

1. **Tool-side (code, shipped with the tool):**
   - the module docstring (`notify_user.py:1-117`) — the doctrine carrier;
   - `CATEGORY_NAME` / `CATEGORY_DOC` (`notify_user.py:149-169`; `metrics_read.py:141-202`) — read generically by the help/docs machinery (loader/help render it; no registry edit needed beyond CATEGORY_MODULES);
   - `_full_doc_` / `_build_full_doc()` (`notify_user.py:538/:543`) — the `tool_help` surface.
   → The two doctrine lines belong here FIRST: "Fixed files are the authority; this tool only reads them" fits the module docstring + CATEGORY_DOC; "Every feature shipping dormant / config-armed / ceremony-gated / with silently-biting rules OWES a guideline (see docs/guidelines/TEMPLATE.md)" fits `_full_doc_` (the agent-facing teaching text) — docs/guidelines/TEMPLATE.md exists at this base (landed by merge 5316e0cd3: README.md, TEMPLATE.md, guideline-live-views-passcode.md, guideline-memoir-reflection-lane.md, guideline-notify-user-bounds.md; `docs/guidelines/README.md:3-5` = CANONICAL, Emperor-ratified placement).
2. **Agent-side (prompt artifacts):** `agents/<agent>/tools_note.md` — a DB-versioned prompt artifact (soul/rule/workflow/tools_note/memory/growth are NOT file-written at runtime: `daemon/tools/prompt_artifacts.py::check_write` refuses at two `daemon/tools/filesystem.py` sites; edits run through the DB prompt-versioning state machine — the `agent-prompt-versioning` contract). Notify-user carried its doctrine as an agent-facing `## Notify-User` section in `agents/ari/tools_note.md:820-859` (convention: per-category `##` section; the metrics lane carried the same in THREE tools_note.md files — `metrics_read.py:136-138` comments "the three `tools_note.md` sections"). For guidelines: one `## Guidelines` section in EACH of the four allowed agents' `tools_note.md` (ari, leader, devops, sonic-leader).
3. **Re-stamp duty (SAME commit as any prompt-artifact edit):** regenerate `agents/.prompt-artifacts.sha256` and re-pin affected `SEALED_SHA_BASELINES` rows with dated attestation (notify-user fold: `.agents/shared/planning/ari-notify-user/decisions.md:143`).
4. Registry prose (`_tool_registry.py:326` / `:441-443` greps) is census, not doctrine. House pattern = doctrine in docstring/CATEGORY_DOC/_full_doc_ + tools_note; the tools_note route is where the OBLIGATION phrasing ("OWES a guideline") belongs, since it is behavioral guidance to the agent, while the read-only authority line belongs in the tool help (contract statement).

---

## E. Placement — `daemon/tools/guidelines.py` is house-consistent

- Every category is one flat module in `daemon/tools/` named snake_case, matching the module path in CATEGORY_MODULES: `notify_user.py`, `metrics_read.py`, `nest_report_read.py`, `ens_env_tools.py`, `pok.py`, `prompt_assent.py`. **`daemon/tools/guidelines.py` is exactly this convention.** (Suffix styles vary: bare family word `pok.py`/`memoir.py`/`scheduling.py` vs `_tools`/`_read` suffixed — single-word plain `guidelines.py` matches the `pok.py`/`memoir.py` bare family style; `guidelines_tools.py` would match the ens-env/service style. Both observed; the bare family word is the dominant recent choice (pok, memoir, prompt_reload, prompt_assent).)
- Category key: house rule is KEBAB-case when multi-word, exact family word when single (`notify-user`, `metrics-read`, `memoir`, `pok`). `"guidelines"` is single-word — the key is `guidelines` with a `GUIDELINES_CATEGORY_KEY = "guidelines"` module constant (the notify-user N7 / memoir D4.1 exact-key discipline; never invent variants).
- Tool names snake_case (`guidelines_list`, `guideline_read` — matches `board_create`/`metrics_families` flat snake style). The nest-report-read naming-split precedent (operational `nest-` vs data `anomaly-` vocabulary) does not apply — the tools operate ON the guidelines vocabulary, so `guidelines_*`/`guideline_*` names are consistent.
- Read-only + privileged mirrors `metrics-read` / `nest-report-read` structurally: no write tool in the category, no audit row on reads, tools stay present and refuse typed when the store is unmounted (RULING-1 shape), auth-first gate order.
- The factory + registry + instance.py extend + loader warm-list placement: append AFTER the latest extends (prompt-assent at `instance.py:7102-7106` is currently last of the privileged family; the guidelines extend goes after it, still before help/scan/filter) — placement within the extends block is by family grouping (board→…→prompt-assent chronological); before `create_mission_tools_if_available` (`:7116`) and the always-wired factories is fine as long as the precedes-help/scan/filter pin holds.

---

## Files-to-touch inventory (the registration leg, at a glance)

**Code (5 files, 1 new):**
1. NEW `daemon/tools/guidelines.py` (docstring doctrine, CATEGORY_NAME/CATEGORY_DOC, GUIDELINES_CATEGORY_KEY, GUIDELINES_AUTHORIZED_AGENTS frozenset{ari,leader,devops,sonic-leader}, TOOL_NAMES, typed tokens, factory `create_guidelines_tools(manager, *, current_instance_id, agent_id, agent_tag)` with `@register_tool_category` ABOVE `@tool`, `_full_doc_`, never-raise, auth-first gate; reads ONLY docs/guidelines/*.md).
2. `daemon/tools/_tool_registry.py` — PRIVILEGED entry #22 + SAME-PR prose re-derivation; CATEGORY_MODULES; DYNAMIC_TOOL_NAMES; KNOWN_TOOL_NAMES (regenerate).
3. `daemon/tools/instance.py` — import + extend (+ standard comment) before help/scan/filter.
4. `daemon/loader.py` — warm-list import + stub.

**Agents (4 meta.json + 4 tools_note.md + manifest):**
5. `agents/ari/meta.json` (append END), 6. `agents/leader/meta.json`, 7. `agents/devops/meta.json`, 8. `agents/sonic-leader/meta.json`; 9. four `agents/<a>/tools_note.md` `## Guidelines` sections; 10. `agents/.prompt-artifacts.sha256` + SEALED_SHA_BASELINES re-pin.

**Tests/pins (SAME commit):**
11. NEW `tests/unit/tools/test_guidelines_registration.py`; 12-17. the 6 EXPECTED_PRIVILEGED_SET definitions; 18-25. the 12 count pins / 11 files (matches B5 as corrected); 26-30. the 5 inline frozenset pins; 31. `test_board_registration.py:310` ari array literal; 32. `test_anomaly_report_grant_pins.py:99/:317`; 33. `test_aegis_security_family.py:171` (verify pin shape); 34. `test_service_tool_flag_off_byte_identical.py:819` stale docstring census correction; 35. mutation-pack target in `test/packs/metrics_read_mutation_matrix_test.py`.

**Census/docs:**
36. `.agents/shared/metrics/tool-categories.json`; 37. planning dir `.agents/shared/planning/guideline-read-tool/*` (committed in-branch); 38. tester RESULTS + PACKS registration; 39. registry SAME-PR derivation date line (same file as #2).

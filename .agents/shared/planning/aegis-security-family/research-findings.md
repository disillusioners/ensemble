# Research Findings — Aegis Security Family

> **ARCHITECT AMENDMENT NOTE (2026-11-04).** The Phase-0 architect review
> ([`architecture-recommendation.md`](./architecture-recommendation.md) @ `f7204bd9d`) ruled Q1–Q4 and
> issued binding amendments AM-1…AM-15 (folded in `decisions.md` §C2). Claims in THIS digest that the
> rulings superseded are marked inline with `[SUPERSEDED by AM-x]`; pure factual errors are corrected
> with a dated note. **Superseded:** D4's "aegis holds `chart` + the charter spawn right" resolution
> (AM-1 — chart is DROPPED); D8's "`webfetch` is the CVE-lookup capability" (AM-3 — operator-disabled
> live; the live inventory is context7 + plane + zai-web-reader/web-search/zread); D7's "no daemon
> code reads a per-agent `watchover` block" and "the real lever is the per-instance API" (AM-5 — the
> key is inert except for `watcher`; the API is an operator-only tool-call gate, not a supervisor);
> C6's "the bank is GLOBAL ⇒ anything aegis seeds becomes searchable by worker" (AM-7 — the true
> invariant is manifest-gating; ownership filter also applies). **Corrected:** D2's "`project`→6" is
> actually **21 tools** (AM-2).

**Commission:** `aegis-security-family` (user-ratified design, Discord 2026-11-04)
**Worktree:** `/home/nea/ensemble-src-wt-aegis-security-family`
**Branch / commit:** `feature/aegis-security-family` @ `1f6898ae2889ff002b4656486f6be3ee1048b649` (= `origin/latest` at lane start)
**Produced by:** technical-analysis worker (plan-explorer digest consolidated + first-hand re-verification)
**Status:** Complete — record of verified facts for the plan-creation worker

---

## 0. Provenance and verification method

This digest consolidates the three explorer digests (mechanics / pins / guide+skills) and adds
**Section D** — fourteen additional facts I verified first-hand by reading the code, because the
DP1–DP8 resolutions depend on them and the original digest did not cover them.

Every claim carries a `file:line` citation into the lane worktree at the commit above.
No discovery work was re-run; the explorers' findings were re-read for citation accuracy and
extended only where a design point demanded it.

**Correction to the dispatch brief:** the brief cites
`tests/unit/agents/test_maintenancer_agent.py` as a pattern to mirror. **That PATH is stale** (the
directory is `tests/unit/`, not `tests/unit/agents/`). The real, extant patterns are
`tests/unit/agents/test_sketcher_agent.py` (the richer one — 40+ test functions) and
`tests/unit/test_maintenancer_team_members_within_team.py` (pure-file-parsing org-chart test).
Both are recorded below (B8). **Second correction (2026-11-04, plan council C1(a)):** the
`tests/unit/test_maintenancer_agent.py` file at the correct path **DOES exist** and carries a
load-bearing leader-length pin — see §B8 and the C1(a) correction there.

---

## A. MECHANICS

### A1 — The spawn/send gate is caller-side `team_members` ONLY

- Spawn gate: `daemon/tools/instance.py:3176-3196`; send gate: `daemon/tools/instance.py:4420-4462`.
- Both delegate to `daemon/tools/_auth.py:90-260` (`_check_team_membership`).
- **Deny-by-default.** `daemon/tools/_auth.py:145-151`: if `team_members` is missing or empty
  AND no matching `tools.allow` category maps to the requested agent, the request is denied with
  *"Allowed team members: []"*.
- **Versioned caller meta is resolved first** — `daemon/tools/_auth.py:152-169`: the caller
  meta is fetched via `registry.get_version(caller_agent_id, version_tag)` and falls back to
  `get_resolved`. A versioned caller enforces its OWN `team_members` / `tools.allow`.
- **Implied members are merged from categories.** `daemon/tools/_auth.py:190-195`: for every
  category in the caller's `tools.allow` that appears in `TOOL_REQUIRED_AGENTS`, the backing
  agent(s) are appended to the effective team.
- `TOOL_REQUIRED_AGENTS` (`daemon/tools/_auth.py:35-51`) — the complete map, five rows:

  | category | implied team member(s) |
  |---|---|
  | `knowledge` | `explorer`, `kb-writer` |
  | `chart` | `charter` |
  | `image` | `image-reader` |
  | `design` | `image-comparator` |
  | `council` | `governor` |

- **`tools.deny` AND `tools.deny_spawn` both strip implied members** — `daemon/tools/_auth.py:196-244`.
  `deny` additionally removes the category's tools from the callable set (handled by the tool
  layer); `deny_spawn` removes only the spawn right and leaves the tool callable. The two are
  merged into one `spawn_block_categories` set.
- Entries in `deny`/`deny_spawn` that are **not** `TOOL_REQUIRED_AGENTS` keys are treated as
  literal tool names at this layer — a no-op for the spawn gate, but they close the bypass where
  an agent denies a category at the tool layer yet still spawns its backing agent directly.
- `deny_spawn` is registered in the schema (`daemon/registry.py:96-105`) with its own docstring;
  it is **only** for implied-member stripping. It never removes tools.
- **No current agent meta uses `deny_spawn`** (verified by scanning every `agents/*/meta.json`).

### A2 — Leaves are empty/absent `team_members`

- `agents/sketcher/meta.json` → `"team_members": []`; `agents/critic/meta.json` → `"team_members": []`.
- `agents/worker/meta.json` → the key is **absent entirely**; `daemon/registry.py:689` defaults
  it to `[]`.
- Result: `worker`, `sketcher`, `critic` are all leaves under the same rule.

### A3 — `ari` reaches `aegis` through `job_create` with ZERO ari changes

- `ari` has **no `instance` category** and **no `team_members`** in `agents/ari/meta.json`.
- `ari`'s agent-reach is the `job` category (`agents/ari/meta.json` → `tools.allow` contains
  `"job"`). `job_create` is registered under that category at `daemon/tools/job_queue.py:1399-1411`
  and takes a **free-form `agent_id`** (`daemon/tools/job_queue.py:1402` — *"Agent ID to run the
  job (e.g., 'developer', 'leader')"*).
- **Verified: the job path has NO team-membership gate.** `grep -n "_check_team_membership\|team_members"
  daemon/tools/job_queue.py` returns **zero hits**. The job path never consults
  `TOOL_REQUIRED_AGENTS` and never reads the caller's `team_members`.
- Consequence: `ari` can dispatch `aegis` by `agent_id="aegis"` the moment `agents/aegis/`
  registers and the registry scan picks it up. **Zero `ari` changes.**

### A4 — `leader` → `aegis` requires exactly ONE line

- `leader` carries the `instance` category (`agents/leader/meta.json` → `tools.allow` starts with
  `"instance"`).
- `leader`'s `team_members` today: `planner, developer, reviewer, tidier, approver, architect,
  tester, giter, devops, explorer, wanderer, kb-writer, doc-writer, maintenancer, designer, gaia`
  — **`aegis` is absent.**
- `leader` has **no `job` category** in its allow list, so `leader` has no job-based reach path.
  The `instance` category's `send_message` / `spawn_instance` are its only agent-reach surface.
- ⇒ **One line added to `agents/leader/meta.json` `team_members`: `"aegis"`.**
- `leader`'s `tools.deny` is `None` (absent). `leader`'s `llm_model` is absent (system default).

### A5 — Allow/deny entries accept categories OR individual tool names

- `daemon/registry.py:82-86` — the `allow` field docstring: *"List of tool categories or
  individual tool names to allow."* Same for `deny` (`:91-96`).
- `daemon/tools/instance.py:563-579` — expansion: an entry that is a registered category expands
  to that category's tools; any other entry is added as a **literal tool name**.
  `deny` then subtracts: `allowed_tools -= denied_tools` (`:579`). Deny wins after allow expansion.
- `agents/critic/meta.json` mixes both idioms in one agent:
  `allow: ["read_file","image","design"]` (a literal + two categories),
  `deny: ["bash","proc","instance","service","midflight","shared_meta_kv","infra","mcp","image_save"]`
  (a mix of categories and literal tool names).
- **Privileged categories never join the default universe** — `daemon/tools/instance.py:551-561`:
  in the empty-allow branch, `if category_key in PRIVILEGED_TOOL_CATEGORIES: continue`.

### A6 — `filesystem` is one category holding all six file tools

- `daemon/tools/filesystem.py:10` declares the category; the six tools register at
  `daemon/tools/filesystem.py:588 / :661 / :752 / :1105 / :1307 / :1508` —
  `list_directory, read_file, glob_files, grep_files, write_file, edit_file`.
- **There is no read-only filesystem sub-category.** Structural read-only is expressed as
  *allow `filesystem` + deny `["write_file","edit_file"]`* — which is the schema's own worked
  example (`daemon/registry.py:111-114`).
- **`bash` is an unconstrained write path.** The bash lane runs arbitrary commands; a `write_file`
  deny does not stop `bash > file`. For any agent holding `bash`, read-only-on-target is
  **doctrinal**, exactly as the commission anticipated.

### A7 — There is no web-search tool category; three CVE-lookup routes exist

- **No `web`-family category is registered.** The complete registered-category list is in D2.
- Route 1 — the **`mcp` category** (`daemon/tools/instance.py:467-495`): a *dynamic* sweep of
  live `mcp_<server>_<tool>` names, populated at instance-construction time from the registered
  MCP servers. It only resolves tools whose server is actually registered on the deployment.
- Route 2 — **`bash curl`/`wget`**: always available wherever `bash` is granted.
- Route 3 — **`mcp_rpc`** (privileged, `ari`-only per `daemon/tools/_tool_registry.py`) — **not
  available to children.**
- **Cost of route 1:** granting `"mcp"` sweeps in **every** live MCP server's tools, not just a
  chosen one. See D8 for the actual server inventory. **[CORRECTED by AM-3 — 2026-11-04]** the
  heading's claim "there is no web-search tool category" is true as a CATEGORY statement, but the
  implied "no web-search capability exists" is FALSE on live — `zai-web-search` (a user MCP server)
  provides it through the `mcp` grant; see D8 for the corrected inventory.
- **Mitigation verified (D13):** the sweep excludes any tool owned by another registered category
  (`_owned_elsewhere`, `daemon/tools/instance.py:488-495`), so granting `"mcp"` does **not** leak
  the `plane_*` tools.

### A8 — `llm.allowed_models` catalog

- `daemon/config.py:437-504` — `allowed_models`; exact, case-insensitive string match;
  validated only at the four spawn-time selection flows (spawn `model=` override, the weighted
  `llm_models` pool filter, `spawn_councilor` model validation, session-restore re-validation).
- Shipped default tuple: `("agentic", "coding", "coding2", "vision", "img-gen", "decision")`
  (`daemon/config.py:486`; mirrored at `config.yaml:87`).
- Current pins: `designer`/`sketcher`/`critic` → `"vision"`; `worker` → `"coding"`
  (single-string pin since v0.25.1 — the client-side weighted `coding2:30/coding:70` pool was
  removed); `ari`/`leader` → absent (system default).

### A9 — Registry discovery is fail-soft in two distinct ways

- `daemon/registry.py:609-789` scans `agents/*/`, skipping `_`-prefixed dirs and symlinks.
- **Bad JSON or `AgentMetadata` ValidationError → warn + SKIP** — the agent silently unregisters;
  the boot does NOT crash.
- **`tools` parse failure → `tools_filter = None` = ALL TOOLS ALLOWED** —
  `daemon/registry.py:656-662` (parse guard) and `daemon/registry.py:79` (`None` semantics).
  ⇒ **Keep every `tools` block valid.** A malformed `tools` block on a security agent is a
  privilege escalation, not a lint error.
- `meta.id` / directory-name mismatch → warn only; the directory name wins.

### A10 — `worker` needs ZERO changes

- The spawn gate reads **only the caller's meta** (`daemon/tools/_auth.py:152-169`). There is no
  spawnee-side registry consulted for team membership.
- No test pins worker's team state — `grep -rn "team_members" tests/` finds only docstrings,
  patches of other agents, and unrelated suites.
- ⇒ Adding `"worker"` to `aegis`'s `team_members` is a pure caller-side change.

### A11 — `meta.json` field vocabulary (`daemon/registry.py:674-721`)

`id, name, description, icon, color, version, llm_model, llm_models (pool), system, innate_skills,
skill_injection, no_force_explore, recursion_limit_multiplier, default_queue,
tools{allow, deny, deny_spawn}, team_members, context_injection{heuristic_match_shared_md_files},
watchover{timeout_seconds}, snapshot_enabled, skill_search_interval, mcp_full_access, capabilities, tags`.

Exemplar bodies:

| agent | lines | shape |
|---|---|---|
| `designer` | 22 | full category allow incl. `instance`+`design`+`view-views`; `team_members: [worker, sketcher, critic]`; `recursion_limit_multiplier: 7`; `skill_injection: false` |
| `sketcher` | 31 | individual-tool allow; `watchover.timeout_seconds: 90`; `default_queue: system_parallel_queue`; `team_members: []`; `recursion_limit_multiplier: 12` |
| `critic` | 18 | mixed allow/deny; `team_members: []` |
| `worker` | 41 | 21 allow categories; no `instance`; no `team`; `llm_model: coding` |

---

## B. PIN CEREMONY + TEST SURFACE

### B1 — Baseline shape: 161 rows, depth-2 only

- `agents/.prompt-artifacts.sha256` = **161 rows**, format `<sha256>␣␣agents/<dir>/<file>`.
- Coverage = **depth-2** `VERSIONED_PROMPT_ARTIFACTS` only (`daemon/tools/prompt_artifacts.py:119-140`):
  `soul.md, rule.md, workflow.md, tools_note.md, tools.md (legacy), skill.md (legacy),
  memory.md, growth.md`.
- The collector is explicit — `daemon/tools/prompt_artifacts.py:355-377`
  (`iter_versioned_artifact_paths`): *"exactly `<agents_root>/<dir>/<artifact>`… `_`-dirs included,
  depth-3+ excluded."*
- Observed distribution of the 161 rows: `soul.md` 42, `rule.md` 39, `workflow.md` 40,
  `tools_note.md` 35, `growth.md` 5.
- **⇒ `meta.json`, `skill-set.yaml`, and `skills-template/*` add ZERO rows.**
- **⇒ 4 new agents × 4 prompt files (`soul/rule/workflow/tools_note`) = 161 → 177.**
- No pytest asserts the count. Enforcement = stage-time tester leg (`PROMPT-BASELINE: OK N/N`) +
  a non-blocking boot WARN + the `stage.sh` journal leg.

### B2 — Re-stamp procedure

- `python3 scripts/protection/generate_prompt_artifact_baseline.py` regenerates (writes);
  `--check --tree <dir>` verifies read-only (exit 0 clean / 1 drift / 2 no-tree).
  **[AM-11 — 2026-11-04]** the `--check` exit code is **0 BOTH when clean AND when the baseline file
  is ABSENT** (`generate_prompt_artifact_baseline.py:127-133`) — every baseline leg must additionally
  assert the file exists and has exactly 177 rows; `rc=0` alone proves nothing.
- **No venv required** — the script loads `daemon/tools/prompt_artifacts.py` **by file path**
  via `importlib` (`scripts/protection/generate_prompt_artifact_baseline.py:40-59`) precisely
  because `import daemon.tools.prompt_artifacts` drags the langchain stack, unavailable to the
  bare host `python3` that `stage.sh` runs under. `prompt_artifacts.py` is stdlib-only by design.
- Output is deterministic: sorted paths, `sha256sum -c` compatible, no comments.
- **Doctrine:** agent files + the regenerated baseline ship in **ONE commit**
  (`agents/.prompt-artifacts.sha256` is an agents-tree file and rides the same pin ceremony).

### B3 — THE TRAP: `test_plane_agent_surface_census.py` loads the REAL tree

- `tests/unit/tools/test_plane_agent_surface_census.py` globs `*/meta.json` across the real
  `agents/` tree — so **every new agent is automatically in scope**.
- **E3** (`:15-18`, `:62-67`): `DISCLOSED_READ_HOLDERS: frozenset[str] = frozenset({"watcher"})`
  — *"Frozen at the census's first run… any GROWTH beyond this set is a RED."*
- Mechanism: an agent with **empty/absent `tools.allow`** inherits the **default universe**
  (`daemon/tools/instance.py:551-561`), which **includes the `plane` read tools** (the `plane`
  category is not privileged). Such an agent therefore resolves plane reads → **E3 RED**.
- **FOLD (mandatory): every new agent carries an explicit, NON-EMPTY `tools.allow`.**
- **E4** (`:19-21`): *any* new agent with `mcp_full_access` including `plane` reddens.
  **FOLD: never set `mcp_full_access` on any aegis-family agent.**

### B4 — DYNAMIC report-integrity check

- `tests/unit/test_report_integrity_prompts.py`.
- `D_REQUIRED_SNIPPETS` (`:110-114`) = **`("[REPORT SANITY:", "interim, not completion", "send_message")`**.
- `PROMPT_SURFACE` (`:121-125`) = `("rule.md", "workflow.md", "soul.md")`.
- The dynamic rot-mitigation test (`:232-266`) enumerates **every** `agents/*/meta.json`; for any
  agent with a **non-empty `team_members`** it scans `rule.md` + `workflow.md` + `soul.md` **plus
  every `skills-template/*.md`** and fails if any snippet is missing.
- `GRANDFATHERED_PARENTS` (`:160-162`) = `{blueprinter, devops, giter, jober}` — a new agent is
  not grandfathered.
- ⇒ **Aegis (non-empty `team_members`) MUST carry all three snippets.** The three children have
  empty teams ⇒ exempt (the test skips them).
- ⇒ The literal token **`send_message` must appear in aegis prose** — and this is *compatible*
  with the writing guide, which explicitly lists *"Tools it actually invokes (`spawn_instance`,
  `send_message`, `load_skill=`, `git status`)"* as legitimate agent-facing references
  (`docs/agent-prompt-writing-guide.md:44`).

### B5 — `SEALED_SHA_BASELINES` — 35 entries, untouched by this lane

- `tests/test_chart_image_delivery_e2e.py:1425-1505` — `SEALED_SHA_BASELINES: dict[str, str]`
  with **35 entries**. Values are **content-SHA256**, not commit shas.
- Entries include `agents/charter/{workflow.md,rule.md,soul.md,meta.json}`,
  `agents/charter/skills-template/install-mermaid-cli.{md,lib.sh}`,
  `agents/_prompt_system/innate-skills/chart/skill.md`, `agents/ari/workflow.md`, plus
  `daemon/` files.
- **None reference a not-yet-existing path** ⇒ adding new agent dirs cannot redden this baseline.
- The verification leg is `TestPhaseDDoesNotModifySealedArtifacts` at
  `tests/test_chart_image_delivery_e2e.py:1508`.

### B6 — No-touch confirmations

| file | why it is safe |
|---|---|
| `tests/unit/test_instance_ops_registration.py` | adds nothing — provided the new agents never request `instance_read` / `instance_ops` (both are **privileged**, `daemon/tools/_tool_registry.py:367+`) |
| `.agents/shared/metrics/tool-categories.json` + `tests/unit/tools/test_tool_category_artifact.py` | no new `register_tool_category` sites ⇒ the artifact is untouched. **This is the F-1 trap class** (a stale committed artifact reddened a whole lane) |
| `tests/unit/agents/test_sketcher_agent.py:610-630` | `test_agent_registry_scan` probes **fixed** names `critic`/`sketcher` — the aegis analogue needs its own probe (D9) |
| agents-tree manifest count | **338** — verified: `find agents -type f \| wc -l` = 338, matching the release-manifest `agents 338` count exactly. Breakdown: 1 depth-1 (`.prompt-artifacts.sha256`) + 235 depth-2 + 102 depth-3 |

### B7 — Implement environment

- `cd /home/nea/ensemble-src-wt-aegis-security-family && uv sync` creates `.venv` from `uv.lock` (`README.md:45-50`); `dev.sh`
  auto-picks `.venv/bin/python`. `requires-python >=3.13`.
- Default pytest addopts deselect `integration` / `postgres` / `slow` / `conformance`.
- **The baseline re-stamp is venv-free** (B2) — it can run before the venv exists.

### B8 — The real test patterns to mirror (correction to the brief)

- **`tests/unit/agents/test_sketcher_agent.py`** — the richest exemplar (40+ functions):
  `test_allow_set_exact` (`:129`), `test_closed_allow_is_the_fence` (`:133`),
  `test_default_queue_system_parallel` (`:140`), `test_recursion_limit_multiplier_12` (`:143`),
  `test_watchover_timeout_at_least_60` (`:146`), `test_empty_team_leaf` (`:153`),
  `test_no_system_internals_in_prose` (`:164`), `test_no_md_path_tokens_in_prose` (`:171`),
  `test_rule_md_at_most_seven_cardinals` (`:179`), `test_tone_block_in_soul` (`:184`),
  `test_no_adapted_from_provenance` (`:189`), `test_section7_exempt_no_scrutiny_guidance` (`:195`),
  `test_skill_set_entries_have_templates_and_match_versions` (`:235`),
  `test_agent_registry_scan` (`:610`).
- **`tests/unit/test_maintenancer_team_members_within_team.py`** — pure-file-parsing org-chart
  test (no daemon/DB, no LLM): asserts `meta.json` `team_members` equals an expected frozenset,
  then scans every `skills-template/*.md` for fallback-language regexes and fails if a named
  peer is **not** in `team_members`. It explicitly treats *"ask the leader"* as a caller
  reference, **not** a violation. Its own header documents the guide §8 basis.
  ⇒ This is the exact shape for the aegis team-topology pin.
- **~~`tests/unit/test_maintenancer_agent.py` DOES NOT EXIST~~ [CORRECTED 2026-11-04 — plan council, C1(a)]** the earlier claim conflated a STALE PATH with non-existence. The file **EXISTS** at `tests/unit/test_maintenancer_agent.py` (20,671 B) — it was `tests/unit/agents/test_maintenancer_agent.py` (the brief's path) that is stale. **This is a load-bearing correction:** the file's `test_leader_team_members_length_16` (`:517-524`) asserts `len(leader.team_members) == 16` ⇒ the leader one-liner (+`aegis` ⇒ 17) is a **DETERMINISTIC net-new red at Gate A** unless folded IN the pin-ceremony commit. It is a **REAL pin, registered in the fold list** — not a no-touch file. The pin's own docstring documents the house extension pattern: *"Spec — 15 after 'designer', 16 after appending 'gaia' (environment-terraform)"* ⇒ the sanctioned fold: 16 after 'gaia', **17 after appending 'aegis'** (C1(b)).
- **`tests/unit/tools/test_prompt_section_reference_integrity.py`** (W5, plan council) — **ABSENT
  from the original fold list; now registered.** This is the only repo-wide prompt-surface sweep: it
  scans ALL `agents/*/{soul,rule,tools_note,workflow,memory}.md` AND every `skills-template/*.md` —
  exactly the 16 prompt files + 4 skill bodies this lane lands. Its `BARE_AGENTS_PREFIX_RE` (`:251`)
  enforces the second closure class (bare `agents/`-prefix cross-reference tokens) that guide §10
  (`docs/agent-prompt-writing-guide.md:254`) requires alongside the `.md` token class. It runs
  repo-wide at Gate A regardless (council adjudication), and is additionally a **named Phase-5 Gate
  D leg** in `phase5-plan.md`.

---

## C. GUIDE + SKILLS

### C1 — `docs/agent-prompt-writing-guide.md` (344 lines) — binding rules

| § | rule | consequence for the aegis family |
|---|---|---|
| §1 | Write in the **first person** as the agent. **Forbidden in prose:** `meta.json`, `tools.allow`/`tools.deny`, any `daemon/` path, `skill-set.yaml`-as-a-file, the `innate_skills` field name, seeder/`seed_all`, `agent_id=` plumbing, `default_agent_versions`, test file paths | aegis prose says *"my direct use is read-only"* — never *"my allow list narrows the grant"* |
| §1 | **Legitimate:** tools the agent actually invokes (`spawn_instance`, `send_message`, `load_skill=`, `git status`); the skill bank; its own files it genuinely introspects | `send_message` is allowed prose ⇒ B4 is satisfiable |
| §2 | **File roles, one concern each:** `soul`=identity/templates/tone; `rule`=cardinals ≤7 + guidelines; `workflow`=process/dispatch/fan-in; `tools_note`=tool-by-tool operational boundary | 4 files per agent, non-overlapping |
| §2 | **Canonical-home rule:** an artifact appearing in more than one file gets ONE home; the others link. *"A false single-source claim is worse than honest duplication."* | the severity taxonomy and report format live in **one** place (the aegis strategy skill), not in four prompt files |
| §3 | `rule.md` = **≤7 Cardinal rules** at top, guidelines below; no literal duplicates | aegis has 7 candidate cardinals — the guide's cap is a HARD constraint on the design |
| §3 | **Cross-reference convention v2:** only `See <Section Name>` (same agent) and `See <agent>'s <Section Name>` (cross-agent). **ANY filename or path token is forbidden.** Closure grep = `\.md\|workflow\.md\|rule\.md\|soul\.md\|tools_note\.md\|memory\.md` → zero hits | applies to all four agents' prompt surfaces |
| §3 | *Scope carve-out:* the closure grep **excludes operational filesystem paths** — tool parameter values (e.g. `file="soul.md"`) and **runtime read/write targets** (own memory/notes dirs, planning/convention docs) | aegis's runtime engagement-dir paths and its own `skills-template/*.lib.sh` read target are **enumerable exemptions**, not violations — charter is the precedent (charter's `workflow.md` cites `skills-template/install-mermaid-cli.lib.sh`) |
| §4 | Machine-enforced `tools.deny` goes in `meta.json` for safety-critical prohibitions (`edit_file`, `write_file`, `git_commit`, `db_*`). *"Don't describe the meta.json deny mechanism in prose."* | the write-deny is enforced structurally and **not narrated** in the prompt |
| §5 | Tone block in `soul.md`: **voice to caller** + **voice in dispatch prompts** + **per-severity framing** | aegis emits severity labels ⇒ the per-severity framing block is mandatory |
| §6 | My own `*-strategy` skill is for my planning only; never embed it in a worker dispatch. Version consistency: *".md frontmatter is the source of truth"* (never name the manifest file) | the aegis strategy skill is aegis's own planning skill; dispatch prompts must not carry it |
| §7 | **END TURN contract** — after `send_message`, END YOUR TURN; state the *why*; state it **once**, in `workflow.md` | aegis dispatches constantly ⇒ the contract is load-bearing |
| §7 | **Fan-in escape valve** — 4-step ladder: confirm stuck → re-dispatch **ONCE** → mark `[incomplete]` + deliver partial + `### Gaps` → **max 1 re-dispatch** | aegis fans out to 4 lanes; the ladder is mandatory |
| §7 | **Report scrutiny is mandatory for any agent with a non-empty team** — the exact directive text (§7) | aegis MUST carry it (B4) |
| §8 | **Fallbacks stay within `team_members`.** *"Escalation across agents… is the caller's job (leader decides)."* | aegis may NOT write "spawn a developer" — remediation hand-off goes to **leader** |
| §9 | v2 steady state — base dir mirrors the v2 dir | aegis is a NEW base agent (no `[v2]`), so this does not bind |
| §10 | Pre-commit checklist: no system internals; one canonical home; no false "stated once"; ≤7 cardinals; cross-references resolve; tone block; escape valve; skill versions consistent; fallbacks in-team; **no adapted-from/migrated-from/verified-from provenance**; tests pass | the phase gate checklist |

### C2 — Skill mechanics: two modes

- `skill-set.yaml` entry schema: `{name, version, auto_load, category, description}` + optional
  `requires:{mcp, tools, env}`.
- **A skill declaring a non-empty `requires:` block MUST begin its body with
  `capability_check(...)`** — `daemon/services/skill_seed_service.py:361-381`; the check fires
  *before* the include-resolver pass, so the **authored** body is what is inspected. Violation ⇒
  the skill is **skipped** (not seeded) and a warning is logged
  (`daemon/services/skill_seed_service.py:373-380`).
- Seeding runs at **daemon boot** into the **DB-backed GLOBAL skill bank** (`project_id=None`)
  — `daemon/services/skill_seed_service.py:325-426`, `daemon/manager.py:4584-4620`.
  Version-guarded ⇒ idempotent re-seed.
- **Mode (i) — bank-seeded** via `skill-set.yaml` (exemplars: `designer`, `sketcher`).
- **Mode (ii) — FILE-INVOKED, NO manifest** (`agents/charter/` has **no `skill-set.yaml`**;
  `agents/charter/skills-template/install-mermaid-cli.md` is therefore **never seeded** and never
  appears in the global bank). Charter invokes it directly — `agents/charter/rule.md:9-13`
  (Cardinal #1) and `agents/charter/workflow.md:150-190`.
- ⇒ Mode (ii) keeps security tooling **out of the global bank** (searchable by every
  dynamic-skill agent). Directly relevant to the aegis-vs-worker boundary.

### C3 — Self-install skeleton (the only in-repo executable installer template)

- `agents/charter/skills-template/install-mermaid-cli.md` — **364 lines**;
  `agents/charter/skills-template/install-mermaid-cli.lib.sh` — **325 lines**.
  (`install-opendesign` is **not** in-repo — it is the external ancestor.)
- Section headings: `Fences (preserved verbatim from install-opendesign v1.3.0 lineage)` (:31),
  `Pre-flight detect (mandatory first step)` (:57), `Resolve the toolchain (steps)` (:110),
  `Step 0 — Source the lib + warm-probe fast path (idempotency gate)` (:78).
- **Fence set:** NO Docker; NO system packages / apt; **NO remote fetch-and-execute** —
  `npx --no-install` only; honest-stop `HARD_BLOCKER` envelope; idempotent verify-only fast path.
- **User-space install root:** the `$HOME/.nvm` pattern; **absolute-path capture**
  (`NVM_NODE=$(nvm which 24)`) because *"non-interactive bash does NOT source ~/.bashrc"* —
  `install-mermaid-cli.md:137-145`.
- **Pinned-major installs** — `mermaid-cli@12`, `chrome@stable` explicitly rejected as floating
  (`:167-176`).
- **STAGE-then-verify-then-PROMOTE:** config is written to a `mktemp` + trap location and
  promoted **only after** verification passes; failures exit non-zero **without** promoting.
- **VERIFY with real evidence** (render contract); any failure ⇒ non-zero exit, no promotion.
- **PROMOTE** = `mv` + record + release lock; **lock release MUST run on failure**; the attempt
  counter caps self-heal at 2.
- **REVERSAL (uninstall) section** is mandatory.
- **`READINESS_PROBE` in the `.lib.sh`:** exit **0 warm / 1 cold / 2 in-progress**; BSD-portable;
  no hard `flock`. `.lib.sh` functions observed: `charter_readiness_probe`,
  `charter_acquire_install_lock`, `charter_release_install_lock`,
  `charter_write_pending_install_marker`, `charter_read_install_session_state`,
  `charter_bump_install_session_state`, `charter_clear_install_session_state`,
  `_charter_mmdc_render`, `charter_verify_toolchain`.
- **Checksum caveat:** the mermaid installer does **NOT** checksum artifacts — integrity = major
  pins + staged-verify + probe re-check. The in-repo checksum precedent to combine is
  `daemon/services/live_views.py:1357-1406` (`SHA256SUMS` hex + W3C-SRI base64, both pin forms).
  ⇒ **Scanner installers must add sha256 verification of fetched release assets.**

### C4 — Dispatch-doctrine exemplar (`agents/designer/workflow.md` + `rule.md`)

- **Sole-lane rule:** *"Sketcher is the ONLY OD generation lane"* ⇒ aegis analogue: *"hacker,
  cracker, sweeper are the ONLY analysis lanes; worker is the ONLY generalist lane; I never run
  offensive tools myself."*
- END TURN + batching convention.
- **Spawn-gating cardinal:** *"Sub-team: X+Y+Z only… never spawn another <self>."*
- One-shot per partition; escape-valve ladder; report scrutiny.
- **Round budget ≤3, severity-gated:** advisory-only at cap ⇒ accept-with-disclosure;
  critical at cap ⇒ escalate-only.
- Sharding gate (4-part test).
- **Soul-level lane line:** *"I design and review; implementation belongs to developer… That
  line is soul-level, not a tool block."*

### C5 — Clean slate confirmed

- `semgrep` / `gitleaks` / `cppcheck` / `clang-tidy` / `libFuzzer` / `hashcat` / `AFL` / `john` ⇒
  **ZERO hits** across `agents/`, `daemon/`, `docs/`.
- `trivy` ⇒ **1 prose mention** — `agents/devops/rule.md:49`.
- Adjacent complementary surfaces (do **not** duplicate):
  `agents/architect/skills-template/security-design.md` (design-time STRIDE) and
  `agents/reviewer/skills-template/security-review.md` (static, explicitly *"No active probing"*).
- ⇒ Cross-reference them by **section-name form**, never by filename.

### C6 — `worker` today

- 6 files; `skill-set.yaml` is **empty** (`skills: []`); **no** `skills-template/` directory;
  no `instance`/`job` tools; SemiAuto default (stops on breaking/dangerous changes).
- Its `tools.allow` (21 categories): `bash, proc, filesystem, time, self, help, image, image-gen,
  knowledge, mcp, context, shared_meta_kv, dynamic-skill, system-log, plane_sync, infra, service,
  ens-env, snapshot_create, snapshot_search, secrets`.
- **The bank is GLOBAL** ⇒ anything aegis seeds via `skill-set.yaml` becomes searchable by worker
  and every dynamic-skill agent. The *"worker never runs offensive tools"* boundary is
  **prose/doctrine only**; choosing **file-invoked mode** for the offensive/scanner skills keeps
  their procedure text out of the shared bank entirely.
  **[RESTATED by AM-7 — 2026-11-04]** the stated invariant is overstated: an **ownership filter**
  drops bank-cloned skills owned by another agent (`skill_search_service.py:718-822`). The TRUE
  invariant is **manifest-gating**: seeding enumerates **only** `skill-set.yaml` entries
  (`skill_seed_service.py:274-283, :340, :348-352`; NO directory scan of `skills-template/` exists
  anywhere), so un-manifested skills are never banked ⇒ unreachable by every path; the project-skill
  loader indexes only `.agents/<ns>/skills/<name>/skill.json` (`project_skill_source/manifest.py:200-250`)
  ⇒ double gate. **Disclosed:** the installer `.md` bodies are readable on disk in the release
  payload — the boundary is the SEARCH INDEX, not the filesystem.

---

## D. ADDITIONAL FACTS VERIFIED FIRST-HAND (needed by DP1–DP8)

### D1 — The complete privileged-category set (17 entries)

`daemon/tools/_tool_registry.py:367+` (`PRIVILEGED_TOOL_CATEGORIES`), parsed with comments
stripped:

```
system_upgrade, system-log, ens-db, ens-env, view-views, secrets, settings,
board, self-alarm, view-write, board-link, wedge-capture,
instance_read, instance_ops, mcp_rpc, notify-user, metrics-read
```

**Consequence:** privileged categories **never** join the empty-allow default universe
(`daemon/tools/instance.py:551-561`) **and** are not granted by a non-empty allow unless
explicitly named. ⇒ A closed allow already excludes all 17.

### D2 — Complete category → tool map (registered categories only)

`question`→`ask_questions` · `infra`→8 asset tools · `mcp_rpc`→4 remote-hands tools ·
`scheduling`→4 · `instance_read`→5 · `rag`→16 · `db`→5 (incl. `db_conn_add/delete`,
`db_postgres_dml_select`) · `instance_ops`→6 · `dynamic-skill`→6 (incl. `skill_create`,
`skill_fix`) · `service`→5 · `view-write`→1 · `proc`→3 · `ens-db`→4 · `image`→1 ·
`image-gen`→1 · `chart`→1 · `shared_meta_kv`→1 · `blueprint`→8 · `self-alarm`→1 ·
`wedge-capture`→2 · `todo`→6 · `knowledge`→2 (`explore`, `experience`) · `system`→3
(`system_env`, `system_config`, `system_health`) · `job`→19 · `design`→1 · `system_upgrade`→4 ·
`instance`→10 (`spawn_instance`, `send_message`, `terminate_instance`, `pause_instance`,
`resume_instance`, `get_instance_info`, `subtree_messages`, `subtree_status`,
`set_instance_tunable`) · `council`→4 · `skill-evolution`→6 · `mother`→1 · `context`→2 ·
`ens-env`→1 · `filesystem`→6 · `bash`→1 · `time`→1 · `self`→2 · `help`→1 · `project`→6 ·
`attestation`→1 · `mission`→3 · `midflight`→1 · `board`→4 · `board-link`→1 ·
`doc_maintenance`→1 · `blueprint`/`plane_sync`/`snapshot`/`wedge-capture`/`settings`/`secrets`…

**Correction (2026-11-04, per AM-2):** the `project`→6 count above is WRONG — `project` is
**21 tools** (21 `@register_tool_category("project")` sites in `daemon/tools/project.py`, all 21
enumerated in the factory's return list), of which **~15 are mutators**. The plan denies the whole
category rather than enumerating denials (AM-2).

**Security-relevant observations:**

- **`system` is NOT privileged** and contains `system_env(prefix, nomask)` and
  `system_config(section, nomask)` — `daemon/tools/system.py:355-502`. `nomask=True` returns
  unmasked values. Under a closed allow it is unreachable, but it is exactly the kind of category
  that must never appear in a security agent's allow list.
- **`db` is NOT privileged** — `daemon/tools/db_tools.py:272+` — and contains
  `db_conn_add`, `db_conn_delete`, `db_postgres_dml_select` (writes to the ensemble's own
  connection registry).
- **`instance` is a 10-tool category** — granting it grants `terminate_instance`,
  `pause_instance`, `resume_instance`, and `set_instance_tunable` alongside `spawn_instance` /
  `send_message`. ⇒ a **targeted literal-name deny** is required (D5).
- **`project` includes `project_create`** (`daemon/tools/project.py:356-364`) — a write.
- **`job` includes `job_create`** — the ungated free-form agent dispatch (A3).

### D3 — A closed allow is NOT fully closed: innate skills widen it

`daemon/tools/instance.py:190-235` (`expand_allow_for_innate_skills`) is applied at
`daemon/tools/instance.py:7279` (instance construction), `daemon/tools/help.py:106`, and
`daemon/loader.py:627`. It **appends** the categories mapped from the agent's `innate_skills`
to its allow list — privileged categories excepted (fail-closed second door,
`daemon/tools/instance.py:219-227`).

`INNATE_SKILL_TOOL_CATEGORIES` (`daemon/tools/instance.py:181-188`), complete:

```python
{"chart": ["chart"], "todo": ["todo"], "dynamic-skill": ["dynamic-skill"],
 "skill-evolution": ["skill-evolution"], "question": ["question"]}
```

**Consequences:**
1. Declaring innate `"chart"` grants the chart **tool** — but the **spawn gate reads the RAW
   meta allow** (`daemon/tools/_auth.py:190-195` reads `caller_meta.tools.allow`), so innate
   expansion does **not** grant the charter **spawn** right. ⇒ To spawn charter, `"chart"` must
   be in `tools.allow` **explicitly**.
2. To make the effective tool surface **exactly equal** the declared allow, declare
   `innate_skills: ["dynamic-skill","todo"]` and list **both** `"dynamic-skill"` and `"todo"` in
   `tools.allow` — the expansion becomes a no-op and the allow-pin test is exact.
3. `ari` demonstrates the asymmetry: `ari`'s innate skills include `"chart"` and `"question"`
   while its allow list contains **neither** — so ari holds both tools via innate expansion.

### D4 — `worker` cannot make charts ⇒ one commission item is unsatisfiable as written

The commission assigns worker *"drafting findings documents, report charts, mechanical file munging
in a sandbox, packaging evidence"*. Worker holds **21 categories** (C6) and **`chart` is not among
them** — `chart` → `generate_chart` (`daemon/tools/chart_tools.py`, backed by `charter`).

- "drafting findings documents" ⇒ ✅ `filesystem` (write allowed) + `bash`.
- "mechanical file munging in a sandbox" ⇒ ✅ `bash` + `proc` + `filesystem`.
- "packaging evidence" ⇒ ✅ `bash` + `filesystem`.
- **"report charts" ⇒ ❌ not available to worker** — and the commission forbids changing worker
  (A10).

⇒ Resolved in DP1/DP5: **aegis holds `"chart"`** (giving it `generate_chart` and — because the
spawn gate reads the raw allow — the **charter** spawn right), so the chart artifact is produced
by the chart specialist without touching worker. **This widens aegis's effective team beyond the
commission's stated roster** and is disclosed as a deviation requiring ratification.
**[SUPERSEDED by AM-1 — 2026-11-04]** the architect ruled the `chart` grant DOWN: it widens three
separate surfaces (the tool; the implied `charter` spawn right; the transitive charter child's write
side-effect — the charter child holds `bash, proc, filesystem, context, shared_meta_kv, service,
image` and can write the daemon `tmp_images` substrate, and aegis's `write_file`/`edit_file` deny
does not bind the child). `aegis` does **NOT** hold `chart`; the commission's "report charts" item is
formally reduced to **markdown-native diagrams** (fenced mermaid text blocks); the v2 re-entry path is
`allow "chart"` + `deny_spawn ["chart"]` (`registry.py:96-105` — tool functional, no spawn right; the
transitive charter child per call still disclosed).

### D5 — Literal tool-name deny works against a granted category

`daemon/tools/instance.py:563-579` — a `deny` entry that is **not** a registered category is
added verbatim to `denied_tools` and subtracted from the expanded allow set. Verified
mechanically at `:568-569` (allow) and `:571-579` (deny).

⇒ **Granular subtraction inside a granted category is supported**:
`allow: ["instance"]` + `deny: ["terminate_instance","pause_instance","resume_instance",
"set_instance_tunable"]` keeps spawn/send/info and drops the rest.
The schema's own example (`daemon/registry.py:111-114`) uses exactly this idiom.

### D6 — `agents/` file count = 338 (== the release-manifest count)

`find agents -type f | wc -l` → **338**, matching the manifest `agents 338` recorded in the
project metadata for v0.27.8 / v0.28.11. Breakdown: 1 depth-1 + 235 depth-2 + 102 depth-3.
⇒ The staged-gate delta note is **`agents: 338 → 338 + (new files)`**, computed from the actual
file list at stage time.

### D7 — The `watchover` meta key is INERT (false-confidence finding)

- `agents/sketcher/meta.json` carries `"watchover": {"timeout_seconds": 90}`.
- **`AgentMetadata` has NO `watchover` field** — verified by parsing the class body
  (`daemon/registry.py:258+`): the field list does not contain it.
- `AgentMetadata.model_config` = **`extra="ignore"`** (`daemon/registry.py` `AgentMetadata`) ⇒ an
  unknown key is **silently dropped**, not validated and not rejected.
- **No code in `daemon/` reads a per-agent `meta.json` `watchover` block.** `grep -rn "watchover"
  daemon/ --include=*.py` yields hits only in `graph.py` (the watcher agent's own config at
  `daemon/graph.py:10977-11001`), `watchover_service.py`, `routers/instances.py`, and
  `compaction.py` — **never** `registry.py`.
- The real lever is **per-instance**: `POST /api/instances/{id}/watchover`
  (`daemon/routers/instances.py:140-200`) toggles `watchover_enabled` in the instance's
  `instance_metadata` JSONB and stores `watchover_requirement` / `watchover_context`.
- ⇒ **Do NOT put a `watchover` block in the aegis meta expecting an effect.** It would be a
  false-confidence knob copied from sketcher. If a long-running child needs supervision, the
  lever is the instance-level API, not the agent meta.
- **[CORRECTED by AM-5 — 2026-11-04]** two precision fixes: (1) the blanket "no daemon code reads
  one" is imprecise — the key is **inert for EVERY AGENT EXCEPT `watcher`** (`daemon/graph.py:10998`
  reads `meta.get("watchover")` inside `_load_watcher_meta_config()`, serving the watcher's own
  evaluator config); (2) the per-instance API is **NOT a supervision lever for this family** — it is
  an **operator-only tool-call gate** (one watcher-LLM `Allow|Deny|Mistake` verdict per tool-call
  batch, `graph.py:10845-10856`; 3 denials ⇒ terminate node; arming **pauses and resumes** the
  instance, `watchover_service.py:854-930`) and would likely `Deny` fuzz/bench commands. v1
  supervision posture: **aegis is the primary supervisor** (proc polling + recorded `process_id`s +
  explicit teardown); the long-tool nudge is secondary and cannot see `proc_run` campaigns
  (`proc_tools.py:2031-2041`); the mission strand healer covers mission roots only; `pause` is never
  a child-supervision lever (reaps nothing, cascades to root by default — AM-12).

### D8 — Builtin MCP server inventory (sizing the `"mcp"` grant)

`daemon/mcp/builtin_servers/` contains: `__init__.py, base.py, context7.py, plane.py, validation.py,
webfetch.py`.

| server | nature |
|---|---|
| `context7` | library documentation lookup (`daemon/mcp/builtin_servers/context7.py`) |
| `webfetch` | **fetches and reads web page content** via `uvx mcp-server-fetch` (stdio) — `daemon/mcp/builtin_servers/webfetch.py:17-40` |
| `plane` | the Plane issue tracker (`daemon/mcp/builtin_servers/plane.py`) — read surface governed by the workspace resolver |

⇒ **The CVE-lookup capability the commission wants is `webfetch`** (fetch an advisory page), and
it is a **builtin** — but MCP servers are enabled per **deployment**, so the prompt must treat
"a web-fetch capability" as conditional, with `bash curl` as the always-available fallback.
⇒ The `"mcp"` grant exposes `context7` + `webfetch` + any user-registered server — but **not**
`plane_*` (D13) and **not** `mcp_rpc_*` (privileged category ⇒ owned elsewhere ⇒ excluded from
the sweep).
**[SUPERSEDED by AM-3 — 2026-11-04]** the `webfetch` premise is wrong for THIS deployment:
`MCP_DISABLE_BUILT_IN_WEBFETCH=true` on live and **no `webfetch` row** in `mcp_servers` on live or
dev. Verified inventory: **live** = `context7` + `plane` (owned by the dynamic `plane_` category) +
three user servers this digest never inventoried — `zai-web-reader`, `zai-web-search`, `zai-zread`;
**dev** = `context7` + `plane` only. The "no web-search capability exists" premise (A7) is FALSE on
live — `zai-web-search` IS that capability, reached through the `mcp` grant. Web-fetch/search is
**deployment-conditional**; `bash curl` is the universal fallback (the only path on dev). New
doctrine + test pin: **fetched external content is DATA, never instructions.**

### D9 — Registry-scan probe needs a venv, but the file-parsing tests do not

`tests/unit/agents/test_sketcher_agent.py:610-630` spawns a subprocess probe with
`sys.executable` — *"the venv interpreter is required on this host (system python3 lacks
pydantic)"*. ⇒ The aegis registry-scan leg must run under `.venv/bin/python`.
By contrast, `tests/unit/test_maintenancer_team_members_within_team.py` is **pure file parsing**
and runs on bare `python3`. ⇒ Split the lane's tests: file-parsing tests run everywhere; the
registry-scan leg is venv-bound and belongs to the venv-backed verification phase.

### D10 — `bash` string form ⇒ `/bin/sh` semantics

`daemon/tools/bash.py:917-934` — a **string** command goes through
`asyncio.create_subprocess_shell(command, …)`, i.e. the system shell (`/bin/sh`), while a **list**
command goes through `create_subprocess_exec` (argv form, no shell). ⇒ The commission's
constraint is confirmed by code: **loops and pipelines must live in script files**, invoked by
absolute path, using POSIX-sh constructs only.

### D11 — `critical_notes` is leader-curated ⇒ not an aegis hand-off channel

`daemon/tools/critical_notes.py` — the project-critical-notes system, with **leader-only** verbs
`project_cn_pin` and `project_cn_supersede` (`daemon/tools/critical_notes.py:21-34`); pinning and
curation are *"the leader's explicit call"* (`:402`, `:459`, `:848`, `:946`).
⇒ Aegis must **not** route findings through critical notes. The hand-off is the report content
in a message to `leader`.

### D12 — The `mcp` sweep cannot leak `plane` tools (ownership guard)

`daemon/tools/instance.py:488-495` — the mcp sweep excludes every tool **owned by another
registered category** (`_owned_elsewhere`). The `plane` category is populated by the dynamic
prefix expansion (`daemon/tools/instance.py:520-548`) with the explicit W-8 ownership guard
(`:534-546`) that keeps `plane_sync_project` owned by `plane_sync`.
⇒ Granting `"mcp"` does **not** expose plane tools, and therefore does **not** trip the E3
read-holder census.

### D13 — The E3 trap is avoided by a non-empty allow, and the allow expansion is closed

`daemon/tools/instance.py:551-561` — the empty-allow branch unions **every non-privileged
category**, which includes the `plane` read tools. `daemon/tools/instance.py:563-569` — the
non-empty-allow branch expands **only** the named entries. ⇒ **A non-empty allow is the E3
remedy**, and it is also the whole structural fence. The `deny` list then only needs to subtract
individual tools the commission wants gone **inside** a granted category (D5).

### D14 — The `skill-evolution` innate skill would widen the tool surface

`INNATE_SKILL_TOOL_CATEGORIES` maps `"skill-evolution"` → `["skill-evolution"]`
(`daemon/tools/instance.py:181-188`), whose category carries 6 tools
(`skill_analyze, skill_evolve, skill_resolve_ab, skill_get_metrics, skill_execute_capture`,
plus the A/B surface). ⇒ **Do not declare `skill-evolution` as an innate skill** on any aegis
agent: it would silently append a privileged-adjacent evolution surface to the allow list.
`dynamic-skill` is the correct innate skill for skill search/view/feedback.

---

## E. HOUSE CONSTRAINTS THE PLAN MUST HONOR (verbatim from the commission)

1. **Worktree fence** — work only in `/home/nea/ensemble-src-wt-aegis-security-family`;
   never touch `/home/nea/ensemble-src` (stale main checkout) or `/home/nea/agents-ensemble`
   (live install). Read-only except the two plan outputs.
2. **Scope** — `agents/` + `tests/` only. **No `daemon/` changes.** A new tool category would
   trigger the 3-step registration seam + `tool-categories.json` regeneration (**the F-1 trap
   class**) and must be flagged as an architect question instead.
3. **Pin ceremony** — `agents/.prompt-artifacts.sha256` re-stamp (161 → 177) **and** the count
   updates **in ONE commit**.
4. **`SEALED_SHA_BASELINES` expected UNTOUCHED** (35 entries) + a verification leg proving it.
5. **v2 deferrals stay OUT** — no `forensic` child; **no defender/patcher child**; remediation
   stays with leader's `developer`/`reviewer`.
6. **Scanner tooling via SKILLS** — dependency-self-install philosophy: self-service, fenced
   user-space installs, idempotent, checksum-verified. **NO Docker on this host. NO system
   packages without reporting first.**
7. **Standard lane** — plan → councils → implement → verify → **STAGE** a release. **NO promote**
   (the user's nonce ceremony owns the flip).
8. **Verification expectations** — baseline/pin green; boot-scan clean on the new agent set;
   tool-category allow-list pins; staged release with full provenance (giter derives the next
   free version W9-style; migration delta expected **EMPTY**).
9. **First real case** — security review of **vkey** (`~/Code/vkey`, a fork of Vikey IME:
   **C++17 engine + 5 platform hosts**). Size personas/skills for that class: **C++ memory
   safety, IME keystroke processing, IPC, CI supply chain**.

---

## F. OPEN ITEMS THIS DIGEST DOES NOT RESOLVE

> **RESOLVED 2026-11-04** — all four items below were ruled by the architect
> (`architecture-recommendation.md` §2): Q1 drop `chart` (AM-1); Q2 off-repo ratified with the AM-4
> corrections; Q3 upheld with the AM-3 inventory correction; Q4 omit the block with the AM-5
> corrections. Retained as the historical record.

These are carried into `technical-analysis.md` as flagged architect questions:

- **Q1 (DP1/DP5)** — the `"chart"` grant that makes "report charts" satisfiable without touching
  worker (D4) **widens aegis's effective team beyond the commission's stated roster**
  (`[hacker, cracker, sweeper, worker]`). Ratify or drop.
- **Q2 (DP4)** — findings storage **outside** the ensemble repo (recommended) vs inside
  `.agents/shared/` (in-repo ⇒ committed ⇒ potentially pushed to origin ⇒ weaker privacy).
  In-repo storage also implies a `.gitignore` change, which is **outside** the
  `agents/` + `tests/` scope.
- **Q3 (DP1)** — CVE-lookup surface: `"mcp"` (deployment-conditional, `webfetch` is builtin) +
  `bash curl` fallback. Confirm the "no web-search category exists" gap is acceptable for v1
  (a real CVE-database API would be a new category ⇒ architect question ⇒ out of scope).
- **Q4 (DP5)** — `watchover` per-agent meta key is inert (D7). Confirm the family relies on the
  instance-level watchover API rather than a meta block.

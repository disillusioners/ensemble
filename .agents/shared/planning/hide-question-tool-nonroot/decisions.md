# Decision Record: Hide `ask_questions` From Non-Root Instances

**Commission:** `hide-ask-questions-from-non-root-instances`
**Repository:** `/home/nea/ensemble-src` — branch `latest`, HEAD `1ebdf469f` (recorded 2026-10-20)
**Record scope:** the *why* behind decisions **P1–P11** — rationale, rejected alternatives, invariants, evidence.
**Implementation body:** [`plan-overview.md`](./plan-overview.md) (sibling worker). This file does **not** restate the plan; where a decision has an execution shape, this record points at the plan section that carries it (cited by heading name).

**How to read.** Each of P1–P11 is one section with five fixed blocks: **Decision** (the pinned shape), **Rationale** (with file:line evidence), **Rejected alternatives** (what was considered, why it was rejected, what it would have cost), **Invariants created** (hard rules later work must not break), **Verification** (the test ID from the plan's §11.1 `T1–T8` registry, or an existing pin test, plus the file:line seam). Evidence lines marked `UNVERIFIED` could not be confirmed in this environment and are flagged for implementation time.

> **Standing rule — P1–P11 are PINNED.** They are *recorded* here, not re-opened. Changing any of them is a **new commission with its own decision record**, not an edit to this file.

---

## §0. Test-ID registry (the plan's §11.1 contract, referenced from every *Verification* block)

| ID | Asserts | Plan section |
|---|---|---|
| **T1** | `ask_questions` is **absent from the built toolset** for a non-root (synthetic meta *allows* `question`, so absence cannot be blamed on the allow-filter) | §11.1 |
| **T2** | `ask_questions` **is present** for a root with `source_type` ∈ {discord, slack, telegram, whatsapp} | §11.1 |
| **T3** | `ask_questions` **absent** for a root with `source_type=None` (a job root is not a user-facing root) | §11.1 |
| **T4** | legacy `parent_id == ""` + chat source ⇒ **present**; `""`-parent + no source ⇒ **absent** | §11.1 |
| **T5** | `parent_id="p"` + `source_type="discord"` ⇒ **absent** (fail-closed: a child carrying a chat source is not a root) | §11.1 |
| **T6** | child report carrying "Open questions for the orchestrator" reaches the parent as a `HumanMessage` (report row → `PROCESS_REPORT` → `ReportInjection` → drain) | §11.1 |
| **T7** | parent `send_message` to a `COMPLETED` child takes the `enqueue-revive` branch and the child flips to `RUNNING` | §11.1 |
| **T8** | a **legacy** pack (created before the gate) is still answerable — RAM pack, durable shadow, rehydration, CAS; a root **without** the tool answers via `job_answer` | §11.1 |

---

## P1 — The root predicate

**Decision.** A *user-facing root* is `(parent_id IS NULL OR parent_id == "") AND (source_type ∈ {discord, slack, telegram, whatsapp})`. The predicate is evaluated **only from values threaded into the tool build** — never from a DB row read at registration time.

**Rationale.**

| Element | Verified source | Why this element |
|---|---|---|
| Root test idiom `(parent_id IS NULL) \| (parent_id == "")` | `daemon/repositories/instance/repository.py:1049-1050` | Legacy rows carry `""`; a bare `IS NULL` test would miss them |
| Chat-source set `("telegram","slack","discord","whatsapp")` | `daemon/repositories/instance/repository.py:860` (`SQLModelInstanceRepository.CHAT_SOURCE_TYPES`) | The instance-domain taxonomy |
| Same 4-member set, independently | `daemon/tools/upgrade_journal.py:3358-3360` (`USER_ORIGIN_CHAT_SOURCE_TYPES`) | Two independent taxonomies agree — no third set to reconcile |
| `source_type` writer | `daemon/services/instance_lifecycle.py:2389-2390` — `if source_type: instance_metadata["source_type"] = source_type` | **Exactly one** assignment into `instance_metadata` for this key (verified by grep across `daemon/`) |
| `original_source` | inherited by children — `daemon/services/instance_lifecycle.py:2446-2450` (`spawn_result.inherited_source`) | Inherited ⇒ a chat child would look like a root |
| `source_type` is **not** inherited | same writer, guarded by `if source_type:`; no inheritance path exists | Children never carry a chat `source_type` ⇒ the predicate cannot be fooled by inheritance |

**Why threading is mandatory, not stylistic.** At spawn, the tools are built at `instance_lifecycle.py:2137`; the instance row is INSERTed later at `:2433-2444` and committed at `:5379`. The `instance_mappings` row is created only *after* spawn returns (`daemon/sources/mapper.py:362` spawn → `:379` mapping insert). A row-reading or mapping-reading gate would therefore evaluate "no row" for **every freshly spawned chat root** and silently strip the tool from the exact instances the commission is meant to preserve.

**Rejected alternatives.**

| Alternative | Why rejected | Cost had it been chosen |
|---|---|---|
| Read the instance row at registration time | Row does not exist yet at `:2137` (INSERT `:2433`, commit `:5379`) | Silent breakage of the **primary** path — every new chat root loses the tool. Fails *visibly* as "non-roots denied", so the bug would ship looking correct |
| Read `instance_mappings` at registration time | Same timing defect; the mapping is also keyed per `(source_id, external_user_id)`, not per instance | Same silent primary-path breakage, plus a per-user/per-instance key mismatch |
| Use `original_source` instead of `source_type` | `original_source` **is** inherited (`instance_lifecycle.py:2446-2450`); live `ensemble_dev` rows carry `original_source` values `api` / `experience:*` / `explore:*` — never a chat platform | Every descendant of a chat root would satisfy the predicate ⇒ exactly the outcome the commission forbids |
| Grant to all roots by `parent_id` alone (no chat-source requirement) | Non-chat roots (webhook lane, API lane, internal roots) would keep the tool | The commission's premise — only a human-facing chat root can *ask the human* — is lost; the internal lanes gain a user-facing capability they cannot service |
| Adopt the 3-member `_PLATFORM_INSTRUCTIONS` key set instead | `_PLATFORM_INSTRUCTIONS` (`instance_lifecycle.py:1006-1036`) is a **prompt-formatting** whitelist (discord/slack/telegram); `whatsapp` has no adapter | A `whatsapp` root would be denied a tool it *should* have (it is a registered chat source per both taxonomies). Recorded asymmetry, **not** a re-litigation: a whatsapp root passes P1 but receives no platform prompt section — plan §1.4 |

**Invariants created.**
- `instance_metadata["source_type"]` has **exactly one writer** (`instance_lifecycle.py:2390`). A second writer is forbidden — that is precisely what P3 must avoid.
- `original_source` is **never** an input to any root/user-facing predicate.
- The root test is `(parent_id IS NULL OR parent_id == "")` — never a bare `IS NULL`.
- The chat-source set is the 4-member registry set; it is not the 3-member prompt-formatting set.

**Verification.** **T2** (root + chat source ⇒ present), **T3** (root, no source ⇒ absent), **T4** (legacy `""` parent), **T5** (child carrying a chat source ⇒ absent). Seams: `daemon/tools/instance.py:5895-5900` (gate), `daemon/repositories/instance/repository.py:860` + `:1049-1050` (predicate sources). Plan §1.1, §1.3, §2.2.

> **Evidence gap flagged.** The commission cites "all 14 chat instances are roots with 0 chat-source children" from live data. From this environment the `.env` points at `ensemble_dev` (127.0.0.1:5432), which holds **34 instances / 23 roots / 0 chat-source instances**; `ensemble_prod` is **not reachable** with the dev credential. The claim is therefore **UNVERIFIED — confirm at implementation time** against `ensemble_prod`. The predicate's correctness does not depend on it (it fails closed), but the *blast radius* estimate does.

---

## P2 — The gate: build-time hiding at `daemon/tools/instance.py:5895-5900`

**Decision.** Make the unconditional `create_question_tools(manager, current_instance_id, getattr(manager, "_live_hub", None))` call **conditional** on the P1 predicate; register **nothing** from the `question` category for non-roots. Thread keyword-only `parent_id` and `source_type` into `create_instance_tools` from **both** call sites: spawn (`instance_lifecycle.py:2137`; the values are locals of `spawn_instance` at `:1805` / `:1811`) and restore (`:4618`; `parent_id=meta.parent_id` at `:4603`, `source_type` read from the row at `:4595-4597`).

**Rationale.**

- The gate site is verified: `daemon/tools/instance.py:5895-5900` is the only production registration of the `question` category. `create_question_tools` is called from exactly one place in `daemon/` (`instance.py:5895`); the other call sites are tests (`tests/job_queue/test_midflight_qa.py:647,874`; `tests/test_question_tools.py:88`; `tests/test_question_tools_validation.py:131`).
- **Hiding beats refusing.** `_apply_tool_filter` runs at `instance.py:6238` — *after* the gate. A tool that is never appended is genuinely absent from the toolset; a tool that is appended and then refused still consumes a slot in the model's tool list, still appears in `KNOWN_TOOL_NAMES` and the help surface, and still tempts the model into calling it. The commission's requirement is *never registered*, not *refused at runtime*.
- **The precedent exists and is copyable.** `append_platform_context` (`instance_lifecycle.py:1039-1081`) implements exactly this shape: two pure gates over threaded values — Gate 1 `if parent_id is not None: return system_prompt` (`:1075`), Gate 2 `section = _PLATFORM_INSTRUCTIONS.get(source_type)` (`:1078`) — with the docstring stating the rule verbatim: *"callers are responsible for resolving `source_type` from the instance metadata BEFORE calling this appender — at spawn time the instance row has not been INSERTed yet, so a DB lookup here would return `None` and the feature would silently no-op."* (`:1053-1057`). Our gate is the same pattern with the same failure mode already documented.
- The restore path **must** be threaded too: on restart the tool list is rebuilt at `:4618` from the persisted row, so a spawn-only gate would re-expose the tool to every non-root after a daemon restart.

**Rejected alternatives.**

| Alternative | Why rejected | Cost had it been chosen |
|---|---|---|
| Runtime refusal — register the tool, reject the call | The tool still occupies the model's tool list, still shows in help/`KNOWN_TOOL_NAMES` (`_tool_registry.py:916`), and the model still emits a doomed call. Also the requirement is *never registered* | Capability leak in the prompt surface + every non-root burns a turn on a tool that always errors. Not the requested behaviour |
| Gate inside `create_question_tools` | Its signature is **3 positional args** — `create_question_tools(manager, current_instance_id, live_event_hub=None)` (`daemon/tools/question_tools.py:405-409`) — with **no** `parent_id`/`source_type` channel; the factory is also called directly by tests | Requires changing the factory signature (breaking 4+ test call sites) and turns a *registration* concern into a *tool-body* concern; the tool would still be registered and visible |
| Add `"question"` to `PRIVILEGED_TOOL_CATEGORIES` | It is an **exact-set** frozen set with **5 pins** (below). Also privilege means *default-deny*, i.e. the tool would be denied to **everyone** including the root we want to keep it | Breaks all 5 pins in one PR (each pin demands a same-PR constants update with adjudication), and even then the grant would have to be re-opened per agent — the privileged path grants by *explicit* `tools.allow` only, and the empty-allow universe is denied, so the root-scoped semantics get *worse*, not better |
| Remove `"question"` from `CATEGORY_MODULES` | `daemon/tools/_tool_registry.py:729` | The boot validator (`daemon/registry.py:1393` for allow entries, `:1405` for deny entries) would warn **on every boot** for `leader`, `devops`, `project-manager` — and `project-manager` *denies* `question` (`agents/project-manager/meta.json:64`), so a removed category turns its legitimate deny entry into a warning too |
| Gate on the `source_type` **row read** at registration | See P1 — row does not exist at spawn | Silent primary-path breakage |
| Gate on `original_source` | Inherited by children | Grandchildren of a chat root keep the tool |

**The five exact-set pins** (all verified; a `question` addition trips every one — "silent additions and silent removals … both trip the pins", `_tool_registry.py:234-238`):

| # | File:line | Assertion |
|---|---|---|
| 1 | `tests/unit/tools/test_upgrade_registration.py:102` (method), `:142` (assert) | `PRIVILEGED_TOOL_CATEGORIES == frozenset({…})` — exactly eight entries |
| 2 | `tests/unit/tools/test_attestation_registration.py:142` (method), `:186` (assert) | same exact-set assert |
| 3 | `tests/integration/test_maintenancer_spawn_resolves_tools.py:288` (class docstring), `:324` (assert) | same exact-set assert |
| 4 | `tests/integration/test_service_tool_flag_off_byte_identical.py:808` (method), `:823` (assert) | same exact-set assert |
| 5 | `tests/unit/tools/test_secrets_registration.py:218` (assert) | same exact-set assert |

**Invariants created.**
- `PRIVILEGED_TOOL_CATEGORIES` is a **frozen exact set of eight** (`system_upgrade`, `system-log`, `ens-db`, `ens-env`, `view-views`, `cu`, `secrets`, `settings`) — `_tool_registry.py:239-339`. No add, no remove, without a same-PR five-pin update.
- `"question"` **stays** in `CATEGORY_MODULES` (`_tool_registry.py:729`); `"midflight"` stays at `:732`.
- `daemon/tools/question_tools.py` source is **not** deleted, renamed, or re-categorised — `tests/unit/tools/test_frozen_tool_name_discovery.py:223` requires `KNOWN_TOOL_NAMES` to equal the source-discovered set exactly.
- The gate is a **build-time conditional on threaded values** — never a runtime check, never a DB read.
- `create_question_tools` keeps its **3-argument** signature.
- Both `create_instance_tools` call sites (`:2137`, `:4618`) thread the same two keyword-only arguments.

**Verification.** **T1** (absence, allow-filter-neutral), **T2**–**T5** (predicate matrix), **T3** explicitly covers the job-root case. Seam: `daemon/tools/instance.py:5895-5900`; filter-ordering proof `:6238`; `create_question_tools` signature `daemon/tools/question_tools.py:405-409`. Plan §2.1, §2.2, §2.3, §2.5.

---

## P3 — The mappings lane (in scope)

**Decision.** `daemon/routers/mappings.py:113-137` spawns a **chat-bound root with no `source_type`** (verified: `manager.spawn_instance(agent_id=…, instance_id=…)` at `:114-117` — no `source_type` argument) → a false negative under P1. Fix by **threading the source type into the existing single writer** (`instance_lifecycle.py:2390`) — never by adding a second metadata writer. Keep the fix additive and minimal.

**Rationale.** The mappings route is the HTTP path that binds a chat source to an agent root. Without `source_type`, the spawned root's `instance_metadata` carries no chat marker, so the P1 predicate evaluates false and the root would lose `ask_questions` — the exact population the commission exists to preserve. Threading into the existing writer preserves the single-writer invariant and keeps the fix to one keyword argument at one call site.

**The live chat-root lane already threads it — verified, so the fix is narrow.** The production chat path is `daemon/sources/registry.py:1029-1035` → `mapper.get_or_create_instance(..., source_type=source_type)` → `daemon/sources/mapper.py:362-365` → `spawn_instance_with_mcp(..., source_type=source_type)` → `spawn_instance(source_type=…)` → `instance_lifecycle.py:2390`. The adapter's `source_type` reaches it from `MessageSourceAdapter.source_type` (`daemon/sources/base.py:159-160`). **The only lanes that do *not* thread it are the mappings router (`:114-117`) and `mapper.handle_incoming_message` (`mapper.py:452`, which has **no production caller** — only `tests/test_sources_mapper.py`).

**Rejected alternatives.**

| Alternative | Why rejected | Cost had it been chosen |
|---|---|---|
| Add a **second** metadata writer for `source_type` in the mappings lane | Two writers ⇒ the key can diverge (one set, one not) depending on lane. The whole P1 evaluation rule rests on there being one | A silent per-lane inconsistency: a mappings-created root and a registry-created root would carry different metadata for the same logical thing. The predicate would then be lane-dependent |
| Make the mappings lane's root a root by **grant** (e.g. `tools.allow` on the agent) instead of by predicate | The P2 gate strips the tool for **non-roots** regardless of allow-lists (the gate runs before `_apply_tool_filter` at `:6238`) | `tools.allow` cannot re-admit a tool the gate never registered ⇒ the fix would be inert, and the agent's allow-list would lie about its actual capability |
| Widen the predicate to "any root whose mapping names a chat source" | Reintroduces the mapping read (undecidable at spawn — P1) | Same timing defect, plus the mapping is per-user |
| Leave the mappings lane broken (out of scope) | The commission's own premise — "roots still ask" — fails for the mappings lane | A silent functional gap: mappings-created chat roots quietly lose the tool, and the failure looks like "the gate is too strict" |

**Invariants created.**
- `source_type` has **one** writer — `instance_lifecycle.py:2390` — for **every** spawn lane, including the mappings lane.
- The mappings-lane fix is **additive**: one threaded keyword at the existing call site; no new writer, no new metadata key.
- The set of lanes that can produce a user-facing root is closed: registry/mapper lane (already threaded), mappings router lane (P3 fix), restore path (reads the persisted value).

**Verification.** Plan §3. **T2** covers the predicate's positive branch; the mappings-lane case is a dedicated new assertion in the plan's T-series (plan §11.1 / §3). Cross-check seam: `daemon/routers/mappings.py:114-117` vs `daemon/sources/mapper.py:362-365` vs `daemon/sources/registry.py:1029-1035`.

---

## P4 — `meta.json` plan (defense in depth)

**Decision.** `agents/leader/meta.json` — remove `"question"` from `tools.allow` (`:15`) **and** from `innate_skills` (`:8`). `agents/devops/meta.json` — same, `tools.allow` (`:12`) and `innate_skills` (`:8`). `agents/ari/meta.json` — **add** `"question"` to `tools.allow` **and** `innate_skills` (ari holds neither today). `agents/project-manager/meta.json:64` — **unchanged** (the entry is inside the `deny` array, already compliant). `agents/critic/meta.json` — **unchanged**. `agents/watcher/meta.json` — empty `allow` list ⇒ default-open universe ⇒ it **currently holds** `ask_questions`; the gate strips it; pinning the allow-list is optional hygiene.

**Rationale.** Every entry below was read from the files:

| Agent | Verified state | Effect |
|---|---|---|
| `leader` | `innate_skills` (`:8`) = `["coordination","chart","todo","question"]`; `tools.allow` (`:14-15`) contains `"question"` | Two independent doors open — both must close |
| `devops` | `innate_skills` (`:8`) = `["dynamic-skill","chart","question","todo"]`; `tools.allow` (`:12`) contains `"question"` | Same |
| `ari` | `innate_skills` (`:9`) = `["job-orchestration","chart","todo"]`; `tools.allow` (`:11-38`) — **no** `"question"`; `deny` (`:39`) | Holds **neither** today ⇒ the grant is a real add |
| `project-manager` | `deny` array opens at `:54`; `"question"` at `:64` | Already compliant — **no edit** |
| `critic` | `allow` (`:13`) = `["read_file","image","design"]`; `deny` (`:14`) | Never held it — **no edit** |
| `watcher` | `allow` (`:22`) = `[]` | Empty allow ⇒ **default-open universe** ⇒ it holds the tool today |

**Why both doors matter, mechanically.** `INNATE_SKILL_TOOL_CATEGORIES` (`daemon/tools/instance.py:158-164`) maps `"question" → ["question"]` (`:163`), and `expand_allow_for_innate_skills` (`:167+`) appends those categories to the allow list — except privileged ones, and `"question"` is **not** privileged (`_tool_registry.py:239-339`). So an `innate_skills` entry alone is sufficient to grant the category; a `tools.allow` entry is a second, independent grant. Removing only one leaves the door open. This is why P4 removes from **both** lists on leader and devops.

**The `meta.json` edits are defense-in-depth, not the control.** The authoritative control is the P2 gate (plan §2.1, §2.6): with the gate in force, `tools.allow` cannot re-admit a tool that was never registered.

**Rejected alternatives.**

| Alternative | Why rejected | Cost had it been chosen |
|---|---|---|
| Edit `meta.json` only; skip the code gate | `watcher`'s empty allow ⇒ default-open universe ⇒ it holds the tool with no `meta.json` change at all; and any future agent with an empty allow inherits it | The capability keeps leaking into the default-open universe — the exact population the commission targets |
| Gate only; skip `meta.json` | The prompt surface still tells `leader`/`devops` they have a question skill and a question tool they no longer have | Prompt/behavior drift: agents would try to call a tool that is absent — a hallucinated-call loop rather than a clean absence |
| Grant `"question"` to `ari` in `tools.allow` **only** (no `innate_skills`) | The skill text would not land in ari's prompt ⇒ ari gets the tool with no teaching, and the root-scoped skill rewrite (P5) would never reach the only agent that needs it | The chat root acquires a pause-and-answer capability it has no instructions for — the highest-risk population (R1 in the plan's risk register) |
| Edit `project-manager/meta.json` "for symmetry" | Its `"question"` is inside the **deny** array — already correct | A no-op edit churns a file that needs no change, and risks flipping deny→allow by accident |
| Pin `watcher`'s allow-list as part of this commission | Optional hygiene; the gate already strips the tool | Scope creep into an unrelated agent's capability surface, with its own regression surface (watcher currently *relies* on the default-open universe) |

**Invariants created.**
- `leader` and `devops` hold **neither** `"question"` in `tools.allow` **nor** in `innate_skills` after this commission.
- `ari` holds `"question"` in **both** `tools.allow` and `innate_skills` (the grant is deliberately doubled — capability + teaching).
- `project-manager`'s `"question"` **deny** entry is preserved verbatim.
- `critic/meta.json` is untouched.
- `meta.json` is **not** covered by the prompt-artifact baseline (`daemon/tools/prompt_artifacts.py:119-140` covers depth-2 `agents/<agent>/<artifact>.md` only; `agents/.prompt-artifacts.sha256` has **0** `meta.json` entries) ⇒ **no** baseline regeneration is required for meta.json edits alone.

**Verification.** Plan §4, §10.3. Existing pins that move: `tests/test_innate_skills_refactoring.py:67` expects `leader` `innate_skills == ["coordination","chart","todo"]` — currently **RED** (leader still lists `question`), turns **GREEN** after P4. `tests/unit/test_devops_agent.py:165` expects `devops` `innate_skills == ["dynamic-skill","todo"]` — currently **RED** (devops carries `chart` **and** `question`); it **stays RED** unless the expectation is updated to `["dynamic-skill","chart","todo"]` (dropping `chart` is out of scope). A new pin must assert the ari grant and the leader/devops closure.

---

## P5 — Root-scoped skill rewrite

**Decision.** Rewrite `agents/_prompt_system/innate-skills/question/skill.md` so it teaches **root behaviour only** ("you may ask the human when you are the user-facing root"), plus the explicit **chat-lane nuance**: in a chat lane the user replies in normal chat, so **prefer asking in the reply text**; use the tool only when a structured answer channel exists. Also state the **non-root half**: if you have no `ask_questions` tool you are a non-root — decide per your own mandate and, if genuinely blocked, end your report with "Open questions for the orchestrator".

**Rationale.** The skill is a **single shared file** injected by name — `daemon/loader.py:841-847` (`_resolve_innate_skill_paths` resolves `agents/_prompt_system/innate-skills/<name>/skill.md` for each entry in `meta.innate_skills`). The current file is 53 lines and teaches the *unconditional* use of the tool ("`ask_questions()` is your direct line to the user. Ask whenever you need a decision…"), which becomes **wrong** for every non-root after P2/P4. The teaching home must match the new capability boundary. Because the file is shared, one rewrite fixes every holder — leader and devops stop receiving it when their `innate_skills` entry is removed (P4), and ari starts receiving it when its entry is added.

**Chat-lane nuance is load-bearing, not stylistic.** The chat lane delivers the user's reply as a **normal chat message**, not as a structured answer to a paused pack. A root that pauses for an answer in a chat lane can strand the turn. Hence: prefer the reply text; use the tool only where a structured answer channel exists.

**Rejected alternatives.**

| Alternative | Why rejected | Cost had it been chosen |
|---|---|---|
| A **new** shared innate-skill directory (e.g. `innate-skills/root-question/`) | Duplicates the skill; the loader resolves by **name** (`loader.py:841-847`) ⇒ two files teaching the same capability, and every future agent would have to pick one. Also: an empty `innate_skills: []` is treated as *absent* and falls back to a legacy `skills/` scan (`loader.py:856-858`) — a near-empty list is a trap | Two homes for one contract; drift between them; agents that declare neither get **no** question teaching at all (the missing-directory case is only a WARN — `loader.py:868`) |
| Edit the existing skill **in place** *and* keep per-agent copies | The whole point of the centralised `innate-skills/` registry is one canonical copy per capability | N copies × N drift vectors; the canonical-home convention (`docs/agent-prompt-writing-guide.md`) is violated |
| Delete the skill file and teach only via the report contract | Non-roots need to know *why* they have no tool; and the root needs the tool's mechanics (pause/resume, one-pack-at-a-time, `options`/`allow_custom` shape) | The tool's operating rules survive only in the tool docstring; agents that hold the tool but were never taught it will misuse it |
| Teach only the root half, leave the non-root half implicit | The non-root half is the *behavioural* half of this commission — "ask is never your turn-ending move" | Non-roots would still treat the missing tool as an outage and stall rather than reporting |

**Invariants created.**
- `agents/_prompt_system/innate-skills/question/skill.md` is the **single canonical home** for the question contract across all agents.
- The skill states the root-only capability boundary explicitly, and states the non-root fallback ("Open questions for the orchestrator").
- The skill carries the chat-lane preference ("prefer asking in your reply text").
- `innate_skills` is **never** set to `[]` to suppress a skill — an empty array is treated as absent (`loader.py:856-858`) and triggers the legacy scan.
- `agents/_prompt_system/**` is **depth ≥ 3** ⇒ **not** covered by the prompt-artifact baseline (`prompt_artifacts.py:257`, `len(parts) != 2`) ⇒ no baseline regeneration for this file.

**Verification.** Plan §5, §5.1, §10.4. New assertions per plan §11.1 (T-series, prompt-contract class). Seams: `daemon/loader.py:841-847` (resolution), `:868` (missing-dir WARN), `agents/_prompt_system/innate-skills/question/skill.md` (the file). Note `agents/ari/workflow.md` and `agents/ari/rule.md` are **sealed** — the ari `*.md` files stay untouched; the grant is `meta.json`-only (P4).

---

## P6 — Report contract: "Open questions for the orchestrator"

**Decision.** Canonical section title **"Open questions for the orchestrator"** (the user's verbatim phrasing). Include it **only** when genuinely blocked — **omit it entirely** otherwise. Cap **0–3** framed questions (project-manager precedent: `agents/project-manager/soul.md:58-59` and `:78-79` both read `## Decisions Pending` / `<0–3 framed questions>`). Place it **immediately after** the one-line report summary. Non-roots must prefer deciding per their own mandate; **"ask" is never the turn-ending move** for them.

**Rationale.** The report body is free text, and the **combination step caps it**: `_combine_messages` (`daemon/services/child_reports.py:1538`) truncates at `MAX_COMBINED_REPORT_CHARS` (10 000) and appends `…[truncated]…` (`:1563-1565`). A section placed at the **end** of the report can therefore be **cut off** — the parent would receive a report whose blockers silently vanished. Placing it immediately after the summary puts it inside the guaranteed-surviving prefix.

**Precedent for the cap.** `agents/project-manager/soul.md` already establishes "0–3 framed questions" as the house shape for pending decisions (`:58-59` report block, `:78-79` roadmap block). Reusing it keeps one idiom across the tree instead of inventing a second.

**Rejected alternatives.**

| Alternative | Why rejected | Cost had it been chosen |
|---|---|---|
| Move the section to the **END** of the report | Truncation risk at 10 000 chars (`child_reports.py:1538` + `:1563-1565`) — the blockers are the *last* thing to be cut, i.e. the most likely thing to be cut | A blocked child whose report is long would report **no** questions, and the parent would read "done" — the failure mode is invisible |
| Always include the section (even when empty) | Adds noise to every report; trains the parent to ignore a section that is usually empty | Report bloat; parents desensitise to the section ⇒ a real question is as likely to be ignored as an empty one |
| Unbounded question count | The parent's own context and attention are the scarce resource; the report is drained into the parent's turn as a `HumanMessage` | Parent overload; the 0–3 cap is the existing house rule (`project-manager/soul.md:58-59`) |
| A different section title (e.g. "Questions for the parent") | The title is the **user's verbatim phrasing**; it is the string the parent-side contract (§P7) and the prompt sweep will match on | Cross-agent string drift; a parent that greps for one title misses the other |
| Keep "ask" as the non-root's turn-ending move | The commission's premise is that non-roots **cannot** reach the human; an "ask" turn-end strands the child | A child that ends on "ask" has neither delivered a report nor raised a pack ⇒ the parent never learns ⇒ the run hangs |
| A structured section (YAML/JSON block) in the report | The report body is free text drained verbatim into the parent's turn (`daemon/graph.py:8721-8745`); a parser would have to live in the drain path | A new parser in a load-bearing path (the report drain) for zero benefit — the parent is an LLM, not a JSON parser |

**Invariants created.**
- The section title is exactly `Open questions for the orchestrator` — one string, one spelling, used by every prompt that references it.
- The section appears **only** when the non-root is genuinely blocked.
- The section holds **0–3** framed questions.
- The section is placed **immediately after** the one-line report summary — never at the tail.
- Prompt cross-references to the section use the **section name only** (no bare `.md` / bare `agents/` tokens — `tests/unit/tools/test_prompt_section_reference_integrity.py`).

**Verification.** Plan §6, §10.4, §10.5. **T6** covers the delivery path (report row → `PROCESS_REPORT` → `ReportInjection` → drain into the parent as a `HumanMessage`; seams `child_reports.py:3882-3883`, `:3985`, `:4152-4158`, `daemon/graph.py:8721-8745`).

---

## P7 — Parent-side contract

**Decision.** Leaders **receiving** an open-question section should usually **answer it themselves** (TrueAuto authority) and instruct the child through the **existing continuation path** (`send_message` to the child instance). Escalate to the user **only** when genuinely user-critical: a **non-root** leader escalates through **its own** report chain to a root (never "ask the user" wording); a **root** uses its own tool.

**Rationale — the continuation path already exists; no new machinery.**

| Step | Seam |
|---|---|
| Child completion → parent notified | `daemon/services/child_reports.py:2517` `_process_child_completion_and_notify_parent` |
| Report row | `child_reports.py:3882-3883` — `source=f"internal_report:{instance.instance_id}:{completed_message_id}"`, `type=MessageType.COMPLETION_REPORT.value` |
| `PROCESS_REPORT` task | `child_reports.py:3985` — `task_type=TaskType.PROCESS_REPORT.value` |
| `ReportInjection` row | `child_reports.py:4152-4158` — `ReportInjection(..., content=last_content)` |
| Drained into the parent | `daemon/graph.py:8721-8745` — `report_injection_slot.drain(instance_id)` → `HumanMessage` |
| Parent → child continuation | `daemon/tools/instance.py:1316` `_route_send_message`; terminal branch `:1418-1424` → `("enqueue-revive", prior_status)` |
| Child revived | `daemon/services/instance_messaging.py:2369-2375` (`is_terminal_revival` set: COMPLETED / TERMINATED / ERROR / FAILED) → `:2379` `instance.status = InstanceStatus.RUNNING.value` |

The parent-side contract therefore requires **no new machinery** — only a prompt-level instruction that the parent *uses* the path it already has.

**Rejected alternatives.**

| Alternative | Why rejected | Cost had it been chosen |
|---|---|---|
| Build a **new** parent→child question/answer channel | The report + `send_message` + revive path already carries the round trip; a new channel duplicates it | Two ways to answer a child ⇒ two sets of semantics, two sets of bugs, and the new one is untested while the old one is production-proven |
| Non-root leaders escalate to the user directly | A non-root has no user-facing tool after P2 (that is the whole point) — the escalation would have no carrier | The escalation would either be dropped or would require the very tool the commission removes |
| Parents answer *every* open question automatically (no escalation) | Some questions are genuinely user-critical (a credential, a business decision); a parent guessing would be wrong | Silent wrong answers to user-critical questions; the parent would fabricate an authority it does not have |
| The child waits for the parent's answer *in place* (no revive) | The child has ended its turn; a paused-in-place wait is the pause machinery the commission is removing from non-roots | Deadlock: the child holds no runnable state, the parent has no message to act on |

**Invariants created.**
- The parent-side answer path is the **existing** report + `send_message` + revive path. No parallel channel is introduced.
- Escalation wording: a non-root leader **never** says "ask the user"; it routes upward through its own report chain. A root uses its own tool.
- "Ask the user" phrasing is reserved for roots.

**Verification.** Plan §7, §7.1, §7.2, §10.6. **T7** covers the revive branch (`instance.py:1418-1424` → `instance_messaging.py:2369-2381`). **T6** covers the delivery path.

---

## P8 — Mid-flight coexists (no change)

**Decision.** `midflight` (`daemon/tools/_tool_registry.py:732`) is **non-blocking progress/decision surfacing during a turn**; the open-questions section is a **terminal report section**. They are **not substitutes**. **No midflight changes.**

**Rationale.** They differ in **when** and **what**: midflight fires *inside* a turn to unblock progress without ending it; the open-questions section fires *at the end* of a turn to hand a blocker to the parent. A child that has a real blocker mid-turn has two legitimate channels — report it midflight (keep working on what it can) and/or surface it in the terminal section (hand it over). Collapsing them would either force a turn-end for a non-blocking note, or bury a real blocker in a progress stream the parent may never read.

**Rejected alternatives.**

| Alternative | Why rejected | Cost had it been chosen |
|---|---|---|
| Make midflight the blocker channel for non-roots | Midflight is explicitly **non-blocking**; a blocker that needs a decision is, by definition, blocking | A non-root would emit a midflight and then **wait** — with nothing waiting on it. The parent may never read the midflight stream ⇒ the blocker is lost |
| Make the open-questions section a midflight event | A terminal section is the only thing guaranteed to reach the parent as a `HumanMessage` (`graph.py:8721-8745`); midflight rides the push lanes (EventBus / LiveEventHub / work_notifier) | The blocker would arrive on a lane with no guaranteed consumer ⇒ silent loss |
| Remove midflight entirely | It serves a different purpose (progress visibility) and is a separate, opt-in category | Loss of an unrelated capability; blast radius far outside this commission |

**Invariants created.**
- `"midflight"` stays in `CATEGORY_MODULES` (`_tool_registry.py:732`) and midflight's tool surface is untouched.
- The open-questions channel is the **terminal report section**; midflight is the **in-turn** channel. Neither is a substitute for the other.

**Verification.** Plan §8. No code change ⇒ no new test; the plan's §11.2 re-run of the existing suite is the check (no midflight regression).

---

## P9 — Defense in depth stays intact

**Decision.** The v0.25.5 answer-routing fix (`daemon/services/answer_resolver.py`, `resolve_answer_target` at `:642`), the pack shadow (`daemon/services/midflight_qa.py:391` `stamp_question_pack_metadata` / `:426` `clear_question_pack_metadata`), boot rehydration (`daemon/api.py:338` `_boot_rehydrate_question_packs`; `question_manager.rehydrate_from_payloads` at `:408`), and the answer CAS (`question_manager.set_answers` at `:321-373`) must remain **fully intact**. A **legacy pack created before the gate must still be answerable**, and a **root without the tool** must still be able to answer a child's legacy pack via `job_answer` (category `job`).

**Rationale.** The gate removes a **creation** path, not an **answering** path. The only production pack writer is `daemon/tools/question_tools.py:502` (`manager._question_manager.set_question_pack(...)`) — so a pack can no longer be *created* by a non-root, but a pack that **already exists** (created before the gate shipped, or by a root) must remain answerable through every downstream seam. The answering path is independent of tool registration:

| Seam | Verified |
|---|---|
| RAM pack store | `question_manager.py:206` (`class QuestionManager`), `:224-225` (`_packs`, `_lock`), `:228` (`set_question_pack`) |
| Answer CAS | `question_manager.py:321-373` (`set_answers` → `(pack, transitioned)`) |
| Boot rehydration | `question_manager.py:408` (`rehydrate_from_payloads`); original `pack_id` preserved at `:487-492` (else the `QUESTION_PACK_MISMATCH` guard breaks) |
| Durable shadow stamp / clear | `midflight_qa.py:391` / `:426` |
| Boot hook | `api.py:338` `_boot_rehydrate_question_packs` |
| Instance-addressed answer | `daemon/routers/answer_helper.py:262-282` — RAM miss ⇒ one-shot rehydration from the durable shadow; `rehydrate_from_payloads` called at `:280` |
| Job-scoped answer routing | `answer_resolver.py:642` `resolve_answer_target` |
| Job-scoped answer tool | `daemon/tools/job_queue.py:620` (`job_answer`), `daemon/tools/_tool_registry.py:981` (`"job_answer"` in `KNOWN_TOOL_NAMES`) |

**Rejected alternatives.**

| Alternative | Why rejected | Cost had it been chosen |
|---|---|---|
| Disable/remove the pack machinery alongside the tool | The tool is only a *creation* convenience; the answering path is how the UI/API, the job lane, and every legacy in-flight pack operate | Every pending pack at cut-over becomes unanswerable ⇒ user-visible stall on live runs; the FE answer endpoints stop working |
| Gate the **answer** path on root-ness | Answers come from users and from the job lane — not from the instance that asked | A root's answer to a child's legacy pack would be refused ⇒ the exact orphan the migration is meant to avoid |
| Have the gate also strip `job_answer` | `job_answer` is the **fallback** answering surface for a root that lacks `ask_questions` | The fallback disappears precisely when it is needed |
| Migrate/deletes legacy packs at cut-over | A migration on live in-flight packs is a data mutation for no benefit — the answering path already tolerates them (shadow + rehydration) | A schema/data change on live packs for zero functional gain; and it would violate the P11 **empty-migration-delta** premise |

**Invariants created.**
- The `question` category's **answering** surface is unchanged by this commission: `set_answers` CAS, the durable shadow, boot rehydration, `resolve_answer_target`, `job_answer` and the instance-addressed answer route all stay as-is.
- `daemon/tools/question_tools.py` remains the **only** production pack writer; its source is never deleted (also required by `tests/unit/tools/test_frozen_tool_name_discovery.py:223`).
- The original `pack_id` must survive rehydration (`question_manager.py:487-492`).
- **No** schema migration may be added for this commission (keeps the P11 delta empty).

**Verification.** Plan §9, §15. **T8** is the dedicated test: legacy pack answerable end-to-end (RAM + shadow + rehydration + CAS) and `job_answer` resolving a pack with **no** `ask_questions` tool in play. Existing packs to re-run: `tests/job_queue/test_midflight_qa.py`, `tests/test_question_manager.py`, `tests/job_queue/test_job_answer_tool.py`, `tests/test_question_pack_boot_rehydrate.py`.

---

## P10 — Sequencing

**Decision.** Implement **only after** the sibling commission `ffeb7190` ("question pause/resume asymmetry" — JOB id `ffeb7190-df9e-4d96-bda2-be95748226db`, branch `fix/question-pause-resume-asymmetry`, worktree `/home/nea/ensemble-src-wt-qprasym`) merges into `latest`. Their scope: `daemon/services/question_manager.py`, `daemon/routers/answer_helper.py`, `daemon/services/instance_messaging.py`, `daemon/services/instance_lifecycle.py`. Overlap with us: `instance_lifecycle.py` param threading at `:2137` / `:4618` — keep **additive and minimal**. Disjoint: `daemon/tools/instance.py:5895`. Their worktree must **not** be touched. Branch for this work: `feature/hide-question-tool-nonroot`, branched from `latest` **after** their merge. Coordination artifacts: KV census row `wt.active.feature/hide-question-tool-nonroot.<task-id>` written **before** spawning giter; KV claim row `wt.claim.hide-question-tool-nonroot` — **merge, do not clobber** the leader's `overlap_files` / `blocked_by` keys. Plan artifacts live in `.agents/shared/planning/hide-question-tool-nonroot/`.

**Rationale.** The sibling rewrites `question_manager.py`, `answer_helper.py`, `instance_messaging.py` and `instance_lifecycle.py` — three of the four files P9's "defense in depth" invariant depends on, and the exact file we must thread two keyword arguments through. Concurrent edits to `instance_lifecycle.py` produce either a merge conflict inside a load-bearing lifecycle function or a silent semantic clash between "the pause is a no-op for a non-root" and "the pause resumes a legacy pack". Their worktree also carries **uncommitted** work (below), so branching before their merge would fork a half-finished pause-asymmetry design.

**Verified state at authoring (2026-10-20).**

| Fact | Value | How verified |
|---|---|---|
| Sibling branch | `fix/question-pause-resume-asymmetry` | `git -C /home/nea/ensemble-src-wt-qprasym branch --show-current` |
| Sibling worktree HEAD | `1ebdf469f` — **same commit as `latest`** | `git log --oneline -2` in the worktree |
| Commits ahead of `latest` | **0** ⇒ not merged | `git rev-list --count origin/latest..HEAD` → `0` |
| Worktree cleanliness | **NOT clean** — 7 modified tracked files (`daemon/api.py`, `daemon/config.py`, `daemon/repositories/instance/repository.py`, `daemon/routers/answer_helper.py`, `daemon/routers/instances.py`, `daemon/services/instance_lifecycle.py`, `daemon/services/pool_orchestrator.py`), 2 new untracked services (`daemon/services/question_pause_set.py`, `daemon/services/stale_pause_sweep.py`), 1 new untracked test (`tests/unit/test_question_pause_resume_asymmetry.py`); diffstat `7 files changed, 311 insertions(+), 1 deletion(-)` | `git status --short` + `git diff --stat` |

> **Correction to the commission brief.** The brief and the KV claim row describe the sibling worktree as *"0 commits ahead of latest, clean"*. It is **0 commits ahead but NOT clean** — there is substantial uncommitted work in flight, including edits to `daemon/services/instance_lifecycle.py` (the very file we must thread through) and a **new** `daemon/services/question_pause_set.py` + `daemon/services/stale_pause_sweep.py`. This **strengthens** the gate: the sibling is further from merge than the brief assumed, and the plan's §12.1 characterisation ("one untracked file") understates the delta. The gate is unchanged; the wait is longer.

**Rejected alternatives.**

| Alternative | Why rejected | Cost had it been chosen |
|---|---|---|
| Implement in parallel on a branch from `latest` now | The sibling's `instance_lifecycle.py` rewrite is uncommitted and unmerged; our two keyword arguments would land in a file that is about to change | A merge conflict inside `spawn_instance` / `_restore_instance`, resolved under time pressure, with a silent risk of dropping one of the two threaded values (⇒ a non-root silently keeps the tool, or a chat root silently loses it) |
| Implement in the sibling's worktree | The commission forbids touching it | Destroying in-flight sibling work |
| Merge our branch first "to be ready" | Reverses the dependency and pushes the merge conflict onto the sibling | Same conflict, wrong direction — and it makes the sibling's author resolve our param threading without the gate's context |
| Skip the gate and ship the `meta.json` edits first | `watcher` (empty allow ⇒ default-open universe) would keep the tool; the leak the commission targets stays open | A partial release that appears to fix the problem while the default-open universe still holds `ask_questions` |

**Invariants created.**
- **Phase 0 is a hard wait**: no implementation file is edited until `fix/question-pause-resume-asymmetry` is merged into `latest`.
- After the merge, **every anchor in this record must be re-verified** — the sibling's `instance_lifecycle.py` edits shift line numbers (`:1805`, `:1811`, `:2137`, `:2389-2390`, `:4595-4597`, `:4603`, `:4618`).
- Our `instance_lifecycle.py` edits are **additive**: two keyword-only arguments, no restructuring of `spawn_instance` or `_restore_instance`.
- The sibling worktree is **read-only** to us.
- The KV claim row is **merged**, never clobbered — the leader's `overlap_files` / `blocked_by` keys survive.

**Verification.** Plan §12.1, §12.2, §12.3. The gate is verified by `git rev-list --count origin/latest..HEAD` on the sibling branch = 0 **before** Phase 0 closes, and the branch exists locally as `fix/question-pause-resume-asymmetry`.

---

## P11 — Staging (stage only, no promote)

**Decision.** **Stage only — NO promote.** The user's **3-factor nonce ceremony** owns promotes. Follow the established pipeline: `scripts/upgrade/stage.sh` with provenance sidecars and the tip-identity freshness guard; record the `ENSEMBLE_ROLLBACK_SAFE` adjudication **explicitly** (the migration delta for this commission is **EMPTY** — no `daemon/migrations/versions/` file changes — so `rollback_safe=true` is legitimate, but the reasoning must be stated per the standing rule); bump the version via the project convention (`scripts/bump_version.py` — if it dies at an off-PATH `uv-sync`, complete manually with **exact** semantics: 4 files = `pyproject.toml`, `daemon/__init__.py`, `tests/test_api.py`, `uv.lock`); keep the staging worktree for the promote ceremony (both provenance sidecars intact).

**Rationale.** The standing rule for this project is explicit: a **non-empty** migration delta requires recorded semantic reasoning (not a reflex `=0`); this commission's delta is **empty**, which makes `rollback_safe=true` legitimate — and the reasoning is still recorded because the rule asks for it explicitly, not only when it is contested. The version-bump file set is confirmed by the tool itself: `scripts/bump_version.py` touches `pyproject.toml` (`:84-87`), `daemon/__init__.py` (`:89-92`), `tests/test_api.py` (`:94-97`), `uv.lock` (`:99-109`), plus `CHANGELOG.md` **conditionally** (`:133-145`). The staging worktree is kept so the provenance sidecars survive to the promote ceremony — the v0.25.2 lesson (sidecars died with the worktree; post-hoc verification fell back to exit-0 entailment).

**Rejected alternatives.**

| Alternative | Why rejected | Cost had it been chosen |
|---|---|---|
| Perform the promote | The 3-factor nonce ceremony is the user's; automation stops at demo (ADR-017), live steps are user-executed | An un-gated live flip — the exact failure the whole ladder exists to prevent |
| Set `rollback_safe` by reflex without reasoning | The standing rule requires the explicit adjudication (semantic reasoning, not a heuristic) | A future reader cannot tell *why* the delta is safe; the rule's purpose (auditability) is defeated |
| Add a migration to record the new tool-registration rule | A migration would make the delta non-empty for **no schema change** | A pointless schema artifact that (a) triggers the non-empty-delta adjudication burden and (b) violates the project's rule that every SQL file is a persistent schema record — this is not a schema change |
| Delete the staging worktree after staging | The v0.25.2 provenance-evidence gap | Post-hoc verification degrades to exit-0 entailment; the promote ceremony has no sidecars to verify against |
| Bump the version by hand from the start | `scripts/bump_version.py` is the convention and produces the exact 4-file set plus the conditional CHANGELOG | Manual bumps have drifted before (the `bump_version.py` git-add hazard, fixed @ `8f47661fe`); the script is the safer path, with manual completion only as the documented fallback |

**Invariants created.**
- **No promote.** The live symlink and the live daemon epoch are untouched by this commission.
- `daemon/migrations/versions/` is **unchanged** ⇒ the migration delta vs the base release is **EMPTY**.
- The rollback-safety adjudication is **recorded explicitly** (`rollback_safe=true source=explicit=true`), not inferred.
- The version bump touches exactly **4** files (+ `CHANGELOG.md` only when the script detects a header rename).
- The staging worktree is **retained** with both provenance sidecars intact.
- The prompt-artifact baseline is regenerated **iff** a depth-2 `agents/<agent>/<file>.md` file changed (`scripts/protection/generate_prompt_artifact_baseline.py`) — `meta.json` and `agents/_prompt_system/**` are **not** covered (`prompt_artifacts.py:257`).

**Verification.** Plan §13. Stage-time gates: the tip-identity guard (L1 `HEAD == refs/remotes/origin/latest`; a non-tip tree exits **78** `non-tip-tree`) and the provenance sidecars (re-hashed at stage, never trusted). Post-stage: migration-delta check `git diff <base>..<head> -- daemon/migrations/versions/` must be empty; sha 3-way match (staged artifact vs manifest vs build); `current` symlink unchanged; live daemon PID/epoch unchanged.

---

## §2. Rejected alternatives — cross-cutting index

| # | Alternative | Rejected under | One-line reason |
|---|---|---|---|
| 1 | Runtime refusal (register the tool, reject the call) | P2 | The tool still occupies the model's tool list and the help surface; the requirement is *never registered* |
| 2 | Gate inside `create_question_tools` | P2 | Its 3-arg signature has no `parent_id`/`source_type` channel and is called directly by tests |
| 3 | Read the instance row at registration time | P1, P2 | The row does not exist at spawn (`:2137` before INSERT `:2433`, commit `:5379`) ⇒ silent primary-path breakage |
| 4 | Read `instance_mappings` at registration time | P1 | Same timing defect (spawn `:362` before mapping `:379`); mapping is per-user, not per-instance |
| 5 | Use `original_source` instead of `source_type` | P1 | `original_source` **is** inherited by children ⇒ chat grandchildren would pass the predicate |
| 6 | Add `"question"` to `PRIVILEGED_TOOL_CATEGORIES` | P2 | Frozen exact set with **5** pins; privilege = default-deny ⇒ the root would be denied too |
| 7 | Remove `"question"` from `CATEGORY_MODULES` | P2 | The boot validator (`:1393`, `:1405`) would warn every boot for leader/devops/project-manager |
| 8 | A **second** metadata writer for `source_type` (mappings lane) | P3 | Two writers ⇒ lane-dependent metadata; breaks the single-writer invariant P1 rests on |
| 9 | Rename the grant category (e.g. `ask`) | P2, P4 | Renaming breaks the frozen-name pin, the `KNOWN_TOOL_NAMES` source-discovery pin, and the loader's innate-skill→category mapping for zero functional gain |
| 10 | Midflight as the blocker channel instead of a terminal report section | P8 | Midflight is **non-blocking** and has no guaranteed consumer; a blocker sent there can be lost |
| 11 | Open-questions section at the **END** of the report | P6 | Truncation at 10 000 chars (`child_reports.py:1538`) cuts the tail — the blockers vanish and the parent reads "done" |
| 12 | A **new** shared innate-skill directory | P5 | Two homes for one contract; `innate_skills: []` is treated as absent (`loader.py:856-858`) — a near-empty list is a trap |
| 13 | Grant the question category to all roots by `parent_id` (no chat-source requirement) | P1 | Webhook/API/internal roots would keep a tool they cannot service |
| 14 | "Ask" remaining the turn-ending move for non-roots | P6 | A non-root cannot reach the human ⇒ the turn ends with neither a report nor a pack ⇒ the run hangs |
| 15 | Disable the pack machinery alongside the tool | P9 | Every pending pack at cut-over becomes unanswerable; the FE/API answer paths stop working |
| 16 | Build a new parent→child question channel | P7 | The report + `send_message` + revive path already carries the round trip |
| 17 | Implement in parallel with the sibling commission | P10 | Uncommitted sibling edits to the same file; conflict under time pressure can silently drop a threaded value |
| 18 | Promote after staging | P11 | The 3-factor nonce ceremony is the user's; automation stops at demo (ADR-017) |

---

## §3. Consolidated invariants

A future change must not break any of these without a new commission and a new decision record.

**Data & identity**
1. `instance_metadata["source_type"]` has **exactly one writer** — `daemon/services/instance_lifecycle.py:2390`.
2. `original_source` is **never** an input to a root / user-facing predicate.
3. The root test is `(parent_id IS NULL OR parent_id == "")` — never a bare `IS NULL`.
4. The chat-source set is the 4-member registry set (`repository.py:860`), corroborated by `upgrade_journal.py:3358-3360`. It is **not** the 3-member prompt-formatting set.
5. `source_type` is **never** inherited by children.

**Gate & registration**
6. The predicate is evaluated **only** from values threaded into the tool build — never from a DB row read at registration time.
7. Hiding = **never registered** (the tool is absent from the toolset), not a runtime refusal.
8. `create_question_tools` keeps its **3-argument** signature; its source file is never deleted, renamed, or re-categorised.
9. Both `create_instance_tools` call sites (`:2137` spawn, `:4618` restore) thread the same two keyword-only arguments.
10. The gate runs **before** `_apply_tool_filter` (`:6238`) ⇒ a `tools.allow` entry can never re-admit a gated tool.

**Registry surfaces**
11. `PRIVILEGED_TOOL_CATEGORIES` is a **frozen exact set of eight** — no add, no remove without a same-PR five-pin update.
12. `"question"` stays in `CATEGORY_MODULES` (`:729`); `"midflight"` stays at `:732`.
13. `agents/leader/meta.json` and `agents/devops/meta.json` hold **neither** `"question"` in `tools.allow` **nor** in `innate_skills`; `agents/ari/meta.json` holds it in **both**.
14. `agents/project-manager/meta.json`'s `"question"` **deny** entry is preserved; `agents/critic/meta.json` is untouched.
15. `innate_skills` is **never** set to `[]` to suppress a skill (empty ⇒ absent ⇒ legacy scan, `loader.py:856-858`).

**Prompt contract**
16. The section title is exactly `Open questions for the orchestrator` — one spelling, tree-wide.
17. The section appears **only** when genuinely blocked, holds **0–3** framed questions, and sits **immediately after** the one-line report summary — never at the tail.
18. `agents/_prompt_system/innate-skills/question/skill.md` is the **single canonical home** for the question contract.
19. Prompt cross-references use the **section name only** — no bare `.md`, no bare `agents/` (`tests/unit/tools/test_prompt_section_reference_integrity.py`).

**Behavioural**
20. Midflight (in-turn, non-blocking) and the open-questions section (terminal report) coexist; neither substitutes for the other.
21. "Ask the user" wording is reserved for **roots**; a non-root leader escalates upward through its own report chain.
22. The parent-side answer path is the **existing** report + `send_message` + revive path — no parallel channel.

**Answering / durability**
23. The answering surface is untouched: `set_answers` CAS, the durable shadow, boot rehydration, `resolve_answer_target`, `job_answer`, and the instance-addressed answer route all stay as-is.
24. A legacy pack remains answerable after the gate ships; the original `pack_id` survives rehydration (`question_manager.py:487-492`).
25. **No** schema migration for this commission ⇒ the P11 delta stays empty.

**Process**
26. **Phase 0 is a hard wait** on `fix/question-pause-resume-asymmetry` merging into `latest`; every anchor is re-verified after that merge.
27. The sibling worktree is read-only to us.
28. The KV claim row is **merged**, never clobbered.
29. **Stage only — never promote.** The 3-factor nonce ceremony owns promotes.
30. Any edit to a sealed file (`tests/test_chart_image_delivery_e2e.py:1350` `SEALED_SHA_BASELINES`, tripwire `:1418`) requires an **adjudicated re-pin**: git-log last-touch attribution → constants-only update → targeted test → provenance-labelled commit.

---

## §4. Deferred / out of scope

| Item | Status | Note |
|---|---|---|
| `agents/watcher/meta.json` allow-list pinning | **Deferred — optional hygiene** | `allow: []` ⇒ default-open universe ⇒ watcher holds the tool today; the gate strips it. Pinning the list is a separate change (watcher currently *relies* on the default-open universe) |
| `_tool_metadata` residue for the `question` category | **Deferred — cosmetic** | `_tool_metadata` (module global, `_tool_registry.py:18`) is populated by `register_tool_category` (`:395`) and by `scan_tools_for_full_docs` (`:631`; registration `:660-662`). The category may remain resolvable for the help surface after the gate. Documented, not fixed |
| Mappings-lane source-type taxonomy beyond the chat set | **Deferred** | The chat set is pinned at 4 members. Adding a source type is a taxonomy change for a separate commission (the 3-member `_PLATFORM_INSTRUCTIONS` asymmetry is recorded in plan §1.4 and **not** re-litigated here) |
| Promote ceremony | **Owned by the user** | 3-factor nonce gate. This commission stages only |
| `whatsapp` platform prompt section | **Out of scope** | A whatsapp root passes P1 (it is a registered chat source) but receives no platform formatting section (no adapter). Recorded asymmetry — plan §1.4 |
| The 3-member vs 4-member chat-set divergence | **Recorded, not decided** | Two independent taxonomies (`repository.py:860`, `upgrade_journal.py:3358`) both say 4; the *prompt-formatting* whitelist says 3. Not a predicate problem |
| "All 14 chat instances are roots" live-data claim | **UNVERIFIED** | `ensemble_prod` is not reachable with this environment's credential; `ensemble_dev` holds 0 chat instances. Confirm at implementation time — the predicate fails closed, so the blast-radius estimate is what depends on it |
| Sibling-worktree "clean" claim | **Corrected** | It is 0 commits ahead but carries 7 modified + 3 new files — see P10 |
| Any change to the `question` tool's **validation, payload shape, or pause semantics** | **Out of scope** | The sibling commission owns pause/resume asymmetry; this commission owns **registration** only |
| Renaming the tool or the category | **Out of scope** | Breaks the frozen-name pins for zero functional gain — see §2 #9 |
| A structured (JSON/YAML) report section | **Rejected** | The report drain is a load-bearing path; the parent is an LLM, not a parser — see P6 |

---

## §5. Evidence index

| file:line | What it proves | Used by |
|---|---|---|
| `daemon/tools/instance.py:5895-5900` | The unconditional `create_question_tools` registration — the gate site | P2 |
| `daemon/tools/instance.py:2450` | `create_instance_tools` signature (the two new keyword-only args) | P2 |
| `daemon/tools/instance.py:158-164` (`:163`) | `INNATE_SKILL_TOOL_CATEGORIES` — `"question"` is auto-granted by `innate_skills` alone (not privileged) | P2, P4 |
| `daemon/tools/instance.py:167+` | `expand_allow_for_innate_skills` — the innate-skill → allow-list door | P4 |
| `daemon/tools/instance.py:373-405` | `resolve_tool_filter` — both-empty ⇒ `None` ⇒ default-open universe | P4 |
| `daemon/tools/instance.py:6235` / `:6238` | `scan_tools_for_full_docs` / `_apply_tool_filter` — the filter runs **after** the gate | P2 |
| `daemon/tools/instance.py:6243` / `:6274` | `_strip_privileged_category_tools` / `_apply_tool_filter` definitions | P2 |
| `daemon/tools/instance.py:1316` / `:1418-1424` | `_route_send_message`; the terminal-revive branch → `("enqueue-revive", …)` | P7 |
| `daemon/tools/question_tools.py:405-409` | `create_question_tools` 3-arg signature | P2 |
| `daemon/tools/question_tools.py:425` / `:502` | `@register_tool_category("question")`; the **only** production pack writer | P2, P9 |
| `daemon/tools/_tool_registry.py:18` / `:395` / `:631` / `:660-662` | `_tool_metadata` global; populated by the decorator and by `scan_tools_for_full_docs` | P2 (cosmetic) |
| `daemon/tools/_tool_registry.py:239-339` | `PRIVILEGED_TOOL_CATEGORIES` — a frozen 8-member frozenset; "question" absent | P2 |
| `daemon/tools/_tool_registry.py:690` / `:729` / `:732` | `CATEGORY_MODULES`; `"question"`; `"midflight"` | P2, P8 |
| `daemon/tools/_tool_registry.py:916` / `:981` | `"ask_questions"` and `"job_answer"` in `KNOWN_TOOL_NAMES` | P2, P9 |
| `daemon/tools/_tool_registry.py:234-238` | "Silent additions and silent removals … both trip the pins" | P2 |
| `daemon/registry.py:1393` / `:1405` | The boot-validator allow/deny warnings that a removed category would trigger | P2 |
| `daemon/repositories/instance/repository.py:860` | `CHAT_SOURCE_TYPES = ("telegram","slack","discord","whatsapp")` | P1 |
| `daemon/repositories/instance/repository.py:1049-1050` | The root test idiom `(parent_id IS NULL) \| (parent_id == "")` | P1 |
| `daemon/tools/upgrade_journal.py:3358-3360` | `USER_ORIGIN_CHAT_SOURCE_TYPES` — the same 4-member set, independently | P1 |
| `daemon/services/instance_lifecycle.py:1006-1036` | `_PLATFORM_INSTRUCTIONS` (3-member prompt set) | P1 (asymmetry) |
| `daemon/services/instance_lifecycle.py:1039-1081` (`:1075`, `:1078`, `:1053-1057`) | `append_platform_context` — the precedent: two pure gates over threaded values, and the docstring naming the spawn-time row-read failure | P1, P2 |
| `daemon/services/instance_lifecycle.py:1801-1812` (`:1805`, `:1811`) | `spawn_instance` signature — `parent_id` and `source_type` locals | P2 |
| `daemon/services/instance_lifecycle.py:2137` | Spawn-time `create_instance_tools` call (before the row exists) | P1, P2 |
| `daemon/services/instance_lifecycle.py:2389-2390` | **The single `source_type` writer** | P1, P3 |
| `daemon/services/instance_lifecycle.py:2433-2444` / `:5379` | The INSERT and the COMMIT — after the tool build | P1 |
| `daemon/services/instance_lifecycle.py:2446-2450` | `original_source` inheritance | P1 |
| `daemon/services/instance_lifecycle.py:4595-4597` / `:4603` / `:4618` | Restore path: `source_type` from the row, `parent_id=meta.parent_id`, the second `create_instance_tools` call | P2 |
| `daemon/routers/mappings.py:114-117` | The mappings lane spawns a chat-bound root with **no** `source_type` | P3 |
| `daemon/sources/mapper.py:362-365` / `:379` | The mapper lane **does** thread `source_type`; the mapping row is created **after** spawn returns | P1, P3 |
| `daemon/sources/mapper.py:422` / `:452` | `msg.metadata.get("source_type","webhook")`; `handle_incoming_message` calls `get_or_create_instance` **without** `source_type` — **test-only lane** (no production caller) | P3 |
| `daemon/sources/registry.py:1029-1035` | The **live** chat-root spawn lane — `source_type` threaded | P3 |
| `daemon/sources/base.py:159-160` | `MessageSourceAdapter.source_type` — the adapter's registered platform | P3 |
| `daemon/services/child_reports.py:1538` / `:1563-1565` | `_combine_messages`; the 10 000-char cap and `…[truncated]…` | P6 |
| `daemon/services/child_reports.py:2517` | `_process_child_completion_and_notify_parent` | P7 |
| `daemon/services/child_reports.py:3882-3883` / `:3985` / `:4152-4158` | Report row (`internal_report:<child>:<msg_id>`, `COMPLETION_REPORT`) → `PROCESS_REPORT` task → `ReportInjection` row | P6, P7 |
| `daemon/graph.py:8721-8745` | The report drain into the parent as a `HumanMessage` | P6, P7 |
| `daemon/services/instance_messaging.py:2369-2375` / `:2379` | Terminal-revival detection; the status flip to `RUNNING` | P7 |
| `daemon/services/answer_resolver.py:642` | `resolve_answer_target` (v0.25.5 answer-routing fix) | P9 |
| `daemon/services/midflight_qa.py:391` / `:426` | The pack shadow stamp / clear | P9 |
| `daemon/api.py:338` | `_boot_rehydrate_question_packs` | P9 |
| `daemon/services/question_manager.py:206` / `:224-225` / `:228` / `:321-373` / `:408` / `:487-492` | `QuestionManager`; `_packs`+`_lock`; `set_question_pack`; the `set_answers` CAS; `rehydrate_from_payloads`; original-`pack_id` preservation | P9 |
| `daemon/routers/answer_helper.py:262-282` | The instance-addressed answer route; RAM miss ⇒ one-shot rehydration from the durable shadow | P9 |
| `daemon/tools/job_queue.py:620` | `job_answer` — the root-without-the-tool answering surface | P9 |
| `daemon/loader.py:841-847` / `:856-858` / `:868` | Innate-skill path resolution; empty `innate_skills` ⇒ legacy scan; missing dir ⇒ WARN only | P4, P5 |
| `daemon/tools/prompt_artifacts.py:119-140` / `:257` | `VERSIONED_PROMPT_ARTIFACTS`; depth-2-only coverage | P4, P5, P11 |
| `agents/.prompt-artifacts.sha256` (161 entries) | The prompt-artifact baseline — covers depth-2 `.md` only; **0** `meta.json` entries | P4, P11 |
| `agents/leader/meta.json:8` / `:14-15` | leader `innate_skills` and `tools.allow` both carry `"question"` | P4 |
| `agents/devops/meta.json:8` / `:12` | devops `innate_skills` and `tools.allow` both carry `"question"` | P4 |
| `agents/ari/meta.json:9` / `:11-38` / `:39` | ari holds **neither** today; the grant is a real add | P4 |
| `agents/project-manager/meta.json:54` / `:64` | The `deny` array; `"question"` inside it — already compliant | P4 |
| `agents/critic/meta.json:13` / `:14` | critic's allow/deny — never held it | P4 |
| `agents/watcher/meta.json:22` | `"allow": []` — default-open universe | P4 |
| `agents/_prompt_system/innate-skills/question/skill.md` (53 lines) | The current unconditional-ask teaching text | P5 |
| `agents/project-manager/soul.md:58-59` / `:78-79` | The `## Decisions Pending` / `<0–3 framed questions>` precedent | P6 |
| `agents/leader/workflow.md:703` | The "begin work with a tool call, deliver your report, or ask" clause (the leader's home) | P6 (sweep) |
| 14 files carry the exact phrase `begin, deliver, or ask`; 16 lines carry the `deliver, or ask` variant | The clause-family sweep set — narrower than the 37 files containing `or ask` overall | P6 (sweep) |
| `agents/ari/tools_note.md:453` | `"relay them, wait, or ask the user"` — upgrade-interlock semantics, **not** in the sweep set | P6 (scope boundary) |
| `tests/unit/tools/test_upgrade_registration.py:102` / `:142` | Exact-set pin #1 | P2 |
| `tests/unit/tools/test_attestation_registration.py:142` / `:186` | Exact-set pin #2 | P2 |
| `tests/integration/test_maintenancer_spawn_resolves_tools.py:288` / `:324` | Exact-set pin #3 | P2 |
| `tests/integration/test_service_tool_flag_off_byte_identical.py:808` / `:823` | Exact-set pin #4 | P2 |
| `tests/unit/tools/test_secrets_registration.py:218` | Exact-set pin #5 | P2 |
| `tests/test_innate_skills_refactoring.py:67` | leader `innate_skills == ["coordination","chart","todo"]` — currently RED, turns GREEN | P4 |
| `tests/unit/test_devops_agent.py:165` | devops `innate_skills == ["dynamic-skill","todo"]` — currently RED; must become `["dynamic-skill","chart","todo"]` | P4 |
| `tests/test_chart_image_delivery_e2e.py:1350` / `:1418` | `SEALED_SHA_BASELINES` (35 files) + the SHA tripwire | §3 #30, P4, P5 |
| `tests/unit/tools/test_prompt_section_reference_integrity.py` | The bare-`.md` / bare-`agents/` cross-reference sweep | P6 |
| `tests/unit/tools/test_frozen_tool_name_discovery.py:223` | `KNOWN_TOOL_NAMES` must equal the source-discovered set exactly ⇒ the question tool source must not be deleted | P2, P9 |
| `scripts/bump_version.py:84-109` / `:133-145` | The 4-file version-bump set + the conditional `CHANGELOG.md` | P11 |
| `scripts/protection/generate_prompt_artifact_baseline.py` | The prompt-artifact baseline generator/verifier | P11 |
| `scripts/upgrade/stage.sh` | The staging entry point (provenance sidecars, tip-identity guard) | P11 |
| `/home/nea/ensemble-src-wt-qprasym` @ `1ebdf469f` | Sibling branch `fix/question-pause-resume-asymmetry`; 0 commits ahead; **7 modified + 3 new files, not clean** | P10 |

---

*End of decision record. The implementation body lives in [`plan-overview.md`](./plan-overview.md) — plan sections referenced by heading name above: §1.1/§1.3/§1.4 (P1), §2.1/§2.2/§2.3/§2.5/§2.6 (P2), §3 (P3), §4 (P4), §5/§5.1 (P5), §6 (P6), §7/§7.1/§7.2 (P7), §8 (P8), §9/§15 (P9), §10.x (prompt sweep), §11.1–§11.4 (tests), §12.1–§12.3 (P10), §13 (P11).*

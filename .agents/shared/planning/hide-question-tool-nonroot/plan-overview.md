# hide-question-tool-nonroot — Implementation Plan (Plan Overview)

> **Artifact map.** This file = the implementation plan. The rationale record (P1..P11 decisions, rejected alternatives, evidence) lives in the sibling file **`.agents/shared/planning/hide-question-tool-nonroot/decisions.md`** — link to it by heading, do not duplicate it here. P-numbers below refer to that file's decision IDs exactly as issued by the commission.
>
> Every `file:line` in this plan was READ at authoring time against `latest` @ `1ebdf469f` (v0.25.7, branch `latest`, repo `/home/nea/ensemble-src`). Anchors that could not be verified are marked **`UNVERIFIED — confirm at implementation time`**.

---

## 0. Header

| Item | Value |
|---|---|
| **Goal** | The `ask_questions` tool is **hidden** (never registered in an instance's toolset — NOT a runtime refusal) from every non-root instance. Only user-facing root instances keep it. Non-root children surface blockers in their completion report under the canonical section **"Open questions for the orchestrator"**; the parent answers per its own authority and instructs the child through the existing continuation path, or forwards upward, or (if the parent is the root) asks the user through its own tool. |
| **Non-goals** | (a) No change to the mid-flight channel. (b) No change to the question-pack machinery (create/CAS/rehydrate/shadow). (c) No DB/migration change. (d) No promote. (e) No new runtime refusal, no "deny" semantics — absence at registration only. (f) No rewrite of historical docs / CHANGELOG / planning records. |
| **Commission status** | **Design PINNED** (P1..P11 authoritative). **Implementation GATED** on sibling commission `ffeb7190` (pause/resume asymmetry) merging into `latest` — verified NOT merged as of this authoring (see §12). |
| **Scope tier** | **Full-stack behavioral change** — daemon tool-build gate + mappings-lane fix + 3 `meta.json` grants + shared innate-skill rewrite + 59-file prompt/doc sweep + 13 adjudicated seal re-pins + staged (not promoted) release. |
| **Why this tier** | The user-visible outcome ("roots still ask") needs BOTH halves: the daemon gate (authoritative) **and** the `ari` grant (otherwise "roots still ask" is vacuous — ari holds neither `question` in `tools.allow` nor in `innate_skills` today). The prompt sweep breadth is forced by the "or ask" clause family living in 35 prompt files; 13 of those are sealed-SHA-pinned and therefore force adjudicated re-pins. |
| **Branch (future)** | `feature/hide-question-tool-nonroot`, branched from `latest` **after** `ffeb7190` merges. |
| **Plan artifacts** | `.agents/shared/planning/hide-question-tool-nonroot/plan-overview.md` (this file) + `decisions.md` (sibling worker — NOT written here). |

---

## 1. P1 — Root predicate (PINNED)

### 1.1 Exact expression

```
USER_FACING_ROOT := (parent_id IS NULL OR parent_id == "")
                     AND (source_type IN {"discord", "slack", "telegram", "whatsapp"})
```

| Element | Value | Source of truth (read-verified) |
|---|---|---|
| Root test idiom | `(Instance.parent_id.is_(None)) | (Instance.parent_id == "")` | `daemon/repositories/instance/repository.py:1050` — legacy rows carry `""` |
| Chat-source set | `("telegram", "slack", "discord", "whatsapp")` | `daemon/repositories/instance/repository.py:860` (`SQLModelInstanceRepository.CHAT_SOURCE_TYPES`) |
| `source_type` writer | `instance_metadata["source_type"] = source_type` | `daemon/services/instance_lifecycle.py:2390` — **the only writer**; guarded by `if source_type:` at `:2389` |
| `original_source` | **NEVER use** | It *is* inherited by children → a chat child would look like a root. `source_type` is not inherited. |

### 1.2 Why `source_type` and never `original_source`

`source_type` is written in exactly one place (`instance_lifecycle.py:2390`) and is never propagated to children; `original_source` **is** inherited, so a chat-sourced child would satisfy a naive predicate. Live data (commission audit): all 14 chat instances are roots with 0 chat-source children — **UNVERIFIED — confirm against `ensemble_prod` at implementation time** (the reachable `ensemble_dev` holds 34 instances / 23 roots / **0** chat-source instances). The predicate fails closed, so only the blast-radius estimate depends on this claim; see `decisions.md` P1.

### 1.3 EVALUATION RULE (thread, never read)

The predicate **must** be evaluated from values **threaded into the tool build**, never from a DB row read at registration time.

| Why | Evidence |
|---|---|
| At spawn the instance row is **not INSERTed yet** | registration `instance_lifecycle.py:2137` vs INSERT `:2433-2434`, commit `:5375` |
| The `instance_mappings` row is created **after** spawn returns | `daemon/sources/mapper.py:362` (spawn) vs `:379-387` (mapping insert) |
| A row-reading gate would silently strip the tool from **every freshly spawned chat root** | consequence of the two rows above |

⇒ The gate is a **build-time conditional on threaded values** (§2). Both threaded values exist at both call sites (§2.3).

### 1.4 Recorded asymmetry (NOT a re-litigation — pinned set is the 4-member set)

The precedent helper `append_platform_context` whitelists a **3-member** platform set (`discord`, `slack`, `telegram` — keys of `_PLATFORM_INSTRUCTIONS`, `daemon/services/instance_lifecycle.py:1006-1036`); `whatsapp` has no adapter (see the interactive-chat vs registry-set split documented at `daemon/constants.py:128-144`). P1 is pinned to the **4-member registry set** (`repository.py:860`). Consequence to document in the code comment: a `whatsapp` root passes the P1 predicate and therefore receives `ask_questions`, but gets **no** platform prompt section (no adapter). Also note the precedent's Gate 1 (`if parent_id is not None: return`, `:1075-1076`) treats `parent_id == ""` as root-eligible — the same treatment P1 gives legacy empty-string roots.

---

## 2. P2 — The gate (PINNED)

### 2.1 Gate site — exact target shape

`daemon/tools/instance.py:5895-5900` (current, unconditional):

```python
5895:    question_tool_list = create_question_tools(
5896:        manager,
5897:        current_instance_id,
5898:        getattr(manager, "_live_hub", None),
5899:    )
5900:    tools.extend(question_tool_list)
```

Target shape (build-time conditional; the tool is **never registered** for non-roots):

```python
    # ── Question tools (per-instance user-pause-and-answer flow) ──
    # Registered ONLY for user-facing ROOT instances:
    #   (parent_id IS NULL OR parent_id == "") AND source_type IN CHAT_SOURCE_TYPES
    # A non-root gets NO question-category tool — the tool is never added to
    # the toolset (not a runtime refusal). A non-root surfaces its blockers
    # in its completion report under "Open questions for the orchestrator".
    # ``parent_id`` / ``source_type`` are THREADED IN by the caller: the
    # instance row does not exist yet at spawn time, so a row read here
    # would strip the tool from every freshly spawned chat root.
    if _is_user_facing_root(parent_id=parent_id, source_type=source_type):
        question_tool_list = create_question_tools(
            manager,
            current_instance_id,
            getattr(manager, "_live_hub", None),
        )
        tools.extend(question_tool_list)
```

**Placement matters**: `_apply_tool_filter(tools, agent_id, mcp_tool_names, version_tag=version_tag)` runs at `daemon/tools/instance.py:6238` — i.e. **after** the gate. The gate therefore removes the tool from the list *before* any allow/deny filtering ⇒ the tool is genuinely absent, not filtered at runtime.

### 2.2 The predicate helper (new, module-level, in `daemon/tools/instance.py`)

```python
# Chat source types that mark an instance as USER-FACING (P1).
# Mirrors SQLModelInstanceRepository.CHAT_SOURCE_TYPES
# (daemon/repositories/instance/repository.py:860) — kept local (no
# cross-package import from a tool module); a unit pin asserts the two
# sets stay equal.
CHAT_SOURCE_TYPES_FOR_ROOT: frozenset[str] = frozenset(
    {"discord", "slack", "telegram", "whatsapp"}
)


def _is_user_facing_root(
    *, parent_id: str | None, source_type: str | None
) -> bool:
    """True only for a user-facing root instance (P1 predicate).

    Fail-closed: an unknown / missing ``source_type`` is NOT a root, and a
    child that somehow carries a chat ``source_type`` is NOT a root.
    """
    if parent_id is not None and parent_id != "":
        return False
    return (source_type or "") in CHAT_SOURCE_TYPES_FOR_ROOT
```

### 2.3 Keyword-only threading — BOTH call sites

`daemon/tools/instance.py:2450` (signature change — keyword-only, additive, no default-order break):

```python
def create_instance_tools(
    manager: "InstanceManager",
    current_instance_id: str,
    agent_id: str = "",
    version_tag: str | None = None,
    *,
    parent_id: str | None = None,
    source_type: str | None = None,
):
```

| Call site | Current | Change | Where the values come from |
|---|---|---|---|
| **spawn** | `daemon/services/instance_lifecycle.py:2137` → `create_instance_tools(self._manager, instance_id, resolved_agent_id, version_tag=effective_version_tag)` | add `parent_id=parent_id, source_type=source_type` | `spawn_instance` locals — `parent_id` param at `:1805`, `source_type` param at `:1811` |
| **restore** | `daemon/services/instance_lifecycle.py:4618` → `create_instance_tools(self._manager, instance_id, resolved_agent_id, version_tag=resolved_tag)` | add `parent_id=meta.parent_id, source_type=source_type` | `meta.parent_id` at `:4603`; `source_type` read from the instance row's `instance_metadata` at `:4595-4597` (survives restart) |

> The restore path is what makes the gate durable across daemon restarts: a root restored from DB keeps the tool, a child restored from DB stays tool-less.

### 2.4 Precedent to copy verbatim — `append_platform_context`

`daemon/services/instance_lifecycle.py:1039-1081` — the canonical "root-only, whitelist-only, pure, no DB read" shape:

| Element | Line | Content |
|---|---|---|
| Signature | `:1039-1043` | `def append_platform_context(system_prompt: str, source_type: str | None = None, parent_id: Optional[str] = None) -> str:` |
| Docstring rationale | `:1053-1057` | "callers are responsible for resolving `source_type` … BEFORE calling this appender — at spawn time the instance row has not been INSERTed yet, so a DB lookup here would return `None` and the feature would silently no-op." |
| Gate 1 | `:1074-1076` | `if parent_id is not None: return system_prompt` |
| Gate 2 | `:1077-1080` | `section = _PLATFORM_INSTRUCTIONS.get(source_type); if not section: return system_prompt` |
| Platform whitelist | `:1006-1036` | `_PLATFORM_INSTRUCTIONS` — keys `discord`, `slack`, `telegram` |

### 2.5 The two FORBIDDEN moves (and their consequences)

| Forbidden move | Consequence if done | Evidence |
|---|---|---|
| **Do NOT add `"question"` to `PRIVILEGED_TOOL_CATEGORIES`** (`daemon/tools/_tool_registry.py:239-339`) | The set is an exact-**8** frozenset; adding a 9th trips **5 exact-set pins**: `tests/unit/tools/test_upgrade_registration.py:142`, `tests/unit/tools/test_attestation_registration.py:143`, `tests/integration/test_maintenancer_spawn_resolves_tools.py:288,324`, `tests/integration/test_service_tool_flag_off_byte_identical.py:823` (category-membership assert at `:216`), `tests/unit/tools/test_secrets_registration.py:211,218` | verified — all five assert `PRIVILEGED_TOOL_CATEGORIES == frozenset({...})` |
| **Do NOT remove `"question"` from `CATEGORY_MODULES`** (`daemon/tools/_tool_registry.py:729`) | The boot validator (`daemon/registry.py:1384-1394` for allow, `:1396-1406` for deny) warns per unresolved category/tool name ⇒ boot WARN for `leader`, `devops`, `project-manager` on **every boot** | verified |

Also **do NOT** gate inside `create_question_tools` (3-arg signature, also called from tests) and **do NOT** delete `daemon/tools/question_tools.py` — the tool source must stay discoverable (see §11 "existing tests that may break" #1).

### 2.6 Cosmetic residue — `_tool_metadata` (R3)

| Fact | Line | Note |
|---|---|---|
| `_tool_metadata` is a module-level global | `daemon/tools/_tool_registry.py:18` | — |
| `clear_registry()` clears it | `:625-628` | — |
| `scan_tools_for_full_docs` **adds** to it | `:631` (def), `:660-665` (register block), `:682-686` (update block) | The commission's anchor `:6235` is **UNVERIFIED — the file is 1 229 lines**; the real add-site is `:660-665` |
| Help surface | `daemon/tools/help.py:105-115` expands innate skills → categories, independent of the gate | ⇒ `"question"` may still be listed on the help surface. **Cosmetic** — document, do not chase |

---

## 3. P3 — Mappings lane (PINNED, IN SCOPE)

`daemon/routers/mappings.py:113-137` spawns a **chat-bound ROOT with NO `source_type`** ⇒ false negative for P1 (the root would lose the tool).

| Step | Site | Action |
|---|---|---|
| 1 | `daemon/routers/mappings.py:78` | `source = await asyncio.to_thread(manager._source_repository.get_source_config, source_id)` — **already loaded** in this handler |
| 2 | `daemon/repositories/source/models.py:59` | the row carries `source_type: str = Field(index=True)`; `get_source_config` returns that ORM row (`daemon/repositories/source/repository.py:207-210`) |
| 3 | `daemon/routers/mappings.py:114-117` | `manager.spawn_instance(agent_id=…, instance_id=…)` → **add** `source_type=source.source_type` |
| 4 | `daemon/manager.py:9201-9212` | `spawn_instance(..., source_type: str | None = None)` — **already accepts** it (no manager change needed) |
| 5 | `daemon/services/instance_lifecycle.py:2389-2390` | the **single** writer stamps `instance_metadata["source_type"]` — **never add a second metadata writer** |

The chat lane itself already threads it: `daemon/sources/mapper.py:362-366` → `spawn_instance_with_mcp(..., source_type=source_type)`. **Keep the fix additive and minimal** (one keyword at one call site).

---

## 4. P4 — `meta.json` plan (PINNED)

Defense-in-depth + prompt hygiene. **The authoritative control is the P2 gate** (§2).

| File | Line (verified) | Current | Change | Effect |
|---|---|---|---|---|
| `agents/leader/meta.json` | `:8` | `"innate_skills": ["coordination", "chart", "todo", "question"]` | **remove `"question"`** | Question Skill prompt text stops landing in leader prompts; drops the innate→category grant (`INNATE_SKILL_TOOL_CATEGORIES["question"] → ["question"]`, `daemon/tools/instance.py:158-164`) |
| `agents/leader/meta.json` | `:15` | `tools.allow` contains `"question"` | **remove `"question"`** | leader instances never resolve the question category |
| `agents/devops/meta.json` | `:8` | `"innate_skills": ["dynamic-skill", "chart", "question", "todo"]` | **remove `"question"`** | same as above (devops is a non-root child) |
| `agents/devops/meta.json` | `:12` | `tools.allow` contains `"question"` | **remove `"question"`** | devops never resolves the question category |
| `agents/ari/meta.json` | `:9` | `"innate_skills": ["job-orchestration", "chart", "todo"]` | **ADD `"question"`** | ari is the default chat-facing root; grants the tool + the (rewritten) Question Skill prompt |
| `agents/ari/meta.json` | `:11-38` (`tools.allow` list) | no `"question"` | **ADD `"question"`** | without this, "roots still ask" is vacuous — ari is the live Discord root |
| `agents/project-manager/meta.json` | `:64` | `"question"` inside **`tools.deny`** | **UNCHANGED** | already compliant (deny ≠ grant) |
| `agents/critic/meta.json` | `:9` / `:13` | `innate_skills: ["dynamic-skill","todo"]`; `allow: ["read_file","image","design"]` | **UNCHANGED** | never held the tool |
| `agents/watcher/meta.json` | `:22` | `"allow": []` (empty ⇒ default-open universe) | **OPTIONAL hygiene**: pin an explicit allow list | today watcher HOLDS `ask_questions` by default-open; the gate strips it. Pinning the allow list is hygiene, not correctness. |

> **Never set `innate_skills` to `[]`** — `daemon/loader.py:863` treats an empty array as *absent* and falls back to a legacy `skills/` scan. Removing one element keeps the array non-empty for leader/devops (3 and 3 elements respectively) — safe.

---

## 5. P5 — Root-scoped skill rewrite (PINNED)

**Target file:** `agents/_prompt_system/innate-skills/question/skill.md` (54 lines today; loaded by name through `daemon/loader.py:841-847` `_resolve_innate_skill_paths` → `agents/_prompt_system/innate-skills/<name>/skill.md`).

**Not sealed** — the sealed innate-skill entry is `agents/_prompt_system/innate-skills/chart/skill.md` (`tests/test_chart_image_delivery_e2e.py:1358`), not `question/skill.md`.

### 5.1 Rewrite spec (the new file MUST say all of this, in the agent's first person)

| # | Required content | Why |
|---|---|---|
| 1 | I am the user-facing root: I talk to the human directly. Ask when I need a decision only the human has — and decide everything else myself. | Root-only teaching home |
| 2 | **Chat-lane nuance:** in a chat lane the human replies in normal chat — so **prefer asking in my reply text**; use the tool only when a structured answer channel exists. | §10 of the commission: a mid-turn pack pause in a chat lane can strand a turn (R1) |
| 3 | Keep the tool reference table (signature + field table) — it is the only place the field semantics live. | one concern per file |
| 4 | Keep "Pause / Resume" semantics: the instance pauses after the call; answers arrive as a normal message. | unchanged behavior |
| 5 | **The non-root half:** if I do not have the ask tool, I am **not** the user-facing root. I decide per my own mandate; if I am genuinely blocked, I end my report with the section **"Open questions for the orchestrator"** (0-3 framed questions) — asking is never my turn-ending move. | the contract every non-root needs, in the one file every agent that holds the question skill reads |
| 6 | No mention of `meta.json`, `tools.allow`, `innate_skills`, `daemon/` paths, loader mechanics, or test paths. | prompt-writing guide §1 forbidden layer |

> After P4 the skill text lands **only** in ari (leader + devops lose it). Agents without the skill (worker, coder, tester, …) get the non-root contract from their **own** agent files (§10).

---

## 6. P6 — Report contract (PINNED)

| Element | Rule | Evidence / precedent |
|---|---|---|
| Canonical section title | **`Open questions for the orchestrator`** (user's verbatim phrasing) | §5 spec #5 |
| When to include | **Only when genuinely blocked.** Omit the section entirely otherwise (no empty heading, no "none"). | decision |
| Cap | **0-3 framed questions** | project-manager precedent — `agents/project-manager/soul.md:58-59`, `:78-79`, `:93-94`; `agents/project-manager/workflow.md:203-204` ("Decisions Pending / `<0–3 framed questions>`") |
| Placement | **Immediately after the one-line report summary** | the report is free text; the combined child-report content is capped at **10 000 chars** + `"…[truncated]…"` (`daemon/services/child_reports.py:1537-1545` `_combine_messages`, cap constant `MAX_COMBINED_REPORT_CHARS`) ⇒ a **tail** section can be cut. Right-after-summary survives truncation. |
| Preference | Non-roots **prefer deciding per their own mandate**; "ask" is never the turn-ending move for them. | decision |
| Not a substitute | Mid-flight ≠ open-questions (P8). | `daemon/tools/_tool_registry.py:732` |

---

## 7. P7 — Parent-side contract (PINNED)

### 7.1 The existing continuation path (NO new machinery — reuse it)

| Step | Site | What happens |
|---|---|---|
| 1 | `daemon/services/child_reports.py:2517` `_process_child_completion_and_notify_parent(instance_id, completed_message_id)` | child completion → parent notification |
| 2 | `daemon/services/child_reports.py:3878-3889` | `MessageQueue` row — `instance_id=instance.parent_id`, `content=last_content`, `source=f"internal_report:{child_iid}:{msg_id}"`, `type=MessageType.COMPLETION_REPORT`, `status=READY` |
| 3 | `daemon/services/child_reports.py:3984-3992` | `Task(task_type=TaskType.PROCESS_REPORT, instance_id=instance.parent_id, message_id=report_message_id, status=PENDING)` |
| 4 | `daemon/services/child_reports.py:4152-4159` | `ReportInjection(parent_instance_id, child_instance_id, child_message_id, report_message_id, content=last_content)` |
| 5 | `daemon/graph.py:8721-8745` | drain → `HumanMessage` appended into the parent's turn (with tool-call pairing heal) |
| 6 | `daemon/tools/instance.py:1316` `_route_send_message(manager, target_instance_id)` | parent routes `send_message` to the child |
| 7 | `daemon/tools/instance.py:1418-1424` | terminal-status branch ⇒ `("enqueue-revive", prior_status)` (`TERMINAL_INSTANCE_STATUSES`) |
| 8 | `daemon/services/instance_messaging.py:2365-2381` | `COMPLETED/TERMINATED/ERROR/FAILED` ⇒ `instance.status = RUNNING` (revive) |
| 9 | `daemon/services/instance_messaging.py:4235-4250` | watcher / auto-load re-registration on revive |

⇒ A parent that answers an "Open questions for the orchestrator" section via `send_message(child_id, "<answer>")` revives the child and the answer lands in the child's next turn. **No new transport.**

### 7.2 Escalation rule

| Situation | Move |
|---|---|
| Parent is a **leader** (non-root) with authority to decide | Answer the child directly (§7.1) |
| Genuinely **user-critical**, parent is a **non-root** | Escalate **through the parent's own report chain** to a root — never "ask the user" wording |
| Parent **is** the user-facing root | Use its own tool (`ask_questions`) — and in a chat lane prefer asking in reply text (§5.1 #2) |

---

## 8. P8 — Mid-flight coexists (PINNED)

| Fact | Site |
|---|---|
| `midflight` category ⇒ `daemon.tools.midflight_report` | `daemon/tools/_tool_registry.py:732` |
| Mid-flight = **non-blocking** progress/decision surfacing **during** a turn | `_tool_registry.py:730-731` comment |
| Open-questions section = **terminal** report section | P6 |
| **No mid-flight changes.** | decision |

`agents/critic/tools_note.md:25` ("`midflight` → no leader-facing mid-flight questions; blockers go back through my orchestrator") and `agents/designer/tools_note.md:79` are already consistent with the split — **no change**.

---

## 9. P9 — Defense in depth stays intact (PINNED)

| Artifact | Citation | Must remain intact |
|---|---|---|
| Job-scoped answer routing | `daemon/services/answer_resolver.py:642` `resolve_answer_target(manager, work_instance_id, question_pack_id=None)` | ✅ |
| Durable pack shadow (write) | `daemon/services/midflight_qa.py:391` `stamp_question_pack_metadata(manager, instance_id, pack)` | ✅ |
| Durable pack shadow (clear, both unwind sites) | `daemon/services/midflight_qa.py:426` `clear_question_pack_metadata(...)` | ✅ |
| Boot rehydration | `daemon/api.py:338` `_boot_rehydrate_question_packs(manager)`; `daemon/services/question_manager.py:408` `rehydrate_from_payloads(payloads)` (idempotent; preserves the original `pack_id`, `:487-495`) | ✅ |
| Answer CAS (exactly-once) | `daemon/services/question_manager.py:321-373` `set_answers(instance_id, answers) -> (pack, transitioned)` | ✅ |
| Answer resolution incl. RAM→durable fallback | `daemon/routers/answer_helper.py:262-282` (`get_question_pack` → `get_metadata_value` → `rehydrate_from_payloads`) | ✅ |
| Only production pack writer | `daemon/tools/question_tools.py:502` (`manager._question_manager.set_question_pack(...)`) | ✅ — the gate means **no new** pack can be created by a non-root |
| Root answering without the tool | `job_answer` tool (category `job`) — `daemon/tools/job_queue.py:770`, doc at `:620`, `:872-894` | ✅ |

**No coupling exists between the pack machinery and tool registration** — the pack path is instance-addressed / job-addressed, not tool-registration-addressed.

---

## 10. Prompt & doc sweep — EXECUTABLE table

### 10.1 Canonical-home VERDICT (required by the commission)

> **VERDICT: the guide's canonical-home rule is INTRA-agent.**
> Evidence: `docs/agent-prompt-writing-guide.md:68` — *"If an artifact … appears in more than one file, **pick one canonical home and make the others link**"* — the file-roles table it belongs to (`:57-63`) enumerates **one agent's** files (`soul.md` / `rule.md` / `workflow.md` / `tools_note.md` / `*-strategy.md` / `memory.md`); `:210` states the same-agent scope explicitly (*"Duplicate the scrutiny rule across several files **of the same agent** → One canonical home"*); `:63` names the *strategy skill* as *"The single canonical home"* for repeated artifacts **within an agent**.
> **Consequence:** a per-agent statement of the open-questions contract in each agent's **own** file is compliant. It is **not** a cross-agent duplication violation.
>
> **BUT the intra-agent rule still binds**: for every agent that appears in **more than one of its own files** in the sweep set, the contract must be stated **ONCE** in that agent's canonical home and the other files of the **same** agent must carry a pure section cross-reference (**"See &lt;Section Name&gt;"** — `guide:99-108`, no filename/path token). `guide:78` forbids a false *"stated once"* claim — if a same-agent copy survives, say so honestly or remove it.
>
> **Cross-agent single home** for the *ask* contract itself: `agents/_prompt_system/innate-skills/question/skill.md` — one shared file, injected by name (`daemon/loader.py:841-847`); after P4 it lands in ari only. Non-root agents get their contract from their own files (§10.3).

### 10.2 Prompt-editing rules — quoted VERBATIM from `docs/agent-prompt-writing-guide.md`

| # | Rule (verbatim) | Cite |
|---|---|---|
| R-a | *"Agent prompts **build the agent**. They are read by the agent as its own self-concept and operating manual. Write in **first person** ("I", "my") and describe what the agent **does, sees, and is allowed to do** — never describe the system that hosts it."* | `:11` |
| R-b | *"never reference these inside prompt prose: `meta.json` (the file itself …), `tools.allow` / `tools.deny` …, `daemon/tools/_tool_registry.py`, `daemon/tools/instance.py`, or any `daemon/` path, `skill-set.yaml` as a file the agent reads or bumps …, `innate_skills` …, `auto_load` field mechanics …, seeder / `seed_all` / `SkillSeedService` / seeding pipeline, `agent_id=` plumbing …, version-tag / registry resolution …, test file paths (`tests/unit/...`), `default_agent_versions` config concept"* | `:15-27` |
| R-c | *"The prompt is composed in order: `soul` → `rule` → skills → `tools_note` → `workflow` → `memory`. Each file owns exactly one concern. Don't duplicate content that belongs to another file."* | `:55` |
| R-d | *"If an artifact (a tier table, a dispatch snippet, an output template, a contract string) appears in more than one file, **pick one canonical home and make the others link**."* | `:68` |
| R-e | *"Do **not** write "this contract is stated once, in `X.md`; I do not maintain parallel copies" unless the copies are actually removed. A false single-source claim is worse than honest duplication."* | `:78` |
| R-f | *"**Every** prompt-surface cross-reference — both bare file refs and the intermediate `file.md → Section` arrow form — is now forbidden."* Allowed forms: **`See <Section Name>`** (same agent) and **`See <agent>'s <Section Name>`** (cross agent). *"Forbidden in prompt text: ANY filename or path token."* | `:93-108` |
| R-g | *"State the boundary operationally — what the agent may and may not do with each tool — not the system that enforces it."* | `:128` |
| R-h | *"After `send_message` (or `convene_council_with_skill`), **END YOUR TURN**. State the *why* … State it **once** in `workflow.md`; other files reference it. Do not copy the full paragraph into 4 files."* | `:176-180` |
| R-i | *"the typical state for a versioned agent is: `agents/<name>/` (the base) is **byte-identical** to `agents/<name>[v2]/`. The v2 dir is the source of truth; the base dir is a frozen mirror. Editing either dir in isolation creates drift — both must move together."* | `:238-241` |
| R-j | *"**Every agent definition with a non-empty team MUST carry report-scrutiny guidance** that conditions on the visible `[REPORT SANITY: …]` marker pattern"* — registry-completeness test `tests/unit/test_report_integrity_prompts.py`. | `:203-213` |
| R-k | Pre-commit checklist (no system internals · one canonical home · no false "stated once" · ≤7 Cardinal rules · cross-references resolve · tone directive · fan-in escape valve · skill versions consistent · fallbacks stay within `team_members` · no provenance annotations · tests still pass). | `:255-273` |

### 10.3 Class A — tool grant (3 files)

| File | Anchors | Change | Notes |
|---|---|---|---|
| `agents/leader/meta.json` | `:8`, `:15` | remove `"question"` from `innate_skills` + `tools.allow` | P4; SEALED? **no** (not in the 35) |
| `agents/devops/meta.json` | `:8`, `:12` | remove `"question"` from `innate_skills` + `tools.allow` | P4; not sealed |
| `agents/ari/meta.json` | `:9`, `:11-38` | **add** `"question"` to `innate_skills` + `tools.allow` | P4; not sealed. `agents/ari/rule.md` and `agents/ari/workflow.md` are **SEALED — do not touch** |

### 10.4 Class B — ask-guidance → open-questions contract

Canonical-home column = **the file that owns the contract for that agent**; every other file of the same agent carries `See Open questions for the orchestrator` (or `See <agent>'s Open questions for the orchestrator` cross-agent) and **no filename token**.

| File | Anchors (verified content) | Change | Canonical home for this agent | Seal pin |
|---|---|---|---|---|
| `agents/_prompt_system/innate-skills/question/skill.md` | whole file (54 lines) | FULL REWRITE per §5.1 | cross-agent shared home | not sealed (chart/skill.md is) |
| `agents/leader/soul.md` | `:19` "I pause and ask for user input"; `:56` "I ask when it matters via `ask_questions()`"; `:58-64` the 5-item "I ask for user input when:" list | agent-POV rewrite: I am not the user-facing root; my blockers ride my report | identity file owns *identity*; pointer to the workflow section | not sealed |
| `agents/leader/rule.md` | `:208` "**Ask User**"; `:209` "**Ask User**"; `:214` "**Ask User**"; `:215` "**Ask User**"; `:216` "Leader (collaborate with user)" | authority table: "Ask User" → "**Report open questions**" / "Leader (decide — escalate only if user-critical)"; add pointer | rule.md = rule-shaped home → pointer to workflow section | **SEALED** (`tests/test_chart_image_delivery_e2e.py:1388`) |
| `agents/leader/workflow.md` | `:213` SemiAuto override ("Only ask user when complexity is HIGH…"); `:464` "ask the user OR delegate a repro to Tester"; `:548` "Surface the competing hypotheses to the user and ask for direction"; `:703` clause-family quote | rewrite: open-questions reporting replaces user-asking; **add the new section "Open questions for the orchestrator"** (0-3, placement, omission rule) | **canonical home (leader)** | not sealed |
| `agents/leader/tools_note.md` | whole file (7 149 bytes) — documents neither question nor midflight | OPTIONAL: one operational line — I have no user-ask channel; my blockers ride the report | tools_note owns the tool-by-tool reference | not sealed |
| `agents/worker/rule.md` | `:187` "**Ask** — if something blocks me, the question IS the turn ending."; `:189` scope carve-out mentioning "question-to-dispatcher turns" | "Ask" → "**Report open questions**" (section, 0-3) — never a tool-less ask | **canonical home (worker)** — E_HOME `rule.md` | not sealed. **MUST KEEP** `"before ending any turn"` + `"zero tool calls"` (`tests/unit/test_report_integrity_prompts.py:115-118`) |
| `agents/jober/rule.md` | `:94` "Ask the user which project they want to interact with"; `:221` "❌ Asking the user, getting an answer, then ignoring it" | redirect to the orchestrator / report | **canonical home (jober)** | not sealed |
| `agents/jober/workflow.md` | `:23` "Ask user to confirm project before proceeding" | pointer → jober's rule.md section | pointer | not sealed |
| `agents/giter/workflow.md` | `:108` "Step 4: Ask user - which files to include?"; `:157` "Step 5: Ask user for branch purpose" | redirect → report open questions | **canonical home (giter)** (no rule.md in sweep) | not sealed |
| `agents/tester/rule.md` | `:75` "If missing, ask the user to create it" | redirect → report | **canonical home (tester)** | not sealed |
| `agents/tester/workflow.md` | `:966` "**If missing** — Ask user for project-specific requirements" | pointer → tester's rule.md section | pointer | not sealed |
| `agents/doc-writer/rule.md` | `:8` "**Clarify ambiguities** with a focused clarification (default to ONE question…" | clarify with the dispatcher / report | **canonical home (doc-writer)** | not sealed |
| `agents/doc-writer/soul.md` | `:19` "ask a focused clarification (default to ONE question…" | pointer → doc-writer's rule.md section | pointer | not sealed |
| `agents/doc-writer/workflow.md` | `:10` "ONE question; ask more only if multiple critical ambiguities exist" | pointer | pointer | not sealed |
| `agents/gaia/workflow.md` | `:60` "4. Ask user to try again" | redirect → report | **canonical home (gaia)** | not sealed |
| `agents/wanderer/workflow.md` | `:91` "If the question is ambiguous in a way that affects the answer, ask before guessing" | "ask the dispatcher" (already-correct form) | **canonical home (wanderer)** — E_HOME `rule.md` | not sealed |
| `agents/wanderer/rule.md` | `:68` clause family | see §10.5 | rule.md | not sealed |
| `agents/wanderer/skills-template/code-investigation.md` | `:128` "clarify with the dispatcher before tracing"; `:33` "clarify scope with the dispatcher" | **VERIFY-ONLY** — already the non-root-correct form; change only if it says "ask the user" | skill template → pointer | not sealed |
| `agents/wanderer/skills-template/codebase-mapping.md` | `:32` "clarify scope with the dispatcher before proceeding" | VERIFY-ONLY | skill template | not sealed |
| `agents/wanderer/skills-template/library-research.md` | `:34` "clarify scope with the dispatcher before proceeding" | VERIFY-ONLY | skill template | not sealed |
| `agents/wanderer/skills-template/root-cause-analysis.md` | `:33` "clarify scope with the dispatcher before proceeding" | VERIFY-ONLY | skill template | not sealed |
| `agents/_baby_template/rule.md` | `:6` "Ask all essential questions before doing anything else"; `:15` "Rush through questions"; `:40` "**Curiosity** — Ask questions when something is unclear" | rewrite as the non-root contract (highest leverage — fixes every future agent) | **canonical home (_baby_template)** | not sealed |
| `agents/_baby_template/workflow.md` | `:13,:15,:21,:27` "Ask These Questions (in order)" blocks; `:71` "Clarify the task, ask questions if needed"; `:83` "Uncertain about something? → Ask for clarification" | pointer → _baby_template's rule.md section | pointer | not sealed |
| `agents/_mother/rule.md` | `:7` "Ask clarifying questions when requirements are unclear"; `:9` "don't ask redundant questions" | rewrite as the non-root contract | **canonical home (_mother)** | not sealed |
| `agents/_mother/workflow.md` | `:7` "Ask these questions in order"; `:52`, `:104` "Ask specific questions based on what they want to change" | pointer → _mother's rule.md section | pointer | not sealed |
| `agents/designer/soul.md` | `:68` "every turn ends on a tool call, a deliverable, or a question to the leader" | "…or an open question for my orchestrator" (paraphrase form) | **canonical home (designer)** | not sealed |
| `agents/coder/soul.md` | `:149` clause family | see §10.5 | **canonical home (coder)** — E_HOME `soul.md` | **SEALED** (`:1382`) |

### 10.5 Clause family — "begin, deliver, or ask" / "or ask"

**Edit rule (light, uniform):** replace the terminal `or ask` / `or ask — a question to my dispatcher` with the open-questions reporting semantics: `or report open questions`. Keep every other token of the paragraph — especially `"before ending any turn"` and `"zero tool calls"` (`E_REQUIRED_SNIPPETS`, `tests/unit/test_report_integrity_prompts.py:115-118`).

**Short form (14 files)**

| File | Anchors | Seal pin |
|---|---|---|
| `agents/approver/rule.md` | `:9`, `:63` | **SEALED** (`:1378`) |
| `agents/approver[v2]/rule.md` | `:9`, `:63` | **SEALED** (`:1379`) |
| `agents/architect/rule.md` | `:9` | **SEALED** (`:1380`) |
| `agents/coder/soul.md` | `:149` | **SEALED** (`:1382`) |
| `agents/developer/rule.md` | `:11` | **SEALED** (`:1383`) |
| `agents/developer[v2]/rule.md` | `:11` | not sealed (only `developer[v2]/tools_note.md` is) |
| `agents/planner/rule.md` | `:9` | **SEALED** (`:1390`) |
| `agents/planner[v2]/rule.md` | `:9` | not sealed (only `planner[v2]/tools_note.md` is) |
| `agents/reviewer/rule.md` | `:9` | **SEALED** (`:1393`) |
| `agents/reviewer[v2]/rule.md` | `:9` | **SEALED** (`:1394`) |
| `agents/tester/rule.md` | `:41` (clause family) + `:75` (Class B) | not sealed |
| `agents/tidier/rule.md` | `:18` | **SEALED** (`:1395`) |
| `agents/tidier[v2]/rule.md` | `:18` | **SEALED** (`:1396`) |
| `agents/wanderer/rule.md` | `:68` | not sealed |

**Long form (19 files)** — same light edit

`agents/approver/skills-template/approval-strategy.md` · `agents/approver[v2]/skills-template/approval-strategy.md` · `agents/architect/workflow.md` · `agents/coder/skills-template/work-partition.md` · `agents/developer/skills-template/dev-strategy.md` · `agents/developer[v2]/skills-template/dev-strategy.md` · `agents/developer/workflow.md` · `agents/developer[v2]/workflow.md` · `agents/governor/workflow.md` · `agents/leader/workflow.md` (`:703`) · `agents/planner/workflow.md` · `agents/planner[v2]/workflow.md` · `agents/project-manager/workflow.md` · `agents/reviewer/skills-template/review-strategy.md` · `agents/reviewer[v2]/skills-template/review-strategy.md` · `agents/tester/workflow.md` · `agents/tidier/workflow.md` · `agents/tidier[v2]/workflow.md` · `agents/wanderer/workflow.md`

⇒ `agents/project-manager/workflow.md` is **SEALED** (`:1392`) ⇒ one more adjudicated re-pin.

**Paraphrases (2)**: `agents/designer/soul.md:68`; `agents/worker/rule.md:187-189`.

**v1↔v2 parity (R-i)**: `developer` ↔ `developer[v2]`, `planner` ↔ `planner[v2]`, `reviewer` ↔ `reviewer[v2]`, `approver` ↔ `approver[v2]`, `tidier` ↔ `tidier[v2]` must move together (base is the frozen mirror of the v2 dir).

### 10.6 Class C — parent-side handling (largely right; re-anchor names + add the general case)

| File | Anchors | Change | Seal pin |
|---|---|---|---|
| `agents/reviewer/workflow.md` | `:208` "### 5a. Distinguish a Clarifying-Question Report from a Final Report"; `:211` the clarifying-question reply paragraph (already describes `send_message` revive) | re-anchor the section name to the canonical vocabulary; keep the revive mechanism verbatim | not sealed |
| `agents/reviewer[v2]/workflow.md` | `:208`, `:211` | mirror of reviewer (parity) | not sealed |
| `agents/governor/workflow.md` | `:46` "**Clarifying questions — complete-to-ask (NEVER pause):** The governor has no direct human channel … there is no `ask_questions` tool … END YOUR TURN / complete with a self-contained clarifying question addressed to the requester"; `:51` the revival re-validation line | KEEP the semantics; align the wording to "open questions for the orchestrator" | not sealed |
| `agents/leader/workflow.md` | `:712-717` "Agent instance asks: 'Shall I proceed?' … ✅ RIGHT: send_message(instance_id, "Proceed")" | **KEEP** — this IS the continuation channel (P7) | not sealed |

### 10.7 Class D — adopt naming/semantics, NO change

| File | Anchors | Action |
|---|---|---|
| `agents/maintenancer/skills-template/bug-advisory.md` | `:50` "remaining-questions: <list \| none>" | NO EDIT — adopt the vocabulary in our contract |
| `agents/maintenancer/skills-template/ens-db-repair.md` | `:52` | NO EDIT |
| `agents/maintenancer/skills-template/health-check.md` | `:46` | NO EDIT |
| `agents/maintenancer/skills-template/job-mission-repair.md` | `:52` | NO EDIT |
| `agents/maintenancer/skills-template/restart-upgrade-ops.md` | `:53` | NO EDIT |
| `agents/project-manager/soul.md` | `:58-59`, `:78-79`, `:93-94` "### Decisions Pending / `<0–3 framed questions>`" | NO EDIT (heading rename optional — see §10.5 note) |
| `agents/project-manager/workflow.md` | `:203-204` | clause-family edit only |
| `agents/critic/tools_note.md` | `:25` | NO EDIT |
| `agents/charter/workflow.md` | `:52`, `:16` | NO EDIT |
| `agents/designer/tools_note.md` | `:79` | NO EDIT |

### 10.8 Docs (optional scope notes)

| File | Anchors | Action |
|---|---|---|
| `docs/job-task-system.md` | `:447-482` (the "Question/stuck emission fan-out (QA events…)" section) | annotate: emitters are roots-only; the transport contract is unchanged |
| `docs/setup.md` | `:1336-1339` (the answer-gate / `user_answer_pending` runbook note) | scope the answer-gate handle to roots |
| `docs/agents.md` | `:336-352` (the tool-category table — **already stale**, no `question` row today) | optional new row; **do not inherit the existing drift** (leave the rest of the stale table alone) |

### 10.9 DO NOT REWRITE (historical / awareness)

`docs/bugs/pause-during-report-turn-orphans-message-jobitem.md` · `docs/plans/turn-reconciler-named-transitions.md` · `CHANGELOG.md` · `docs/hallucination-protection.md` · `docs/architecture/*` · the planning tree under `.agents/shared/planning/` (180 dirs at read time — awareness only, except our own directory).

---

## 11. Test plan

### 11.1 New tests T1–T8

| ID | Proposed test file | Setup | Assertion | Notes |
|---|---|---|---|---|
| **T1** | `tests/unit/tools/test_question_tool_root_registration.py` (NEW) | Drive the **REAL** `create_instance_tools` factory — copy the harness at `tests/unit/tools/test_secrets_registration.py:311-322` (`MagicMock` manager + `create_instance_tools(manager, f"inst-{agent}", agent)`); synthetic agent meta whose `tools.allow` **includes** `"question"` so the allow-filter cannot be the cause; call with `parent_id="parent-x"` | `ask_questions` **not** in the built tool names **and** no tool whose category is `question` | Must be an **absence at registration**, not a runtime refusal. The synthetic meta MUST allow `question` — otherwise the test proves nothing. |
| **T2** | same file | `parent_id=None`, `source_type` ∈ {`discord`,`slack`,`telegram`,`whatsapp`} (parametrize) | `ask_questions` **IS** in the built tool names | root shows |
| **T3** | same file | `parent_id=None`, `source_type=None` | **not** present | a job root is not a user-facing root |
| **T4** | same file | `parent_id=None`, `source_type=""` and `parent_id=""` + `source_type="discord"` | `""`-parent + chat source ⇒ **present**; `""`-parent + no source ⇒ **absent** | legacy empty-string parent idiom (`repository.py:1050`) |
| **T5** | same file | `parent_id="p"`, `source_type="discord"` | **not** present | defense: a child that somehow carries a chat source type is not a root (fail-closed) |
| **T6** | `tests/unit/services/test_child_report_open_questions_flow.py` (NEW) | A child instance completes with a report containing the section "Open questions for the orchestrator"; drive the real path | report row (`type=COMPLETION_REPORT`, `source=internal_report:<child>:<msg_id>`) → `PROCESS_REPORT` task → `ReportInjection` row → drained into the parent's turn as a `HumanMessage` | Reuse the harness shape from `tests/e2e/test_full_chain_turn_reconciler.py` / `tests/job_queue/test_job_wait_for_children.py` |
| **T7** | `tests/tools/test_send_message_status_guard.py` (EXTEND) | Parent calls `send_message(child_id, "<answer>")` while the child is `COMPLETED` | routing returns the `enqueue-revive` branch (`daemon/tools/instance.py:1418-1424`) and the child's status flips to `RUNNING` (`instance_messaging.py:2365-2381`); the instruction is delivered into the child's next turn | Existing file already covers the status guard |
| **T8** | `tests/test_question_pack_boot_rehydrate.py` + `tests/job_queue/test_midflight_qa.py` + `tests/test_question_manager.py` + `tests/job_queue/test_job_answer_tool.py` (EXTEND) | A legacy pack created **before** the gate (by a now-tool-less instance) is still answerable: RAM pack + durable shadow + rehydration + CAS; a root **without** the tool answers via `job_answer` | `set_answers` CAS returns `(pack, True)` exactly once; shadow cleared at both unwind sites; `rehydrate_from_payloads` restores the original `pack_id`; `job_answer` resolves the pack with no `ask_questions` tool in play | Proves P9 + the migration story (§15) |

### 11.2 Existing tests that may break — and the required action

| File:line | Why | Required action |
|---|---|---|
| `tests/unit/tools/test_frozen_tool_name_discovery.py:223` | `KNOWN_TOOL_NAMES` must equal the source-discovered set **exactly** | **NO EDIT** — the tool source `daemon/tools/question_tools.py` stays (only its registration is gated) ⇒ source-discovery is unchanged ⇒ pin stays green. Re-run to confirm. Breaks **only** if the question tool source is deleted/renamed (forbidden — §2.5). |
| `tests/test_innate_skills_refactoring.py:67` | expects `leader` `innate_skills == ["coordination","chart","todo"]` | **NO EDIT** — currently RED (leader still lists `question`), turns **GREEN** after P4. Re-run to confirm. |
| `tests/unit/test_devops_agent.py:165` | expects `devops` `innate_skills == ["dynamic-skill","todo"]` | **MUST EDIT** → `["dynamic-skill","chart","todo"]`. Currently RED (devops has `chart`+`question`); after P4 it stays RED unless the expectation is updated (`chart` stays — dropping it is out of scope). |
| `tests/test_chart_image_delivery_e2e.py:1418` (tripwire) | SHA-pins **35** files; **13** of them are edited by this sweep (see §11.3) | **ADJUDICATED SEAL RE-PIN** per edited file: git-log last-touch attribution → **constants-only** update of `SEALED_SHA_BASELINES` → targeted test run → provenance-labelled commit. Do **not** edit the sealed prompt files' semantics beyond the contract. |
| `tests/unit/test_report_integrity_prompts.py` | `E_REQUIRED_SNIPPETS` (`"before ending any turn"`, `"zero tool calls"`) must survive in the canonical homes; `D_REQUIRED_SNIPPETS` (`"[REPORT SANITY:"`, `"interim, not completion"`, `"send_message"`) in `D_HOME_FILES` (leader→`workflow.md`, project-manager→`rule.md`+`workflow.md`, …) | **NO EDIT expected** — but the clause-family rewrite MUST preserve the two `E` snippets verbatim in each affected canonical home. Verify per file. |
| `tests/unit/tools/test_prompt_section_reference_integrity.py` | closure sweep: no bare `.md` / bare `agents/` cross-reference tokens; no empty/glued captures | **NO EDIT** — the sweep's cross-references must be `See Open questions for the orchestrator` / `See <agent>'s Open questions for the orchestrator` (section names only). |
| `tests/test_prompt_artifact_baseline.py` | `agents/.prompt-artifacts.sha256` pins the depth-2 prompt artifacts | **REGENERATE** after the sweep: `python3 scripts/protection/generate_prompt_artifact_baseline.py` (writes the baseline; `--check --tree <dir>` verifies). Depth-2 only ⇒ `agents/<agent>/<file>.md` are covered; `agents/_prompt_system/**` and `agents/<agent>/skills-template/**` (depth 3) are **not**. |
| `tests/test_tool_filter.py:34` | frozen category→tool map contains `"question": ["ask_questions"]` | **NO EDIT** — the tool still exists in source. |
| `tests/unit/test_ari_agent.py`, `tests/unit/test_ari_worker_integration.py` | assert ari allow-list **membership** (`"job" in allow`, `"instance" not in allow`), not exact sets | **NO EDIT** — adding `"question"` to ari's allow does not break membership asserts. |
| any test asserting `ask_questions` for leader/devops | — | Re-run `grep -rn "ask_questions" tests/ --include=*.py` at implementation time (read-only grep today: hits are in `tests/e2e/test_*`, `tests/test_question_tools*.py`, `tests/job_queue/test_midflight_qa.py`, `tests/unit/test_*pause*/answer*`, `tests/test_tool_filter.py` — none assert leader/devops toolset membership). |

### 11.3 SEALED-SHA overlap (verified) — 13 files trip the tripwire

The commission's brief names 5 sealed files; **reading `tests/test_chart_image_delivery_e2e.py:1350-1398` shows 35 sealed files, and 13 of them are edited by this sweep**:

| # | Sealed file | Sweep row |
|---|---|---|
| 1 | `agents/approver/rule.md` (`:1378`) | §10.5 short |
| 2 | `agents/approver[v2]/rule.md` (`:1379`) | §10.5 short |
| 3 | `agents/architect/rule.md` (`:1380`) | §10.5 short |
| 4 | `agents/coder/soul.md` (`:1382`) | §10.4 + §10.5 |
| 5 | `agents/developer/rule.md` (`:1383`) | §10.5 short |
| 6 | `agents/doc-writer/rule.md` (`:1386`) | §10.4 |
| 7 | `agents/leader/rule.md` (`:1388`) | §10.4 |
| 8 | `agents/planner/rule.md` (`:1390`) | §10.5 short |
| 9 | `agents/project-manager/workflow.md` (`:1392`) | §10.5 long |
| 10 | `agents/reviewer/rule.md` (`:1393`) | §10.5 short |
| 11 | `agents/reviewer[v2]/rule.md` (`:1394`) | §10.5 short |
| 12 | `agents/tidier/rule.md` (`:1395`) | §10.5 short |
| 13 | `agents/tidier[v2]/rule.md` (`:1396`) | §10.5 short |

**All 35 pins are currently GREEN** (verified by hashing every entry at authoring time) ⇒ every one of the 13 needs a re-pin; there are **no** pre-existing reds to absorb. Sealed files **NOT** in the sweep (safe): `agents/ari/rule.md`, `agents/ari/workflow.md`, `agents/charter/*` (4 + 2 install-skill files), `agents/devops/rule.md`, `agents/governor/rule.md`, `agents/maintenancer/rule.md`, `agents/developer[v2]/tools_note.md`, `agents/planner[v2]/tools_note.md`, `agents/wanderer/soul.md`, `agents/_prompt_system/innate-skills/chart/skill.md`, `daemon/constants.py`, `daemon/sources/*` (5 daemon files).

### 11.4 Exact commands

Canonical invocation (per `.agents/tester/PACKS.md` house idiom; `pytest` config in `pyproject.toml:86-97` — `testpaths=["tests"]`, `addopts="-m 'not integration and not postgres and not slow'"`, `asyncio_mode=auto`, `timeout=30`):

```bash
cd /home/nea/ensemble-src
FENCE="env -u POSTGRES_DB -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_USER -u POSTGRES_PASSWORD -u DATABASE_URL"

# ── NEW tests (Phase 2) ─────────────────────────────────────────────
timeout 300 $FENCE .venv/bin/pytest \
  tests/unit/tools/test_question_tool_root_registration.py \
  tests/unit/services/test_child_report_open_questions_flow.py \
  -q --tb=short -p no:randomly

# ── T7 / T8 extensions ──────────────────────────────────────────────
timeout 300 $FENCE .venv/bin/pytest \
  tests/tools/test_send_message_status_guard.py \
  tests/test_question_manager.py \
  tests/test_question_pack_boot_rehydrate.py \
  tests/job_queue/test_midflight_qa.py \
  tests/job_queue/test_job_answer_tool.py \
  -q --tb=short -p no:randomly

# ── registration / registry pins (Phase 6) ──────────────────────────
timeout 300 $FENCE .venv/bin/pytest \
  tests/unit/tools/test_frozen_tool_name_discovery.py \
  tests/unit/tools/test_upgrade_registration.py \
  tests/unit/tools/test_attestation_registration.py \
  tests/unit/tools/test_secrets_registration.py \
  tests/unit/tools/test_ens_env_registration.py \
  tests/unit/tools/test_service_registration.py \
  tests/integration/test_maintenancer_spawn_resolves_tools.py \
  tests/integration/test_service_tool_flag_off_byte_identical.py \
  -q --tb=short -p no:randomly

# ── prompt-integrity pins (Phase 5/6) ───────────────────────────────
timeout 300 $FENCE .venv/bin/pytest \
  tests/unit/test_report_integrity_prompts.py \
  tests/unit/tools/test_prompt_section_reference_integrity.py \
  tests/test_prompt_artifact_baseline.py \
  tests/test_innate_skills_refactoring.py \
  tests/unit/test_devops_agent.py \
  tests/unit/test_ari_agent.py \
  tests/unit/test_ari_worker_integration.py \
  tests/test_tool_filter.py \
  -q --tb=short -p no:randomly

# ── SEAL TRIPWIRE (Phase 6 — after each re-pin batch) ──────────────
timeout 300 $FENCE .venv/bin/pytest \
  tests/test_chart_image_delivery_e2e.py::TestPhaseDDoesNotModifySealedArtifacts \
  -q --tb=short -p no:randomly

# ── wider sweep (dir-level, xdist) ──────────────────────────────────
timeout 600 $FENCE .venv/bin/pytest tests/unit/tools tests/unit/services -q --tb=short -n 8
```

House idioms to keep: `timeout` wrapper (outer) + `--tb=short` + `-p no:randomly`; `-n 8` only for dir packs; ambient `POSTGRES_*`/`DATABASE_URL` scrub is **load-bearing** (ambient live-prod poison); `--override-ini="timeout=280"` for long tests.

---

## 12. Sequencing & branch/worktree discipline (P10)

### 12.1 The gate — verified NOT merged at authoring time

| Fact | Evidence at authoring |
|---|---|
| Sibling branch exists | `fix/question-pause-resume-asymmetry` |
| Sibling worktree | `/home/nea/ensemble-src-wt-qprasym` @ `1ebdf469f` (**same commit as `latest`**) ⇒ **0 commits ahead, not merged** — and **NOT clean**: 7 modified tracked files (incl. `daemon/services/instance_lifecycle.py`, the file we must thread) + 3 new untracked (`daemon/services/question_pause_set.py`, `daemon/services/stale_pause_sweep.py`, `tests/unit/test_question_pause_resume_asymmetry.py`); diffstat `7 files changed, 311 insertions(+), 1 deletion(-)`. **Correction to the brief** (which said "0 commits ahead, clean"): the sibling is further from merge than assumed ⇒ the Phase 0 wait is longer, and the gate is *strengthened*, not relaxed. See `decisions.md` P10 |
| Their scope | `daemon/services/question_manager.py`, `daemon/routers/answer_helper.py`, `daemon/services/instance_messaging.py`, `daemon/services/instance_lifecycle.py` |
| **Overlap with us** | `daemon/services/instance_lifecycle.py` param threading at `:2137` / `:4618` — keep **additive + minimal** (two keyword arguments, no restructuring) |
| **Disjoint** | `daemon/tools/instance.py:5895` (the gate) — no file-level conflict |
| Their worktree | **MUST NOT be touched** |

⇒ **Phase 0 is a hard wait**: do not start implementation until `fix/question-pause-resume-asymmetry` is merged into `latest` and `latest` is re-read (the sibling merge may shift our line numbers — re-verify every anchor in §1-§3 at that moment).

### 12.2 Branch & worktree

| Rule | Value |
|---|---|
| Branch | `feature/hide-question-tool-nonroot`, branched from `latest` **after** the sibling merge |
| Worktree | a **new** worktree (e.g. `/home/nea/ensemble-src-wt-hideqtool`); never reuse another commission's worktree |
| Sibling worktree | never read-write, never commit into |
| Commits | pathspec-scoped per phase; seal re-pins as a **separate, provenance-labelled** commit (constants-only) |

### 12.3 KV coordination artifacts (merge, do not clobber)

| Row | Content | Rule |
|---|---|---|
| census | `wt.active.feature/hide-question-tool-nonroot.<task-id>` | write **BEFORE** spawning giter |
| claim | `wt.claim.hide-question-tool-nonroot` (leader-owned: `blocked_by` = "ffeb7190 (pause/resume symmetry) merged to latest", `overlap_files` = the 6 file list, `planned_branch`, `commission`, `opened`) | if giter writes its own claim row under the **same slug**, **MERGE** — preserve the leader's `overlap_files` / `blocked_by` keys |
| plan artifacts | `.agents/shared/planning/hide-question-tool-nonroot/` | this file + `decisions.md` |

---

## 13. Staging (P11)

**Stage only — NO promote.** The user's 3-factor nonce ceremony owns promotes.

| Step | Action | Verification |
|---|---|---|
| 1 | Merge the feature branch into `latest` (`--no-ff`, audit-trail branch pushed) | `git log --oneline` shows the merge |
| 2 | Version bump via the project convention: `python3 scripts/bump_version.py` (`scripts/bump_version.py:1-30`) | **If it dies at an off-PATH `uv-sync`, complete manually with EXACT semantics — 4 files: `pyproject.toml`, `daemon/__init__.py`, `tests/test_api.py`, `uv.lock`** |
| 3 | Regenerate `agents/.prompt-artifacts.sha256` (if any `agents/**` file changed) | `python3 scripts/protection/generate_prompt_artifact_baseline.py` |
| 4 | Stage: `bash scripts/upgrade/stage.sh <version>` (Makefile target `upgrade-stage` → `Makefile:369-370`) | provenance sidecars written; **tip-identity freshness guard** (L1: `HEAD == refs/remotes/origin/latest`; L2 sidecar re-hash) must pass — a non-tip tree exits **78** `non-tip-tree` |
| 5 | **Keep the staging worktree** (both provenance sidecars intact) for the promote ceremony | avoids the v0.25.2 provenance-evidence gap |
| 6 | **Migration delta = EMPTY** — no file under `daemon/migrations/versions/` changes (this commission touches no schema) | `git diff <base>..<head> -- daemon/migrations/versions/` → empty |
| 7 | **Rollback-safe adjudication (STANDING RULE — explicit semantic reasoning, not a reflex `=0`)** | `ENSEMBLE_ROLLBACK_SAFE=1` is legitimate: (1) zero migration files added/changed ⇒ no schema mutation to reverse; (2) the daemon change is a **build-time tool-registration conditional** — reversible by the next release; (3) the `meta.json` grants are additive prompt/tool config, no data state; ⇒ `rollback_safe=true`, `source=explicit=true`. Record the reasoning in the stage log line (`rollback_safe=true source=explicit=true`). |
| 8 | Do **not** flip `current`; do **not** restart the daemon | the live symlink and the live epoch must be unchanged by this commission |
| 9 | Report the staged path, binary sha256, migration-delta verdict, and the seal-re-pin provenance in the final report | — |

---

## 14. Risk register

| ID | Risk | Impact | Mitigation | Verification action |
|---|---|---|---|---|
| **R1** | Granting `ari` the `question` category changes **LIVE Discord chat behavior** (new capability for the chat-facing agent; a mid-turn pack pause in a chat lane could strand a turn) | high | Root-scoped skill steers *"prefer asking in my reply text"* for chat lanes (§5.1 #2); the existing wedge guard + answer path protect; **flag explicitly in the final report** | Read the rewritten skill end-to-end before merge; confirm the chat-lane preference sentence is present; no live promote performed (stage only) |
| **R2** | The mappings-API lane (P3) could regress the `source_type` **single-writer** invariant | high | Thread the type into the **existing** writer (`instance_lifecycle.py:2390`) — never add a second metadata writer; keep the edit additive (one keyword at one call site) | `grep -n 'source_type' daemon/services/instance_lifecycle.py` ⇒ exactly one assignment to `instance_metadata["source_type"]`; new test asserting a mappings-lane root resolves the tool |
| **R3** | Stale `_tool_metadata` residue may leave `"question"` listed on the help surface | low (cosmetic) | `_tool_metadata` is a module-level global (`:18`) only cleared by `clear_registry()` (`:625-628`) and added to by `scan_tools_for_full_docs` (`:660-665`); the help surface (`:105-115`) is independent of the gate | Verify the help listing after the change; document the residue in the report — **do not** chase it |
| **R4** | Legacy in-flight packs at deploy time | medium | Migration story (§15): packs live in RAM **and** are shadowed into `instance_metadata['question_pack_payload']` ⇒ the answer path resolves them (CAS + shadow clear + boot rehydration). **No pack is orphaned by the gate** | T8 |
| **R5** | The sibling commission merges into `latest` while we implement | medium | Implement **only after** the merge (Phase 0 gate); never touch their worktree; keep `instance_lifecycle.py` edits additive + minimal | Re-read `latest` after the merge; re-verify §1-§3 anchors (line numbers shift) |
| **R6** | Prompt sweep breadth (59 files) risks prompt-integrity test failures and seal/hash pins | medium | §11.2 pin list + §11.3 the 13-file seal overlap; **require targeted test runs per phase**; re-pin constants-only, provenance-labelled | Run §11.4's prompt-integrity + seal-tripwire commands after each sweep phase, not once at the end |

---

## 15. Migration story

After the gate, **no NEW pack can be created by a non-root** (the only production pack writer is `daemon/tools/question_tools.py:502`).

Any pack that already exists for a non-root **stays routable**:

| Stage | Where | Behavior |
|---|---|---|
| In RAM | `daemon/services/question_manager.py:206`, `:224-225` (`self._packs`, guarded by `self._lock`) | live pack |
| Durable shadow | `daemon/services/midflight_qa.py:391` `stamp_question_pack_metadata` → `instance_metadata['question_pack_payload']` | survives restart |
| Resolution | `daemon/routers/answer_helper.py:262-282` (RAM pack → durable shadow → `rehydrate_from_payloads`) | resolvable |
| Arbitration | `daemon/services/question_manager.py:321-373` `set_answers` (CAS, exactly-once) | no double-answer |
| Shadow clear | `daemon/services/midflight_qa.py:426` `clear_question_pack_metadata` (both unwind sites) | no resurrection |
| Restart | `daemon/api.py:338` `_boot_rehydrate_question_packs` → `question_manager.rehydrate_from_payloads` (`:408`, idempotent, preserves the original `pack_id`, `:487-495`) | durable across restart |

⇒ **No migration / DB change is required; no pack is orphaned by the gate.** A root **without** the tool can still answer a child's legacy pack via `job_answer` (category `job`). **Verify with T8.**

---

## 16. Coupling map + implementation order

### 16.1 Coupling map (file-level)

| Phase | Files | Coupled to | Strength |
|---|---|---|---|
| 1 | `daemon/tools/instance.py`, `daemon/services/instance_lifecycle.py`, `daemon/routers/mappings.py` | §2 gate ↔ §3 lane (both thread `source_type`) | **strong** — the lane fix is worthless unless the gate reads the threaded value |
| 2 | `tests/unit/tools/test_question_tool_root_registration.py` (NEW), `tests/unit/services/test_child_report_open_questions_flow.py` (NEW) | Phase 1 (asserts the gate); §6/§7 (report + continuation) | strong |
| 3 | `agents/{leader,devops,ari}/meta.json` | Phase 1 (defense-in-depth); the `ari` grant needs the gate to let it through | strong |
| 4 | `agents/_prompt_system/innate-skills/question/skill.md` | Phase 3 (only `ari` receives it afterwards) | medium |
| 5 | 59 prompt/doc files | Phase 4 (vocabulary); the guide's canonical-home rule (§10.1); 13 seal re-pins | **strong** (breadth) |
| 6 | 13 seal constants + targeted tests | Phase 5 | strong |
| 7 | `scripts/upgrade/*`, `scripts/bump_version.py` | Phases 1-6 merged | medium |

### 16.2 Phase order (dependency rationale per phase)

| Phase | Content | Depends on | Rationale |
|---|---|---|---|
| **0** | **Wait for the sibling merge.** Poll `latest` until `fix/question-pause-resume-asymmetry` is merged; then re-read `latest` and re-verify every §1-§3 anchor (line numbers shift). | — | P10 hard gate; R5 |
| **1** | **Daemon gate + mappings lane.** `daemon/tools/instance.py` (predicate helper + conditional + keyword-only signature), `daemon/services/instance_lifecycle.py:2137` & `:4618` (threading), `daemon/routers/mappings.py:114-117` (`source_type=source.source_type`). | Phase 0 | The gate is the authoritative control; both call sites must be threaded **in the same change** or the restore path silently loses the tool for roots |
| **2** | **New tests T1-T5** (registration) in `tests/unit/tools/test_question_tool_root_registration.py`. | Phase 1 | Proves the gate before any prompt work; a green T1/T2 pair is the go/no-go for Phase 3's ari grant |
| **3** | **`meta.json` grants** — leader/devops remove, ari add (§10.3). Plus the `tests/unit/test_devops_agent.py:165` expectation fix. | Phase 2 green | Defense-in-depth only after the authoritative gate is proven; the ari grant is what makes "roots still ask" real |
| **4** | **Root-scoped skill rewrite** (§5.1) of `agents/_prompt_system/innate-skills/question/skill.md`. | Phase 3 | The skill must teach root behavior **after** only ari holds it |
| **5** | **Prompt & doc sweep** (§10.4-§10.8) — 59 files, intra-agent canonical homes (§10.1), guide rules R-a..R-k (§10.2). | Phase 4 | The sweep uses the final vocabulary; doing it before the skill rewrite risks two vocabularies |
| **6** | **Targeted test runs + seal re-pin adjudication** (§11.2-§11.4): 13 constants-only re-pins (git-log last-touch attribution → constants-only update → targeted test → provenance-labelled commit) + the prompt-artifact baseline regeneration + `tests/unit/test_devops_agent.py` expectation. | Phase 5 | The tripwire only fails **after** the edits; re-pin is a discrete, auditable act |
| **7** | **Merge + version bump + stage (no promote)** (§13). | Phases 1-6 | Staging consumes the merged tree; the EMPTY migration delta ⇒ `rollback_safe=true` (explicit reasoning) |

---

## 17. Definition of done / exit criteria

- [ ] The gate exists at `daemon/tools/instance.py:5895-5900` as a **build-time conditional**; `ask_questions` is **absent** (not refused) from every non-root toolset.
- [ ] `parent_id` + `source_type` are threaded **keyword-only** into `create_instance_tools` from **both** call sites (`instance_lifecycle.py:2137` spawn, `:4618` restore).
- [ ] The predicate is evaluated from **threaded values** — no DB row read at registration (verified by code inspection + T1-T5).
- [ ] `PRIVILEGED_TOOL_CATEGORIES` is **unchanged** (8 entries) and `"question"` **remains** in `CATEGORY_MODULES` (`:729`).
- [ ] The mappings lane passes `source_type` into the **single** writer; exactly one `instance_metadata["source_type"] =` assignment exists in the codebase.
- [ ] `meta.json`: leader/devops lose `"question"` (allow + innate_skills); ari gains it; project-manager / critic / watcher untouched (watcher optional hygiene).
- [ ] The Question Skill teaches **root** behavior + the chat-lane preference + the non-root half, in first person, with **no forbidden-layer tokens** (guide R-b).
- [ ] Every non-root agent's contract is stated **once per agent** in its canonical home; same-agent cross-references use section names only (guide R-f); no false "stated once" claim (guide R-e).
- [ ] `tests/unit/test_devops_agent.py:165` updated; `tests/test_innate_skills_refactoring.py:67` verified GREEN; `tests/unit/tools/test_frozen_tool_name_discovery.py:223` verified GREEN (source unchanged).
- [ ] All 13 edited sealed files re-pinned with **constants-only** updates, git-log attribution, targeted test run, and a provenance-labelled commit; the tripwire `tests/test_chart_image_delivery_e2e.py::TestPhaseDDoesNotModifySealedArtifacts` is GREEN.
- [ ] `agents/.prompt-artifacts.sha256` regenerated; `tests/test_prompt_artifact_baseline.py` GREEN.
- [ ] `tests/unit/test_report_integrity_prompts.py` and `tests/unit/tools/test_prompt_section_reference_integrity.py` GREEN.
- [ ] T1-T8 all pass under the fenced invocation of §11.4.
- [ ] Nothing under `daemon/migrations/versions/` changed (EMPTY delta); `rollback_safe=true` recorded with the explicit semantic reasoning.
- [ ] Staged (no promote): `releases/<ver>/` exists with provenance sidecars; the staging worktree is **kept**; `current` symlink and live daemon epoch unchanged.
- [ ] The final report states: files changed, the 13 re-pins, the ari-grant live-behavior flag (R1), the `_tool_metadata` residue (R3), and that **no promote** was performed.

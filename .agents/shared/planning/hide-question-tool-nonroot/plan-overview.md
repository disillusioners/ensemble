# hide-question-tool-nonroot — Implementation Plan (Plan Overview)

> **Artifact map.** This file = the implementation plan. The rationale record (P1..P11 decisions, rejected alternatives, evidence) lives in the sibling file **`.agents/shared/planning/hide-question-tool-nonroot/decisions.md`** — link to it by heading, do not duplicate it here. P-numbers below refer to that file's decision IDs exactly as issued by the commission.
>
> Every `file:line` in this plan was READ at authoring time against `latest` @ `1ebdf469f` (v0.25.7, branch `latest`, repo `/home/nea/ensemble-src`), and the **post-sibling-merge anchors were re-verified at `0e7500a1b`** (this branch, after the `e2899f8bd` merge) — the drift table is in §12.1. Anchors that could not be verified are marked **`UNVERIFIED — confirm at implementation time`**.

---

## 0. Header

| Item | Value |
|---|---|
| **Goal** | The `ask_questions` tool is **hidden** (never registered in an instance's toolset — NOT a runtime refusal) from every non-root instance. Only user-facing root instances keep it. Non-root children surface blockers in their completion report under the canonical section **"Open questions for the orchestrator"**; the parent answers per its own authority and instructs the child through the existing continuation path, or forwards upward, or (if the parent is the root) asks the user through its own tool. |
| **Capability model (two layers)** | **Effective capability = {user-facing root} ∩ {agent grants the category}.** The **GATE** (§2) is the authoritative **hiding** control — *necessary*: a non-root NEVER gets the tool registered, even if its agent still lists `"question"`. The **ALLOW-LIST** (`meta.json`) is the **capability** grant — *sufficient*: a user-facing root only actually receives the tool if its agent grants the category. Neither layer alone is the whole truth. |
| **Non-goals** | (a) No change to the mid-flight channel. (b) No change to the question-pack machinery (create/CAS/rehydrate/shadow). (c) No DB/migration change. (d) No promote. (e) No new runtime refusal, no "deny" semantics — absence at registration only. (f) No rewrite of historical docs / CHANGELOG / planning records. (g) **Scoping limitation (RULING-1, disclosed — risk F3):** a user-facing root whose agent does **NOT** grant the `"question"` category cannot ask. The mappings API can bind ANY agent to a chat source; today no non-ari agent is known chat-bound ⇒ the gap is **LATENT, not active**. |
| **Commission status** | **Design PINNED** (P1..P11 authoritative; the plan review returned **APPROVE-WITH-NOTES** and its 7 text-level blockers are folded in — §0.1). **Implementation GATED** on sibling commission `ffeb7190` (pause/resume asymmetry) landing in `latest`: its commit `e2899f8bd` is **ALREADY merged INTO THIS BRANCH** (merge `0e7500a1b`) but **NOT yet in `latest`** (`git merge-base --is-ancestor e2899f8bd latest` → false) ⇒ Phase 0 is now *"merge `latest` INTO this branch"* (BLK-6, §12). |
| **Scope tier** | **Full-stack behavioral change** — daemon tool-build gate + mappings-lane fix + 3 `meta.json` grants + shared innate-skill rewrite + **59 sweep rows** (54 files actually edited = 3 `meta.json` + 51 prompt/doc; **55** with `agents/governor/rule.md` — machine-derived manifest §10.10) + **14** adjudicated seal re-pins (of a **35**-file sealed baseline) + staged (not promoted) release. |
| **Why this tier** | The user-visible outcome ("roots still ask") needs BOTH halves: the daemon gate (authoritative hiding) **and** the `ari` grant (otherwise "roots still ask" is vacuous — ari holds neither `question` in `tools.allow` nor in `innate_skills` today). The prompt sweep breadth is forced by the "or ask" clause family living in 35 prompt files; **14** of those are sealed-SHA-pinned and therefore force adjudicated re-pins. |
| **Branch (existing)** | `feature/hide-question-tool-nonroot` — **already created**; the worktree `/home/nea/ensemble-src-wt-hidenonroot` **locks** the branch. HEAD `0e7500a1b` (merge of the sibling `e2899f8bd`). **REUSE** it; after the sibling lands in `latest`, run `git merge latest` **INTO** this branch — **MERGE, never rebase** (BLK-6, §12.2). |
| **Plan artifacts** | `.agents/shared/planning/hide-question-tool-nonroot/plan-overview.md` (this file) + `decisions.md` (sibling worker — NOT written here). |

### 0.1 Plan-review amendments folded in (review 2026-10-20 — APPROVE-WITH-NOTES)

The plan review confirmed the **design** sound and reopened **no** pinned decision (P1..P11). Its 7 blocking findings were **text-level** and are folded in here; every numeric constant below is canonical and shared with `decisions.md`.

| Finding | Disposition | Landed in |
|---|---|---|
| **BLK-1** | Two-layer model: gate = hiding (necessary), allow-list = capability (sufficient). leader/devops **KEEP** `"question"` in `tools.allow`, **REMOVE** it from `innate_skills`; ari **ADDS** both. Scoping limitation disclosed. Discriminating **T9** added. | §0, §4, §10.3, §10.5, §11.1, §14 (R7/F3/F6), §17 |
| **BLK-2** | `agents/governor/rule.md:82` joins the clause-family sweep (14th re-pin); `agents/governor/workflow.md:46` becomes a **section-name pointer** (RULING-2 — the governor's contract lives once, in `rule.md`). | §10.5, §10.6, §11.3 |
| **BLK-3** | The three **semantic** pins the SHA seal does NOT cover are named (attestation contract · mission-watch vocabulary · "Chat Delivery") — **NO EDIT expected — verify**. | §11.2, §11.4, §17 |
| **BLK-4** | §17 final-report contract **replaced** by the commission's 7 items **verbatim** + the 3 existing extras (10 total). | §13 (step 9), §17 |
| **BLK-5** | **Phase 7 = reviewer council** (0-critical bar, owner: **reviewer**); **Phase 8 = independent tester** (owner: **tester**); staging shifts to **Phase 9**. | §16, §17 |
| **BLK-6** | This commission's **own** branch + worktree already exist ⇒ **reuse**; `git merge latest` INTO the branch, **never rebase**; both KV rows **exist** ⇒ Phase 0 KV work is verify/merge, not create. | §12.1, §12.2, §12.3 |
| **BLK-7** | §11.1 gains a **"Red pre-change?"** column; T4 splits into **T4a** (preservation) / **T4b** (gate proof); proof set named so a green preservation run is never reported as gate proof. | §11.1, §11.4, §17 |
| N-series | N1 (unpinned `tool_count` −1; no `ask_questions` membership assertion today) · N3 (`daemon/sources/*` = **6**) · N4 (machine-derived sweep manifest) · N5 (R3 mechanism) · N6/F1 (root-only fan-out) · N7 (one chat-source taxonomy + three-way pin) · N8 (legacy unstamped rows — no backfill) · N10 (skill = **53** lines) · N12 (`job_answer` def at **:3403**) · N13 (keep `whatsapp`) · N14 (D-snippet verify clause). | see each section |
| F-series | **F1..F8** added to the risk register with mitigation + verification action. | §14 |

---

## 1. P1 — Root predicate (PINNED)

### 1.1 Exact expression

```
USER_FACING_ROOT := (parent_id IS NULL OR parent_id == "")
                     AND (source_type IN {"discord", "slack", "telegram", "whatsapp"})
```

| Element | Value | Source of truth (read-verified) |
|---|---|---|
| Root test idiom | `(Instance.parent_id.is_(None)) | (Instance.parent_id == "")` | `daemon/repositories/instance/repository.py:1109` (and `:1134`) — legacy rows carry `""` |
| Chat-source set | `("telegram", "slack", "discord", "whatsapp")` | `daemon/repositories/instance/repository.py:919` — the **class-attr definition** of `SQLModelInstanceRepository.CHAT_SOURCE_TYPES` (the `:343` / `:346` / `:374` hits are doc/use sites, not the definition) |
| `source_type` writer | `instance_metadata["source_type"] = source_type` | `daemon/services/instance_lifecycle.py:2390` — **the only writer**; guarded by `if source_type:` at `:2389` |
| `original_source` | **NEVER use** | It *is* inherited by children → a chat child would look like a root. `source_type` is not inherited. |

### 1.2 Why `source_type` and never `original_source`

`source_type` is written in exactly one place (`instance_lifecycle.py:2390`) and is never propagated to children; `original_source` **is** inherited, so a chat-sourced child would satisfy a naive predicate. Live data (commission audit): all 14 chat instances are roots with 0 chat-source children — **UNVERIFIED — confirm against `ensemble_prod` at implementation time** (the reachable `ensemble_dev` holds 34 instances / 23 roots / **0** chat-source instances). The predicate fails closed, so only the blast-radius estimate depends on this claim; see `decisions.md` P1.

### 1.3 EVALUATION RULE (thread, never read)

The predicate **must** be evaluated from values **threaded into the tool build**, never from a DB row read at registration time.

| Why | Evidence |
|---|---|
| At spawn the instance row is **not INSERTed yet** | registration `instance_lifecycle.py:2137` vs the DB write delegated to `_spawn_instance_db_sync` (called `:2433`, helper `:5343`, `Instance(...)` `:5417`, `session.commit()` `:5451`) |
| The `instance_mappings` row is created **after** spawn returns | `daemon/sources/mapper.py:362` (spawn) vs `:379-387` (mapping insert) |
| A row-reading gate would silently strip the tool from **every freshly spawned chat root** | consequence of the two rows above |

⇒ The gate is a **build-time conditional on threaded values** (§2). Both threaded values exist at both call sites (§2.3).

**Legacy rows without the `source_type` stamp — PRE-EXISTING, NO BACKFILL (N8).** The single writer (`instance_lifecycle.py:2390`) has existed since **2026-08-12**; rows created before it carry no `source_type` in `instance_metadata`. Such a row is **not** a user-facing root for predicate purposes ⇒ on restore it receives **no** question tool. ⇒ The §2.3 note *"a root restored from DB keeps the tool"* holds **ONLY** for a root whose row carries the stamp. **No backfill** is planned: a backfill would make the migration delta non-empty and break the P11 EMPTY-delta premise (§13 step 6, §15). Risk **F8** — out of scope by decision.

### 1.4 Recorded asymmetry (NOT a re-litigation — pinned set is the 4-member set)

The precedent helper `append_platform_context` whitelists a **3-member** platform set (`discord`, `slack`, `telegram` — keys of `_PLATFORM_INSTRUCTIONS`, `daemon/services/instance_lifecycle.py:1006-1036`); `whatsapp` has no adapter (see the interactive-chat vs registry-set split documented at `daemon/constants.py:128-144`). P1 is pinned to the **4-member registry set** (`repository.py:919`). **N13 — `whatsapp` STAYS in the predicate set** (forward-compat): `daemon/constants.py:128-144` lists **no whatsapp adapter**, and `_PLATFORM_INSTRUCTIONS` (`instance_lifecycle.py:1006`) covers only discord/slack/telegram — the asymmetry is recorded verbatim at `daemon/tools/upgrade_journal.py:3337` ("…but NO whatsapp adapter exists in …"). Keeping `whatsapp` costs nothing today and avoids a second taxonomy edit when an adapter lands. Consequence to document in the code comment: a `whatsapp` root passes the P1 predicate and therefore receives `ask_questions`, but gets **no** platform prompt section (no adapter). Also note the precedent's Gate 1 (`if parent_id is not None: return`, `:1075-1076`) treats `parent_id == ""` as root-eligible — the same treatment P1 gives legacy empty-string roots.

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
# (daemon/repositories/instance/repository.py:919).
#
# N7 DECISION — do NOT let the chat-source taxonomy become a THIRD copy.
# The two existing authorities are:
#   * daemon/repositories/instance/repository.py:919
#       SQLModelInstanceRepository.CHAT_SOURCE_TYPES  (tuple)
#   * daemon/tools/upgrade_journal.py:3358
#       USER_ORIGIN_CHAT_SOURCE_TYPES                 (frozenset)
# PREFERRED: import one of them outright (the semantics match — the same
# 4-member set) and derive this module's constant from it.
# FALLBACK (if a tool-module -> repository import is refused by the layering
# rule): keep ONE local canonical set here and add a THREE-WAY equality
# unit pin asserting
#     local == SQLModelInstanceRepository.CHAT_SOURCE_TYPES
#           == USER_ORIGIN_CHAT_SOURCE_TYPES
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

**N7 — one canonical chat-source taxonomy.** Two copies already exist (`repository.py:919` tuple, `upgrade_journal.py:3358` frozenset); the plan's local set must not become a third. Implementation: **prefer importing** the existing constant whose semantics match (either one) and derive `CHAT_SOURCE_TYPES_FOR_ROOT` from it; if the layering rule forbids the import, keep exactly one local definition **plus a three-way equality unit pin** (local == `SQLModelInstanceRepository.CHAT_SOURCE_TYPES` == `USER_ORIGIN_CHAT_SOURCE_TYPES`). The pin is mandatory in both variants — it is what makes the drift visible instead of silent.

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
| **restore** | `daemon/services/instance_lifecycle.py:4690` → `create_instance_tools(self._manager, instance_id, resolved_agent_id, version_tag=resolved_tag)` | add `parent_id=meta.parent_id, source_type=source_type` | `meta.parent_id` at `:4675`; `source_type` read from the instance row's `instance_metadata` at `:4667-4668` (survives restart) — **all three anchors SHIFTED by the sibling merge `0e7500a1b`** |

> The restore path is what makes the gate durable across daemon restarts: a root restored from DB **whose row carries the `source_type` stamp** keeps the tool, a child restored from DB stays tool-less. (Legacy unstamped rows are PRE-EXISTING and out of scope — §1.3, risk **F8**.)

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

Defense-in-depth + prompt hygiene. **The authoritative control is the P2 gate** (§2) — it is *necessary* (it **hides** the tool). The allow-list is *sufficient* (it grants the **capability**). **Effective capability = {user-facing root} ∩ {agent grants the category}** (RULING-1).

| File | Line (verified) | Current | Change | Effect |
|---|---|---|---|---|
| `agents/leader/meta.json` | `:8` | `"innate_skills": ["coordination", "chart", "todo", "question"]` | **REMOVE `"question"`** ⇒ `["coordination","chart","todo"]` | The Question Skill **text** stops landing in leader prompts; drops the innate→category grant (`INNATE_SKILL_TOOL_CATEGORIES["question"] → ["question"]`, `daemon/tools/instance.py:158-164`). The leader **RETAINS the capability** (it keeps `"question"` in `tools.allow`) — this is a **teaching gap, not a capability gap** (risk **F6**). Side effect: the already-red pin `tests/test_innate_skills_refactoring.py:67` turns **GREEN**. |
| `agents/leader/meta.json` | `:15` | `tools.allow` contains `"question"` | **KEEP** | RULING-1: the allow-list is the **capability** grant. Removing it would collapse effective capability to `{ari}` only — a leader bound to a chat source (mappings API) would pass the gate and then be stripped by `_apply_tool_filter` ⇒ it could not ask, contradicting the policy. |
| `agents/devops/meta.json` | `:8` | `"innate_skills": ["dynamic-skill", "chart", "question", "todo"]` | **REMOVE `"question"`** ⇒ `["dynamic-skill","chart","todo"]` | Same split as the leader: skill **text** gone, **capability** retained. `tests/unit/test_devops_agent.py:165` must be edited to this exact list (it is currently RED for a different reason). |
| `agents/devops/meta.json` | `:12` | `tools.allow` contains `"question"` | **KEEP** | RULING-1 — capability grant stays. |
| `agents/ari/meta.json` | `:9` | `"innate_skills": ["job-orchestration", "chart", "todo"]` | **ADD `"question"`** | ari is the default chat-facing root; grants the tool + the (rewritten) Question Skill prompt (root teaching) |
| `agents/ari/meta.json` | `:11-38` (`tools.allow` list) | no `"question"` | **ADD `"question"`** | without this, "roots still ask" is vacuous — ari is the live Discord root |
| `agents/project-manager/meta.json` | `:64` | `"question"` inside **`tools.deny`** | **UNCHANGED** | already compliant (deny ≠ grant) |
| `agents/critic/meta.json` | `:9` / `:13` | `innate_skills: ["dynamic-skill","todo"]`; `allow: ["read_file","image","design"]` | **UNCHANGED** | never held the tool |
| `agents/watcher/meta.json` | `:22` | `"allow": []` (empty ⇒ default-open universe) | **OPTIONAL hygiene**: pin an explicit allow list | today watcher HOLDS `ask_questions` by default-open; the gate strips it — behaviour delta, risk **F2**. Pinning the allow list is hygiene, not correctness. |

> **Never set `innate_skills` to `[]`** — `daemon/loader.py:863` treats an empty array as *absent* and falls back to a legacy `skills/` scan. Removing one element keeps the array non-empty for leader/devops (3 elements each) — safe.

> **RULING-1 limitation (disclose in the final report, risk F3):** a **user-facing root whose agent does not grant the category cannot ask**. The mappings API (§3) can bind ANY agent to a chat source; today no non-ari agent is known chat-bound ⇒ the gap is **LATENT, not active**. The end-to-end proof of the two-layer model is **T9** (§11.1).

---

## 5. P5 — Root-scoped skill rewrite (PINNED)

**Target file:** `agents/_prompt_system/innate-skills/question/skill.md` (**53** lines today; loaded by name through `daemon/loader.py:841-847` `_resolve_innate_skill_paths` → `agents/_prompt_system/innate-skills/<name>/skill.md`).

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
| Root answering without the tool | `job_answer` tool (category `job`) — **def at `daemon/tools/job_queue.py:3403`** (`async def job_answer`); tool docstring at `:620`; prose at `:872-894`. (The council's `:770` is a **call site inside the docstring**, not the def — N12.) | ✅ |

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
> **Cross-agent single home** for the *ask* contract itself: `agents/_prompt_system/innate-skills/question/skill.md` — one shared file, injected by name (`daemon/loader.py:841-847`); after P4 it lands in **ari only** (leader/devops lose the innate-skill grant but **KEEP** the capability — RULING-1), so it becomes the **ROOT-TEACHING** home. Non-root agents get their contract from their own files (§10.3).

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
| `agents/leader/meta.json` | `:8`, `:15` | **remove** `"question"` from `innate_skills` (⇒ `["coordination","chart","todo"]`); **KEEP** it in `tools.allow` | P4 / RULING-1; SEALED? **no** (not in the 35) |
| `agents/devops/meta.json` | `:8`, `:12` | **remove** `"question"` from `innate_skills` (⇒ `["dynamic-skill","chart","todo"]`); **KEEP** it in `tools.allow` | P4 / RULING-1; not sealed |
| `agents/ari/meta.json` | `:9`, `:11-38` | **add** `"question"` to `innate_skills` + `tools.allow` | P4; not sealed. `agents/ari/rule.md` and `agents/ari/workflow.md` are **SEALED — do not touch** |

### 10.4 Class B — ask-guidance → open-questions contract

Canonical-home column = **the file that owns the contract for that agent**; every other file of the same agent carries `See Open questions for the orchestrator` (or `See <agent>'s Open questions for the orchestrator` cross-agent) and **no filename token**.

| File | Anchors (verified content) | Change | Canonical home for this agent | Seal pin |
|---|---|---|---|---|
| `agents/_prompt_system/innate-skills/question/skill.md` | whole file (**53** lines) | FULL REWRITE per §5.1 | cross-agent shared home / **root-teaching** home | not sealed (chart/skill.md is) |
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

**Conditional clause (RULING-1 / risk R7):** the rewrite applies **unless you are the user-facing root**. A root's own ask wording must NOT be degraded — a root bound to a chat source keeps "ask the user" as its move (the tool is the root's ask channel). Where the sentence is agent-agnostic, render it as `…or report open questions (unless you are the user-facing root)`. Mitigation: the rewritten shared skill (`agents/_prompt_system/innate-skills/question/skill.md`) teaches the **root half**, so the sweep only removes the *non-root* default and never removes the root's capability.

**Short form (15 files)**

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
| `agents/governor/rule.md` | `:82` — "**Before ending any turn** on a task dispatched to me, I do one of: begin the work with a tool call, deliver the synthesis on work already done, **or ask the question that blocks me**." | **SEALED** (sha `84a93586df376ad39dc553af36f86f684977ecfa8d717186f2fe8f16846c8cf5`, `tests/test_chart_image_delivery_e2e.py:1387`) — **the 14th re-pin**. The line ALSO carries the junk-report clause ("…is not work-in-progress; it is detected as a junk/no-work report…") and **1 × "Chat Delivery"** — **both must survive verbatim**. Also a **D_HOME + E_HOME** carrier for the governor (`tests/unit/test_report_integrity_prompts.py:92,:106`) ⇒ the D and E snippets must survive too. |

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
| `agents/governor/workflow.md` | `:46` "**Clarifying questions — complete-to-ask (NEVER pause):** The governor has no direct human channel … there is no `ask_questions` tool … END YOUR TURN / complete with a self-contained clarifying question addressed to the requester"; `:51` the revival re-validation line | **RULING-2 — SECTION-NAME POINTER ONLY.** The governor's open-questions contract lives **ONCE**, in `agents/governor/rule.md` (the governor's D_HOME and E_HOME are both `rule.md` — `tests/unit/test_report_integrity_prompts.py:92,:106`). Replace the block's restated contract with the pointer **"See Open questions for the orchestrator"** — no filename token, no second statement of the same contract (guide R-e "stated once" trap / R-f). KEEP the semantics (`complete-to-ask`, NEVER pause) and the `:51` revival re-validation line verbatim. | not sealed |
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

### 10.10 SWEEP MANIFEST — machine-derived (N4)

**Unit of account = one distinct file in the sweep set.** A file may appear in two sweep tables (e.g. `agents/leader/workflow.md` appears in §10.4, §10.5-long and §10.6) ⇒ the table rows collapse to distinct files. The numbers below are **generated** by enumerating §10.3–§10.6 — never hand-counted — and the derivation command is embedded so any reviewer can regenerate them.

| Class | Sweep table | Table rows | Distinct files contributed | Actually edited |
|---|---|---|---|---|
| **A** — tool grant | §10.3 | 3 | 3 | 3 |
| **B** — ask → open-questions | §10.4 | 27 | 27 | 22 |
| **5-short** — clause family | §10.5 | 15 | 12 | 12 |
| **5-long** — clause family | §10.5 | 19 | 16 | 16 |
| **C** — parent-side | §10.6 | 4 | 2 | 2 |
| | **TOTAL — as amended (with `agents/governor/rule.md`)** | **68** | **60** | **55** |
| | *TOTAL — before the governor addition (the canonical "59 sweep rows" state)* | *67* | *59* | *54* |

| Canonical figure | Value | Derivation |
|---|---|---|
| **Sweep rows** | **59** (+1 ⇒ **60** with `agents/governor/rule.md`) | distinct files enumerated from §10.3–§10.6 |
| **Files actually edited** | **3 `meta.json` + 51 prompt/doc = 54** | 59 − 5 not-edited |
| **— with `agents/governor/rule.md`** | **3 `meta.json` + 52 prompt/doc = 55** | 54 + 1 |
| **Not edited (5)** | `agents/leader/tools_note.md` (**OPTIONAL**) · `agents/wanderer/skills-template/{code-investigation,codebase-mapping,library-research,root-cause-analysis}.md` (**4 × VERIFY-ONLY**) | §10.4 |

⇒ **The number the final report quotes is 55** (55 files actually edited, of which 3 are `meta.json`).

**Derivation command** (read-only; run from the repo root — it re-reads THIS file):

```bash
python3 - <<'PY'
import re
P='.agents/shared/planning/hide-question-tool-nonroot/plan-overview.md'
L=open(P,encoding='utf-8').read().split('\n')
def sec(a,b):
    i=next(i for i,l in enumerate(L) if l.startswith(a))
    j=next(j for j,l in enumerate(L) if j>i and l.startswith(b)); return L[i:j]
def rows(s):
    out=[]
    for l in s:
        if l.startswith('|'):
            c=[x.strip() for x in l.strip().strip('|').split('|')]
            if c and re.fullmatch(r'`[^`]+`',c[0]): out.append((c[0].strip('`'),' | '.join(c).lower()))
    return out
allrows = rows(sec('### 10.3','### 10.4')) + rows(sec('### 10.4','### 10.5')) \
        + rows(sec('### 10.5','### 10.6')) + rows(sec('### 10.6','### 10.7'))
longline=[l for l in sec('### 10.5','### 10.6') if 'approval-strategy.md' in l][0]
allrows += [(f,'edit') for f in re.findall(r'`([^`]+)`',longline) if f.startswith('agents/')]
seen=set(); edited=0
for f,txt in allrows:
    if f in seen: continue
    seen.add(f)
    if 'verify-only' not in txt and 'optional' not in txt: edited+=1
print('sweep rows',len(seen),'| edited',edited)
PY
```

---

## 11. Test plan

### 11.1 New tests T1–T9

**Red pre-change? (BLK-7).** The column states whether the test is expected to **FAIL** before this commission lands.

- **Gate proof — RED pre-change: T1, T3, T4b, T5, T9.**
- **Preservation / regression guards — GREEN pre-change: T2, T4a, T6, T7, T8.**

> **Non-obvious failure mode — state it explicitly.** Pre-change, `create_instance_tools` does **not** accept the `parent_id=` / `source_type=` kwargs ⇒ the gate-proof tests do **not** fail with a clean assertion error, they fail with **`TypeError: create_instance_tools() got an unexpected keyword argument 'parent_id'`** (or `… 'source_type'`). A `TypeError` in the red run is the **expected** red signature; a clean "expected absent, got present" assertion failure means the kwargs landed without the gate ⇒ treat that as a **different** (worse) defect. A **green preservation run is never reported as gate proof** (proof-set naming: §11.4, §17).

| ID | Proposed test file | Setup | Assertion | Red pre-change? | Notes |
|---|---|---|---|---|---|
| **T1** | `tests/unit/tools/test_question_tool_root_registration.py` (NEW) | Drive the **REAL** `create_instance_tools` factory — copy the harness at `tests/unit/tools/test_secrets_registration.py:311-322` (`MagicMock` manager + `create_instance_tools(manager, f"inst-{agent}", agent)`); synthetic agent meta whose `tools.allow` **includes** `"question"` so the allow-filter cannot be the cause; call with `parent_id="parent-x"` | `ask_questions` **not** in the built tool names **and** no tool whose category is `question` | **RED — gate proof** | Must be an **absence at registration**, not a runtime refusal. The synthetic meta MUST allow `question` — otherwise the test proves nothing. |
| **T2** | same file | `parent_id=None`, `source_type` ∈ {`discord`,`slack`,`telegram`,`whatsapp`} (parametrize) | `ask_questions` **IS** in the built tool names | GREEN — preservation | root shows |
| **T3** | same file | `parent_id=None`, `source_type=None` | **not** present | **RED — gate proof** | a job root is not a user-facing root |
| **T4a** | same file | `parent_id=""` (legacy empty-string root) + `source_type="discord"` | `ask_questions` **IS** present | GREEN — preservation | **PRESERVATION half** of the old T4: the legacy empty-string parent idiom (`repository.py:1109`) must keep the tool. |
| **T4b** | same file | `parent_id=""` + `source_type=""` (empty source) | **not** present | **RED — gate proof** | **GATE half** of the old T4: an empty-string parent with no chat source is not a user-facing root (fail-closed). |
| **T5** | same file | `parent_id="p"`, `source_type="discord"` | **not** present | **RED — gate proof** | defense: a child that somehow carries a chat source type is not a root (fail-closed) |
| **T6** | `tests/unit/services/test_child_report_open_questions_flow.py` (NEW) | A child instance completes with a report containing the section "Open questions for the orchestrator"; drive the real path | report row (`type=COMPLETION_REPORT`, `source=internal_report:<child>:<msg_id>`) → `PROCESS_REPORT` task → `ReportInjection` row → drained into the parent's turn as a `HumanMessage` | GREEN — preservation | Reuse the harness shape from `tests/e2e/test_full_chain_turn_reconciler.py` / `tests/job_queue/test_job_wait_for_children.py` |
| **T7** | `tests/tools/test_send_message_status_guard.py` (EXTEND) | Parent calls `send_message(child_id, "<answer>")` while the child is `COMPLETED` | routing returns the `enqueue-revive` branch (`daemon/tools/instance.py:1418-1424`) and the child's status flips to `RUNNING` (`instance_messaging.py:2365-2381`); the instruction is delivered into the child's next turn | GREEN — preservation | Existing file already covers the status guard |
| **T8** | `tests/test_question_pack_boot_rehydrate.py` + `tests/job_queue/test_midflight_qa.py` + `tests/test_question_manager.py` + `tests/job_queue/test_job_answer_tool.py` (EXTEND) | A legacy pack created **before** the gate (by a now-tool-less instance) is still answerable: RAM pack + durable shadow + rehydration + CAS; a root **without** the tool answers via `job_answer` | `set_answers` CAS returns `(pack, True)` exactly once; shadow cleared at both unwind sites; `rehydrate_from_payloads` restores the original `pack_id`; `job_answer` resolves the pack with no `ask_questions` tool in play | GREEN — preservation | Proves P9 + the migration story (§15). `job_answer` **def** at `daemon/tools/job_queue.py:3403`, docstring at `:620`. |
| **T9** | `tests/unit/tools/test_question_tool_root_registration.py` (NEW, same file) | **END-TO-END capability (BLK-1 discriminator):** (a) a **chat-root** instance whose agent **DOES** grant the category (`leader` meta — `"question"` in `tools.allow`) ⇒ (b) the **SAME agent** (`leader`) spawned as a **CHILD** (`parent_id="parent-x"`) | (a) `ask_questions` **IS** registered (gate admits **AND** allow admits) · (b) `ask_questions` is **NOT** registered | **RED — gate proof** | **The discriminating test for the two-layer model.** T2 alone only proves the gate **admits**; T9 proves gate-admits **AND** allow-admits ⇒ effective capability = {user-facing root} ∩ {agent grants the category}. Without T9, a regression that stripped `"question"` from leader's allow-list would still pass T1–T8. |

### 11.2 Existing tests that may break — and the required action

| File:line | Why | Required action |
|---|---|---|
| `tests/unit/tools/test_frozen_tool_name_discovery.py:223` | `KNOWN_TOOL_NAMES` must equal the source-discovered set **exactly** | **NO EDIT** — the tool source `daemon/tools/question_tools.py` stays (only its registration is gated) ⇒ source-discovery is unchanged ⇒ pin stays green. Re-run to confirm. Breaks **only** if the question tool source is deleted/renamed (forbidden — §2.5). |
| `tests/test_innate_skills_refactoring.py:67` | expects `leader` `innate_skills == ["coordination","chart","todo"]` | **NO EDIT** — currently RED (leader still lists `question`), turns **GREEN** after P4 (RULING-1: the list becomes exactly `["coordination","chart","todo"]`). Re-run to confirm. |
| `tests/unit/test_devops_agent.py:165` | expects `devops` `innate_skills == ["dynamic-skill","todo"]` | **MUST EDIT** → `["dynamic-skill","chart","todo"]` (RULING-1: the list becomes exactly that). Currently RED (devops has `chart`+`question`); after P4 it stays RED unless the expectation is updated (`chart` stays — dropping it is out of scope). |
| `tests/test_chart_image_delivery_e2e.py:1418` (tripwire) | SHA-pins **35** files; **14** of them are edited by this sweep (see §11.3) | **ADJUDICATED SEAL RE-PIN** per edited file (14 constants-only updates): git-log last-touch attribution → **constants-only** update of `SEALED_SHA_BASELINES` → targeted test run → provenance-labelled commit. Do **not** edit the sealed prompt files' semantics beyond the contract. |
| `tests/unit/test_report_integrity_prompts.py` | `E_REQUIRED_SNIPPETS` (`"before ending any turn"`, `"zero tool calls"`) must survive in the canonical homes; `D_REQUIRED_SNIPPETS` (`"[REPORT SANITY:"`, `"interim, not completion"`, `"send_message"`) in `D_HOME_FILES` (leader→`workflow.md`, project-manager→`rule.md`+`workflow.md`, governor→`rule.md`, …) | **NO EDIT expected** — but the clause-family rewrite MUST preserve **both** families verbatim in each affected canonical home: the **E** snippets `"before ending any turn"` + `"zero tool calls"` **and** the **D** snippets `"[REPORT SANITY:"` + `"interim, not completion"` + `"send_message"` (N14 — the D snippets were previously named only in the *why* column; the per-file verify clause now names them explicitly). Verify per file, per agent, per family. |
| `tests/unit/tools/test_prompt_section_reference_integrity.py` | closure sweep: no bare `.md` / bare `agents/` cross-reference tokens; no empty/glued captures | **NO EDIT** — the sweep's cross-references must be `See Open questions for the orchestrator` / `See <agent>'s Open questions for the orchestrator` (section names only). |
| `tests/test_prompt_artifact_baseline.py` | `agents/.prompt-artifacts.sha256` pins the depth-2 prompt artifacts | **REGENERATE** after the sweep: `python3 scripts/protection/generate_prompt_artifact_baseline.py` (writes the baseline; `--check --tree <dir>` verifies). Depth-2 only ⇒ `agents/<agent>/<file>.md` are covered; `agents/_prompt_system/**` and `agents/<agent>/skills-template/**` (depth 3) are **not**. |
| `tests/test_tool_filter.py:34` | frozen category→tool map contains `"question": ["ask_questions"]` | **NO EDIT** — the tool still exists in source. |
| `tests/unit/test_ari_agent.py`, `tests/unit/test_ari_worker_integration.py` | assert ari allow-list **membership** (`"job" in allow`, `"instance" not in allow`), not exact sets | **NO EDIT** — adding `"question"` to ari's allow does not break membership asserts. |
| any test asserting `ask_questions` for leader/devops | — | Re-run `grep -rn "ask_questions" tests/ --include=*.py` at implementation time (read-only grep today: hits are in `tests/e2e/test_*`, `tests/test_question_tools*.py`, `tests/job_queue/test_midflight_qa.py`, `tests/unit/test_*pause*/answer*`, `tests/test_tool_filter.py` — none assert leader/devops toolset membership). |
| `tools/dev/verify_service_default_open.py:87` (the `create_instance_tools` call; `tool_count` written at `:102`) | The script records `len(by_name)` as `tool_count`. After the gate the built toolset is **one tool smaller** for the agents it exercises ⇒ `tool_count` shifts by **−1**. **No assertion depends on it** — it is a reported number, not a pinned constant. | **NO EDIT** — but **record the −1 shift** in the Phase 6 notes so a reviewer does not read it as an unexplained regression. (N1) |
| *(N1)* the whole test corpus | **NO test asserts `ask_questions` membership in a built toolset today** — which is exactly why the new T1–T5/T9 must be **genuinely discriminating** (a synthetic meta that ALLOWS `"question"` in the `tools.allow` category), and why T9 is needed to cover the allow-list half. | **NO EDIT** — record as the rationale for the T1/T9 "the synthetic meta MUST allow `question`" requirement. |
| `tests/unit/tools/test_attestation_prompt_contract.py:48-49` — **semantic pin (BLK-3a)**: `agents/leader/rule.md` **AND** `agents/leader/workflow.md` must **KEEP** `"Wrong Attestation Order"`, `"PURE TOOLCALL TURN"`, `"c5d9a38a shape"`, `"report-first-then-attest"`, `"attest-first, report-second"`, `"subsequent standalone AI message"`, `"non-delegating turns"` and must **NOT** gain `"CONDITIONAL on delegation"`, `"Deliver the full detailed final report"`, `"as its own message FIRST"`, `"call \`attest_completion\` ALONE"` | The SHA seal does **not** cover the attestation **contract strings** — only the file bytes. The leader's two files are both edited by the sweep (leader/rule.md §10.4 + §10.5, leader/workflow.md §10.4 + §10.5) ⇒ the semantic contract must survive even though the SHA changes. | **NO EDIT expected — verify.** Do **not** add the forbidden fragments; do **not** drop the required ones. Re-run the file after the leader sweep. |
| `tests/unit/tools/test_mission_watch_prompts.py` — **semantic pin (BLK-3b)**: `agents/jober/rule.md` + `agents/jober/workflow.md` (and the rest of the 9-file mission-watch set) keep the `watch_mission` vocabulary and **ZERO `watch_job` tokens** | The SHA seal does **not** cover this vocabulary. `agents/jober/rule.md` and `agents/jober/workflow.md` are edited by the sweep (§10.4). | **NO EDIT expected — verify.** The sweep must not reintroduce `watch_job`/`watch_jobs` (word-boundary match) nor drop the decision-rule text. |
| `tests/test_chart_image_delivery_audit.py:58,467-480` — **semantic pin (BLK-3c)**: the literal **"Chat Delivery"** must survive verbatim in **EVERY** chart-capable file the sweep edits (**20** agents, `CHART_CAPABLE_AGENTS`) | The SHA seal covers file bytes but **not** this literal per agent; `agents/governor/rule.md` (the new 14th re-pin) carries **1 × "Chat Delivery"**, and the sweep edits several other chart-capable files. | **NO EDIT expected — verify.** Pin #17 (`test_pin_17_chat_delivery_references`) is parametrized over the 20 chart-capable agents; every edited one must still match. |

### 11.3 SEALED-SHA overlap (verified) — **14** files trip the tripwire

The commission's brief names 5 sealed files; **reading `tests/test_chart_image_delivery_e2e.py:1350-1398` shows 35 sealed files, and 14 of them are edited by this sweep** (13 + `agents/governor/rule.md`, added by BLK-2):

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
| **14** | **`agents/governor/rule.md` (`:1387`)** — sha `84a93586df376ad39dc553af36f86f684977ecfa8d717186f2fe8f16846c8cf5`, carries **1 × "Chat Delivery"** | §10.5 short (`:82`) — the OPENING-DISCIPLINE clause; also the governor's D_HOME **and** E_HOME ⇒ the D **and** E snippets must survive |

**All 35 pins are currently GREEN** (verified by hashing every entry at authoring time; the governor's baseline sha re-verified against the working tree at `0e7500a1b`) ⇒ every one of the **14** needs a re-pin; there are **no** pre-existing reds to absorb.

**Sealed files NOT in the sweep (safe):** `agents/ari/rule.md`, `agents/ari/workflow.md`, `agents/charter/*` (4 + 2 install-skill files), `agents/devops/rule.md`, `agents/maintenancer/rule.md`, `agents/developer[v2]/tools_note.md`, `agents/planner[v2]/tools_note.md`, `agents/wanderer/soul.md`, `agents/_prompt_system/innate-skills/chart/skill.md`, `daemon/constants.py`, `daemon/sources/*` (**6** daemon files — `base.py`, `registry.py`, `dispatcher.py`, `adapters/discord/adapter.py`, `adapters/slack/adapter.py`, `adapters/telegram.py`; **N3**: the plan previously said 5).

| Split of the 35-file sealed baseline | Count |
|---|---|
| Edited by the sweep ⇒ **re-pin** | **14** (13 + `agents/governor/rule.md`) |
| Not edited ⇒ safe | **21** (22 with `daemon/sources/*` = 6, **minus** `agents/governor/rule.md` which moved into the sweep) |
| **Total** | **35** ✓ (14 + 21) |

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

# ── SEMANTIC PINS (BLK-3) — NOT covered by the SHA seal ────────────
#    attestation contract · mission-watch vocabulary · "Chat Delivery"
timeout 300 $FENCE .venv/bin/pytest \
  tests/unit/tools/test_attestation_prompt_contract.py \
  tests/unit/tools/test_mission_watch_prompts.py \
  tests/test_chart_image_delivery_audit.py \
  -q --tb=short -p no:randomly

# ── SEAL TRIPWIRE (Phase 6 — after each re-pin batch) ──────────────
timeout 300 $FENCE .venv/bin/pytest \
  tests/test_chart_image_delivery_e2e.py::TestPhaseDDoesNotModifySealedArtifacts \
  -q --tb=short -p no:randomly

# ── wider sweep (dir-level, xdist) ──────────────────────────────────
timeout 600 $FENCE .venv/bin/pytest tests/unit/tools tests/unit/services -q --tb=short -n 8
```

House idioms to keep: `timeout` wrapper (outer) + `--tb=short` + `-p no:randomly`; `-n 8` only for dir packs; ambient `POSTGRES_*`/`DATABASE_URL` scrub is **load-bearing** (ambient live-prod poison); `--override-ini="timeout=280"` for long tests.

**Proof set (BLK-7) — name it in every report.** The suite contains two different kinds of evidence and they must never be conflated:

| Proof set | Tests | Pre-change state | What it proves |
|---|---|---|---|
| **GATE PROOF** (RED pre-change) | **T1, T3, T4b, T5, T9** | **RED** — fails with `TypeError: create_instance_tools() got an unexpected keyword argument 'parent_id'` (the kwargs do not exist yet) | the gate actually hides the tool from non-roots, and the two-layer capability model holds |
| **PRESERVATION** (GREEN pre-change) | **T2, T4a, T6, T7, T8** | **GREEN** | nothing regressed — roots still get the tool, the legacy `""`-parent idiom still works, the report→continuation flow still works, legacy packs stay answerable |

⇒ A **green preservation run is NEVER reported as gate proof**, and a **green gate-proof set** is a defect (the gate did not bite). Report both sets separately, by name.

---

## 12. Sequencing & branch/worktree discipline (P10)

### 12.1 The gate — sibling merged INTO THIS BRANCH, NOT yet into `latest` (BLK-6)

| Fact | Verified evidence at `0e7500a1b` (this branch) |
|---|---|
| Sibling branch | `fix/question-pause-resume-asymmetry`, commit **`e2899f8bd`** (11 files: `daemon/api.py`, `config.py`, `repositories/instance/repository.py`, `routers/answer_helper.py`, `routers/instances.py`, `services/instance_lifecycle.py`, `services/pool_orchestrator.py`, + new `services/question_pause_set.py`, `services/stale_pause_sweep.py`, + 2 test files) |
| Sibling worktree | `/home/nea/ensemble-src-wt-qprasym` — **MUST NOT be touched** |
| **Already merged INTO THIS BRANCH** | ✅ merge commit **`0e7500a1b`** (this branch's HEAD) contains `e2899f8bd` |
| **NOT yet in `latest`** | ❌ `git merge-base --is-ancestor e2899f8bd latest` → **false** |
| Their scope | `daemon/services/question_manager.py`, `daemon/routers/answer_helper.py`, `daemon/services/instance_messaging.py`, `daemon/services/instance_lifecycle.py` (+ `api.py`, `config.py`, `routers/instances.py`, `services/pool_orchestrator.py`) |
| **Overlap with us** | `daemon/services/instance_lifecycle.py` param threading at `:2137` (spawn) / `:4690` (restore) — keep **additive + minimal** (two keyword arguments, no restructuring) |
| **Disjoint** | `daemon/tools/instance.py:5895` (the gate), `_tool_registry`, `question_manager`, `instance_messaging` — no file-level conflict |
| **Anchor drift caused by the merge** | restore call `:4618` → **`:4690`**; `source_type` row read `:4595-4597` → **`:4667-4668`**; `meta.parent_id` `:4603` → **`:4675`**. Spawn `:2137`, `source_type` writer `:2390`, `append_platform_context` `:1039`, `_PLATFORM_INSTRUCTIONS` `:1006`, and `instance.py:5895` / `:6238` / `:2450` / `:163` are **unchanged**. |

⇒ **Phase 0 is still a wait — but on the OTHER side**: the sibling's work is already *in* this branch, so what remains is **`git merge latest` INTO this branch** once the sibling has landed in `latest` (their own merge to `latest` is still awaited). Re-verify every anchor in §1–§3 against the post-merge tree before implementing — the table above is the verified post-merge reality at `0e7500a1b`.


### 12.2 Branch & worktree — **REUSE** the existing ones (BLK-6)

| Rule | Value |
|---|---|
| Branch | `feature/hide-question-tool-nonroot` — **ALREADY EXISTS** (this commission's own). **REUSE** it; do not create a second branch |
| Worktree | `/home/nea/ensemble-src-wt-hidenonroot` — **ALREADY EXISTS** and **LOCKS** the branch (a branch checked out in a worktree cannot be checked out elsewhere). **REUSE** it; never reuse another commission's worktree |
| Sibling worktree | `/home/nea/ensemble-src-wt-qprasym` — never read-write, never commit into |
| **Merge, NEVER rebase** | after the sibling lands in `latest`, run **`git merge latest` INTO this branch**. A **rebase rewrites the commit sha** and breaks the recorded blob sha256 provenance (and the tip-identity freshness guard at stage time). |
| Ordering | `git merge latest` (absorbing the sibling's own merge into `latest`) **then** implement **then** merge this branch into `latest` at Phase 9. |
| Commits | pathspec-scoped per phase; seal re-pins as a **separate, provenance-labelled** commit (constants-only) |
| Plan artifacts | already committed on this branch (`8fa3319f7`, 2 files) — the committer step stages this file **and** `decisions.md` in **ONE** commit |

### 12.3 KV coordination artifacts — **both rows ALREADY EXIST** (BLK-6) ⇒ verify/merge, do NOT create

| Row | Status | Content | Rule |
|---|---|---|---|
| census | **EXISTS** — `wt.active.feature/hide-question-tool-nonroot.hidenonroot` | the worktree state (`base: latest`, the planning-artifacts-only commit `8fa3319f7`, the fenced `.env`, the Phase-0 gate) | **VERIFY / UPDATE** — do **not** create a second census row; keep the existing key |
| claim | **EXISTS** — `wt.claim.hide-question-tool-nonroot` (leader-owned: `blocked_by` = "ffeb7190 (pause/resume symmetry) merged to latest", `overlap_files` = the 6 file list, `planned_branch`, `commission`, `opened`) | **UPDATE the `blocked_by` value** to the new reality (the sibling is merged into THIS branch at `0e7500a1b`; it is still not in `latest`) | **MERGE**, never clobber — preserve the leader's `overlap_files` / `blocked_by` keys; if a worker writes its own claim row under the **same slug**, **MERGE** the two |
| plan artifacts | `.agents/shared/planning/hide-question-tool-nonroot/` | this file + `decisions.md` | both are committed together in ONE commit by the committer step |

⇒ **Phase 0's KV work is VERIFY/MERGE, not create.**

---

## 13. Staging (P11)

**Stage only — NO promote.** The user's 3-factor nonce ceremony owns promotes.

> **Deploy-time behaviour change (N6 / F1) — must appear in the deploy paragraph of the final report.** The mid-flight QA / answer-event fan-out becomes **ROOT-ONLY**: only roots mint question packs (the sole production writer is `daemon/tools/question_tools.py:502`), so with the gate in place the QA relay lane and the answer-event surface fire **for roots only**. Non-roots surface blockers through the report's "Open questions for the orchestrator" section instead.

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
| 9 | **Emit the final report** — the 7 commission items **verbatim** + the 3 extras (contract in §17), including the **flow proof** (child open-question → parent instruction) and the explicit **in-flight child question-pack statement** | the report is the commission's deliverable; §17 is the contract |

---

## 14. Risk register

| ID | Risk | Impact | Mitigation | Verification action |
|---|---|---|---|---|
| **R1** | Granting `ari` the `question` category changes **LIVE Discord chat behavior** (new capability for the chat-facing agent; a mid-turn pack pause in a chat lane could strand a turn) | high | Root-scoped skill steers *"prefer asking in my reply text"* for chat lanes (§5.1 #2); the existing wedge guard + answer path protect; **flag explicitly in the final report** | Read the rewritten skill end-to-end before merge; confirm the chat-lane preference sentence is present; no live promote performed (stage only) |
| **R2** | The mappings-API lane (P3) could regress the `source_type` **single-writer** invariant | high | Thread the type into the **existing** writer (`instance_lifecycle.py:2390`) — never add a second metadata writer; keep the edit additive (one keyword at one call site) | `grep -n 'source_type' daemon/services/instance_lifecycle.py` ⇒ exactly one assignment to `instance_metadata["source_type"]`; new test asserting a mappings-lane root resolves the tool |
| **R3** | Stale `_tool_metadata` residue leaves `"question"` **advertised on the help surface for EVERY instance of a granted agent — including non-roots** | low (cosmetic) | The help surface is computed from the **AGENT META**, not from the instance: `daemon/tools/help.py:105-115` → `expand_allow_for_innate_skills` → `resolve_tool_filter` ⇒ every instance of an agent that grants `"question"` (leader, devops, ari) sees it listed, **even when the gate stripped the tool from that instance**. `_tool_metadata` is a **module-level global** (`daemon/tools/_tool_registry.py:18`) cleared **only** by `clear_registry()` (`:625-628`) and appended to by `scan_tools_for_full_docs` (`:660-665`) ⇒ **cross-instance residue** (one instance's scan can leave the category listed for another instance's help). The gate is a *registration* gate and must not touch this surface. | Verify the help listing after the change; state the **precise mechanism** (agent-meta computation + module-level global + cross-instance residue) in the final report — **do not** chase it |
| **R4** | Legacy in-flight packs at deploy time | medium | Migration story (§15): packs live in RAM **and** are shadowed into `instance_metadata['question_pack_payload']` ⇒ the answer path resolves them (CAS + shadow clear + boot rehydration). **No pack is orphaned by the gate** | T8 |
| **R5** | The sibling commission merges into `latest` while we implement | medium | Implement **only after** the merge (Phase 0 gate); never touch their worktree; keep `instance_lifecycle.py` edits additive + minimal | Re-read `latest` after the merge; re-verify §1-§3 anchors (line numbers shift) |
| **R6** | Prompt sweep breadth (**59 sweep rows / 54 files edited, 55 with `agents/governor/rule.md`**) risks prompt-integrity test failures and seal/hash pins | medium | §11.2 pin list + §11.3 the **14**-file seal overlap; **require targeted test runs per phase**; re-pin constants-only, provenance-labelled | Run §11.4's prompt-integrity + seal-tripwire commands after each sweep phase, not once at the end |
| **R7** | The clause-family rewrite could **degrade a root's own ask wording** — a root bound to a chat source must keep the ability to ask | medium | The edit rule is **conditional**: "…or report open questions **(unless you are the user-facing root)**" (§10.5); the rewritten shared skill teaches the **root** half; the root's **capability** is untouched (gate still admits it, allow-list still grants it) | Grep the swept files for the conditional form; confirm every clause-family sentence carries it; confirm `agents/_prompt_system/innate-skills/question/skill.md` still teaches the root ask |
| **F1** | The mid-flight QA / answer-event fan-out becomes **ROOT-ONLY** (only roots mint packs — the sole production writer is `daemon/tools/question_tools.py:502`) | medium | Deliberate consequence of the gate; the non-root path becomes the report section (§6); the fan-out itself is unchanged | State it in the **deploy paragraph** of the final report (§13) + confirm no non-root can mint a pack (T1/T3/T5) |
| **F2** | `agents/watcher/meta.json` has `allow: []` ⇒ **default-open universe** ⇒ it HOLDS `ask_questions` today; the gate strips it | low | The gate (not the allow-list) is the hiding control ⇒ the tool is gone for watcher either way; pinning watcher's allow list is optional hygiene | Confirm `ask_questions` is absent from a watcher's built toolset; optionally pin the allow list (§4) |
| **F3** | A **non-ari agent bound to a chat source** would pass the gate but not hold the category ⇒ it cannot ask | low (latent) | RULING-1 limitation, **disclosed**: the mappings API can bind ANY agent to a chat source, but no non-ari agent is known chat-bound today ⇒ the gap is **LATENT, not active** | State it as a non-goal + a final-report line (§0, §17); re-check the chat-bound agent list at implementation time |
| **F4** | **Job roots and API roots lose the tool** — the predicate is *stricter* than "non-root" (a root with no chat source is not a user-facing root) | medium | Commission-endorsed and intended (P1 fail-closed); such roots answer via `job_answer` | Disclose the behaviour delta in the final report; T3/T4b pin it |
| **F5** | The **53 test files** that call `create_instance_tools` now build toolsets **without** the question tool (re-derived: `grep -rl create_instance_tools tests/ --include=*.py` → 53 files; the plan's earlier "~73–109 callers" was a stale estimate) | low | Benign today — **no test asserts `ask_questions` membership in a built toolset** (N1) ⇒ nothing breaks; but a future test that asserts its presence would fail for a confusing reason | Record the census row (53 files) in the final report; `tools/dev/verify_service_default_open.py`'s unpinned `tool_count` shifts by **−1** (no assertion depends on it) |
| **F6** | The **leader loses the question-SKILL text** (`innate_skills` loses `"question"`) while still needing to answer a root's pack | low | A **teaching gap, not a capability gap**: under RULING-1 the leader **KEEPS** `"question"` in `tools.allow` ⇒ it retains the **capability**; only the skill **text** is gone | Confirm the leader's toolset still contains `ask_questions` (T9 case (a) uses the leader meta); the shared skill teaches the root half for whoever holds it |
| **F7** | The **governor** carries its open-questions contract in **two** files (`rule.md` + `workflow.md`) ⇒ two vocabularies for one contract (the "stated once" trap, guide R-e) | medium | RULING-2: the contract lives **ONCE** in `agents/governor/rule.md` (the governor's D_HOME **and** E_HOME are both `rule.md` — `tests/unit/test_report_integrity_prompts.py:92,:106`); `workflow.md:46` becomes a **section-name pointer** | Confirm `workflow.md:46` carries only the pointer; confirm `rule.md` carries the contract + the D/E snippets; re-pin `governor/rule.md` (14th) |
| **F8** | **Legacy rows without the `source_type` stamp** (written before 2026-08-12) are not user-facing roots ⇒ they lose the tool on restore | low | **PRE-EXISTING and OUT OF SCOPE — NO BACKFILL** (a backfill would make the migration delta non-empty and break the P11 EMPTY-delta premise, §13 step 6 / §15) | State it in §1.3 + §15; confirm `daemon/migrations/versions/` is untouched |

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

⇒ **No migration / DB change is required; no pack is orphaned by the gate.** A root **without** the tool can still answer a child's legacy pack via `job_answer` (category `job`, **def at `daemon/tools/job_queue.py:3403`**). **Verify with T8.**

**Root-only fan-out (N6 / F1).** After the gate, only roots mint question packs (the sole production writer is `daemon/tools/question_tools.py:502`) ⇒ the mid-flight QA relay lane and the answer-event surface are **ROOT-ONLY**. The pack machinery itself is unchanged; only *who can create a pack* narrows.

**Legacy unstamped rows — NO BACKFILL (N8 / F8).** Rows created before the `source_type` writer existed (**2026-08-12**) carry no stamp ⇒ they are not user-facing roots ⇒ a restored such instance gets no question tool. **No backfill** is performed: it would add a migration file and make the delta non-empty, breaking the P11 EMPTY-delta premise and the `rollback_safe` adjudication in §13 step 7. The behaviour delta is disclosed (F8), not repaired.

---

## 16. Coupling map + implementation order

### 16.1 Coupling map (file-level)

| Phase | Files | Coupled to | Strength |
|---|---|---|---|
| 0 | *(no file edits)* — branch / worktree / KV verification | Phase 9 | medium |
| 1 | `daemon/tools/instance.py`, `daemon/services/instance_lifecycle.py`, `daemon/routers/mappings.py` | §2 gate ↔ §3 lane (both thread `source_type`) | **strong** — the lane fix is worthless unless the gate reads the threaded value |
| 2 | `tests/unit/tools/test_question_tool_root_registration.py` (NEW), `tests/unit/services/test_child_report_open_questions_flow.py` (NEW) | Phase 1 (asserts the gate); §6/§7 (report + continuation) | strong |
| 3 | `agents/{leader,devops,ari}/meta.json` + `tests/unit/test_devops_agent.py` | Phase 1 (defense-in-depth); the `ari` grant needs the gate to let it through | strong |
| 4 | `agents/_prompt_system/innate-skills/question/skill.md` | Phase 3 (only `ari` receives it afterwards) | medium |
| 5 | the sweep — **59 sweep rows**, **54 files actually edited** (3 `meta.json` + 51 prompt/doc), **55** with `agents/governor/rule.md` (manifest §10.10) | Phase 4 (vocabulary); the guide's canonical-home rule (§10.1); **14** seal re-pins | **strong** (breadth) |
| 6 | **14** seal constants + targeted tests | Phase 5 | strong |
| 7 | *(review artifacts only)* — reviewer council | Phases 1-6 | strong |
| 8 | *(test artifacts only)* — independent tester verification | Phase 7 | strong |
| 9 | `scripts/upgrade/*`, `scripts/bump_version.py` | Phases 1-8 | medium |

### 16.2 Phase order (dependency rationale per phase)

| Phase | Content | Depends on | Rationale |
|---|---|---|---|
| **0** | **Wait for the sibling to land in `latest`, then merge `latest` INTO this branch.** The sibling `e2899f8bd` is **already merged into this branch** (`0e7500a1b`) but **not into `latest`** ⇒ run `git merge latest` (**MERGE, never rebase** — §12.2), then re-read the tree and re-verify every §1-§3 anchor (the sibling merge shifted the restore anchors). **KV work = VERIFY/MERGE, not create** (§12.3). | — | P10 hard gate; R5; BLK-6 |
| **1** | **Daemon gate + mappings lane.** `daemon/tools/instance.py` (predicate helper + conditional + keyword-only signature), `daemon/services/instance_lifecycle.py:2137` & `:4690` (threading), `daemon/routers/mappings.py:114-117` (`source_type=source.source_type`). | Phase 0 | The gate is the authoritative control; both call sites must be threaded **in the same change** or the restore path silently loses the tool for roots |
| **2** | **New tests T1–T5, T9** (registration + capability) in `tests/unit/tools/test_question_tool_root_registration.py`. | Phase 1 | Proves the gate before any prompt work; the **GATE PROOF** set (T1, T3, T4b, T5, T9) turning green is the go/no-go for Phase 3's grants. **Test-first**: capture the red pre-change evidence (expect `TypeError` — §11.1) |
| **3** | **`meta.json` grants** — leader/devops **remove `"question"` from `innate_skills` and KEEP it in `tools.allow`**; ari **adds** both (§4, §10.3). Plus the `tests/unit/test_devops_agent.py:165` expectation fix. | Phase 2 green | Defense-in-depth only after the authoritative gate is proven; the ari grant is what makes "roots still ask" real; the two-layer model (BLK-1) is what keeps a leader-as-chat-root able to ask |
| **4** | **Root-scoped skill rewrite** (§5.1) of `agents/_prompt_system/innate-skills/question/skill.md`. | Phase 3 | The skill must teach root behavior **after** only ari holds it |
| **5** | **Prompt & doc sweep** (§10.4-§10.8, §10.10 manifest) — **59 sweep rows / 54 files edited (55 with `agents/governor/rule.md`)**, intra-agent canonical homes (§10.1), guide rules R-a..R-k (§10.2). | Phase 4 | The sweep uses the final vocabulary; doing it before the skill rewrite risks two vocabularies |
| **6** | **Targeted test runs + seal re-pin adjudication** (§11.2-§11.4): **14** constants-only re-pins (git-log last-touch attribution → constants-only update → targeted test → provenance-labelled commit) + the prompt-artifact baseline regeneration + `tests/unit/test_devops_agent.py` expectation. | Phase 5 | The tripwire only fails **after** the edits; re-pin is a discrete, auditable act |
| **7** | **Reviewer council** — owner: the **reviewer** agent (council, **0-critical bar**). | Phases 1-6 | The council's 7 blocking findings are text-level and were folded into this plan; a second council confirms the fold. **0 critical** is the bar. |
| **8** | **Independent tester functional verification** — owner: the **tester** agent: registration-level (**GATE PROOF** T1/T3/T4b/T5/T9 vs **PRESERVATION** T2/T4a/T6/T7/T8 — reported separately, never conflated), the **flow proof** (child open-question → parent instruction), and the staged smoke on the merged tree. | Phase 7 | Independent verification on the **merged** tree, not on the developer's branch — the council approves the code, the tester proves the behaviour. |
| **9** | **Merge + version bump + stage (no promote)** (§13) + emit the final report (§17). | Phases 1-8 | Staging consumes the merged tree; the EMPTY migration delta ⇒ `rollback_safe=true` (explicit reasoning) |

---

## 17. Definition of done / exit criteria

- [ ] The gate exists at `daemon/tools/instance.py:5895-5900` as a **build-time conditional**; `ask_questions` is **absent** (not refused) from every non-root toolset.
- [ ] `parent_id` + `source_type` are threaded **keyword-only** into `create_instance_tools` from **both** call sites (`instance_lifecycle.py:2137` spawn, `:4690` restore — post-sibling-merge anchors).
- [ ] The predicate is evaluated from **threaded values** — no DB row read at registration (verified by code inspection + T1–T5, T9).
- [ ] `PRIVILEGED_TOOL_CATEGORIES` is **unchanged** (8 entries) and `"question"` **remains** in `CATEGORY_MODULES` (`:729`).
- [ ] The mappings lane passes `source_type` into the **single** writer; exactly one `instance_metadata["source_type"] =` assignment exists in the codebase.
- [ ] `meta.json` (two-layer model, RULING-1): leader/devops **lose `"question"` from `innate_skills`** (⇒ `["coordination","chart","todo"]` / `["dynamic-skill","chart","todo"]`) and **KEEP it in `tools.allow`**; ari **gains it in both**; project-manager / critic / watcher untouched (watcher optional hygiene).
- [ ] The Question Skill teaches **root** behavior + the chat-lane preference + the non-root half, in first person, with **no forbidden-layer tokens** (guide R-b).
- [ ] Every non-root agent's contract is stated **once per agent** in its canonical home; same-agent cross-references use section names only (guide R-f); no false "stated once" claim (guide R-e).
- [ ] `tests/unit/test_devops_agent.py:165` updated; `tests/test_innate_skills_refactoring.py:67` verified GREEN; `tests/unit/tools/test_frozen_tool_name_discovery.py:223` verified GREEN (source unchanged).
- [ ] All **14** edited sealed files re-pinned with **constants-only** updates, git-log attribution, targeted test run, and a provenance-labelled commit; the tripwire `tests/test_chart_image_delivery_e2e.py::TestPhaseDDoesNotModifySealedArtifacts` is GREEN. (`agents/governor/rule.md` is the 14th — sha `84a93586…`; its **1 × "Chat Delivery"** and the junk-report clause on the same line survive.)
- [ ] **Semantic pins (BLK-3) — "NO EDIT expected — verify" — all GREEN:** `tests/unit/tools/test_attestation_prompt_contract.py` (attestation contract strings kept / forbidden strings absent in `agents/leader/rule.md` + `agents/leader/workflow.md`) · `tests/unit/tools/test_mission_watch_prompts.py` (`watch_mission` vocabulary kept, **zero** `watch_job` tokens) · `tests/test_chart_image_delivery_audit.py` ("Chat Delivery" survives verbatim in all **20** chart-capable agents).
- [ ] `agents/.prompt-artifacts.sha256` regenerated; `tests/test_prompt_artifact_baseline.py` GREEN.
- [ ] `tests/unit/test_report_integrity_prompts.py` and `tests/unit/tools/test_prompt_section_reference_integrity.py` GREEN.
- [ ] **T1–T9** all pass under the fenced invocation of §11.4.
- [ ] **Proof sets reported separately, by name (BLK-7):** the **GATE PROOF** set (**T1, T3, T4b, T5, T9**) is GREEN and was RED before the change; the **PRESERVATION** set (**T2, T4a, T6, T7, T8**) is GREEN and was GREEN before the change. **A green preservation run is never reported as gate proof.** The red pre-change evidence (the `TypeError` on the not-yet-existing kwargs) is captured in the Phase 2 report.
- [ ] **Phase 7 — reviewer council APPROVED with 0 critical** (owner: the **reviewer** agent).
- [ ] **Phase 8 — independent tester verification PASSED on the merged tree** (owner: the **tester** agent): registration-level + flow proof + staged smoke.
- [ ] Nothing under `daemon/migrations/versions/` changed (EMPTY delta); `rollback_safe=true` recorded with the explicit semantic reasoning.
- [ ] Staged (no promote): `releases/<ver>/` exists with provenance sidecars; the staging worktree is **kept**; `current` symlink and live daemon epoch unchanged.
### 17.1 Final-report contract (BLK-4 — 7 commission items **verbatim** + 3 extras = 10)

The final report **must** contain the following **10** items. Items **1–7** are the commission's required items, **verbatim**; items **8–10** are the retained extras.

| # | Item | Source / requirement |
|---|---|---|
| **1** | **The pinned predicate** — the exact P1 expression and why `source_type` (never `original_source`) | §1 (P1, PINNED) |
| **2** | **The registration gate** — the build-time conditional, the two call sites, and the fact that the tool is **never registered** for a non-root (absence, not refusal) | §2 (P2, PINNED) |
| **3** | **List of agent files touched** — the sweep manifest: **55 files actually edited** (3 `meta.json` + 52 prompt/doc) of **60 sweep rows**, machine-derived (§10.10) | §10.10 |
| **4** | **Flow proof** — a child surfaces an open question in its report ⇒ the parent receives it and answers via `send_message` ⇒ the child resumes and continues | §7, T6, T7, T9 |
| **5** | **Tests** — the **GATE PROOF** set (T1, T3, T4b, T5, T9) and the **PRESERVATION** set (T2, T4a, T6, T7, T8) reported **separately, by name**; the red pre-change evidence | §11.1, §11.4 |
| **6** | **Staging plan** — the staged path, version, binary sha256, the EMPTY migration delta, the `rollback_safe=true` adjudication, and **no promote** | §13 |
| **7** | **Explicit in-flight child question-pack statement** — what happens to any pack already in flight for a child at deploy time (RAM + durable shadow + rehydration + CAS ⇒ no pack is orphaned) | §15, T8 |
| **8** | The **14** adjudicated seal re-pins (with their provenance) and the **35**-file sealed baseline split (14 re-pinned / 21 safe) | §11.3 |
| **9** | The **ari-grant live-behavior flag** (R1) and the **precise `_tool_metadata` / help-surface mechanism** (R3 / N5) | §5.1, §14 |
| **10** | The **deploy paragraph**: the mid-flight QA / answer-event fan-out is now **ROOT-ONLY** (N6 / F1) — and that **no promote** was performed | §13, §15 |

> Items **3**, **5** and **10** also carry the disclosures the council required: the RULING-1 scoping limitation (F3), the job-root/API-root behaviour delta (F4), the 53-file test-census shift (F5), and the leader's teaching-gap note (F6).

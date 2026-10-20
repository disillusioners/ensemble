# hide-question-tool-nonroot — Implementation Plan (Plan Overview)

> **Artifact map.** This file = the implementation plan. The rationale record (P1..P11 decisions, rejected alternatives, evidence) lives in the sibling file **`.agents/shared/planning/hide-question-tool-nonroot/decisions.md`** — link to it by heading, do not duplicate it here. P-numbers below refer to that file's decision IDs exactly as issued by the commission.
>
> Every `file:line` in this plan was READ at authoring time against `latest` @ `1ebdf469f` (v0.25.7, branch `latest`, repo `/home/nea/ensemble-src`), and the **post-sibling-merge anchors were re-verified at `0e7500a1b`** (this branch, after the `e2899f8bd` merge) — the drift table is in §12.1. The **branch-truth anchors were re-verified at `a9c041ab0`** (tidy pass `3ab411557` + contract-completion round `a9c041ab0` — §0.1). Anchors that could not be verified are marked **`UNVERIFIED — confirm at implementation time`**.

---

## 0. Header

| Item | Value |
|---|---|
| **Goal** | The `ask_questions` tool is **hidden** (never registered in an instance's toolset — NOT a runtime refusal) from every non-root instance. Only user-facing root instances keep it. Non-root children surface blockers in their completion report under the canonical section **"Open questions for the orchestrator"**; the parent answers per its own authority and instructs the child through the existing continuation path, or forwards upward, or (if the parent is the root) asks the user through its own tool. |
| **Capability model (two layers)** | **Effective capability = {user-facing root} ∩ {agent grants the category}.** The **GATE** (§2) is the authoritative **hiding** control — *necessary*: a non-root NEVER gets the tool registered, even if its agent still lists `"question"`. The **ALLOW-LIST** (`meta.json`) is the **capability** grant — *sufficient*: a user-facing root only actually receives the tool if its agent grants the category. Neither layer alone is the whole truth. |
| **Non-goals** | (a) No change to the mid-flight channel. (b) No change to the question-pack machinery (create/CAS/rehydrate/shadow). (c) No DB/migration change. (d) No promote. (e) No new runtime refusal, no "deny" semantics — absence at registration only. (f) No rewrite of historical docs / CHANGELOG / planning records. (g) **Scoping limitation (RULING-1, disclosed — risk F3):** a user-facing root whose agent does **NOT** grant the `"question"` category cannot ask. The mappings API can bind ANY agent to a chat source; today no non-ari agent is known chat-bound ⇒ the gap is **LATENT, not active**. |
| **Commission status** | **Design PINNED** (P1..P11 authoritative; the plan review returned **APPROVE-WITH-NOTES** and its 7 text-level blockers are folded in — §0.1). **IMPLEMENTATION LANDED through fix round 4** @ branch tip **`30ea77149`** (`21bccf157` → `2f28cb969` → `12291f2a4` fix round 1 → `b7e3a0808` tester next-turn-execution + `job_answer` wrapper (test-only) → `4088be633` fix round 2: **B3 CLOSED** by the semantic-class sweep, **W3 ordering pin** added to the gate pack, **W1 renamed** honestly, **3 seal re-pins**, baseline **161/161** → `7e7062ce3` (the **ratified R-b CONVERT** — gaia/** + _mother/** flat→root-conditional; 5 files — 4 already-counted prose + baseline regen ⇒ partition unchanged at that point) → `b3b9d351f` (**plan amendment #4**) → **`3ab411557` tidy pass** (M1 gate-diagnostics log · L13 fail-closed `isinstance` guard · M7 seal provenance corrections · M2/M3/M4 comment debt · L4/L5/L6/L10 tidy) → **`a9c041ab0` contract-completion round** (**B1**: the **11** new canonical homes ⇒ **16 → 27**, full-section verbatim-core parity with `worker/rule.md`; **B3**: the governor `tools_note.md` category drop + the `job-orchestration` roots-only rider + the devops addendum) → **`235ec6934` iteration-003 closure, round 1** (test-only: **T4b** re-shaped to the **SPECIFIED** legacy case `parent_id=""` **AND** `source_type=""` ⇒ no tool; **NEW** `test_t6_root_with_non_chat_source_type_has_no_question_tool` — a root with a **REAL** non-chat `source_type`; **mutant re-proof**: **M-i** and **M-ii** now **CAUGHT**, exact red sets, zero collateral, restore-green) → **`9f8b49306`** (the **13-row UNAUDITED-DIRS ledger** — **2** canonical homes added ⇒ **27 → 29**; **11** ADJUDICATED-NO-HOME with evidence) → **`23859484e`** (the **13-row PARENT-SIDE ledger** — **8** paragraph carriers added; **5** RULINGS) → **`30ea77149`** (the **maintenancer** parent-side paragraph **REMOVED** — a runtime probe with the real `_apply_tool_filter` proved it holds **NO** outbound instance channel; the file is byte-identical to base `a9aa911a3`) — §0.1, §10.4, §10.4a, §10.4b, §10.4c, §10.7, §10.10, §10.11, §11, §14, §17.1). **FINAL BRANCH TRUTH: partition 97 / commission surface 95 / canonical homes 29 / parent-side carriers 19 / seal 36 ops over 18 distinct files / gate pack 14 tests / pre-existing 11, 0 new.** STILL OWED: the **base-sync re-entry** (§12.4 — the sibling lane's work **did reach `latest`** (re-verified 2026-10-20), but **`latest` is NOT yet in this branch**; as of 2026-10-20 `origin/latest` = local `latest` = `e6eb13fcc` (v0.26.1 tip) ⇒ merge `origin/latest` INTO this branch, **never rebase**) and **staging** (§13 — the 8-step ENDGAME RULING; the version bump targets the **next free version**). |
| **Scope tier** | **Full-stack behavioral change** — daemon tool-build gate + mappings-lane fix + 3 `meta.json` grants + shared innate-skill rewrite + the prompt/doc sweep — the **full branch partition is 97 files** (§10.10, machine-derived from `git diff --name-only 0e7500a1b..30ea77149`): **78 prompt prose + 3 `meta.json` + 1 regenerated baseline + 3 `daemon/` + 3 `docs/` + 7 tests + 2 planning docs**. The **commission surface is 95** (97 − the plan's own 2 planning docs) + **36** adjudicated seal re-pin constant **operations** over **18** distinct sealed files (of a **35**-file sealed baseline) + staged (not promoted) release. |
| **Why this tier** | The user-visible outcome ("roots still ask") needs BOTH halves: the daemon gate (authoritative hiding) **and** the `ari` grant (otherwise "roots still ask" is vacuous — ari holds neither `question` in `tools.allow` nor in `innate_skills` today). The prompt sweep breadth is forced by the "or ask" clause family (**41** sites) and by the open-questions contract needing **29** canonical homes; **18** of the sealed-SHA-pinned files are edited and force adjudicated re-pins (**36** constant-update operations over **18** distinct files after the tidy pass + contract-completion round + the iteration-003 closure rounds — §11.3). |
| **Branch (existing)** | `feature/hide-question-tool-nonroot` — **already created**; the worktree `/home/nea/ensemble-src-wt-hidenonroot` **locks** the branch. HEAD `30ea77149` (iteration-003 closure: `235ec6934` → `9f8b49306` → `23859484e` → `30ea77149`; the contract-completion round `a9c041ab0`, the tidy pass `3ab411557`, plan amendment #4 `b3b9d351f` and the sibling merge `0e7500a1b` are ancestors). **REUSE** it; the sibling lane's work **DID reach `latest`** (re-verified 2026-10-20 — its branch tip `e73c5c73f` is an ancestor of `latest`), but **`latest` is NOT yet in this branch** ⇒ run `git merge origin/latest` **INTO** this branch — **MERGE, never rebase** (BLK-6, §12.2, §12.4). |
| **Plan artifacts** | `.agents/shared/planning/hide-question-tool-nonroot/plan-overview.md` (this file) + `decisions.md` (sibling worker — NOT written here). |

### 0.1 Plan-review amendments folded in (review 2026-10-20 — APPROVE-WITH-NOTES)

The plan review confirmed the **design** sound and reopened **no** pinned decision (P1..P11). Its 7 blocking findings were **text-level** and are folded in here; every numeric constant below is canonical and shared with `decisions.md`.

| Finding | Disposition | Landed in |
|---|---|---|
| **BLK-1** | Two-layer model: gate = hiding (necessary), allow-list = capability (sufficient). leader/devops **KEEP** `"question"` in `tools.allow`, **REMOVE** it from `innate_skills`; ari **ADDS** both. Scoping limitation disclosed. Discriminating **T9** added. | §0, §4, §10.3, §10.5, §11.1, §14 (R7/F3/F6), §17 |
| **BLK-2** | `agents/governor/rule.md:82` joins the clause-family sweep (14th re-pin); `agents/governor/workflow.md:46` becomes a **section-name pointer** (RULING-2 — the governor's contract lives once, in `rule.md`). | §10.5, §10.6, §11.3 |
| **BLK-3** | The three **semantic** pins the SHA seal does NOT cover are named (attestation contract · mission-watch vocabulary · "Chat Delivery") — **NO EDIT expected — verify**. | §11.2, §11.4, §17 |
| **BLK-4** | §17 final-report contract **replaced** by the commission's 7 items **verbatim** + the 3 existing extras (10 total). | §13 (step 10), §17 |
| **BLK-5** | **Phase 7 = reviewer council** (0-critical bar, owner: **reviewer**); **Phase 8 = independent tester** (owner: **tester**); staging shifts to **Phase 9**. | §16, §17 |
| **BLK-6** | This commission's **own** branch + worktree already exist ⇒ **reuse**; `git merge latest` INTO the branch, **never rebase**; both KV rows **exist** ⇒ Phase 0 KV work is verify/merge, not create. | §12.1, §12.2, §12.3 |
| **BLK-7** | §11.1 gains a **"Red pre-change?"** column; T4 splits into **T4a** (preservation) / **T4b** (gate proof); proof set named so a green preservation run is never reported as gate proof. | §11.1, §11.4, §17 |
| **Fix round 1** (approver iteration 002 — AMENDMENT #3, landed `21bccf157`→`2f28cb969`→`12291f2a4`) | The iteration-001 genuine gaps closed on branch: **B3** devops non-root contract + 14th canonical home (§10.4) · **B4** sweep completion gaia/designer/coder — 6 EDIT + 2 NO-EDIT + 2 disclosed scope additions (§10.4) · **B5** charter `:71` self-contradiction (§10.7) · **W3** escalate-family ×4 conditionalized (§10.6) · seal re-pins → **18** constants / **17** files (§11.3) · **T6/T7/T8 landed** + **T2/T4a reclassified** via the signature-adaptive harness (§11.1) · mappings-lane registry row — the grep substitute is obsolete (§11.1/§11.2) · sibling facts + **second base-sync re-entry** (§12.1, §12.4) · **§13 push step** · Phase-8/9 split · counts regenerated 66/41/14/18. **Those are the fix-round-1 numbers — SUPERSEDED by the fix-round-3 row below (95 / 93 / 27 / 41 / 31-over-17 / 13).** | §10.4-§10.7, §10.10, §11, §12.1, §12.4, §13, §14, §16, §17 |
| **Fix round 2** (AMENDMENT #4 — LANDED `b7e3a0808` → `4088be633`, 29 files, +393/−161) | **B3 CLOSED** — the semantic-class sweep routed every human-decision instruction to the report channel: **26 `agents/**` prose files**, **~115 CONVERT sites** over a **~230-row adjudication ledger** (§10.4a, §10.11) · **4 files** gained / reused the canonical `## Open questions for the orchestrator` host (jober/rule.md, maintenancer/rule.md, project-manager/rule.md) ⇒ **canonical homes 14 → 16** · **11 new edit-set members** (63 → 74 prose) · **W3 ordering pin** added to the gate pack (**12 → 13** tests) · **W1 renamed** honestly (T4a → `test_root_with_threaded_chat_source_type_has_question_tool`) · **3 seal re-pins** ⇒ **21 operations / 17 distinct** · baseline regenerated **161/161** · batteries: **zero new failures** (7 pre-existing) · gate-pack **mutation proof** (M1 → 5 reds, M2 → 2 reds, M3/M3b, undetected NONE). | §0.1, §10.4a, §10.10, §10.11, §11.1, §11.3, §11.4, §11.5, §14, §16, §17 |
| **Fix round 3** (AMENDMENT #5 — LANDED `3ab411557` → `a9c041ab0`, 7 + 16 files) — **SUPERSEDED by the fix-round-4 row below** (its counts are the fix-round-3 counts, not the current ones) | **Tidy pass `3ab411557`** (7 files): **M1** — one `logger.debug` on the fail-closed hide shape (parent leg passes, `source_type` is not a chat type) so **the exact failure mode this commission repaired twice is now VISIBLE on recurrence** · **L13** — fail-closed `isinstance` guard so a non-`str` `source_type` can no longer raise `TypeError` at the frozenset membership check · **M7** — seal **provenance corrections** (3 attribution shas git-log-verified; `6fa226976` attributed; round-2 rows labelled content-sha256; the 35/35 constant VALUES byte-identical) · **M2/M3/M4/L4/L5/L6/L10** comment debt · **contract-completion round `a9c041ab0`** (16 files): **B1** — **11** new canonical homes (16 → **27**), full-section verbatim-core parity with `worker/rule.md` (worker-verified **27/27** tree-wide), **PARENT-SIDE paragraph added to all SIX families** (the brief's "expected NO" for architect/planner/tidier was **WRONG** — LEADER-RATIFIED deviation, §10.4b), **pointer reductions = 0 — VERIFIED NO-OP** (LEADER-RATIFIED, §10.4b) · **B3** — the governor `tools_note.md` `question`-category drop (the governor's `tools.allow` **never had** `question` — the earlier claim was stale), the `job-orchestration` roots-only rider, the devops `rule.md:133` addendum · **B2** — the **T2/T4a classification corrected to GREEN pre-change** (§11.1, §11.5, §17, §17.1) · **FINDING-2 disclosed** — the pre-existing `job_answer` order red (§11.5) · census **53 → 56** (§14 F5) · **W4** two-layer capability-tension disclosure row (§14). **FINAL COUNTS: 95 / 93 / 27 / 41 / 31-over-17 / 13 / inventory-11 / 56.** | §0.1, §10.4, §10.4b, §10.7, §10.10, §11.1, §11.3, §11.5, §12.4, §14, §16, §17, §17.1 |
| **Fix round 4** (AMENDMENT #6 — approver **iteration 003** closure; LANDED `235ec6934` → `9f8b49306` → `23859484e` → `30ea77149`, **4 commits, 11 files, +158/−19**) — **SUPERSEDES the fix-round-3 counts above** | **Round 1 `235ec6934`** (test-only): **T4b** re-shaped to the **SPECIFIED** legacy case (`parent_id=""` **AND** `source_type=""` ⇒ no tool) + **NEW** `test_t6_root_with_non_chat_source_type_has_no_question_tool` (a root with a **REAL** non-chat `source_type` — webhook/scheduler, cited from `daemon/models/source.py:27,30` + `daemon/routers/sources.py:155`; negative control `repository.py:919`) ⇒ **gate pack 13 → 14**; **mutant re-proof**: **M-i** ("" admitted as chat) ⇒ red set EXACTLY `test_t4b_legacy_empty_parent_and_empty_source_type_has_no_question_tool`; **M-ii** (any non-chat admitted) ⇒ red set EXACTLY `test_t6_root_with_non_chat_source_type_has_no_question_tool`; **zero collateral; restore-green proven** ⇒ both surviving approver mutants are now **CAUGHT**. **Round 2 `9f8b49306`**: the **13-row UNAUDITED-DIRS ledger** — **HOME-ADDED ×2** (`blueprinter/rule.md:34` — reports upward + is a dispatcher; `charter/rule.md:74` — its NEEDS MORE INFO is a **pre-work abort**, not a report-time question) ⇒ **canonical homes 27 → 29**; **ADJUDICATED-NO-HOME ×11** with evidence. **Round 3 `23859484e`**: the **13-row PARENT-SIDE ledger** — **ADDED ×8**; **RULING ×5** (devops/giter: no instance category ⇒ no fan-in; jober: the job-lane equivalent is `job_continue` — the canonical `send_message` wording was **factually wrong** — LEADER-RATIFIED; _baby_template/_mother: leaf templates, blast radius avoided). **Round 4 `30ea77149`**: the **maintenancer** parent-side paragraph **REMOVED** (branch "remove", not "reword") — a runtime probe with the real `_apply_tool_filter` proved maintenancer holds **NO** outbound instance channel (effective allow lacks `instance`; `send_message` NO, `spawn_instance` NO; its only DB writer refuses `messages`/`message_queue`) ⇒ the file is **byte-identical to base `a9aa911a3`** and its seal constant is back to its pre-B3 value `abe43bae…` (**the self-consistency proof**). **BONUS SCAN**: of all 20 parent-side carriers, maintenancer was the **ONLY** one whose `meta.json` lacked the instance category — no other agent asserts a missing channel. **FINAL COUNTS: 97 / 95 / 29 / 19 / 36-over-18 / 14 / pre-existing-11, 0 new.** | §0.1, §10.4, §10.4b, §10.4c, §10.10, §11.1, §11.3, §11.5, §12.4, §14, §16, §17, §17.1 |
| **Approver iteration-003 disposition** (B1–B5) | The iteration-003 findings are **closed** as **leader-dispositioned** — the branch's edit rounds are **CLOSED at `30ea77149`** (4 commits after `a9aa911a3`); transcribed faithfully, not re-litigated. **B1** → **FIXED** (both mutants now caught; exact-red-set proof) · **B2** → the §13 / §17.1 item counts are **aligned to the FINAL count**, stated in **BOTH** places · **B3** → the parity claim is **restated** in the **29/29 prefix-extension** form (the strict "byte-identical across all" form is **NOT** reintroduced — that WAS the blocker) · **B4** → the 13 unaudited dirs are recorded as **2 homes + 11 adjudications** · **B5** → the parent-side coverage is recorded as **8 added + 5 rulings** + the maintenancer removal. **Full disposition table: §17.1.** | §17.1, §10.4c |
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

**Legacy rows without the `source_type` stamp — PRE-EXISTING, NO BACKFILL (N8).** The single writer (`instance_lifecycle.py:2390`) has existed since **2026-08-12**; rows created before it carry no `source_type` in `instance_metadata`. Such a row is **not** a user-facing root for predicate purposes ⇒ on restore it receives **no** question tool. ⇒ The §2.3 note *"a root restored from DB keeps the tool"* holds **ONLY** for a root whose row carries the stamp. **No backfill** is planned: a backfill would make the migration delta non-empty and break the P11 EMPTY-delta premise (§13 step 7, §15). Risk **F8** — out of scope by decision.

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

**LANDED — the tidy pass (`3ab411557`) added two guards to the helper; the planned last line was superseded:**

| ID | Landed change | Why |
|---|---|---|
| **M1** — gate diagnostics log | ONE `logger.debug("ask_questions hidden: root-candidate with source_type=%r", source_type)` on the **suspicious shape** (the parent leg passes, `source_type` is not a chat type) | that shape is byte-identical to a forgotten `source_type=` thread — **the exact failure mode this commission repaired twice** ⇒ the failure is now **VISIBLE on recurrence** instead of silent. Child spawns short-circuit on the parent leg above ⇒ the noise stays bounded to root candidates. |
| **L13** — fail-closed `isinstance` guard | `return isinstance(source_type, str) and source_type in CHAT_SOURCE_TYPES_FOR_ROOT` (replaces the planned `return (source_type or "") in …`) | a non-`str` `source_type` can no longer raise `TypeError` at the frozenset membership check — it now **fails closed** (not a root). |

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
| `agents/devops/meta.json` | `:8` | `"innate_skills": ["dynamic-skill", "chart", "question", "todo"]` | **REMOVE `"question"`** ⇒ `["dynamic-skill","chart","todo"]` | Same split as the leader: skill **text** gone, **capability** retained — **devops's prompt treatment now matches the leader's honestly** (fix round 1 / B3: `devops/rule.md` + `devops/soul.md` carry the non-root contract + the 14th canonical home, §10.4). `tests/unit/test_devops_agent.py:165` was edited to this exact list (LANDED). |
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

Canonical-home column = **the file that owns the contract for that agent**; every other file of the same agent carries `See Open questions for the orchestrator` (or `See <agent>'s Open questions for the orchestrator` cross-agent) and **no filename token**. **Canonical homes = 29** after fix round 4 (27 after fix round 3) (was **16** after fix round 2, **14** after fix round 1 — devops/rule.md joined as the 14th; was 13). Fix round 2 added **maintenancer/rule.md** + **project-manager/rule.md** as hosts; **jober/rule.md** was a pre-existing host. Fix round 3 (`a9c041ab0`) added the **11** unserved dirs as hosts — see **§10.4b**. **Re-derived by tree grep** (read-only, re-run at `a9c041ab0`): `grep -rl --include=*.md -E '^## Open questions for the orchestrator' agents/` ⇒ **27** hosts — `_baby_template/rule.md` · `_mother/rule.md` · `approver/rule.md` · `approver[v2]/rule.md` · `architect/rule.md` · `coder/soul.md` · `designer/soul.md` · `developer/rule.md` · `developer[v2]/rule.md` · `devops/rule.md` · `doc-writer/rule.md` · `gaia/workflow.md` · `giter/workflow.md` · `governor/rule.md` · `jober/rule.md` · `leader/workflow.md` · `maintenancer/rule.md` · `planner/rule.md` · `planner[v2]/rule.md` · `project-manager/rule.md` · `reviewer/rule.md` · `reviewer[v2]/rule.md` · `tester/rule.md` · `tidier/rule.md` · `tidier[v2]/rule.md` · `wanderer/rule.md` · `worker/rule.md`.

| File | Anchors (verified content) | Change | Canonical home for this agent | Seal pin |
|---|---|---|---|---|
| `agents/_prompt_system/innate-skills/question/skill.md` | whole file (**53** lines) | FULL REWRITE per §5.1 | cross-agent shared home / **root-teaching** home | not sealed (chart/skill.md is) |
| `agents/_prompt_system/innate-skills/job-orchestration/skill.md` | `:164-166` — the **Scope note** above the job-status table; the `QUESTION_REQUESTED` row itself at `:170` | **EDITED (fix round 3, B3 — the rider)** — a one-line **roots-only** emission note: QUESTION_REQUESTED relay events are emitted **only by user-facing roots**, because the daemon gate hides the asking tool from non-root instances, so a non-root watcher **never** sees QUESTION_REQUESTED on a job it watches | cross-agent shared skill — a **scope note**, not a contract home | not sealed (depth 3) |
| `agents/leader/soul.md` | `:19` "I pause and ask for user input"; `:56` "I ask when it matters via `ask_questions()`"; `:58-64` the 5-item "I ask for user input when:" list | agent-POV rewrite: I am not the user-facing root; my blockers ride my report | identity file owns *identity*; pointer to the workflow section | not sealed |
| `agents/leader/rule.md` | `:208` "**Ask User**"; `:209` "**Ask User**"; `:214` "**Ask User**"; `:215` "**Ask User**"; `:216` "Leader (collaborate with user)" | authority table: "Ask User" → "**Report open questions**" / "Leader (decide — escalate only if user-critical)"; add pointer | rule.md = rule-shaped home → pointer to workflow section | **SEALED** (`tests/test_chart_image_delivery_e2e.py:1388`) |
| `agents/leader/workflow.md` | `:213` SemiAuto override ("Only ask user when complexity is HIGH…"); `:464` "ask the user OR delegate a repro to Tester"; `:548` "Surface the competing hypotheses to the user and ask for direction"; `:703` clause-family quote | rewrite: open-questions reporting replaces user-asking; **add the new section "Open questions for the orchestrator"** (0-3, placement, omission rule) | **canonical home (leader)** | not sealed |
| `agents/leader/tools_note.md` | whole file (7 149 bytes) — documents neither question nor midflight | OPTIONAL: one operational line — I have no user-ask channel; my blockers ride the report | tools_note owns the tool-by-tool reference | not sealed |
| `agents/governor/tools_note.md` | `:153` — the category heading read *"### `instance` category — `self`, `instance`, `help`, `question`"*; `:155` — the following paragraph named the `self` / `help` / **`question`** categories | **EDITED (fix round 3, B3)** — `question` **dropped** from the category list and the paragraph re-pointed at the canonical section (*"Open questions for the requester ride the report channel (See Open questions for the orchestrator)"*) | the governor's contract lives **ONCE**, in `agents/governor/rule.md` (RULING-2) — the tools_note only points at it | not sealed. **CORRECTION: the governor's `tools.allow` NEVER had `question`** (`['council','instance','self','project','help','shared_meta_kv','knowledge','time']`) — the earlier claim that it did was **stale**; only **ari / leader / devops** grant the category (§14 W4) |
| `agents/worker/rule.md` | `:187` "**Ask** — if something blocks me, the question IS the turn ending."; `:189` scope carve-out mentioning "question-to-dispatcher turns" | "Ask" → "**Report open questions**" (section, 0-3) — never a tool-less ask | **canonical home (worker)** — E_HOME `rule.md` | not sealed. **MUST KEEP** `"before ending any turn"` + `"zero tool calls"` (`tests/unit/test_report_integrity_prompts.py:115-118`) |
| `agents/jober/rule.md` | `:94` "Ask the user which project they want to interact with"; `:221` "❌ Asking the user, getting an answer, then ignoring it" | redirect to the orchestrator / report | **canonical home (jober)** | not sealed |
| `agents/jober/workflow.md` | `:23` "Ask user to confirm project before proceeding" | pointer → jober's rule.md section | pointer | not sealed |
| `agents/giter/workflow.md` | `:108` "Step 4: Ask user - which files to include?"; `:157` "Step 5: Ask user for branch purpose" | redirect → report open questions | **canonical home (giter)** (no rule.md in sweep) | not sealed |
| `agents/tester/rule.md` | `:75` "If missing, ask the user to create it" | redirect → report | **canonical home (tester)** | not sealed |
| `agents/tester/workflow.md` | `:966` "**If missing** — Ask user for project-specific requirements" | pointer → tester's rule.md section | pointer | not sealed |
| `agents/doc-writer/rule.md` | `:8` "**Clarify ambiguities** with a focused clarification (default to ONE question…" | clarify with the dispatcher / report | **canonical home (doc-writer)** | not sealed |
| `agents/doc-writer/soul.md` | `:19` "ask a focused clarification (default to ONE question…" | pointer → doc-writer's rule.md section | pointer | not sealed |
| `agents/doc-writer/workflow.md` | `:10` "ONE question; ask more only if multiple critical ambiguities exist" | pointer | pointer | not sealed |
| `agents/gaia/workflow.md` | `:60` "4. Ask user to try again" · **fix round 1 (`8589d9be5`)**: `:14`, `:39` EDIT · `:71` **NO-EDIT adjudicated** (it IS the contract text itself) · `:89` **NO-EDIT adjudicated** (inbound-user voice) · `:80` + flowchart `:98` **disclosed scope additions** (preventing a fresh self-contradiction after the `:71`/`:89` adjudications) | redirect → report | **canonical home (gaia)** | not sealed |
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
| `agents/devops/rule.md` | **B3 (fix round 1, `f332e83e0` + residual `c5bd90146`)** — the ask-family was adjudicated **BY CONSTRUCT after the briefed line numbers proved STALE (anchor drift recorded)**: **`:9`, `:32`, `:91`, `:92`, `:110`, `:114`, `:156`, `:164`, `:203`** rewritten to the non-root form + the canonical section **"Open questions for the orchestrator"** added VERBATIM from the tester/rule.md home · **fix round 3 (B3)**: `:133` *"If Medium or above: show plan, get confirmation"* → *"show the plan, then stop and report the blocked action as an open question (See Open questions for the orchestrator); proceed only when the approval arrives as a new dispatch"* — the **flat** never-ask form (devops is structurally-never-root per R-b) | ask-family → non-root contract + canonical section | **canonical home (devops) — the 14th canonical home** (at fix round 1; **16** after fix round 2, **27** after fix round 3 — §10.4a, §10.4b) | **SEALED** — re-pinned ×2: `617bf69a0`-attribution, superseded by the residual → current `febf0e89…` (`tests/test_chart_image_delivery_e2e.py:1390`) |
| `agents/devops/soul.md` | **B3**: `:46`, `:49`, `:77` — production-approval framing reconciled | pointer → devops's rule.md section | pointer | not sealed |
| `agents/gaia/rule.md` | **B4 (fix round 1)**: `:9` | redirect → report (non-root form) | gaia's home stays `workflow.md`; this row carries the `rule.md` site | not sealed |
| `agents/designer/rule.md` | **B4**: `:30` "Default to asking the orchestrator, not guessing… my channel is the `NEEDS MORE INFO` reply" | section pointer added (already orchestrator-form) | designer's home stays `soul.md` | not sealed |
| `agents/designer/workflow.md` | **B4**: `:34` | pointer | pointer | not sealed |
| `agents/designer/skills-template/design-strategy.md` | **B4**: `:31` | pointer (skill template) | pointer | not sealed |
| `agents/coder/workflow.md` | **B4**: `:30` "ask the dispatcher before guessing" — rendered in the **wanderer:91 precedent form verbatim** | EDIT (clause-family completion) | coder's home stays `soul.md` | not sealed |
| `agents/blueprinter/rule.md` | **HOME-ADDED (fix round 4, `9f8b49306`)** — `:34` — reports upward and **is** a dispatcher | the canonical "## Open questions for the orchestrator" home ADDED — the 674 B core as a **prefix-extension** (§10.4b, §10.4c.2) | **canonical home (blueprinter)** | **SEALED** (new distinct sealed file — §11.3 row 18) |
| `agents/charter/rule.md` | **HOME-ADDED (fix round 4, `9f8b49306`)** — `:74` — its NEEDS MORE INFO is a **pre-work abort**, not a report-time question | the canonical "## Open questions for the orchestrator" home ADDED — the 674 B core as a **prefix-extension** (§10.4b, §10.4c.2) | **canonical home (charter)** | **SEALED** (new distinct sealed file — §11.3 row 18) |

### 10.4a Class B-r2 — FIX ROUND 2 semantic-class sweep (B3 CLOSURE) — **LANDED** `4088be633`

Fix round 2 (`4088be633`, 29 files, +393/−161) **closed B3**. The briefed sweep had only covered the *"begin, deliver, or ask"* clause family; the remaining **human-decision instructions** (approval / confirmation / show-user / decision-verb classes) were routed to the same report channel, canonicalised so a non-root **REPORTS** a blocker instead of asking the human directly.

| Metric | Value |
|---|---|
| Prose files converted | **26** (`agents/**`) |
| CONVERT sites | **~115** |
| Adjudication ledger | **~230 rows** — every CONVERT **and** every NO-EDIT carries a reason |
| Sites caught **ONLY** by the manual semantic read (no regex reached them) | **6** |
| Canonical-host files gained / reused | **4** — `jober/rule.md` (pre-existing host, re-used) · `maintenancer/rule.md` (**new** host) · `project-manager/rule.md` (**new** host) · the `tester/*` ensure.md family (resolves to the pre-existing `tester/rule.md` host) ⇒ **2 headings gained ⇒ hosts 14 → 16** |
| New edit-set members (63 → 74 agents prose) | **11** — marked ★ below |
| Clause-family sites | **41** — **UNCHANGED**; round 2's ~115 conversions were **other semantic classes**, not the clause family |

**The 26 converted files** (★ = new edit-set member — in the branch diff, never in any sweep table):

| File | Round-2 note |
|---|---|
| `agents/_baby_template/soul.md` | ★ · round-1 survivor the token grep missed (`:11`) |
| `agents/_baby_template/workflow.md` | ask-family residual |
| `agents/_mother/rule.md` | approval class |
| `agents/_mother/workflow.md` | ask-family residual |
| `agents/_prompt_system/innate-skills/coordination/skill.md` | ★ · shared coordination skill (outside the versioned-prompt-artifact set) |
| `agents/devops/soul.md` | production-approval class |
| `agents/devops/workflow.md` | ★ · **6 sites** (known file) |
| `agents/gaia/workflow.md` | ask-family residual |
| `agents/giter/rule.md` | ★ · **8 sites** (known file) |
| `agents/giter/soul.md` | ★ · approval class |
| `agents/giter/workflow.md` | **`:111`** (known file) |
| `agents/jober/rule.md` | **`:124` / `:126` / `:194`** (known file) + round-1 survivor **`:211-212`** |
| `agents/jober/workflow.md` | ask-family residual |
| `agents/leader/rule.md` | approval class — **SEALED, re-pinned** (round 2) |
| `agents/leader/workflow.md` | round-1 survivors **`:147` / `:188`** |
| `agents/maintenancer/growth.md` | ★ · inert file, still converted (R-c) |
| `agents/maintenancer/rule.md` | round-1 survivor **`:6`** + new host — **SEALED, re-pinned** (round 2) |
| `agents/maintenancer/skills-template/ens-db-repair.md` | ★ · Class-D row flipped to CONVERT |
| `agents/maintenancer/skills-template/restart-upgrade-ops.md` | ★ · Class-D row flipped to CONVERT |
| `agents/maintenancer/tools_note.md` | ★ · approval class |
| `agents/project-manager/rule.md` | round-1 survivor **`:13`** + new host (R-c) |
| `agents/project-manager/workflow.md` | approval class — **SEALED, re-pinned** (round 2) |
| `agents/tester/growth.md` | ★ · inert file, still converted (R-c) |
| `agents/tester/rule.md` | ensure.md family |
| `agents/tester/skills-template/ensure-validation.md` | ★ · ensure.md family |
| `agents/tester/workflow.md` | ask-family residual |

**The 4 known files** (the round-2 lane's headline conversions): `agents/devops/workflow.md` (**6** sites) · `agents/giter/rule.md` (`:8`) · `agents/giter/workflow.md` (`:111`) · `agents/jober/rule.md` (`:124` / `:126` / `:194`).
**The round-1 survivors the token grep missed** (caught by the manual semantic read): `agents/leader/workflow.md` (`:147` / `:188`) · `agents/jober/rule.md` (`:211-212`) · `agents/_baby_template/soul.md` (`:11`) · `agents/maintenancer/rule.md` (`:6`) · `agents/project-manager/rule.md` (`:13`) · the `tester/*` ensure.md family.

**Leader ratifications (ADJUDICATION #4 — decisions, NOT open questions).** The round-2 lane's ledger carries **11 NO-EDIT + 1 CONVERT** leader-ratified adjudications; the three that change how the sweep must be read are recorded normatively below (R-a / R-b / R-c).

| ID | Ratification | Effect on the sweep |
|---|---|---|
| **R-a** | The **upgrade nonce ceremony is ROOT-BOUND** — a non-root maintenancer structurally **CANNOT** complete a live upgrade: the root must **mint AND arm** the nonce. The lane left this wording **NO-EDIT deliberately** — a relay would weaken the 3-factor ceremony. | A `NO EDIT` adjudication on the nonce text, **on purpose**. It is a **contract**, not a sweep gap. **Final-report line (verbatim):** *"the upgrade nonce ceremony is root-only; non-root agents must not relay it."* |
| **R-b** | **TWO canonical wording families, RATIFIED AS DELIBERATE** — the sweep must NOT "unify" them. | (i) **root-CONDITIONAL** clause (*"…unless you are the user-facing root"*) for agents that can plausibly **BE** a user-facing root — **leader, maintenancer, project-manager**; (ii) **FLAT** *"never ask the user directly"* for **structurally-never-root** agents — **jober, _baby_template, giter, devops**. Rationale: a conditional preserves the root's own ask capability (the gate admits a root; the wording must not pre-empt it), whereas a structurally-never-root agent gains nothing from a conditional and loses clarity. On disk (measured during Amendment #4; the gaia/_mother half of the conditional form landed as `7e7062ce3` on top of `4088be633`): **34** files carry the root-conditional form, **9** carry the flat form. |
| **R-c** | project-manager *"the final call is human"* → **conditional** form **RATIFIED**; **growth.md conversions KEPT** (inert files — never composed into a prompt — but **consistency over diff-minimization**). | `agents/project-manager/rule.md:13` keeps the human-decision framing **and** routes the decision through the report; `maintenancer/growth.md` + `tester/growth.md` stay converted. |

### 10.4b Fix round 3 — CONTRACT-COMPLETION ROUND (B1) — **LANDED** `a9c041ab0`

**B1 — the 11 unserved dirs gained a canonical home.** The commit added the canonical section to **11** new homes (all `rule.md`, per the D/E registry) ⇒ **canonical homes 16 → 27**. The **PARENT-SIDE** paragraph was added to **all six** families.

| # | New canonical home | Family | Seal pin |
|---|---|---|---|
| 1 | `agents/developer/rule.md` | developer (v1) | **SEALED** (`:1383`) |
| 2 | `agents/developer[v2]/rule.md` | developer (v2) | not sealed (only `developer[v2]/tools_note.md` is) |
| 3 | `agents/approver/rule.md` | approver (v1) | **SEALED** (`:1378`) |
| 4 | `agents/approver[v2]/rule.md` | approver (v2) | **SEALED** (`:1379`) |
| 5 | `agents/architect/rule.md` | architect | **SEALED** (`:1380`) |
| 6 | `agents/planner/rule.md` | planner (v1) | **SEALED** (`:1390`) |
| 7 | `agents/planner[v2]/rule.md` | planner (v2) | not sealed (only `planner[v2]/tools_note.md` is) |
| 8 | `agents/reviewer/rule.md` | reviewer (v1) | **SEALED** (`:1393`) |
| 9 | `agents/reviewer[v2]/rule.md` | reviewer (v2) | **SEALED** (`:1394`) |
| 10 | `agents/tidier/rule.md` | tidier (v1) | **SEALED** (`:1395`) |
| 11 | `agents/tidier[v2]/rule.md` | tidier (v2) | **SEALED** (`:1396`) |

**Parity claim — WORDING PRECISION (AMENDMENT #5 item 6, RESTATED by AMENDMENT #6 / approver i3 B3).** The claim is stated in the **prefix-extension** form: **the 4-bullet core is byte-identical across 29/29 homes (all prefix-extensions of the 674 B core)**. The earlier strict form — *"byte-identical across all"* — is **WITHDRAWN**: it WAS the approver i3 blocker, because two homes carry a **trailing addition** beyond the core (their contract body is identical, but the section carries an extra trailing element), so a whole-section byte-equality claim is not true and must not be asserted. The **674 B 4-bullet core** is the unit of equality; the trailing-addition outliers are **prefix-extensions** of it. The §10.4 block was verified **29/29 tree-wide** (worker-verified) against `worker/rule.md` at `30ea77149`.

**PARENT-SIDE paragraph — DEVIATION FROM THE BRIEF (LEADER-RATIFIED).** The brief stated that architect / planner / tidier would need **NO** parent-side paragraph. That expectation was **WRONG** — each of the three dispatches work, so each needs the parent-side half. The paragraph was therefore added to **ALL SIX families** (developer, approver, architect, planner, reviewer, tidier). Evidence:

| Agent | Evidence that it IS a dispatcher/controller | Anchor |
|---|---|---|
| architect | controller/dispatcher + competitive fan-out | `agents/architect/soul.md:5,7,12` |
| planner | two dispatch channels, one of them `spawn_instance` | `agents/planner/soul.md:15-20` |
| tidier | "the v2 Tidier dispatches workers" | `agents/tidier/soul.md:18` |

⇒ **RECORDED AS A LEADER-RATIFIED DEVIATION** — the landed tree is the truth; the brief's expectation is superseded, and the deviation is recorded here rather than left implicit.

**Pointer reductions = 0 — VERIFIED NO-OP (LEADER-RATIFIED).** The brief's pointer-reduction expectation did **not** materialise, and the verification shows why: **every remaining clause occurrence is the §10.5 landed keep-verbatim allow-list** (the long-form list at §10.5 — plan-overview.md:506+ — **19/19/19**), with **served-tree precedent** (the already-served agents kept the same occurrences). ⇒ **no pointer-reduction operation was performed; the count is 0 by adjudication, not by omission.** Recorded here so the approver sees the adjudication rather than a silent skip.

### 10.4c Fix round 4 — APPROVER ITERATION-003 CLOSURE (AMENDMENT #6) — **LANDED** `235ec6934` → `9f8b49306` → `23859484e` → `30ea77149`

**Scope.** The approver's iteration-003 findings are closed as **leader-dispositioned** — the branch's edit rounds are **CLOSED at `30ea77149`** (4 commits after `a9aa911a3`). This subsection is the **transcript**: findings are recorded faithfully, not re-litigated.

**Files vs base `a9aa911a3`: 11 unique, +158/−19, 4 commits** (8 agents prose + baseline + the seal test file + the gate pack). `agents/maintenancer/rule.md` is **NOT** in the list — `30ea77149` reverted it **byte-identical to base**.

**FULL partition `0e7500a1b..30ea77149` = 97 = 78 agents prose + 3 `meta.json` + 1 baseline + 3 `daemon/` + 3 `docs/` + 7 tests + 2 planning docs** (commission surface **95**). The derivation command re-runs read-only and must match — §10.10, command A.

#### 10.4c.1 Round 1 — `235ec6934` — the gate pack (test-only)

| Item | Detail |
|---|---|
| **T4b re-shaped** | `test_t4b_legacy_empty_parent_and_empty_source_type_has_no_question_tool` (`:258`) — re-shaped to the **SPECIFIED** legacy case: `parent_id=""` **AND** `source_type=""` ⇒ **no tool** |
| **T6-gate ADDED** | `test_t6_root_with_non_chat_source_type_has_no_question_tool` (`:329`) — a root with a **REAL** non-chat `source_type` (webhook/scheduler, cited from `daemon/models/source.py:27,30` + `daemon/routers/sources.py:155`; negative control `repository.py:919`) |
| **Gate pack** | **13 → 14** tests |

> **NAME COLLISION — recorded, not silently resolved.** The new gate-pack test's pytest name begins `test_t6_`, while §11.1's **T6** is the **flow proof** in `tests/unit/services/test_child_report_open_questions_flow.py` (T6×5 cases). The two live in **different files** and prove different layers (gate vs. flow). The plan's canonical T-ID set stays **T1–T9**; §11.1 and this subsection refer to the new gate test as **T6-gate** so the IDs stay unambiguous. §11.1's T6 row is unchanged.

**MUTANT RE-PROOF at HEAD `30ea77149`** (independent; scratch removed) — the two mutants the approver found **surviving** are now **CAUGHT**:

| Mutant | Mutation | Red set (EXACT) | Collateral | Restore |
|---|---|---|---|---|
| **M-i** | `""` admitted as a chat source type | `test_t4b_legacy_empty_parent_and_empty_source_type_has_no_question_tool` | **zero** | **green** |
| **M-ii** | any non-chat source type admitted | `test_t6_root_with_non_chat_source_type_has_no_question_tool` | **zero** | **green** |

#### 10.4c.2 Round 2 — `9f8b49306` — the 13-row UNAUDITED-DIRS ledger

| # | Dir | Disposition | Evidence / rationale |
|---|---|---|---|
| 1 | `blueprinter/rule.md` | **HOME-ADDED** | `:34` — reports upward **and** is a dispatcher |
| 2 | `charter/rule.md` | **HOME-ADDED** | `:74` — its NEEDS MORE INFO is a **pre-work abort**, not a report-time question |
| 3 | `critic` | **ADJUDICATED — NO HOME** | blocker → orchestrator clause |
| 4 | `sketcher` | **ADJUDICATED — NO HOME** | blocker → orchestrator clause |
| 5 | `doc-maintainer` | **ADJUDICATED — NO HOME** | Errors / unclear_intent |
| 6 | `experiencer` | **ADJUDICATED — NO HOME** | structured gap form |
| 7 | `image-reader` | **ADJUDICATED — NO HOME** | structured gap form |
| 8 | `kb-importer` | **ADJUDICATED — NO HOME** | structured gap form |
| 9 | `kb-writer` | **ADJUDICATED — NO HOME** | structured gap form |
| 10 | `explorer` | **ADJUDICATED — NO HOME** | hard-contract conflict + the `EXEMPT_AGENTS` registry — **LEADER-RATIFIED** |
| 11 | `image-comparator` | **ADJUDICATED — NO HOME** | leaf, structured output |
| 12 | `skill-keeper` | **ADJUDICATED — NO HOME** | leaf, structured output |
| 13 | `watcher` | **ADJUDICATED — NO HOME** | text-only verdict by design |

⇒ **canonical homes 27 → 29** (`blueprinter/rule.md`, `charter/rule.md` added). Both new homes are **prefix-extensions** of the 674 B core — §10.4b's restated parity claim.

#### 10.4c.3 Round 3 — `23859484e` — the 13-row PARENT-SIDE ledger

**Carrier derivation.** The list is derived from `git show 23859484e` (the 8 **added** carriers) **plus** the 12 pre-existing carriers (⇒ 20 after this round) **minus** the maintenancer removal at `30ea77149` (⇒ **19** at HEAD).

| # | Carrier | Disposition | Detail |
|---|---|---|---|
| 1 | `agents/coder/soul.md:169` | **ADDED** | dispatcher of `worker` instances |
| 2 | `agents/designer/soul.md:81` | **ADDED** | orchestrator-form dispatcher |
| 3 | `agents/governor/rule.md:199` | **ADDED** | council-manager — its "revival guidance" is the parent-side channel |
| 4 | `agents/maintenancer/rule.md:96` | **ADDED → REMOVED** at `30ea77149` | the paragraph named a channel maintenancer does **not** hold — §10.4c.4 |
| 5 | `agents/project-manager/rule.md:58` | **ADDED** | dispatcher |
| 6 | `agents/tester/rule.md:144` | **ADDED** | dispatcher |
| 7 | `agents/wanderer/rule.md:88` | **ADDED** | dispatcher |
| 8 | `agents/blueprinter/rule.md:45` | **ADDED** | reports upward + is a dispatcher |
| 9 | `devops` | **RULING — NO PARAGRAPH** | no instance category ⇒ no fan-in |
| 10 | `giter` | **RULING — NO PARAGRAPH** | no instance category ⇒ no fan-in |
| 11 | `jober` | **RULING — NO PARAGRAPH** | the job-lane equivalent is **`job_continue`** — the canonical `send_message` wording was **factually wrong** — **LEADER-RATIFIED** |
| 12 | `_baby_template` | **RULING — NO PARAGRAPH** | leaf template — blast radius avoided |
| 13 | `_mother` | **RULING — NO PARAGRAPH** | leaf template — blast radius avoided |

**PARENT-SIDE PARAGRAPH CARRIERS = 19** at HEAD (18 agent files + the **leader variant** `agents/leader/workflow.md`); `agents/maintenancer/rule.md` removed by `30ea77149`. Verified by tree grep for the parent-side sentence at `30ea77149`.

#### 10.4c.4 Round 4 — `30ea77149` — the maintenancer removal (adjudicated)

**DEFECT.** `agents/maintenancer/rule.md:96` (the paragraph added at `23859484e`) asserted a tool call the agent cannot make: *"…I instruct that instance through the continuation path (send_message to the child instance revives it with its context intact)."*

**REAL-CHANNEL DETERMINATION** (a runtime probe with the **real** `_apply_tool_filter`, not a reading of the prompt):

| Evidence | Finding |
|---|---|
| `agents/maintenancer/meta.json:12` — `tools.allow` | `["system-log", "ens-db", "knowledge", "system_upgrade", "system_restart", "db"]` — the **`instance` category is ABSENT**; the allow list is exclusive |
| `daemon/tools/instance.py:2573` / `:3646` | `spawn_instance` and `send_message` are both `@register_tool_category("instance")` |
| `daemon/tools/instance.py:386-416` / `:6432` | `resolve_tool_filter` expands only the allowed categories; `_apply_tool_filter` filters by name membership |
| **runtime probe** (production filter, worktree venv) | maintenancer keeps `db_execute`, `ens_db_postgres_select`, `explore`, `generate_chart`, `system_log_search`, `system_restart_status`, `system_upgrade_status`, `todo_create` — **`send_message` NO, `spawn_instance` NO** |
| `daemon/tools/ens_db_tools.py:247-254`, `:441-487` | its only DB writer (`ens_db_repair_execute`) **refuses** writes to `messages` / `message_queue` (self-surgery refusal) — not an outbound instruction channel either |

⇒ maintenancer's real channel is **INBOUND-ONLY**: it receives dispatched work and its only outbound artifact is its own completion report, which the daemon delivers to the requester. **The PARENT, not maintenancer, revives a child with context intact.**

**ADJUDICATION — REMOVE the paragraph entirely** (the branch is "remove", **not** "reword"). The **4-bullet 674 B canonical core** of the "## Open questions for the orchestrator" home **STAYS**; only the parent-side paragraph goes. Rationale: with no `instance` category there is no channel to instruct the reporting instance, so no rewording can name a genuine one; a reword naming the report-only path would still imply maintenancer resolves the child's question itself, which is the parent's job.

**CONSEQUENCE — the self-consistency proof.** `agents/maintenancer/rule.md` is now **byte-identical to base `a9aa911a3`** (verified: an empty `git diff a9aa911a3..30ea77149 -- agents/maintenancer/rule.md`), and its seal constant is **back to its pre-B3 value** `abe43bae715c62a341788fbe5d610138936b7b4e83df87c8bbfd3611bf446aec` (`:1448`) — the round-4 re-pin is a **revert**, not a new value. (`agents/charter/rule.md` `2cb6e01b…` `:1405` · `agents/coder/soul.md` `6263b648…` `:1434` · `agents/governor/rule.md` `07aaed75…` `:1439`.)

**BONUS SCAN.** Of all **20** parent-side carriers, maintenancer was the **ONLY** one whose `meta.json` lacked the instance category — **no other agent asserts a missing channel**.

#### 10.4c.5 Pre-existing failures — **11 / 0 new** (unchanged node IDs)

The round-4 battery at `30ea77149` reports **zero new failures**; the **11** known pre-existing failures (5 closure-sweep, 2 devops `meta.json`, 3 gaia `meta.json`, 1 `job_answer` order pin) are unchanged in **count and node ID** — §11.5.

#### 10.4c.6 Corrections to the AMENDMENT #6 brief (reported, not substituted)

| # | The brief said | **TREE TRUTH at `30ea77149`** |
|---|---|---|
| 1 | `report_integrity_guard.py` and `report_integrity_repro.py` are test files at `tests/unit/services/` / `tests/integration/` | **The brief's path corrections were RIGHT in intent — the artifacts are the TEST files, whose names carry the `test_` prefix.** Full tree-truth (verified at `30ea77149`): `daemon/services/report_integrity_guard.py` = the SOURCE module (+ sibling `report_integrity_metrics.py`); **`tests/unit/services/test_report_integrity_guard.py`** = the guard's TEST; **`tests/integration/test_report_integrity_repro.py`** = the repro TEST (**745 lines**, tracked, last touched `26fe4d9f7`). An earlier correction claiming "report_integrity_repro.py does not exist anywhere" was itself wrong — it searched the **unprefixed** name and missed the `test_`-prefixed file. ⇒ resolution recorded honestly: the original list was loose in naming but correct in location; every figure here is tree-verified |
| 2 | `origin/latest = 14a7f3762 (v0.26.0)` | **already stale** — `origin/latest` = local `latest` = **`e6eb13fcc`** (**v0.26.1** tip, TIP-VERIFIED) as of this amendment. `14a7f3762` exists in that history as the seal re-pin commit. ⇒ the version bump must target the **NEXT FREE VERSION** (latest is v0.26.1 ⇒ likely **0.26.2**; **confirm at bump time — do NOT hardcode**) — §13 step 2 |

### 10.5 Clause family — "begin, deliver, or ask" / "or ask"

**Edit rule (light, uniform):** replace the terminal `or ask` / `or ask — a question to my dispatcher` with the open-questions reporting semantics: `or report open questions`. Keep every other token of the paragraph — especially `"before ending any turn"` and `"zero tool calls"` (`E_REQUIRED_SNIPPETS`, `tests/unit/test_report_integrity_prompts.py:115-118`).

**Conditional clause (RULING-1 / risk R7):** the rewrite applies **unless you are the user-facing root**. A root's own ask wording must NOT be degraded — a root bound to a chat source keeps "ask the user" as its move (the tool is the root's ask channel). Where the sentence is agent-agnostic, render it as `…or report open questions (unless you are the user-facing root)`. Mitigation: the rewritten shared skill (`agents/_prompt_system/innate-skills/question/skill.md`) teaches the **root half**, so the sweep only removes the *non-root* default and never removes the root's capability.

**Site count (W4-counts, fix round 1):** **41** clause-family sites (was **34** = 15 short + 19 long; **+7 fix-round sites**: `gaia/rule.md:9`, `gaia/workflow.md:14`, `:39`, `designer/rule.md:30`, `designer/workflow.md:34`, `designer/skills-template/design-strategy.md:31`, `coder/workflow.md:30`).

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
| `agents/governor/rule.md` | `:82` — "**Before ending any turn** on a task dispatched to me, I do one of: begin the work with a tool call, deliver the synthesis on work already done, **or ask the question that blocks me**." | **SEALED** (sha `84a93586df376ad39dc553af36f86f684977ecfa8d717186f2fe8f16846c8cf5` at re-pin time; pin now at `tests/test_chart_image_delivery_e2e.py:1392` — current constant `a779742d…`) — **the 14th re-pin**. The line ALSO carries the junk-report clause ("…is not work-in-progress; it is detected as a junk/no-work report…") and **1 × "Chat Delivery"** — **both must survive verbatim**. Also a **D_HOME + E_HOME** carrier for the governor (`tests/unit/test_report_integrity_prompts.py:92,:106`) ⇒ the D and E snippets must survive too. |

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
| `agents/leader/workflow.md` | `:699` "escalate to the user" (FLAGGED by the implementer, leader-ratified #3) | **W3 — conditionalized to the report chain** (`6711c769e`): a non-root leader escalates user-critical items via its OWN report chain to a root — never "ask the user" wording (D4) | not sealed |
| `agents/maintenancer/rule.md` | `:9` | **W3** — same escalate-family conditionalization to the report chain | **SEALED** — re-pinned `d6e448a6…` (`tests/test_chart_image_delivery_e2e.py:1394`) |
| `agents/maintenancer/workflow.md` | `:53` | **W3** — same | not sealed |
| `agents/project-manager/rule.md` | `:45` | **W3** — same | not sealed |

### 10.7 Class D — adopt naming/semantics, NO change

| File | Anchors | Action |
|---|---|---|
| `agents/maintenancer/skills-template/bug-advisory.md` | `:50` "remaining-questions: <list \| none>" | NO EDIT — adopt the vocabulary in our contract |
| `agents/maintenancer/skills-template/ens-db-repair.md` | `:52` | **EDITED (fix round 2)** — the Class-D NO-EDIT was **flipped to CONVERT** by the semantic-class sweep (approval class ⇒ routed to the report channel) — §10.4a |
| `agents/maintenancer/skills-template/health-check.md` | `:46` | NO EDIT |
| `agents/maintenancer/skills-template/job-mission-repair.md` | `:52` | NO EDIT |
| `agents/maintenancer/skills-template/restart-upgrade-ops.md` | `:53` | **EDITED (fix round 2)** — the Class-D NO-EDIT was **flipped to CONVERT** by the semantic-class sweep — §10.4a |
| `agents/project-manager/soul.md` | `:58-59`, `:78-79`, `:93-94` "### Decisions Pending / `<0–3 framed questions>`" | NO EDIT (heading rename optional — see §10.5 note) |
| `agents/project-manager/workflow.md` | `:203-204` | clause-family edit only |
| `agents/critic/tools_note.md` | `:25` | NO EDIT |
| `agents/charter/workflow.md` | `:52`, `:16` — original sweep: keep-as-is · `:71` — **EDITED (fix round 1, B5, `6711c769e`)**: "ask the user to pick" → the NEEDS MORE INFO report form, self-consistent with `:52` | keep-as-is → **EDITED + re-pinned** (`2a5078f9…`, `tests/test_chart_image_delivery_e2e.py:1357`) |
| `agents/designer/tools_note.md` | `:79` | NO EDIT |
| `agents/_baby_template/rule.md` — identity ritual | `:12` "Perform tasks before learning your identity", learning-phase discipline | **NO EDIT (adjudicated — approver iteration-002 note)**: identity-phase discipline, not an ask channel; the ask-family sites `:6` / `:15` / `:40` WERE edited in the original sweep (`564ed0d2f`) |
| `agents/_baby_template/rule.md` — `:15` polarity | `:15` "Rush through questions" | **EDITED (already in the original sweep)** — now reads "Rush through questions, and never end a turn on a question instead of on the work" (a question is never the turn-ending move) |
| `agents/_inner_soul/{rule,soul,workflow}.md` | whole files — a **documentation-only** directory (no `meta.json` ⇒ invisible to agent discovery; **never loaded at runtime** — the behaviour lives in `daemon/tools/inner_soul.py`) | **NO EDIT (LEADER-RATIFIED)** — two independent reasons: (1) the files are **never composed into a prompt**, so the open-questions sweep has **no reach** there; (2) the `inner_soul` tool's **real** write path is an **out-of-band operator gate** — a draft lands in a `pending_approval` DB row and **the file is NEVER touched** until the operator approves (`daemon/tools/inner_soul.py:16`, `:1577`, `:1873`) ⇒ the `pending_approval` state is **not** an agent ask-channel and must **not** be swept into the report contract. |
| `agents/jober/rule.md` — `:211-212` polarity | `:211-212` "ask before proceeding" | **EDITED (fix round 2) — the round-1 NO-EDIT adjudication was REVERSED** by the manual semantic read: the block *was* parent-bound, but the acquisition verb still names a human, so the wording now reads *"The report is my only channel to the decision — I never ask the user directly"* — §10.4a |

### 10.8 Docs (optional scope notes)

| File | Anchors | Action |
|---|---|---|
| `docs/job-task-system.md` | `:447-482` (the "Question/stuck emission fan-out (QA events…)" section) | annotate: emitters are roots-only; the transport contract is unchanged |
| `docs/setup.md` | `:1336-1339` (the answer-gate / `user_answer_pending` runbook note) | scope the answer-gate handle to roots |
| `docs/agents.md` | `:336-352` (the tool-category table — **already stale**, no `question` row today) | **new row ADDED (landed `b2615416c` — the scope note was taken)**; do not inherit the existing drift (leave the rest of the stale table alone) |

### 10.9 DO NOT REWRITE (historical / awareness)

`docs/bugs/pause-during-report-turn-orphans-message-jobitem.md` · `docs/plans/turn-reconciler-named-transitions.md` · `CHANGELOG.md` · `docs/hallucination-protection.md` · `docs/architecture/*` · the planning tree under `.agents/shared/planning/` (180 dirs at read time — awareness only, except our own directory).

### 10.10 SWEEP MANIFEST — machine-derived (N4) — **FINAL at fix round 4** (`30ea77149`)

**Unit of account = one distinct file in the sweep set.** A file may appear in two sweep tables (e.g. `agents/leader/workflow.md` appears in §10.4, §10.5-long and §10.6) ⇒ the table rows collapse to distinct files. The numbers below are **generated** by enumerating §10.3–§10.7 (incl. the new §10.4a) plus §10.8 docs — never hand-counted — with the **branch diff as the machine cross-check** (below).

| Class | Sweep table | Table rows | Distinct files contributed | Actually edited |
|---|---|---|---|---|
| **A** — tool grant | §10.3 | 3 | 3 | 3 |
| **B** — ask → open-questions (incl. B3/B4 fix-round-1 rows + the 2 fix-round-3 rows + the 2 fix-round-4 home-additions) | §10.4 | 38 | 38 | 33 |
| **B-r2** — fix-round-2 semantic-class sweep (B3 closure) | §10.4a | 26 | 26 | 26 |
| **5-short** — clause family | §10.5 | 15 | 15 | 15 |
| **5-long** — clause family (one comma line, 19 paths) | §10.5 | 19 | 19 | 19 |
| **C** — parent-side + W3 escalate-family | §10.6 | 8 | 7 | 7 |
| **D** — Class-D rows + B5 charter flip + adjudications + the `_inner_soul` NO-EDIT row | §10.7 | 11 | 11 | 4 |
| **Docs** — scope notes (landed `b2615416c`) | §10.8 | 3 | 3 | 3 |
| | **TOTAL — FINAL (fix round 4 included)** | **123** | **96** | **84** |

| Canonical figure | Value | Derivation |
|---|---|---|
| **Files actually edited (final)** | **84 = 3 `meta.json` + 81 prompt/doc** | 96 distinct − 12 not-edited |
| **Not edited (12)** | `agents/leader/tools_note.md` (**OPTIONAL — not taken**) · `agents/wanderer/skills-template/{code-investigation,codebase-mapping,library-research,root-cause-analysis}.md` (**4 × VERIFY-ONLY**) · `agents/maintenancer/skills-template/{bug-advisory,health-check,job-mission-repair}.md` (3 Class-D rows) · `agents/project-manager/soul.md` · `agents/critic/tools_note.md` · `agents/designer/tools_note.md` · `agents/_inner_soul/{rule,soul,workflow}.md` (**documentation-only**, never loaded at runtime — §10.7) | §10.4, §10.7 (the two remaining Class-D maintenance rows were **flipped to CONVERT** in fix round 2) |
| **Canonical homes** | **29** | tree grep for `^## Open questions for the orchestrator` — §10.4a, §10.4b, §10.4c.2 |

**THE 97-FILE BRANCH PARTITION (the authoritative reconciliation — fix round 4).** `git diff --name-only 0e7500a1b..30ea77149` = **97 files**:

| Partition | Count | Contents |
|---|---|---|
| **agents prompt prose** (`.md`) | **78** | the sweep edit-set (63 after fix round 1 **+ 11 new members** in round 2 **+ 2 new members** in round 3 **+ 2 new members** in round 4) |
| **agents `meta.json`** | **3** | `leader` · `devops` · `ari` (P4 grants — §10.3) |
| **agents baseline** (regenerated) | **1** | `agents/.prompt-artifacts.sha256` (161 artifacts) |
| **`daemon/`** | **3** | `daemon/tools/instance.py` · `daemon/services/instance_lifecycle.py` · `daemon/routers/mappings.py` (the gate + the lane) |
| **`docs/`** | **3** | `docs/agents.md` · `docs/job-task-system.md` · `docs/setup.md` (§10.8) |
| **`tests/`** | **7** | the gate pack · the flow pack · the next-turn pack · the `job_answer` pack · the seal file · `test_devops_agent.py` · `test_ari_worker_integration.py` |
| **planning** (this commission's own docs) | **2** | `plan-overview.md` · `decisions.md` |
| | **97** | ⇒ **commission surface = 95** (97 − the plan's own 2 planning docs) |

⇒ **The number the final report quotes is 97** (full branch partition), of which the **commission surface is 95** — **78 prompt prose + 3 `meta.json` + 1 baseline + 3 `daemon/` + 3 `docs/` + 7 tests**. The prompt/doc edit-set is **79** (76 agents prose + 3 docs).

**THE 11 NEW EDIT-SET MEMBERS (fix round 2 — 63 → 74 agents prose).** In the branch diff but in **no** sweep table before round 2: `agents/_baby_template/soul.md` · `agents/_prompt_system/innate-skills/coordination/skill.md` · `agents/devops/workflow.md` · `agents/giter/rule.md` · `agents/giter/soul.md` · `agents/maintenancer/growth.md` · `agents/maintenancer/skills-template/ens-db-repair.md` · `agents/maintenancer/skills-template/restart-upgrade-ops.md` · `agents/maintenancer/tools_note.md` · `agents/tester/growth.md` · `agents/tester/skills-template/ensure-validation.md`. (Two of them — `ens-db-repair.md`, `restart-upgrade-ops.md` — were **Class-D NO-EDIT rows** that round 2 flipped to CONVERT; the other **9** were never in any sweep table. The 4 edited files absent from the prompt-artifact baseline — `coordination/skill.md` + the three `skills-template/*.md` — sit **outside** the versioned-prompt-artifact set, which is depth-2 only.)

**THE 2 NEW EDIT-SET MEMBERS (fix round 4 — 76 → 78 agents prose).** `agents/blueprinter/rule.md` (the **HOME-ADDED** canonical home — `:34`, reports upward + is a dispatcher) · `agents/charter/rule.md` (the **HOME-ADDED** canonical home — `:74`, its NEEDS MORE INFO is a **pre-work abort**, not a report-time question). Both were added by the **UNAUDITED-DIRS round** `9f8b49306`; both are **SEALED** ⇒ `agents/charter/rule.md` is a **NEW distinct sealed file** (17 → **18** distinct, membership still 35) — §11.3. The other 8 round-4 edits (`coder/soul.md`, `designer/soul.md`, `governor/rule.md`, `project-manager/rule.md`, `tester/rule.md`, `wanderer/rule.md`, `blueprinter/rule.md` and the reverted `maintenancer/rule.md`) are **already-counted** files — they were in the partition before this round.

**THE 2 NEW EDIT-SET MEMBERS (fix round 3 — 74 → 76 agents prose).** `agents/_prompt_system/innate-skills/job-orchestration/skill.md` (the **roots-only** QUESTION_REQUESTED relay rider — §10.4) · `agents/governor/tools_note.md` (the `question`-category drop + the canonical-section pointer — §10.4). The other **9** round-3 prose files are the **11 new canonical homes** of §10.4b, which were already inside the sweep set as clause-family sites.

**Fix-round commit map:** `f332e83e0` (B3 devops) · `8589d9be5` (B4 gaia/designer/coder) · `6711c769e` (B5 charter + W3 escalate) · `c5bd90146` (devops residual) · `78469d671` + `6fa226976` (seal re-pins, fix round 1) · `12291f2a4` (flow proofs T6/T7/T8) · `b7e3a0808` (tester: next-turn execution + `job_answer` wrapper — test-only) · `4088be633` (**fix round 2**: B3 closure + 3 seal re-pins + baseline regen + gate-pack pins) · `7e7062ce3` (ratified **R-b CONVERT** — gaia/** + _mother/**) · `b3b9d351f` (**plan amendment #4**) · `3ab411557` (**tidy pass**: M1 diagnostics log · L13 fail-closed guard · M7 seal provenance corrections + comment debt — §11.6) · `a9c041ab0` (**fix round 3 / contract completion**: B1 — the 11 new canonical homes + the PARENT-SIDE paragraph + the pointer NO-OP; B3 — the governor `tools_note.md` drop + the `job-orchestration` rider + the devops addendum; 10 seal re-re-pins + baseline regen).

**Derivation command A — the 97-file branch partition** (read-only; run from the repo root of the worktree; **output must match** the partition table above):

```bash
cd /home/nea/ensemble-src-wt-hidenonroot
git diff --name-only 0e7500a1b..30ea77149 | wc -l          # -> 97
git diff --name-only 0e7500a1b..30ea77149 | awk -F'/' '{
  if ($1=="agents") { if ($NF=="meta.json") print "agents/meta.json";
                      else if ($NF ~ /sha256/)  print "agents/baseline";
                      else print "agents/prose" }
  else if ($1=="daemon") print "daemon"
  else if ($1=="docs")   print "docs"
  else if ($1=="tests")  print "tests"
  else print "planning" }' | sort | uniq -c
# -> 2 planning / 1 agents/baseline / 3 agents/meta.json / 78 agents/prose
#    / 3 daemon / 3 docs / 7 tests
```

**Derivation command B — the sweep manifest** (read-only; re-reads THIS file; §10.7 NO-EDIT adjudication rows are skipped by their two-cell shape + the `no edit` filter; the §10.4a range is now part of the **B** class):

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
classes=[('A  10.3','### 10.3','### 10.4'),('B  10.4','### 10.4','### 10.4a'),
         ('B-r2 10.4a','### 10.4a','### 10.5'),('5-short 10.5','### 10.5','### 10.6'),
         ('C  10.6','### 10.6','### 10.7'),('D  10.7','### 10.7','### 10.8'),
         ('Docs 10.8','### 10.8','### 10.9')]
allrows=[]; tot=0
for name,a,b in classes:
    r=rows(sec(a,b)); allrows+=r; tot+=len(r)
    seen=set(); ed=0
    for f,t in r:
        if f in seen: continue
        seen.add(f)
        if 'verify-only' not in t and 'optional' not in t and 'no edit' not in t: ed+=1
    print(f'{name}: rows={len(r)} distinct={len(seen)} edited={ed}')
longline=[l for l in sec('### 10.5','### 10.6') if 'approval-strategy.md' in l][0]
longrows=[(f,'edit') for f in re.findall(r'`([^`]+)`',longline) if f.startswith('agents/')]
allrows += longrows; tot += len(longrows)
seen=set(); edited=0
for f,txt in allrows:
    if f in seen: continue
    seen.add(f)
    if 'verify-only' not in txt and 'optional' not in txt and 'no edit' not in txt: edited+=1
print('TOTAL class rows',tot,'| sweep rows(distinct)',len(seen),'| edited',edited)
PY
# -> TOTAL class rows 123 | sweep rows(distinct) 96 | edited 84
```

### 10.11 APPENDIX — the reusable audit artifact: the sweep PATTERN SET (P1–P6) + the MANUAL SEMANTIC READ

The reusable audit artifact of this sweep: the pattern set that found the sites, **plus** the human pass that found what the patterns could not. Re-run it for any future prompt-surface sweep of the same shape (the pattern set is the *method*; the per-site reasons live in the ~230-row adjudication ledger).

| Pattern | What it matches | Verdict |
|---|---|---|
| **P1** | the **21 briefed token patterns** (`ask` / `ask the user` / `ask you` / `question` / `confirm` / `approval` / … token family) | **NECESSARY BUT INSUFFICIENT** — it found the briefed sites and stopped there |
| **P2** | **two-way human-actor** (a line naming a human on **both** sides of the interaction) | **NECESSARY BUT INSUFFICIENT** |
| **P3 / P3'** | **human-actor census** + **decision-verbs-near-human** | **discriminating** |
| **P4** | **whole-partition human-actor census, read LINE-BY-LINE — NOT line-scoped** | **discriminating** — the pass that found the round-1 survivors |
| **P5** | **delivery / escalation verbs** | supporting |
| **P6** | the bare token **`'human'`** | supporting |
| **MANUAL SEMANTIC READ** | every candidate line read by a human, plus the whole partition read for context | **caught 6 sites that NO regex reached** |

**Verdict on P1/P2 — NECESSARY BUT INSUFFICIENT.** They find the briefed shapes and stop there: a pattern is a **token** test, never a **meaning** test.

**The blind-spot insight (what P1/P2 structurally cannot see):**

- **acquisition verbs that name NO human on the line** — *"ask before proceeding"*, *"ask if you should keep retrying"*: the line never says "user", so the human-actor census sees nothing;
- **question-template forms** — `Ask: "…"` — the token sits in a template slot, not in a prose clause;
- **"ask you"** — not "ask user" ⇒ the human-actor pattern does not fire;
- **"present best plan to user"** — P1 needs `present` and `plan` **adjacent**; one word between them ⇒ P1 misses it.

⇒ **P3/P4 are the discriminating passes.** P3/P3' catch decision-verbs sitting next to a human noun; **P4** is a *whole-partition* human-actor census read **line-by-line, NOT line-scoped** — it is the pass that catches a human-named site whose **action** lives on a neighbouring line.

**The 6 sites caught ONLY by the manual semantic read** (no regex reached them) are the reason the round-2 lane could close B3 at all; their per-site reasons are carried in the round-2 adjudication ledger (~230 rows) and in the sibling decision record. **A sweep that stops at P1/P2 is not finished** — the ledger is the artefact, not the pattern list.

---

## 11. Test plan

### 11.1 New tests T1–T9

**Red pre-change? (BLK-7, RECLASSIFIED by fix round 1 — A1).** THREE evidence classes now:

- **GATE PROOF — RED pre-change: T1, T3, T4b, T5, T6-gate, T9.** (**T6-gate** added in fix round 4 — §10.4c.1; the plan's T-IDs stay **T1–T9**, and **T6-gate** is the gate-pack test whose pytest name begins `test_t6_` — it is **NOT** §11.1's T6 flow proof, which lives in a different file.)
- **PRESERVATION — GREEN PRE-CHANGE (signature-adaptive harness): T2, T4a.** The landed harness passes the `parent_id=` / `source_type=` kwargs ONLY when the function signature already supports them, and otherwise builds the toolset the pre-change way — and the pre-change build registers the question tool **unconditionally** ⇒ the preservation guards **PASS** pre-change. The landed class is literally named **`TestPreservationGuardsGreenPreChange`** (`tests/unit/tools/test_question_tool_root_gate.py:311`), and its docstring (`:52-71`) states the rationale: *"always passing the new kwargs would raise `TypeError` pre-change, failing the preservation guards for a reason that has nothing to do with the behaviour they protect"*. ⇒ the **RED** witness for the new kwargs' absence is the dedicated **AC3 keyword-only signature pin** (`test_ac3_parent_id_and_source_type_are_keyword_only`; `:69` — *"two parameters exist AND are keyword-only, and it is RED before the…"*; `:466` — *"RED-pre-change (gate proof) — AC3 seam pin"*).
- **FLOW PROOFS — LANDED: T6, T7, T8** (the **flow-proof** T6, in a different file from the gate pack) in `tests/unit/services/test_child_report_open_questions_flow.py` (42,584 bytes; 13 `def test_` + parametrization = **16 cases**: T6×5, T7×7, T8×4; **mutation-verified 5/5**). No longer "planned" — authored and green on the branch at `12291f2a4`.

> **Non-obvious failure mode — HISTORICAL NOTE (superseded by the signature-adaptive harness).** Pre-change, `create_instance_tools` did **not** accept the `parent_id=` / `source_type=` kwargs, so a naive harness would have failed with **`TypeError: create_instance_tools() got an unexpected keyword argument 'parent_id'`** (or `… 'source_type'`). The landed harness converts that into a **GREEN pre-change** preservation run instead — the pre-change build registers the question tool unconditionally, so *"a legitimate root keeps the tool"* is asserted exactly as it should be. **The adaptivity is not allowed to hide a missing implementation**: the dedicated **AC3 keyword-only signature pin** is **RED** before the change and fails loudly if the implementation forgets the two parameters. ⇒ **A green preservation run is never reported as gate proof** (proof-set naming: §11.4, §11.5, §17, §17.1).

| ID | Proposed test file | Setup | Assertion | Red pre-change? | Notes |
|---|---|---|---|---|---|
| **T1** | `tests/unit/tools/test_question_tool_root_gate.py` (**LANDED** — the gate pack, 23,329 bytes / **14 tests** — 12 after fix round 1, **13** after fix round 2's W3 ordering pin, **14** after fix round 4's T6-gate) | Drive the **REAL** `create_instance_tools` factory — copy the harness at `tests/unit/tools/test_secrets_registration.py:311-322` (`MagicMock` manager + `create_instance_tools(manager, f"inst-{agent}", agent)`); synthetic agent meta whose `tools.allow` **includes** `"question"` so the allow-filter cannot be the cause; call with `parent_id="parent-x"` | `ask_questions` **not** in the built tool names **and** no tool whose category is `question` | **RED — gate proof** | Must be an **absence at registration**, not a runtime refusal. The synthetic meta MUST allow `question` — otherwise the test proves nothing. |
| **T2** | same file | `parent_id=None`, `source_type` ∈ {`discord`,`slack`,`telegram`,`whatsapp`} (parametrize) | `ask_questions` **IS** in the built tool names | **GREEN pre-change — PRESERVATION** (signature-adaptive; `TestPreservationGuardsGreenPreChange:311`) | root shows |
| **T3** | same file | `parent_id=None`, `source_type=None` | **not** present | **RED — gate proof** | a job root is not a user-facing root |
| **T4a** | same file | `parent_id=""` (legacy empty-string root) + `source_type="discord"` | `ask_questions` **IS** present | **GREEN pre-change — PRESERVATION** (signature-adaptive; `TestPreservationGuardsGreenPreChange:311`) | **PRESERVATION half** of the old T4: the legacy empty-string parent idiom (`repository.py:1109`) must keep the tool. **RENAMED in fix round 2 — see the W1 row below.** |
| **W1** (fix round 2) | same file — **the rename** | `test_t4a_restore_path_root_with_stamped_row_has_question_tool` **→** `test_root_with_threaded_chat_source_type_has_question_tool` | the test asserts the **threaded** `source_type` reaches the gate — **not** a DB row stamp | **LANDED (fix round 2)** | **A naming-honesty correction, NOT a coverage loss.** The old name over-claimed: a genuine **restore-path** assertion was **not cheap** — it needs a row + the repositories + the LLM restore lane — and **this pack never touches a repository**, so it cannot cover the restore-path row stamp. The honest-name option was taken per instruction: the name and the docstring now state exactly what is asserted. The behaviour it pins is unchanged. |
| **T4b** | same file — `test_t4b_legacy_empty_parent_and_empty_source_type_has_no_question_tool` (`:258`) | `parent_id=""` **AND** `source_type=""` (the **SPECIFIED** legacy case — re-shaped in fix round 4) | **not** present | **RED — gate proof** | **GATE half** of the old T4: an empty-string parent with no chat source is not a user-facing root (fail-closed). **Re-shaped in fix round 4** to the SPECIFIED legacy case (`parent_id=""` AND `source_type=""` ⇒ no tool) ⇒ it is now the **exact** red witness for mutant **M-i** ("" admitted as a chat source type) — §10.4c.1. |
| **T5** | same file | `parent_id="p"`, `source_type="discord"` | **not** present | **RED — gate proof** | defense: a child that somehow carries a chat source type is not a root (fail-closed) |
| **T6-gate** (fix round 4) | same file — `test_t6_root_with_non_chat_source_type_has_no_question_tool` (`:329`) | a **root** (`parent_id=None`) whose `source_type` is a **REAL non-chat** type — webhook / scheduler (cited from `daemon/models/source.py:27,30` + `daemon/routers/sources.py:155`; negative control `repository.py:919`) | **not** present | **RED — gate proof** | **LANDED in fix round 4** (`235ec6934`). Proves the gate rejects a **real** non-chat `source_type`, not merely an empty string ⇒ it is the **exact** red witness for mutant **M-ii** (any non-chat admitted) — §10.4c.1. **NAME COLLISION**: the pytest name begins `test_t6_`; §11.1's **T6** is the **flow proof** in a different file — the plan's T-IDs stay **T1–T9** and this row is called **T6-gate** to keep them unambiguous (§10.4c.1). |
| **T6** | `tests/unit/services/test_child_report_open_questions_flow.py` (**LANDED** — T6×5 cases) | A child instance completes with a report containing the section "Open questions for the orchestrator"; drive the real path | report row (`type=COMPLETION_REPORT`, `source=internal_report:<child>:<msg_id>`) → `PROCESS_REPORT` task → `ReportInjection` row → drained into the parent's turn as a `HumanMessage` | **LANDED — preservation (flow proof)** | Harness borrowed from `tests/e2e/test_full_chain_turn_reconciler.py` / `tests/job_queue/test_job_wait_for_children.py` |
| **T7** | `tests/unit/services/test_child_report_open_questions_flow.py` (**LANDED** — T7×7 cases, incl. the real tool-routing revive via `tests/helpers/send_message_fixtures.py`; the status-guard EXTEND was superseded by the one-file flow pack) | Parent calls `send_message(child_id, "<answer>")` while the child is `COMPLETED` | routing returns the `enqueue-revive` branch (`daemon/tools/instance.py:1418-1424`) and the child's status flips to `RUNNING` (`instance_messaging.py:2365-2381`); the instruction is delivered into the child's next turn | **LANDED — preservation (flow proof)** | Deviation disclosed: plan spread T7/T8 across other files; landed one-file per the brief constraint |
| **T8** | `tests/unit/services/test_child_report_open_questions_flow.py` (**LANDED** — T8×4 cases: shadow rehydrate, original pack id + CAS, duplicate-answer CAS loser, AST static no-tool-registry proof over answer_helper/question_manager/midflight_qa, real `create_instance_tools` root-without-category holds no `ask_questions`; `job_answer` proven at the shared-helper level) | A legacy pack created **before** the gate (by a now-tool-less instance) is still answerable: RAM pack + durable shadow + rehydration + CAS; a root **without** the tool answers via `job_answer` | `set_answers` CAS returns `(pack, True)` exactly once; shadow cleared at both unwind sites; `rehydrate_from_payloads` restores the original `pack_id`; `job_answer` resolves the pack with no `ask_questions` tool in play | **LANDED — preservation (flow proof)** | Proves P9 + the migration story (§15). `job_answer` **def** at `daemon/tools/job_queue.py:3403`, docstring at `:620`. |
| **T9** | `tests/unit/tools/test_question_tool_root_gate.py` (**LANDED**, same file) | **END-TO-END capability (BLK-1 discriminator):** (a) a **chat-root** instance whose agent **DOES** grant the category (`leader` meta — `"question"` in `tools.allow`) ⇒ (b) the **SAME agent** (`leader`) spawned as a **CHILD** (`parent_id="parent-x"`) | (a) `ask_questions` **IS** registered (gate admits **AND** allow admits) · (b) `ask_questions` is **NOT** registered | **RED — gate proof** | **The discriminating test for the two-layer model.** T2 alone only proves the gate **admits**; T9 proves gate-admits **AND** allow-admits ⇒ effective capability = {user-facing root} ∩ {agent grants the category}. Without T9, a regression that stripped `"question"` from leader's allow-list would still pass T1–T8. |
| **W3** (fix round 2) | same file — `TestGateBeforeFilterOrdering::test_gate_registration_precedes_tool_filter_in_build_path` (`:624` / `:627`) | **AST-parse** of `daemon/tools/instance.py`, scoped to `create_instance_tools`; builds an **inverted** copy of the source **in memory** as a self-check | `create_question_tools(` **PRECEDES** `_apply_tool_filter(` in the build path — so an agent's `tools.allow` entry can **never** re-admit a gated tool for a non-root (the gate runs **first**) | **LANDED (fix round 2) — gate pack 12 → 13** (→ **14** after fix round 4's T6-gate) | **Fails on reorder** — proven against a real inverted copy in `/tmp` **and** against the in-test in-memory mutant self-check (the check asserts it fails on the inverted source). **No hardcoded line numbers** ⇒ the pin survives anchor drift. |
| **Mappings-lane proof (A2)** | `tests/unit/tools/test_question_tool_root_gate.py` — **AC6** (`:477-522`), commit `925f9aced` | `daemon/routers/mappings.py` spawns a CHAT-BOUND ROOT; the lane hands the source's chat platform type to the SAME single writer (§3) | a mappings-lane root resolves `ask_questions` | **LANDED** | The §3 fix has a registry row — the mappings-lane test EXISTS; the old grep-substitute DoD is **OBSOLETE** (§11.2). |

### 11.2 Existing tests that may break — and the required action

| File:line | Why | Required action |
|---|---|---|
| `tests/unit/tools/test_frozen_tool_name_discovery.py:223` | `KNOWN_TOOL_NAMES` must equal the source-discovered set **exactly** | **NO EDIT** — the tool source `daemon/tools/question_tools.py` stays (only its registration is gated) ⇒ source-discovery is unchanged ⇒ pin stays green. Re-run to confirm. Breaks **only** if the question tool source is deleted/renamed (forbidden — §2.5). |
| `tests/test_innate_skills_refactoring.py:67` | expects `leader` `innate_skills == ["coordination","chart","todo"]` | **NO EDIT** — currently RED (leader still lists `question`), turns **GREEN** after P4 (RULING-1: the list becomes exactly `["coordination","chart","todo"]`). Re-run to confirm. |
| `tests/unit/test_devops_agent.py:165` | expects `devops` `innate_skills == ["dynamic-skill","todo"]` | **MUST EDIT** → `["dynamic-skill","chart","todo"]` (RULING-1: the list becomes exactly that). Currently RED (devops has `chart`+`question`); after P4 it stays RED unless the expectation is updated (`chart` stays — dropping it is out of scope). |
| `tests/test_chart_image_delivery_e2e.py:1418` (tripwire) | SHA-pins **35** files; **18** of them are now edited (14 sweep + 3 fix-round-1 + `agents/charter/rule.md` added in fix round 4 — see §11.3) | **ADJUDICATED SEAL RE-PIN** per edited file (**36 constants-only update OPERATIONS** landed over **18** distinct files — devops re-pinned twice; leader/rule.md, maintenancer/rule.md, project-manager/workflow.md re-pinned again in fix round 2; **10** re-re-pins in the contract-completion round `a9c041ab0`, all of files already inside the 17): git-log last-touch attribution → **constants-only** update of `SEALED_SHA_BASELINES` → targeted test run → provenance-labelled commit. Do **not** edit the sealed prompt files' semantics beyond the contract. |
| `tests/unit/test_report_integrity_prompts.py` | `E_REQUIRED_SNIPPETS` (`"before ending any turn"`, `"zero tool calls"`) must survive in the canonical homes; `D_REQUIRED_SNIPPETS` (`"[REPORT SANITY:"`, `"interim, not completion"`, `"send_message"`) in `D_HOME_FILES` (leader→`workflow.md`, project-manager→`rule.md`+`workflow.md`, governor→`rule.md`, …) | **NO EDIT expected** — but the clause-family rewrite MUST preserve **both** families verbatim in each affected canonical home: the **E** snippets `"before ending any turn"` + `"zero tool calls"` **and** the **D** snippets `"[REPORT SANITY:"` + `"interim, not completion"` + `"send_message"` (N14 — the D snippets were previously named only in the *why* column; the per-file verify clause now names them explicitly). Verify per file, per agent, per family. |
| `tests/unit/tools/test_prompt_section_reference_integrity.py` | closure sweep: no bare `.md` / bare `agents/` cross-reference tokens; no empty/glued captures | **NO EDIT** — the sweep's cross-references must be `See Open questions for the orchestrator` / `See <agent>'s Open questions for the orchestrator` (section names only). |
| `tests/test_prompt_artifact_baseline.py` | `agents/.prompt-artifacts.sha256` pins the depth-2 prompt artifacts | **REGENERATE** after the sweep: `python3 scripts/protection/generate_prompt_artifact_baseline.py` (writes the baseline; `--check --tree <dir>` verifies). Depth-2 only ⇒ `agents/<agent>/<file>.md` are covered; `agents/_prompt_system/**` and `agents/<agent>/skills-template/**` (depth 3) are **not**. |
| `tests/test_tool_filter.py:34` | frozen category→tool map contains `"question": ["ask_questions"]` | **NO EDIT** — the tool still exists in source. |
| `tests/unit/test_ari_agent.py`, `tests/unit/test_ari_worker_integration.py` | `test_ari_agent.py` asserts membership only ⇒ **NO EDIT**. **`tests/unit/test_ari_worker_integration.py:345` asserts an EXACT SET on ari's `innate_skills`** — the plan's original "membership-only / NO EDIT" claim was a **plan error** | **MUST EDIT (B6 — LANDED, leader-ratified #1):** `:345` updated constants-only to `{"job-orchestration","chart","todo","question"}`. The grant legitimately breaks an exact-set assert ⇒ the constants-only edit is required and correct. |
| any test asserting `ask_questions` for leader/devops | — | **SUPERSEDED (A2)** — the gate pack (`tests/unit/tools/test_question_tool_root_gate.py`, **13 tests** incl. the AC6 mappings-lane proof + the W3 ordering pin) is the registry; this grep was the pre-test substitute and is **OBSOLETE as a DoD**. Kept only as an optional Phase-8 hygiene re-run (read-only census: hits are in `tests/e2e/test_*`, `tests/test_question_tools*.py`, `tests/job_queue/test_midflight_qa.py`, `tests/unit/test_*pause*/answer*`, `tests/test_tool_filter.py` — none assert leader/devops toolset membership). |
| `tools/dev/verify_service_default_open.py:87` (the `create_instance_tools` call; `tool_count` written at `:102`) | The script records `len(by_name)` as `tool_count`. After the gate the built toolset is **one tool smaller** for the agents it exercises ⇒ `tool_count` shifts by **−1**. **No assertion depends on it** — it is a reported number, not a pinned constant. | **NO EDIT** — but **record the −1 shift** in the Phase 6 notes so a reviewer does not read it as an unexplained regression. (N1) |
| *(N1)* the whole test corpus | **NO test asserts `ask_questions` membership in a built toolset today** — which is exactly why the new T1–T5/T9 must be **genuinely discriminating** (a synthetic meta that ALLOWS `"question"` in the `tools.allow` category), and why T9 is needed to cover the allow-list half. | **NO EDIT** — record as the rationale for the T1/T9 "the synthetic meta MUST allow `question`" requirement. |
| `tests/unit/tools/test_attestation_prompt_contract.py:48-49` — **semantic pin (BLK-3a)**: `agents/leader/rule.md` **AND** `agents/leader/workflow.md` must **KEEP** `"Wrong Attestation Order"`, `"PURE TOOLCALL TURN"`, `"c5d9a38a shape"`, `"report-first-then-attest"`, `"attest-first, report-second"`, `"subsequent standalone AI message"`, `"non-delegating turns"` and must **NOT** gain `"CONDITIONAL on delegation"`, `"Deliver the full detailed final report"`, `"as its own message FIRST"`, `"call \`attest_completion\` ALONE"` | The SHA seal does **not** cover the attestation **contract strings** — only the file bytes. The leader's two files are both edited by the sweep (leader/rule.md §10.4 + §10.5, leader/workflow.md §10.4 + §10.5) ⇒ the semantic contract must survive even though the SHA changes. | **NO EDIT expected — verify.** Do **not** add the forbidden fragments; do **not** drop the required ones. Re-run the file after the leader sweep. |
| `tests/unit/tools/test_mission_watch_prompts.py` — **semantic pin (BLK-3b)**: `agents/jober/rule.md` + `agents/jober/workflow.md` (and the rest of the 9-file mission-watch set) keep the `watch_mission` vocabulary and **ZERO `watch_job` tokens** | The SHA seal does **not** cover this vocabulary. `agents/jober/rule.md` and `agents/jober/workflow.md` are edited by the sweep (§10.4). | **NO EDIT expected — verify.** The sweep must not reintroduce `watch_job`/`watch_jobs` (word-boundary match) nor drop the decision-rule text. |
| `tests/test_chart_image_delivery_audit.py:58,467-480` — **semantic pin (BLK-3c)**: the literal **"Chat Delivery"** must survive verbatim in **EVERY** chart-capable file the sweep edits (**20** agents, `CHART_CAPABLE_AGENTS`) | The SHA seal covers file bytes but **not** this literal per agent; `agents/governor/rule.md` (the new 14th re-pin) carries **1 × "Chat Delivery"**, and the sweep edits several other chart-capable files. | **NO EDIT expected — verify.** Pin #17 (`test_pin_17_chat_delivery_references`) is parametrized over the 20 chart-capable agents; every edited one must still match. |

### 11.3 SEALED-SHA overlap (verified) — **18** files trip the tripwire (fix rounds 2-4 included)

The commission's brief names 5 sealed files; **reading `tests/test_chart_image_delivery_e2e.py` shows 35 sealed files, and 18 of them are edited** (14 sweep incl. `agents/governor/rule.md` + 3 fix-round: `agents/charter/workflow.md` (B5), `agents/devops/rule.md` (B3), `agents/maintenancer/rule.md` (W3) + **1 NEW distinct file in fix round 4: `agents/charter/rule.md`** — the canonical home added by the UNAUDITED-DIRS round `9f8b49306`). Seal-pin line numbers SHIFTED with the fix-round attribution comment block (e.g. governor/rule.md `:1387`→`:1392`, leader/rule.md `:1388`→`:1393`, devops/rule.md now `:1390`, charter/workflow.md `:1357`) — **the SHA constants are the identity; grep, don't trust line numbers**:

| # | Sealed file | Sweep row |
|---|---|---|
| 1 | `agents/approver/rule.md` (`:1430`) | §10.5 short |
| 2 | `agents/approver[v2]/rule.md` (`:1431`) | §10.5 short |
| 3 | `agents/architect/rule.md` (`:1432`) | §10.5 short |
| 4 | `agents/coder/soul.md` (`:1434`) | §10.4 + §10.5 |
| 5 | `agents/developer/rule.md` (`:1435`) | §10.5 short |
| 6 | `agents/doc-writer/rule.md` (`:1438`) | §10.4 |
| 7 | `agents/leader/rule.md` (`:1388` → now `:1440`) — **RE-PINNED in fix round 2** | §10.4 + §10.4a |
| 8 | `agents/planner/rule.md` (`:1449`) | §10.5 short |
| 9 | `agents/project-manager/workflow.md` (`:1392` → now `:1451`) — **RE-PINNED in fix round 2** | §10.5 long + §10.4a |
| 10 | `agents/reviewer/rule.md` (`:1452`) | §10.5 short |
| 11 | `agents/reviewer[v2]/rule.md` (`:1453`) | §10.5 short |
| 12 | `agents/tidier/rule.md` (`:1454`) | §10.5 short |
| 13 | `agents/tidier[v2]/rule.md` (`:1455`) | §10.5 short |
| **14** | **`agents/governor/rule.md` (now `:1439`)** — sha `07aaed75b0290d2d857ede82f876b6d55979d2f67ba29b861781531f815b41f2` (re-pinned in fix round 3), carries **1 × "Chat Delivery"** | §10.5 short (`:82`) — the OPENING-DISCIPLINE clause; also the governor's D_HOME **and** E_HOME ⇒ the D **and** E snippets must survive |
| **15** | **`agents/charter/workflow.md`** — current sha **`2a5078f9…`** (`tests/test_chart_image_delivery_e2e.py:1404`) | §10.7 flip (B5, `6711c769e`) — re-pin `78469d671`, attribution `<-66512e9dd` |
| **16** | **`agents/devops/rule.md`** — current sha **`100d2859…`** (`:1437`) | §10.4 (B3, `f332e83e0` + residual `c5bd90146`) — **RE-PINNED TWICE** (`78469d671` with `617bf69a0` attribution, superseded by `6fa226976`) |
| **17** | **`agents/maintenancer/rule.md`** — current sha **`abe43bae715c62a341788fbe5d610138936b7b4e83df87c8bbfd3611bf446aec`** (`:1448`) | §10.6 (W3, `6711c769e`) — re-pin `78469d671`, attribution `<-74e9168a2`; **RE-PINNED AGAIN in fix round 2** (`4088be633`, attribution "B3 closure"); **RE-PINNED AGAIN in fix round 3** (`23859484e`, parent-side paragraph added) and **REVERTED in fix round 4** (`30ea77149`, the paragraph removed) — the constant is **back to its pre-B3 value** and the file is **byte-identical to base `a9aa911a3`** ⇒ **the self-consistency proof** (§10.4c.4) |
| **18** | **`agents/charter/rule.md`** — current sha **`2cb6e01b3bdfd07865aed967d524b98d7bcd965f60ffd741e52a7ee71c44573c`** (`:1405`) | **NEW distinct sealed file** — fix round 4 (`9f8b49306`): the canonical home ADDED; its NEEDS MORE INFO is a **pre-work abort**, not a report-time question (§10.4c.2) |

**All 35 constants re-verified from real bytes post-fix-round (35/35 GREEN; no pattern-fill — the pre-edit mismatch count was exactly the edit targets).** Re-pin arithmetic on branch: **14** (`b05ac37d4`, sweep) + **3** (`78469d671`: charter, devops, maintenancer) + **1** (`6fa226976`: devops residual) + **3** (`4088be633`, **fix round 2**: leader/rule.md, maintenancer/rule.md, project-manager/workflow.md) + **10** (`a9c041ab0`, **contract-completion round**: approver/rule.md, approver[v2]/rule.md, architect/rule.md, developer/rule.md, devops/rule.md, planner/rule.md, reviewer/rule.md, reviewer[v2]/rule.md, tidier/rule.md, tidier[v2]/rule.md) + **1** (`9f8b49306`, **fix round 4 — UNAUDITED-DIRS round**: `agents/charter/rule.md` — a **NEW distinct** sealed file) + **3** (`23859484e`, **fix round 4 — PARENT-SIDE round**: coder/soul.md, governor/rule.md, maintenancer/rule.md) + **1** (`30ea77149`, **fix round 4 — maintenancer revert**: maintenancer/rule.md back to its **pre-B3** value) = **36 constant-update OPERATIONS** across **18 distinct files** — the **10** round-3 updates and the **3** round-4 parent-side updates are **re-re-pins of files already inside the set**; only `agents/charter/rule.md` raises the distinct count (17 → **18**) ⇒ the baseline split is **18 re-pinned / 17 safe = 35** ✓. **9 seal-file commits total, 8 ops-bearing** — the tidy pass `3ab411557` is **M7 PROVENANCE CORRECTIONS** (attribution comments only), **not** new constant operations. There were **no** pre-existing reds to absorb. **Current constants (re-verified from real bytes at `30ea77149`):** charter/workflow.md `2a5078f9…` (`:1404`) · **charter/rule.md `2cb6e01b…` (`:1405` — NEW distinct)** · approver/rule.md `c09bfb29…` (`:1430`) · approver[v2]/rule.md `c09bfb29…` (`:1431`) · architect/rule.md `ce968961…` (`:1432`) · **coder/soul.md `6263b648…` (`:1434`)** · developer/rule.md `9b06c29c…` (`:1435`) · **devops/rule.md `100d2859…` (`:1437`)** · doc-writer/rule.md `350dd33c…` (`:1438`) · **governor/rule.md `07aaed75…` (`:1439`)** · leader/rule.md `f0bda673…` (`:1440`) · **maintenancer/rule.md `abe43bae…` (`:1448` — byte-identical to base `a9aa911a3`)** · planner/rule.md `4282ce61…` (`:1449`) · project-manager/workflow.md `bde47909…` (`:1451`) · reviewer/rule.md `77c75b6d…` (`:1452`) · reviewer[v2]/rule.md `77c75b6d…` (`:1453`) · tidier/rule.md `73624de0…` (`:1454`) · tidier[v2]/rule.md `73624de0…` (`:1455`).

**M7 — seal PROVENANCE CORRECTIONS (tidy pass `3ab411557`; attribution comments only, the 35/35 constant VALUES are byte-identical).** The fix-round-1 attributions were re-pointed at the **in-range** commit whose edit made each pin stale — charter/workflow.md `66512e9dd` → `6711c769e`, devops/rule.md `617bf69a0` → `c5bd90146`, maintenancer/rule.md `74e9168a2` → `6711c769e` (all three git last-touch **verified**); the `6fa226976` residual re-pin gained its missing in-file attribution line; the fix-round-2 rows keep their content-sha256 values, now **explicitly labelled as content-sha256**, plus the in-range commit `4088be633`.

**Brief correction — where the constant table lives.** `SEALED_SHA_BASELINES` is defined in the **e2e** file `tests/test_chart_image_delivery_e2e.py` (**`:1382`**) — **not** in the audit file. The audit file (`tests/test_chart_image_delivery_audit.py:167`) only *references* it in a provenance comment.

**Sealed files NOT in the sweep (safe, 17):** `agents/ari/rule.md`, `agents/ari/workflow.md`, `agents/charter/soul.md` + `agents/charter/meta.json` + 2 charter install-skill files (4 of charter's 6 — `charter/workflow.md` LEFT the safe set in fix round 1, **`charter/rule.md` LEFT the safe set in fix round 4**), `agents/developer[v2]/tools_note.md`, `agents/planner[v2]/tools_note.md`, `agents/wanderer/soul.md`, `agents/_prompt_system/innate-skills/chart/skill.md`, `daemon/constants.py`, `daemon/sources/*` (**6** daemon files — `base.py`, `registry.py`, `dispatcher.py`, `adapters/discord/adapter.py`, `adapters/slack/adapter.py`, `adapters/telegram.py`; **N3**: the plan previously said 5).

| Split of the 35-file sealed baseline | Count |
|---|---|
| Edited ⇒ **re-pin** | **18** (14 sweep + 3 fix-round-1: charter/workflow.md, devops/rule.md, maintenancer/rule.md + **1 NEW distinct file in fix round 4: `agents/charter/rule.md`**) — **36 constant-update OPERATIONS** over **18** distinct files (devops ×2; leader/rule.md, maintenancer/rule.md, project-manager/workflow.md re-pinned again in fix round 2; **10** re-re-pins in the contract-completion round `a9c041ab0`, all of files already inside the set; **+1 op** `9f8b49306` charter/rule.md — the NEW distinct ⇒ 18; **+3 ops** `23859484e` coder/soul.md, governor/rule.md, maintenancer/rule.md; **+1 op** `30ea77149` maintenancer **REVERTED to its pre-B3 value**) |
| Not edited ⇒ safe | **17** |
| **Total** | **35** ✓ (18 + 17) |

### 11.4 Exact commands

Canonical invocation (per `.agents/tester/PACKS.md` house idiom; `pytest` config in `pyproject.toml:86-97` — `testpaths=["tests"]`, `addopts="-m 'not integration and not postgres and not slow'"`, `asyncio_mode=auto`, `timeout=30`):

```bash
# C3: use the WORKTREE's own venv/environment — the main checkout's .venv resolves to `latest`, NEVER use it; this worktree has its own .venv
cd /home/nea/ensemble-src-wt-hidenonroot
FENCE="env -u POSTGRES_DB -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_USER -u POSTGRES_PASSWORD -u DATABASE_URL"

# ── LANDED tests (the REAL files — C3) ──────────────────────────────
# gate pack 14 tests (12 + the W3 ordering pin + the T6-gate non-chat source_type case; incl. the AC6 mappings-lane proof)
# + flow proofs 16 cases + next-turn execution 3 + job_answer wrapper
timeout 300 $FENCE .venv/bin/pytest \
  tests/unit/tools/test_question_tool_root_gate.py \
  tests/unit/services/test_child_report_open_questions_flow.py \
  tests/unit/services/test_child_report_next_turn_execution.py \
  tests/job_queue/test_job_answer_tool.py \
  -q --tb=short -p no:randomly

# ── T7/T8 neighbour regression files (the flows themselves LANDED in the flow pack) ──
timeout 300 $FENCE .venv/bin/pytest \
  tests/tools/test_send_message_status_guard.py \
  tests/test_question_manager.py \
  tests/test_question_pack_boot_rehydrate.py \
  tests/job_queue/test_midflight_qa.py \
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

### 11.5 BATTERIES + GATE-PACK MUTATION PROOF (fix round 2 + the AMENDMENT #5 reconciliation)

**Battery table** (fix round 2 — worktree venv, env-scrubbed, **no whole-suite runs**):

| Battery | Result | Note |
|---|---|---|
| `tests/unit/tools/test_question_tool_root_gate.py` | **14 passed** | the gate pack — 12 + the **W3** ordering pin + the **T6-gate** non-chat `source_type` case |
| `tests/unit/tools/test_prompt_section_reference_integrity.py` | **1462 passed / 5 failed** | the 5 failures are the **known pre-existing** closure-sweep set |
| `tests/unit/test_report_integrity_prompts.py` | **51 passed / 20 skipped** | the D/E snippet + cross-reference pins |
| `tests/unit/test_devops_agent.py` | **61 passed / 2 failed** | the 2 failures are the **known pre-existing** `meta.json` assertions |
| `tests/test_chart_image_delivery_audit.py` | **46 passed** | the "Chat Delivery" semantic pin |
| `tests/test_chart_image_delivery_e2e.py` | **24 passed / 4 deselected** | the seal tripwire |
| `tests/unit/services/test_child_report_open_questions_flow.py` | **16 passed** | the flow proofs (T6×5 / T7×7 / T8×4) |
| `tests/unit/services/test_child_report_next_turn_execution.py` | **3 passed** | the next-turn execution proof (tester commit `b7e3a0808`) |
| `tests/test_prompt_artifact_baseline.py` | **19 passed** | the baseline self-test |
| `tests/job_queue/test_job_answer_tool.py` | **1 failed** (the pre-existing order pin) | **FINDING-2 — DISCLOSED.** The order assertion at `tests/job_queue/test_job_answer_tool.py:945` (`class TestJobAnswerRegistration`, `:913`) expects `job_answer` to be the **LAST** entry of `create_job_tools()` but gets **`job_resume`**. **PROVEN PRE-WINDOW by TWO independent methods**: (1) a **parent-version replay** of the assertion, and (2) **last-touch attribution** (`829feb94d` / `5b194ef7c` — both outside the commission window). **DISCLOSURE:** the tester's commit `b7e3a0808` edited this file **without disclosing** the red ⇒ the failure was invisible in the round-2 battery summary. |
| **TOTAL FAILURES** | **exactly the 11 known pre-existing (5 closure-sweep + 2 devops `meta.json` + 3 gaia `meta.json` + 1 `job_answer` order pin) — ZERO new** | every failing file is untouched by the branch's shipped-code changes ⇒ no closure-sweep fix was needed. See the inventory reconciliation + the FINDING-2 disclosure below. |

**KNOWN-PRE-EXISTING FAILURE INVENTORY — FINAL (11).** Reconciled after the tidy pass + contract-completion round. **ALL are the stale-expectation class and NONE is caused by this branch**:

| # | Class | Count | Proven pre-window by | Anchor |
|---|---|---|---|---|
| 1 | closure sweep | **5** | the swept files are untouched by the branch's shipped-code changes | `tests/unit/tools/test_prompt_section_reference_integrity.py` — 1462P/5F |
| 2 | devops `meta.json` | **2** | the devops `meta.json` expectations are stale relative to the grant | `tests/unit/test_devops_agent.py` — 61P/2F |
| 3 | gaia `meta.json` | **3** | **empty `git log` in BOTH ranges** — `agents/gaia/meta.json` was last touched by `3e46576dc` (service-tool per-agent meta-grants), well outside the window | `tests/unit/test_gaia_agent.py` — stale expectations |
| 4 | `job_answer` order pin | **1** | **TWO independent methods** — a **parent-version replay** + **last-touch attribution** (`829feb94d` / `5b194ef7c`) | `tests/job_queue/test_job_answer_tool.py:945` — **FINDING-2**, undisclosed by `b7e3a0808` |
| | **TOTAL** | **11** | | **ALL stale-expectation class — ZERO caused by this branch** |

**Baseline regeneration (fix round 2).** `scripts/protection/generate_prompt_artifact_baseline.py` ⇒ **161 artifacts**; `--check` **0 diffs**; `sha256sum -c` **161/161 OK**; drift set = **22** changed entries, 0 added, 0 removed — every entry a **strict subset** of the 26 files edited this round, **no foreign file**. (The 4 edited files absent from the baseline — `coordination/skill.md` + the three `skills-template/*.md` — sit outside the depth-2 versioned-prompt-artifact set.)

**GATE-PACK MUTATION PROOF (read-only; scratch worktree, removed afterwards) — the evidence that the pins are NON-VACUOUS.**

| Mutant | Mutation | Result |
|---|---|---|
| **M1** | **neuter the GATE** — `if _is_user_facing_root(...)` → `if True` | **5 REDS** |
| **M2** | **neuter only the chat-source half** of the predicate | **2 REDS** — T1/T5 stay green ⇒ the **two predicate halves are independently pinned** |
| **M3** | **drop the section in transit** | **2 REDS** |
| **M3b** | **drop the `MessageQueue` carrier** | **1 RED** |
| — | **UNDETECTED** | **NONE** |
| — | **orthogonality** | M1/M2 leave the flow file **16/16 green**; M3/M3b leave the gate file **13/13 green** |
| **M-i** (fix round 4) | **"" admitted as a chat source type** | **1 RED — EXACTLY** `test_t4b_legacy_empty_parent_and_empty_source_type_has_no_question_tool` · **zero collateral** · **restore-green** |
| **M-ii** (fix round 4) | **any non-chat source type admitted** | **1 RED — EXACTLY** `test_t6_root_with_non_chat_source_type_has_no_question_tool` · **zero collateral** · **restore-green** |

**Proof set (BLK-7) — name it in every report.** The suite contains two different kinds of evidence and they must never be conflated:

| Proof set | Tests | Pre-change state | What it proves |
|---|---|---|---|
| **GATE PROOF** (RED pre-change) | **T1, T3, T4b, T5, T6-gate, T9** | **RED** pre-change (the gate is absent ⇒ the tool is registered ⇒ the absence assertion fails) | the gate actually hides the tool from non-roots, and the two-layer capability model holds |
| **PRESERVATION — signature-adaptive** (GREEN pre-change) | **T2, T4a** | **GREEN pre-change** — the pre-change build registers the question tool **unconditionally**, so *"a legitimate root keeps the tool"* already held. Landed class **`TestPreservationGuardsGreenPreChange`** (`:311`); rationale docstring `:52-71` (*"always passing the new kwargs would raise `TypeError` pre-change, failing the preservation guards for a reason that has nothing to do with the behaviour they protect"*). The **RED** witness for the new kwargs' absence is the **AC3 keyword-only signature pin** (`:69`, `:466`). | roots still get the tool; the legacy `""`-parent idiom still works |
| **FLOW PROOFS — LANDED** | **T6, T7, T8** | green on the branch (`12291f2a4`; 16 cases, mutation-verified 5/5) | the report→continuation flow works; legacy packs stay answerable |

⇒ A **green preservation run is NEVER reported as gate proof** (the preservation set is green pre-change **by design**), and a **green gate-proof set** is a defect (the gate did not bite). Report both sets separately, by name — and report the preservation set's **RED witness** (the AC3 keyword-only signature pin) as part of the gate-proof evidence.


### 11.6 TIDY PASS (AMENDMENT #5) — diagnostics, fail-closed guard, seal provenance — **LANDED** `3ab411557`

The tidy pass (`3ab411557`, **7 files**: `daemon/tools/instance.py` · `daemon/services/instance_lifecycle.py` · `daemon/routers/mappings.py` · `tests/unit/tools/test_question_tool_root_gate.py` · `tests/test_chart_image_delivery_e2e.py` · `tests/job_queue/test_job_answer_tool.py` · `tests/unit/services/test_child_report_next_turn_execution.py`) — a **comment / diagnostics / hygiene** pass; **no behaviour change to the gate predicate**.

| ID | Change | Detail |
|---|---|---|
| **M1** | gate diagnostics log | ONE `logger.debug` on the fail-closed hide shape (the parent leg passes, `source_type` is not a chat type) — §2.2. **The exact failure mode this commission repaired twice is now VISIBLE on recurrence** instead of silent. |
| **L13** | fail-closed `isinstance` guard | a non-`str` `source_type` now fails **closed** instead of raising `TypeError` at the frozenset membership check — §2.2. |
| **M7** | seal provenance corrections | the 3 attribution shas are **git-log-verified**; `6fa226976` is attributed; the round-2 rows are labelled **content-sha256**; the 35/35 constant VALUES are byte-identical — §11.3. **NOT** new constant operations. |
| M2 / M3 / M4 | comment debt | the definition-site taxonomy rationale reduced to a pointer at the import block (the full rationale stays at the import); the mappings spawn comment cut to 3 lines with the cross-module single-writer claim moved to the writer site; the spawn/restore rationale blocks share one sentence (a missing `#` separator added so the new rationale does not merge into the C1-fix paragraph). |
| L4 / L5 / L6 / L10 | hygiene | unused `import uuid` / `from pathlib import Path` removed; function-local json/Path imports hoisted to the module block; the redundant outer registration-before-filter assert dropped (the W3 pin's inner assert is untouched); `create_instance_tools` annotated `-> list[BaseTool]` (runtime-verified: 75/75 elements are `BaseTool` instances). |

**Deferred set — recorded as follow-ups (NOT part of this branch's scope).**

| Deferred item | Follow-up owner | Note |
|---|---|---|
| **M5 / M6** | tidier | not taken in this pass |
| **L1–L3 / L7–L9 / L11 / L12** | tidier | not taken in this pass |
| pre-existing unused imports | tidier | repository-wide hygiene, outside the commission's file set |
| **`agents/charter/workflow.md:589` sealed fence defect** | **seal-set owner** | a fence defect inside a **sealed** file — fixing it would require another adjudicated re-pin ⇒ routed to the seal-set owner, **not self-served here** |

---

## 12. Sequencing & branch/worktree discipline (P10)

### 12.1 The gate — the branch carries the sibling; `latest` has moved (BLK-6) — **state re-verified 2026-10-20**

| Fact | Verified evidence at `30ea77149` (this branch), re-verified 2026-10-20 |
|---|---|
| Sibling branch | `fix/question-pause-resume-asymmetry`, commit **`e2899f8bd`** (11 files: `daemon/api.py`, `config.py`, `repositories/instance/repository.py`, `routers/answer_helper.py`, `routers/instances.py`, `services/instance_lifecycle.py`, `services/pool_orchestrator.py`, + new `services/question_pause_set.py`, `services/stale_pause_sweep.py`, + 2 test files) |
| Sibling worktree | `/home/nea/ensemble-src-wt-qprasym` — **MUST NOT be touched** |
| **Already merged INTO THIS BRANCH** | ✅ merge commit **`0e7500a1b`** (the branch's merge point; **an ancestor of the current tip `30ea77149`**) contains `e2899f8bd` |
| **In `latest` now — CORRECTED 2026-10-20** | ✅ `git merge-base --is-ancestor e2899f8bd latest` → **TRUE** (re-verified). The sibling's work **did** reach `latest` (its branch tip `e73c5c73f` is an ancestor of `latest`; `daemon/services/question_pause_set.py` and `daemon/services/stale_pause_sweep.py` are PRESENT in `origin/latest`). **The earlier ❌/false claim is STALE** — it was true when written and is superseded by this dated re-verification. What remains is the **other** direction: `latest` is **NOT** yet in this branch (§12.4) |
| Their scope (CORRECTED — KV `overlap_CORRECTED`) | `answer_helper.py` (+139/−41), `instance_lifecycle.py` (insert-only), `api.py`, `config.py`, `repositories/instance/repository.py`, `routers/instances.py`, `pool_orchestrator.py` + NEW `question_pause_set.py`, `stale_pause_sweep.py` — **`question_manager` NOT touched; `instance_messaging.py` NO LONGER in their diff** |
| **Overlap with us** | `daemon/services/instance_lifecycle.py` ONLY — hunks confined to `pause_instance_cascade`, >1300 lines from our call sites; on THEIR branch the restore call shifts to **~`:4759`** and `CHAT_SOURCE_TYPES` def shifts `repository.py:919`→`:1000` ⇒ **RE-GREP after the merge** (§12.4 step 3), never reuse pre-merge line numbers |
| **Disjoint (verified on their tip)** | `daemon/tools/instance.py:5895` (the gate), `_tool_registry`, `question_manager`, `instance_messaging`, `mappings.py`, **ALL `agents/**`** (zero prompt-surface collision) — no file-level conflict |
| **Sibling facts — fix-round update (C1), DATED** | their branch was **7 commits ahead, tip `86fe0e03a`, 13 files** **as of the fix-round-2 reading**; the **current** dated reading is in **§12.4** — sibling numbers move while this plan sits still, so **never quote them without a timestamp**. **`latest` has ALSO moved — re-verified 2026-10-20: `latest` = `origin/latest` = `e6eb13fcc`** (v0.26.1 tip, TIP-VERIFIED) ⇒ origin is **NOT** behind any more, and the **§13 push step must still be executed** (the tip-identity guard requires `HEAD == refs/remotes/origin/latest` **at stage time** — a later push by another lane re-opens the gap) |
| **Anchor drift caused by the merge** | restore call `:4618` → **`:4690`**; `source_type` row read `:4595-4597` → **`:4667-4668`**; `meta.parent_id` `:4603` → **`:4675`**. Spawn `:2137`, `source_type` writer `:2390`, `append_platform_context` `:1039`, `_PLATFORM_INSTRUCTIONS` `:1006`, and `instance.py:5895` / `:6238` / `:2450` / `:163` are **unchanged**. |

⇒ **Phase 0 is no longer a wait.** The sibling's work is in this branch **and** in `latest`; what remains is the **other** direction — **`git merge origin/latest` INTO this branch** (§12.4, the 8-step ENDGAME RULING). Re-verify every anchor in §1–§3 against the post-merge tree before implementing — the table above is the verified reality at `30ea77149`, re-verified 2026-10-20.


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

### 12.4 BASE-SYNC RE-ENTRY — **ENDGAME RULING** (C2 — explicit numbered operation)

**Sibling / origin facts — DATED (never state sibling numbers as current without a timestamp).**

> **As of 2026-10-20 (re-verified read-only at this branch's tip `30ea77149`):** the sibling lane `fix/question-pause-resume-asymmetry` **DID reach `latest`** — its branch tip `e73c5c73f` is an **ancestor of `latest`**, and `daemon/services/question_pause_set.py` + `daemon/services/stale_pause_sweep.py` are **PRESENT** in `origin/latest`. The commit `e2899f8bd` is an ancestor of **both** `latest` and this branch. **The earlier "the sibling never merged" framing is superseded by this dated re-verification** — what was true at authoring time is not true now, and this plan says so rather than repeating a stale claim. **The gap is the OTHER direction:** `origin/latest` is **NOT** yet in this branch (the sibling tip `e73c5c73f` is **not** an ancestor of `30ea77149`).
>
> **`origin/latest` = local `latest` = `e6eb13fcc`** (v0.26.1 tip, TIP-VERIFIED) as of 2026-10-20. This **supersedes** the earlier `14a7f3762` (v0.26.0) figure recorded in earlier amendments — `14a7f3762` exists in that history as the seal re-pin commit. **⇒ the version bump must target the NEXT FREE VERSION** (§13 step 2).

**STANDING RULE — never state sibling numbers as current without a timestamp.** A sibling branch keeps moving while this plan sits still ⇒ every sibling figure here (commit counts, file counts, tips, the `latest`/`origin/latest` positions) is a **dated observation, not a standing fact**. Quote it with its timestamp, or re-measure it before relying on it. **An un-dated sibling number in a report is a defect.**

**The 8-step ENDGAME (the ruling):**

1. **`git fetch` then fast-forward local `latest`** — local `latest` is already at `e6eb13fcc`; **verify at execution** (it may have moved again) and `git checkout latest && git merge --ff-only origin/latest`.
2. **`git merge origin/latest` INTO THIS BRANCH** — **never rebase** (preserve the `8fa3319f7` planning-artifact provenance; §12.2). The merge brings the sibling's remainder (its tip `e73c5c73f` is not yet in this branch) **and** the ari-tools lane.
3. **RECONCILE THE TWO SEAL FILES + THE VERSION PIN.** (a) `tests/test_chart_image_delivery_e2e.py` — the incoming `origin/latest` carries **1 adjudicated re-pin** (`agents/ari/workflow.md` `4ffa5bee…` → `f31f5bc4…`; verified: this branch still holds the **old** file bytes `4ffa5bee…`, matching the **old** constant, while `origin/latest` holds the **new** bytes `f31f5bc4…`) against **our 36 constant-update OPERATIONS** — **recompute every constant from the real file bytes**; never trust a sidecar or a stale value. (b) `tests/test_api.py` — the incoming tree asserts the **v0.26.1** version (`:227` `assert data["version"] == "0.26.1"`), which **collides with our version bump** — the bump must update it in the same commit (the canonical 4-file bump set: `pyproject.toml`, `daemon/__init__.py`, `tests/test_api.py`, `uv.lock`).
4. **RE-GREP every anchor after the merge** — the sibling shifts `CHAT_SOURCE_TYPES` (`daemon/repositories/instance/repository.py` def `:919`→`:1000`) and the restore call (→ ~`:4759`); do not reuse pre-merge line numbers anywhere in §1–§3.
5. **Re-run the targeted suites** — **gate pack 14** (`tests/unit/tools/test_question_tool_root_gate.py`) + flow proofs (`tests/unit/services/test_child_report_open_questions_flow.py`, 16) + next-turn execution (3) + the pins (§11.4, from the worktree venv) — before any merge to `latest`.
6. **Merge this branch into `latest` with `--no-ff`** (audit-trail branch pushed).
7. **Push `latest` + the feature branch** — the stage-time **tip-identity freshness guard** (L1: `HEAD == refs/remotes/origin/latest`) requires `origin/latest` to be **at** the merged tip; exit **78** `non-tip-tree` otherwise.
8. **Version bump to the NEXT FREE version**, then **STAGE ONLY** — **no promote**. `latest` is **v0.26.1** as of 2026-10-20 ⇒ likely **0.26.2**, but **confirm at bump time — do NOT hardcode**. Empty migration delta ⇒ the standing **explicit** rollback-safe reasoning (§13 step 8).

---

## 13. Staging (P11)

**Stage only — NO promote.** The user's 3-factor nonce ceremony owns promotes.

> **Deploy-time behaviour change (N6 / F1) — must appear in the deploy paragraph of the final report.** The mid-flight QA / answer-event fan-out becomes **ROOT-ONLY**: only roots mint question packs (the sole production writer is `daemon/tools/question_tools.py:502`), so with the gate in place the QA relay lane and the answer-event surface fire **for roots only**. Non-roots surface blockers through the report's "Open questions for the orchestrator" section instead.

| Step | Action | Verification |
|---|---|---|
| 1 | Merge the feature branch into `latest` (`--no-ff`, audit-trail branch pushed) | `git log --oneline` shows the merge |
| 2 | Version bump via the project convention: `python3 scripts/bump_version.py` (`scripts/bump_version.py:1-30`) — **to the NEXT FREE VERSION** (`latest` is **v0.26.1** as of 2026-10-20 ⇒ likely **0.26.2**; **confirm at bump time — do NOT hardcode 0.26.1**) | **If it dies at an off-PATH `uv-sync`, complete manually with EXACT semantics — 4 files: `pyproject.toml`, `daemon/__init__.py`, `tests/test_api.py`, `uv.lock`**; `tests/test_api.py` also carries the incoming v0.26.x version expectation — reconcile it in the same commit (§12.4 step 3b) |
| 3 | Regenerate `agents/.prompt-artifacts.sha256` (if any `agents/**` file changed) + **re-check as a NO-OP before staging** (else the guard reads the tree as dirty-build) | `python3 scripts/protection/generate_prompt_artifact_baseline.py` then `--check` → clean |
| 4 | **PUSH `latest` to origin BEFORE staging (NEW — §13 push step)**: the stage tip-identity guard prefers `refs/remotes/origin/latest` and exits **78** `non-tip-tree` otherwise. **DATED 2026-10-20:** `origin/latest` = local `latest` = `e6eb13fcc` — origin is **NOT** behind *at that moment*, but another lane's push re-opens the gap ⇒ this step stays REAL, not hypothetical | `git ls-remote origin latest` == local `latest` sha (re-check at execution) |
| 5 | Stage: `bash scripts/upgrade/stage.sh <version>` (Makefile target `upgrade-stage` → `Makefile:369-370`) | provenance sidecars written; **tip-identity freshness guard** (L1: `HEAD == refs/remotes/origin/latest`; L2 sidecar re-hash) must pass — a non-tip tree exits **78** `non-tip-tree` |
| 6 | **Keep the staging worktree** (both provenance sidecars intact) for the promote ceremony | avoids the v0.25.2 provenance-evidence gap |
| 7 | **Migration delta = EMPTY** — no file under `daemon/migrations/versions/` changes (this commission touches no schema) | `git diff <base>..<head> -- daemon/migrations/versions/` → empty |
| 8 | **Rollback-safe adjudication (STANDING RULE — explicit semantic reasoning, not a reflex `=0`)** | `ENSEMBLE_ROLLBACK_SAFE=1` is legitimate: (1) zero migration files added/changed ⇒ no schema mutation to reverse; (2) the daemon change is a **build-time tool-registration conditional** — reversible by the next release; (3) the `meta.json` grants are additive prompt/tool config, no data state; ⇒ `rollback_safe=true`, `source=explicit=true`. Record the reasoning in the stage log line (`rollback_safe=true source=explicit=true`). |
| 9 | Do **not** flip `current`; do **not** restart the daemon | the live symlink and the live epoch must be unchanged by this commission |
| 10 | **Emit the final report** — the 7 commission items **verbatim** + the 3 extras + the fix-round disclosures (contract in §17.1 = **34** items — the §13 and §17.1 counts are **the same number**, per approver i3 **B2**), including the **flow proof** (child open-question → parent instruction) and the explicit **in-flight child question-pack statement** | the report is the commission's deliverable; §17 is the contract |

---

## 14. Risk register

| ID | Risk | Impact | Mitigation | Verification action |
|---|---|---|---|---|
| **R1** | Granting `ari` the `question` category changes **LIVE Discord chat behavior** (new capability for the chat-facing agent; a mid-turn pack pause in a chat lane could strand a turn) | high | Root-scoped skill steers *"prefer asking in my reply text"* for chat lanes (§5.1 #2); the existing wedge guard + answer path protect; **flag explicitly in the final report** | Read the rewritten skill end-to-end before merge; confirm the chat-lane preference sentence is present; no live promote performed (stage only) |
| **R2** | The mappings-API lane (P3) could regress the `source_type` **single-writer** invariant | high | Thread the type into the **existing** writer (`instance_lifecycle.py:2390`) — never add a second metadata writer; keep the edit additive (one keyword at one call site) | `grep -n 'source_type' daemon/services/instance_lifecycle.py` ⇒ exactly one assignment to `instance_metadata["source_type"]`; new test asserting a mappings-lane root resolves the tool |
| **R3** | Stale `_tool_metadata` residue leaves `"question"` **advertised on the help surface for EVERY instance of a granted agent — including non-roots** | low (cosmetic) | The help surface is computed from the **AGENT META**, not from the instance: `daemon/tools/help.py:105-115` → `expand_allow_for_innate_skills` → `resolve_tool_filter` ⇒ every instance of an agent that grants `"question"` (leader, devops, ari) sees it listed, **even when the gate stripped the tool from that instance**. `_tool_metadata` is a **module-level global** (`daemon/tools/_tool_registry.py:18`) cleared **only** by `clear_registry()` (`:625-628`) and appended to by `scan_tools_for_full_docs` (`:660-665`) ⇒ **cross-instance residue** (one instance's scan can leave the category listed for another instance's help). The gate is a *registration* gate and must not touch this surface. | Verify the help listing after the change; state the **precise mechanism** (agent-meta computation + module-level global + cross-instance residue) in the final report — **do not** chase it |
| **R4** | Legacy in-flight packs at deploy time | medium | Migration story (§15): packs live in RAM **and** are shadowed into `instance_metadata['question_pack_payload']` ⇒ the answer path resolves them (CAS + shadow clear + boot rehydration). **No pack is orphaned by the gate** | T8 |
| **R5** | The sibling commission merges into `latest` while we implement | medium | Implement **only after** the merge (Phase 0 gate); never touch their worktree; keep `instance_lifecycle.py` edits additive + minimal | Re-read `latest` after the merge; re-verify §1-§3 anchors (line numbers shift) |
| **R6** | Prompt sweep breadth (**97-file branch partition / 95 commission surface / 81 prompt-doc edited** — final edit-set, §10.10) risks prompt-integrity test failures and seal/hash pins | medium | §11.2 pin list + §11.3 the **18**-file seal overlap (**36** re-pin constant-update operations over 18 distinct files); **require targeted test runs per phase**; re-pin constants-only, provenance-labelled | Run §11.4's prompt-integrity + seal-tripwire commands after each sweep phase, not once at the end |
| **R7** | The clause-family rewrite could **degrade a root's own ask wording** — a root bound to a chat source must keep the ability to ask | medium | The edit rule is **conditional**: "…or report open questions **(unless you are the user-facing root)**" (§10.5); the rewritten shared skill teaches the **root** half; the root's **capability** is untouched (gate still admits it, allow-list still grants it) | Grep the swept files for the conditional form; confirm every clause-family sentence carries it; confirm `agents/_prompt_system/innate-skills/question/skill.md` still teaches the root ask |
| **R8** (fix round 4 — **NAMED FOLLOW-UP COMMISSION**, pre-existing, out of scope here) | The **maintenancer grant/prompt mismatch**: `agents/maintenancer/workflow.md:26` **prescribes** `spawn_instance` / `send_message`, and **Cardinal #3** ("END TURN after send_message") presumes the same channel — but the maintenancer's `tools.allow` has **no `instance` category** (verified by runtime probe with the real `_apply_tool_filter` — §10.4c.4), so the prompt **prescribes a call the grant does not deliver** | medium | **NOT self-served here.** The fix-round-4 remit was the *parent-side paragraph* only, and the commit explicitly left `agents/maintenancer/meta.json` and the pre-existing grant/prompt mismatch **as-is**. ⇒ **routed as a NAMED follow-up commission**: align the maintenancer's `workflow.md:26` + Cardinal #3 with its actual grant (either grant the `instance` category, or route the prescription to the report channel) | Verify in the follow-up lane that `agents/maintenancer/workflow.md:26` no longer prescribes a channel the grant withholds, and that the `instance`-category decision is explicit. **Cross-ref: §17.1 item 33; §10.4c.4** |
| **F1** | The mid-flight QA / answer-event fan-out becomes **ROOT-ONLY** (only roots mint packs — the sole production writer is `daemon/tools/question_tools.py:502`) | medium | Deliberate consequence of the gate; the non-root path becomes the report section (§6); the fan-out itself is unchanged | State it in the **deploy paragraph** of the final report (§13) + confirm no non-root can mint a pack (T1/T3/T5) |
| **F2** | `agents/watcher/meta.json` has `allow: []` ⇒ **default-open universe** ⇒ it HOLDS `ask_questions` today; the gate strips it | low | The gate (not the allow-list) is the hiding control ⇒ the tool is gone for watcher either way; pinning watcher's allow list is optional hygiene | Confirm `ask_questions` is absent from a watcher's built toolset; optionally pin the allow list (§4) |
| **F3** | A **non-ari agent bound to a chat source** would pass the gate but not hold the category ⇒ it cannot ask | low (latent) | RULING-1 limitation, **disclosed**: the mappings API can bind ANY agent to a chat source, but no non-ari agent is known chat-bound today ⇒ the gap is **LATENT, not active** | State it as a non-goal + a final-report line (§0, §17); re-check the chat-bound agent list at implementation time |
| **F4** | **Job roots and API roots lose the tool** — the predicate is *stricter* than "non-root" (a root with no chat source is not a user-facing root) | medium | Commission-endorsed and intended (P1 fail-closed); such roots answer via `job_answer` | Disclose the behaviour delta in the final report; T3/T4b pin it |
| **F5** | The **56 test files** that call `create_instance_tools` now build toolsets **without** the question tool (re-derived at `a9c041ab0`: `grep -rln --include=*.py "create_instance_tools" tests/ \| wc -l` → **56**; the plan's earlier "~73–109 callers" was a stale estimate and the fix-round-2 value **53** was one commit behind). **The `--include=*.py` is LOAD-BEARING**: without it the `__pycache__` binary matches inflate the count to **100** | low | Benign today — **no test asserts `ask_questions` membership in a built toolset** (N1) ⇒ nothing breaks; but a future test that asserts its presence would fail for a confusing reason | Record the census row (**56** files, with the `--include=*.py` command embedded) in the final report; `tools/dev/verify_service_default_open.py`'s unpinned `tool_count` shifts by **−1** (no assertion depends on it) |
| **W4** (reviewer, **on record — non-blocking disclosure**) | **Two-layer capability tensions that survive the branch** — the gate (§2) and the allow-list (§4) are independent, and the sweep left three deliberate asymmetries: (a) **devops** holds `"question"` in `tools.allow` against a **flat never-ask** prompt (a capability it may never exercise); (b) **maintenancer / project-manager / gaia / _mother** carry **root-conditional** prompts (*"…unless you are the user-facing root"*) **WITHOUT** the category — their conditional half is unreachable for them; (c) only **ari / leader / devops** grant the category at all | low (documented, not a defect) | **Recorded, not resolved.** The gate is the authoritative hiding control and the allow-list is the capability grant; each agent's combination is a deliberate outcome of the sweep, not an accident. The §14 F3 scoping limitation (a user-facing root whose agent does not grant the category cannot ask) already covers the *active* half | Re-read the four agents' `tools.allow` + their canonical home's wording together and confirm the asymmetry is **intentional**; if a future change grants a category to one of the four, update the other side in the same commit |

| **F6** | The **leader loses the question-SKILL text** (`innate_skills` loses `"question"`) while still needing to answer a root's pack | low | A **teaching gap, not a capability gap**: under RULING-1 the leader **KEEPS** `"question"` in `tools.allow` ⇒ it retains the **capability**; only the skill **text** is gone | Confirm the leader's toolset still contains `ask_questions` (T9 case (a) uses the leader meta); the shared skill teaches the root half for whoever holds it |
| **F7** | The **governor** carries its open-questions contract in **two** files (`rule.md` + `workflow.md`) ⇒ two vocabularies for one contract (the "stated once" trap, guide R-e) | medium | RULING-2: the contract lives **ONCE** in `agents/governor/rule.md` (the governor's D_HOME **and** E_HOME are both `rule.md` — `tests/unit/test_report_integrity_prompts.py:92,:106`); `workflow.md:46` becomes a **section-name pointer** | Confirm `workflow.md:46` carries only the pointer; confirm `rule.md` carries the contract + the D/E snippets; re-pin `governor/rule.md` (14th) |
| **F8** | **Legacy rows without the `source_type` stamp** (written before 2026-08-12) are not user-facing roots ⇒ they lose the tool on restore | low | **PRE-EXISTING and OUT OF SCOPE — NO BACKFILL** (a backfill would make the migration delta non-empty and break the P11 EMPTY-delta premise, §13 step 7 / §15) | State it in §1.3 + §15; confirm `daemon/migrations/versions/` is untouched |

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

**Legacy unstamped rows — NO BACKFILL (N8 / F8).** Rows created before the `source_type` writer existed (**2026-08-12**) carry no stamp ⇒ they are not user-facing roots ⇒ a restored such instance gets no question tool. **No backfill** is performed: it would add a migration file and make the delta non-empty, breaking the P11 EMPTY-delta premise and the `rollback_safe` adjudication in §13 step 8. The behaviour delta is disclosed (F8), not repaired.

---

## 16. Coupling map + implementation order

### 16.1 Coupling map (file-level)

| Phase | Files | Coupled to | Strength |
|---|---|---|---|
| 0 | *(no file edits)* — branch / worktree / KV verification | Phase 9 | medium |
| 1 | `daemon/tools/instance.py`, `daemon/services/instance_lifecycle.py`, `daemon/routers/mappings.py` | §2 gate ↔ §3 lane (both thread `source_type`) | **strong** — the lane fix is worthless unless the gate reads the threaded value |
| 2 | `tests/unit/tools/test_question_tool_root_gate.py` (**LANDED — 14 tests**, incl. the W3 ordering pin + the T6-gate non-chat `source_type` case), `tests/unit/services/test_child_report_open_questions_flow.py` (**LANDED — T6/T7/T8**), `tests/unit/services/test_child_report_next_turn_execution.py` (**LANDED — 3**, tester commit `b7e3a0808`), `tests/job_queue/test_job_answer_tool.py` (**LANDED**) | Phase 1 (asserts the gate); §6/§7 (report + continuation) | strong |
| 3 | `agents/{leader,devops,ari}/meta.json` + `tests/unit/test_devops_agent.py` | Phase 1 (defense-in-depth); the `ari` grant needs the gate to let it through | strong |
| 4 | `agents/_prompt_system/innate-skills/question/skill.md` | Phase 3 (only `ari` receives it afterwards) | medium |
| 5 | the sweep — **78 agents prose + 3 docs = 81 prompt/doc files edited** (final edit-set; manifest §10.10; machine cross-check = the **97-file branch partition**: 78 prose + 3 `meta.json` + 1 baseline + 3 `daemon/` + 3 `docs/` + 7 tests + 2 planning ⇒ **commission surface 95**) | Phase 4 (vocabulary); the guide's canonical-home rule (§10.1); **36** seal re-pin constant-update operations | **strong** (breadth) |
| 6 | **36** seal constant-update operations (18 distinct files) + targeted tests | Phase 5 | strong |
| 7 | *(review artifacts only)* — reviewer council | Phases 1-6 | strong |
| 8 | *(test artifacts only)* — independent tester verification | Phase 7 | strong |
| 9 | `scripts/upgrade/*`, `scripts/bump_version.py` | Phases 1-8 | medium |

### 16.2 Phase order (dependency rationale per phase)

| Phase | Content | Depends on | Rationale |
|---|---|---|---|
| **0** | **Wait for the sibling to land in `latest`, then merge `latest` INTO this branch.** The sibling `e2899f8bd` is **already merged into this branch** (`0e7500a1b`) but **not into `latest`** ⇒ run `git merge latest` (**MERGE, never rebase** — §12.2), then re-read the tree and re-verify every §1-§3 anchor (the sibling merge shifted the restore anchors). **KV work = VERIFY/MERGE, not create** (§12.3). | — | P10 hard gate; R5; BLK-6 |
| **1** | **Daemon gate + mappings lane.** `daemon/tools/instance.py` (predicate helper + conditional + keyword-only signature), `daemon/services/instance_lifecycle.py:2137` & `:4690` (threading), `daemon/routers/mappings.py:114-117` (`source_type=source.source_type`). | Phase 0 | The gate is the authoritative control; both call sites must be threaded **in the same change** or the restore path silently loses the tool for roots |
| **2** | **Gate tests T1–T5, T9** (registration + capability) in `tests/unit/tools/test_question_tool_root_gate.py` — **LANDED**. | Phase 1 | Proves the gate before any prompt work; the **GATE PROOF** set (T1, T3, T4b, T5, T9) turning green is the go/no-go for Phase 3's grants. **Test-first red evidence**: the **GATE PROOF** set (T1, T3, T4b, T5, T9) is **RED pre-change**; the **PRESERVATION** guards (T2, T4a) are **GREEN pre-change** under the signature-adaptive harness — the pre-change build registers the question tool unconditionally, so *"a legitimate root keeps the tool"* already held (landed class `TestPreservationGuardsGreenPreChange`, `tests/unit/tools/test_question_tool_root_gate.py:311`; rationale docstring `:52-71`), and the **RED** witness for the new kwargs' absence is the **AC3 keyword-only signature pin** (`:69`, `:466`) — §11.1 |
| **3** | **`meta.json` grants** — leader/devops **remove `"question"` from `innate_skills` and KEEP it in `tools.allow`**; ari **adds** both (§4, §10.3). Plus the `tests/unit/test_devops_agent.py:165` expectation fix. | Phase 2 green | Defense-in-depth only after the authoritative gate is proven; the ari grant is what makes "roots still ask" real; the two-layer model (BLK-1) is what keeps a leader-as-chat-root able to ask |
| **4** | **Root-scoped skill rewrite** (§5.1) of `agents/_prompt_system/innate-skills/question/skill.md`. | Phase 3 | The skill must teach root behavior **after** only ari holds it |
| **5** | **Prompt & doc sweep** (§10.4-§10.8, §10.10 manifest) — **81 prompt/doc files edited** (78 agents prose + 3 docs; final edit-set incl. fix rounds 1-4), **29** intra-agent canonical homes (§10.1, §10.4b, §10.4c.2), guide rules R-a..R-k (§10.2). | Phase 4 | The sweep uses the final vocabulary; doing it before the skill rewrite risks two vocabularies |
| **6** | **Targeted test runs + seal re-pin adjudication** (§11.2-§11.5): **36** constants-only re-pin update operations across **18** distinct files (git-log last-touch attribution → constants-only update → targeted test → provenance-labelled commit; devops re-pinned twice; leader/rule.md, maintenancer/rule.md, project-manager/workflow.md re-pinned again in fix round 2; **10** re-re-pins in fix round 3, all of files already inside the set; **+1 op** `9f8b49306` charter/rule.md — the NEW distinct ⇒ 18; **+3 ops** `23859484e`; **+1 op** `30ea77149` maintenancer REVERTED to its pre-B3 value) + the prompt-artifact baseline regeneration ×4 (44 + 12 + 1 + 22 entries) + `tests/unit/test_devops_agent.py` + `tests/unit/test_ari_worker_integration.py:345` expectations. — **LANDED.** | Phase 5 | The tripwire only fails **after** the edits; re-pin is a discrete, auditable act |
| **7** | **Reviewer council** — owner: the **reviewer** agent (council, **0-critical bar**). | Phases 1-6 | The council's 7 blocking findings are text-level and were folded into this plan; a second council confirms the fold. **0 critical** is the bar. |
| **8** | **Independent tester FUNCTIONAL verification** — owner: the **tester** agent: registration-level (**GATE PROOF** T1/T3/T4b/T5/T9 vs the signature-adaptive **PRESERVATION** T2/T4a — reported separately, never conflated) + the **flow proofs** (T6/T7/T8, LANDED — child open-question → parent instruction) + the **next-turn execution proof** (3 cases, LANDED) + the **gate-pack mutation proof** (M1 → 5 reds, M2 → 2 reds, M3/M3b, undetected NONE). **Phase 8 owns the functional pass** (round 1 of the project's two-round tester convention); **staged verification happens AFTER staging, in Phase 9** (W4-phase8/9). | Phase 7 | Independent verification on the **merged** tree, not on the developer's branch — the council approves the code, the tester proves the behaviour. |
| **9** | **Merge + version bump + PUSH latest to origin (§13 step 4) + stage (no promote)** (§13) + **independent staged verification AFTER staging** (round 2 of the two-round tester convention) + emit the final report (§17). | Phases 1-8 | Staging consumes the merged tree; the EMPTY migration delta ⇒ `rollback_safe=true` (explicit reasoning); the push makes the tip-identity guard pass |

---

## 17. Definition of done / exit criteria

- [ ] The gate exists at `daemon/tools/instance.py:5895-5900` as a **build-time conditional**; `ask_questions` is **absent** (not refused) from every non-root toolset.
- [ ] `parent_id` + `source_type` are threaded **keyword-only** into `create_instance_tools` from **both** call sites (`instance_lifecycle.py:2137` spawn, `:4690` restore — post-sibling-merge anchors).
- [ ] The predicate is evaluated from **threaded values** — no DB row read at registration (verified by code inspection + T1–T5, T9).
- [ ] `PRIVILEGED_TOOL_CATEGORIES` is **unchanged** (8 entries) and `"question"` **remains** in `CATEGORY_MODULES` (`:729`).
- [ ] The mappings lane passes `source_type` into the **single** writer; exactly one `instance_metadata["source_type"] =` assignment exists in the codebase.
- [ ] `meta.json` (two-layer model, RULING-1): leader/devops **lose `"question"` from `innate_skills`** (⇒ `["coordination","chart","todo"]` / `["dynamic-skill","chart","todo"]`) and **KEEP it in `tools.allow`**; ari **gains it in both**; project-manager / critic / watcher untouched (watcher optional hygiene).
- [ ] The Question Skill teaches **root** behavior + the chat-lane preference + the non-root half, in first person, with **no forbidden-layer tokens** (guide R-b).
- [ ] Every non-root agent's contract is stated **once per agent** in its canonical home; same-agent cross-references use section names only (guide R-f); no false "stated once" claim (guide R-e). **Canonical homes = 29** (fix round 4) — re-derived by tree grep for `^## Open questions for the orchestrator` (§10.4a, §10.4b, §10.4c.2). **Parity claim, in the restated prefix-extension form (approver i3 B3): the 4-bullet core is byte-identical across 29/29 homes (all prefix-extensions of the 674 B core)** — the strict *"byte-identical across all"* form is **withdrawn** (it was the blocker; two homes carry a trailing addition).
- [ ] `tests/unit/test_devops_agent.py:165` updated; `tests/test_innate_skills_refactoring.py:67` verified GREEN; `tests/unit/tools/test_frozen_tool_name_discovery.py:223` verified GREEN (source unchanged).
- [ ] All **36** seal re-pin constant-update OPERATIONS across **18** edited sealed files landed with constants-only updates, git-log attribution, targeted test runs, and provenance-labelled commits; the tripwire `tests/test_chart_image_delivery_e2e.py::TestPhaseDDoesNotModifySealedArtifacts` is GREEN and all **35** constants re-verified from real bytes. (`agents/governor/rule.md` sha `84a93586…` — its **1 × "Chat Delivery"** and the junk-report clause survive; fix-round re-pins: charter/workflow.md `2a5078f9…`, devops/rule.md `febf0e89…` ×2, maintenancer/rule.md `abe43bae…` ×2; **fix-round-2 re-pins: leader/rule.md `f0bda673…`, maintenancer/rule.md `abe43bae…`, project-manager/workflow.md `bde47909…`**; **fix-round-3 re-re-pins (10, `a9c041ab0`): approver/rule.md, approver[v2]/rule.md, architect/rule.md, developer/rule.md, devops/rule.md, planner/rule.md, reviewer/rule.md, reviewer[v2]/rule.md, tidier/rule.md, tidier[v2]/rule.md** — the 3 round-2 and all 10 round-3 updates are **RE-re-pins of already-counted files** ⇒ distinct stays **17** **through fix round 3**; **fix round 4 adds `agents/charter/rule.md` as a NEW distinct sealed file** (the canonical home added at `9f8b49306`) ⇒ distinct **18**, split **18 / 17 = 35** ✓. **Fix-round-4 seal ops: +1** `9f8b49306` charter/rule.md, **+3** `23859484e` (coder/soul.md, governor/rule.md, maintenancer/rule.md), **+1** `30ea77149` (maintenancer **REVERTED to its pre-B3 value** `abe43bae…`) ⇒ **36** total. Current round-4 constants: `agents/charter/rule.md` `2cb6e01b…` (`:1405`) · `agents/coder/soul.md` `6263b648…` (`:1434`) · `agents/governor/rule.md` `07aaed75…` (`:1439`) · `agents/maintenancer/rule.md` `abe43bae…` (`:1448`, **byte-identical to base `a9aa911a3`** — the self-consistency proof, §10.4c.4). The tidy pass's seal edit is **M7 PROVENANCE CORRECTIONS** — attribution comments only, the 35/35 constant VALUES byte-identical.)
- [ ] **Semantic pins (BLK-3) — "NO EDIT expected — verify" — all GREEN:** `tests/unit/tools/test_attestation_prompt_contract.py` (attestation contract strings kept / forbidden strings absent in `agents/leader/rule.md` + `agents/leader/workflow.md`) · `tests/unit/tools/test_mission_watch_prompts.py` (`watch_mission` vocabulary kept, **zero** `watch_job` tokens) · `tests/test_chart_image_delivery_audit.py` ("Chat Delivery" survives verbatim in all **20** chart-capable agents).
- [ ] `agents/.prompt-artifacts.sha256` regenerated; `tests/test_prompt_artifact_baseline.py` GREEN (**161 artifacts; `--check` 0 diffs; `sha256sum -c` 161/161 OK; drift = 22 changed entries, a strict subset of the 26 round-2 files, no foreign file**).
- [ ] `tests/unit/test_report_integrity_prompts.py` and `tests/unit/tools/test_prompt_section_reference_integrity.py` GREEN.
- [ ] **T1–T9** all pass under the fenced invocation of §11.4 (worktree venv): gate pack `tests/unit/tools/test_question_tool_root_gate.py` (**14** tests) + flow pack `tests/unit/services/test_child_report_open_questions_flow.py` (16) + next-turn pack `tests/unit/services/test_child_report_next_turn_execution.py` (3) + `tests/job_queue/test_job_answer_tool.py`.
- [ ] **W3 pin landed** — `TestGateBeforeFilterOrdering::test_gate_registration_precedes_tool_filter_in_build_path` asserts (AST, no hardcoded line numbers) that `create_question_tools(` **precedes** `_apply_tool_filter(` in `create_instance_tools`; it **fails on reorder** (proven against a real inverted copy in `/tmp` and against the in-test in-memory mutant self-check). Gate pack **12 → 13** (→ **14** after fix round 4's T6-gate).
- [ ] **W1 rename landed** — `test_t4a_restore_path_root_with_stamped_row_has_question_tool` → `test_root_with_threaded_chat_source_type_has_question_tool`; recorded as a **naming-honesty correction, NOT a coverage loss** (the pack never touches a repository, so it cannot cover the restore-path row stamp; the name and docstring now state exactly what is asserted).
- [ ] **Batteries (fix round 2 + fix round 4) — ZERO new failures**: gate pack **14P** · closure sweep 1462P/5F (pre-existing) · report integrity 51P/20S · devops agent 61P/2F (pre-existing) · chart audit 46P · seal tripwire 24P/4D · flow proofs 16P · next-turn 3P · baseline 19P ⇒ **TOTAL FAILURES = exactly the 11 known pre-existing (5 closure-sweep + 2 devops `meta.json` + 3 gaia `meta.json` + 1 `job_answer` order pin)** — ALL stale-expectation class, **NONE caused by this branch** (§11.5).
- [ ] **Proof sets reported separately, by name (BLK-7 + A1, corrected by AMENDMENT #5/B2, extended by AMENDMENT #6):** the **GATE PROOF** set (**T1, T3, T4b, T5, T6-gate, T9**) is GREEN and was **RED** before the change; the **PRESERVATION** guards (**T2, T4a**) are signature-adaptive and **GREEN PRE-CHANGE** — the pre-change build registers the question tool unconditionally, so *"a legitimate root keeps the tool"* already held (landed class `TestPreservationGuardsGreenPreChange`, `tests/unit/tools/test_question_tool_root_gate.py:311`; rationale docstring `:52-71`) — and the **RED** witness for the new kwargs' absence is the **AC3 keyword-only signature pin** (`:69`, `:466`); the **FLOW PROOFS** (**T6, T7, T8**) are LANDED in the flow pack (16 cases, mutation-verified 5/5). **A green preservation run is never reported as gate proof.**
- [ ] **Phase 7 — reviewer council APPROVED with 0 critical** (owner: the **reviewer** agent).
- [ ] **Phase 8 — independent tester FUNCTIONAL verification PASSED on the merged tree** (owner: the **tester** agent): registration-level (gate pack, **14** tests) + flow proofs (T6/T7/T8) + next-turn execution (3) + the **gate-pack mutation proof** (M1 → 5 reds, M2 → 2 reds, M3/M3b, **M-i → 1 exact red, M-ii → 1 exact red**, **undetected NONE**, orthogonality holds). **Phase 9 — staged verification AFTER staging** (two-round tester convention).
- [ ] Nothing under `daemon/migrations/versions/` changed (EMPTY delta); `rollback_safe=true` recorded with the explicit semantic reasoning.
- [ ] Staged (no promote): `releases/<ver>/` exists with provenance sidecars; the staging worktree is **kept**; `current` symlink and live daemon epoch unchanged.
### 17.1 Final-report contract (BLK-4 — 7 commission items **verbatim** + 3 extras + 7 fix-round-1 disclosures + **3 fix-round-2 ratifications** + **5 fix-round-3 disclosures** + **9 fix-round-4 disclosures** = **34** items)

The final report **must** contain the following **34** items. Items **1–7** are the commission's required items, **verbatim**; items **8–10** are the retained extras; items **11–17** are the fix-round (AMENDMENT #3) disclosures; items **18–20** are the **fix-round-2 leader ratifications** (R-a / R-b / R-c) — **decisions, not open questions**; items **21–25** are the **fix-round-3 disclosures** (AMENDMENT #5) — the B2 classification correction, the two B1 leader ratifications, the B3 correction, and the FINDING-2 disclosure; items **26–34** are the **fix-round-4 disclosures** (AMENDMENT #6 — approver iteration-003 closure) — §10.4c.

> **COUNT ALIGNMENT (approver i3 B2) — the §13 and §17.1 numbers are THE SAME NUMBER.** The earlier mismatch (*§13 said 20, §17.1 said 25*) is **closed**: both now read **34**. If further items are added, **both places must be updated together** — an un-aligned count is a defect.

| # | Item | Source / requirement |
|---|---|---|
| **1** | **The pinned predicate** — the exact P1 expression and why `source_type` (never `original_source`) | §1 (P1, PINNED) |
| **2** | **The registration gate** — the build-time conditional, the two call sites, and the fact that the tool is **never registered** for a non-root (absence, not refusal) | §2 (P2, PINNED) |
| **3** | **List of agent files touched** — the sweep manifest: the **97-file branch partition** (78 agents prose + 3 `meta.json` + 1 baseline + 3 `daemon/` + 3 `docs/` + 7 tests + 2 planning), the **95-file commission surface**, and the **81** prompt/doc edited (78 agents prose + 3 docs); machine-derived with the derivation command (§10.10) | §10.10 |
| **4** | **Flow proof** — a child surfaces an open question in its report ⇒ the parent receives it and answers via `send_message` ⇒ the child resumes and continues | §7, T6, T7, T9 |
| **5** | **Tests** — reported **separately, by name**, and with the pre-change state stated exactly: **gate-proof (RED pre-change) = T1, T3, T4b, T5, T6-gate, T9** (T6-gate added in fix round 4 — the gate-pack test whose pytest name begins `test_t6_`, **not** the flow-proof T6); **preservation (GREEN pre-change, signature-adaptive) = T2, T4a, with the AC3 keyword-only signature pin as the RED witness for the new kwargs' absence** (landed class `TestPreservationGuardsGreenPreChange`, `tests/unit/tools/test_question_tool_root_gate.py:311`; rationale docstring `:52-71`; AC3 pin `:69`, `:466`); plus the LANDED **FLOW PROOFS** (T6/T7/T8 — 16 cases, mutation-verified 5/5), the **W3 ordering pin** (gate pack 12 → **13** → **14**), the **W1 honest rename**, the next-turn execution proof (3) and the **gate-pack mutation proof** (M1 → 5 reds, M2 → 2 reds, M3/M3b, **M-i → 1 exact red, M-ii → 1 exact red**, undetected NONE) | §11.1, §11.4, §11.5 |
| **6** | **Staging plan** — the staged path, version, binary sha256, the EMPTY migration delta, the `rollback_safe=true` adjudication, and **no promote** | §13 |
| **7** | **Explicit in-flight child question-pack statement** — what happens to any pack already in flight for a child at deploy time (RAM + durable shadow + rehydration + CAS ⇒ no pack is orphaned) | §15, T8 |
| **8** | The **36** adjudicated seal re-pin constant-update OPERATIONS / **18** distinct files (with their provenance) and the **35**-file sealed baseline split (**18** re-pinned / **17** safe) | §11.3 |
| **9** | The **ari-grant live-behavior flag** (R1) and the **precise `_tool_metadata` / help-surface mechanism** (R3 / N5) | §5.1, §14 |
| **10** | The **deploy paragraph**: the mid-flight QA / answer-event fan-out is now **ROOT-ONLY** (N6 / F1) — and that **no promote** was performed | §13, §15 |
| **11** | The **census**: exactly **3 agents held the tool before this change — `leader`, `devops`, `watcher`** (watcher via its default-open empty allow list) | §4, F2 |
| **12** | The **fail-closed default-kwargs invariant**: an unstamped/absent `source_type` ⇒ NOT a user-facing root ⇒ no tool (the gate default is denial) | §1, §2 |
| **13** | The **legacy unstamped-row silent delta**: rows created before the 2026-08-12 writer carry no stamp ⇒ no tool on restore — disclosed, NO BACKFILL (N8/F8) | §1.3, §15 |
| **14** | **T2's anti-vacuity precondition**: the synthetic meta **allows `"question"`** — otherwise T2 proves nothing (the allow-filter could be the cause) | §11.1 |
| **15** | **T9's coupling to the real leader meta**: T9 reads `agents/leader/meta.json` — a future failure there is an **alarm, not flakiness** (it means the leader allow-grant moved) | §11.1 |
| **16** | The **dead / test-only source_type-less spawn lanes**: `daemon/sources/slack/thread_manager.py:217` (dead) and `daemon/sources/mapper.py:452` (test-only) spawn without `source_type` — **thread or delete when revived** | §3, P3 review note |
| **17** | The **maintainer knowledge-file `ask_user` reference** — a pre-existing audit-trail item, not a regression of this commission | §10.9, fix-round census |
| **18** | **R-a — the upgrade nonce ceremony is ROOT-BOUND.** A non-root maintenancer structurally **cannot** complete a live upgrade: the root must **mint AND arm** the nonce. The wording was left **NO-EDIT deliberately** (a relay would weaken the 3-factor ceremony). Report it **verbatim**: *"the upgrade nonce ceremony is root-only; non-root agents must not relay it."* | §10.4a (R-a) |
| **19** | **R-b — the TWO canonical wording families, ratified as deliberate** (a future sweep must not "unify" them): (i) **root-CONDITIONAL** *"…unless you are the user-facing root"* for agents that can plausibly **be** a user-facing root — **leader, maintenancer, project-manager**; (ii) **FLAT** *"never ask the user directly"* for **structurally-never-root** agents — **jober, _baby_template, giter, devops**. State the **agent-class rationale** (a conditional preserves the root's own ask capability; a structurally-never-root agent gains nothing from one). | §10.4a (R-b) |
| **20** | **R-c — the project-manager conditional form is ratified** ("the final call is human" → the conditional form), and the **growth.md conversions are KEPT** (inert files — never composed into a prompt — but **consistency over diff-minimization**). | §10.4a (R-c) |
| **21** | **B2 — the T2/T4a classification is GREEN pre-change.** The preservation guards are **GREEN** pre-change (the landed harness is signature-adaptive and the pre-change build registers the question tool unconditionally) — landed class `TestPreservationGuardsGreenPreChange` (`:311`), rationale docstring `:52-71`; the **RED** witness for the new kwargs' absence is the **AC3 keyword-only signature pin** (`:69`, `:466`). ⇒ **gate-proof (RED pre-change) = T1, T3, T4b, T5, T6-gate, T9**; **preservation (GREEN pre-change, signature-adaptive) = T2, T4a** | §11.1, §11.5, §17 |
| **22** | **B1 — the PARENT-SIDE paragraph deviation (LEADER-RATIFIED).** The brief expected **NO** parent-side paragraph for architect / planner / tidier; that expectation was **WRONG** — each of the three dispatches work (evidence: `agents/architect/soul.md:5,7,12`; `agents/planner/soul.md:15-20`; `agents/tidier/soul.md:18`). The paragraph was added to **all six** families. **Record the deviation + the evidence.** | §10.4b |
| **23** | **B1 — the pointer reductions are 0 (LEADER-RATIFIED, VERIFIED NO-OP).** No pointer-reduction operation was performed: every remaining clause occurrence is the §10.5 landed keep-verbatim allow-list (the long-form list at plan-overview.md:506+ — **19/19/19**) with served-tree precedent ⇒ the count is **0 by adjudication, not by omission**. **Recorded so the approver sees the adjudication rather than a silent skip.** | §10.4b |
| **24** | **B3 — the governor's `tools.allow` NEVER had `question`** (the earlier claim was stale). The governor's `tools_note.md` category list was corrected (`question` dropped; the paragraph re-pointed at the canonical section). Only **ari / leader / devops** grant the category. | §10.4, §14 (W4) |
| **25** | **FINDING-2 — the pre-existing `job_answer` order red was undisclosed.** `tests/job_queue/test_job_answer_tool.py:945` (`class TestJobAnswerRegistration`, `:913`) expects `job_answer` to be the **LAST** entry of `create_job_tools()` and gets **`job_resume`**; **proven pre-window by TWO independent methods** (a parent-version replay + last-touch attribution `829feb94d` / `5b194ef7c`). The tester's commit `b7e3a0808` edited that file **without disclosing** the red. **ALL 11** known-pre-existing failures are the stale-expectation class — **NONE caused by this branch**. | §11.5 |
| **26** | **AMENDMENT #6 — the four iteration-003-closure commits and the FINAL counts.** `235ec6934` → `9f8b49306` → `23859484e` → `30ea77149` (4 commits after `a9aa911a3`; **11 unique files, +158/−19**). **FINAL COUNTS: partition 97 / commission surface 95 / canonical homes 29 / parent-side carriers 19 / seal 36 ops over 18 distinct (membership 35) / gate pack 14 tests / pre-existing 11, 0 new.** The branch's edit rounds are **CLOSED** at `30ea77149`. | §0.1, §10.4c |
| **27** | **The 13-row UNAUDITED-DIRS ledger** — **2 HOME-ADDED** (`blueprinter/rule.md:34` — reports upward + dispatcher; `charter/rule.md:74` — its NEEDS MORE INFO is a **pre-work abort**, not a report-time question) ⇒ **canonical homes 27 → 29**; **11 ADJUDICATED-NO-HOME** with evidence (critic/sketcher blocker→orchestrator; doc-maintainer Errors/unclear_intent; experiencer/image-reader/kb-importer/kb-writer structured gap forms; explorer hard-contract conflict + `EXEMPT_AGENTS` registry — **LEADER-RATIFIED**; image-comparator/skill-keeper leaf structured output; watcher text-only verdict by design). | §10.4c.2 |
| **28** | **The 13-row PARENT-SIDE ledger** — **8 ADDED** (coder/soul.md:169, designer/soul.md:81, governor/rule.md:199, maintenancer/rule.md:96 [later removed], project-manager/rule.md:58, tester/rule.md:144, wanderer/rule.md:88, blueprinter/rule.md:45) + **5 RULINGS** (devops/giter: no instance category ⇒ no fan-in; jober: the job-lane equivalent is **`job_continue`** — the canonical `send_message` wording was **factually wrong** — LEADER-RATIFIED; _baby_template/_mother: leaf templates, blast radius avoided). **PARENT-SIDE PARAGRAPH CARRIERS = 19** at HEAD (18 agent files + the leader variant). | §10.4c.3 |
| **29** | **The maintenancer parent-side paragraph was REMOVED (adjudicated) — the branch is "remove", not "reword".** A runtime probe with the **real** `_apply_tool_filter` proved maintenancer holds **NO** outbound instance channel (effective allow lacks `instance`; `send_message` NO, `spawn_instance` NO; its only DB writer refuses `messages`/`message_queue`) ⇒ its real channel is **inbound-only** and the **PARENT** revives a child. `agents/maintenancer/rule.md` is now **byte-identical to base `a9aa911a3`** and its seal constant is back to its **pre-B3** value `abe43bae…` — **the self-consistency proof**. **BONUS SCAN**: of all 20 parent-side carriers, maintenancer was the **ONLY** one whose `meta.json` lacked the instance category — no other agent asserts a missing channel. | §10.4c.4 |
| **30** | **The MUTANT RE-PROOF table (both surviving approver mutants are now CAUGHT).** **M-i** ("" admitted as a chat source type) ⇒ red set **EXACTLY** `test_t4b_legacy_empty_parent_and_empty_source_type_has_no_question_tool`; **M-ii** (any non-chat admitted) ⇒ red set **EXACTLY** `test_t6_root_with_non_chat_source_type_has_no_question_tool`; **zero collateral**; **restore-green proven**. Independent, scratch removed. | §10.4c.1, §11.5 |
| **31** | **The parity claim, restated (approver i3 B3).** **The 4-bullet core is byte-identical across 29/29 homes (all prefix-extensions of the 674 B core).** The earlier strict *"byte-identical across all"* form is **WITHDRAWN** — it WAS the blocker, because two homes carry a **trailing addition** beyond the core. The 674 B core is the unit of equality. | §10.4b, §10.4c |
| **32** | **The two corrections to the AMENDMENT #6 brief (reported, not substituted).** (1) **TREE TRUTH**: `report_integrity_guard.py` is a **SOURCE module** at `daemon/services/report_integrity_guard.py` (not a test) and **`report_integrity_repro.py` does not exist anywhere in the tree** (0 hits) ⇒ the brief's earlier file list is **doubly-unresolved** and the tree truth is the finding. (2) **DATED FIGURE**: the brief's `origin/latest = 14a7f3762 (v0.26.0)` is **stale** — `origin/latest` = local `latest` = **`e6eb13fcc`** (v0.26.1 tip, TIP-VERIFIED) ⇒ the version bump must target the **NEXT FREE VERSION** (likely 0.26.2; **confirm at bump time — do NOT hardcode**). | §10.4c.6, §12.4, §13 |
| **33** | **The maintenancer grant/prompt mismatch — a NAMED FOLLOW-UP COMMISSION (pre-existing, out of scope here, routed).** `agents/maintenancer/workflow.md:26` **and Cardinal #3** still **PRESCRIBE** `spawn_instance` / `send_message` that the grant does not deliver. Not self-served: the fix-round-4 remit was the *parent-side paragraph* only, and `agents/maintenancer/meta.json` was left untouched. ⇒ a follow-up lane must align the prescription with the actual `instance`-category grant. | §14 (R8), §10.4c.4 |
| **34** | **The approver iteration-003 DISPOSITION TABLE (B1–B5)**, reproduced below — the findings are **closed as leader-dispositioned**, transcribed faithfully and **not re-litigated**. | §17.1, §0.1, §10.4c |

**APPROVER ITERATION-003 DISPOSITION TABLE (B1–B5) — closed as leader-dispositioned.**

| Finding | Disposition | Evidence |
|---|---|---|
| **B1** (T4b + the surviving mutants) | **FIXED** | both mutants are now **CAUGHT** — exact-red-set proof, zero collateral, restore-green (§10.4c.1, item 30) |
| **B2** (§13 "20 items" vs §17.1 "25") | **ALIGNED TO THE FINAL COUNT** — **both places now read 34**; the §13 step-10 count and the §17.1 count are **the same number** | §13 step 10 + the §17.1 alignment note above |
| **B3** (the byte-identical claim) | **RESTATED** as the **29/29 prefix-extension** form — *"the 4-bullet core is byte-identical across 29/29 homes (all prefix-extensions of the 674 B core)"*. The strict **"byte-identical across all"** form is **NOT** reintroduced — that WAS the blocker | §10.4b, item 31 |
| **B4** (13 unaudited dirs) | **2 HOMES + 11 ADJUDICATIONS** — the ledger rows | §10.4c.2, item 27 |
| **B5** (parent-side) | **8 ADDED + 5 RULINGS** — the ledger rows — **plus** the maintenancer removal (adjudicated: no outbound channel) | §10.4c.3, §10.4c.4, items 28–29 |

> Items **3**, **5** and **10** also carry the disclosures the council required: the RULING-1 scoping limitation (F3), the job-root/API-root behaviour delta (F4), the **56**-file test-census shift (F5), and the leader's teaching-gap note (F6).

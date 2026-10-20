# Decision Record: Hide `ask_questions` From Non-Root Instances

**Commission:** `hide-ask-questions-from-non-root-instances`
**Repository:** canonical home = `/home/nea/ensemble-src-wt-hidenonroot`, branch `feature/hide-question-tool-nonroot`, HEAD `4088be633` (**fix round 2 LANDED** — 29 files, +393/−161, on top of the tester's `b7e3a0808`; 2026-10-20). **Post-authoring: the ratified R-b CONVERT (gaia/** + _mother/** flat→root-conditional) landed as `7e7062ce3` on top of `4088be633`** (5 files — 4 already-counted prose + baseline regen ⇒ partition unchanged). Base release `latest` = `843b669c5`; `origin/latest` = `1ebdf469f` (**origin is behind** — the stage tip-identity guard compares against `origin/latest`, so staging stays blocked until origin catches up).
**Record scope:** the *why* behind decisions **P1–P13** — rationale, rejected alternatives, invariants, evidence.
**Implementation body:** [`plan-overview.md`](./plan-overview.md) (sibling worker). This file does **not** restate the plan; where a decision has an execution shape, this record points at the plan section that carries it (cited by heading name).

**How to read.** Each of P1–P13 is one section with five fixed blocks: **Decision** (the pinned shape), **Rationale** (with file:line evidence), **Rejected alternatives** (what was considered, why it was rejected, what it would have cost), **Invariants created** (hard rules later work must not break), **Verification** (the test ID from the plan's §11.1 `T1–T9` registry, or an existing pin test, plus the file:line seam). Evidence lines marked `UNVERIFIED` could not be confirmed in this environment and are flagged for implementation time.

> **Standing rule — P1–P11 are PINNED; P12 is an addition, not a re-opening.** They are *recorded* here, not re-opened. Changing any of them is a **new commission with its own decision record**, not an edit to this file.
>
> **Amendment of 2026-10-20 (plan-review council).** The council returned **APPROVE-WITH-NOTES** with **7 blocking** items — all **TEXT-level**. The **design was independently verified sound** and **no pinned decision P1–P11 was re-opened**: the rulings correct the *wording* of P4, add P12, and record the newly-verified facts. P4 is **restated** under the two-layer ruling (RULING-1) — the *capability* grant moves, the *hiding* control does not. **P12 is new** (RULING-2, the governor single-home rule).
>
> **Amendment #3 — 2026-10-20 (fix round LANDED; iteration 002 for the approver).** The fix round has **landed**: `21bccf157 → 2f28cb969 → 12291f2a4` (branch tip `12291f2a4`). This amendment records **LANDED FACTS and classification changes only** — it **re-derives nothing** and **re-litigates nothing**: **P1–P12 stand** as pinned. What changes here is (a) the **proof-set classification** for **T2/T4a** (a third class, see §0), (b) the **LANDED status** of **T6/T7/T8** with their real test file and case counts, (c) the **canonical counts** (sweep **66**, clause-family sites **41**, canonical homes **14**, re-pins **18**), (d) the **sibling/base-sync facts** in P10, and (e) the **new invariants and deferred items** in §3/§4. The canonical numbers in this file are now **66 / 41 / 14 / 18** (was 55 / 34 / 13 / 14) and must agree with `plan-overview.md`. **⟵ superseded by Amendment #4.**
>
> **Amendment #4 — 2026-10-20 (fix round 2 LANDED; FINAL branch truth).** Fix round 2 has **landed**: `12291f2a4 → b7e3a0808` (tester, **test-only**: next-turn execution **3P** + `job_answer` wrapper **+2**) `→ 4088be633` (fix round 2 — 29 files, **+393/−161**). Branch tip = **`4088be633`**. This amendment records **LANDED FACTS only** — it **re-derives nothing** and **re-litigates nothing**: **P1–P12 stand** as pinned, and **P13 is new** (the three leader ratifications **R-a / R-b / R-c**). What changes: (a) the **final counts** (partition **93**, commission surface **91**, sweep edit-set **74** prose, clause-family sites **41** (unchanged), canonical homes **16**, re-pin ledger **21 operations / 17 distinct**, gate pack **13**), (b) the **W3 structural pin** — the gate-before-filter ordering is now **AST-pinned and mutation-verified**, not merely documented (§0, P2, §5), (c) the **W1 honest rename** of the T4a test (naming-honesty correction, **not** a coverage loss), (d) the **B3 closure** (26 `agents/**` conversions, ~115 CONVERT sites, 4 canonical-host additions), and (e) the **new §3 invariants / §4 adjudications / §5 evidence rows**. The canonical numbers in this file are now **93 / 91 / 74 / 41 / 16 / 21-ops-over-17 / 13** and must agree with `plan-overview.md`.

---

## §0. Test-ID registry (the plan's §11.1 contract, referenced from every *Verification* block)

| ID | Asserts | Class | Plan section |
|---|---|---|---|
| **T1** | `ask_questions` is **absent from the built toolset** for a non-root (synthetic meta *allows* `question`, so absence cannot be blamed on the allow-filter) | **gate proof** | §11.1 |
| **T2** | `ask_questions` **is present** for a root with `source_type` ∈ {discord, slack, telegram, whatsapp} | **preservation — signature-adaptive (3rd class)** | §11.1 |
| **T3** | `ask_questions` **absent** for a root with `source_type=None` (a job root is not a user-facing root) | **gate proof** | §11.1 |
| **T4a** | legacy `parent_id == ""` + chat source ⇒ **present** (preservation half) | **preservation — signature-adaptive (3rd class)** | §11.1 |
| **T4b** | legacy `parent_id == ""` + **no** source ⇒ **absent** (gate half) | **gate proof** | §11.1 |
| **T5** | `parent_id="p"` + `source_type="discord"` ⇒ **absent** (fail-closed: a child carrying a chat source is not a root) | **gate proof** | §11.1 |
| **T6** | child report carrying "Open questions for the orchestrator" reaches the parent as a `HumanMessage` (report row → `PROCESS_REPORT` → `ReportInjection` → drain) | preservation — **LANDED** | §11.1 |
| **T7** | parent `send_message` to a `COMPLETED` child takes the `enqueue-revive` branch and the child flips to `RUNNING` | preservation — **LANDED** | §11.1 |
| **T8** | a **legacy** pack (created before the gate) is still answerable — RAM pack, durable shadow, rehydration, CAS; a root **without** the tool answers via `job_answer` | preservation — **LANDED** | §11.1 |
| **T9** | **END-TO-END capability** (discriminating): a chat-root instance whose agent **grants** the category (leader-as-chat-root) ends up with the tool **REGISTERED** (gate admits **and** allow admits); the **same agent as a child** does not | **gate proof** | §11.1 |
| **W3** | **Structural pin (LANDED, Amendment #4)**: inside `daemon/tools/instance.py::create_instance_tools`, the `create_question_tools(...)` **registration precedes** the `_apply_tool_filter(...)` **application** — resolved by **AST** (never a text grep, never a hardcoded line number), so a `tools.allow` entry can never re-admit a gated tool for a non-root. Ships with an **in-test mutant self-check** (the pin must FAIL on an in-memory inverted copy) | **structural pin — gate pack 12 → 13** | §11.1 |
| **W1** | **Naming-honesty rename (LANDED, Amendment #4)**: the T4a preservation case `test_t4a_restore_path_root_with_stamped_row_has_question_tool` → **`test_root_with_threaded_chat_source_type_has_question_tool`**. The old name described a *restore-path* scenario the test does not exercise; the honest name states what it actually asserts (a **root with a threaded chat `source_type`** keeps the tool). The restore-path stamping remains **statically covered** by the **restore-lane threading seam** (`daemon/services/instance_lifecycle.py:4667-4668`/`:4675`/`:4690` — the restore call site threads both kwargs) and by the **W3** AST pin (the threaded values reach the gate before the filter) — **no coverage was lost** | **naming-honesty correction — not a coverage change** | §11.1 |

> **Proof-set classification (council BLK-7) — as amended by Amendment #3.** *Gate proof* = **T1, T3, T4b, T5, T9** — these must be **RED before the change** and GREEN after. *Preservation / regression guards* = **T2, T4a, T6, T7, T8** — GREEN before, GREEN after. A green preservation run is **never** reported as gate proof. **T2 alone only proves the gate admits**; T9 is the only test that proves the *whole* capability chain (gate ∧ allow) admits.
>
> **THIRD CLASS (Amendment #3) — "red-signature / behavioural-preservation via the signature-adaptive harness".** **T2** and **T4a** are preservation guards, but their **pre-change RED signature is not the naive `TypeError`** — it is a *clean assertion failure* produced by the **signature-adaptive harness**. **Correction of the earlier prediction.** The council recorded that the gate-proof tests are RED pre-change because the keyword-only `parent_id` / `source_type` arguments of `create_instance_tools` do not exist yet ⇒ the failure is a `TypeError` on the not-yet-existing kwargs. That prediction was **STALE for T2/T4a only**: a harness that *always* passed the two new kwargs would raise `TypeError` pre-change for a reason that has nothing to do with the behaviour those guards protect. The landed harness is therefore **signature-adaptive** — it passes the two values **only when the signature already supports them**, and otherwise builds the toolset the way the pre-change code path does (`tests/unit/tools/test_question_tool_root_gate.py:44-63`, the HARNESS NOTE). The pre-change build registers the question tool unconditionally, so the preservation guards assert exactly what they are meant to assert ("a legitimate root keeps the tool") and fail with a **clean assertion diff**, not a `TypeError`. The adaptivity **cannot hide a missing implementation**: the dedicated signature pin `test_ac3_parent_id_and_source_type_are_keyword_only` (`test_question_tool_root_gate.py:441`) asserts the two parameters exist **and** are keyword-only, and it is **RED before the change**. The `TypeError` prediction **remains correct for the gate-proof set (T1, T3, T4b, T5, T9)**, whose harness *deliberately* passes the new kwargs so the pre-change failure is the loud `TypeError`.
>
> **T2's anti-vacuity precondition.** T2 is only meaningful if the synthetic meta it builds **allows** `"question"` — otherwise the test would pass for the wrong reason (the allow-filter, not the gate, would be producing the presence). The gate-proof twin **T1** states the same precondition in the other direction ("synthetic meta *allows* `question`, so absence cannot be blamed on the allow-filter"). This precondition is now an **invariant** (§3).
>
> **LANDED status (Amendment #3) — T6 / T7 / T8.** All three are **LANDED** in `tests/unit/services/test_child_report_open_questions_flow.py` (commit **`12291f2a4`**) — **16 cases total**: **T6 × 5**, **T7 × 7**, **T8 × 4** (13 `def` + 1 `@pytest.mark.parametrize` over the 4 terminal statuses ⇒ 5 + 4+3 + 4 = 16). **Mutation-verified 5/5** (five targeted runtime mutations were each detected; the scratch tree was deleted). The **mappings-lane proof** (P3) is **not** in this file — it lives inside the gate pack, `tests/unit/tools/test_question_tool_root_gate.py` (commit **`925f9aced`**), as `test_mappings_lane_threads_the_sources_chat_type` (`:490`).
>
> **Battery (Amendment #4) — fix round 2: 7 pre-existing failures, ZERO new.** The landed round-2 batteries: **gate pack 13P**; **closure sweep 1462P / 5F** (pre-existing); **report-integrity 51P / 20S**; **devops agent 61P / 2F** (pre-existing); **chart audit 46P**; **seal tripwire 24P / 4D**; **flow proofs 16P + 3P**; **baseline self-test 19P**. **TOTAL failures = exactly the 7 known pre-existing, ZERO new.** The **prompt-artifact baseline is 161/161**, `--check` reports **0 diffs**, and the round-2 drift set (**22** changed entries) is a **strict subset** of the 26 files edited in the round.
>
> **Registry scope.** This registry lists **test IDs** only. Decisions are registered by their own `## Pn` sections and by the §3 consolidated invariants. **P12** (prompt-contract decisions, RULING-2) is **new** as of the 2026-10-20 plan review; it is cross-referenced from the plan. **P13** (the leader ratifications **R-a / R-b / R-c**, Amendment #4) is **new** as of 2026-10-20; it is cross-referenced from the plan.

---

## P1 — The root predicate

**Decision.** A *user-facing root* is `(parent_id IS NULL OR parent_id == "") AND (source_type ∈ {discord, slack, telegram, whatsapp})`. The predicate is evaluated **only from values threaded into the tool build** — never from a DB row read at registration time.

**Rationale.**

| Element | Verified source | Why this element |
|---|---|---|
| Root test idiom `(parent_id IS NULL) \| (parent_id == "")` | `daemon/repositories/instance/repository.py:1109` (and `:1134`) — was `:1049-1050` at `1ebdf469f`; the sibling merge shifted it | Legacy rows carry `""`; a bare `IS NULL` test would miss them |
| Chat-source set `("telegram","slack","discord","whatsapp")` | `daemon/repositories/instance/repository.py:919` (`SQLModelInstanceRepository.CHAT_SOURCE_TYPES` — the **class-attr definition**; `:343` / `:346` / `:374` are doc/use sites) — was `:860` at `1ebdf469f` | The instance-domain taxonomy |
| Same 4-member set, independently | `daemon/tools/upgrade_journal.py:3358` (`USER_ORIGIN_CHAT_SOURCE_TYPES`, a **frozenset**) | Two independent taxonomies agree — **no third set to reconcile**; N7 pins the *import* of one of them rather than a third copy |
| `source_type` writer | `daemon/services/instance_lifecycle.py:2389-2390` — `if source_type: instance_metadata["source_type"] = source_type` | **Exactly one** assignment into `instance_metadata` for this key (verified by grep across `daemon/`) |
| `original_source` | inherited by children — `daemon/services/instance_lifecycle.py:2446-2450` (`spawn_result.inherited_source`) | Inherited ⇒ a chat child would look like a root |
| `source_type` is **not** inherited | same writer, guarded by `if source_type:`; no inheritance path exists | Children never carry a chat `source_type` ⇒ the predicate cannot be fooled by inheritance |

**Why threading is mandatory, not stylistic.** At spawn, the tools are built at `instance_lifecycle.py:2137`; the instance row is INSERTed later — the DB write is delegated to `_spawn_instance_db_sync` (called at `:2433-2445`; the helper is defined at `:5343`, builds `Instance(...)` at `:5417` and commits at `:5451`). The `instance_mappings` row is created only *after* spawn returns (`daemon/sources/mapper.py:362` spawn → `:379` mapping insert). A row-reading or mapping-reading gate would therefore evaluate "no row" for **every freshly spawned chat root** and silently strip the tool from the exact instances the commission is meant to preserve.

**Rejected alternatives.**

| Alternative | Why rejected | Cost had it been chosen |
|---|---|---|
| Read the instance row at registration time | Row does not exist yet at `:2137` (DB write `:2433`, commit `:5451`) | Silent breakage of the **primary** path — every new chat root loses the tool. Fails *visibly* as "non-roots denied", so the bug would ship looking correct |
| Read `instance_mappings` at registration time | Same timing defect; the mapping is also keyed per `(source_id, external_user_id)`, not per instance | Same silent primary-path breakage, plus a per-user/per-instance key mismatch |
| Use `original_source` instead of `source_type` | `original_source` **is** inherited (`instance_lifecycle.py:2446-2450`); live `ensemble_dev` rows carry `original_source` values `api` / `experience:*` / `explore:*` — never a chat platform | Every descendant of a chat root would satisfy the predicate ⇒ exactly the outcome the commission forbids |
| Grant to all roots by `parent_id` alone (no chat-source requirement) | Non-chat roots (webhook lane, API lane, internal roots) would keep the tool | The commission's premise — only a human-facing chat root can *ask the human* — is lost; the internal lanes gain a user-facing capability they cannot service |
| Adopt the 3-member `_PLATFORM_INSTRUCTIONS` key set instead | `_PLATFORM_INSTRUCTIONS` (`instance_lifecycle.py:1006-1036`) is a **prompt-formatting** whitelist (discord/slack/telegram); `whatsapp` has no adapter — `constants.py:128-144` lists no `whatsapp:` prefix either, and `daemon/tools/upgrade_journal.py:3337` records the same asymmetry ("`whatsapp` is in the accept set but NO whatsapp adapter exists") | A `whatsapp` root would be denied a tool it *should* have (it is a registered chat source per both taxonomies). Recorded asymmetry, **not** a re-litigation: a whatsapp root passes P1 but receives no platform prompt section — plan §1.4 |

**Invariants created.**
- `instance_metadata["source_type"]` has **exactly one writer** (`instance_lifecycle.py:2390`). A second writer is forbidden — that is precisely what P3 must avoid.
- `original_source` is **never** an input to any root/user-facing predicate.
- The root test is `(parent_id IS NULL OR parent_id == "")` — never a bare `IS NULL`.
- The chat-source set is the 4-member registry set; it is not the 3-member prompt-formatting set.
- **N7 — one canonical chat-source set.** The gate **imports** an existing constant (`SQLModelInstanceRepository.CHAT_SOURCE_TYPES`, `repository.py:919`, a tuple) rather than minting a **third** copy of the taxonomy; the two existing sets (`repository.py:919` tuple, `upgrade_journal.py:3358` frozenset) and the imported set are held equal by a **three-way equality unit pin**.
- **N13 — `whatsapp` stays in the predicate set** (forward-compat). The asymmetry is explicit, not incidental: `whatsapp` is a registered chat `source_type` in both taxonomies but has **no adapter** (`upgrade_journal.py:3337`; `constants.py:128-144` lists no `whatsapp:` prefix; `_PLATFORM_INSTRUCTIONS` = discord/slack/telegram).

**Verification.** **T2** (root + chat source ⇒ present), **T3** (root, no source ⇒ absent), **T4a** (legacy `""` parent + chat source ⇒ present), **T4b** (legacy `""` parent, no source ⇒ absent), **T5** (child carrying a chat source ⇒ absent). Seams: `daemon/tools/instance.py:5895-5900` (gate), `daemon/repositories/instance/repository.py:919` + `:1109` (predicate sources). Plan §1.1, §1.3, §2.2.

> **Evidence gap flagged.** The commission cites "all 14 chat instances are roots with 0 chat-source children" from live data. From this environment the `.env` points at `ensemble_dev` (127.0.0.1:5432), which holds **34 instances / 23 roots / 0 chat-source instances**; `ensemble_prod` is **not reachable** with the dev credential. The claim is therefore **UNVERIFIED — confirm at implementation time** against `ensemble_prod`. The predicate's correctness does not depend on it (it fails closed), but the *blast radius* estimate does.

---

## P2 — The gate: build-time hiding at `daemon/tools/instance.py:5895-5900`

**Decision.** Make the unconditional `create_question_tools(manager, current_instance_id, getattr(manager, "_live_hub", None))` call **conditional** on the P1 predicate; register **nothing** from the `question` category for non-roots. Thread keyword-only `parent_id` and `source_type` into `create_instance_tools` from **both** call sites: spawn (`instance_lifecycle.py:2137`; the values are locals of `spawn_instance` at `:1805` / `:1811`) and restore (`:4690`; `parent_id=meta.parent_id` at `:4675`, `source_type` read from the row at `:4667-4668`).

> **Anchor drift (post-merge).** The restore-path anchors shifted in the sibling merge `e2899f8bd` (merged into this branch at `0e7500a1b`): the restore `create_instance_tools` call `:4618` → **`:4690`**; the `source_type` row read `:4595-4597` → **`:4667-4668`**; `parent_id=meta.parent_id` `:4603` → **`:4675`**. The spawn call (`:2137`), the writer (`:2390`), the gate (`:5895`), the filter call (`:6238`) and the `create_instance_tools` signature (`:2450`) are **unchanged**.

**Rationale.**

- The gate site is verified: `daemon/tools/instance.py:5895-5900` is the only production registration of the `question` category. `create_question_tools` is called from exactly one place in `daemon/` (`instance.py:5895`); the other call sites are tests (`tests/job_queue/test_midflight_qa.py:647,874`; `tests/test_question_tools.py:88`; `tests/test_question_tools_validation.py:131`).
- **Hiding beats refusing.** `_apply_tool_filter` runs at `instance.py:6238` — *after* the gate. A tool that is never appended is genuinely absent from the toolset; a tool that is appended and then refused still consumes a slot in the model's tool list, still appears in `KNOWN_TOOL_NAMES` and the help surface, and still tempts the model into calling it. The commission's requirement is *never registered*, not *refused at runtime*.
- **The precedent exists and is copyable.** `append_platform_context` (`instance_lifecycle.py:1039-1081`) implements exactly this shape: two pure gates over threaded values — Gate 1 `if parent_id is not None: return system_prompt` (`:1075`), Gate 2 `section = _PLATFORM_INSTRUCTIONS.get(source_type)` (`:1078`) — with the docstring stating the rule verbatim: *"callers are responsible for resolving `source_type` from the instance metadata BEFORE calling this appender — at spawn time the instance row has not been INSERTed yet, so a DB lookup here would return `None` and the feature would silently no-op."* (`:1053-1057`). Our gate is the same pattern with the same failure mode already documented.
- The restore path **must** be threaded too: on restart the tool list is rebuilt at `:4690` from the persisted row, so a spawn-only gate would re-expose the tool to every non-root after a daemon restart.

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
- Both `create_instance_tools` call sites (`:2137` spawn, `:4690` restore) thread the same two keyword-only arguments.
- The gate is **necessary** (it is the authoritative *hiding* control) but **not sufficient** (capability also requires the agent to grant the category) — the two-layer model is pinned in **P4**.
- **The gate-before-filter ordering is now TESTED, not merely documented (Amendment #4).** It was previously a **documented invariant** (a stated rule in this record + a code comment). It is now **AST-pinned** by **`TestGateBeforeFilterOrdering::test_gate_registration_precedes_tool_filter_in_build_path`** (`tests/unit/tools/test_question_tool_root_gate.py:627`, class at `:625`): the pin reads the source of `daemon/tools/instance.py`, resolves **both** call sites with the **AST** (never a text grep, never a hardcoded line number — comments naming the symbols and the module-level `def _apply_tool_filter` are both out of scope), and asserts the registration position is strictly smaller than the filter position. The pin carries its own **mutant self-check**: it builds an in-memory **inverted** copy of the real source and asserts the same check **fails** on it, so the pin cannot silently degrade into a no-op that passes on an inverted file. Proven against a real inverted copy in `/tmp` **and** by the in-test mutant. The gate pack is therefore **12 → 13** tests.

**Verification.** **T1** (absence, allow-filter-neutral), **T2**–**T5** (predicate matrix: **T2**, **T4a**, **T4b**, **T5**; **T3** explicitly covers the job-root case), **T9** (end-to-end capability — gate admits **and** allow admits). Seam: `daemon/tools/instance.py:5895-5900`; filter-ordering proof `:6238`; `create_question_tools` signature `daemon/tools/question_tools.py:405-409`. **W3 (LANDED, Amendment #4):** the filter-ordering proof is no longer a comment — it is the AST pin `test_gate_registration_precedes_tool_filter_in_build_path` (`tests/unit/tools/test_question_tool_root_gate.py:627`), with an in-test mutant self-check. Plan §2.1, §2.2, §2.3, §2.5.

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
- **Fail-closed default-kwargs invariant (Amendment #3).** `create_instance_tools` threads the two values as **keyword-only** parameters with **fail-closed defaults** — `parent_id: str | None = None`, `source_type: str | None = None` (`daemon/tools/instance.py:2517-2520`). `_is_user_facing_root` (`daemon/tools/instance.py:2490-2510`) fails closed on **both** halves: a non-empty `parent_id` is never a root, and `(source_type or "") in CHAT_SOURCE_TYPES_FOR_ROOT` means a **missing or unknown** `source_type` is never a root. Consequence: **any call site that forgets to thread the values silently yields NO tool** (the safe direction), never a spurious grant. A caller that threads only one of the two also fails closed. This is why the gate could be adopted without auditing every call site — but it is also why an *un-threaded* chat root is a **silent false negative**, not a loud failure.
- **Remaining `source_type`-less spawn lanes (recorded, not fixed — Amendment #3).** Two spawn call sites still omit `source_type`: (a) `daemon/sources/adapters/slack/thread_manager.py:217` (`ThreadManager.get_or_create_instance` at `:186`) — **DEAD**: defined but with **no production caller** (the live `get_or_create_instance` callers are `mapper.get_or_create_instance` from `daemon/sources/registry.py:1029` and `daemon/sources/scheduler.py:1064`); (b) `daemon/sources/mapper.py:452` (`handle_incoming_message`) — **test-only** (no production caller; only `tests/test_sources_mapper.py`), and it defaults the source to `"webhook"` (`mapper.py:422`), which is **not** a chat source, so the predicate fails closed there anyway. Neither lane is in the commission's edit set. **Action when revived: thread the source type, or delete the lane** — reviving either without threading makes it a **silent false negative** under P1 (a chat-bound root that never gets the stamp ⇒ never gets the tool).

**Verification.** Plan §3. **T2** covers the predicate's positive branch. **The mappings-lane case is LANDED — the test EXISTS.** It lives **inside the gate pack** `tests/unit/tools/test_question_tool_root_gate.py` as `test_mappings_lane_threads_the_sources_chat_type` (`:490`, class at `:477`, labelled **AC6**), landed in commit **`925f9aced`**; it asserts the mappings route hands the source's chat platform type to the spawn call so the chat-bound root is stamped by the **single** writer. **The plan's DoD grep substitute is OBSOLETE and is dropped from P3** — the grep-based check is superseded by the executed test, and the plan's DoD reference is to be amended (not this file's job; recorded here so both files agree). Cross-check seam: `daemon/routers/mappings.py:114-117` vs `daemon/sources/mapper.py:362-365` vs `daemon/sources/registry.py:1029-1035`.

---

## P4 — `meta.json` plan (defense in depth) — the two-layer capability model

**Decision.** Effective capability is the **intersection** of two independent layers:

```
effective capability  =  { the instance is a user-facing root }  ∩  { the agent's meta grants the "question" category }
```

- The **GATE** (P2) is the authoritative **hiding** control: a non-root **never** gets the tool registered, whatever its agent's meta says. The gate runs **before** `_apply_tool_filter` (`instance.py:6238`) ⇒ an allow-list entry can never re-admit a gated tool. The gate is **necessary**.
- The **ALLOW-LIST** (`meta.json`) is the **capability grant**: it decides whether a *root* that passes the gate actually receives the tool. The allow-list is **sufficient** — but only for a root.

The `meta.json` edits (defense in depth + prompt hygiene — the authoritative control is the P2 gate):

| Agent | `tools.allow` | `innate_skills` | Note |
|---|---|---|---|
| `agents/leader/meta.json` | **KEEP** `"question"` | **REMOVE** ⇒ `["coordination","chart","todo"]` | the already-red pin `tests/test_innate_skills_refactoring.py:67` turns **GREEN** |
| `agents/devops/meta.json` | **KEEP** `"question"` | **REMOVE** ⇒ `["dynamic-skill","chart","todo"]` | `tests/unit/test_devops_agent.py:165` must be **edited** to that exact list |
| `agents/ari/meta.json` | **ADD** `"question"` | **ADD** `"question"` | the root-teaching grant (capability **+** teaching) |
| `agents/project-manager/meta.json:64` | **unchanged** (`"question"` inside the `deny` array) | unchanged | already compliant — **no edit** |
| `agents/critic/meta.json` | **unchanged** | unchanged | never held it — **no edit** |
| `agents/watcher/meta.json` | **unchanged** (`allow: []` ⇒ default-open universe) | unchanged | the gate strips it; pinning the allow list stays **optional hygiene** |

**LANDED (Amendment #3) — the devops prompt contract is now in place.** The `meta.json` grants above were only the **defense-in-depth** half; the fix round also closed the **prompt-contract** half for `devops` (approver item **B3**). `agents/devops/rule.md` now carries the canonical `## Open questions for the orchestrator` section, copied **verbatim** from the `agents/tester/rule.md` home, and devops' ask-family sites were rewritten to the **non-root** form (report the blocked action as an open question — **never ask the user directly**); `agents/devops/soul.md`'s production-approval framing was reconciled to the same channel. **Anchor drift — recorded, not a defect.** The briefed devops line numbers were **STALE**; the family was adjudicated **by construct**, not by the briefed numbers: the real sites are `agents/devops/rule.md` **9 / 32 / 91 / 92 / 110 / 114 / 156 / 164 / 203** and `agents/devops/soul.md` **46 / 49 / 77**. `agents/devops/rule.md` is **sealed** ⇒ it took an **adjudicated re-pin** (constant `febf0e89…`, §5). **devops was the 14th canonical home** (canonical homes **13 → 14** at the fix round; **16** after fix round 2 — `agents/maintenancer/rule.md` and `agents/project-manager/rule.md` joined, see Amendment #4); the sealed baseline itself stays **35** entries.

**Rationale.** Every entry below was read from the files:

| Agent | Verified state (pre-change) | Effect of the ruling |
|---|---|---|
| `leader` | `innate_skills` (`:8`) = `["coordination","chart","todo","question"]`; `tools.allow` (`:14-15`) contains `"question"` | the `innate_skills` door closes; the **capability** door stays open |
| `devops` | `innate_skills` (`:8`) = `["dynamic-skill","chart","question","todo"]`; `tools.allow` (`:12`) contains `"question"` | same |
| `ari` | `innate_skills` (`:9`) = `["job-orchestration","chart","todo"]`; `tools.allow` (`:11-38`) — **no** `"question"`; `deny` (`:39`) | holds **neither** today ⇒ the grant is a real add |
| `project-manager` | `deny` array opens at `:54`; `"question"` at `:64` | already compliant — **no edit** |
| `critic` | `allow` (`:13`) = `["read_file","image","design"]`; `deny` (`:14`) | never held it — **no edit** |
| `watcher` | `allow` (`:22`) = `[]` | empty allow ⇒ **default-open universe** ⇒ it holds the tool today; the gate strips it |

**Why the two layers cannot collapse into one.** The gate answers *"may this instance ever see the tool?"*; the allow-list answers *"does this agent have the capability?"*. A single layer would either (a) leave the default-open universe (`watcher`'s empty allow, any future agent with an empty allow) holding the tool — the exact population the commission targets — or (b) strip the category from the agents that legitimately need the *answering* side of the contract.

**Why leader and devops KEEP the category in `tools.allow`.** Both must still be able to **answer a root's pack**: a legacy or in-flight pack is answered through the `question` surface (P9), and the leader's answering path must not depend on a tool it no longer has. Removing the category from `tools.allow` would (a) make the leader's answering path depend on a tool it no longer has, and (b) make a **leader bound to a chat source** unable to ask at all — it would pass the P1 gate and then be stripped by `_apply_tool_filter`, contradicting the policy that "roots still ask". The `innate_skills` entry, by contrast, is *teaching* (it injects the shared skill text into the prompt) — that text is the unconditional-ask teaching the commission must retract for non-roots, so it is the half that is removed.

**Why both doors matter, mechanically.** `INNATE_SKILL_TOOL_CATEGORIES` (`daemon/tools/instance.py:158-164`) maps `"question" → ["question"]` (`:163`), and `expand_allow_for_innate_skills` (`:167`) appends those categories to the allow list — except privileged ones, and `"question"` is **not** privileged (`_tool_registry.py:239-339`). So an `innate_skills` entry alone is sufficient to grant the category; a `tools.allow` entry is a second, independent grant. Removing only the `innate_skills` door leaves the capability door open — which is exactly the intent under the two-layer model.

**The `meta.json` edits are defense-in-depth, not the control.** The authoritative control is the P2 gate (plan §2.1, §2.6): with the gate in force, `tools.allow` cannot re-admit a tool that was never registered.

**Documented scoping limitation (RULING-1).** A user-facing root whose agent does **not** grant the category **cannot ask**. The mappings API can bind **any** agent to a chat source; today **no non-ari agent is known to be chat-bound** ⇒ the limitation is **LATENT, not active**. It is disclosed as risk **F3** and carried as a final-report line.

**Rejected alternatives.**

| Alternative | Why rejected | Cost had it been chosen |
|---|---|---|
| Narrow the grant to **`ari` only** (strip `"question"` from leader's and devops' `tools.allow` as well) | With the category stripped from the allow-list, effective capability collapses to **`{ari}` only** ⇒ a **chat-bound leader** would pass the P1 gate and then be stripped by `_apply_tool_filter` ⇒ it could not ask at all — directly contradicting "roots still ask" | The capability becomes agent-fixed rather than role-fixed: whether an agent can ask is decided by *which agent it is*, not by *whether it is a user-facing root*. The mappings lane (P3) would produce a root that silently cannot ask |
| Edit `meta.json` only; skip the code gate | `watcher`'s empty allow ⇒ default-open universe ⇒ it holds the tool with no `meta.json` change at all; and any future agent with an empty allow inherits it | The capability keeps leaking into the default-open universe — the exact population the commission targets |
| Gate only; skip `meta.json` | The prompt surface still tells `leader`/`devops` they have a question skill and a question tool they no longer have | Prompt/behavior drift: agents would try to call a tool that is absent — a hallucinated-call loop rather than a clean absence |
| Remove `"question"` from leader's `tools.allow` **and** leave it in `innate_skills` | `innate_skills` alone re-grants the category (`instance.py:163`) ⇒ the removal is inert | The "removal" would be theatre: the capability survives through the other door, and the false confidence would survive longer than the code |
| Grant `"question"` to `ari` in `tools.allow` **only** (no `innate_skills`) | The skill text would not land in ari's prompt ⇒ ari gets the tool with no teaching, and the root-scoped skill rewrite (P5) would never reach the only agent that needs it | The chat root acquires a pause-and-answer capability it has no instructions for — the highest-risk population (R1 in the plan's risk register) |
| Edit `project-manager/meta.json` "for symmetry" | Its `"question"` is inside the **deny** array — already correct | A no-op edit churns a file that needs no change, and risks flipping deny→allow by accident |
| Pin `watcher`'s allow-list as part of this commission | Optional hygiene; the gate already strips the tool | Scope creep into an unrelated agent's capability surface, with its own regression surface (watcher currently *relies* on the default-open universe) |

**Invariants created.**
- The **two-layer model** holds: the gate is the authoritative *hiding* control; the allow-list is the *capability* grant; effective capability is their intersection.
- `leader` and `devops` **KEEP** `"question"` in `tools.allow` but **LOSE** it from `innate_skills` (leader ⇒ `["coordination","chart","todo"]`; devops ⇒ `["dynamic-skill","chart","todo"]`).
- `ari` holds `"question"` in **both** `tools.allow` and `innate_skills` (the grant is deliberately doubled — capability + teaching).
- `project-manager`'s `"question"` **deny** entry is preserved verbatim.
- `critic/meta.json` is untouched.
- `innate_skills` is **never** set to `[]` to suppress a skill (an empty array is treated as absent — `loader.py:856-858` — and triggers the legacy scan).
- `meta.json` is **not** covered by the prompt-artifact baseline (`daemon/tools/prompt_artifacts.py:119-140` covers depth-2 `agents/<agent>/<artifact>.md` only; `agents/.prompt-artifacts.sha256` has **0** `meta.json` entries) ⇒ **no** baseline regeneration is required for meta.json edits alone.
- **The pre-change census is exactly THREE agents (Amendment #3) — recorded as fact, not re-derived.** Before this commission, exactly **three** agents could hold `ask_questions`: **`leader`** (`tools.allow` + `innate_skills` both listed it), **`devops`** (same), and **`watcher`** (`tools.allow: []` ⇒ the **default-open universe** ⇒ it held the tool with **no** meta entry at all). `ari` held **neither** before the grant; `project-manager` **denied** it; `critic` never held it. This is the census the two-layer model is designed against: the commission's fix has to close the **explicit** doors (leader, devops) **and** the **implicit** door (watcher's empty allow ⇒ default-open universe) — and the gate is what closes the third.

**Verification.** Plan §4, §10.3. Existing pins that move: `tests/test_innate_skills_refactoring.py:67` expects `leader` `innate_skills == ["coordination","chart","todo"]` — currently **RED** (leader still lists `question`), turns **GREEN** after P4. `tests/unit/test_devops_agent.py:165` expects `devops` `innate_skills == ["dynamic-skill","todo"]` — currently **RED** for a *different* reason (devops carries `chart` **and** `question`); it must be **edited** to `["dynamic-skill","chart","todo"]` (dropping `chart` is out of scope) — it does not turn green by removal alone. **T2** (gate admits a root), **T4a** (legacy `""`-parent root admitted), and **T9** (end-to-end: a chat-root instance of a **granting** agent — leader-as-chat-root — ends up with the tool **REGISTERED**; the same agent as a **child** does not) are the capability-side proofs. A new pin must assert the ari grant and the leader/devops `innate_skills` closure.

**The ari row is MUST-EDIT (Amendment #3) — leader-ratified.** `tests/unit/test_ari_worker_integration.py:345` asserts an **exact set** on ari's `innate_skills` (`assert set(meta.get("innate_skills", [])) == expected_skills`). The grant (ari gains `"question"` in `innate_skills`) legitimately breaks that assertion ⇒ a **constants-only** edit is **required and correct**. The plan's §11.2 had claimed **NO EDIT** (membership-only) — that was a **plan error**, surfaced by the implementation lane and **ratified by the leader**. The landed file now asserts the granted set. Record it as a **MUST-EDIT** row, not as a "no edit expected" row.

**T2's anti-vacuity precondition (Amendment #3).** T2 proves the *gate* admits a root only because the synthetic meta it builds **allows** `"question"` — otherwise the presence could be produced by the allow-filter alone and the test would pass for the wrong reason. The twin **T1** states the same precondition in the other direction. This precondition is recorded as an **invariant** (§3).

**T9's coupling to the real leader meta (Amendment #3).** The capability-E2E proof (leader-as-chat-root ⇒ **REGISTERED**; same agent as a **child** ⇒ not) reads the **real** `agents/leader/meta.json` rather than a synthetic meta. Consequence: **a future change to the leader's allow-list or `innate_skills` turns T9 red** — that is an **alarm, not flakiness** (it means the leader can no longer be the granting agent for the capability-E2E pair, and the pair must be re-verified). This is recorded as an **invariant** (§3) so a future reader does not "fix" it by loosening the assertion.

---

## P5 — Root-scoped skill rewrite

**Decision.** Rewrite `agents/_prompt_system/innate-skills/question/skill.md` so it teaches **root behaviour only** ("you may ask the human when you are the user-facing root"), plus the explicit **chat-lane nuance**: in a chat lane the user replies in normal chat, so **prefer asking in the reply text**; use the tool only when a structured answer channel exists. Also state the **non-root half**: if you have no `ask_questions` tool you are a non-root — decide per your own mandate and, if genuinely blocked, end your report with "Open questions for the orchestrator".

**Rationale.** The skill is a **single shared file** injected by name — `daemon/loader.py:841-847` (`_resolve_innate_skill_paths` resolves `agents/_prompt_system/innate-skills/<name>/skill.md` for each entry in `meta.innate_skills`). The current file is 53 lines and teaches the *unconditional* use of the tool ("`ask_questions()` is your direct line to the user. Ask whenever you need a decision…"), which becomes **wrong** for every non-root after P2/P4. The teaching home must match the new capability boundary. Because the file is shared, one rewrite fixes every holder — leader and devops stop receiving it when their `innate_skills` entry is removed (P4), and ari starts receiving it when its entry is added.

**Chat-lane nuance is load-bearing, not stylistic.** The chat lane delivers the user's reply as a **normal chat message**, not as a structured answer to a paused pack. A root that pauses for an answer in a chat lane can strand the turn. Hence: prefer the reply text; use the tool only where a structured answer channel exists.

**F6 — a teaching gap, not a capability gap.** After P4, `leader` and `devops` lose the **skill text** (the `innate_skills` door closes) but **keep the capability** (the `tools.allow` door stays open). The leader can still **answer** a root's pack; it simply no longer receives the unconditional-ask teaching — which is the point of the commission. The cost is that a leader-as-chat-root is now a *capable but untaught* asker; that is why the shared skill text is rewritten to teach the **root** behaviour explicitly (this section) and why the clause-family sweep carries the **conditional** wording (P12).

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

**Verification.** Plan §6, §10.4, §10.5. **T6** covers the delivery path (report row → `PROCESS_REPORT` → `ReportInjection` → drain into the parent as a `HumanMessage`; seams `child_reports.py:3882-3883`, `:3985`, `:4152-4158`, `daemon/graph.py:8721-8745`). **LANDED (Amendment #3):** T6 is **T6 × 5 cases** in `tests/unit/services/test_child_report_open_questions_flow.py` (commit **`12291f2a4`**, class `TestT6OpenQuestionsReachParent` at `:317`) — real `ChildReportsService` + `DependencyBus`: the `COMPLETION_REPORT` row is addressed to `parent_id`, the `PROCESS_REPORT` task is created for the parent, the `ReportInjection` row keeps the section **verbatim**, the parent drains the open-questions from the injection **alone** (production `claim_for_injection` drains it **exactly once**), and a **no-section control** (a report without the section carries no section).

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

**Verification.** Plan §7, §7.1, §7.2, §10.6. **T7** covers the revive branch (`instance.py:1418-1424` → `instance_messaging.py:2369-2381`). **T6** covers the delivery path. **LANDED (Amendment #3):** T7 is **T7 × 7 cases** in `tests/unit/services/test_child_report_open_questions_flow.py` (commit **`12291f2a4`**, class `TestT7ParentInstructionRevivesTerminalChild` at `:537`) — one `@pytest.mark.parametrize` over the **4** terminal statuses (COMPLETED / TERMINATED / ERROR / FAILED) plus a **paused control** (a paused child is **not** revived), **real tool routing** (`send_message` to a terminal target routes to the revive branch), and a **paused-reject** case (the tool refuses a paused target). **Caveat for the tester phase (disclosed):** the **next-turn execution** of the revived child is **not** driven by this file — the proof is at the **shared-helper** level (`instance_messaging._prepare_enqueued_message`); the plan wanted T7/T8 spread across other files, but the one-file brief constrained placement (placement deviation disclosed, not a scope reduction).

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
| Job-scoped answer tool | `daemon/tools/job_queue.py:3403` (`async def job_answer` — the **def site**; the tool's docstring entry is at `:620`; the council's `:770` is a *reference* inside that docstring) , `daemon/tools/_tool_registry.py:981` (`"job_answer"` in `KNOWN_TOOL_NAMES`) |

**N6 / F1 — the mid-flight QA and answer-event fan-out becomes ROOT-ONLY.** Only a root can mint a pack: the **sole production pack writer** is `daemon/tools/question_tools.py:502` (`manager._question_manager.set_question_pack(...)`), and after P2/P4 only a user-facing root holds the tool. Therefore the **mid-flight QA relay lane** and the **answer event fan-out** are exercised by roots alone — the fan-out stops being a two-sided (asker + answerer) lane and becomes a root-side lane. **No answering path is removed** (the `set_answers` CAS, the durable shadow, boot rehydration, `resolve_answer_target`, `job_answer` and the instance-addressed route are untouched — see the table above); what changes is *who can originate* a pack. This belongs in the **migration / deploy story** (plan §15) and is carried as risk **F1** in the final report — a deploy that flips the gate changes the shape of the mid-flight QA traffic, not its availability.

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
- Only **roots** can mint a pack (sole production writer `question_tools.py:502`) ⇒ the mid-flight QA / answer-event fan-out is **root-only**; this changes the *origin* of the traffic, never the *answering* surface.

**Verification.** Plan §9, §15. **T8** is the dedicated test: legacy pack answerable end-to-end (RAM + shadow + rehydration + CAS) and `job_answer` resolving a pack with **no** `ask_questions` tool in play. Existing packs to re-run: `tests/job_queue/test_midflight_qa.py`, `tests/test_question_manager.py`, `tests/job_queue/test_job_answer_tool.py`, `tests/test_question_pack_boot_rehydrate.py`. **LANDED (Amendment #3):** T8 is **T8 × 4 cases** in `tests/unit/services/test_child_report_open_questions_flow.py` (commit **`12291f2a4`**, class `TestT8LegacyPackStaysAnswerableWithoutTheQuestionTool` at `:857`) — a legacy pack answered **after the RAM store is gone** (shadow rehydrate, original `pack_id` preserved), a **duplicate answer** that is a **CAS loser**, a **runtime spy** proving **zero** tool-registry consultations on the answer path, an **AST separation** proof over `answer_helper` / `question_manager` / `midflight_qa`, and a **real** root-without-the-category instance that **holds no** `ask_questions` tool. **Caveat for the tester phase (disclosed):** the **`job_answer` WRAPPER** (the plan assigned it to `tests/job_queue/test_job_answer_tool.py`) is **not** driven by this file — the proof is at the **shared-helper** level; the placement deviation is disclosed, not a scope reduction.

---

## P10 — Sequencing

**Decision.** This commission's **own branch and worktree already exist** and are **reused**, not re-created: branch **`feature/hide-question-tool-nonroot`**, worktree **`/home/nea/ensemble-src-wt-hidenonroot`** (the branch is **locked** by the worktree — `git worktree` refuses to check the same branch out twice, and the plan artifacts are committed on it at `8fa3319f7`). Once the sibling lane has merged into `latest`, run **`git merge latest` INTO this branch** — **MERGE, NOT REBASE**: a rebase rewrites the commit sha and breaks the recorded blob-sha256 provenance of the plan artifacts. Our `instance_lifecycle.py` edits stay **additive and minimal** (two keyword-only arguments). The sibling's worktree is **read-only** to us. Both KV rows **already exist** — census `wt.active.feature/hide-question-tool-nonroot.hidenonroot` and claim `wt.claim.hide-question-tool-nonroot` (with the 6 `overlap_files` + `blocked_by`) ⇒ Phase 0 KV work is **VERIFY / MERGE, not create**.

> **The base-sync now happens TWICE (Amendment #3).** The **first** base-sync already happened — merge commit `0e7500a1b` brought the sibling's earlier commit `e2899f8bd` into this branch and shifted the restore-path anchors. The sibling lane has since **grown** (7 commits ahead of `latest`, tip `86fe0e03a`) and `latest` itself has **moved** (`843b669c5`). A **SECOND base-sync re-entry step** is therefore owed — see **"The second base-sync re-entry step"** below. It is an **explicit, numbered operation**, not an implicit part of "merge before staging".

**Rationale.** The sibling rewrites `answer_helper.py`, `instance_lifecycle.py`, `repository.py`, `api.py`, `config.py` and `routers/instances.py`, and adds `pool_orchestrator.py`, `question_pause_set.py` and `stale_pause_sweep.py` — `instance_lifecycle.py` is the exact file we must thread two keyword arguments through, and `answer_helper.py` is a P9 "defense in depth" seam. Concurrent edits to `instance_lifecycle.py` produce either a merge conflict inside a load-bearing lifecycle function or a silent semantic clash between "the pause is a no-op for a non-root" and "the pause resumes a legacy pack". (**Amendment #3:** the earlier sentence in this record listed `question_manager.py` and `instance_messaging.py` as sibling-touched files — the **corrected** overlap payload no longer includes either; see the table below and KV `wt.claim.hide-question-tool-nonroot.overlap_CORRECTED`. The *reason* for the wait is unchanged.)

**Verified state at 2026-10-20 — updated by Amendment #3 (fix round landed; `latest` moved).**

| Fact | Value | How verified |
|---|---|---|
| This branch | `feature/hide-question-tool-nonroot` @ **`4088be633`** | `git rev-parse --abbrev-ref HEAD` / `git log --oneline -1` in the worktree |
| This worktree | `/home/nea/ensemble-src-wt-hidenonroot` — plan artifacts committed at `8fa3319f7` (2 files) | `git log --oneline` |
| Fix round | **LANDED** — 9 commits `21bccf157 → 2f28cb969 → 12291f2a4` (devops contract B3 · sweep completion B4 · charter B5 + escalate W3 · 3 seal re-pins · 2 baseline regens · devops residual · devops residual re-pin · flow proofs), **plus fix round 2**: `b7e3a0808` (tester — next-turn execution 3P + `job_answer` wrapper +2, **test-only**) `→ 4088be633` (semantic-class sweep, 26 prompt files, 3 seal re-pins, baseline regen, W3 pin) | `git log --oneline 21bccf157..HEAD` |
| `latest` | **`843b669c5`** — *dev-strategy v1.4.0, developer-docs only* | `git rev-parse latest` |
| `origin/latest` | **`1ebdf469f`** — **origin is BEHIND** `latest` | `git rev-parse origin/latest` |
| Sibling lane | `fix/question-pause-resume-asymmetry` — **7 commits ahead**, tip **`86fe0e03a`**, **13 files** | `git rev-list --count <merge-base>..86fe0e03a` = 7; `git diff --name-only <merge-base>..86fe0e03a` = 13 |
| Sibling file set (corrected) | `instance_messaging.py` is **NO LONGER** in their diff; new files `daemon/services/pool_orchestrator.py`, `daemon/services/question_pause_set.py`, `daemon/services/stale_pause_sweep.py` | `git diff --name-only <merge-base>..86fe0e03a` (overlap payload corrected per the leader; see KV `wt.claim.hide-question-tool-nonroot.overlap_CORRECTED`) |
| Sibling in `latest`? | **NO** — merge-base of `86fe0e03a` and `latest` is `1ebdf469f` ⇒ the sibling is **still un-merged** to `latest` ⇒ the wait is **live** | `git merge-base 86fe0e03a latest` |
| ⇒ `git merge latest` | **still required**, and now **for the second time** (the first was `0e7500a1b`) | the merge is one-way into this branch |
| Worktree cleanliness | clean **except** the foreign `agents/shared/planning/designer-agent/install-audit.jsonl` (not ours — untouched) | `git status --porcelain` |
| KV rows | both census + claim rows **already exist** ⇒ Phase 0 is **verify/merge**, not create | shared meta KV |
| Branch-diff reconciliation | `0e7500a1b..4088be633` = **93 files** = **74** agents prose + **3** `meta.json` + **1** regenerated baseline + **3** daemon + **3** docs + **7** tests + **2** planning docs ⇒ **commission surface 91** (excluding the plan's own docs). Under `agents/` + `docs/` alone: **81 files** = 74 + 3 + 1 + 3 | `git diff --name-only 0e7500a1b..4088be633 \| wc -l` |

**The second base-sync re-entry step (Amendment #3) — an explicit, numbered operation.**

1. **`git merge latest` INTO this branch** — **never rebase** (a rebase rewrites the commit shas and breaks the recorded blob-sha256 provenance of the plan artifacts at `8fa3319f7`).
2. **Expect the dev-strategy merge** from `843b669c5` (dev-strategy v1.4.0 — developer-docs only). The council's **read-only merge-file simulation was CLEAN**: the clause survives the merge, **no `"or ask"` residue** remains, and the `developer` ↔ `developer[v2]` **twin parity** holds. **Still re-verify on the real merge** — the simulation is evidence, not proof.
3. **RE-GREP every anchor post-merge.** Known drifts: `CHAT_SOURCE_TYPES` class-attr definition `repository.py:919` → **`:1000`** (measured at the sibling tip `86fe0e03a`); the restore-path `create_instance_tools` call `:4690` → **~`:4759`** (briefed projection; **measured `:4773`** at the sibling tip). Both are **projections** — re-derive them from the real merged bytes rather than trusting either number.
4. **Re-run the targeted suites** — the gate pack (`tests/unit/tools/test_question_tool_root_gate.py`), the flow proofs (`tests/unit/services/test_child_report_open_questions_flow.py`), and the prompt/seal pins — **before any merge to `latest`**. **Use the worktree's own venv**: the main checkout's `.venv` resolves to `latest` (editable install) and will import the wrong tree. The worktree has its own `.venv` (created for this round).
5. Only then may the branch be merged to `latest` — and **staging stays blocked** until `HEAD == origin/latest` (the tip-identity guard), which requires origin to catch up with `843b669c5`.

**The merge shifted our anchors — re-verified at `0e7500a1b` (authoritative).**

| Anchor | At `1ebdf469f` | At `0e7500a1b` |
|---|---|---|
| `instance_lifecycle.py` — spawn `create_instance_tools` call | `:2137` | `:2137` (unchanged) |
| `instance_lifecycle.py` — restore `create_instance_tools` call | `:4618` | **`:4690`** |
| `instance_lifecycle.py` — restore `source_type` row read | `:4595-4597` | **`:4667-4668`** |
| `instance_lifecycle.py` — restore `parent_id=meta.parent_id` | `:4603` | **`:4675`** |
| `instance_lifecycle.py` — `source_type` writer | `:2390` | `:2390` (unchanged) |
| `instance_lifecycle.py` — `_PLATFORM_INSTRUCTIONS` | `:1006` | `:1006` (unchanged) |
| `instance_lifecycle.py` — `append_platform_context` | `:1039-1081` | `:1039` (unchanged) |
| `daemon/tools/instance.py` — `create_question_tools` gate | `:5895-5900` | `:5895` (unchanged) |
| `daemon/tools/instance.py` — `_apply_tool_filter` call | `:6238` | `:6238` (unchanged) |
| `daemon/tools/instance.py` — `create_instance_tools` signature | `:2450` | `:2450` (unchanged) |
| `daemon/tools/instance.py` — `INNATE_SKILL_TOOL_CATEGORIES["question"]` | `:163` | `:163` (unchanged) |
| `daemon/repositories/instance/repository.py` — `CHAT_SOURCE_TYPES` | `:860` | **`:919`** (the class-attr definition; `:343` / `:346` / `:374` are doc/use sites) |
| `daemon/repositories/instance/repository.py` — root-test idiom | `:1049-1050` | **`:1109`** (and `:1134`) |
| `daemon/tools/upgrade_journal.py` — `USER_ORIGIN_CHAT_SOURCE_TYPES` | `:3358-3360` | `:3358`; **`:3337`** carries the "NO whatsapp adapter exists" comment |
| `daemon/tools/job_queue.py` — `job_answer` | `:620` (docstring) | docstring `:620`; **`async def job_answer` at `:3403`** |

> **Correction to the commission brief (carried forward).** The brief and the KV claim row described the sibling worktree as *"0 commits ahead of latest, clean"*. It was 0 commits ahead but **not** clean — it carried 7 modified tracked files (incl. `daemon/services/instance_lifecycle.py`, the file we must thread) + 3 new untracked (`daemon/services/question_pause_set.py`, `daemon/services/stale_pause_sweep.py`, `tests/unit/test_question_pause_resume_asymmetry.py`). That work has since landed as `e2899f8bd` and is merged into this branch; the *gate* (wait for `latest`) is unchanged, and the anchor drift it caused is now **re-verified** above.

**Rejected alternatives.**

| Alternative | Why rejected | Cost had it been chosen |
|---|---|---|
| Create a **new** branch + worktree | This commission's branch and worktree already exist and the branch is **locked** by the worktree; the plan artifacts are already committed on it (`8fa3319f7`) | A second worktree for the same branch is refused by git; a differently-named branch would split the plan artifacts from the implementation and break the recorded blob-sha256 provenance |
| **Rebase** this branch onto `latest` after the sibling lands | A rebase rewrites the commit sha ⇒ the recorded blob-sha256 provenance of the plan artifacts (`8fa3319f7`) no longer resolves, and the audit trail (git-log last-touch attribution for the seal re-pins) is rewritten underneath the reviewers | Silent provenance loss — the very thing the seal re-pin discipline (`§3` #30) depends on for its git-log attribution |
| Implement in parallel on a branch from `latest` now | The sibling's `instance_lifecycle.py` rewrite was uncommitted and unmerged; our two keyword arguments would land in a file that is about to change | A merge conflict inside `spawn_instance` / `_restore_instance`, resolved under time pressure, with a silent risk of dropping one of the two threaded values (⇒ a non-root silently keeps the tool, or a chat root silently loses it) |
| Implement in the sibling's worktree | The commission forbids touching it | Destroying in-flight sibling work |
| Merge our branch first "to be ready" | Reverses the dependency and pushes the merge conflict onto the sibling | Same conflict, wrong direction — and it makes the sibling's author resolve our param threading without the gate's context |
| Skip the gate and ship the `meta.json` edits first | `watcher` (empty allow ⇒ default-open universe) would keep the tool; the leak the commission targets stays open | A partial release that appears to fix the problem while the default-open universe still holds `ask_questions` |
| **Create** the Phase-0 KV rows | Both rows already exist; creating them would clobber the leader's `overlap_files` / `blocked_by` keys | Loss of the coordination record; two agents believing they own the lane |

**Invariants created.**
- **Phase 0 is a hard wait**: no implementation file is edited until `fix/question-pause-resume-asymmetry` is merged into `latest`; then `git merge latest` is run **into this branch** (merge, never rebase).
- **The base-sync happens TWICE and the second one is an explicit numbered step** (Amendment #3) — merge `latest` in ⇒ expect the dev-strategy merge ⇒ **re-grep every anchor** ⇒ **re-run the targeted suites with the worktree's own venv** ⇒ only then merge to `latest`. It is **not** an implicit part of "merge before staging".
- **The main checkout's `.venv` is NEVER used for this branch's tests** (Amendment #3) — it is an editable install that resolves to `latest`, so it imports the **wrong** `daemon` tree. The worktree has its own `.venv`; every test command run for this commission must use it.
- The branch `feature/hide-question-tool-nonroot` and worktree `/home/nea/ensemble-src-wt-hidenonroot` are **reused as-is**; the worktree is **not** re-created and the branch is **not** renamed.
- Every anchor in this record is re-verified after each merge — the sibling merge shifted the restore-path anchors (`:4618` → `:4690`, `:4595-4597` → `:4667-4668`, `:4603` → `:4675`).
- Our `instance_lifecycle.py` edits are **additive**: two keyword-only arguments, no restructuring of `spawn_instance` or `_restore_instance`.
- The sibling worktree is **read-only** to us.
- The KV claim row is **merged**, never clobbered — the leader's `overlap_files` / `blocked_by` keys survive.
- Phase 0 KV work is **verify/merge**, never **create**.

**Verification.** Plan §12.1, §12.2, §12.3. The gate is verified by `git merge-base --is-ancestor 86fe0e03a latest` (currently **false** — the merge-base of the sibling tip and `latest` is `1ebdf469f` ⇒ **the wait is live**) and, once true, by `git merge latest` on this branch followed by a re-read of every anchor in the table above. The branch exists locally as `feature/hide-question-tool-nonroot` and is locked by the worktree. **Amendment #3:** the **second** base-sync adds four more checks on top — (2) the expected dev-strategy merge from `843b669c5` (clause survives, no `"or ask"` residue, twin parity), (3) the **re-grep of every anchor**, (4) the **targeted suite re-run with the worktree's own venv**. Staging remains blocked until `HEAD == origin/latest` (tip-identity guard) — and `origin/latest` is currently **`1ebdf469f`**, i.e. **behind** `latest` (`843b669c5`).

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
- **N8 — legacy unstamped rows are NOT backfilled.** Rows created before the `source_type` stamp existed (the writer dates to **2026-08-12**) carry no `source_type` ⇒ they do not satisfy the P1 predicate ⇒ a root restored from such a row does **not** get the tool. This is **pre-existing** behaviour, explicitly **out of scope**, and a backfill is **forbidden**: it would make the migration delta **non-empty** and break the **EMPTY-delta** premise this section rests on. The "a root restored from the DB keeps the tool" claim is true **only** for a root whose row **carries** the stamp.

**Verification.** Plan §13. Stage-time gates: the tip-identity guard (L1 `HEAD == refs/remotes/origin/latest`; a non-tip tree exits **78** `non-tip-tree`) and the provenance sidecars (re-hashed at stage, never trusted). Post-stage: migration-delta check `git diff <base>..<head> -- daemon/migrations/versions/` must be empty; sha 3-way match (staged artifact vs manifest vs build); `current` symlink unchanged; live daemon PID/epoch unchanged. The N8 no-backfill rule is verified by the **empty** `git diff … -- daemon/migrations/versions/` output — a single new migration file would fail this gate.

---

## P12 — Prompt-contract decisions (canonical homes, the governor single-home, conditional clause wording, adjudicated re-pins)

> **New in the 2026-10-20 plan review (RULING-2).** P1–P11 are pinned; P12 is an **addition**. It governs *where* the prompt contract is stated, not *what* it says. Cross-referenced from the plan.

**Decision.**

1. **The canonical-home rule is INTRA-agent.** The prompt-writing guide's canonical-home rule (`docs/agent-prompt-writing-guide.md:68` + `:55` + `:63`) binds **within one agent**: each agent states the contract **once**, in that agent's own canonical home, and its other files point at it. It does **not** bind across agents — there is **no cross-agent dedup** of the clause-family wording. (The one cross-agent home is the shared question skill, `agents/_prompt_system/innate-skills/question/skill.md` — see P5.)
2. **RULING-2 — the governor's contract lives ONCE in `agents/governor/rule.md`.** `D_HOME_FILES` and `E_HOME_FILES` both name `rule.md` for `governor` (`tests/unit/test_report_integrity_prompts.py:92` and `:106`), so `rule.md` is the governor's home for both the report-scrutiny (D) and opening-discipline (E) guidance. `agents/governor/workflow.md:46` becomes a **section-name pointer** ("See Open questions for the orchestrator") — **not** a second statement of the contract.
3. **The clause-family edit rule is CONDITIONAL.** The rewritten clause reads "begin the work with a tool call, deliver the synthesis on work already done, or report open questions — **unless you are the user-facing root**, in which case you may ask the human". A root's own ask wording must **not** be degraded by the sweep.
4. **Every edit to a sealed file requires the adjudicated re-pin** — now **21** re-pin *operations* (**14 + 3 + 1 + 3**) across **17** distinct sealed files (the 3 round-2 updates are **re-re-pins of already-counted files**, so the distinct count does not move). The **sealed baseline** covers **35** entries.

**Rationale.**

- *Intra-agent, not cross-agent.* A cross-agent dedup would move a per-agent instruction into a shared file and break the guide's own canonical-home convention (`docs/agent-prompt-writing-guide.md:68`); the clause-family sweep is therefore compliant as a **per-agent** edit. A shared home would also re-introduce the "two homes for one contract" trap that P5 rejects.
- *The governor's two contract-bearing files.* `agents/governor/rule.md:82` carries the **BEGIN/DELIVER/OR ASK** clause — the last sentence of a long line that **also** carries the junk-report clause — and the file carries **1 ×** "Chat Delivery". The file is **sealed**: the **pre-sweep** sha was `84a93586df376ad39dc553af36f86f684977ecfa8d717186f2fe8f16846c8cf5` at `tests/test_chart_image_delivery_e2e.py:1387`; the sweep's adjudicated re-pin moved it to the current **`a779742d…`** at **`:1392`**. Both the clause and the "Chat Delivery" literal must **survive** the edit; the sweep rewrites the clause's wording, not its presence. **Verified landed:** the clause now reads "…deliver the synthesis on work already done, or report open questions (**unless you are the user-facing root**)", and the file still carries **1 ×** "Chat Delivery" and the **"zero tool calls"** / **"before ending any turn"** snippets.
- *Why `rule.md` and not `workflow.md`.* The governor's D and E homes are **both** `rule.md` (the test registry, not convention, decides). Splitting the contract across the two files would create a **second vocabulary** for one contract — the "stated once" trap the guide's R-e rule forbids.
- *Why the clause must be conditional.* After P4 the question tool exists only for a user-facing root. An unconditional "report open questions" clause would tell a **root** to route its question through a report section instead of asking the human — degrading the very capability the commission preserves. The conditional form is the prompt-side expression of the two-layer model (P4).
- *Sweep size (machine-derived, not hand-counted) — as amended by Amendment #4.* The sweep edit-set is **74 prose files** (was 66 at Amendment #3; 63 → 74 after the round-2 semantic-class sweep); the **clause-family site** count is **41** (**unchanged** — round 2 converted *other* semantic classes, not clause-family sites); **canonical homes = 16** (was 14 — round 2 added `agents/maintenancer/rule.md` and `agents/project-manager/rule.md`); the **re-pin count is 21** operations (**14 + 3 + 1 + 3**) across **17** distinct sealed files. The branch diff reconciles exactly: **`0e7500a1b..4088be633` = 93 files** = **74** agents prose + **3** `meta.json` + **1** regenerated prompt-artifact baseline + **3** daemon + **3** docs + **7** tests + **2** planning docs; the **commission surface excluding the plan's own docs = 91**.

> **LANDED (Amendment #3) — the fix round's seal re-pins and baseline regens.** The fix round re-pinned **3 files / 4 constant-update operations** (commit `78469d671` — `charter/workflow.md ← 66512e9dd`, `devops/rule.md ← 617bf69a0`, `maintenancer/rule.md ← 74e9168a2` — then `6fa226976` re-pinned the devops residual `617bf69a0 ⇒ febf0e89…` after the `c5bd90146` edit). Net: **17** distinct sealed files carry a changed constant out of the **35**-entry baseline ⇒ the split is **17 re-pinned / 18 safe**. **Current seal constants** (`tests/test_chart_image_delivery_e2e.py`): `charter/workflow.md` `2a5078f9…` (`:1357`), `devops/rule.md` `febf0e89…` (`:1390`), `governor/rule.md` `a779742d…` (`:1392`), `leader/rule.md` `261ab3af…` (`:1393`), `maintenancer/rule.md` `d6e448a6…` (`:1394`). All **35** constants were **independently re-verified from real file bytes** — the pre-edit mismatch count was **exactly 3** (the 3 targets), **zero collateral**, i.e. **no pattern-fill**. The prompt-artifact baseline was regenerated **twice** (`f41134c13`, `2f28cb969`): the drift set was **exactly the 12 depth-2 edits**; `--check` reports **161 OK** and `sha256sum -c` reports **161 OK**.
>
> **LANDED (Amendment #3) — the sweep completion and the charter/escalate fixes.** Sweep completion (B4): **6 EDIT** sites — `agents/gaia/rule.md:9`, `agents/gaia/workflow.md:14` and `:39`, `agents/designer/rule.md:30`, `agents/designer/workflow.md:34`, `agents/designer/skills-template/design-strategy.md:31`, `agents/coder/workflow.md:30`; **2 NO-EDIT adjudicated** — `agents/gaia/workflow.md:71` **IS** the contract text itself and `:89` is an **inbound-user** site (not a non-root ask site); plus **2 disclosed scope additions** — `agents/gaia/workflow.md:80` and the flowchart at `:98` — added to avoid a **fresh self-contradiction** the B4 edit would otherwise have created. Charter (B5): `agents/charter/workflow.md:71` now uses the **`NEEDS MORE INFO`** report form, self-consistent with its own `:52` rule. Escalate family (W3), conditionalised to the report chain: `agents/leader/workflow.md:699`, `agents/maintenancer/rule.md:9` + `agents/maintenancer/workflow.md:53`, `agents/project-manager/rule.md:45`.
>
> **LANDED (Amendment #3) — devops is the 14th canonical home.** `agents/devops/rule.md` now carries the canonical `## Open questions for the orchestrator` section, **verbatim** from the `agents/tester/rule.md` home. The **canonical-home count moves 13 → 14**; the sealed baseline itself is **unchanged at 35** files. The devops ask-family **anchor drift** (the briefed line numbers were stale; the family was adjudicated **by construct** — `rule.md` 9/32/91/92/110/114/156/164/203 + `soul.md` 46/49/77) is recorded in the §5 evidence index.
>
> **LANDED (Amendment #4) — B3 CLOSED, the fix-round-2 semantic-class sweep, and the W1 honest rename.** **B3 is CLOSED**: the round-2 commit `4088be633` converted **26 `agents/**` prompt files**, **~115 CONVERT sites**, in **4 canonical-host files** — `agents/_mother/rule.md` and `agents/jober/rule.md` (both **pre-existing** canonical homes) plus `agents/maintenancer/rule.md` and `agents/project-manager/rule.md` (both **new** canonical homes ⇒ **14 → 16**). The round also closed the **round-1 survivors the token grep missed**: `agents/leader/workflow.md:147`/`:188`, `agents/jober/rule.md:211-212`, `agents/_baby_template/soul.md:11`, `agents/maintenancer/rule.md:6`, `agents/project-manager/rule.md:13`, and the `agents/tester/*` `ensure.md` family; the 4 known files `agents/devops/workflow.md` ×6, `agents/giter/rule.md:8`, `agents/giter/workflow.md:111`, `agents/jober/rule.md:124`/`:126`/`:194` are inside the converted set. **The sweep pattern set is P1–P6 plus a MANUAL SEMANTIC READ** — P1 = 21 briefed token patterns; P2 = two-way human-actor; P3/P3' = human-actor census + decision-verbs-near-human; P4 = whole-partition line-by-line census; P5 = delivery/escalation verbs; P6 = `human`; plus a **manual read** that caught **6 sites no regex reached**. **P1/P2 are necessary-but-insufficient** — blind to acquisition verbs that name no human, to question-template forms, to `ask you`, and to present+plan adjacency; **P3/P4 are the discriminating passes**. The round added **11 new edit-set members** (prose **63 → 74**): `agents/_baby_template/soul.md`, `agents/_prompt_system/innate-skills/coordination/skill.md`, `agents/giter/rule.md`, `agents/giter/soul.md`, `agents/maintenancer/growth.md`, `agents/maintenancer/skills-template/ens-db-repair.md`, `agents/maintenancer/skills-template/restart-upgrade-ops.md`, `agents/maintenancer/tools_note.md`, `agents/tester/growth.md`, `agents/tester/skills-template/ensure-validation.md`, `agents/devops/workflow.md`. The **3 round-2 seal re-pins** are `agents/leader/rule.md` → `f0bda673c7b112ef7a43f17f1166f99fcb11f1562aabf0d7a5c9879e924a231a`, `agents/maintenancer/rule.md` → `abe43bae715c62a341788fbe5d610138936b7b4e83df87c8bbfd3611bf446aec`, `agents/project-manager/workflow.md` → `bde479095bbda6a3e3e069afe8a719e0f8b5673f289d2d0d5bf7a15eecb123ad` — every value **recomputed from real file bytes**, the other **32** entries untouched, **35/35** independently re-verified, and the seal-file diff is **3 constants + 6 comment lines**.
>
> **W1 — HONEST RENAME (Amendment #4): naming-honesty, NOT a coverage loss.** The T4a preservation case was renamed `test_t4a_restore_path_root_with_stamped_row_has_question_tool` → **`test_root_with_threaded_chat_source_type_has_question_tool`**. The old name asserted a **restore-path** scenario the test does not exercise; the new name states what the test actually asserts (a **root with a threaded chat `source_type`** keeps the tool). The **honest-name option was taken deliberately** over a cheap rename: the real restore-path assertion is **not cheap** — it needs a row, the repos, and the LLM restore lane. The **restore-path stamping remains statically covered** by the **restore-lane threading seam** `daemon/services/instance_lifecycle.py:4667-4668`/`:4675`/`:4690` (the restore call site threads both kwargs — P2) and by the **W3** AST pin (the threaded values reach the gate before the filter). **No coverage was removed.**

**Rejected alternatives.**

| Alternative | Why rejected | Cost had it been chosen |
|---|---|---|
| State the governor contract in **both** `rule.md` and `workflow.md` | The "stated once" trap — two files, two vocabularies for one contract; a later edit fixes one and leaves the other stale | The governor's own prompt carries two subtly different statements of the same rule; a reader (or a checker) cannot tell which is authoritative |
| **Split** the governor contract across the two files (D in `rule.md`, E in `workflow.md`) | The test registry fixes **both** homes to `rule.md` (`test_report_integrity_prompts.py:92`, `:106`) ⇒ the split would immediately fail the integrity pin, and it fragments one contract across two files | A red integrity pin plus a permanently half-migrated contract |
| A **cross-agent** shared home for the clause-family wording (dedup across all 66 sweep files) | The guide's canonical-home rule is **intra-agent** (`:68`, `:55`, `:63`); a shared file would violate it and re-introduce the P5 "two homes" trap | A guide-convention violation plus the risk that a shared file's wording overrides each agent's own mandate |
| An **unconditional** clause-family rewrite ("or report open questions" everywhere) | It would strip a **root's** ability to ask the human in its own turn — the exact behaviour the commission preserves | The commission would degrade the population it exists to protect, and would contradict P4's two-layer model |
| Editing the governor clause **without** the adjudicated re-pin | The file is **sealed** (pre-sweep `84a93586…` at `tests/test_chart_image_delivery_e2e.py:1387`; current `a779742d…` at `:1392`); the tripwire (`:1418`) would fail the run | A red tripwire on a legitimately-edited file, with no attribution record of who changed what and why |
| Editing `agents/ari/rule.md` / `agents/ari/workflow.md` | Both are **sealed** and carry the attestation/mission-watch contracts; the ari grant is **`meta.json`-only** (P4) | Two more adjudicated re-pins for no functional gain, and a wider blast radius on the sealed set |

**Invariants created.**
- The canonical-home rule binds **intra-agent**; each agent states the contract **once**, in its own canonical home; cross-file references inside one agent use the **section name only**.
- The governor's open-questions contract is stated **once**, in `agents/governor/rule.md`; `agents/governor/workflow.md:46` is a **pointer** only.
- The clause-family wording is **conditional** ("unless you are the user-facing root") — a root's ask wording is never degraded by the sweep (**R7**).
- Every edit to a sealed file requires the **adjudicated re-pin** (git-log last-touch attribution → constants-only update → targeted test → provenance-labelled commit) — **21** re-pin **operations** (**14 + 3 + 1 + 3**) across **17** distinct sealed files (⇒ baseline split **17 re-pinned / 18 safe**). The **3 round-2 operations are re-re-pins of already-counted files** (`agents/leader/rule.md`, `agents/maintenancer/rule.md`, `agents/project-manager/workflow.md`) ⇒ the distinct count does not move.
- The **three semantic pins the SHA seal does not cover** are named and must survive:
  - **(a) the attestation contract** — `agents/leader/rule.md` **and** `agents/leader/workflow.md` must **keep** "Wrong Attestation Order", "PURE TOOLCALL TURN", "c5d9a38a shape", "report-first-then-attest", "attest-first, report-second", "subsequent standalone AI message", "non-delegating turns" and must **not** gain "CONDITIONAL on delegation", "Deliver the full detailed final report", "as its own message FIRST", "call `attest_completion` ALONE" (`tests/unit/tools/test_attestation_prompt_contract.py:48-49`).
  - **(b) the mission-watch vocabulary** — `agents/jober/rule.md` + `agents/jober/workflow.md` keep the `watch_mission` vocabulary and **zero** `watch_job` tokens (9 files, 0 `watch_job`; `tests/unit/tools/test_mission_watch_prompts.py`).
  - **(c) "Chat Delivery"** — the literal must survive **verbatim** in every chart-capable file the sweep edits (**20** agents; `tests/test_chart_image_delivery_audit.py:58`, `:467-480`).
- **N14 — the per-file verify clause names the D snippets too**, not only the E ones: `"[REPORT SANITY:"`, `"interim, not completion"`, `"send_message"` (`D_REQUIRED_SNIPPETS`, `tests/unit/test_report_integrity_prompts.py:110`) alongside `"before ending any turn"`, `"zero tool calls"` (`E_REQUIRED_SNIPPETS`, `:115`).
- The sweep edit-set is **74 prose files** (was 66; machine-derived manifest, plan §10.10) — **3** `meta.json` + **74** prompt/doc; the branch diff reconciles as **93 = 74** prose + **3** `meta.json` + **1** regenerated baseline + **3** daemon + **3** docs + **7** tests + **2** planning docs (⇒ **commission surface 91**). The **clause-family site** count is **41** (**unchanged**); **canonical homes = 16**; the sealed baseline is **35** entries; the re-pin count is **21** operations (**14 + 3 + 1 + 3**) across **17** distinct sealed files (⇒ split **17 / 18**).

**Verification.** Plan §10 (prompt sweep), §10.1-§10.9, §11.2-§11.4. Pins to re-run per edited file: `tests/unit/test_report_integrity_prompts.py` (D + E snippets, both homes), `tests/unit/tools/test_attestation_prompt_contract.py:48-49` (leader rule+workflow), `tests/unit/tools/test_mission_watch_prompts.py` (jober rule+workflow), `tests/test_chart_image_delivery_audit.py:58,467-480` (the 20 chart-capable agents), `tests/unit/tools/test_prompt_section_reference_integrity.py` (no bare `.md` / bare `agents/`), the seal tripwire `tests/test_chart_image_delivery_e2e.py::TestPhaseDDoesNotModifySealedArtifacts` (`:1418`) with the **21** adjudicated constants-only re-pins (**14 + 3 + 1 + 3** operations over **17** distinct sealed files), and the prompt-artifact baseline `agents/.prompt-artifacts.sha256` (depth-2 only) refreshed for the edited files.

## P13 — The fix-round-2 leader ratifications (R-a, R-b, R-c)

> **New in Amendment #4 (2026-10-20).** P1–P12 are pinned; P13 is an **addition**. It records three **ratified decisions** that came out of fix round 2. They are **decisions, not open questions** — they are closed, and they are recorded here so a future sweep does not "unify", "relay", or "simplify" them away. Each carries the same five blocks as every other decision in this record.

**Decision.**

- **R-a — The upgrade nonce ceremony is ROOT-BOUND.** A **non-root** maintenancer **structurally cannot** complete a live upgrade: the **root must mint AND arm** the nonce. A non-root agent **must never relay** the ceremony. Final-report line (verbatim): *"the upgrade nonce ceremony is root-only; non-root agents must not relay it."*
- **R-b — TWO canonical wording families, RATIFIED AS DELIBERATE.** The round-2 sweep landed **two** families of non-root wording, and they are **not** two drafts of one rule:
  - **root-CONDITIONAL** — "…**unless you are the user-facing root**" — for the **plausibly-root** agents: **`leader`**, **`maintenancer`**, **`project-manager`**.
  - **FLAT** — "**never ask the user directly**" — for the **structurally-never-root** agents: **`jober`**, **`_baby_template`**, **`giter`**, **`devops`**.
- **R-c — project-manager's "the final call is human" is converted to the conditional form; the `growth.md` conversions are KEPT.**

**Rationale.**

- *R-a — why the ceremony cannot be relayed.* The live-upgrade ceremony is gated by the **3-factor nonce** (`daemon/tools/upgrade_tools.py`), whose third factor is a **real user-issued nonce** that only the user can mint and echo. The maintenancer's own Cardinal rules already encode the boundary: `agents/maintenancer/rule.md:8` — *"The upgrade 3-factor nonce gate on `system_upgrade` is UNTOUCHED and separate: never bypass it; relay any user nonce verbatim without agent-side fabrication"*; `:31` — *"The three-factor nonce gate is satisfied only by a real user-issued nonce — I do not imagine one, I do not construct one. If no nonce exists, I report `Blocked — missing user nonce` and stop."*; `:83` — *"Never invent a nonce, never echo one the user did not send."* The lane **deliberately left the wording NO-EDIT** — a *relay* instruction would have put a ceremony-critical line in a non-root's reach, weakening the very 3-factor property the ceremony exists to enforce. The one pre-existing nonce-relay sentence — `agents/maintenancer/knowledge/06-restart-upgrade-runbook.md:21` (*"Relay verbatim via `ask_user`; agent MUST NOT fabricate or echo"*) — stays a **§4 deferred audit-trail item**, **not** swept.
- *R-b — why two families and not one.* The families split on the **agent class**, not on style. A **plausibly-root** agent (`leader`, `maintenancer`, `project-manager`) *can* legitimately be the user-facing root on some run and a child on another ⇒ its prompt must carry the **exception** ("unless you are the user-facing root"), or the commission would strip the very asking ability it preserves. A **structurally-never-root** agent (`jober`, `_baby_template`, `giter`, `devops`) can never satisfy the P1 predicate ⇒ telling it "…unless you are the user-facing root" would be a **lie about its own capability**; the flat form is the truthful statement. Both families are load-bearing. The landed evidence: the conditional form at `agents/leader/workflow.md:703` (*"…or report open questions (unless you are the user-facing root)"*), `agents/leader/workflow.md:699`, `agents/maintenancer/rule.md:9`, `agents/project-manager/rule.md:45`; the flat form at `agents/jober/rule.md:132`, `agents/jober/workflow.md:87`, `agents/giter/rule.md:8`, `agents/devops/soul.md:49`, `agents/devops/workflow.md:197`.
- *R-b — the leader's other round-2 ratifications.* **11 NO-EDIT** (each adjudicated and left alone): `agents/giter/rule.md:7` (**display-only** verb); `agents/giter/soul.md:12` + `agents/giter/workflow.md:41` (**input provenance**); `agents/devops/soul.md:46`/`:77` (**the channel is defined at `:49`**); `agents/devops/workflow.md:313` (**example annotation**); `agents/gaia/rule.md:56` (**consent-by-explanation is already the report channel**); `agents/_mother/rule.md:25` (**authorization-provenance gate**); the **`_mother` Q&A templates** (the **round-1 softening stands**); the **leader "report to user" output-direction family**; `agents/_baby_template/rule.md:31` + `agents/_inner_soul/*` (a **real out-of-band `pending_approval` gate**, not a user-ask site); and the **`growth.md`** conversions (**inert**). **1 CONVERT ratified:** `agents/gaia/**` + `agents/_mother/**` — the **flat** never-ask form becomes the **ROOT-CONDITIONAL** form, because both classes are plausibly-root.
- *R-c — why the conversion and why the `growth.md` edits stay.* `agents/project-manager/rule.md:13` now reads *"I surface options with trade-offs and a recommendation; **the final call is not mine**. A decision that is the human's to make reaches them through my report (See Open questions for the orchestrator); I escalate only the genuinely user-critical ones."* The old wording ("the final call is human") described a **user-facing** channel that a non-root project-manager does not have ⇒ the conditional form is the truthful statement (same rationale as R-b). The `growth.md` conversions were **kept** even though they are **inert** — `growth.md` is **never composed into a prompt** ⇒ the edit carries no behavioural risk, and **consistency over diff-minimization** is the chosen rule.

**Rejected alternatives.**

| Alternative | Why rejected | Cost had it been chosen |
|---|---|---|
| **Relay the nonce via `maintenancer`** (R-a) | A relay puts the 3-factor ceremony in a **non-root's** reach. The ceremony's third factor is a **user-minted** nonce; a relayed or agent-relayed nonce is exactly what the gate exists to prevent | The 3-factor ceremony would be weakened to a 2-factor relay — the one control the project has ruled *not* to weaken (`upgrade_policy` / ADR-036 lineage) |
| **Sweep the nonce wording anyway** (R-a) | The line is a **ceremony instruction**, not an ask-family site; changing it alters the ceremony's instructions for **no contract gain** | A prompt edit with a live-upgrade blast radius and no benefit to the commission's contract |
| **Unify the two wording families** (R-b) | The flat form would **strip the root exception** from the plausibly-root agents ⇒ the commission would degrade the population it exists to protect; the conditional form would **lie** to the structurally-never-root agents about a capability they can never have | One of the two populations gets a false prompt: either the roots lose their ask ability, or the never-root agents are told about a door that does not exist for them |
| **Convert the flat family to conditional** (R-b) | Same as above, from the other side | Structurally-never-root agents carry a conditional they can never satisfy — prompt noise that trains a rule the agent can never use |
| **Convert the conditional family to flat** (R-b) | Same as above, from the other side | The plausibly-root agents lose the exception ⇒ "roots still ask" is broken |
| **Revert the project-manager "final call" edit** (R-c) | The unconditional "the final call is human" tells a **non-root** project-manager it has a user-facing channel it does not have | A structurally-wrong prompt for the very agent that most often frames decisions for the human |
| **Revert the `growth.md` conversions** (R-c) | They are **inert** (never composed into a prompt) ⇒ zero behavioural risk; reverting them would leave the tree internally inconsistent for no gain | A permanently half-converted tree — the next sweep would have to re-derive the class rule from scratch |

**Invariants created.**

- **The upgrade nonce ceremony is ROOT-ONLY and must never be relayed by a non-root agent.** The root must mint AND arm the nonce; a non-root reports `Blocked — missing user nonce` and stops. The pre-existing `ask_user` relay sentence in `agents/maintenancer/knowledge/06-restart-upgrade-runbook.md:21` stays an **audit-trail item** (adjudicated, never bulk-rewritten).
- **The two wording families are DELIBERATE and must not be unified.** A future sweep classifies each site by **agent class** first: plausibly-root (`leader`, `maintenancer`, `project-manager`) ⇒ **root-CONDITIONAL**; structurally-never-root (`jober`, `_baby_template`, `giter`, `devops`) ⇒ **FLAT**. "Unify the two families" is a **rejected alternative**, not a cleanup.
- **`growth.md` conversions are INERT and stay**: `growth.md` is never composed into a prompt ⇒ consistency over diff-minimization.
- The 11 round-2 **NO-EDIT** adjudications are **closed** — they are not re-opened by a later sweep; the listed line is either **display-only**, **input-provenance**, an **example annotation**, an **out-of-band `pending_approval` gate**, or an **inert** file.
- The **W1 rename** is a **naming-honesty** correction, **not** a coverage change: the restore-path stamping stays statically covered by the **restore-lane threading seam** (`daemon/services/instance_lifecycle.py:4667-4668`/`:4675`/`:4690`) and by the **W3** AST pin (ordering).

**Verification.**

| Pin | What it proves | Where |
|---|---|---|
| **W3** (gate pack) | the gate is **registered before** `_apply_tool_filter` — AST-pinned, with an in-test mutant self-check | `tests/unit/tools/test_question_tool_root_gate.py:627` |
| Report-integrity pins | the D + E snippet vocabulary survives every converted file (the conditional family did not break the per-file verify clause) | `tests/unit/test_report_integrity_prompts.py:92`/`:106`/`:110`/`:115` |
| Section-reference integrity | the converted files reference the section **by name only** (no bare `.md` / bare `agents/`) | `tests/unit/tools/test_prompt_section_reference_integrity.py` |
| "Chat Delivery" audit | the literal survives verbatim in the **20** chart-capable agents | `tests/test_chart_image_delivery_audit.py:58`, `:467-480` |
| Seal tripwire | the **3** round-2 re-pins are constants-only and the **35** entries still match the real bytes | `tests/test_chart_image_delivery_e2e.py:1350`/`:1418` |
| Prompt-artifact baseline | the converted depth-2 files are in the baseline and the drift set is a **strict subset** of the round's edits | `agents/.prompt-artifacts.sha256` (**161** entries) — `--check` **0 diffs**, `sha256sum -c` **161 OK** |
| Round-2 batteries | **7 pre-existing failures, ZERO new** (gate pack 13P; closure sweep 1462P/5F; report-integrity 51P/20S; devops agent 61P/2F; chart audit 46P; seal tripwire 24P/4D; flow proofs 16P + 3P; baseline self-test 19P) | §0, §5 |

---

## §2. Rejected alternatives — cross-cutting index

| # | Alternative | Rejected under | One-line reason |
|---|---|---|---|
| 1 | Runtime refusal (register the tool, reject the call) | P2 | The tool still occupies the model's tool list and the help surface; the requirement is *never registered* |
| 2 | Gate inside `create_question_tools` | P2 | Its 3-arg signature has no `parent_id`/`source_type` channel and is called directly by tests |
| 3 | Read the instance row at registration time | P1, P2 | The row does not exist at spawn (`:2137` before the DB write `:2433`, commit `:5451`) ⇒ silent primary-path breakage |
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
| 19 | Narrow the question grant to **`ari` only** (strip `"question"` from leader's and devops' `tools.allow`) | P4 | Effective capability collapses to `{ari}` ⇒ a chat-bound leader passes the gate and is then stripped by `_apply_tool_filter` ⇒ it cannot ask at all, contradicting "roots still ask" |
| 20 | State the governor contract in **both** `rule.md` and `workflow.md` | P12 | The "stated once" trap — two vocabularies for one contract; a later edit fixes one and leaves the other stale |
| 21 | A **third** chat-source taxonomy copy (mint a new set instead of importing the existing one) | P1, P11 | Three divergent sets; N7 requires **importing** `CHAT_SOURCE_TYPES` (`repository.py:919`) + a three-way equality unit pin |
| 22 | **Backfill** legacy unstamped rows | P1, P11 | A migration would make the delta **non-empty** and break the P11 EMPTY-delta premise; the rows are **pre-existing** (the writer dates to 2026-08-12) and explicitly out of scope |
| 23 | **Split** the governor contract across two files (D in `rule.md`, E in `workflow.md`) | P12 | The integrity registry fixes **both** homes to `rule.md` (`test_report_integrity_prompts.py:92`, `:106`) ⇒ the split fails the pin and fragments one contract |
| 24 | **Relay the upgrade nonce via `maintenancer`** (non-root completes a live upgrade) | P13 (R-a) | The 3-factor ceremony's third factor is a **user-minted** nonce; a relay weakens it to a 2-factor relay — the control the project has ruled *not* to weaken |
| 25 | **Unify the two non-root wording families** into one | P13 (R-b) | The flat form strips the root exception from the plausibly-root agents; the conditional form lies to the structurally-never-root agents. The split is by **agent class** and is deliberate |

---

## §3. Consolidated invariants

A future change must not break any of these without a new commission and a new decision record.

**Data & identity**
1. `instance_metadata["source_type"]` has **exactly one writer** — `daemon/services/instance_lifecycle.py:2390`.
2. `original_source` is **never** an input to a root / user-facing predicate.
3. The root test is `(parent_id IS NULL OR parent_id == "")` — never a bare `IS NULL` (`repository.py:1109`).
4. The chat-source set is the 4-member registry set (`repository.py:919`), corroborated by `upgrade_journal.py:3358`. It is **not** the 3-member prompt-formatting set.
5. `source_type` is **never** inherited by children.
6. **One canonical chat-source set (N7).** The gate **imports** an existing constant rather than minting a third copy; the two existing sets and the imported set are held equal by a **three-way equality unit pin**.
7. **No backfill (N8).** Legacy rows without the `source_type` stamp are **pre-existing** and **out of scope**; a backfill is forbidden because it would make the migration delta **non-empty** and break the P11 EMPTY-delta premise. "A root restored from the DB keeps the tool" holds **only** for a root whose row carries the stamp.

**Gate & registration**
8. The predicate is evaluated **only** from values threaded into the tool build — never from a DB row read at registration time.
9. Hiding = **never registered** (the tool is absent from the toolset), not a runtime refusal.
10. `create_question_tools` keeps its **3-argument** signature; its source file is never deleted, renamed, or re-categorised.
11. Both `create_instance_tools` call sites (`:2137` spawn, `:4690` restore) thread the same two keyword-only arguments.
12. The gate runs **before** `_apply_tool_filter` (`:6238`) ⇒ a `tools.allow` entry can never re-admit a gated tool.

**Capability (two-layer model)**
13. **The two-layer capability model holds:** the gate is the authoritative **hiding** control; the allow-list is the **capability** grant; effective capability = `{user-facing root} ∩ {the agent's meta grants "question"}`.
14. `agents/leader/meta.json` and `agents/devops/meta.json` **KEEP** `"question"` in `tools.allow` but **LOSE** it from `innate_skills` (leader ⇒ `["coordination","chart","todo"]`; devops ⇒ `["dynamic-skill","chart","todo"]`); `agents/ari/meta.json` holds it in **both**.
15. `agents/project-manager/meta.json`'s `"question"` **deny** entry is preserved; `agents/critic/meta.json` is untouched.
16. `innate_skills` is **never** set to `[]` to suppress a skill (empty ⇒ absent ⇒ legacy scan, `loader.py:856-858`).
17. A user-facing root whose agent does **not** grant the category cannot ask — a **documented limitation** (LATENT today: no non-ari agent is known chat-bound), disclosed as risk **F3** and as a final-report line.

**Registry surfaces**
18. `PRIVILEGED_TOOL_CATEGORIES` is a **frozen exact set of eight** — no add, no remove without a same-PR five-pin update.
19. `"question"` stays in `CATEGORY_MODULES` (`:729`); `"midflight"` stays at `:732`.

**Prompt contract**
20. The section title is exactly `Open questions for the orchestrator` — one spelling, tree-wide.
21. The section appears **only** when genuinely blocked, holds **0–3** framed questions, and sits **immediately after** the one-line report summary — never at the tail.
22. `agents/_prompt_system/innate-skills/question/skill.md` is the **single canonical home** for the question contract.
23. Prompt cross-references use the **section name only** — no bare `.md`, no bare `agents/` (`tests/unit/tools/test_prompt_section_reference_integrity.py`).
24. **The canonical-home rule binds INTRA-agent (P12).** One statement per agent, in that agent's canonical home; same-agent cross-references are section-name-only. No cross-agent dedup of the clause-family wording.
25. **The governor's contract is stated ONCE (P12, RULING-2)** — in `agents/governor/rule.md` (both the D and the E home, `test_report_integrity_prompts.py:92`, `:106`); `agents/governor/workflow.md:46` is a **pointer** only.
26. **The clause-family wording is CONDITIONAL** ("unless you are the user-facing root") ⇒ a root's ask wording is never degraded by the sweep (**R7**).
27. The three **semantic** pins the SHA seal does not cover are named and must survive: the attestation contract (`test_attestation_prompt_contract.py:48-49`), the `watch_mission` / zero-`watch_job` vocabulary (`test_mission_watch_prompts.py`), and the literal "Chat Delivery" in the **20** chart-capable agents (`test_chart_image_delivery_audit.py:58`, `:467-480`).
28. The per-file verify clause names the **D** snippets (`"[REPORT SANITY:"`, `"interim, not completion"`, `"send_message"`) as well as the **E** ones.

**Behavioural**
29. Midflight (in-turn, non-blocking) and the open-questions section (terminal report) coexist; neither substitutes for the other.
30. "Ask the user" wording is reserved for **roots**; a non-root leader escalates upward through its own report chain.
31. The parent-side answer path is the **existing** report + `send_message` + revive path — no parallel channel.

**Answering / durability**
32. The answering surface is untouched: `set_answers` CAS, the durable shadow, boot rehydration, `resolve_answer_target`, `job_answer`, and the instance-addressed answer route all stay as-is.
33. A legacy pack remains answerable after the gate ships; the original `pack_id` survives rehydration (`question_manager.py:487-492`).
34. **No** schema migration for this commission ⇒ the P11 delta stays empty.
35. Only **roots** can mint a pack (sole production writer `question_tools.py:502`) ⇒ the mid-flight QA / answer-event fan-out is **root-only**; the *answering* surface is unchanged (risk **F1**).

**Process**
36. **Phase 0 is a hard wait** on `fix/question-pause-resume-asymmetry` merging into `latest`; then **`git merge latest` INTO this branch** (merge, never rebase); every anchor is re-verified after each merge.
37. This commission's own branch (`feature/hide-question-tool-nonroot`) and worktree (`/home/nea/ensemble-src-wt-hidenonroot`) are **reused as-is**; Phase-0 KV work is **verify/merge**, never create.
38. The sibling worktree is read-only to us.
39. The KV claim row is **merged**, never clobbered.
40. **Stage only — never promote.** The 3-factor nonce ceremony owns promotes.
41. Any edit to a sealed file (`tests/test_chart_image_delivery_e2e.py:1350` `SEALED_SHA_BASELINES`, **35** entries; tripwire `:1418`) requires an **adjudicated re-pin**: git-log last-touch attribution → constants-only update → targeted test → provenance-labelled commit. **21** re-pin **operations** (**14 + 3 + 1 + 3**) across **17** distinct sealed files ⇒ the baseline split is **17 re-pinned / 18 safe**. The fix round contributed **3 files / 4 constant updates** (`agents/devops/rule.md` twice: `617bf69a0` superseded by `febf0e89…`); **fix round 2 contributed 3 more operations on 3 already-counted files** (`agents/leader/rule.md` → `f0bda673…`, `agents/maintenancer/rule.md` → `abe43bae…`, `agents/project-manager/workflow.md` → `bde47909…`) ⇒ the **distinct** count does not move.
42. The proof set is named: **gate proof (RED pre-change) = T1, T3, T4b, T5, T9**; **preservation guards (GREEN pre-change) = T2, T4a, T6, T7, T8**. A green preservation run is never reported as gate proof. The gate-proof pre-change failure mode is a **`TypeError` on the not-yet-existing kwargs**. **Third class (Amendment #3): T2 and T4a** are preservation guards whose pre-change RED signature is a **clean assertion failure** produced by the **signature-adaptive harness** (`tests/unit/tools/test_question_tool_root_gate.py:44-63`), **not** the `TypeError` — the earlier `TypeError` prediction was **STALE for T2/T4a only**. The adaptivity **cannot hide a missing implementation**: the dedicated signature pin `test_ac3_parent_id_and_source_type_are_keyword_only` (`:441`) asserts the two parameters exist **and** are keyword-only, and is **RED pre-change**.
43. **Phase sequence = 0, 1, 2, 3, 4, 5, 6, 7 (reviewer council), 8 (independent tester), 9 (staging).** Staging is **Phase 9**; Phase 7 is the **reviewer council** with a **0-critical** bar (owner: the reviewer agent); Phase 8 is the **independent tester functional verification** — registration-level + flow + staged smoke — run on the **merged tree** (owner: the tester agent).
44. **The final report carries 10 items: the commission's 7 required items VERBATIM + 3 extras.**
    - *The 7 required items (verbatim):* (1) the **pinned predicate**; (2) the **registration gate**; (3) the **list of agent files touched**; (4) the **flow proof** (child open-question → parent instruction); (5) the **tests**; (6) the **staging plan**; (7) an **explicit in-flight child question pack statement**.
    - *The 3 extras (retained):* the **21** adjudicated seal re-pins (with their provenance) and the **35**-file sealed baseline split (**17** re-pinned files / **18** safe — note the arithmetic: **21** = re-pin *operations* (14 + 3 + 1 + 3), **17** = distinct sealed files whose constant actually changed, because `agents/devops/rule.md` was re-pinned **three times** and the **3** round-2 re-pins target already-counted files — each collapses to one net constant); the **ari-grant live-behaviour flag** (R1) together with the **precise `_tool_metadata` / help-surface mechanism** (R3 / N5); and the **deploy paragraph** — the mid-flight QA / answer-event fan-out is now **ROOT-ONLY** (N6 / F1) — together with the statement that **no promote** was performed.
    - Items **3**, **5** and **10** additionally carry the disclosures the council required: the RULING-1 scoping limitation (F3), the job-root / API-root behaviour delta (F4), the **53**-file test-census shift (F5), and the leader's teaching-gap note (F6).

**Landed fix-round facts (Amendment #3) — added as invariants**

45. **The pre-change census is exactly THREE agents.** Before this commission, exactly **3** agents could hold `ask_questions`: **`leader`** (`tools.allow` + `innate_skills`), **`devops`** (both), and **`watcher`** (`tools.allow: []` ⇒ the **default-open universe** ⇒ it held the tool with **no** meta entry). `ari` held **neither** pre-change; `project-manager` **denied** it; `critic` never held it. The fix had to close the **explicit** doors (leader, devops) **and** the **implicit** door (watcher's empty allow) — only the **gate** closes the third.
46. **Fail-closed default-kwargs.** `create_instance_tools` threads the two values as **keyword-only** params defaulting to `None` (`daemon/tools/instance.py:2517-2520`), and `_is_user_facing_root` (`:2490-2510`) fails closed on **both** halves — a non-empty `parent_id` is never a root, and `(source_type or "") in CHAT_SOURCE_TYPES_FOR_ROOT` makes a missing/unknown `source_type` never a root. Therefore **any call site that forgets to thread a value yields NO tool** (the safe direction), never a spurious grant.
47. **T2's anti-vacuity precondition.** The preservation proof is only meaningful because the synthetic meta it builds **allows** `"question"`; otherwise the allow-filter alone could produce the presence and the test would pass for the wrong reason. **T1** states the same precondition in the other direction.
48. **T9's coupling to the real leader meta.** The capability-E2E proof reads the **real** `agents/leader/meta.json`. A future change to the leader's allow-list / `innate_skills` turns T9 red — that is an **alarm, not flakiness** (the granting agent for the E2E pair changed ⇒ re-verify the pair). Do **not** "fix" it by loosening the assertion.
49. **The remaining `source_type`-less spawn lanes are recorded, not fixed.** `daemon/sources/adapters/slack/thread_manager.py:217` (**dead** — defined, no production caller) and `daemon/sources/mapper.py:452` (**test-only**; defaults the source to `"webhook"`, not a chat source ⇒ fails closed anyway). **When either is revived: thread the source type, or delete the lane** — reviving either without threading makes it a **silent false negative** under P1.
50. **The legacy unstamped-row delta is SILENT, and stays that way.** A row created before the `source_type` stamp existed (the writer dates to **2026-08-12**) does not satisfy P1 ⇒ a root restored from such a row **silently loses** the tool (no error, no warning — the gate simply does not register it). This is **pre-existing**, **out of scope**, and **not backfilled** (N8): a backfill would make the migration delta non-empty and break the P11 EMPTY-delta premise. The delta is **silent by design** (fail-closed), so it must be **disclosed in the final report**, not "detected" in production.
51. **The worktree's own `.venv` is the only venv used for this branch's tests.** The main checkout's `.venv` is an editable install resolving to `latest` ⇒ it imports the **wrong** `daemon` tree. Every targeted-suite run for this commission uses the worktree venv.
52. **The upgrade nonce ceremony is ROOT-ONLY and must NEVER be relayed by a non-root agent (R-a, P13).** The **root** must mint AND arm the nonce; a non-root reports `Blocked — missing user nonce` and stops. The pre-existing `ask_user` relay sentence in `agents/maintenancer/knowledge/06-restart-upgrade-runbook.md:21` stays a **deferred audit-trail item** (adjudicated, never bulk-rewritten). Final-report line, verbatim: *"the upgrade nonce ceremony is root-only; non-root agents must not relay it."*
53. **The two non-root wording families are DELIBERATE and must NOT be unified (R-b, P13).** A sweep classifies each site by **agent class** first — **plausibly-root** (`leader`, `maintenancer`, `project-manager`) ⇒ the **root-CONDITIONAL** form ("…unless you are the user-facing root"); **structurally-never-root** (`jober`, `_baby_template`, `giter`, `devops`) ⇒ the **FLAT** form ("never ask the user directly"). "Unify the two families" is a **rejected alternative** (§2 #25), not a cleanup.
54. **Canonical homes = 16** (Amendment #4; was **14** at Amendment #3, **13** before that). Measured by a tree grep of `^## Open questions for the orchestrator` — the exact list: `agents/_baby_template/rule.md`, `agents/_mother/rule.md`, `agents/coder/soul.md`, `agents/designer/soul.md`, `agents/devops/rule.md`, `agents/doc-writer/rule.md`, `agents/gaia/workflow.md`, `agents/giter/workflow.md`, `agents/governor/rule.md`, `agents/jober/rule.md`, `agents/leader/workflow.md`, `agents/maintenancer/rule.md`, `agents/project-manager/rule.md`, `agents/tester/rule.md`, `agents/wanderer/rule.md`, `agents/worker/rule.md`. The sealed baseline itself stays **35** entries.
55. **The seal re-pin ledger = 21 operations / 17 distinct sealed files** (Amendment #4): **14** (sweep) + **3** (fix round 1) + **1** (devops residual) + **3** (fix round 2). The **3** round-2 operations are **re-re-pins of already-counted files** (`agents/leader/rule.md` → `f0bda673…`, `agents/maintenancer/rule.md` → `abe43bae…`, `agents/project-manager/workflow.md` → `bde47909…`) ⇒ the **distinct** count stays **17** and the baseline split stays **17 re-pinned / 18 safe**.
56. **The gate-before-filter ordering is TESTED, not merely documented (W3, P2).** It is AST-pinned by `TestGateBeforeFilterOrdering::test_gate_registration_precedes_tool_filter_in_build_path` (`tests/unit/tools/test_question_tool_root_gate.py:627`) with an **in-test mutant self-check** — the pin must fail on an in-memory inverted copy of the real source. The gate pack is **13** tests. A reorder of the two call sites is a **red pin**, not a latent bug.
57. **The W1 rename is a naming-honesty correction, NOT a coverage change (P13).** `test_t4a_restore_path_root_with_stamped_row_has_question_tool` → `test_root_with_threaded_chat_source_type_has_question_tool`. The restore-path stamping remains **statically covered** by the **restore-lane threading seam** `daemon/services/instance_lifecycle.py:4667-4668`/`:4675`/`:4690` and by the **W3** AST pin (ordering).

---

## §4. Deferred / out of scope

| Item | Status | Note |
|---|---|---|
| `agents/watcher/meta.json` allow-list pinning | **Deferred — optional hygiene** | `allow: []` ⇒ default-open universe ⇒ watcher holds the tool today; the gate strips it. Pinning the list is a separate change (watcher currently *relies* on the default-open universe). Risk **F2** |
| `_tool_metadata` residue for the `question` category | **Deferred — cosmetic, and now sharpened (N5)** | The **help surface is computed from the AGENT META**, not from the built toolset: `daemon/tools/help.py:105-115` → `expand_allow_for_innate_skills` → `resolve_tool_filter`. So **every instance of a granting agent — including non-roots — sees `ask_questions` advertised** even though the gate never registers it. Separately, `_tool_metadata` (`_tool_registry.py:18`) is a **module-level global** populated by `register_tool_category` (`:395`) and `scan_tools_for_full_docs` (`:631`; registration `:660-662`), and it is cleared **only** by `clear_registry()` ⇒ **cross-instance residue**. Documented, not fixed |
| Mappings-lane source-type taxonomy beyond the chat set | **Deferred** | The chat set is pinned at 4 members. Adding a source type is a taxonomy change for a separate commission (the 3-member `_PLATFORM_INSTRUCTIONS` asymmetry is recorded in plan §1.4 and **not** re-litigated here) |
| Promote ceremony | **Owned by the user** | 3-factor nonce gate. This commission stages only |
| `whatsapp` platform prompt section | **Out of scope** | A whatsapp root passes P1 (it is a registered chat source) but receives no platform formatting section (no adapter — `upgrade_journal.py:3337`; `constants.py:128-144` lists no `whatsapp:` prefix). Recorded asymmetry — plan §1.4. Risk **N13** |
| The 3-member vs 4-member chat-set divergence | **Recorded, not decided** | Two independent taxonomies (`repository.py:919`, `upgrade_journal.py:3358`) both say 4; the *prompt-formatting* whitelist says 3. Not a predicate problem. N7 pins the **import** rather than a third copy |
| "All 14 chat instances are roots" live-data claim | **UNVERIFIED** | `ensemble_prod` is not reachable with this environment's credential; `ensemble_dev` holds 0 chat instances. Confirm at implementation time — the predicate fails closed, so the blast-radius estimate is what depends on it |
| Sibling-worktree "clean" claim | **Corrected** | It was 0 commits ahead but carried 7 modified + 3 new files — see P10. The work has since landed as `e2899f8bd` and is merged into this branch at `0e7500a1b` |
| Any change to the `question` tool's **validation, payload shape, or pause semantics** | **Out of scope** | The sibling commission owns pause/resume asymmetry; this commission owns **registration** only |
| Renaming the tool or the category | **Out of scope** | Breaks the frozen-name pins for zero functional gain — see §2 #9 |
| A structured (JSON/YAML) report section | **Rejected** | The report drain is a load-bearing path; the parent is an LLM, not a parser — see P6 |
| **Legacy rows without the `source_type` stamp** | **Out of scope — NO BACKFILL (N8)** | Pre-existing (the writer dates to 2026-08-12). Such rows do not satisfy the P1 predicate ⇒ a root restored from one does **not** get the tool. A backfill is **forbidden**: it would make the migration delta non-empty and break the P11 EMPTY-delta premise. Risk **F8**. **Amendment #3 — the delta is SILENT:** the gate simply does not register the tool; there is no error and no warning. It must therefore be **disclosed in the final report**, not "detected" in production (§3 #50) |
| **The remaining `source_type`-less spawn lanes** | **Deferred — recorded, not fixed (Amendment #3)** | (a) `daemon/sources/adapters/slack/thread_manager.py:217` (`ThreadManager.get_or_create_instance` at `:186`) — **DEAD**: defined but with **no production caller** (the live callers are `mapper.get_or_create_instance` from `daemon/sources/registry.py:1029` and `daemon/sources/scheduler.py:1064`); (b) `daemon/sources/mapper.py:452` (`handle_incoming_message`) — **test-only** (only `tests/test_sources_mapper.py`), and it defaults the source to `"webhook"` (`mapper.py:422`), which is **not** a chat source ⇒ fails closed anyway. Neither is in the commission's edit set. **Action when revived: thread the source type, or delete the lane** — reviving either without threading makes it a **silent false negative** under P1 (§3 #49) |
| **`agents/maintenancer/knowledge/06-restart-upgrade-runbook.md:21`** | **Deferred — pre-existing audit-trail item (Amendment #3)** | The line reads *"Relay verbatim via `ask_user`; agent MUST NOT fabricate or echo"* — an `ask_user` reference that is **not** one of the commission's ask-family sites (it is a **nonce-relay** instruction inside the 3-factor upgrade ceremony, not a turn-ending "ask the human" move). It is **pre-existing** and was **not** swept. **Deliberately left as an audit-trail item**: changing the nonce-relay wording would alter the ceremony's instructions for no contract gain. If a later sweep touches the ask family, this line must be **adjudicated, not bulk-rewritten**. **Amendment #4 (R-a, ratified):** the **upgrade nonce ceremony is root-only** — a non-root maintenancer **structurally cannot** complete a live upgrade (the root must mint AND arm the nonce) and **must not relay** it. The NO-EDIT is now a **ratified decision**, not an open item (§3 #52) |
| **The `create_instance_tools` test callers** | **Deferred — informational (F5)** | **53** test files contain the string `create_instance_tools` (`grep -rl` census) ⇒ they now build toolsets **without** the question tool. Benign today — and **no test asserts `ask_questions` membership in a built toolset today**, so nothing depends on it — but a future test that asserts the tool's *presence* would fail with a confusing cause. Re-derive the census at implementation time |
| **The round-2 sweep ledger** | **Adjudicated — the audit artifact** (Amendment #4) | The ledger holds **~230 rows** across **26** converted `agents/**` files, of which **~115** sites were **CONVERT**ed and **6** sites were caught **only by the manual semantic read** (no regex reached them). The reusable audit artifact is the **pattern set P1–P6 + the manual read** (§5): P1 = 21 briefed token patterns; P2 = two-way human-actor; P3/P3' = human-actor census + decision-verbs-near-human; P4 = whole-partition line-by-line census; P5 = delivery/escalation verbs; P6 = `human`; plus a **manual semantic read**. **P1/P2 are necessary-but-insufficient** (blind to acquisition verbs that name no human, to question-template forms, to `ask you`, to present+plan adjacency); **P3/P4 are the discriminating passes** |
| **The 11 round-2 NO-EDIT adjudications** | **Adjudicated — closed** (Amendment #4) | Each site was adjudicated and **left alone**: `agents/giter/rule.md:7` (**display-only**); `agents/giter/soul.md:12` + `agents/giter/workflow.md:41` (**input provenance**); `agents/devops/soul.md:46`/`:77` (the **channel is defined at `:49`**); `agents/devops/workflow.md:313` (**example annotation**); `agents/gaia/rule.md:56` (**consent-by-explanation is already the report channel**); `agents/_mother/rule.md:25` (**authorization-provenance gate**); the **`_mother` Q&A templates** (the **round-1 softening stands**); the **leader "report to user" output-direction family**; `agents/_baby_template/rule.md:31` + `agents/_inner_soul/*` (a **real out-of-band `pending_approval` gate**); and the **`growth.md`** conversions (**inert**). **Closed** — a later sweep does not re-open them (§3 #53) |
| **The 1 round-2 CONVERT adjudication** | **Adjudicated — converted** (Amendment #4) | `agents/gaia/**` + `agents/_mother/**`: the **flat** never-ask form becomes the **ROOT-CONDITIONAL** form, because both classes are **plausibly-root** (R-b). The 11 **NO-EDIT** sites and this **CONVERT** are the round-2 outcome set (P13) |

### F-series risk register (carried here; the plan's R-register holds the mitigation detail)

| # | Risk | Why it matters |
|---|---|---|
| **F1** | The mid-flight QA / answer-event fan-out becomes **root-only** | Only roots mint packs (sole production writer `daemon/tools/question_tools.py:502`) ⇒ the lane's traffic shape changes at the deploy. **No answering path is removed** (P9) |
| **F2** | `watcher` (`allow: []` ⇒ default-open universe) **loses** the tool | Behaviour delta for an agent that never asked for it; the gate strips it as a side effect. Allow-list pinning stays optional hygiene |
| **F3** | A **non-ari agent bound to a chat source** cannot ask | The mappings API can bind any agent; today no non-ari agent is known chat-bound ⇒ **LATENT**. Disclosed as a scoping limitation of the two-layer model (P4) and as a final-report line |
| **F4** | **Job roots and API roots lose the tool** | The predicate is **stricter** than "non-root" — it requires a *chat* source. Commission-endorsed (P1) but **user-visible**; it must be disclosed in the final report |
| **F5** | The **53** `create_instance_tools` test callers now build toolsets without the question tool | Benign today; a confusing cause if a future test asserts its presence. Informational only |
| **F6** | The **leader loses the question-SKILL text** while still needing to answer a root's pack | A **teaching gap, not a capability gap** — under the two-layer model the leader **KEEPS** the category in `tools.allow` ⇒ it retains the capability; only the shared skill text stops landing in its prompt (P5) |
| **F7** | The governor's **two** contract-bearing files | Closed by **RULING-2**: the contract lives once in `agents/governor/rule.md`; `agents/governor/workflow.md:46` becomes a pointer (P12) |
| **F8** | **Legacy unstamped rows** | Pre-existing, out of scope, no backfill (N8) — see the table above |
| **R7** | The clause-family edit rule must be **conditional** ("unless you are the user-facing root") | An unconditional rewrite would strip a **root's** own ask wording — degrading the population the commission preserves (P12) |
---

## §5. Evidence index

| file:line | What it proves | Used by |
|---|---|---|
| `daemon/tools/instance.py:5895-5900` | The unconditional `create_question_tools` registration — the gate site | P2 |
| `daemon/tools/instance.py:2450` | `create_instance_tools` signature (the two new keyword-only args) | P2 |
| `daemon/tools/instance.py:158-164` (`:163`) | `INNATE_SKILL_TOOL_CATEGORIES` — `"question"` is auto-granted by `innate_skills` alone (not privileged) | P2, P4 |
| `daemon/tools/instance.py:167` | `expand_allow_for_innate_skills` — the innate-skill → allow-list door | P4 |
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
| `daemon/repositories/instance/repository.py:919` | `CHAT_SOURCE_TYPES = ("telegram","slack","discord","whatsapp")` — the **class-attr definition**; `:343` / `:346` / `:374` are doc/use sites (was `:860` at `1ebdf469f`) | P1 |
| `daemon/repositories/instance/repository.py:1109` (and `:1134`) | The root test idiom `(parent_id IS NULL) \| (parent_id == "")` (was `:1049-1050` at `1ebdf469f`) | P1 |
| `daemon/tools/upgrade_journal.py:3358` | `USER_ORIGIN_CHAT_SOURCE_TYPES` — the same 4-member set, independently (a **frozenset**) | P1 |
| `daemon/tools/upgrade_journal.py:3337` | The "whatsapp is in the accept set but **NO whatsapp adapter exists**" comment — the forward-compat rationale for keeping `whatsapp` in the predicate set | P1, §3 (N13) |
| `daemon/tools/help.py:105-115` | The **help surface is computed from the AGENT META** (`expand_allow_for_innate_skills` → `resolve_tool_filter`) ⇒ every instance of a granting agent — **including non-roots** — sees `ask_questions` advertised | P4, §4 (N5) |
| `daemon/services/instance_lifecycle.py:1006-1036` | `_PLATFORM_INSTRUCTIONS` (3-member prompt set) | P1 (asymmetry) |
| `daemon/services/instance_lifecycle.py:1039-1081` (`:1075`, `:1078`, `:1053-1057`) | `append_platform_context` — the precedent: two pure gates over threaded values, and the docstring naming the spawn-time row-read failure | P1, P2 |
| `daemon/services/instance_lifecycle.py:1801-1812` (`:1805`, `:1811`) | `spawn_instance` signature — `parent_id` and `source_type` locals | P2 |
| `daemon/services/instance_lifecycle.py:2137` | Spawn-time `create_instance_tools` call (before the row exists) | P1, P2 |
| `daemon/services/instance_lifecycle.py:2389-2390` | **The single `source_type` writer** | P1, P3 |
| `daemon/services/instance_lifecycle.py:2433-2445` / `:5343` / `:5417` / `:5451` | The DB write is delegated to `_spawn_instance_db_sync` (called at `:2433-2445`; the helper builds `Instance(...)` at `:5417` and commits at `:5451`) — all **after** the tool build at `:2137` | P1 |
| `daemon/services/instance_lifecycle.py:2446-2450` | `original_source` inheritance | P1 |
| `daemon/services/instance_lifecycle.py:4667-4668` / `:4675` / `:4690` | Restore path: `source_type` from the row, `parent_id=meta.parent_id`, the second `create_instance_tools` call (was `:4595-4597` / `:4603` / `:4618` at `1ebdf469f` — shifted by the sibling merge) | P2 |
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
| `daemon/tools/job_queue.py:620` (docstring) / `:3403` | `job_answer` — the root-without-the-tool answering surface; **`async def job_answer` is at `:3403`** (the council's `:770` is a *reference inside* the docstring) | P9 |
| `daemon/loader.py:841-847` / `:856-858` / `:868` | Innate-skill path resolution; empty `innate_skills` ⇒ legacy scan; missing dir ⇒ WARN only | P4, P5 |
| `daemon/tools/prompt_artifacts.py:119-140` / `:257` | `VERSIONED_PROMPT_ARTIFACTS`; depth-2-only coverage | P4, P5, P11 |
| `agents/.prompt-artifacts.sha256` (161 entries) | The prompt-artifact baseline — covers depth-2 `.md` only; **0** `meta.json` entries | P4, P11 |
| `agents/leader/meta.json:8` / `:14-15` | leader `innate_skills` and `tools.allow` both carry `"question"` (**pre-change**) ⇒ P4 removes the **`innate_skills`** entry only; the `tools.allow` entry is **kept** | P4 |
| `agents/devops/meta.json:8` / `:12` | devops `innate_skills` and `tools.allow` both carry `"question"` (**pre-change**) ⇒ P4 removes the **`innate_skills`** entry only; the `tools.allow` entry is **kept** | P4 |
| `agents/ari/meta.json:9` / `:11-38` / `:39` | ari holds **neither** today; the grant is a real add | P4 |
| `agents/project-manager/meta.json:54` / `:64` | The `deny` array; `"question"` inside it — already compliant | P4 |
| `agents/critic/meta.json:13` / `:14` | critic's allow/deny — never held it | P4 |
| `agents/watcher/meta.json:22` | `"allow": []` — default-open universe | P4 |
| `agents/_prompt_system/innate-skills/question/skill.md` (53 lines) | The current unconditional-ask teaching text | P5 |
| `agents/project-manager/soul.md:58-59` / `:78-79` | The `## Decisions Pending` / `<0–3 framed questions>` precedent | P6 |
| `agents/leader/workflow.md:703` | The "begin work with a tool call, deliver your report, or ask" clause (the leader's home) | P6 (sweep) |
| `agents/governor/rule.md:82` | The **BEGIN/DELIVER/OR ASK** clause — the *last sentence* of a long line that **also** carries the junk-report clause; carries **1 ×** "Chat Delivery"; **SEALED** (pre-sweep `84a93586…` at `tests/test_chart_image_delivery_e2e.py:1387`; current `a779742d…` at `:1392`). **Landed wording:** "…or report open questions (unless you are the user-facing root)" | P12 |
| `agents/governor/workflow.md:46` | The governor's "complete-to-ask" paragraph ⇒ becomes a **section-name pointer** under RULING-2 (the contract lives once in `rule.md`) | P12 |
| **41** clause-family sites (was 34) across the sweep edit-set; 14 files carry the exact phrase `begin, deliver, or ask`, 16 lines carry the `deliver, or ask` variant | The clause-family sweep set — narrower than the 37 files containing `or ask` overall. The **41** is the canonical count (it grew with the fix round's sweep completion: **6 EDIT** + **2 NO-EDIT adjudicated** + **2 disclosed scope additions**, plus the devops family) and is **UNCHANGED by fix round 2** — round 2 converted **other** semantic classes (acquisition / escalation / human-decision verbs), not clause-family sites | P6 (sweep), P12, P13 |
| `agents/ari/tools_note.md:453` | `"relay them, wait, or ask the user"` — upgrade-interlock semantics, **not** in the sweep set | P6 (scope boundary) |
| `tests/unit/tools/test_upgrade_registration.py:102` / `:142` | Exact-set pin #1 | P2 |
| `tests/unit/tools/test_attestation_registration.py:142` / `:186` | Exact-set pin #2 | P2 |
| `tests/integration/test_maintenancer_spawn_resolves_tools.py:288` / `:324` | Exact-set pin #3 | P2 |
| `tests/integration/test_service_tool_flag_off_byte_identical.py:808` / `:823` | Exact-set pin #4 | P2 |
| `tests/unit/tools/test_secrets_registration.py:218` | Exact-set pin #5 | P2 |
| `tests/test_innate_skills_refactoring.py:67` | leader `innate_skills == ["coordination","chart","todo"]` — currently RED, turns GREEN | P4 |
| `tests/unit/test_devops_agent.py:165` | devops `innate_skills == ["dynamic-skill","todo"]` — currently RED; must become `["dynamic-skill","chart","todo"]` | P4 |
| `tests/test_chart_image_delivery_e2e.py:1350` / `:1418` | `SEALED_SHA_BASELINES` (**35** files) + the SHA tripwire | §3 #41, P4, P5, P12 |
| `tests/test_chart_image_delivery_e2e.py:1387` / `:1392` | The sealed sha of `agents/governor/rule.md` — **pre-sweep** `84a93586df376ad39dc553af36f86f684977ecfa8d717186f2fe8f16846c8cf5` (`:1387`) → **current** `a779742dcec8d30eb44495afda0c5ada93b9409a4ffac83912f7af2afa22ad37` (`:1392`), both verified byte-identical to the working-tree file at their respective commits | P12 |
| `tests/unit/tools/test_prompt_section_reference_integrity.py` | The bare-`.md` / bare-`agents/` cross-reference sweep | P6, P12 |
| `tests/unit/tools/test_attestation_prompt_contract.py:48-49` | The **attestation semantic pin** — leader `rule.md` + `workflow.md` must KEEP the attestation vocabulary and must NOT gain the forbidden phrasings | P12, §3 #27 |
| `tests/unit/tools/test_mission_watch_prompts.py` | The **mission-watch semantic pin** — `watch_mission` vocabulary present, **zero** `watch_job` tokens (9 files, 0 `watch_job`) | P12, §3 #27 |
| `tests/test_chart_image_delivery_audit.py:58,467-480` | The **"Chat Delivery"** pin — the literal must survive verbatim in the **20** chart-capable agents | P12, §3 #27 |
| `tests/unit/test_report_integrity_prompts.py:92` / `:106` / `:110` / `:115` | `D_HOME_FILES` / `E_HOME_FILES` both name `rule.md` for `governor` (⇒ its single home); `D_REQUIRED_SNIPPETS` = `"[REPORT SANITY:"`, `"interim, not completion"`, `"send_message"`; `E_REQUIRED_SNIPPETS` = `"before ending any turn"`, `"zero tool calls"` | P12, §3 #25, #28 |
| `tools/dev/verify_service_default_open.py:87` | The dev verifier's **UNPINNED** `tool_count` (shifts by **−1** when the question tool is no longer registered; **no assertion depends on it**) — and the corollary that **no test asserts `ask_questions` membership in a built toolset today** ⇒ the new tests must be genuinely discriminating | P2, P4, §4 (F5) |
| 53 test files containing `create_instance_tools` (`grep -rl` census) | The test-callers that now build toolsets **without** the question tool — benign today, confusing cause if a future test asserts its presence | P4, §4 (F5) |
| `tests/unit/tools/test_frozen_tool_name_discovery.py:223` | `KNOWN_TOOL_NAMES` must equal the source-discovered set exactly ⇒ the question tool source must not be deleted | P2, P9 |
| `scripts/bump_version.py:84-109` / `:133-145` | The 4-file version-bump set + the conditional `CHANGELOG.md` | P11 |
| `scripts/protection/generate_prompt_artifact_baseline.py` | The prompt-artifact baseline generator/verifier | P11 |
| `scripts/upgrade/stage.sh` | The staging entry point (provenance sidecars, tip-identity guard) | P11 |
| Sibling branch `fix/question-pause-resume-asymmetry` — tip **`86fe0e03a`**, **7 commits ahead** of `latest` (`843b669c5`), **13 files**; merge-base `1ebdf469f` ⇒ **still NOT merged to `latest`** | The sibling's current state — `instance_messaging.py` is **NO LONGER** in their diff; new files `daemon/services/pool_orchestrator.py`, `daemon/services/question_pause_set.py`, `daemon/services/stale_pause_sweep.py`. Overlap payload **corrected** per the leader (KV `wt.claim.hide-question-tool-nonroot.overlap_CORRECTED`) | P10 |
| `tests/unit/tools/test_question_tool_root_gate.py` (commit **`925f9aced`**) | The **gate pack** — the real, landed gate-proof + preservation + capability-E2E file: **T1** (`:191`), **T3** (`:220`), **T4b** (`:246`), **T5** (`:270`), **T2** (`:306`), **T4a** (`:330`), the legacy-`""`-parent case (`:358`), the **T9** capability-E2E pair (`:402` root half, `:418` child half), the **AC3 keyword-only signature pin** (`:441`), the three-way chat-source taxonomy equality pin (`:563`), the **mappings-lane proof** (`:490`, class `:477`), and — since Amendment #4 — the **W3 gate-before-filter structural pin** (`:627`, class `:625`). The pack is **13** tests (was 12) | P2, P3, P4, §0, P13 |
| `tests/unit/tools/test_question_tool_root_gate.py:44-63` | The **HARNESS NOTE** — why the harness is **signature-adaptive**: it passes the two new kwargs **only** when the signature already supports them, so the preservation guards fail pre-change with a **clean assertion**, not a `TypeError`; the adaptivity cannot hide a missing implementation (the AC3 pin is RED pre-change) | §0, §3 #42 |
| `tests/unit/services/test_child_report_open_questions_flow.py` (commit **`12291f2a4`**) | The **flow-proof file** — **16 cases**: **T6 × 5** (class `TestT6OpenQuestionsReachParent` at `:317`), **T7 × 7** (class `TestT7ParentInstructionRevivesTerminalChild` at `:537`; `:548` parametrized over the 4 terminal statuses), **T8 × 4** (class `TestT8LegacyPackStaysAnswerableWithoutTheQuestionTool` at `:857`). **Mutation-verified 5/5** | P6, P7, P9, §0 |
| `agents/devops/rule.md:9` / `:32` / `:91` / `:92` / `:110` / `:114` / `:156` / `:164` / `:203` | The devops **ask-family anchor drift** — the family was adjudicated **by construct**, not by the briefed line numbers (which were **stale**). Each site now carries the non-root form ("report it as an open question … never ask the user directly") | P4, P12, §3 #45 |
| `agents/devops/soul.md:46` / `:49` / `:77` | The devops **soul.md** production-approval framing reconciled to the report channel (same construct-based adjudication) | P4, P12 |
| `agents/devops/rule.md:74` | The canonical `## Open questions for the orchestrator` section in devops — copied **verbatim** from the `agents/tester/rule.md` home ⇒ **devops is the 14th canonical home** | P12 |
| `agents/tester/rule.md:135` | The **source** of the canonical section text that devops copies verbatim | P12 |
| `tests/test_chart_image_delivery_e2e.py:1357` / `:1390` / `:1392` / `:1393` / `:1394` | The **current** seal constants after the fix round: `charter/workflow.md` `2a5078f9…`, `devops/rule.md` `febf0e89…`, `governor/rule.md` `a779742d…` (all **35** constants independently re-verified from real bytes; pre-edit mismatch count was **exactly 3** ⇒ **no pattern-fill**) | P12, §3 #41 |
| `tests/test_chart_image_delivery_e2e.py` (round-2 constants, `:1396`-block) | The **3 round-2 seal re-pins**, every value **recomputed from real file bytes** on the round-2 worktree: `agents/leader/rule.md` → `f0bda673c7b112ef7a43f17f1166f99fcb11f1562aabf0d7a5c9879e924a231a`, `agents/maintenancer/rule.md` → `abe43bae715c62a341788fbe5d610138936b7b4e83df87c8bbfd3611bf446aec`, `agents/project-manager/workflow.md` → `bde479095bbda6a3e3e069afe8a719e0f8b5673f289d2d0d5bf7a15eecb123ad`; the other **32** entries untouched, **35/35** re-verified; the seal-file diff is **3 constants + 6 comment lines** | P12, P13, §3 #55 |
| The **worktree's own `.venv`** (`/home/nea/ensemble-src-wt-hidenonroot/.venv`) | The **only** venv that may run this branch's tests — the main checkout's `.venv` is an editable install resolving to `latest` ⇒ it imports the **wrong** `daemon` tree. Verify with `python -c "import daemon; print(daemon.__file__)"` before any targeted run | P10, §3 #51 |
| `agents/.prompt-artifacts.sha256` (**161** entries, regenerated ×2 in the fix round) | The prompt-artifact baseline — drift was **exactly the 12 depth-2 edits**; `--check` **161 OK**, `sha256sum -c` **161 OK** | P11, P12 |
| `tests/unit/tools/test_question_tool_root_gate.py:627` (class `TestGateBeforeFilterOrdering` at `:625`) | **W3 — the gate-before-filter structural pin.** Resolves **both** call sites of `daemon/tools/instance.py::create_instance_tools` with the **AST** (never a text grep, never a hardcoded line number) and asserts the `create_question_tools(...)` **registration precedes** the `_apply_tool_filter(...)` **application** ⇒ an agent's `tools.allow` entry can never re-admit a gated tool for a non-root. Ships with an **in-test mutant self-check** (the pin must FAIL on an in-memory inverted copy) ⇒ it cannot degrade into a no-op | P2, §3 #56, §0 |
| `tests/unit/tools/test_question_tool_root_gate.py:339` (old name at `:345`) | **W1 — the honest rename.** `test_t4a_restore_path_root_with_stamped_row_has_question_tool` → `test_root_with_threaded_chat_source_type_has_question_tool`. The old name asserted a **restore-path** scenario the test does not exercise; the honest name states what it asserts (a **root with a threaded chat `source_type`**). The **restore-path stamping stays statically covered** by the **restore-lane seam** below and by the **W3** AST pin (ordering) ⇒ **naming-honesty, not a coverage loss** | §0, §3 #57, P13 |
| `daemon/services/instance_lifecycle.py:4667-4668` / `:4675` / `:4690` | The **restore-lane threading seam** — the restore `source_type` row read, `parent_id=meta.parent_id`, and the second `create_instance_tools` call. The static cover that the W1 rename did **not** remove | P2, §3 #57 |
| The **gate-pack mutation proof** (read-only, scratch worktree, **removed**) | The **W3** pin is **mutation-verified**: **M1** neuter the gate ⇒ **5 REDS**; **M2** neuter only the chat-source half ⇒ **2 REDS** (T1/T5 stay green ⇒ the **predicate halves are independently pinned**); **M3** drop the section in transit ⇒ **2 REDS**; **M3b** MessageQueue carrier ⇒ **1 RED**; **UNDETECTED: NONE**. Orthogonality: M1/M2 ⇔ flow **16/16**, M3/M3b ⇔ gate **13/13** | P2, §0, §5 |
| The **sweep pattern set P1–P6 + the manual semantic read** | The **reusable audit artifact** of the round-2 sweep (not a one-off edit list): P1 = **21** briefed token patterns; P2 = two-way human-actor; P3/P3' = human-actor census + decision-verbs-near-human; P4 = whole-partition line-by-line census; P5 = delivery/escalation verbs; P6 = `human`; plus a **manual semantic read** that caught **6 sites no regex reached**. **P1/P2 are necessary-but-insufficient** (blind to acquisition verbs that name no human, to question-template forms, to `ask you`, to present+plan adjacency); **P3/P4 are the discriminating passes** | P12, P13, §4 |
| The **canonical-homes 16 grep** (`^## Open questions for the orchestrator`, tree-wide) | The measured canonical-home set (**16** files): `_baby_template/rule.md`, `_mother/rule.md`, `coder/soul.md`, `designer/soul.md`, `devops/rule.md`, `doc-writer/rule.md`, `gaia/workflow.md`, `giter/workflow.md`, `governor/rule.md`, `jober/rule.md`, `leader/workflow.md`, `maintenancer/rule.md`, `project-manager/rule.md`, `tester/rule.md`, `wanderer/rule.md`, `worker/rule.md` (round 2 added **`maintenancer/rule.md`** and **`project-manager/rule.md`**: 14 → 16) | P12, §3 #54 |

---

*End of decision record. The implementation body lives in [`plan-overview.md`](./plan-overview.md) — plan sections referenced by heading name above: §1.1/§1.3/§1.4 (P1), §2.1/§2.2/§2.3/§2.5/§2.6 (P2), §3 (P3), §4/§10.3 (P4), §5/§5.1 (P5), §6 (P6), §7/§7.1/§7.2 (P7), §8 (P8), §9/§15 (P9), §12.1–§12.3 (P10), §13 (P11), §10/§10.1–§10.9/§11.2–§11.4 (P12).*

*Amendment #3 (2026-10-20) — fix round landed at `12291f2a4`. Canonical numbers for BOTH files: sweep edit-set **66** · clause-family sites **41** · canonical homes **14** · re-pins **18** (operations; **17** distinct sealed files) · sealed baseline **35** · branch-diff reconciliation **70 = 1 + 3 + 66** · tests **T1–T9** · phases **0–9**. **⟵ superseded by Amendment #4.***

*Amendment #4 (2026-10-20) — fix round 2 landed at `4088be633` (on top of the tester's `b7e3a0808`). **FINAL canonical numbers for BOTH files:** branch partition **93** = 74 agents prose + 3 `meta.json` + 1 baseline + 3 daemon + 3 docs + 7 tests + 2 planning docs · **commission surface 91** · sweep edit-set **74** prose · clause-family sites **41** (unchanged) · canonical homes **16** · re-pins **21 operations / 17 distinct sealed files** (split **17 / 18** of 35) · gate pack **13** · tests **T1–T9** + **W3** (gate-before-filter AST pin) + **W1** (honest rename) · phases **0–9** · batteries: **7 pre-existing failures, ZERO new**.*

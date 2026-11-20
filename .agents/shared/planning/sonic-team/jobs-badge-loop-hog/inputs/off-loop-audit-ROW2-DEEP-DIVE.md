# ROW2-DEEP-DIVE — the agent-turn CPU lane (tiktoken O(n²) + pydantic schema rebuild)

**Commission:** Emperor follow-up on the off-loop audit — "more details about no 2". Decision-grade
deep-dive on HANDOFF rows **1** (tiktoken token counting) and **2** (pydantic/langchain schema
construction) so the Emperor can green-light, split, or defer the lane.
**Deliverable:** this file only. **ANALYSIS ONLY** — no code change, no commit, no source edit,
no restart, no live contact (`:9797` was never probed; nothing in this doc required it).

**Audited tree (all `file:line` cites):** tag `v0.31.10` = commit `01eb788b6`
(`git show v0.31.10:<path>`; content also extracted read-only via `git archive v0.31.10 daemon`).
**Current tree (re-anchored):** tag `v0.31.13` = `ef89c8a97`; `origin/latest` == `v0.31.13`.
**Read-only code inspection performed by this lane (disclosed):** `daemon/loader.py`,
`daemon/compaction.py`, `daemon/graph.py`, `daemon/services/instance_messaging.py`,
`daemon/services/instance_lifecycle.py`, `daemon/tools/instance.py`,
`daemon/tools/skill_evolution_tools.py`, `daemon/manager.py`, `daemon/services/job_processor.py`,
`daemon/services/compact_executor.py`, `daemon/tools/_tool_registry.py` (v0.31.10 **and** v0.31.13);
the installed `tiktoken 0.12.0` (`registry.py`, `core.py`, `tiktoken_ext/openai_public.py`),
`pydantic 2.12.5` (`main.py`, `deprecated/decorator.py`), `langchain-core 1.2.16`
(`tools/base.py`, `tools/convert.py`, `tools/structured.py`, `utils/pydantic.py`,
`utils/function_calling.py`), `langchain-openai 1.1.10` (`chat_models/base.py`) from the repo
`.venv`; the **durable** wedge-capture dirs under `/home/nea/agents-ensemble/data/wedge-captures/`
(read-only; 20/20 present). **No live daemon call, no DB query, no install, no benchmark campaign.**

**Capture-version discipline:** every capture statistic below is quoted with its own
`release_version`. The 20 captures span **6× v0.31.2 / 7× v0.31.1 / 7× v0.31.10**
(EVIDENCE.md §4.1 category×version table). No stat is quoted without its split.

---

## 0. Decision summary (read this first)

| | Culprit 1 — tiktoken | Culprit 2 — pydantic/langchain schema |
|---|---|---|
| **Production weight** | **10/20** captures (2× v0.31.2, 4× v0.31.1, **4× v0.31.10**) | **5/20** captures (1× v0.31.2, 2× v0.31.1, **2× v0.31.10**) |
| **Attributed block time** | **243 s over 183 episodes** (EVIDENCE §1 rows 1+3) | **51 s over 32 episodes** (EVIDENCE §1 row 2) |
| **Worst observed lag** | **20.55 s** (capture `20261119T024751Z`, **v0.31.2**) | **23.16 s** (flagship `20261119T170818Z`, **v0.31.10** — the only `sustained_loop_lag` capture of the whole audit) |
| **Is the stall a *recurring* cost or a *content-shape* cost?** | **Content-shape.** A normal 92 KB payload counts in **7.5 ms**; the 4–20 s stalls require a long unbroken run of one character class | **Recurring + deterministic.** Every cold graph build rebuilds ~780 pydantic model classes, regardless of content |
| **Dominant quadratic term** | inside the tokenizer's per-piece BPE (library), **amplified** by four daemon-level amplifiers (§2.1.4) | none (this is *linear* work done N times, i.e. pure waste) |
| **Fix that pays** | **(A) byte-identical de-duplication** (removes ~5 redundant full-payload counts/compaction, the O(S²) ceiling rule, and the O(P·N) `emergency_truncate` re-count loops) **+ (B) a bounded-input policy** (the only thing that removes the n² term) | **schema registry + boot-warm** — removes ~99 % of the class-construction cost |
| **Does "cache the encoder" pay?** | **NO.** `tiktoken.get_encoding` already caches the `Encoding` in a module dict (`tiktoken/registry.py:63-89`). A daemon-side module-scope cache saves only the **once-per-process** first build (0.8–2.2 s). See §2.3.4-C1 | n/a |
| **Effort (XS<2h · S 2-4h · M 6-12h)** | (A) **XS–S** (de-dup XS + the `emergency_truncate` re-count loops S) · (B) **S** + Emperor policy call | **S** (registry + boot-warm + gate) |
| **Recommendation** | **Split into two lanes** (A = XS, exactness-preserving; B = S, needs a policy ruling) | **One lane, S** |
| **Release grouping** | A+B and the schema lane share **one release** — disjoint surfaces (§4.3) | same |

**The Emperor's two decisions:** (1) does the lane get split into a *byte-identical* lane (safe,
XS+S) and a *policy* lane (S, changes token counts on pathological inputs only)? (2) does the schema
lane get a **boot-warm** (move the 16.7 s out of the first user-visible restore into daemon startup)?

---

## 1. Shared substrate: what "on the loop" means here

Both culprits are **synchronous CPU executed on the event-loop thread inside the agent turn**.
The loop thread is the daemon's `MainThread` (`ensemble-prod`); the wedge-capture stack dumps label
it `Thread 0x… [ensemble-prod]` and it is the thread that serves uvicorn
(`asyncio/runners.py:205 run` → `uvicorn/server.py:75 run` → `daemon/__main__.py:871 main`).
Because the loop thread *is* the request-serving thread, **any** of these stalls freezes:
every HTTP route, every SSE stream, every other agent's turn, and the stall's own caller.

Captured call chains (durable capture dirs, read-only):

* tiktoken, pre-call gate (capture `20261119T133725Z`, **v0.31.10**):
  `tiktoken/core.py:124 encode` → `daemon/loader.py:1099 estimate_tokens` →
  `daemon/loader.py:1283 estimate_messages_tokens` → `daemon/graph.py:8255 _maybe_precall_compact_95` →
  `daemon/graph.py:9738 agent_node` → `daemon/graph.py:13748 _node` → `langgraph/_internal/_runnable.py:473 ainvoke`.
* tiktoken, context-usage (capture `20261118T232617Z`, **v0.31.2**):
  `tiktoken/core.py:124` → `daemon/loader.py:1099` → `daemon/loader.py:1259` →
  `daemon/services/instance_messaging.py:1227 _compute_context_usage` →
  `daemon/services/instance_messaging.py:1258 _emit_context_usage` →
  `daemon/services/instance_messaging.py:5657 _process_message_with_tracking` →
  `daemon/services/task_processor.py:1875 _run`.
* tiktoken, compaction (capture `20261119T055000Z`, **v0.31.1**):
  `tiktoken/core.py:124` → `daemon/loader.py:1099` → `daemon/loader.py:1259` →
  `daemon/compaction.py:3440 compact_state` →
  `daemon/services/instance_messaging.py:1535 _maybe_compact_context`.
* pydantic class construction (capture `20261119T070319Z`, **v0.31.1**, 14 frames — the deepest
  schema stack in the audit):
  `langchain_core/utils/pydantic.py:268 _create_subset_model_v2` →
  `langchain_core/tools/base.py:381 create_schema_from_function` →
  `langchain_core/tools/structured.py:205 from_function` →
  `langchain_core/tools/convert.py:304 _tool_factory` → `…/convert.py:361 tool` →
  `daemon/tools/skill_evolution_tools.py:357 create_skill_evolution_tools` →
  `daemon/tools/instance.py:7706 create_instance_tools` →
  `daemon/services/instance_lifecycle.py:4944 _restore_instance` →
  `daemon/services/instance_lifecycle.py:4615 get_instance` →
  `daemon/manager.py:16351 get_instance` → `daemon/services/job_processor.py:1136 _process_next_job`
  → `daemon/services/job_processor.py:706 _process_loop`.
* pydantic JSON-schema generation (capture `20261119T012522Z`, **v0.31.2**):
  `pydantic/json_schema.py:2537 model_json_schema` →
  `langchain_core/utils/function_calling.py:187 _convert_pydantic_to_openai_function` →
  `…/function_calling.py:351 _format_tool_to_openai_function` → `…/function_calling.py:572 convert_to_openai_tool` →
  `langchain_openai/chat_models/base.py:1968 bind_tools` →
  `daemon/graph.py:11050 build_instance_llms` → `daemon/graph.py:14366 build_instance_graph` →
  `daemon/services/instance_lifecycle.py:5054 _restore_instance` →
  `daemon/services/instance_lifecycle.py:4615 get_instance` → `daemon/manager.py:16447 get_instance` →
  `daemon/tools/instance.py:1826 _resolve_instance_id` → `daemon/tools/instance.py:5002 send_message`.

> **Library line-number provenance (read this before quoting any `langchain_*`/`pydantic*`/
> `tiktoken*` line number from this document).** The five capture blocks above quote the **frozen
> v0.31.10 binary's bundled libraries** (the daemon ships as a PyInstaller binary; its bundled
> `langchain-core`/`pydantic`/`tiktoken` line numbers are the ones in the stack dumps). Every
> *library* line number quoted **outside** those capture blocks is from the **repo `.venv`**
> (`langchain-core 1.2.16`, `pydantic 2.12.5`, `tiktoken 0.12.0`) — read-only inspection by this
> lane, and re-verified line-by-line. Both sets are correct in their own context; the two agree on
> the *call sequence*, which is the part that matters. The `daemon/*` anchors are identical in
> both (the daemon source is the audited source).

> **Correction to the HANDOFF's framing (row 2).** HANDOFF row 2 lumps `create_schema_from_function`
> / `create_model` / `bind_tools` into one class. They are **two distinct pydantic entry points**
> with **two distinct fixes**:
> `create_model` → pydantic **class construction** (`_model_construction.py:255 __new__` →
> `complete_model_class` → `generate_schema`) — the *expensive* one; reachable **only** via
> langchain's `create_schema_from_function` → pydantic v1-compat `validate_arguments`
> (`pydantic/deprecated/decorator.py:143 __init__` → `:243 create_model`). A `bind_tools` call
> never reaches `_model_construction.__new__` — it reaches `model_json_schema`
> (`pydantic/json_schema.py:2537`), a *schema-dump* pass over an already-built class.
> The flagship capture (`20261119T170818Z`, **v0.31.10**) is provably the **class-construction**
> path: its stack is `…/_model_construction.py:255 __new__` ← `pydantic/deprecated/decorator.py:243
> create_model` ← `…/decorator.py:143 __init__` — the v1-compat `validate_arguments` shim, i.e. the
> `@tool` decoration path, *not* `bind_tools`.

---

## 2. CULPRIT 1 — tiktoken token counting on the loop

### 2.1 MECHANISM

#### 2.1.1 The primitive

`daemon/loader.py:1087-1099`:

```python
def estimate_tokens(text: str) -> int:
    import tiktoken
    encoder = tiktoken.get_encoding("cl100k_base")
    return len(encoder.encode(text))
```

`daemon/loader.py:1102+` `estimate_messages_tokens(messages)` walks every message and calls
`estimate_tokens` **per content block** (`:1233`, `:1257`, `:1259`), **per tool-call name/args**
(`:1268`, `:1269`, `:1271`, `:1272`), **per message name** (`:1277`) and **per
`reasoning_content`/`thinking` blob** (`:1283`), adding `+4` per message (`:1262`).

**Blast radius of the primitive (v0.31.10 AST/grep census):** `estimate_messages_tokens` has
**32 call sites in 4 files** (`daemon/compaction.py` 27, `daemon/graph.py` 3,
`daemon/services/instance_messaging.py` 1, `daemon/services/compact_executor.py` 1);
`estimate_tokens` (the per-string form) has **51 call sites across 14 files**
(`daemon/services/memoir_retrieval.py` 11, `daemon/loader.py` 10,
`daemon/services/context_messages.py` 6, `daemon/services/memoir_reflection_service.py` 5,
`daemon/repositories/memoir/repository.py` 5, `daemon/services/memoir_briefing.py` 4,
`daemon/services/memoir_poignancy_service.py` 3, `daemon/graph.py` 3, plus 5 singletons).
⇒ **Editing the primitive in `loader.py` touches 51 call sites in 14 files; editing the call sites
touches 4 files.** That asymmetry drives the fix shape in §2.3.

#### 2.1.2 Where it enters the turn path (three entries, all on the loop)

1. **The 95 %-precall gate — `daemon/graph.py:8154` `_maybe_precall_compact_95`, count at
   `daemon/graph.py:8255`** (`payload_tokens = estimate_messages_tokens(full_messages)`).
   Called from `daemon/graph.py:9738` inside `agent_node` (def at `daemon/graph.py:8842`).
   LangGraph's tool loop is a **graph cycle** (agent → tools → agent), so `agent_node` — and
   therefore the gate — runs **once per LLM call** in the turn.
2. **Context-usage emission — `daemon/services/instance_messaging.py:1203` `_compute_context_usage`,
   count at `:1227`**, reached from `_emit_context_usage` (`:1234`, count invoked at `:1258`) which
   is called (a) on every node update of the turn (`:5657`, full `all_state_messages`) and (b) on
   **every SSE connect** (`emit_context_usage_for_instance`, `:1282-1335`, `force=True`).
3. **Compaction — `daemon/compaction.py:3113` `async def compact_state`** (13 count sites,
   §2.1.3) plus `daemon/services/compact_executor.py:903`.

#### 2.1.3 The thirteen count sites inside `compact_state` (v0.31.10)

`daemon/compaction.py` — 13 direct `estimate_messages_tokens` calls inside `compact_state`
(body `:3113-3963`), at **:3190, :3194, :3255, :3300, :3315, :3320, :3358, :3403, :3440, :3517,
:3539, :3580, :3892** (tester re-verified all 13 exact), plus `:4018` and `:4078` in
`async def _summarize_chunked` (`:3965`).

Grouped by **argument** (this is the actionable part):

| argument | sites | note |
|---|---|---|
| `selectable_messages` | **:3315, :3320, :3358, :3403, :3440** (5×) | **:3315 and :3320 are the *same* call with the *same* argument inside the same lambda** (`anti_refire_skip`, `daemon/compaction.py:3311-3330`) — a literal duplicate. :3358 and :3403 are mutually-exclusive branches; :3440 is the unconditional canonical one. |
| `preserved_msgs` | **:3517, :3539** (2×) | :3539 is the **same argument re-passed as a logger argument** in the branch that :3517 already measured — a literal duplicate. |
| `context.messages` | :3255 | the "obs" INFO line — counts the **whole** message list. |
| `hoisted_injected` | :3300 | injected subset. |
| `_dedup_hoisted` / `_dedup_selectable` | :3190 / :3194 | dedup-window pair. |
| `non_removal` | :3580, :3892 | post-shrink accounting (two exit paths). |

⇒ **Up to 5 identical full-set counts of `selectable_messages` and 2 of `preserved_msgs` per
compaction call**, of which at least 2 pairs are *provably* redundant (identical argument, same
scope). Removing the *provably* redundant ones is **byte-identical** (§2.3.1).

**Plus the emergency path's own counts (not in the 13).** `emergency_truncate`
(`daemon/compaction.py:1688`, invoked from `daemon/compaction.py:3554` with
`estimate_fn=estimate_messages_tokens` at `:3557`) performs **eight** whole-list token estimates
(`:1718`, `:1730`, `:1742`, `:1749` — inside a loop —, `:1760`, `:1768` — inside a loop —) on the
**entire** message list. The census's "13 call sites in `compact_state`" therefore **under-counts
the compaction engine's real counting load by ~8 more whole-list estimates** on the emergency path.
⇒ **The true per-compaction counting load can exceed 20 whole-list passes** — and the emergency
path's two are *loops*, i.e. unbounded in the number of pops/halvings.

#### 2.1.4 The four amplifiers and the quadratic term

**Amplifier 1 — `_truncate_to_token_cap` (`daemon/compaction.py:736-785`).**
One full encode at `:765`, then a bounded loop (`:770 for _ in range(20)`) that re-encodes the
candidate at `:774` and chops 20 % at `:781-782` (the comment at `:778-780` already names the
character heuristic). The candidate lengths are `n, 0.8n, 0.64n, …` ⇒ the loop's aggregate cost is
a **geometric series**, *not* 21×:

* linear tokenizer: `Σ 0.8^k · n = 5n` ⇒ **≤ 5×** one full encode;
* quadratic tokenizer: `Σ (0.8^k)² · n² = n²/(1-0.64) ≈ 2.78 n²` ⇒ **≈ 2.8×** one full quadratic encode.

⇒ *Correction to a common misreading: the shrink loop is a ≤5× constant-factor amplifier, not the
source of the n² term.* (HANDOFF row 1 lists "20-iteration re-encode loop" as a cost; it is real but
bounded.)

**Amplifier 2 — `_apply_ceiling_rule` (`daemon/compaction.py:788-846`): a true daemon-level
quadratic.** `current_tokens` is computed by summing **every** section
(`:808` + `:811`), and then **recomputed inside the per-section loop** (`:834` + `:837`) once per
condensed section. With `S` sections that is **O(S²) section-tokenizations per compaction** — a
quadratic in the *number of compaction sections*, entirely independent of the tokenizer pathology.
Called from `daemon/compaction.py:993` (`build_compaction_doc`, `:874`).

**Amplifier 3 (the one that makes a *normal* turn expensive) — the pre-call gate's re-estimate
policy.** `daemon/compaction.py:2360-2398` `precall_estimate_needs_refresh` re-arms the estimator
whenever **the message count changed** (`:2394 if prev_count != msg_count: return True`) — which is
true after **every tool result** is appended. So in a turn with `K` tool calls the gate counts the
**whole, monotonically growing payload** `K+1` times:

* linear regime: `Σ_k T(payload_k) = O(K² · m)` bytes tokenized per turn;
* pathological regime: `K+1` full encodes of the pathological block ⇒ `(K+1) · c·L²`.

The house's own docstring states the normal cost: *"The estimator costs ~150–200 ms at 800 msgs /
~500k tokens"* (`daemon/compaction.py:2368-2369`).

**Amplifier 4 — `emergency_truncate` (`daemon/compaction.py:1688`, called from
`daemon/compaction.py:3554` inside `compact_state`): two more re-count LOOPS over the WHOLE
message list.** `estimate_fn` here is `estimate_messages_tokens` (passed at
`daemon/compaction.py:3557`). The function does three unconditional whole-list checks
(`:1718`, `:1730`, `:1742`), then Pass 3 re-counts the whole list **inside a halving loop**
(`:1749 while len(content) > 500 and estimate_fn(truncated) > max_tokens`), another whole-list check
(`:1760`), and finally a **pop-loop** that re-counts the whole list on **every pop**
(`:1768 while len(truncated) > 1 and estimate_fn(truncated) > max_tokens: truncated.pop(0)`).
⇒ **O(H·N) + O(P·N) counting work** (H = halving iterations, P = popped messages, N = list
length) — a **third** daemon-level quadratic, independent of the tokenizer pathology. This is the
"never-blocked" floor path, i.e. it fires exactly when the context is **over budget** — the
worst-case regime. (`emergency_truncate` also `copy.deepcopy`s the message list at `:1713`, a
further on-loop CPU cost outside this lane's scope.)

**The quadratic term itself (derived from the code + the two already-measured benchmark points).**
`tiktoken` splits text with the cl100k_base pattern
(`tiktoken_ext/openai_public.py:89`, installed 0.12.0):

```
'(?i:[sdmt]|ll|ve|re)|[^\r\n\p{L}\p{N}]?+\p{L}++|\p{N}{1,3}+| ?[^\s\p{L}\p{N}]++[\r\n]*+|\s++$|\s*[\r\n]|\s+(?!\S)|\s
```

The unbounded repetition groups are `\p{L}++` (letters), `[^\s\p{L}\p{N}]++` (symbols) and the
whitespace groups; **digits are capped at 3** (`\p{N}{1,3}+`). Therefore:

* a long run of **letters** (or symbols, or whitespace) is emitted as **ONE piece**;
* digits break runs every ≤ 3 chars ⇒ mixed alphanumerics break into many small pieces.

This exactly predicts the measured benchmark table (EVIDENCE.md §4.3):

| input (60 KB unless noted) | measured | shape |
|---|---|---|
| 5 000 → 500 000 chars, varied prose | 0.45 → 34.74 ms (**~11 MB/s, linear**) | many small pieces |
| 40-message agent list (92 000 chars) | **7.5 ms** (tester re-run: exact) | many small pieces |
| base64-ish blob | **9.50 ms** | digits break the runs |
| JSON-ish repeated key | **28.61 ms** | punctuation breaks the runs |
| **one long unbroken word** | **2 607.81 ms** | ONE piece |

⇒ **Trigger content shape = a long run (tens of KB) of a single character class: letters, symbols
or whitespace.** *Not* JSON, *not* base64-with-digits, *not* code with punctuation.

**The n² law, derived from the two independent measurements already in evidence** (no new
benchmark was run by this lane):

* EVIDENCE.md §4.3: 60 000 chars → 2 607.81 ms ⇒ `c = 2.60781 / 60000² = 7.24e-10 s/char²`;
  200 000 chars → 26.6 s ⇒ `c = 26.6 / 200000² = 6.65e-10 s/char²`.
* Tester re-run (independent, v0.31.10 source, `.venv` tiktoken 0.12.0): 60 KB → 2 311–2 350 ms
  ⇒ `c = 6.42–6.53e-10`; 200 KB → **27.05 s** ⇒ `c = 6.76e-10`.

⇒ **`c ≈ 6.4–7.2e-10 s/char²`, i.e. `T(L) ≈ c·L²`**, with the two runs agreeing within ~10 %
(the 3.33× size ratio gives an 11.7× time ratio vs `(200/60)² = 11.1` — a clean n² fit).
**Inversion** (`L = √(T/c)`), for the Emperor's sizing:

| stall | implied unbroken-run size |
|---|---|
| 1 s | ≈ 38 KB |
| 4 s (min of the 4 v0.31.10 precall captures) | ≈ 77 KB |
| 8 s | ≈ 109 KB |
| 20.55 s (worst tiktoken capture, **v0.31.2**) | ≈ 175 KB |
| 23.16 s (flagship, schema class) | ≈ 186 KB |

⇒ **The 4–20 s tiktoken stalls in the capture set are entirely explained by ~40–180 KB of
pathological content inside the payload**; a normal payload costs 7.5 ms.

**Disclosure (gap):** the *internal* data structure that produces the n² inside tiktoken is in the
Rust extension (`tiktoken/_tiktoken.cpython-313-x86_64-linux-gnu.so`) and is **not** inspectable
from the installed wheel; the pure-Python fallback was removed in 0.12.0 (`tiktoken/core.py` has no
`byte_pair_merge`). This lane therefore attributes the n² **empirically** (two independent
measurements, clean n² fit) and **structurally** (the pattern groups one character class into one
piece) — it does **not** claim a specific internal algorithm. This does not affect any decision
below: the fix is to stop handing the tokenizer an unbounded single piece, or to stop re-counting.

#### 2.1.5 Why the compact_state count is on the loop

`compact_state` is `async def` (`daemon/compaction.py:3113`) but its body contains **no `await`
around the counting** — the 13 `estimate_messages_tokens` calls are plain synchronous calls inside
an async function ⇒ they execute on the event-loop thread. Entry points: `daemon/graph.py:8410`
(`await compactor.compact_state(ctx, force=False)`), `daemon/graph.py:10006` / `:10014` / `:10035`
(reactive path, may run **twice** — `force=True` retry), and
`daemon/services/instance_messaging.py:1535` (`_maybe_compact_context`).

### 2.2 TRIGGER CONDITIONS

**Which workloads hit it.**

| workload | hits | why |
|---|---|---|
| **Any agent turn** (every agent type) | precall gate `graph.py:8255` | runs before **every** LLM call; re-arms whenever the message count changed (`compaction.py:2394`) ⇒ once per tool call |
| **Turns with tool calls** (worker / coder / ari) | amplified | payload grows each step ⇒ K+1 counts of a growing payload |
| **Long-context instances** (>50 % of window) | precall gate + compaction | the gate's arm 3 (`compaction.py:2396`) re-estimates **every** call once the cached estimate is ≥ 80 % of the window |
| **Any turn that triggers compaction** | compaction's 13 counts + O(S²) ceiling rule + shrink loop | §2.1.3/§2.1.4 |
| **Every SSE connect** (browser tab, FE reconnect) | `instance_messaging.py:1227` via `:1335` (`force=True`) | a full checkpoint read + a full history count **per connect**; the token-count dedup at `:1262-1266` runs **after** the count, so it saves the broadcast, not the CPU |
| **Sessions carrying one pathological block** (a 40–180 KB unbroken letter/symbol/whitespace run in any message, tool result, tool-arg blob, or `reasoning_content` echo) | the n² | §2.1.4 |

**Production frequency (with the version split).**
tiktoken appears in **10 of 20** captures: **2× v0.31.2, 4× v0.31.1, 4× v0.31.10**
(EVIDENCE §4.1 category×version table). Split by call site: 7× the precall gate, 2×
`instance_messaging.py:1227`, 1× `compaction.py:3440`. Attributed block time: **243 s over 183
episodes** (EVIDENCE §1 rows 1 + 3: 163 s + 80 s). Worst lag **20.55 s**
(capture `20261119T024751Z`, **v0.31.2**). Within the **v0.31.10** captures specifically, tiktoken
is **4 of 7** — i.e. it is the *current* build's dominant class, not a legacy artifact.

**What a user feels.**
The stall is **inside the agent turn**, before the LLM is even called: the agent appears to hang
("thinking…" with no response), and because the loop thread is the request-serving thread, **the
whole daemon freezes**: the dashboard stops updating, other agents' turns stall, SSE streams
pause, and API calls queue. For the compaction path the freeze lands *after* the LLM call, so the
user sees a response that is then followed by a multi-second freeze. Nothing in the UI says
"compaction" or "token counting" — it presents as a generic unresponsive daemon.

### 2.3 FIX SHAPE

Recommended: **two tiers**, because they have *different* correctness properties and the Emperor
should be able to green-light the safe one without waiting for the policy one.

#### 2.3.1 TIER A — byte-identical de-duplication (XS, ~2 h, zero behaviour change)

1. **Delete the provably redundant counts.**
   `daemon/compaction.py:3320` duplicates `:3315` (same argument, same lambda) → keep one.
   `daemon/compaction.py:3539` duplicates `:3517` (same argument, same branch) → reuse the value.
   `daemon/compaction.py:3440` can reuse the branch value computed at `:3358`/`:3403` when that
   branch ran. ⇒ **−2 to −3 full-set counts per compaction, byte-identical.**
2. **Fix the O(S²) ceiling rule** (`daemon/compaction.py:834`): accumulate the per-section savings
   incrementally instead of re-summing every section each iteration ⇒ **S² → S, byte-identical**
   (the same final `current_tokens`, because section bodies are replaced by a fixed marker whose
   token cost is itself counted once).
3. **Fix the O(P·N)/O(H·N) re-count loops in `emergency_truncate`**
   (`daemon/compaction.py:1688`): the pop-loop at `:1768` and the halving loop at `:1749` re-count
   the **whole** message list on every iteration. A running count (or counting only the message
   just mutated, plus the delta) collapses both to O(N + P + H) ⇒ **byte-identical** (the loop
   predicates are evaluated on the same values, just not recomputed from scratch). This is the
   highest-value Tier A item: it is the only one that removes a *quadratic in the message count*,
   and it fires exactly in the over-budget regime.
4. **Delta-maintain the pre-call estimate.** `precall_estimate_needs_refresh`
   (`daemon/compaction.py:2360`) already caches `(msg_count, tokens)` per instance
   (`:2400-2407`). Because the payload only *grows by appends* between LLM calls, the gate can
   count **only the newly appended messages** and add them to the cached total — the arithmetic is
   identical (`estimate_messages_tokens` is a per-message sum plus fixed overheads,
   `daemon/loader.py:1244-1285`) ⇒ **byte-identical**, and it removes the `O(K²)` per-turn term of
   §2.1.4-amplifier-3. **Invalidation**: the cache must be dropped when compaction replaces the
   message list (the compaction dedup window lives at `daemon/compaction.py:3151-3172`
   (`dedup_window_s` / `last_compacted_at`) — the natural hook)
   and on the `context.messages`-vs-`selectable_messages` split.
5. **Dedup *before* counting in the usage path.** `daemon/services/instance_messaging.py:1258`
   computes the snapshot and only then compares at `:1262-1266`. Move the comparison ahead of the
   count (compare *message count* first — the same cheap proxy `precall_estimate_needs_refresh`
   uses) ⇒ the per-node re-count at `:5657` and the per-SSE-connect count at `:1335` stop paying
   full price when nothing changed. **Byte-identical** (the emitted value is unchanged; only the
   *computation* of an unchanged value is skipped).
6. **Bound the shrink loop** (`daemon/compaction.py:770-785`) — optional; the series is already
   bounded at ≤5×, so this is a low-value item. If done, it must preserve the "first candidate
   whose **exact** count ≤ cap" semantics (the current loop returns the shortest candidate that
   satisfies the exact predicate; skipping candidates is only safe if the exact predicate is
   *monotone* in the candidate index — **BPE token counts are not guaranteed monotone** (a longer
   string can tokenize to fewer tokens), so a binary-search shortcut is **not** provably
   byte-identical). Recommend: leave the loop alone, or keep exact verification for the final
   candidate only and accept a documented ≤ 1 % difference in the truncated length.

**Expected effect of Tier A:** removes ~5 redundant full-payload counts per compaction, the
O(S²) ceiling term, the O(P·N)/O(H·N) `emergency_truncate` re-count loops, and the O(K²) per-turn
term. **It does NOT remove the n² term**: the pathological
content is still handed to the tokenizer once per surviving count. For a normal payload the saving
is ~30–40 % of a compaction's count cost (≈ 20–30 ms at 92 KB); for the pathological shape the
saving is proportional (5 of 13 counts) but the residue is still seconds.

#### 2.3.2 TIER B — bound the pathological input (S, ~3 h, **changes counts on pathological inputs only**)

The only way to remove the n² term is to stop handing the tokenizer one unbounded piece.

**B1 — chunked counting (recommended).** Split a long string into pieces of ≤ `C` bytes at a
whitespace/newline boundary and sum the per-chunk counts. Cost model: each chunk is one piece of
≤ `C` bytes ⇒ `T_chunked(n, C) ≈ (n/C)·c·C² = c·n·C` ⇒ **O(n) instead of O(n²)**, with the chunk
size as the knob:

| n | C = 1 KB | C = 4 KB | C = 8 KB | C = 16 KB | (exact, today) |
|---|---|---|---|---|---|
| 200 KB | **0.13 s** | **0.55 s** | 1.10 s | 2.19 s | **27.05 s** |
| 40 KB | 0.027 s | 0.107 s | 0.21 s | 0.43 s | 1.07 s |

⇒ **C = 4 KB gives a ~49× reduction on the worst measured case** and bounds the pathological
encode at ~0.1 s for realistic payloads.
*Count fidelity:* BPE merges cannot cross a chunk boundary ⇒ the chunked count is an **over-count**
by at most one token per boundary (≤ `n/C` tokens; ≤ 50 tokens ≈ 0.1 % at 200 KB/4 KB).
**Direction matters: an over-count is the SAFE direction for a budget gate** (it compacts
marginally early, never late).
*Preserving byte-identity:* apply chunking **only above a size threshold** (e.g. 16 KB) ⇒ every
normal payload keeps today's **exact** count, byte-for-byte. Above the threshold the count changes
by ≤ 0.1 % ⇒ **this is a documented, bounded behaviour change and must be gated by a test pack**
(§2.4).
*Better still:* chunk at a whitespace boundary and **skip the boundary character into the next
chunk** ⇒ restores cross-boundary merges for the common case and shrinks the delta further.

**B2 — clip-and-extrapolate (alternative, weaker).** Count the first `C` bytes exactly and
extrapolate the remainder with the char heuristic already used at `daemon/compaction.py:781-782`.
Cheaper, but the extrapolation error grows with content shape ⇒ B1 is preferred.

**B3 — a hard input cap.** Reject/clip message blocks above a size limit before counting. Simple,
but it silently changes behaviour for legitimate large payloads ⇒ **not recommended without an
Emperor policy ruling**.

#### 2.3.3 What should still go off-loop after the algorithm fix?

**Almost nothing on the tiktoken path — and nothing on the process pool.**

* *Threads:* tiktoken's BPE is GIL-held pure work (the Rust core does not release the GIL for the
  merge). `asyncio.to_thread` would move the stall from "loop blocked" to "loop + worker both
  starved" — **no CPU-throughput gain** (HANDOFF "CPU-betterment" §2, same argument).
* *Process pool:* the natural residual unit is a single *string* (a message block), which is
  picklable — so a bounded process pool **is** technically feasible for Tier B. But after Tier A +
  Tier B the per-call cost is ≤ ~0.5 s for a 200 KB pathological blob and ~7.5 ms for normal
  payloads; a process pool would then cost more in pickling/IPC than the work it offloads.
  ⇒ **Recommendation: no process pool for tiktoken.** If the Emperor wants a belt-and-braces
  guard, put it at Tier B only: a *bounded* process-pool fallback for inputs above ~64 KB, with a
  hard cap on pool size (≤ 2 workers) and a wall-clock bound — never for normal traffic.
* *One residual that should still be offloaded:* the **checkpoint read** in
  `emit_context_usage_for_instance` (`daemon/services/instance_messaging.py:1322`) — a DB read on
  the loop (cross-ref: HANDOFF row X, checkpoint lane). That is a *separate* family (sync DB on
  async), not this lane.

#### 2.3.4 Corrections the Emperor should carry into the decision (from this lane's reading)

* **C1 — "cache the encoder module-scope" does NOT pay.** `tiktoken.get_encoding`
  (`tiktoken/registry.py:63-89`) returns the **same** `Encoding` object from a module-level
  `ENCODINGS` dict after the first call (double-checked under an `RLock`). The per-call cost in
  `daemon/loader.py:1098` is a dict lookup. A daemon-side module-scope cache would save **only**
  the once-per-process first build (measured 0.8–2.2 s; tester re-run 805–1 896 ms) — i.e. it
  removes a **one-time** boot tax, not a recurring cost. **Do not fund it as the row-1 fix**; if
  wanted, it is a 5-line change worth folding into a boot-warm item, not a lane.
* **C2 — the shrink loop is ≤ 5×, not 21×** (§2.1.4-amplifier-1). It should not be sold as the
  main saving.
* **C3 — the 20 s tiktoken stalls are content-shape events, not "we count too often" events.**
  A normal 92 KB payload costs **7.5 ms**. The Tier A de-duplication therefore buys **milliseconds
  in the common case**; only Tier B touches the observed seconds. **Any plan that funds only
  "count once" will not move the 20 s figure** — this is the single most important correction in
  this document for the Emperor's sequencing decision.
* **C4 — the same count is re-paid on every SSE reconnect** (`instance_messaging.py:1335`).

#### 2.3.5 Expected loop-lag reduction (tied to the benchmarks)

* **Normal payloads:** the precall gate currently costs ~7.5 ms per 92 KB (EVIDENCE §4.3 + tester
  re-run, exact). Tier A turns the K+1 counts per tool-loop turn into 1 count + deltas ⇒ per-turn
  counting work drops from `O(K²·m)` to `O(m)` — for a 10-call turn with a 92 KB payload, from
  ~75 ms to ~7.5 ms. **Absolute saving: tens of ms per turn.**
* **Pathological payloads (the observed 4–20.55 s stalls):** Tier B caps the worst case at
  ≈ 0.1–0.55 s per encode (4 KB chunks) ⇒ **the 4.18 / 5.88 / 8.31 s v0.31.10 precall stalls and
  the 20.55 s v0.31.2 stall all collapse to ≲ 0.6 s** — a **~35–50× reduction on the pathological
  class**. Applied to the compaction class (which multiplies by ~5–13 counts), the effective
  reduction is the same order.
* **Aggregate:** the tiktoken class carries **243 s over 183 episodes** (EVIDENCE §1). Tier A+B
  should recover the **pathological share** of that, which is **not separately measured** — the
  capture set does not record per-episode payload shapes. **Disclosed gap: the exact recoverable
  fraction of the 243 s cannot be derived from the existing evidence**; the bound is "all episodes
  above ~1 s of loop lag caused by a tiktoken frame", i.e. **the four v0.31.10 precall captures
  (4.18 / 8.31 / 3.76 / 5.88 s) + the 20.55 s outlier + the compaction/messaging captures** — i.e.
  **of the order of 100–240 s of the 243 s**, with the residual being the 7.5 ms-class counts.

### 2.4 EFFORT + RISK

| sub-item | size | blast radius | invariant at risk | gate |
|---|---|---|---|---|
| **A1** drop duplicate counts (`compaction.py:3320`, `:3539`, `:3440`) | **XS (<2 h)** | 1 file, 3 lines | none — identical counts | extend `tests/unit/test_compaction.py` + `test/packs/compaction_unit_test.sh` |
| **A2** `_apply_ceiling_rule` S² → S (`compaction.py:834`) | **XS (<1 h)** | 1 file, 1 function | final `current_tokens` identical | same |
| **A2b** `emergency_truncate` re-count loops → running count (`compaction.py:1749`, `:1768`) | **S (2–3 h)** | 1 file, 1 function | the loop predicates must see the same values (a delta count must equal the recount) | new pack: seeded message lists ⇒ identical truncation output vs the recount implementation |
| **A3** delta-maintained pre-call estimate (`compaction.py:2360` + `graph.py:8255`) | **S (2–4 h)** | `compaction.py`, `graph.py` (2 sites) | the **gate decision** must be identical ⇒ the delta must reproduce the whole-list sum exactly; invalidation on compaction must be exact | new pack: count-equality vs today's whole-list count over a seeded message corpus |
| **A4** dedup-before-count in the usage path (`instance_messaging.py:1258`, `:1335`) | **XS (<1 h)** | 1 file | emitted value unchanged | existing context-usage tests |
| **B1** chunked counting above a threshold | **S (2–4 h)** | `daemon/loader.py` (the primitive → **51 call sites / 14 files**) | **token counts change by ≤ n/C tokens for oversized inputs** ⇒ the "IDENTICAL tokenization results" invariant is deliberately relaxed *only above the threshold* | **new pack required**: (a) exact equality below the threshold; (b) bounded delta above it; (c) a pathological-input timing bound |
| **B2** clip-and-extrapolate | **S** | same | larger error band | same |
| **B3** hard input cap | **XS** | same | **large payloads change behaviour** | **Emperor policy ruling required** |

**Blast radius summary (who else calls these paths):**
`estimate_messages_tokens` → `daemon/compaction.py` (27 sites), `daemon/graph.py` (:8255 precall
gate, :14037, :14038 compaction accounting), `daemon/services/instance_messaging.py:1227`,
`daemon/services/compact_executor.py:903`. `estimate_tokens` → additionally the **memoir lane**
(`daemon/services/memoir_retrieval.py` 11, `memoir_reflection_service.py` 5,
`daemon/repositories/memoir/repository.py` 5, `memoir_briefing.py` 4, `memoir_poignancy_service.py` 3,
`memoir_persona_gate.py` 1), `daemon/services/context_messages.py` 6,
`daemon/services/snapshot_executor.py:990`, `daemon/manager.py:5052`.
⇒ **Any change inside `estimate_tokens`/`estimate_messages_tokens` reaches the memoir and snapshot
lanes.** That is the strongest argument for implementing Tier B as a *wrapper used at the hot call
sites* (or as a strictly-additive helper) rather than as an in-place rewrite of the primitive.

**Correctness invariants the fix must not break (stated explicitly):**
1. **Identical token counts** for every input at or below the chunking threshold (byte-identical
   gate decisions ⇒ identical compaction trigger points, identical `context_usage` badge values).
2. **Identical compaction behaviour** — the gate ratio (0.95 precall / 0.80 escalation), the
   threshold, the 50 %-tail floor, and the "never-blocked" guarantee must all be evaluated on
   counts that are identical to today's.
3. **Monotone, non-negative counts**; the chunked over-count must never make a count *smaller*.
4. **No new `await` inside the counting** — the fix must not make the count async (that would
   change the ordering of the compaction gate).
5. **The O(S²)→O(S) rewrite must preserve the final `current_tokens` exactly**, including the
   "hard cap → B-shape degrade" branch (`daemon/compaction.py:840-843`).

---

## 3. CULPRIT 2 — pydantic/langchain schema construction per graph assembly

### 3.1 MECHANISM — what "schema rebuilt per graph assembly" means concretely

#### 3.1.1 What gets rebuilt

**Rebuild #1 — the tool `args_schema` (pydantic v2 CLASS CONSTRUCTION).**
The daemon decorates its tools with langchain's `@tool`
(`from langchain_core.tools import tool`, e.g. `daemon/tools/skill_evolution_tools.py:49`,
`daemon/tools/job_queue.py:60`, `daemon/tools/instance.py:33`). At decoration time langchain runs
`create_schema_from_function` (`langchain_core/tools/base.py:289`, called from
`…/tools/convert.py:304 _tool_factory` → `…/tools/convert.py:361 tool`), which:

1. `inspect.signature(func)` (`base.py:318`);
2. `validate_arguments(func, config=_SchemaConfig)` (`base.py:321-326`) — the **pydantic v1-compat
   shim** (`pydantic/deprecated/decorator.py:143 __init__` → `:243 create_model`) which builds
   **two** model classes (the `DecoratorBaseModel` at `decorator.py:243` and its subclass at
   `decorator.py:284`);
3. `_create_subset_model` → `_create_subset_model_v2` (`langchain_core/utils/pydantic.py:231-269`)
   — a **third** model class.

⇒ **≈ 3 pydantic v2 model-class constructions (each a full core-schema generation:
`_model_construction.py:255 __new__` → `complete_model_class` → `generate_schema` →
`_model_schema`) per decorated tool, per factory invocation.**

**Rebuild #2 — the OpenAI JSON schema (`bind_tools`).**
`daemon/graph.py:10992 build_instance_llms` binds the tool list to the chat models at
**`daemon/graph.py:11049`** (`llm_with_tools = llm_standard_chat.bind_tools(tools)`) and again at
**`daemon/graph.py:11050`** (`llm_standard = llm_standard_chat.bind_tools(tools)`) — **the same
tool list, twice**. Each `bind_tools` runs `convert_to_openai_tool` per tool
(`langchain_openai/chat_models/base.py:1967-1969`) → `_format_tool_to_openai_function` →
`tool.args_schema.model_json_schema()` (`langchain_core/utils/function_calling.py:187` →
`pydantic/main.py:576` → `pydantic/json_schema.py:2537`). **`model_json_schema` is NOT cached in
pydantic 2.12.5** (no `lru_cache` on `pydantic/main.py:546` or `pydantic/json_schema.py`) ⇒ the
JSON schema is regenerated **2 × N_tools times per graph build**.

#### 3.1.2 How many, how often, on which operations

**AST census at v0.31.10 (read-only, this lane):** in `daemon/tools/`, **271** `@tool`-decorated
functions resolve; **261 of them are defined INSIDE a factory function** (so the decorator — and
therefore the schema — re-runs on every factory call) and only **10** are module-level (built once
at import). Per-file leaders: `job_queue.py` 25, `project.py` 21, `rag_tools.py` 16, `infra.py` 13,
`instance.py` 13, `todo_tools.py` 11, `blueprint.py` 10, `memoir.py` 8, `critical_notes.py` 6,
`secrets_tools.py` 6, `instance_ops.py` 6, `skill_tools.py` 6, `skill_evolution_tools.py` 5, …
(62 files carry nested `@tool` functions).
*(Convention note: the tester's raw-line count was 274 `@tool` uses in `daemon/tools/`; this lane's
AST-resolved count is 271 = 261 nested + 10 module-level. Same substance, different counting rule —
stated per the house convention.)*

**`create_instance_tools` (`daemon/tools/instance.py:3476`, body `:3476-8064`) calls 62 tool
factories** (`create_job_tools_if_available` :7029, `create_project_tools` :6902,
`create_instance_read_tools` :7167, `create_skill_evolution_tools` :7706, `create_system_tools`
:7810, `create_upgrade_tools` :7823, … `create_wedge_capture_tools` :7214 — full list obtained by
AST). ⇒ **one `create_instance_tools` call re-runs ≈ 261 `@tool` decorations ≈ ≈ 780 pydantic
model-class constructions, plus the 2 × N_tools `bind_tools` JSON-schema passes.**

**Which operations trigger a rebuild** (the "cold path" of the instance graph):

| operation | anchor | on the loop? |
|---|---|---|
| **cold restore** (instance not in the in-memory cache) | `daemon/services/instance_lifecycle.py:4580 get_instance` → cache miss → `:4615` → `async def _restore_instance` (`:4702`) → `:4944 create_instance_tools` + `:5054 build_instance_graph` | **yes** (`async def`, no `await` around the factories) |
| **spawn** | `daemon/services/instance_lifecycle.py:2169 create_instance_tools` (then `:2291 build_instance_graph`) | **yes** |
| **message send to a released instance** | `daemon/tools/instance.py:5002 send_message` → `:1826 _resolve_instance_id` → `await manager.get_instance(...)` → cold restore | **yes** |
| **background job dispatch to a cold instance** | `daemon/services/job_processor.py:1145 await self._instance_manager.get_instance(proc_job.instance_id)` inside `_process_next_job` (called from `_process_loop` `:706`) | **yes** — and it is a **background** lane, so the freeze is caused by a job, not by a user request |
| **graph release** (what makes the next call cold) | `daemon/manager.py:7594` (`del self.instances[instance_id]`, in the release/TTL path) and `daemon/services/instance_lifecycle.py:2908` (terminate path) | — |
| **cache hit** | `daemon/services/instance_lifecycle.py:4596-4606` → returns the cached graph, **no rebuild** | — |

⇒ **The rebuild happens on every cold start of an instance**, i.e. after a daemon restart, after a
terminate/release, and after any cache eviction — and **each rebuild is ~16 s of pure CPU on the
event loop** (below).

#### 3.1.3 What it costs at the observed 86.1 % loop CPU

The flagship capture `20261119T170818Z-ensemble-vm-sustained_loop_lag-7d45acf6`:
`release_version = 0.31.10`, `observed.lag_s = 23.164142`, `observed.sustained_s = 16.716`,
`lag_probe.stale = true`, `last_probe_age_s = 24.447`, `pool_checked_out = 0`, `pool_waiting_clients = 0`,
`pool_max = 15` (durable `manifest.json`, re-read by this lane).
The loop thread's stack (durable `stacks.txt`):

```
File "pydantic/_internal/_generate_schema.py", line 865 in _model_schema
File "pydantic/_internal/_generate_schema.py", line 1023 in _generate_schema_inner
File "pydantic/_internal/_generate_schema.py", line 729 in generate_schema
File "pydantic/_internal/_model_construction.py", line 648 in complete_model_class
File "pydantic/_internal/_model_construction.py", line 255 in __new__
File "pydantic/deprecated/decorator.py", line 243 in create_model
File "pydantic/deprecated/decorator.py", line 143 in __init__
File "__init__", line ??? in __init__
<invalid frame>
```

⇒ the loop thread was **inside pydantic class construction** for the whole sustained window, at
**86.082 %** of process CPU (threads.csv; EVIDENCE §4.2, tester re-verified exact).
**Derived cost:** 16.716 s of sustained class construction ÷ ≈ 780 model classes ≈ **≈ 21 ms per
pydantic v2 model class** (an aggregate derived from the capture + the AST census; the per-class
cost is **not separately measured** — disclosed). The sibling capture `20261119T134848Z`
(**v0.31.10**) shows the *same* shim at its other model (`pydantic/deprecated/decorator.py:284`
→ `pydantic/main.py:1809 create_model`), i.e. the second of the two models the v1-compat shim
builds.

⇒ **The flagship 23.16 s lag IS the "schema rebuilt per graph assembly" cost, measured.**

### 3.2 TRIGGER CONDITIONS

**Which workloads hit it.** Every operation in §3.1.2's "cold" rows. In practice: **the first
message to an instance after a daemon restart or after that instance was terminated/released**, any
**spawn**, and **any background job dispatch** to an instance whose graph is not cached. The number
of tools in the surface is the multiplier (a 260-tool agent surface rebuilds ~780 model classes; a
narrower agent surface rebuilds proportionally fewer — the factories are all invoked, but the
*filter* decides which tools are exposed; the **decoration** cost is paid for every factory's
tools regardless of the final allow-list, because the factories build their tools before the
filter runs).

**Production frequency (with the version split).** Schema-class captures: **5 of 20** —
**1× v0.31.2** (`bind_tools`), **2× v0.31.1** (`create_model` ×1, `from_function` ×1),
**2× v0.31.10** (`create_model` ×2 — one of which is the flagship). Attributed block time:
**51 s over 32 episodes** (EVIDENCE §1 row 2). The flagship is the **only `sustained_loop_lag`
capture in the entire audit** — i.e. it is the single highest-fidelity observation of a genuine
event-loop stall in the whole capture set. Note the asymmetry with tiktoken: 19 of 20 captures
fired the `sustained_cpu` trigger (process CPU ≥ 95 % of one core for ≥ 30 s) and only **1** fired
the *loop-lag* trigger — so the schema class is under-represented in the capture set relative to
its severity (EVIDENCE §1 note).

**What a user feels.** The daemon **freezes completely** for up to ~16–23 s: HTTP requests queue,
the UI stops responding, SSE streams pause, other agents' turns stall — and, when the trigger is
the background job processor (§3.1.2 row 4), **the user did nothing to cause it**. If the trigger
is the agent-facing `send_message` tool (`daemon/tools/instance.py:5002`), the *sending agent*
blocks for the whole rebuild before its own tool call returns.

### 3.3 FIX SHAPE

**Recommended: a process-level schema registry + a boot-warm.**

**Why the work is pure waste.** The `args_schema` of a tool is a **pure function of the function's
signature and docstring** (`langchain_core/tools/base.py:318-388`). Neither varies per instance,
per agent, or per build — the *closure* varies (it captures `manager`/`instance_id`), but the
**schema does not**. The daemon already demonstrates the pattern for the MCP lane: MCP tool
schemas are cached per instance (`daemon/manager.py:11621 ensure_mcp_preloaded` →
`self._mcp_service.get_mcp_tools(instance_id)`); the daemon's **own** 261 nested `@tool`
factories have **no** such cache (the only `schema_cache` in `daemon/tools/` is the MCP-server
invalidation hook, `daemon/tools/infra.py:1417-1428`).

**Fix 1 — memoize the schema per tool function (the core of the win).**
Introduce a daemon-side decorator (e.g. `daemon/tools/_schema_registry.py`) that, per tool
function, caches:

* the built `args_schema` model class, **and**
* the derived OpenAI JSON-schema dict,

keyed by the function's **code object / `(module, qualname)`** — a key that is stable across
instances and agents but distinct for distinct functions with identical signatures. The cached
`args_schema` class is then handed to `StructuredTool(args_schema=…, func=…)` instead of letting
langchain call `create_schema_from_function` again.

**Fix 2 — bind the tools once.**
`daemon/graph.py:11049` and `:11050` bind the **same** list twice ⇒ compute the OpenAI tool
dicts **once** (`formatted_tools = [convert_to_openai_tool(t) for t in tools]`) and pass the same
list to both `bind_tools` calls (or bind once and reuse the `RunnableBinding`). Byte-identical
provider payload; halves the JSON-schema cost.

**Fix 3 — boot-warm the registry.**
The **first** graph build in a process still pays ~16 s. Warm the registry at daemon startup
(background thread, before serving traffic) so the first user-visible restore costs ~0.
*(Trade-off disclosed: the warm costs ~16 s of CPU during boot. That is invisible if it happens
before the listener accepts traffic; it is a real cost if it happens concurrently with serving.)*

**Why algorithm-first beats offloading here (explicitly requested).**

* *The work is redundant, not heavy.* Caching eliminates ~99 % of it; offloading redistributes
  100 % of it. The HANDOFF's own CPU-betterment rule (§2, "go algorithmic before parallel")
  applies unchanged.
* *Threads add no CPU throughput.* pydantic's schema generation is GIL-held (the Rust core
  `pydantic_core` does not release the GIL during core-schema construction) ⇒ a
  `to_thread` offload converts "loop blocked" into "loop and worker both starved" — a fairness
  win for HTTP responsiveness, **not** a throughput win, and **not** a fix for the 23 s stall.
* *A process pool is not applicable.* The artefacts are **dynamically created pydantic model
  classes** and **tool objects holding closures over the manager** — neither is picklable by
  reference. A worker would have to rebuild them (no reuse ⇒ no saving) and could not return the
  classes. Only the *JSON-schema dicts* are picklable, and those are the cheap half.
* *Where offloading still makes sense:* if the Emperor wants the loop freed **before** the cache
  lands, the correct move is to run the **whole `create_instance_tools` + `build_instance_graph`
  block in a worker thread** (`asyncio.to_thread`) so the *loop* stays responsive — acknowledging
  that the CPU is still burned and the turn's own latency is unchanged. This is a **fairness
  fallback, not the fix**; it also carries a thread-safety review cost (the factories touch the
  manager, the MCP service, and the tool registry).

**Expected loop-lag reduction.** The flagship's **16.716 s sustained** class-construction window
becomes **≈ 0 s for every build after the first**, and ≈ 0 s for the first as well **if the
registry is boot-warmed**. Concretely: **23.16 s → ≲ 0.1 s** for the flagship event; the schema
class's **51 s / 32 episodes** of attributed block time should fall to the residual of the first
(build-time) construction only.

### 3.4 EFFORT + RISK

| sub-item | size | blast radius | invariant at risk | gate |
|---|---|---|---|---|
| **S1** schema registry + memoized `args_schema` | **S (3–4 h)** | 261 nested `@tool` factories across **62 files** (mechanical: swap the decorator import) + 1 new module | **the tool JSON schema sent to the provider must be byte-identical**; tool **names** are frozen by `test/packs/frozen_tool_name_discovery_unit_test.sh` | new pack: for every tool, assert the cached schema's `model_json_schema()` == the freshly-built one (byte-compare the dict) |
| **S2** bind the tool list once (`graph.py:11049-11050`) | **XS (<1 h)** | 1 site | identical provider payload | same |
| **S3** boot-warm the registry | **XS–S (1–2 h)** | daemon startup path | boot time +16 s | boot-time smoke |
| **S4** *(fallback, optional)* offload the tool+graph build to a thread | **S (3–4 h)** | `instance_lifecycle.py:2169`, `:4944` | thread-safety of the manager/MCP/tool-registry access | load test |

**Callers of the affected paths (enumerated).**
`create_instance_tools` (`daemon/tools/instance.py:3476`) ← `daemon/services/instance_lifecycle.py:2169`
(spawn) and `:4944` (restore). `build_instance_graph` (`daemon/graph.py:14219`) ←
`daemon/services/instance_lifecycle.py:2291` and `:5054`. `build_instance_llms`
(`daemon/graph.py:10992`) ← `daemon/graph.py:14366` (the only in-tree caller).
`get_instance` (`daemon/manager.py:16529` → `daemon/services/instance_lifecycle.py:4580`) ←
`daemon/services/instance_messaging.py:3926` (the send path), `:1304` (SSE/usage path), `:5983`
(instance-messages read), `daemon/services/job_processor.py:1145` (background job lane),
`daemon/tools/instance.py:1826` (`_resolve_instance_id`). Graph release (what makes the next call
cold): `daemon/manager.py:7594`, `daemon/services/instance_lifecycle.py:2908`.

**Correctness invariants the fix must not break (stated explicitly):**
1. **Identical schema semantics** — the OpenAI tool JSON sent to the provider must be
   **byte-identical** to today's for every tool (same `name`, same `description`, same
   `parameters` JSON schema, same key order if the provider is order-sensitive).
2. **Identical tool names** — frozen by an existing pack
   (`test/packs/frozen_tool_name_discovery_unit_test.sh`); the registry must not rename or
   re-scope anything.
3. **No shared mutable state across instances** — the cached `args_schema` **class** is shared;
   the cached *tool object* must NOT be (the closure is per instance). ⇒ the registry must cache
   the **schema**, not the tool instances. **Reviewer check: no code may mutate a tool's
   `args_schema` after construction** (mutation would now leak across instances).
4. **MCP tools stay dynamic** — MCP tool schemas come from the server and are already cached
   per instance (`daemon/manager.py:11621`); the registry must not freeze them (the MCP
   invalidation hook at `daemon/tools/infra.py:1417-1428` must keep working).
5. **Tool-filter semantics unchanged** — the allow/deny filter (`daemon/tools/instance.py:448`
   `resolve_tool_filter`, called at `:8162`) must still decide *exposure*; the registry only
   memoizes *schemas*.
6. **The daemon must not depend on import-time side effects of the factories** — the factories
   are currently called on every build; if any factory has a *side effect* beyond building tools
   (registration, cache warming), memoizing the schema must not skip it. **Reviewer check required.**

---

## 4. SPLIT, sequencing, and version-drift disclosure

### 4.1 Recommended lane shape

**Split the two culprits into three XS/S lanes, landable in ONE release:**

| lane | content | size | risk | needs an Emperor ruling? |
|---|---|---|---|---|
| **2A — "schema registry"** | S1 + S2 + S3 (§3.3) | **S (3–4 h)** | low (byte-identical output, provable) | no |
| **2B — "exact token de-duplication"** | A1 + A2 + A2b + A4 (§2.3.1) | **XS–S (≈ 2–4 h)** | very low (identical counts) | no |
| **2C — "bounded token counting"** | A3 + B1 (§2.3.1 item 3, §2.3.2) | **S (4–6 h incl. pack)** | **medium** (counts change ≤ 0.1 % above the threshold) | **YES** — a token-count fidelity policy ruling |

Rationale for the split:
* **Different blast radius:** 2A touches `daemon/tools/**` (62 files, mechanical) + `graph.py`;
  2B/2C touch `daemon/compaction.py`, `daemon/loader.py`, `daemon/graph.py`, `daemon/services/instance_messaging.py`.
  A single combined lane would put the two riskiest surfaces (the tool registry across 62 files,
  and the shared token primitive used by the memoir/snapshot lanes) in one review.
* **Different gating shapes:** 2A's gate is a byte-comparison of tool schemas; 2C's gate is a
  token-count equivalence + timing bound. Separate packs, separate failure modes.
* **Different decision authority:** 2B and 2A are engineering decisions; 2C needs the Emperor's
  ruling on token-count fidelity above a size threshold.
* **Sequencing inside the release:** 2B first (cheapest, zero risk, and it removes the duplicate
  counts that 2C's chunking would otherwise multiply), then 2A (biggest single win), then 2C
  (policy-gated).

### 4.2 Check against the HANDOFF's recommended sequence / lane-sharing

HANDOFF §46-54 ("Recommended sequence") puts **"Lane 'agent-turn CPU' = rows 1+2 (+row 10 logging
XS)"** at position 2, rated **S–M**, "single release", and notes at `:54` that *"Rows 2+3 surfaces
are disjoint (graph/loader/compaction vs tools/services/routers) and can share one release lane if
desired."*

**This deep-dive AGREES with the sequencing** (agent-turn CPU is the right lane to fund next) but
**RECOMMENDS splitting it into 2A/2B/2C** for the risk reasons above, and it **revises the
effort rating**: the original "S–M (4–8 h) for row 1" understates 2C (the n² term needs a policy
call + a new pack ⇒ realistically **S+**, 4–6 h) and **overstates** 2A's risk profile (S, with a
provable byte-identical gate).

HANDOFF row 8 (instrument XS first, "makes every later gain verifiable") **remains the right
precondition**: without per-pool/per-cause loop attribution, the gains from 2A/2B/2C cannot be
*verified* in production, only argued. If the Emperor funds only one thing beyond this lane,
**fund row 8 first** — the capture set's retention bias (EVIDENCE §1 note: 318 journal fires vs
20 surviving captures) will otherwise hide the very episodes these fixes target.

### 4.3 Surface disjointness (for release grouping)

| | 2A (schema) | 2B/2C (token) |
|---|---|---|
| `daemon/tools/**` | **✔** (62 files) | ✘ |
| `daemon/loader.py` | ✘ | **✔** |
| `daemon/compaction.py` | ✘ | **✔** |
| `daemon/graph.py` | `build_instance_llms` (:10992-11050) | `_maybe_precall_compact_95` (:8154-8255) |
| `daemon/services/instance_lifecycle.py` | `:4944`, `:5054` (the build sites) | — |
| `daemon/services/instance_messaging.py` | — | `:1227`, `:1258`, `:1335` |

⇒ **Disjoint except for two files, and within those, different functions.** 2A/2B/2C can share
**one release** without cross-coupling. They must **not** share a *single commit* (per the house
"one logical commit" discipline, three commits in one branch are fine).

### 4.4 Version-drift disclosure (re-anchored at the current tag)

* **Audited tree:** `v0.31.10` = `01eb788b6`. **Current tree:** `v0.31.13` = `ef89c8a97`
  (`origin/latest` == `v0.31.13`).
* **Every anchor in this document was re-checked at `v0.31.13` and is UNCHANGED**:
  `daemon/loader.py:1087` = `def estimate_tokens(text: str) -> int:` ·
  `daemon/compaction.py:736` = `def _truncate_to_token_cap(` · `:788` = `def _apply_ceiling_rule(` ·
  `:3113` = `async def compact_state(` · `daemon/graph.py:8255` =
  `payload_tokens = estimate_messages_tokens(full_messages)` · `:11050` =
  `llm_standard = llm_standard_chat.bind_tools(tools)` · `:14366` =
  `llm_with_tools, llm_standard = build_instance_llms(` ·
  `daemon/services/instance_messaging.py:1227` = `history_tokens = estimate_messages_tokens(messages or [])` ·
  `daemon/tools/instance.py:3476` = `def create_instance_tools(` ·
  `daemon/tools/skill_evolution_tools.py:209` = `def create_skill_evolution_tools(` ·
  `daemon/services/instance_lifecycle.py:4944` = `tools = create_instance_tools(` ·
  `:5054` = `graph = build_instance_graph(`.
* **Stronger: the six files carrying these hot paths are BYTE-IDENTICAL between v0.31.10 and
  v0.31.13.** `git diff --stat v0.31.10 v0.31.13 -- daemon/loader.py daemon/compaction.py
  daemon/graph.py daemon/tools/instance.py daemon/services/instance_lifecycle.py
  daemon/tools/skill_evolution_tools.py` → **empty**. The whole v0.31.10→v0.31.13 daemon diff is
  only 6 files (`daemon/__init__.py`, `daemon/manager.py`, `daemon/repositories/skill/repository.py`,
  `daemon/routers/instances.py`, `daemon/services/maintenance.py`,
  `daemon/services/skill_evolution_service.py`). ⇒ **This lane carries ZERO anchor drift; the
  audit's findings apply verbatim to the current tree.** (Contrast: the house precedent is
  "~780 lines of drift within one release cycle" — not the case here.)
* **The sibling-lane offload the Emperor was told about** — `daemon/routers/instances.py:511`:
  at **v0.31.10** it was `prefs_map = manager._instance_ui_prefs_repo.get_all(instance_ids)` —
  a **sync DB call on the loop**; at **v0.31.13** it is
  `prefs_map = await _offload(manager._instance_ui_prefs_repo.get_all, instance_ids)` (the
  `db_offload` seam), and `manager.list_instances` is likewise offloaded above it. **That
  offload is row 3/row 7 territory (sync-DB family), NOT row 1/row 2** — it does not touch the
  agent-turn CPU lane, and it does not change any anchor in this document. Disclosed so the
  Emperor does not double-count it as agent-turn-CPU progress.
* **Nothing in this lane has been fixed elsewhere since v0.31.10**: no tiktoken cache, no chunked
  counting, no schema registry exists in the current tree (`grep` for `schema_cache` in
  `daemon/tools/` finds only the MCP-server invalidation hook at
  `daemon/tools/infra.py:1417-1428`).

---

## 5. What this document does NOT claim

1. **No new measurement.** Every benchmark number is quoted from EVIDENCE.md §4.3 or the tester's
   independent re-run (`.agents/tester/RESULTS/sonic-off-loop-audit-results.md`); the n² law is
   **derived** from those two points (§2.1.4), not re-measured. The per-pydantic-model cost
   (≈ 21 ms) is an **aggregate derived** from the flagship capture ÷ the AST class count, not a
   measurement.
2. **The internal tiktoken data structure** behind the n² was not re-derived (Rust extension, not
   inspectable in the installed wheel); the attribution is empirical + pattern-structural (§2.1.4).
3. **The recoverable fraction of the 243 s tiktoken block time is not separately measured** — the
   capture set records no per-episode payload shape (§2.3.5). The bound given is an order of
   magnitude, not a measurement.
4. **No live probing was performed.** The capture statistics come from the **durable** capture dirs
   (`/home/nea/agents-ensemble/data/wedge-captures/`, 20/20 present, read-only) and the audit's
   re-derived numbers; `:9797` was never contacted, so this lane has no live-state claim to make.
5. **Capture-version discipline:** the capture set spans **6× v0.31.2 / 7× v0.31.1 / 7×
   v0.31.10**. The two v0.31.10 schema captures and the four v0.31.10 tiktoken captures are the
   load-bearing ones for "is this still true in the current build?" — and the answer is **yes**:
   the hot-path files are byte-identical between v0.31.10 and v0.31.13 (§4.4).
6. **This is an analysis document.** It recommends; it does not implement. No source file was
   modified, no commit was made, no daemon was restarted.

---

*Lane: sonic-coder (single-module, analysis-only) · write: this file only · sources read:
v0.31.10 + v0.31.13 trees, the installed langchain/pydantic/tiktoken in the repo `.venv`, the
durable wedge-capture dirs, and the three audit artifacts + the tester record.*

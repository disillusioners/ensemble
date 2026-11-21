# TokenEconomizer — External Evidence Pack (evidence-external.md)

**Commission:** Comparative study vs agents-ensemble — external side (Microsoft TokenEconomizer).
**Investigator:** wanderer (external lane). **Sibling:** internal investigator owns `/home/nea/ensemble-src` analysis (not read here).
**Status:** ✅ COMPLETE — all nine facets (a–i) final; every claim cited to the pinned clone (`file:line`), the blog, or the paper (arXiv 2610.22732).

---

## 0. Provenance — the pinned artifact

| Item | Value |
|---|---|
| Repo | https://github.com/microsoft/TokenEconomizer |
| Local clone | `/home/nea/Code/opensource-projects/TokenEconomizer` (shallow, depth 1) |
| Pinned SHA | `f1b91a40e9537c3e5fce146385366cbb009f5ecc` (branch `main`) |
| Commit date | 2026-11-17 18:18:55 -0800 |
| Clone date | 2026-11-18T04:00:29Z |
| Size | 258 tracked files; 83,059 total lines; 53,766 Python; 802 TS/JS |
| Paper | arXiv **2610.22732** (cs.CL, 2026) — "TokenEconomizer: Experience-Driven Black-Box Cost Control for Coding Agents", Li/Xu/Singh/Chen/Inala/Siska/Tamersoy/Yu/Stoica/Galley/Gao — UC Berkeley + Microsoft Research + Microsoft Security (README.md:1-18, 527-538) |
| Blog | https://www.microsoft.com/en-us/research/blog/tokeneconomizer-learning-from-experience-to-make-ai-coding-agents-more-efficient/ (published 2026-11-16) |
| License / runtime | MIT; **standard-library only** Python 3.10+ (README.md:514, pyproject.toml) |

**Top-level layout:** `tokeneconomizer/` (core package: `microskills/`, `toolprune/`, `loopguard/`, `trace/`, `plugin/`, `ledger.py`, `pricing.py`, `stage.py`, `config.py`, `domain.py`, `names.py`) · `plugin/` (installable: per-agent manifests `.claude-plugin/`, `.codex-plugin/`, `.cursor-plugin/`, `.factory-plugin/`, `.antigravity-plugin/`, `.github/plugin/`, hooks JSONs, `scripts/te`+`te-hook` launchers, `slash-commands/`, `opencode/`+`pi/` TS extensions, `hermes/`, `codex-skills/`) · `experiments/` (paper harness: `te_bench/`, `te_harness/`, `scripts/`, `workloads/*.json`, tests) · `tests/` (offline unit tests, ~60 files) · `docs/` (ARCHITECTURE.md, PLUGIN.md, DROID.md, assets) · `scripts/` (build_plugin.py, dead_code.py) · `CLAUDE.md`, `README.md` (543 lines).

**Two-layer architecture (load-bearing for everything below):** the shipped *plugin* (what users install) and the *experiments harness* (what produced the paper) share only the `tokeneconomizer/` package; `experiments/` is NOT part of the installed plugin (README.md:518-523). Several paper mechanisms are **harness-only arms** — the plugin implements different, more conservative defaults (details in §5 and §6).

---

## 1. Blog-claims verification table

Blog: https://www.microsoft.com/en-us/research/blog/tokeneconomizer-learning-from-experience-to-make-ai-coding-agents-more-efficient/ (fetched 2026-11-18, full text captured).

| # | Claimed (commission list) | Verdict | Exact blog wording / evidence | Notes |
|---|---|---|---|---|
| 1 | Black-box PLUGIN on session logs; no agent/model modification; updates repo knowledge after each session | ✅ VERIFIED | "an open-source plugin that learns from an agent's past sessions in a repository … without modifying the agent or its underlying model"; "After each session, TokenEconomizer reads the session's log and updates what it knows about the repository." | Paper: "treats each agent as a black box: it acts through exposed prompt and tool settings, monitors execution events, and does not modify model weights or agent-loop code" (§3). Code: plugin/hooks.json hook events + `tokeneconomizer/trace/*.py` per-agent log readers. |
| 2 | MICRO-SKILLS: one-line repo notes; added to starting context only after several sessions confirm; retired on contradiction | ✅ VERIFIED | "Micro-skills capture repository-specific knowledge in one-line notes. … added to the agent's starting context only after several sessions have confirmed the pattern, and retires the note if later evidence contradicts it." | Code: 3 drifted confirmations (`lifecycle.py:17,113-115`); retire on score<0 with λ=5 contradiction weight (`lifecycle.py:18-21,116-119`). See §4.1. |
| 3 | TOOL PRUNING: removes tools history suggests unused; keeps core run-command/edit tools; restorable | ✅ VERIFIED | "removes tools that repository history suggests the agent is unlikely to use, while always retaining core tools for running commands and editing files. Removed tools can be restored if needed." | Code: `PROTECTED_TOOLS` 20 names (`toolprune/tools.py:41-48`) + host sets (`plugin/adapter.py:37-57`); restore = `te toolprune restore` (`cli.py:540-552`). See §4.2. |
| 4 | Profiling: 23 tool descriptions/turn, 16 never called | ✅ VERIFIED | "The agent received 23 tool descriptions on every turn, but 16 were never called." | Paper §2.2 adds: no repo invokes >7 tools; 3 tools = 98% of all calls. Claude Code catalog snapshot in clone lists 24 tools (`trace/claude_tool_catalog.json`). |
| 5 | Waste: 30% of tool calls repeated an earlier SAME-repo task's action (≥5 tasks accumulated); only 7% cross-repo | ✅ VERIFIED (with precision) | "After a repository had accumulated at least five completed tasks, 30% of tool calls repeated an action from an earlier task in that repository. Only 7% matched an action in another repository's history." | Paper §2.2: rises from 14% (2nd/3rd task) to 30% once ≥5 tasks precede; recurring actions = 12–28% of tool-call bytes. |
| 6 | Failed 17% of tasks = 32% of spend | ✅ VERIFIED | "The 17% of tasks that failed accounted for 32% of the spending." | Paper §2.2: the 10 failed executions of 60; Matplotlib failures 45 vs 20 turns; 45/60 trajectories repeat a failure signature; >half of post-repetition spend sits in runs that still succeed. |
| 7 | Fixed-strategy competitor plugins helped some repos but not others → cost control must adapt PER REPOSITORY | ✅ VERIFIED | "We also tested three popular open-source plugins, each built around a fixed cost-saving strategy. Each lowered costs in some repositories but not others. Together, these findings suggest that effective cost control must adapt to the repository" | Paper §2.1: RTK (output compression), Serena (semantic nav MCP), Aider RepoMap (2048-token map); all three cut Django but raise SymPy. |
| 8 | FAILURE-LOOP: judge model decides stuck-vs-progressing; if stuck → short redirect note; if STILL stuck → alerts developer, does not kill | ✅ VERIFIED — and the blog itself flags the paper/plugin split | "A judge model decides whether the agent is truly stuck or still making progress. If it is stuck, the agent gets a short note suggesting a different approach. **In the open-source plugin, if the agent is still stuck after the note, TokenEconomizer alerts the developer rather than stopping the work itself.**" | The qualifier "In the open-source plugin" is doing real work: the PAPER's controller **terminates** ("If the failure pattern recurs and the judge again finds the agent stuck, the controller terminates the rollout", §3.3; "A second stuck verdict ends the task", App. J) and has a judge-unvetoable forced stop at κ=2.5·B under a strict stall rule. The SHIPPED PLUGIN never stops a turn (`plugin/config.py:22` "The plugin never stops a turn itself"; old `stop` value now acts as `nudge` with a warning). Harness-only abort confirmed by `SpiralConfig` (`config.py:75-100`, knob doc "nudge … before abort"). |
| 9 | Results: 18.4–30.9% cost cut over 277 SWE-Bench tasks, 6 repos, 5 agent+model combos; savings NET of plugin's own model calls; success broadly stable | ✅ VERIFIED | "TokenEconomizer lowered the average cost by 18.4–30.9% … and saved the most in all 30 repository-combination pairs without performance loss."; "Reported savings also account for the cost of TokenEconomizer's own model calls." | Paper §4.2 + Table 1: cheaper in **all 30** repo×setting cells (5.2–40.1%); Pass@1 up in 4/5 (−1.0pp on Claude Code+Opus 4.8). Per-combo (README.md:43 alt-text): CC+Sonnet5 −23.6%/+1.3; CC+Opus4.8 −30.9%/−1.0; Codex+GPT-5.6-Terra −24.7%/+0.9; Codex+GPT-5.6-Sol −26.1%/+1.6; Copilot+Sonnet5 −18.4%/+2.6. |

**Additional blog facts (beyond the claim list), verified:** cheaper than default by the 6th task per repo, cumulative savings 16.9–32.0% (paper Fig 5(b)); Web-Bench 16.0–21.2% lower; security auditing 6.4–19.6% lower; "requires no API key of its own, and keeps what it learns on the developer's machine" (`~/.tokeneconomizer`); model calls "run through your own agent, on the same plan or login" (README.md:76-78).

**Divergences found: NONE at the blog level** — every listed claim reproduces verbatim or with added precision from blog+paper. The divergences that DO exist are *paper/blog story vs shipped-plugin code* (§5) — and the blog is itself careful about the one that matters most (claim 8's "In the open-source plugin" qualifier).

---

## 2. Paper extraction (arXiv 2610.22732, mechanism + evaluation)

### 2.1 Formal framework
- Unit of optimization: chronological task stream τ₁…τₙ in one environment (repo). Controller maintains state Sᵢ₋₁ (micro-skills + evidence, tool-use history, turn stats); before task i: **(ΔPᵢ, 𝒰ᵢ, Bᵢ) = 𝒞(τᵢ, Sᵢ₋₁)** (Eq. 1) — ΔPᵢ = activated micro-skills rendered as a short manual inserted between harness instructions and task description; 𝒰ᵢ = loaded tool set; Bᵢ = turn budget for the failure-loop guard. Objective: minimize mean per-task cost incl. own calls s.t. success ≥ default − ε (Eq. 6; ε not tuned post-hoc).

### 2.2 Mechanism I — micro-skills (paper §3.1 + App. F/G/I)
- **Proposal:** LLM proposer condenses bounded digests → candidate facts, each pairing a one-line instruction with **executable detectors** (regexes / built-in predicates). Proposer called **after task 4 and every 5 tasks thereafter**; window = **8 most recent task digests**; **≤4 candidates**; each candidate must **cite ≥2 window tasks** where its confirming detector matches; admitted candidates get detectors run retroactively over the window.
- **Detector semantics:** *scope* = task exercises the skill; an exercised task **contradicts** (instruction shown wrong, e.g. ImportError for a "installed" package) or else **confirms**; *need* detector = the targeted waste occurred or the stated fact was used (absent need detector → every exercised task counts as needed).
- **Activation (both must hold):** (i) confirmed on **several tasks whose edited file sets do not overlap** (repository-level, not issue-local, recurrence); (ii) **need rate ≥ threshold** (example (d): 0 < 0.4 ⇒ threshold **0.4**).
- **Support / retirement:** qᵢ(f) = Σⱼ γ^(i−j)·(1[confirm] − λ·1[contradict]) (Eq. 2), γ<1, λ>1 (example (c): **λ=5**). Confirmed skill retires when qᵢ(f)<0; **retirement permanent** ("identifier never activated again"); asymmetry deliberate (a stale skill is re-billed every turn; an eagerly retired one can be re-proposed under a new identifier). High-stakes skill retires at **first contradiction** once confirmed. Confirmed-but-below-need-gate = withheld, not retired. Lifecycle: candidate → confirmed → retired (Table 5).
- **Manual template (App. G.1):** `# repository manual ({repository}), learned from prior tasks in this repository` + `- {skill}` bullets + task prompt.

### 2.3 Mechanism II — tool pruning (paper §3.2 + App. F)
- Core tools always retained: **shell execution + file reading, writing, editing**. Others removed after a **fixed number of consecutive completed tasks uncalled (streak = 3)** OR when an **LLM judge** (tool description + task description with PR/issue/commit/file links masked) deems them irrelevant; judge failure ⇒ tool stays loaded. Mask fixed for the whole task. **Re-audit** periodically restores the full catalog (no judge call); a tool called during re-audit is **never masked again** (only never-called tools are masked).

### 2.4 Mechanism III — failure-loop intervention (paper §3.3 + App. F/G)
- **Budget:** Bᵢ = β·Qₚ(turn counts of successful prior tasks), p high, β>1 (Eq. 3). Not a stopping point — a reference for unusually long trajectories. Web-Bench: pre-registered 30 turns until 5 tasks passed.
- **Rule-based trigger (no model call):** repeated failure signatures / dense error output / length > Bᵢ. Failure signature = first error line of a failing output, numbers/addresses/paths stripped.
- **LLM trace judge:** only while trigger open; **≤1 consult per 5 turns**; after each productive verdict the interval **grows by 5 turns up to 20**. Digest = running counters + last 24 actions (never file contents or task text). Output: `{"stuck": bool, "confidence": 0.0-1.0, "reason": ...}`. Confident stuck verdict → **redirect** (LLM-authored summary asking for a materially different hypothesis).
- **Termination (paper behavior):** failure pattern recurs + judge again stuck ⇒ **controller terminates the rollout**. **Forced stop** (judge cannot veto): at **κ·Bᵢ, κ=2.5**, when the **strict stall rule** holds — all three at once: edit churn (≥4 edits to one file OR edits to ≥3 files) AND dense errors (≥6 error outputs) AND repetition (≥20% of failing outputs repeat an earlier failure signature). Productive/uncertain verdicts continue past Bᵢ up to the forced stop.
- **Prompts (App. G.3, verbatim shape):** trace judge "You are monitoring an AI coding agent that is partway through ONE task…"; redirect writer "An AI coding agent is stuck in a repetitive, unproductive loop… Write a SHORT redirect (2-4 sentences)… Do NOT tell the agent to stop…". Redirect example: "Process check (automated): so far you've made 11 edits across 1 file(s) … and run 0 verification(s) … try a MATERIALLY DIFFERENT hypothesis: re-read the failing test's expectation …".

### 2.5 Evaluation methodology (paper §4 + App. B/C/D/E/H/J)
- **Settings (5):** Claude Code 2.1.170 + Sonnet 5; Claude Code + Opus 4.8; Copilot CLI 1.0.84-7 + Sonnet 5; Codex 0.153.1 + GPT-5.6 Terra; Codex + GPT-5.6 Sol. **277 SWE-Bench tasks / 6 repos:** scikit-learn 60, SymPy 60, Django 40, Sphinx 44, Matplotlib 34, xarray 39 (199 Verified + 78 full-split, difficulty-raising; gold patches re-validated). Tasks in chronological order per repo; each task its own session; controller starts empty per repo. **3 seeds.**
- **Methods compared:** default (high effort), medium/low effort, RTK, RepoMap, TE, TE+RTK, TE+RepoMap (Serena excluded — tool pruning would remove its unused server, confounding).
- **Metrics:** Pass@1 (official grader; Web-Bench cumulative Pass@2; security detection F1, line-level strict) + **mean cache-aware cost** (Eq. 4): uncached-input/cache-read/output tokens at provider-reported prices; **every auxiliary call (proposer, tool judge, trace judge, redirect writer) charged to the task that triggered it**; task-agent usage + controller calls written to the same execution record at finalization.
- **Headline numbers:** TE alone −18.4% to −30.9% mean cost, cheaper in **all 30 repo×setting cells** (−5.2 to −40.1%); Pass@1 +1.3/+0.9/+1.6/+2.6 in four settings, −1.0 on CC+Opus 4.8; lowered effort saves similar/more but costs −1.8 to −10.3pp Pass@1. TE+RTK on Opus 4.8: −33.2% with Pass@1 above default. Ablations (CC settings): tool pruning −13.2/−17.7%, micro-skills −11.8/−14.3%, loop guard single digits; every variant Pass@1 within 3pp. Activity (per-stream means, Table 6): proposed ~10-12, confirmed ~6.5-8.4, retired ~1.7-2.6 (~1 in 5 proposed), masked 14.5-15.1 tools, re-audits 3.1-5.2, judge consults 9.9-13.1, redirects 3.9-5.3 (~1 task in 10), aborts 1.4-2.2 (<1 in 20).
- **Harness integration (App. J):** Claude Code headless JSON stream; masked tools via `disallowed-tools`; redirect = stop CLI + new invocation same container (redirect in prompt, edits on disk), second stuck verdict ends task, usage summed. Codex: pruning unit = capability packages, mask written to per-task config file. Copilot CLI: usage + catalog from debug log; `excluded-tools`. **Answer-key guard:** GitHub hosts resolve to unreachable address on all harnesses + pre-exec hook refusing GitHub shell commands (except Codex 0.153.1, whose hook didn't fire — first layer only).

---

## 3. Clone deep-dive — facets (d) micro-skills, (e) tool pruning  *(worker: te-ext-microskills-toolprune)*

### 3.1 Micro-skill admission — code facts (all `tokeneconomizer/microskills/`)

| Mechanism | Value | Evidence |
|---|---|---|
| Promotion threshold | **K_PROMOTE = 3** confirming sessions | `lifecycle.py:17`, applied `:113-115` |
| Non-overlap rule | **greedy pairwise-disjoint edited-file sets** (`_drifted_confirms`): a confirmation counts iff its file set is empty or disjoint from every already-kept confirmation's set; overlapping confirmations discarded entirely | `lifecycle.py:80-88`; edited-file extraction `:40-52` |
| Firing (need) gate | **FIRE_GATE = 0.40 over FIRE_MIN_N = 4** exercised sessions (`injectable()` = ACTIVE ∧ fire_den≥4 ∧ fire_num/fire_den≥0.40) | `lifecycle.py:22-23, 95-102` |
| Support decay γ | **0.8** | `lifecycle.py:18` |
| Contradiction weight λ | **5.0** | `lifecycle.py:19` |
| Retirement floor | score < **0.0** | `lifecycle.py:20, 116-119` |
| Hard kill | high_stakes ACTIVE fact retires on **first** contradiction (HARD_KILL=True short-circuits score) | `lifecycle.py:21, 116-119` |
| envfacts damping | command-spelling contradictions need a **streak of 2** (CONTRADICT_STREAK) | `envfacts.py:155, 168-173` |

- **"Never activated again":** manual `te microskills forget ID` → `forgotten` blacklist (re-admission refused; a *new id restating the forgotten fact* also refused, `distiller.py:709-723, 764-771`). Automatic retirement = sticky RETIRED (no update branch) + revision-locked (re-emitting the id may only change `detector.contradict`, `distiller.py:776-790`) — but not blacklisted. 🟡 nuance vs paper's blanket "never activated again".
- **Contradiction detection:** detectors are regexes over the session's raw command+output blob (`distiller.py:323-332`; blobs host-side only, stripped before the model sees digests, `distiller.py:267-269`). Built-in predicates (10): `repro_written, importerror, badarg, ran_tests, ran_checks, command_failed, missing_command, missing_dependency, bad_usage, edited` (`distiller.py:284-295`); plugin generic subset = 7 (`distiller.py:296-297`); paper's 4 (`repro_written, importerror, badarg, ran_tests`) are the *experiments* set (`distiller.py:443-444`). Third tier: LLM-authored **extractors** (`cmd_regex|out_regex|cmd_count`, `distiller.py:358-377`).
- **Proposer:** real LLM proposer, prompt = **rewrite** of paper G.1 (opens "You mine a DURABLE, REPO-SPECIFIC operating manual…" not "Your task is to mine a durable, repository-specific set of micro-skills…"); adds an **extractor DSL**, a **contradict-only REVISION protocol**, and an explicit **anti-exploration-prohibition clause** ("NEVER phrase it as a prohibition on exploration or tool use") the quoted paper prompt lacks (`distiller.py:430-489`).
- **Numbers match paper:** WINDOW=8, MAX_CANDS=4, MIN_CITE=2 (`distiller.py:31-33`; grounding enforced `:725-740, 767-769`). Cadence differs by layer: plugin default `microskills_propose_every=3` (`plugin/config.py:185`) vs harness `InjectionConfig.propose_every=5` (`config.py:27`; paper's "every 5") vs experiments INJECT arm 5 / ENVFACTS_PLUS 10 (`experiments/te_bench/mechanism_arms.py:22,35`).
- **Manual template (code):** `# repo operating manual (<repo basename>) — learned from prior tasks in this repo` / generic variant `— learned from earlier sessions in this repository` + `- <injection>` bullets (`distiller.py:793-801`). Paper literal differs ("repository manual", full repo id, comma separator).
- **Two-layer learning:** LLM-free **envfacts** layer (max 4 facts, 150 tokens — `envfacts.py:49-50`) renders *before* the paid manual; `microskills=on` = `envfacts+distiller` (`plugin/config.py:112`).
- Lifecycle constants (K_PROMOTE, γ, λ, floor, fire gate) are **hardcoded** — no config surface, no test pins by name (`lifecycle.py:17-23` only occurrences in repo).

### 3.2 Tool pruning — code facts

| Mechanism | Value | Evidence |
|---|---|---|
| Core protected tools | **PROTECTED_TOOLS = 20 names** (`bash, view, grep, glob, edit, create, str_replace, read_bash, stop_bash, list_bash, read_file, shell, apply_patch, Bash, Edit, Write, Read, Grep, Glob, MultiEdit`) | `toolprune/tools.py:41-48` |
| + interactive set | 31 names incl. AskUserQuestion, plan-mode, TodoWrite, Task*, Agent, Skill, **WebFetch, WebSearch** (web never pruned) | `plugin/adapter.py:37-48` |
| + Copilot set | 21 Copilot-surface names | `plugin/adapter.py:52-57` |
| Plugin prune rule | (prompts ≥ **p=10** spanning ≥ **m=2** sessions) OR (≥ **s=5** sessions of any length); ×3 factor for high-risk `NotebookEdit` | `plugin/fold.py:143-152`; defaults `plugin/config.py:187-189`; `adapter.py:60-61` |
| Quiet gate | deny list written only after **60 min** repo-session idleness (protects warm prompt cache) | `plugin/config.py:190`; `fold.py:426-456` |
| Paper's streak rule | **evidence=3 + judge=True** — the EXPERIMENT arm `UNTOOL` | `experiments/te_bench/mechanism_arms.py:64`; streak test `toolprune/stage.py:98` |
| LLM tool-judge | prompt EXISTS in OSS code (LOAD/DROP, JSON `{tool: {"load": bool, "reason": str}}`, issue ≤4000 chars — matches paper G.2 shape) but **dormant in the plugin**: `cfg.judge=False` default + no problem statements; harness-only. Protected tools force-loaded back. Judge failure ⇒ all stay loaded | `toolprune/tools.py:51-104`; wiring `toolprune/stage.py:99-104`; `toolprune/README.md:11-13` "the plugin has no task text and never calls a model to prune" |
| Re-audit | `evidence_reaudit` knob exists (`config.py:58,65`) but **harness-only, off**; plugin substitutes: ever-used tools never proposed + streak reset (`stage.py:97, 190-194`), auto-restore of newly-protected tools (`fold.py:194-205`), `kept` set; Codex-side periodic probe FIRST_PROBE_AT=24 / GAP=32 tasks, one capability at a time | `toolprune/stage.py:88-92`; `toolprune/codex.py:271-272, 400-442` |
| Restore-on-demand | **`te toolprune restore [TOOL…]`** — removes only plugin-added entries (recorded in `untool_applied.json`); restored tools join `kept`, never auto-removed again | `cli.py:540-552`; `denylist.py:542-546, 204-218` |
| Mask application | rewrites the agent's own per-repo settings: Claude `​.claude/settings.local.json` → `permissions.deny` (bare names remove schema from every request); Codex `.codex/config.toml` capability toggles; Copilot `.github/copilot/settings.local.json` → `disabledMcpServers` (**whole MCP servers**, not tools) | `denylist.py:52-54, 240-262, 322-350, 416-455, 549-558`; README.md:438-449 |
| Hygiene | atomic temp+`os.replace` writes; plugin-added entries recorded before writing; created files added to `.git/info/exclude` (never committed); takes effect next session | `denylist.py:124-172, 256-261` |

**Nothing in (d)/(e) is stubbed** — real code, real docstrings, zero TODOs. The divergences are configuration/layering, not missing machinery (§5).

---

## 4. Facet (h) — hardcoded vs adaptive *(wanderer, own reads)*

**Learned at runtime (adaptive, per repository + per agent):** which micro-skill facts exist/admitted/retired (detector evidence per session); envfacts; tool-use evidence (streaks, prompts, used-tools set); loop-guard envelope (percentile of this repo's ended-green turns); deny lists; per-repo/per-agent state under `~/.tokeneconomizer`.

**Hardcoded constants (code-level, no config surface):** all lifecycle constants (§3.1); WINDOW=8 / MAX_CANDS=4 / MIN_CITE=2 / MAX_EXTRACTORS=12; PROTECTED tool sets (20+31+21 names); HIGH_RISK={NotebookEdit} ×3; detector built-in predicates; Codex capability gates (MIN_LOADED_EXPOSURES, MAX_DEMOTIONS_PER_TASK=2, probe 24/32); `WAITER_POLL_S=600`; envfacts MAX_FACTS=4 / MAX_TOKENS=150 (caps configurable via `microskills_max_*`).

**Operator-tunable settings** (`plugin/config.py:178-225` `LiveConfig` defaults; README.md:378-388 table): `microskills="off"`, `microskills_max_facts=4`, `microskills_max_tokens=150`, **k**=`microskills_propose_every=3`, `toolprune="auto"`, **p**=`toolprune_evidence=10`, **m**=`toolprune_min_sessions=2`, **s**=`toolprune_sessions=5`, **q**=`toolprune_quiet_minutes=60`, `toolprune_protect=[]` (glob patterns), `loopguard="nudge"`, `loopguard_aggr∈{conservative,default,aggressive}`, **r**=`loopguard_nudges=1`, `loopguard.<name>` per-threshold pins (12 keys: min_turns, edit_churn, distinct_edit_files, error_outputs, stagnation, envelope_pct, envelope_margin, envelope_min_samples=**e**=5, backstop_mult=**b**=2.5, judge_confidence=**c**=0.7, gate_min_turns, gate_error_outputs; `plugin/config.py:145-159`), `repomap` opt-in (2048 tokens), `paid_model`/`<agent>_paid_model`/effort overrides, `local_model∈{off,fm}` (Apple Foundation Models on-device; `local_roles` default `[judge]`; `local_judge_confidence=0.9` because local confidences are coarse), `shadow` mode (log decisions, act on nothing), `history_import` (learn from Claude Code's pre-plugin logs, off by default, 200 sessions/365 days). Sources: defaults < `<root>/config.json` < repo `config.json` < env (`TE_*`); invalid values warn, never break hooks (`plugin/config.py:86-91`).

**Hardcoded prompts:** proposer (generic + experiments variants), tool judge, loop-guard judge/redirect templates — all embedded in code, not user-editable files.

---

## 5. Repo reality vs paper story — divergences that matter 🔎

1. **The plugin never terminates a turn; the paper's controller does.** Paper §3.3/App. J: second stuck verdict ends the task + judge-unvetoable forced stop at 2.5·B under the strict stall rule. Shipped plugin: `loopguard="nudge"` — "The plugin never stops a turn itself" (`plugin/config.py:22-23`); still-stuck → one developer-facing line "TokenEconomizer: this turn still looks stuck after a redirect (judge-stuck@0.82, 46 tool calls). Press Esc to interrupt it." (README.md:471-472); headless: flagged turn "runs until it ends or reaches the agent's own limits" (README.md:497-498). The abort machinery exists only in the harness (`SpiralConfig` knobs; aborts column in paper Table 6). The blog's "In the open-source plugin…" qualifier (claim 8) is accurate.
2. **Micro-skills is OFF by default in the plugin** (`microskills="off"`, `plugin/config.py:182`; README.md:367-370 gives the reason: proposer costs model calls and savings are repo-dependent). The paper always runs it. Turning it on = `config set microskills=on` → `envfacts+distiller`.
3. **The LLM tool-judge is dormant in the plugin** — code + prompt shipped (`toolprune/tools.py:51-104`) but `judge=False`, no problem statements, "never calls a model to prune" (`toolprune/README.md:11-13`). Paper's pruning = evidence streak (3) **plus** per-task judge; plugin = prompt/session evidence rule (10/2/5) alone (+ Codex capability controller).
4. **Threshold mismatches by layer:** proposer cadence plugin k=3 vs paper 5; prune rule plugin (p=10,m=2,s=5, raw streak 2 via `fold.py:177`) vs paper streak 3; these are *different configurations of the same machinery*, not different machinery. The repo is explicit about the experiments-vs-plugin split (`toolprune/README.md:5-18`; `experiments/` README).
5. **Re-audit exists but is harness-only/off in the plugin** — the plugin's recovery paths are instead: ever-used ⇒ never proposed, streak reset, `kept` set, `toolprune restore` one-command, auto-restore on newly-protected tools, Codex 24/32-task single-capability probes.
6. **Proposer prompt is a rewrite of the paper's G.1** (operating-manual framing, extractor DSL, contradict-only revision protocol, anti-exploration clause). Manual header literal differs. Judge prompt/output shapes match the paper.
7. **"Identifier never activated again" holds strictly only for manual forget**; automatic retirement is sticky-RETIRED + revision-locked (close enough in effect, different in mechanism).
8. **Supported agent versions moved past the paper's harness versions** (plugin supports Claude Code 2.1.300–2.1.310 / Codex 0.153.1–0.164.0 / Copilot 1.0.83–1.0.102, README.md:283-296; paper used CC 2.1.170 / Codex 0.153.1 / Copilot 1.0.84-7) — the released plugin targets newer CLIs than the paper's experiments.
9. **Plugin extras the paper doesn't describe:** quiet-minutes cache protection (q=60), `shadow` mode, on-device local judge (Apple `fm`, 0.9 confidence), per-agent `paid_model` overrides, `history_import` from pre-plugin Claude logs, envfacts LLM-free layer, `/tokeneconomizer:*` slash-command surface, Droid/Grok/Cursor/opencode/Hermes/Antigravity adapters (experimental), anti-exploration guardrail clause.
10. **Sidecall model economics differ plugin-vs-paper:** paper App. J — "Every paid controller call uses the same model and routing as the task agent" (so overhead can't look cheaper). Shipped plugin defaults its own calls to **`haiku`** via the user's own CLI (`plugin/sidecall.py`, `paid_model` default), explicitly noted in-code as a deliberate departure from the experiments. Both charge the calls to the ledger — but the plugin's judge/proposer are cheaper-than-agent by default, so plugin-mode overhead fractions are not comparable to paper numbers.

**Overall:** this is NOT a stub or partial release — 53.8K lines of real Python, every mechanism implemented, offline tests (~60 files) covering behavior, plus the full paper-reproduction harness in `experiments/`. The honest summary: **the open-source plugin is the conservative, interactive-safe configuration of the paper's machinery; the paper's aggressive behaviors (rollout termination, per-task tool judge, re-audit) ship only in the benchmark harness.**

---

## 6. Facets (a) integration surface, (b) storage, (c) controller loop  *(worker: te-ext-integration-storage)*

### 6.1 (a) Integration surface — what "interfaces the agent already provides" concretely means

**10 hosts** (`HOSTS = {claude, codex, copilot, droid, opencode, pi, cursor, hermes, grok, antigravity}`, `plugin/hosts.py:1320-1322`). The surface is a strict 5-layer funnel:

1. **Declarative hook registration (one JSON per host):**
   - `plugin/hooks/hooks.json:3-70` (Claude Code): **SessionStart (15s), UserPromptSubmit (20s), PostToolUse (60s), PostToolUseFailure (60s), Stop (10s), SessionEnd (10s)** — all `"${CLAUDE_PLUGIN_ROOT}/scripts/te-hook"`.
   - `plugin/hooks/codex-hooks.json`: 5 events (no PostToolUseFailure), SessionEnd 3s, `--host codex`, Windows inline `runpy.run_path` bootstrap.
   - `plugin/hooks/copilot-hooks.json`: 6 events, each with a ~2.5KB inline **PowerShell Windows launcher** re-implementing Python discovery (TE_PYTHON → python3/python/py, WindowsApps demotion, 3.10 probe), on total failure logs to errors.log and answers `{}`.
   - `plugin/hooks/cursor-hooks.json:2-23`: camelCase `sessionStart, beforeSubmitPrompt, postToolUse, postToolUseFailure, stop, sessionEnd`.
   - `plugin/hooks/droid-hooks.json`: 5 events. Top-level `plugin/hooks.json:1-13` = **Antigravity: SessionStart, PreInvocation, Stop only** — `--event` flag because "agy's payload names no event" (`hosts.py:808-809`); deliberately no PreToolUse ("one that prints `{}` makes agy deny the tool", `hosts.py:810`).
2. **Launcher `plugin/scripts/te-hook`** (63-line POSIX sh): `PYTHONUTF8=1`; Python discovery TE_PYTHON → python3.15…3.10 → python with `-E -S` version probes (pyenv-shim 127 passes are skipped); Microsoft Store alias demotion; **fails open by construction** — prints `{}` if nothing works (:61-63).
3. **`plugin/scripts/te_bootstrap.py`**: `MIN_VERSION=(3,10)`; locates the package via 5 fallbacks (TE_CORE_PATH → `<plugin>/lib` → `<plugin>/..` → **Codex marketplace `config.toml` `source=` parsing** → pip); written for any Python 3 ("no f-strings, no annotations") so an old interpreter still answers `{}`.
4. **`tokeneconomizer/plugin/hook.py` — the single entry**: stdin read as UTF-8 bytes (locale-proof); no-ops under `TE_SIDECALL=1` (recursion guard for the plugin's own nested agent calls) and `TE_DISABLE=1`; **every exception becomes `{}`** + timestamped traceback in `errors.log`; stdout protocol = `progress` lines first (the only channel Copilot's TUI shows), then exactly one JSON object, with `redirect_stdout(stderr)` so mechanism chatter can't corrupt agent-facing JSON (:85-100). Dispatch: `hosts.detect` → `host.prepare` → `LiveContext.build` → enabled gate → `host.expand` (Antigravity fans one hook call into several controller events) → `LiveController.handle` → `host.render`.
5. **Per-host adapters (`hosts.py`, 1380 lines) + `tokeneconomizer/trace/`** (the *sole* format-knowledge package — "Every mechanism reads a finished session through this package, so the format knowledge lives here and nowhere else", `trace/__init__.py:1-13`): detection priority `--host` hint → antigravity → **grok before claude** ("hooks inherit the parent's environment") → codex → copilot → droid → claude fallback; turn identity `prompt_id`/`turn_id`/`generation_id`; tool normalization (Codex shell-name aliases → canonical `bash`, `apply_patch` path extraction, Claude success-code inference, Copilot exit code from `<shellId…>`); `RESULT_CAP=20000` chars folded per tool result; `FoldLater` — a log that can't be had now leaves the session folded later.

**Micro-skill injection path:** rendered manual cached in `injection.json`, emitted at **SessionStart** as `additionalContext` (`controller.py:107-198` — replay-aware via `keeps_start_context`; Claude 2.1.307 & Codex 0.163.0 replay their first start's context). **Tool pruning path:** agent's own per-repo settings files (§3.2) — no CLI flag. **Redirect path:** mid-turn via hook output context/notice (§7.1), judge async.

### 6.2 (b) Storage — `~/.tokeneconomizer` (or `$TOKENECONOMIZER_HOME`), created 0700

```
~/.tokeneconomizer/
├── bin/te · errors.log · index.json (checkout index, redirects, layout=2)
├── sessions/<host>/<sid>.json      # REPO PINS {root,key,label,remote,how,cwd,at}
├── locks/<host>/                   # hookqueue per-session locks + event queues
├── hosts/<host>/catalog.json       # probed tool catalog
├── merged/                         # set-aside whole-merged state ("Nothing is deleted on the way")
└── repos/<repo-key>/<host>/
    ├── repo.json · sessions/<sid>.json · folded.json ({sid:{at,transcript,imported?}} dedupe)
    ├── ledger.jsonl                # one priced record per folded session (+ kind:"overhead" rows)
    ├── events.jsonl                # audit: inject/nudge/abort/turn/repomap/undelivered
    ├── envelope.json               # {level, resolved_turns[-500], resolved_snaps[-500]}
    ├── injection.json              # learned facts + rendered text (what SessionStart injects)
    ├── untool.json                 # toolprune evidence/proposal/needs_apply
    ├── notices.json · sidecalls.json · untool_applied.json
    └── mechanisms/{envfacts_state,manual_state}/<label>.json
```

- **Repo identity is the key insight** (`paths.py:200-279`): `key = "<slug>-<sha1[:10]>"` of a canonical identity ladder **BY_REMOTE** (normalized `origin` URL **parsed in-process from `.git/config`** — "a hook that shelled out to git would pay a subprocess on every tool call") → **BY_PATH** (realpath of main checkout via worktree `commondir`) → **BY_DIR**. Every clone/worktree of one remote shares learned state; `$TOKENECONOMIZER_REPO` is the metadata-less escape hatch; `GIT_CEILING_DIRECTORIES` honored with home as an extra ceiling.
- **`Store`** (`store.py`): plain JSON documents + append-only JSONL; **atomic** pid-suffixed tmp + `os.replace` writes; paranoid reads (only FileNotFoundError/ValueError fall back to default — a half-written file never reads as empty state); `fcntl.flock`/`msvcrt.locking` **degrading to no-op where neither exists** — "a lost update is recoverable, a crashed hook is not"; `locked()` reports whether the lock was taken so the fold walks away instead of hanging.
- **Ledger record** (`fold.py:516-578`): `{kind:"session", session_id, host, repo, folded_at, started, ended, shadow, imported?, prompts, injection, untool, model, requests, input_tokens, cache_read_input_tokens, cache_creation_input_tokens, cache_creation_1h_input_tokens?, output_tokens, effective_tokens, priced_by, turns, nudges, aborts, injected_chars, untool_applied[], overhead{6 keys}}`.
- **Identity over time** (`repos.py`): session pins (30d) + `index.json` redirects (≤8 hops, post-pin merges only); `adopt()`/`migrate_all` for key changes; misfiled sessions re-folded; merges keep `LEARNED=(injection.json, untool.json, mechanisms)` from the majority side only and **re-fold the other side from logs** — learned state is not additive; displaced state parked as `premerge-*`. **No database anywhere.**

### 6.3 (c) Controller loop — producer/consumer across a process boundary

**In-session half (inline, hook-budget-bound):**
- **SessionStart** (`controller.py:107-198`): inject `injection.json.rendered` on `startup/new/clear/compact`; sha1-deduped notices (toolprune applied, sidecalls paused, migration, repomap); housekeeping (`note_repo` → `prune_pins` → `start_migration` → `history.maybe_start`) then **`fold.spawn_fold(pending_sessions(exclude=self.sid))`** — earlier sessions that ended unseen are folded now.
- **UserPromptSubmit** (:290-310): closes a still-open turn as *interrupted* (recorded but excluded from the envelope); opens a fresh `TurnGuard`; first prompt may inject the prompt-ranked repo map.
- **PostToolUse/PostToolUseFailure** (:346-415): loop-guard only; subagents skipped; live gate overrides `gate_over_envelope=False`, `gate_min_turns≥min_turns` ("never merely because the turn outran this repository's usual length, which interactive turns do all the time"); **the judge never runs in the hook** — `AsyncJudge` starts a detached job, a later call picks up the verdict "with the redirect already written, so the agent never waits"; queued-event discipline: a decision reached on a replayed call is held for the next real call.
- **Stop** (:312-320): closes turn; an **ended-green turn** (resolved proxy: green unless a verification after the last edit failed) feeds the envelope (`loopguard.record_turn` under `"envelope"` lock) — "a turn's own outcome only ever gates LATER turns".
- **SessionEnd** (:418-432): stamps `ended`/`end_reason` → **`fold.spawn_fold([self.sid])`**.

**After-session half (detached):** `context.spawn` runs `python -m tokeneconomizer.plugin.cli fold --session <sid> --host <h> --cwd <repo>` detached (output → `fold.log`) — "anything slower than a hook may take". The **fold** (`fold.py:488-591`): import gate → `"fold"` lock → `host.complete()` (may fetch whole transcript; `FoldLater` → stays pending) → under `"mechanisms"` lock: `_fold_microskills` (envfacts always — free; distiller proposer every N sessions — paid sidecall) rewrites `injection.json`; `_fold_toolprune` (skipped when `prompts==0` — "a session that ran no prompt is no evidence about the tools") → `untool.json`; `session_usage` → `accounting.effective` → priced record + overhead rollup → **append `ledger.jsonl`, write `folded.json`, mark session folded**.

**Resilience:** three independent fold triggers — SessionEnd, the next SessionStart's sweep, and `pending_sessions` (a transcript quiet ≥ **STALE_S=600s** is fold-eligible; Antigravity has no SessionEnd at all and relies entirely on this). `folded.json` dedupes → each session folded exactly once. **Concurrency:** `hookqueue` per-session locks (1-10s waits, each below its hook timeout); overflow events spill to queue files drained by the next lock-holder (caps: 200 events / 8s per drain); SessionStart/UserPromptSubmit outputs are never queued (they must answer their hook). **Shadow mode** computes and logs every decision, acts on none.

## 7. Facets (f) failure-loop, (g) cost accounting, (i) eval harness  *(worker: te-ext-loopguard-cost-eval)*

### 7.1 (f) Failure-loop intervention — three-tier cascade, resolve-safety-first

**Architecture** (`loopguard/rules.py:1-13`): **Tier-0 free gate → (optional) paid LLM judge → strict heuristic fallback**, wrapped by a per-repo "envelope" and a hard backstop the judge cannot veto. Unit of protection = a **prompt-turn** (user prompt → agent stop), NOT a session (`loopguard/turn.py:11-13` — a whole session folded as one trajectory "looks stuck merely by being long"). Per completed tool call (`TurnGuard.fold`, `turn.py:390-454`): fold call into `SpiralState` (edit-tool edits AND shell edits — `sed -i`, redirects/`tee` into source files, inline Python writes, `patch`/`git apply`: `shell_edits.py:1-13`) → check backstop → `assess_abort`. Return ladder: `None` → `("nudge", reason)` → `("abort", reason)`.

**JUDGE PROMPT — VERBATIM** (`loopguard/judge.py:15-27`, `STUCK_JUDGE_SYSTEM`; user prompt = system + `"\n\nRecent process digest:\n" + digest + "\n\nReturn the JSON verdict:"`, `judge.py:40-41`):

> "You monitor an AI coding agent that is partway through ONE task in a software repo. A cheap heuristic flagged its recent process as a possible UNPRODUCTIVE LOOP. Read the leakage-safe digest of its RECENT actions and judge whether it is genuinely STUCK — repeating the same kind of edit/test/failure with no progress toward green tests, unlikely to recover if it keeps going the same way — versus still making real progress (changing failures, narrowing the problem, exploring new hypotheses).
>
> Judge ONLY the workflow from the digest; do NOT infer the task's solution or which lines to change. Favor SPARING (stuck=false) when there is any sign of progress; only return stuck=true with high confidence for a clear, repetitive dead-end.
> Return ONLY a JSON object: {"stuck": bool, "confidence": <0.0-1.0>, "reason": "<short leakage-safe justification>"}."

Parsing (`judge.py:53-62`): first `{...}` regex → `json.loads`, confidence clamped [0,1], reason capped 160 chars, any failure → `stuck=None` (ambiguous). Digest is **leakage-safe** (`state.py:223-241`): commands, search patterns, edited-file **basenames**, error **signatures**, running counters, envelope context line — never file contents or task text.

**NUDGE-WRITER PROMPT — VERBATIM** (`loopguard/judge.py:66-80`, `NUDGE_WRITER_SYSTEM`):

> "An AI coding agent is stuck in a REPETITIVE, UNPRODUCTIVE loop partway through one task in a software repo. You are given a leakage-safe digest of its RECENT ACTIONS only (tool names, shell commands, searched patterns, edited file BASENAMES, and error SIGNATURES — never file contents or the task description). Write a SHORT redirect (2-4 sentences) addressed to the agent that: (1) names the specific repetitive pattern you actually see in the digest (e.g. re-editing the same file, re-running the same test to the same failure); (2) proposes a MATERIALLY DIFFERENT next step (a different file/root-cause to consider, re-reading the failing test's expectation, verifying a new hypothesis before editing again).
> HARD CONSTRAINTS: Do NOT tell it to stop, give up, or reduce effort — only redirect. Do NOT infer the task's solution, name specific line numbers to change, or reference any code/identifier you cannot see in the digest. Output ONLY the redirect as plain text — no preamble, no JSON, no headings."

**REDIRECT TEMPLATE (count-mirror fallback) — VERBATIM** (`rules.nudge_text`, `rules.py:183-198`; selection order judge-riding → LLM writer → this template, `turn.py:465-482`):

> "Process check (automated): so far you've made {N edits} edits across {distinct_edit_files} file(s) (most to `{busiest}`) and run {test_runs} verification(s), and the same failure has recurred {repeated_fail_sigs} time(s) without the tests going green. This looks like a repetitive loop. Do NOT stop — instead step back and try a MATERIALLY DIFFERENT hypothesis: re-read the failing test's expectation, consider a different file or root cause than the ones you've been editing, and verify the new hypothesis directly before editing again."

**`assess_abort` cascade** (`rules.py:109-180`):
- **Hard backstop (pre-judge, judge-unvetoable)** `rules.py:130-144`: `turns ≥ backstop_turns AND is_stuck(...)` → abort; `backstop_turns = envelope × 2.5`, learned value clamped ≤ 0.9× run-cap (`envelope.py:90-123`).
- **Tier-0 gate** `rules.py:78-90`: opens on `repeated_fail_sigs ≥ 1` OR `error_outputs ≥ gate_error_outputs`, only after `gate_min_turns`. Plugin tightens (`controller.py:365-370`): never gate merely for outrunning the envelope (interactive turns do that routinely); `gate_min_turns = max(gate_min_turns, min_turns)`.
- **Judge tier** `rules.py:146-173`: stuck@conf≥threshold → abort even within envelope; `stuck=false` → spare even past envelope; `stuck=None` → spared ("judge-ambiguous-spared"). Judge consult rate-limited: gap 5 turns → max 20, +5 backoff after each productive verdict (`turn.py:340-346`).
- **No-judge fallback** `rules.py:175-180`: strict `is_stuck` conjunction (churny ∧ noisy ∧ repeating, after `min_turns`) + `turns ≥ envelope`.

**Threshold presets** (`DEFAULT_THRESHOLDS` `rules.py:20-43`; `SPIRAL_LEVELS` `:53-66`; envelope `envelope.py:28-45, 83-88`): min_turns 20/28/14 (default/conservative/aggressive), edit_churn 4/5/3, distinct_edit_files 3, error_outputs 6/8/4, stagnation 0.20/0.30/0.12, gate_min_turns 18/24/12, gate_error_outputs 8, judge_confidence **0.7/0.85/0.55**, envelope_pct **p90** (not max — a single 115-turn outlier disabled a max-based guard, `envelope.py:34-38`), envelope_margin 1.25/1.5/1.0, envelope_min_samples 5/7/3, backstop_mult 2.5/3.5/1.8, nudge_on_ambiguous False/–/True, local_judge_confidence 0.9 (`plugin/judge.py:88-92`, on-device `fm` floor), nudge budget r=1 (`plugin/config.py:194`). 🟡 paper describes β·Qₚ with "p high" — code pins p=90, β=1.25, κ=2.5; paper's strict-stall triple (churn ≥4/≥3 files ∧ ≥6 errors ∧ ≥20% repeat) = the code's `is_stuck` conjunction.

**Developer-alert path / termination — THE definitive answer:**
- **The live plugin NEVER terminates a rollout.** Three explicit sites: `plugin/controller.py:396-412` ("Still stuck after the redirect. The plugin never stops a turn itself -- the agents' hooks cannot all do it, and the person decides -- so the last rung is a notice that says how to stop it…"); `plugin/hosts.py:32-38` (module docstring: Claude Code's `{"continue": false}` would halt its loop, but Codex's hook replaces the tool result and the model carries on, Copilot's runs every call — "the person decides"); `plugin/config.py:21-27` (mode `stop` still read, acts as `nudge` with warning). Notice text: `"TokenEconomizer: this turn still looks stuck after a redirect ({short}, {g.cum_turns} tool calls). {stop_hint}"`; `stop_hint` = per-host ("Interrupt the agent to stop it." / headless "Press Ctrl+C to end the run."), overridden per ~10 host adapters (`hosts.py:152-153, 309…1246`); notices ride `{"systemMessage": ...}` (`hosts.py:149-150`), Copilot uses PROGRESS stdout lines, notice-relay hosts add a model instruction to repeat it "word for word" (`hosts.py:170-176`). `te status` counts `"{nudges} redirect(s), {aborts} still-stuck notice(s)"` (`cli.py:294`).
- **The benchmark harness DOES terminate**: `NudgeGuard.monitor` stops the single-shot stream at first stuck detection, re-invokes same container with `base_prompt + RESUME_HEADER + nudge`; abort is terminal (`te_bench/nudge_guard.py:185-190`). Codex-native controller **never aborts** (`codex_spiral.py`: post-green `SHIPPED_POLICY` "never terminally aborts a task"; `defensive` profile default = finalize + every deterministic signature, max 2 nudges, no abort).
- **Plugin judge runs OFF the critical path** (`plugin/judge.py:1-16, 25-50, 116-120`): hook writes `judge/{sid}.request.json`, spawns detached `te judge`, result JSON polled by the NEXT PostToolUse; pending judge reads as "throttled" = spare; spend charged to ledger at spend time as standalone `overhead` record (`plugin/judge.py:103-110`, `accounting.py:47-50`). A paused host (unsigned-in CLI) yields NoVerdict → spare, backstop remains.

### 7.2 (g) Cost accounting

- **Metric** (`pricing.py:1-42, 246-266`): cache-aware **effective_tokens** (fresh-input-token equivalents) = `input×1.0 + cache_read×w_cr + (cache_write−1h)×w_cw + cache_write_1h×w_cw1h + output×w_out`. Rationale: ~90%+ of raw tokens are discounted cache re-reads; raw totals bias A/Bs both ways. Weights keyed on **exact billed model id** (`weights_for`, `pricing.py:191-217`) from `MODEL_WEIGHTS` (snapshot 2026-07-27 + additions 2026-11-13). Key curves: claude-* `cr 0.10 / cw 1.25 / cw1h 2.0 / out 5.0` (Opus/Sonnet-5.5 cache-read 0.05; Fable/Mythos 0.025 — `pricing.py:157-160`); gpt-5.6-sol `0.10/1.25/—/6.0`; gpt-5.3-codex `0.0971/0.0/—/8.0`; gemini-4-* `0.05/0.0/—/5.0`. Unpriced id → **ValueError hard-fail in experiments**; live accounting uses labelled `FALLBACK_WEIGHTS {1.0/0.10/1.25/6.0}` with `priced_by:"fallback"` (`plugin/accounting.py:14-29`). Codex defaults billed id gpt-5.6-sol; router prefixes stripped pre-rollout (`pricing.py:179-188`).
- **Overhead ledger** (`ledger.py:19-26`): per-mechanism fields `optimizer_tokens, untool_classify_tokens, untool_judge_tokens, codex_untool_adapt_tokens, spiral_judge_tokens, spiral_nudge_writer_tokens`; `overhead_frac = overhead/(exec+overhead)` (`accounting.py:51-63`); `imported:true` sessions excluded from rates (`accounting.py:39-43`). Usage unrecoverable → runner estimator → chars÷4 with reply×5 (`pricing.py:269-283`).
- **Side calls** (`plugin/sidecall.py:1-71`): proposer/judge/nudge-writer run through the **user's own agent CLI** — no separate key, billed where the session is billed: Claude `claude -p --output-format json --tools "" --max-turns 1` + no-MCP flags on `paid_model` (**default `haiku`** — a deliberate departure from the experiments, which billed the agent's own model so overhead couldn't look cheaper); Codex `codex exec --json --disable hooks --ephemeral -s read-only` (~25k cached base prompt per call — why the judge is gate-gated); Copilot `copilot -p` with usage from its session log; Antigravity: none (`NO_SIDE_CALLS`). All run with `TE_SIDECALL=1` (hook entry no-ops → no recursion); `TIMEOUT_S=180`; a no-reply call pauses that host's side calls (`errors.log`). Optional on-device judge: `/usr/bin/fm` (~3B, 4096-token window, JSON-schema-constrained verdict `JUDGE_SCHEMA`), role-routed, CLI fallback (`sidecall.py:777-997`). **Net-out confirmed**: every plugin call charged to the session ledger, incl. async judge at spend time.

### 7.3 (i) Evaluation harness (`experiments/`)

- **Layout** (`experiments/README.md:9-16`): `te_harness/` runs each agent CLI inside the SWE-Bench task's own Docker image (Web-Bench host-side), grades with the benchmark's **official grader** (SWE-Bench in venv); per-task `row.json`/`transcript.jsonl`/`run_config.json` → `all_rows.csv` + `summary_by_cell.csv`. `te_bench/` = arms/adapters/task-sets; `scripts/launch/{main_sweep,codex_ab,smoke_test,fleet_budget}.sh`; `workloads/swebench.json` = **277 tasks** (the paper's set), `webbench.json` = **102**, `creddata.json`/`reposvul.json` included-unrun. Pinned agent versions: Claude Code 2.1.170, Codex 0.153.1, Copilot CLI 1.0.84-7 (matches paper App. J). 3 seeds; cost = cache-aware effective tokens **including the arm's own model calls**; cells paired by instance, never pooled across agent+model bases; **answer-key guard** on every arm (README:104; `te_harness/guards/net_guard.py`).
- **27-arm registry** (`te_bench/arms.py:29-40`, `ARM_ORDER` pinned + completeness assert): mechanism arms (`mechanism_arms.py:24-75`) — `INJECT={propose_every:5}`, `ENVFACTS`, `ENVFACTS_PLUS={propose_every:10}` (Codex default), `UNTOOL={evidence:3, judge:true}`, `SPIRAL={guard,judge,nudge}`, `SPIRAL_WEB=SPIRAL+{nudge_llm, frozen_envelope:30.0}` (Web-Bench streams too short for within-stream p90 — matches paper's "pre-registered budget of 30 turns"), `CODEX_SPIRAL={defensive}`, `CODEX_SPIRAL_POSTGREEN`; `trimech` = all three at the same parameters (ablations reuse exact flag sets). Baselines `rtk`/`serena`/`repomap` external, lazily-resolved. `te_harness` is a sibling package never imported by the core; the economizer sees only the `AgentRunner` protocol (`optimizer_call`, `run_task`, `begin/end_stream`).
- **Success-rate stability** = same tasks × same order × 3 seeds per setting (seeds differ only in agent nondeterminism), Pass@1 absolute vs default; Web-Bench `resolved_p2`.
- **Live end-to-end probe** (`plugin/probe.py:1-22`): `te --host copilot probe` builds a throwaway pytest-trap repo, runs the same prompt in **7 real Copilot sessions (~25-60k tokens each)**, applies a proposed deny list, re-runs, reports trap-avoidance + tool-definition token delta — under its own state root.

### 7.4 Worker 3 flags
- `MODEL_WEIGHTS` is a frozen price snapshot (2026-07-27 + 2026-11-13) with vendor-price drift comments (gemini-4-* prices rise 2026-12-01).
- The `stop`→`nudge` alias means historical configs with `stop` silently became notice-only.
- Line numbers pin to `f1b91a4` and will drift.

---

## 8. Sources

- Clone @ `f1b91a40e9537c3e5fce146385366cbb009f5ecc` — all `file:line` citations.
- Blog (2026-11-16) — full text captured 2026-11-18.
- arXiv 2610.22732 HTML (full text incl. Appendices A–J) — captured 2026-11-18.
- Worker report: te-ext-microskills-toolprune (2026-11-18) — facets (d), (e).
- Worker report: te-ext-loopguard-cost-eval (2026-11-18) — facets (f), (g), (i) incl. verbatim judge/nudge prompts.
- Worker report: te-ext-integration-storage (2026-11-18) — facets (a), (b), (c).

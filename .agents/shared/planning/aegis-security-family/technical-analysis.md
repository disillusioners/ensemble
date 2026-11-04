# Technical Analysis: Aegis Security Family

**Date:** 2026-11-04 (lane start 2026-11-04T14:47:59Z)
**Author:** planner[v2] via technical-analysis worker
**Analysis depth:** deep-dive (design-point resolution; the downstream plan worker consumes this verbatim)
**Status:** Ready for Review — DP1–DP8 resolved; Q1–Q4 flagged for the architect

**Worktree:** `/home/nea/ensemble-src-wt-aegis-security-family` @ `1f6898ae2889ff002b4656486f6be3ee1048b649`
**Companion record:** [`research-findings.md`](./research-findings.md) — every citation below resolves to a
numbered fact (A#/B#/C#/D#) in that file.

---

## Question

Design the **aegis** security agent family: a new orchestrator-class agent (peer of `leader`) plus a
three-child white-hat sub-team (`hacker` offensive / `cracker` protection-strength / `sweeper` SAST),
with `worker` joining as a shared generalist lane. Resolve eight design points — **DP1** tool matrices,
**DP2** skill architecture, **DP3** sandbox/ownership model, **DP4** findings artifact + report→fix→
re-verify loop, **DP5** peer posture, **DP6** model assignments, **DP7** test/pin fold enumeration,
**DP8** phase structure — under the binding constraints: `agents/` + `tests/` only (no `daemon/`
changes), no new tool category, one-commit pin ceremony, `SEALED_SHA_BASELINES` untouched, standard
lane ending in **STAGE** (never promote).

---

## Context Summary

The ensemble's agent model is a **caller-side authorization** system. A spawn or send is permitted
only when the *caller's* `meta.json` names the target in `team_members`; the check is deny-by-default
and consults the caller's versioned meta first (A1). Categories in the caller's `tools.allow` silently
**widen** the effective team by adding the category's backing agent — `knowledge`⇒`explorer`+`kb-writer`,
`chart`⇒`charter`, `image`⇒`image-reader`, `design`⇒`image-comparator`, `council`⇒`governor` (A1).
`tools.deny_spawn` is the one lever that strips an implied member **without** removing the tool (A1).
This makes the allow list a *two-axis* surface — tool access and spawn reach — and the aegis family's
whole safety posture rests on getting both axes right.

Two independent reach paths already exist and both are already open: **`ari` reaches any agent through
`job_create` with a free-form `agent_id` and no team gate whatsoever** (A3), and **`leader` reaches only
through `team_members`** because it holds no `job` category (A4). So aegis is dispatchable by `ari`
with zero `ari` changes, and reachable by `leader` with exactly one added line.

The trap surface is the **allow-list closure**. An agent with an empty or absent `tools.allow` inherits
the *default universe* — every non-privileged category — which includes the `plane` read tools, and the
census test freezes the read-holder set at `{"watcher"}` (B3). A new agent that forgets its allow list
does not fail safe; it fails **red**. A second, quieter trap: the allow list is not fully closed, because
`innate_skills` silently *append* their mapped categories to it (D3), and the mapping includes
`chart`, `todo`, `dynamic-skill`, `skill-evolution`, `question` (D3/D14).

The prompt-artifact baseline is a **depth-2** pin: `agents/<dir>/<one of 8 artifact names>`, 161 rows
today, zero rows contributed by `meta.json`, `skill-set.yaml`, or `skills-template/*` (B1/B10). Four new
agents with four prompt files each is therefore exactly **161 → 177** (B1). The baseline re-stamp is
**venv-free** by construction — the generator loads `prompt_artifacts.py` by file path precisely so the
bare host `python3` that `stage.sh` runs under can use it (B2).

Three findings shape the design more than any other. First, **`worker` cannot make charts** — the
commission assigns worker "report charts", but worker's 21-category allow contains no `chart` and the
commission forbids touching worker (D4). Second, **the per-agent `watchover` meta key is inert** —
`AgentMetadata` has no such field, its `model_config` is `extra="ignore"`, and no daemon code reads
one; the real lever is the per-instance REST API (D7). Third, **`bash` runs through `/bin/sh`** for
string commands, so the commission's "loops go through script files" constraint is a code fact, not a
style preference (D10).

---

## Architecture

### Current Patterns (the house shapes this family must fit)

| Pattern | Where | Consequence for aegis |
|---|---|---|
| Caller-side team gate, deny-by-default | `daemon/tools/_auth.py:90-260` (A1) | aegis's roster is a *pure caller-side* declaration |
| Category→agent implied membership | `daemon/tools/_auth.py:181-244` (A1) | every category aegis grants is a spawn-grant unless `deny_spawn` trims it |
| Two-axis allow surface (tools vs spawn) | `daemon/tools/instance.py:563-579` (A5, D5) | granular deny can subtract single tools *inside* a granted category |
| Closed allow as the fence | `daemon/tools/instance.py:551-569` (D13) | non-empty allow ⇒ only named categories; privileged 17 never join (D1) |
| Innate-skill allow widening | `daemon/tools/instance.py:190-235` (D3) | the effective surface is `meta allow ∪ innate-mapped categories` |
| Fail-soft registry | `daemon/registry.py:656-662` (A9) | a malformed `tools` block = **all tools allowed** |
| Literal-file self-install skill | `agents/charter/skills-template/install-mermaid-cli.{md,lib.sh}` (C3) | the only in-repo installer template — the shape to copy |
| File-invoked vs bank-seeded skill | `agents/charter/` (no `skill-set.yaml`) vs `agents/designer/` (C2) | the lever that keeps offensive procedures out of the global bank (C6) |
| Depth-2 prompt pin | `daemon/tools/prompt_artifacts.py:355-377` (B1) | 161 → 177, one commit, last content change to `agents/` |
| Dynamic rot-mitigation for dispatchers | `tests/unit/test_report_integrity_prompts.py:232-266` (B4) | aegis must carry the scrutiny snippets |
| Org-chart rule for fallbacks | `docs/agent-prompt-writing-guide.md` §8 (C1) + `tests/unit/test_maintenancer_team_members_within_team.py` (B8) | aegis may **not** name `developer` as a spawn fallback |
| Sole-lane doctrine | `agents/designer/rule.md` + `workflow.md` (C4) | the aegis analogue: *"I never run offensive tools myself."* |

### Module Boundaries (the aegis family's place in the daemon)

```
                        ┌──────────────────────────┐
   ari ──job_create──▶  │   aegis (orchestrator)   │ ◀── spawn_instance / send_message
   (free-form,         │   team_members =          │     (gated by team_members, A1)
    NO team gate)      │   [hacker, cracker,       │
                        │    sweeper, worker]       │
                        └───────────┬──────────────┘
                                    │ spawn_instance (team-gated)
             ┌──────────────┬───────┴───────┬──────────────┐
             ▼              ▼               ▼              ▼
        hacker          cracker         sweeper         worker
      (offensive)   (crypto/creds)      (SAST)      (shared generalist
      AFL++/libFuzzer hashcat/john  semgrep/gitleaks  lane — NO offensive
      own sandbox     own samples       cppcheck/tidy   tools, doctrine)
             │              │               │              │
             └──────────────┴───────┬───────┴──────────────┘
                                    ▼
                   engagement sandbox (own dirs, own processes)
                                    ▼
                  findings record  ──▶  leader ──▶ developer ──▶ re-verify
                                    (hand-off, no direct developer spawn)
```

**Boundary rules that fall out of the mechanics, not from preference:**

- `aegis → leader` is a **message**, not a spawn. `leader` is not in aegis's team and must not be
  (guide §8 org-chart rule, C1). The remediation hand-off is report *content*; `developer` is
  **leader's** to dispatch (D11 — critical notes are leader-curated too, so they are not a
  hand-off channel either).
- `aegis → worker` **is** a spawn and is structurally permitted (A10 — the gate reads only the
  caller's meta; worker needs no change).
- `aegis → charter` (chart) would be an **implied** member, not a declared one (A1) — this is the
  Q1 deviation (D4).

---

## DP1 — Tool matrix per agent

### Recommendation

**A closed `tools.allow` is the fence; `deny` carries only what must be subtracted *inside* a granted
category; `deny_spawn` carries the one implied-member trim.** Under a non-empty allow the tool filter
grants exactly the named entries and nothing else (`daemon/tools/instance.py:563-569`, D13), so the 17
privileged categories — `secrets`, `ens-db`, `ens-env`, `db`'s siblings, `system_upgrade`, `system-log`,
`settings`, `mcp_rpc`, `instance_read`, `instance_ops`, `metrics-read`, `wedge-capture`, `board`,
`self-alarm`, `view-views`, `view-write`, `board-link`, `notify-user` — are already unreachable (D1).
Listing them in `deny` would be dead weight and would wrongly imply the allow list is open; the sketcher
exemplar says exactly this — *"the closed allow is the fence; a deny list would be dead weight"*
(`tests/unit/agents/test_sketcher_agent.py:133`).

**`aegis` — orchestrator**

| axis | value | why |
|---|---|---|
| `tools.allow` | `instance`, `bash`, `proc`, `filesystem`, `time`, `self`, `help`, `context`, `shared_meta_kv`, `knowledge`, `mcp`, `chart`, `question`, `project`, `midflight`, `dynamic-skill`, `todo` | see per-row rationale below |
| `tools.deny` | `write_file`, `edit_file`, `terminate_instance`, `pause_instance`, `resume_instance`, `set_instance_tunable`, `project_create` | 5 are intra-category subtractions (D5), 1 is a project-row write (D2) |
| `tools.deny_spawn` | `knowledge` | keeps `explore`/`experience`, removes `explorer`/`kb-writer` from the effective team (A1) |
| `team_members` | `["hacker","cracker","sweeper","worker"]` | commission roster, verbatim |
| `innate_skills` | `["dynamic-skill","todo"]` | and **both** categories appear in `tools.allow`, so the innate expansion (D3) is a no-op and the effective surface equals the pinned allow exactly |

Per-category rationale for `aegis`:

- `instance` — required: `spawn_instance` + `send_message` are the orchestrator's whole job. The
  category also carries `terminate_instance`, `pause_instance`, `resume_instance`,
  `set_instance_tunable` (D2) — **all four denied**; instance lifecycle belongs to `leader`.
- `filesystem` — read the target tree, read the findings record. Writes denied (§ below).
- `bash` + `proc` — provision/verify the sandbox, run the verification commands, drive long scans.
- `knowledge` — query the project KB for prior audits and architecture context. **Trimmed** with
  `deny_spawn` so the direct spawn right for `explorer`/`kb-writer` never materialises.
- `mcp` — the CVE-lookup surface: `webfetch` is a **builtin** server (D8) and `context7` rides along.
  The `plane_*` tools are excluded by the ownership guard (D12) ⇒ no E3 census impact (B3).
- `chart` — **the Q1 deviation**: worker cannot make charts (D4), so aegis holds `chart` and the chart
  specialist produces the artifact.
- `question` — scope clarification ("which directories are in scope?"). No implied member.
- `project` — project metadata for engagement scoping. `project_create` denied (D2).
- `midflight` — observe in-flight child state while waiting on lanes (leader holds it).
- `context` / `shared_meta_kv` / `self` / `time` / `help` / `dynamic-skill` / `todo` — engagement
  state, memory, timestamps, tool discovery, skill feedback, multi-lane tracking.

**`hacker` — offensive analyst**

| axis | value | why |
|---|---|---|
| `tools.allow` | `bash`, `proc`, `filesystem`, `time`, `help`, `self` | a leaf that runs a fuzzing campaign and reads the target source |
| `tools.deny` | `write_file`, `edit_file` | structural write fence (A6) |
| `team_members` | `[]` | leaf (A2) |
| `llm_model` | `coding` | DP6 |

**`cracker` — protection-strength auditor**

| axis | value | why |
|---|---|---|
| `tools.allow` | `bash`, `proc`, `filesystem`, `time`, `help`, `self` | runs hashcat/john against self-generated samples; generates the sample data itself (DP2) |
| `tools.deny` | `write_file`, `edit_file` | structural write fence; sample data is produced by a script into the sandbox (D10) |
| `team_members` | `[]` | leaf |

**`sweeper` — SAST workhorse**

| axis | value | why |
|---|---|---|
| `tools.allow` | `bash`, `proc`, `filesystem`, `time`, `help`, `self` | runs the scanner suite and triages output; scanner artifacts land in the sandbox via script redirection (D10) |
| `tools.deny` | `write_file`, `edit_file` | structural write fence |
| `team_members` | `[]` | leaf |

**`leader` — the one-line change**

```diff
   "team_members": [
     "planner", "developer", "reviewer", "tidier", "approver", "architect",
     "tester", "giter", "devops", "explorer", "wanderer", "kb-writer",
-    "doc-writer", "maintenancer", "designer", "gaia"
+    "doc-writer", "maintenancer", "designer", "gaia", "aegis"
   ],
```

Rationale: `leader` holds the `instance` category but **no `job` category**, so `team_members` is its
only agent-reach path (A4). The edit is agents-only and rides the pin ceremony commit. `meta.json` is
**not** in the baseline's artifact set (B1), so this line does **not** change the 161 → 177 count.

### The write fence — what it actually buys

`filesystem` is one category holding all six file tools including `write_file`/`edit_file`; there is
**no read-only filesystem sub-category** (A6). Structural read-only is therefore
*allow `filesystem` + deny `["write_file","edit_file"]`* — the schema's own worked example
(`daemon/registry.py:111-114`). The deny is applied to **all four** agents uniformly.

**Honest limit (must be stated in the aegis persona and in the plan):** every lane holds `bash`, which
is an unconstrained write path (A6). The write fence removes the *easiest accidental* mutation — an
LLM calling `write_file`/`edit_file` directly on a source file — and leaves only the deliberate
`bash` route. Read-only-on-target is therefore **doctrinal for the whole family**, exactly as the
commission anticipated. The design consequence is that the fence must be paired with (a) a persona
cardinal naming the legality line, (b) a sandbox-ownership check before any active technique
(DP3), and (c) an explicit statement in the plan that the fence is a *speed bump*, not a boundary.

A second consequence, and it is a **feature**: because aegis cannot write, **the report document is
written by `worker`** — which the commission already assigns "drafting findings documents" to. The
write-deny makes the commission's division of labour structurally necessary rather than merely
conventional. Same for the children: `cracker` generates its sample corpus with a script into the
sandbox rather than with `write_file` (D10: `/bin/sh` string commands ⇒ loops live in script files).

### CVE-lookup surface (the DP1 sub-question)

Three routes exist (A7); the recommendation is **route 1 + route 2 together, on `aegis` only**:

- **Route 1 — `mcp`**: `webfetch` is a **builtin** server (`daemon/mcp/builtin_servers/webfetch.py:17-40`),
  i.e. the fetch capability the commission wants exists — but MCP servers are enabled *per deployment*,
  so the prompt must treat "a web-fetch capability" as **conditional**.
- **Route 2 — `bash curl`**: always available wherever `bash` is granted; the universal fallback.
- **Route 3 — `mcp_rpc`**: privileged, `ari`-only ⇒ **not available** to the children, and not to
  aegis.

**Children get neither.** CVE context reaches them through the dispatch prompt. Each of the three
children is an offensive-capable lane; the `"mcp"` grant sweeps in *every* live MCP server's tools
(A7/D8), which is not a cost worth paying three times.

*Architect flag — Q3:* there is **no web-search tool category** in the registry (D2/A7). A real
CVE-database API (NVD/CVE JSON over HTTPS) would be a **new category** ⇒ the 3-step registration
seam + `tool-categories.json` regeneration ⇒ **the F-1 trap class** (B3) ⇒ out of scope for this
lane. v1 ships `mcp`+`curl` and treats structured CVE lookup as a v2 item.

### Implied-member side effects — the `deny_spawn` decision

| category aegis holds | implied member(s) | decision |
|---|---|---|
| `knowledge` | `explorer`, `kb-writer` | **trim** — `deny_spawn: ["knowledge"]`. The KB lookup is wanted; the *direct spawn right* over those two agents is not, and they are not in the commission's roster |
| `chart` | `charter` | **accept** — the chart artifact is wanted and cannot be produced any other way (D4). **This is the Q1 deviation.** |
| `mcp` | none | — the sweep is dynamic; `plane_*` is excluded by the ownership guard (D12) |
| `instance` | none | — `instance` is not in `TOOL_REQUIRED_AGENTS` (A1) |
| `question`, `project`, `midflight`, `context`, `shared_meta_kv`, `self`, `time`, `help`, `dynamic-skill`, `todo`, `bash`, `proc`, `filesystem` | none | — none appear in `TOOL_REQUIRED_AGENTS` (A1) |

**Honest caveat on the `knowledge` trim (must reach the plan):** `deny_spawn` closes the *direct*
spawn path only. `explore()` spawns an `explorer` instance internally
(`daemon/tools/knowledge_tools.py:754`) and `experience()` spawns a `kb-writer`
(`daemon/tools/knowledge_tools.py:386`) — both via `invoke_agent_and_wait`, which **does not go through
the team-membership gate**. So holding `knowledge` means aegis can *cause* explorer/kb-writer instances
to exist (a resource cost and an audit-trail event) even though it cannot address them as team members.
That is the same shape as the documented `chart`/`charter` use case (`daemon/registry.py:96-105`).
Recommendation: keep `knowledge`, use it sparingly, and do not let any aegis prompt name `explorer` or
`kb-writer` as a fallback (the org-chart test in DP7 enforces the prose half).

### Alternatives considered

| Alternative | Verdict | Why |
|---|---|---|
| **Open allow + long deny list** (deny `secrets`, `db`, `ens-db`, `system`, `system_upgrade`, `job`, `infra`, `service`, `mcp_rpc`, `instance_read`, `instance_ops`, `settings`, `board`, …) | **Rejected** | Under a closed allow these are already unreachable (D13). A long deny list is dead weight *and* it falsely signals an open allow — the next reader would believe the fence is the deny list. It also makes the allow-pin test fragile (every future category addition would need a deny line) |
| **Grant `chart` to `worker`** so the commission's division holds literally | **Rejected — out of scope** | The commission states **ZERO changes to worker** (A10), and `worker` has no `instance`/`job` tools, so worker cannot spawn charter anyway. It would also be a change to the *shared* lane used by every other caller — the highest-blast-radius edit available |
| **Drop charts from v1 scope** | **Viable alternative to Q1** | Zero deviation from the stated roster; the findings record stays text + JSON. Cost: the commission explicitly names "report charts" as a worker task, so this is a scope reduction, not a neutral choice. **Offered as the fallback if the architect declines Q1** |
| **Give `aegis` the `job` category** so it can dispatch `developer` directly | **Rejected — dangerous** | `job_create` takes a free-form `agent_id` with **no team gate** (A3). Granting it to aegis would let the security family bypass the org-chart rule entirely — aegis could dispatch `developer`, `reviewer`, anything. The remediation hand-off must go through `leader` |
| **Grant `aegis` `mcp_full_access`** | **Rejected** | E4 reddens on any new agent holding `mcp_full_access` including plane (B3) |
| **Children hold `"mcp"`** for their own CVE lookups | **Rejected** | The `"mcp"` grant sweeps in every live MCP server (A7/D8). CVE context is cheaper to pass in the dispatch prompt than to grant a sweeping surface three times |
| **Structural write-deny only on `aegis`, children keep write** | **Rejected** | The guardrail is family-wide ("read-only on target codebases"). Uniform deny across all four is simpler, cheaper to audit, and makes the "worker writes the report" division structurally necessary |

---

## DP2 — Skill architecture

### Recommendation

**One family, four homes. Per-agent skill directories; exactly ONE seeded skill (aegis's methodology);
all three installer families file-invoked so their procedure text never enters the global skill bank.**

| # | Skill | Home | Mode | Sizing for the vkey target class |
|---|---|---|---|---|
| 1 | `aegis-strategy` — STRIDE threat modeling, audit planning, **severity taxonomy**, **findings report format**, engagement scoping, the report→fix→re-verify doctrine | `agents/aegis/skills-template/aegis-strategy.md` | **SEEDED** (`skill-set.yaml`, `auto_load: true`, `category: planning`) | the canonical home for the taxonomy + report format (guide §2 canonical-home rule, C1) — the four prompt files link to it, they do not restate it |
| 2 | `fuzzing-toolchain` — AFL++ + libFuzzer install, harness construction for a **raw keystroke-stream** engine, corpus management, crash triage | `agents/hacker/skills-template/` + `.lib.sh` | **FILE-INVOKED** (no manifest) | the prime target: a C++17 engine ingesting untrusted byte streams. **libFuzzer primary** (LLVM `-fsanitize=fuzzer`, trivial on a clang C++17 build); **AFL++ secondary** (black-box / non-instrumented builds, parallel mode, longer campaigns) |
| 3 | `credential-bench` — hashcat + john install, **synthetic sample-corpus generation discipline**, bench harness, result parsing | `agents/cracker/skills-template/` + `.lib.sh` | **FILE-INVOKED** | hashcat/john **only against corpora the family generates itself** (DP3) — never real user data, never live credentials |
| 4 | `sast-toolchain` — semgrep + gitleaks + cppcheck + clang-tidy + trivy install, run, and **SARIF/JSON triage** | `agents/sweeper/skills-template/` + `.lib.sh` | **FILE-INVOKED** | **cppcheck** (`--enable=all`) + **clang-tidy** (clang-analyzer checks, needs `compile_commands.json`) are the memory-safety workhorses for C++17; **semgrep** for CI/config/scripts idioms; **gitleaks** for secrets across the tree *and* history; **trivy** for the **CI supply chain** (dependency CVEs, image/config scanning) — the commission's fourth target class |

**Why file-invoked for all three installers (C2/C6):** the skill bank is **global**
(`project_id=None`, `daemon/services/skill_seed_service.py:325-426`) — anything seeded via
`skill-set.yaml` becomes searchable by `worker` and by every dynamic-skill agent. The commission's
boundary *"worker does NOT run offensive tools or scanners"* would otherwise be prose against a
searchable global index containing hashcat/john/AFL++ procedures. Charter is the proven precedent for
mode (ii): `agents/charter/` has **no `skill-set.yaml`** and its installer is never seeded (C2).

**Why the methodology skill IS seeded:** it is aegis's own operating doctrine (the designer's
`design-strategy` precedent — `agents/designer/skill-set.yaml`, `auto_load: true`). It is harmless to
other agents, it should be *always* in aegis's context, and it gives the severity taxonomy + report
format a **single canonical home** outside the four prompt files (guide §2, C1). It declares **no
`requires:` block**, so the `capability_check(...)` first-instruction rule
(`daemon/services/skill_seed_service.py:361-381`) does not bind it.

**Why not one shared "security toolbench" skill family across the three children:** the install
*skeleton* (fences → pre-flight → user-space install → checksum verify → staged config → readiness
probe → release lock → reversal) is identical across tools, and the three `.lib.sh` files will
therefore carry three copies of it. The alternatives were weighed:

- *One shared library file referenced by all three* — rejected: the reference must be a path, and
  paths are fragile (the worktree path ≠ the installed release path) and are prompt-prose hazards
  (guide §3 closure grep, C1).
- *One shared library in the bank, loaded by all three* — rejected: it would put the install
  skeleton into the global bank (weakening the very boundary the file-invoked mode buys), and the
  bank is the wrong home for an install library.
- **Three self-contained libraries + a lane test that pins the FENCES block identically in all
  three** — **recommended.** The drift risk is real but bounded, and the mitigation is a *test*, not
  a cross-agent path dependency. The per-tool differences (release-asset URLs, checksum sources,
  readiness probes, tool-specific flags) are substantial enough that each library genuinely diverges
  after the skeleton.

**The checksum gap (C3) — a concrete amendment to the charter template:** the mermaid installer does
**not** checksum artifacts; its integrity story is *major pins + staged verify + probe re-check*. For
scanner installers that is **not sufficient**: `gitleaks`, `semgrep`, and `trivy` ship release
binaries. The in-repo precedent to combine is `daemon/services/live_views.py:1357-1406`
(`SHA256SUMS` hex + W3C-SRI base64, both pin forms). ⇒ **Every installer in this family must
sha256-verify its fetched release asset** before promotion.

**Fences every installer must carry (verbatim from the charter precedent, C3):** NO Docker · NO
system packages / apt · NO remote fetch-and-execute (`npx --no-install` only) · honest-stop
`HARD_BLOCKER` envelope · idempotent verify-only fast path · user-space install root with
**absolute-path capture** (non-interactive bash does not source `~/.bashrc`) · pinned-major versions ·
stage-to-`mktemp` + trap, promote only after verification passes · release lock whose **release MUST
run on failure** · attempt counter capping self-heal at 2 · mandatory **REVERSAL (uninstall)** section ·
`READINESS_PROBE` with **exit 0 warm / 1 cold / 2 in-progress**.

**Prompt-prose hygiene for the skill bodies:** the closure grep forbids `.md` path tokens in prompt
surfaces (C1 §3). The skill bodies are prompt surface (auto-loaded skills assemble into the owning
agent's prompt). ⇒ Reference skills by **bare name** (`fuzzing-toolchain`, never
`fuzzing-toolchain.md`), and reference the `.lib.sh` only where the agent actually reads it —
charter's `workflow.md` cites `skills-template/install-mermaid-cli.lib.sh` and is the **precedent
exemption** for an operational runtime read target (C1 §3 scope carve-out). The lane's closure-grep
leg must **enumerate** those exemptions explicitly rather than silently pass them.

### Alternatives considered

| Alternative | Verdict | Why |
|---|---|---|
| Seed all four skills | **Rejected** | Puts hashcat/john/AFL++/semgrep procedures into the **global** bank where `worker` can search them (C6) — the commission's worker-does-not-run-offensive-tools boundary becomes unenforceable against a searchable index |
| One shared toolbench skill family in a common directory | **Rejected** | Cross-agent path references are fragile (worktree vs installed tree) and are prompt-prose hazards; the shared library would have to be duplicated per agent anyway to be reachable |
| One installer skill per *tool* (8 skills) | **Rejected** | Maximum duplication of the install skeleton across 8 files; the DP7 fence-pin test would have to police 8 copies instead of 3 |
| Per-agent libraries with no drift mitigation | **Rejected** | Accepts silent divergence of the *fences* — the one block that must not drift |
| `aegis-strategy` file-invoked instead of seeded | **Viable alternative** | Keeps the global bank 100% free of security content. Cost: the methodology would not be auto-loaded, so aegis's rule/workflow must carry a "load my strategy skill first" step — a weaker guarantee than `auto_load: true`. **Preferred: seeded** |
| Add `semgrep`/`gitleaks`/etc. as **new tool categories** so other agents can invoke them | **Rejected — out of scope** | A new category triggers the 3-step registration seam + `tool-categories.json` regeneration (the F-1 trap class, B3). The commission explicitly says to flag this as an architect question instead (E2). The installer-skills route achieves the same outcome with **zero daemon changes** |

---

## DP3 — Sandbox / ownership model for `hacker` active testing

### Recommendation

**One engagement-scoped sandbox root, created once, owned by `aegis`, inherited by every lane; the
"self-owned" check is a mechanical prefix assertion the agent performs before any active technique.**

**Sandbox root.** Created once per engagement with `mktemp -d` (the charter precedent,
`agents/charter/rule.md:14-18` — *"never hardcode `/tmp/charter_validate.mmd`; use `mktemp`"*):

```
SANDBOX_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/aegis-<engagement-slug>-XXXXXX")"
```

Sub-layout, fixed at creation so every lane addresses the same paths:

```
$SANDBOX_ROOT/
  build/        # compile artifacts, harnesses, instrumentation output
  corpus/       # fuzzing corpus (seed inputs + crash artifacts)
  out/          # scanner output (SARIF/JSON), bench results
  samples/      # cracker-generated synthetic corpora ONLY
  logs/         # run transcripts, tool versions
```

**Why `mktemp -d` and not a fixed path:** uniqueness per engagement (no cross-engagement collision),
POSIX-portable, and the absolute path is captured once and **carried in the dispatch prompt** — the
same *absolute-path capture* idiom the charter installer uses because non-interactive bash does not
source `~/.bashrc` (C3).

**Lifecycle.**

1. **Open** — `aegis` creates the root, records its absolute path in the engagement record, and passes
   it to every child in its dispatch prompt. The sandbox exists for the **whole engagement**, not one
   turn: evidence must survive for the re-verify pass (DP4).
2. **Use** — lanes write only inside the root. Scanner output, fuzzing corpus, bench corpora, logs.
3. **Close** — an explicit **teardown step** at engagement close (never implicit): stop tracked
   processes, then remove the root, and record what was removed. The charter's install-lock
   *"lock release MUST run on failure"* discipline (C3) is the precedent for "cleanup must run on every
   exit path".

**Self-owned process discipline.** Every process a lane starts must be (a) launched from the sandbox
root as its working directory, (b) **tracked via the `proc` tool** so it can be stopped and
status-checked, and (c) stopped during that lane's teardown — never left running past the END TURN
(guide §7, C1). The `proc` category gives exactly `proc_run` / `proc_logs` / `proc_stop`
(`daemon/tools/proc_tools.py`), and `proc_run` accepts a `workdir` and a `timeout`
(`daemon/tools/proc_tools.py:1970-1982`) — which is what a fuzzing campaign needs. (The daemon's
bash-tool orphan reaper exists as a backstop; the agent's own discipline is the primary control.)

**The "self-owned" check — mechanical, before every active technique.** The legality line must not be
a vibe. Before any active/dynamic step, the agent asserts:

1. The absolute path of the artifact under test **is a prefix-match of the engagement sandbox root**
   (`case "$target" in "$SANDBOX_ROOT"/*) || exit 1`).
2. The target process binds **loopback only** (or no socket at all).
3. No outbound network is required by the target; if it is, the run is refused.

If any assertion fails ⇒ **STOP**, report it as a finding, do not proceed. This makes the commission's
"legality line" a checkable precondition rather than a prose promise — and it is the same shape as the
charter's readiness-probe discipline (`exit 0 warm / 1 cold / 2 in-progress`, C3): *state is machine-
readable, and the unhappy state stops the lane.*

**Network posture.** Fuzz targets bind `127.0.0.1` only; a fuzz target that needs a service stub binds
loopback with a short timeout; **no outbound egress** from a fuzz target. Anything that would require
outbound egress is out of scope by construction — which is also why the CVE-lookup surface (DP1) stays
with `aegis` and is `mcp`/`curl` (read-only fetches), never a lane scanning outward.

**NO-Docker wording.** This host has **no Docker** and it must never be installed (house decision,
project metadata). ⇒ The sandbox is **plain directories + user-space processes**. The wording goes
into the persona as an absolute, and into the installer skills' FENCES block, so the ban is stated
once per artifact and enforced by the skill's own refusal path.

**`/bin/sh`-safe scripting.** The bash tool runs string commands through
`asyncio.create_subprocess_shell` (`daemon/tools/bash.py:926-934`) ⇒ `/bin/sh` semantics (D10). ⇒
**Every loop and pipeline lives in a script file**, invoked by absolute path, with POSIX-sh constructs
only (no arrays, no `[[ ]]`, no process substitution), and `set -eu` at the top. The `.lib.sh` pattern
(C3) is the in-repo precedent — BSD-portable, no hard `flock`.

### Alternatives considered

| Alternative | Verdict | Why |
|---|---|---|
| Docker per engagement | **Rejected — house decision** | No Docker on this host; installing it is out of scope and would breach the "no system changes without reporting" rule |
| A single fixed sandbox path (e.g. `~/.aegis/sandbox`) | **Rejected** | Two concurrent engagements would collide; cleanup is riskier (a fixed path invites deleting the wrong tree). `mktemp -d` + recorded path is the charter precedent |
| Sandbox inside the target repo (e.g. `~/Code/vkey/.aegis/`) | **Rejected** | Pollutes the target tree, risks committing sandbox artifacts, and defeats "read-only on target codebases" |
| Sandbox inside the ensemble repo (`.agents/shared/...`) | **Rejected** | Would be committed ⇒ pushed ⇒ sandbox artifacts and possibly crash dumps leak to origin. The engagement *record* has its own place (DP4); the *sandbox* does not |
| Let each lane create its own sandbox | **Rejected** | Splits the evidence base; the re-verify pass (DP4) must re-run against the *same* corpus and build tree to be meaningful. One root, shared |
| Rely on the bash-tool orphan reaper as the process-discipline control | **Rejected as primary** | It is a backstop after the fact, not a discipline. The lane must track its own processes via `proc` and stop them explicitly |
| The legality line as prose only | **Rejected** | A prefix-match assertion is cheap, deterministic, and testable; prose is not. The commission's own framing ("legality line") is stronger when mechanical |

---

## DP4 — Findings artifact, severity taxonomy, and the report→fix→re-verify loop

### Recommendation

**A five-level severity taxonomy with C++-relevant definitions; a fixed finding record with nine
required fields; storage OUTSIDE the ensemble repo and OUTSIDE the target repo; the remediation
hand-off is a message to `leader`; re-verify is narrow, per-finding, and severity-gated at 3 rounds.**

### Severity taxonomy (canonical home: the `aegis-strategy` skill)

| Level | Definition (C++/IME-shaped) | Examples |
|---|---|---|
| **critical** | Memory corruption reachable from untrusted input, or credential/secret exposure, or code execution | heap/stack OOB **write**, use-after-free, uninitialized-memory **use** in a reachable path, RCE, live-credential leakage, CI injection |
| **high** | Memory-safety violation without write primitive, or a concrete auth/crypto break | OOB **read**, uninitialized read of a pointer, integer overflow leading to an undersized allocation, missing bounds check on attacker-controlled length, weak password hashing (fast hash), plaintext credential storage, IPC message-parsing OOB |
| **medium** | Weakening or conditional weakness; exploitable only with a precondition | TOCTOU, predictable temp file, missing hardening flags (`-fstack-protector`, RELRO), weak-but-not-broken crypto primitive, internal path/version disclosure, unbounded allocation on untrusted input (DoS) |
| **low** | Defence-in-depth gap, no demonstrated path | missing input validation at a trust boundary, verbose error content, stale dependency with no reachable exploit, missing fuzz coverage |
| **info** | Hygiene and hardening suggestions | unreachable code, style, documentation, hardening suggestions with no security claim |

Severity is **not** CVSS and **not** exploitability-verified-only: a finding whose exploit is
*theoretical* can still be critical if the class is memory corruption on untrusted input — with
**confidence** recorded separately (below). This keeps "confirmed crash" from being the only path to
the top severity, which matters for a static SAST-heavy workflow.

### Finding record — nine required fields

```
id                 stable within the engagement (F-001, F-002, …)
title              one line, defect-shaped ("OOB read in keystroke buffer parser")
severity           critical | high | medium | low | info
confidence         high | medium | low   + basis (one clause: "reproduced", "static path", "inferred")
category           STRIDE category
location           file:line  (+ enclosing function)  ← the commission's explicit requirement
description        the attack path, in terms of the untrusted input and the violated assumption
evidence           the command run + the salient output + the artifact path inside the sandbox
impact             what an attacker gains, concretely
fix                a concrete remediation recommendation (never an applied edit)
```

Plus engagement-level state: `status ∈ {open, in-remediation, re-verify-pending, resolved,
accepted-risk, false-positive}` and `re-verify` (result + evidence + round number).

**Format discipline:** the taxonomy + record shape live **once** in `aegis-strategy` (the seeded
skill). The four prompt files *link* to it by section name; they do not restate it (guide §2 canonical
home + the "stated once" trap, C1). The per-severity **tone** framing (guide §5) lives in
`aegis/soul.md` — the tone block is the agent's voice, the taxonomy is doctrine; they are different
concerns and the guide separates them.

### Storage — where the findings live

**Recommended:** **outside both repos**, under a user-owned, non-committed root:

```
$HOME/.local/state/aegis/engagements/<engagement-id>/
  findings.json      # the machine-readable record (nine fields per finding)
  report.md          # the human report
  evidence/          # scanner output, crash artifacts, bench results
  engagement.json    # target root, scope, sandbox path, dates, toolchain versions
```

**Why not `.agents/shared/`:** `.agents/shared/` is inside the ensemble repo and is **committed**
(212 planning dirs are tracked). Security findings committed to the repo are candidates for `git push`
to origin ⇒ "private within the project" is materially weakened — the exact property the commission
asked for. Storing off-repo keeps findings at the same privacy class as the target source itself
(host-local, user-owned, no auth layer on the daemon — the same class as the target tree).

**Cost of this choice:** the engagement artifacts are not reviewable in a PR and not visible to a
teammate without filesystem access to the host. For a white-hat engagement on a fork the user owns,
that is the right default. **Mitigation:** the findings **summary** travels with the hand-off message
to `leader` (severity counts + the critical/high items in full), so the decision-maker is never blocked
by file access.

**Architect flag — Q2:** in-repo storage under `.agents/shared/security/<engagement-id>/` is the
natural alternative and would make findings diffable and reviewable. It requires (a) a `.gitignore`
change — **outside** the `agents/` + `tests/` scope — and (b) acceptance that findings land in git
history. If the architect prefers in-repo storage, the `.gitignore` edit must be authorized explicitly
and the leak-to-origin consequence accepted in writing.

**The `.md` token wrinkle:** `report.md` contains a `.md` token, and the closure grep forbids `.md`
path tokens in prompt surfaces (C1 §3). ⇒ The prompt prose must not *name* the file; it refers to "the
engagement folder" and "the findings record". The file names appear only as runtime read/write targets
(guide §3's explicit carve-out: *"runtime read/write targets (own memory/notes dirs, planning/
convention docs…)"*). The lane's closure-grep leg must **enumerate** this exemption rather than pass
it silently — exactly the charter precedent for `skills-template/install-mermaid-cli.lib.sh`.

### Report → fix → re-verify loop

**States (per finding):**

```
open ──▶ in-remediation ──▶ re-verify-pending ──▶ resolved
                                     │                  ▲
                                     ├──▶ accepted-risk │
                                     └──▶ false-positive
                                     (re-verify FAILED ⇒ back to open, original severity)
```

**Hand-offs:**

1. **aegis → leader** (the only remediation hand-off). aegis sends the severity-ranked findings
   summary. It **does not** name `developer`/`reviewer` as spawn targets — they are not in aegis's
   `team_members`, and guide §8 makes naming an out-of-team peer a violation (C1). The
   `aegis-strategy` skill states the hand-off as *"hand the report to the leader"* — the caller
   reference, exactly as the maintenancer org-chart test treats *"ask the leader"* (B8). aegis also
   does **not** use the critical-notes channel: it is leader-curated with leader-only verbs (D11).
2. **leader → developer** (leader's decision, leader's dispatch).
3. **developer → aegis (re-verify request)**: *"fix landed for F-003, F-007 — re-verify."* Reachable
   because `aegis` is in leader's `team_members` (DP1's one-liner, A4) and leader can message aegis.
4. **aegis → its own children (re-verify)**: aegis re-dispatches **sweeper** (re-scan) or **hacker**
   (re-fuzz) — the lanes that produced the original finding — with the specific finding as the scope.

**What re-verify does and does not do.**

| | Original audit | Re-verify |
|---|---|---|
| Scope | the whole engagement surface | **one finding** (plus a regression check that the fix did not break adjacent behaviour) |
| Lanes | all three, as warranted | **only the lane that produced the finding** |
| Output | the full severity-ranked report | a per-finding verdict: `resolved` / `still-vulnerable` / `inconclusive`, with evidence |
| Budget | — | **max 3 rounds per finding**, severity-gated (designer precedent, C4) |

**Round-budget severity gate (mirrors the designer precedent, C4):** at the cap,
*advisory-only* findings (medium/low/info) ⇒ **accept-with-disclosure** (the finding stays open with
the cap and the residual risk stated in the report); *any* critical/high finding ⇒ **escalate-only**
(reported to `leader` as unresolved at cap — never silently closed). Each re-verify attempt is recorded
with its evidence, so "we tried three times" is auditable rather than asserted.

**On a failed re-verify:** the finding returns to `open` at its **original** severity — re-verify never
downgrades severity on its own; severity changes require a fresh adjudication by aegis, recorded with
its basis.

### Alternatives considered

| Alternative | Verdict | Why |
|---|---|---|
| Store findings in `.agents/shared/security/<engagement>/` | **Viable — Q2** | Diffable and reviewable; but committed ⇒ pushable, and needs a `.gitignore` edit outside the lane's scope |
| Store findings in the target repo | **Rejected** | Pollutes the audited tree; risks committing findings and crash artifacts into the target's history; directly violates "read-only on target codebases" |
| aegis dispatches `developer` directly | **Rejected — guide §8 violation** | `developer` is not in aegis's `team_members`; the spawn would be refused at runtime (A1) and the prose would teach an unreachable peer (B8's failure mode) |
| Use the critical-notes channel for findings | **Rejected** | Leader-curated, leader-only pin/supersede verbs (D11) |
| CVSS-only severity | **Rejected** | CVSS needs a CVE and a vendor score; a fork's own findings have neither, and CVSS cannot express "theoretical memory corruption on untrusted input" without a vector string. The five-level scheme with a separate confidence field covers both static and reproduced findings |
| Full re-audit on every re-verify | **Rejected** | Costly, and it re-opens unrelated findings. Narrow per-finding re-verify with a regression check is both cheaper and more precise |

---

## DP5 — Peer posture

### Recommendation

**`ari` reaches `aegis` through `job_create` with zero `ari` changes; `leader` reaches `aegis` through
the single `team_members` line; all four agents declare `default_queue: system_parallel_queue`; no
`watchover` block in any meta.**

**The `ari` path — confirmed, no change required.** `ari` holds no `instance` category and no
`team_members` (`agents/ari/meta.json`); its agent-reach is the `job` category, and `job_create`
(`daemon/tools/job_queue.py:1399-1411`) takes a **free-form `agent_id`** with **no team-membership
gate** (verified: zero `_check_team_membership` / `team_members` references in `job_queue.py`, A3).
⇒ `ari` dispatches `aegis` by `agent_id="aegis"` the moment `agents/aegis/` registers. **Zero `ari`
edits, zero `ari` risk** — this path is already open for every agent in the system.

**The `leader` path — one line.** `leader` holds `instance` but **no `job` category**, so
`team_members` is its only agent-reach surface (A4). ⇒ add `"aegis"` to
`agents/leader/meta.json` `team_members`. The edit is agents-only; `meta.json` contributes **zero**
baseline rows (B1), so the 161 → 177 count is unaffected.

**Reachability matrix after the change:**

| caller | → aegis | mechanism | change needed |
|---|---|---|---|
| `ari` | ✅ | `job_create(agent_id="aegis")` — ungated (A3) | none |
| `leader` | ✅ | `spawn_instance` / `send_message` via `team_members` (A4) | **one line** in `agents/leader/meta.json` |
| `aegis` | → `hacker`/`cracker`/`sweeper`/`worker` | `spawn_instance` via `team_members` (A1) | aegis's own meta |
| `aegis` | → `charter` | implied member of the `"chart"` grant (A1) | **Q1 deviation** |
| `aegis` | → `explorer`/`kb-writer` | **blocked** by `deny_spawn: ["knowledge"]` (A1) | none |
| `aegis` | → `developer`/`reviewer` | ❌ refused — not in team_members (A1) | hand off to `leader` instead (DP4) |

**`default_queue`.** Recommend **`system_parallel_queue`** on **all four** agents. Rationale: the
family is throughput-oriented — a security engagement is embarrassingly parallel across three analysis
lanes, and FIFO would serialize them. The sketcher precedent uses exactly this value
(`agents/sketcher/meta.json`, A11). Note `worker` is a shared lane and is **not** touched (A10).

**`watchover` — recommend NO `watchover` block in any of the four metas.** The per-agent `watchover`
meta key is **inert**: `AgentMetadata` has no such field, its `model_config` is `extra="ignore"`
(so the key is silently dropped rather than validated), and **no daemon code reads one** (D7). The
sketcher block (`"watchover": {"timeout_seconds": 90}`) is copied precedent, not a working mechanism.
Copying it into the aegis metas would create a **false-confidence knob** — an operator would believe
the family has a supervision timeout when it has none.

**What actually supervises a long-running lane, given no watchover block:** the family's own
mechanisms — the END TURN contract (guide §7, C1), the fan-in escape-valve ladder with a
**max-1-re-dispatch cap** (C1 §7), the `proc` tool's explicit process tracking (DP3), and the
per-instance watchover API `POST /api/instances/{id}/watchover`
(`daemon/routers/instances.py:140-200`) if an operator wants it. **Architect flag — Q4:** confirm the
family relies on the instance-level API rather than a meta block.

### Alternatives considered

| Alternative | Verdict | Why |
|---|---|---|
| Give `ari` an `instance` category or a `team_members` entry for aegis | **Rejected — unnecessary** | The job path already reaches aegis with no gate (A3). Adding `team_members` to ari would *narrow* ari's reach and touch the highest-traffic agent in the system for no benefit |
| Have `ari` reach aegis only via `job_create` and leave `leader` out | **Rejected** | The commission explicitly requires leader to be able to consult aegis (A4), and the leader→aegis path is the one that carries the report→fix→re-verify loop (DP4) |
| Default `watchover.timeout_seconds: 300` on the children | **Rejected — inert key** | It would read as a supervision guarantee and enforce nothing (D7) |
| Leave `default_queue` unset (FIFO) | **Rejected** | Serializes the three analysis lanes; the sketcher precedent (A11) shows the family idiom |

---

## DP6 — Model assignments

### Recommendation

| agent | `llm_model` | why |
|---|---|---|
| `aegis` | **`agentic`** | the orchestrator's work is decomposition, dispatch authoring, evidence adjudication, severity ranking, and report writing — reasoning and lane work, not code emission. `agentic` is the generalist reasoning alias (A8) |
| `hacker` | **`coding`** | writes and debugs C++ fuzz harnesses, reads engine source, drives AFL++/libFuzzer campaigns — a tool-execution lane |
| `cracker` | **`coding`** | writes the synthetic-corpus generator, reasons about crypto primitives, drives hashcat/john and parses their output |
| `sweeper` | **`coding`** | runs the scanner suite, writes triage scripts, parses SARIF/JSON, correlates duplicate findings across tools |

**Why `"coding"` for all three children:** the worker's single-string `"coding"` pin (A8) is the
house precedent for a tool-execution lane, and the v0.25.1 change removed the client-side weighted
`coding2:30/coding:70` pool in favour of proxy-side load-balancing — so `"coding"` is the correct
single-string pin today, and `"coding2"` is not a meaningful distinction to reintroduce here.

**Why not `vision`:** the security family produces no image artifacts; there is nothing for a
vision-capable model to interpret. `vision` is the designer/sketcher/critic pin because those agents
*digest reference images* (A8) — a condition this family does not meet.

**Why not `img-gen` / `decision`:** `img-gen` is the image-generation purpose id (A8); `decision` is
reserved for the decision-purpose consumer family. Neither applies.

**Why not `"coding"` for `aegis`:** aegis does not write code. Its harness-writing is delegated to
`hacker` (whose entire mandate is the fuzz harness); aegis authors *dispatch prompts* and *reports*.
Pinning aegis to `coding` would spend a code-specialised lane on prose-and-adjudication work and would
collide with the family's own doctrine (*"I never run offensive tools myself"*, C4) by implying that
aegis runs the tools.

**The only available values** are `agentic, coding, coding2, vision, img-gen, decision`
(`daemon/config.py:486`, A8) — exact, case-insensitive match, validated only at the four spawn-time
flows. All four recommendations are inside the shipped default, so no config change is needed and no
`llm.allowed_models` re-pin is owed.

### Alternatives considered

| Alternative | Verdict | Why |
|---|---|---|
| `aegis = "coding"` | **Rejected** | No code is written by aegis; it wastes a specialised lane on adjudication and contradicts the sole-lane doctrine |
| `aegis = "agentic"`, children = `"agentic"` | **Rejected** | The children are tool-execution lanes that write harnesses, corpus generators, and triage scripts — the worker precedent is `"coding"` (A8) |
| `sweeper = "coding2"` | **Rejected** | `"coding2"` is not the current single-string pin idiom (v0.25.1 removed the client-side pool); using it would reintroduce a distinction the house deliberately collapsed |
| `hacker = "vision"` | **Rejected** | No image work in the family; `vision` is the design lane's pin (A8) |

---

## DP7 — Test and pin fold enumeration

### Recommendation

**One new test file; one commit that lands the four agent trees, the leader one-liner, the new
baseline (161 → 177), and the new test file together; the baseline re-stamp must be the LAST content
change under `agents/`.**

### The pin ceremony — exactly one commit

```
agents/aegis/{meta.json, soul.md, rule.md, workflow.md, tools_note.md, skill-set.yaml,
              skills-template/aegis-strategy.md}                                  (+7)
agents/hacker/{meta.json, soul.md, rule.md, workflow.md, tools_note.md,
               skills-template/fuzzing-toolchain.md, .../fuzzing-toolchain.lib.sh}  (+7)
agents/cracker/{... skills-template/credential-bench.md, .../credential-bench.lib.sh} (+7)
agents/sweeper/{... skills-template/sast-toolchain.md, .../sast-toolchain.lib.sh}      (+7)
agents/leader/meta.json                                            (MODIFIED — one line)
agents/.prompt-artifacts.sha256                                    (REGENERATED)
```

- **Baseline: 161 → 177** — 4 agents × 4 depth-2 prompt artifacts (`soul/rule/workflow/tools_note`).
  `meta.json`, `skill-set.yaml`, and `skills-template/*` contribute **zero** rows (B1/B10), and the
  `leader` edit touches `meta.json`, which is **not** in the artifact set ⇒ it contributes zero rows
  too. The count is therefore exactly **177**, with no ambiguity.
- **Regenerate with the venv-free command** — `python3 scripts/protection/generate_prompt_artifact_baseline.py`
  (bare host python3; the generator loads `prompt_artifacts.py` by file path precisely so it works
  without the langchain stack, B2). Verify with `--check` ⇒ exit 0.
- **Ordering constraint:** the baseline pins content hashes, so the re-stamp must follow the **final**
  edit to any `agents/**` prompt file. If any prompt file changes after the re-stamp, the baseline
  drifts and the stage-time leg goes red. ⇒ The re-stamp is the **last** content change in the lane.

### New lane test file

**`tests/unit/agents/test_aegis_security_family.py`** — modelled on
`tests/unit/agents/test_sketcher_agent.py` (B8) and
`tests/unit/test_maintenancer_team_members_within_team.py` (B8). **Pure file parsing wherever
possible** — no daemon import, no DB, no LLM — so the file runs on bare `python3` (D9). The single
exception is the registry-scan leg, which needs the venv interpreter.

| # | Test | What it pins | Gate |
|---|---|---|---|
| T1 | `test_aegis_allow_set_exact` | aegis's `tools.allow` equals the exact expected frozenset | allow-list pin |
| T2 | `test_aegis_deny_set_exact` | aegis's `tools.deny` equals the exact expected frozenset — `write_file`/`edit_file` **present** | allow-list pin |
| T3 | `test_aegis_deny_spawn_exact` | `deny_spawn == {"knowledge"}` | implied-member trim |
| T4 | `test_aegis_team_members_exact` | `{hacker, cracker, sweeper, worker}` | roster |
| T5 | `test_children_team_members_empty` | each child's `team_members == []` | leaf (A2) |
| T6 | `test_children_allow_non_empty` | each child's `tools.allow` is **non-empty** | **the E3 fold** (B3) |
| T7 | `test_children_deny_write_fence` | each child's deny contains `write_file` **and** `edit_file` | read-only fence (A6) |
| T8 | `test_no_privileged_category_in_any_allow` | intersection of every agent's allow with the **17** privileged categories (D1) is empty | privileged fence |
| T9 | `test_no_job_category_anywhere` | no aegis-family agent holds `"job"` | the ungated dispatch path (A3) |
| T10 | `test_no_instance_read_or_ops` | no agent requests `instance_read` / `instance_ops` | the B6 no-touch condition |
| T11 | `test_no_mcp_full_access` | no aegis-family meta sets `mcp_full_access` | **the E4 fold** (B3) |
| T12 | `test_aegis_carries_report_scrutiny_guidance` | aegis's surface contains `"[REPORT SANITY:"`, `"interim, not completion"`, `"send_message"` | **the B4 fold** |
| T13 | `test_children_exempt_from_scrutiny_guidance` | children (empty teams) carry neither — mirrors sketcher's Section-7 exemption test (B8) | B4 exemption |
| T14 | `test_no_system_internals_in_prose` ×4 | the §10 forbidden-token list over each agent's prompt surface | guide §1/§10 |
| T15 | `test_no_md_path_tokens_in_prose` ×4 | zero `.md` hits, **with the operational-path exemptions enumerated** (the `.lib.sh` read targets, the engagement-folder paths) | guide §3 closure |
| T16 | `test_rule_md_at_most_seven_cardinals` ×4 | 1 ≤ cardinals ≤ 7 | guide §3 |
| T17 | `test_tone_block_in_soul` ×4 | the voice-to-caller + dispatch-voice + per-severity-framing block is present | guide §5 |
| T18 | `test_no_adapted_from_provenance` ×4 | no "adapted from" / "migrated from" / "verified from" | guide §10 |
| T19 | `test_aegis_skills_name_no_out_of_team_fallback` | aegis's `skills-template/*.md` name **no** out-of-team peer as a spawn fallback; *"hand the report to the leader"* is an allowed caller reference | **guide §8 org-chart rule** (B8) |
| T20 | `test_leader_team_includes_aegis` | `"aegis" ∈ leader.meta.team_members` | the A4 one-liner |
| T21 | `test_seeded_skill_is_methodology_only` | the **only** `skill-set.yaml` entry in the family is `aegis-strategy`; the three installer skills appear in **no** manifest | file-invoked mode (C2/C6) |
| T22 | `test_skill_set_entries_have_templates_and_match_versions` | each seeded skill has a template file and matching frontmatter/manifest versions | guide §6 |
| T23 | `test_installer_fences_block_identical` | the three `.lib.sh` files carry an **identical** FENCES block | **the drift mitigation for DP2** |
| T24 | `test_severity_taxonomy_declared` | `aegis-strategy` declares the 5 levels + the 9 finding fields | DP4 canonical home |
| T25 | `test_worker_untouched` | `agents/worker/**` is byte-identical to the base commit | **mechanical proof of "ZERO changes to worker"** (A10) |
| T26 | `test_default_queue_parallel` ×4 | all four declare `system_parallel_queue` | DP5 |
| T27 | `test_model_assignments` | aegis = `agentic`; children = `coding` | DP6 |
| T28 | `test_registry_scan_aegis_family` | subprocess probe asserting `get_registry().exists()` for all four ids — **venv-bound** (D9) | boot-scan leg |

### Existing tests this lane must not break — and the leg that proves it

| existing test | risk | fold / proof |
|---|---|---|
| `tests/unit/tools/test_plane_agent_surface_census.py` (B3) | a new agent with an empty/absent allow resolves plane reads ⇒ **E3 RED** | **T6** is the fold. `DISCLOSED_READ_HOLDERS` stays `{"watcher"}` |
| `tests/unit/test_report_integrity_prompts.py` (B4) | aegis has a non-empty team ⇒ needs the three snippets | **T12** is the fold |
| `tests/test_chart_image_delivery_e2e.py::TestPhaseDDoesNotModifySealedArtifacts` (B5) | the 35 sealed entries — none reference aegis paths, so adding dirs cannot redden | **run it on the branch as the UNTOUCHED-PROOF leg** (commission requirement) |
| `tests/unit/test_instance_ops_registration.py` (B6) | adds nothing | **T10** is the fold |
| `tests/unit/tools/test_tool_category_artifact.py` + `.agents/shared/metrics/tool-categories.json` (B6) | the **F-1 trap class** — a stale artifact reddens a whole lane | no new `register_tool_category` ⇒ untouched. **Proof leg: the artifact is byte-unchanged in the lane diff** |
| `tests/unit/agents/test_sketcher_agent.py::test_agent_registry_scan` (B6/D9) | probes fixed names | **T28** is the aegis analogue |
| `tests/unit/test_maintenancer_team_members_within_team.py` (B8) | pure file parsing | **T19** mirrors it for aegis |
| agents-tree manifest count (B6/D6) | the staged-gate delta note | **338 → 338 + 28 = 366** (4 agents × 7 files; the `leader` edit modifies an existing file) |

**The manifest delta arithmetic:** 4 agents × (1 `meta.json` + 4 prompt `.md` + 1 `skill-set.yaml` +
2 `skills-template` files for aegis / 1 + 2 for the children) — precisely:
aegis 7 files (`meta.json`, `soul`, `rule`, `workflow`, `tools_note`, `skill-set.yaml`,
`skills-template/aegis-strategy.md`), hacker 7, cracker 7, sweeper 7 ⇒ **+28 new files**, and the
`leader/meta.json` edit modifies an existing file ⇒ **agents tree 338 → 366**. *The plan worker must
re-derive this from the actual file list at implementation time* — if the aegis strategy skill gains a
companion file, or a child gains a `memory.md`, the number moves. Note a `memory.md` would add a
**baseline row** too (+1 per agent) — so the 161 → 177 figure is exact **only** under the four-file
prompt set.

### Alternatives considered

| Alternative | Verdict | Why |
|---|---|---|
| Re-stamp the baseline in a separate commit from the agent files | **Rejected** | House doctrine: agent files + the regenerated baseline ship in **ONE commit** (B2). A split leaves a window where the baseline is stale against the tree |
| Add the aegis family to `DISCLOSED_READ_HOLDERS` | **Rejected** | That would accept plane-read exposure for the new agents. The E3 fold (T6) removes the exposure instead of whitelisting it |
| Grandfather aegis in `GRANDFATHERED_PARENTS` | **Rejected** | The grandfather list exists for pre-Wave-1 agents (B4); a new agent must carry the guidance |
| Rely on the stage-time `PROMPT-BASELINE: OK` leg alone | **Rejected** | It is a stage-time, non-gating check; the lane needs a **committed** baseline plus a pytest-level pin on the count so drift is caught pre-stage |
| Skip T25 (`test_worker_untouched`) | **Rejected** | "ZERO changes to worker" is a commission constraint; a mechanical byte-level proof is cheap and prevents the most likely accidental violation |

---

## DP8 — Phase structure and gate mapping

### Recommendation

Six phases, with the standard lane flow (**plan → councils → implement → verify → STAGE**; **never
promote**). Every phase ends with a gate that is independently checkable.

| Phase | Content | Gate (independently checkable) |
|---|---|---|
| **P0 — Exemplar read + compliance checklist** | Read `agents/designer/{rule,workflow}.md`, `agents/sketcher/`, `agents/critic/`, `agents/charter/skills-template/install-mermaid-cli.*`, `docs/agent-prompt-writing-guide.md`; produce a checklist binding every guide §1/§2/§3/§4/§5/§6/§7/§8/§10 rule to the four agents | The checklist exists and every rule has a named target artifact; the architect signs it off before P1 |
| **P1 — `aegis` agent** | `meta.json` (the DP1 matrix), `soul.md` (identity + tone block + per-severity framing), `rule.md` (≤7 cardinals: white-hat ethos, read-only, legality line, no-self-offensive-tools, sole-lane, report-scrutiny, END TURN), `workflow.md` (dispatch doctrine, fan-in ladder, END TURN, report→fix→re-verify), `tools_note.md` (allow table), `skill-set.yaml` + `skills-template/aegis-strategy.md` (the seeded methodology — canonical home for taxonomy + report format) | The guide-compliance checklist is satisfied **for aegis alone**; T1–T4, T12, T14–T18, T24, T26, T27 pass; the strategy skill's frontmatter version matches the manifest |
| **P2 — the three children** | `hacker` (attack-surface mapping, fuzz-harness construction, self-owned sandbox discipline), `cracker` (crypto/credential/protocol audit, synthetic-corpus discipline, the hard "never real data" rule), `sweeper` (scanner suite + triage). Each: `meta.json` + 4 prompt files + `skills-template/<family>.md` | T5–T7, T13–T18, T26, T27 pass for all three; each child's prompt carries its **own** legality language — the commission's "white-hat ethos explicit in **every** persona" |
| **P3 — the three installer libraries** | `fuzzing-toolchain.lib.sh`, `credential-bench.lib.sh`, `sast-toolchain.lib.sh`: the charter skeleton (fences → pre-flight → user-space install → **sha256-verify the release asset** → staged config → readiness probe → release lock → reversal) | T23 (identical FENCES blocks) passes; each library exposes a readiness probe with **exit 0 warm / 1 cold / 2 in-progress** and a mandatory reversal section; the checksum step is present in all three (the C3 gap, closed) |
| **P4 — pin ceremony + tests + the leader one-liner** | The **one commit**: 4 agent trees + `agents/leader/meta.json` one line + the regenerated baseline (161 → 177) + the new test file | `python3 scripts/protection/generate_prompt_artifact_baseline.py --check` ⇒ **exit 0**; the file has **exactly 177 rows**; `git show --stat` proves the commit contains the agent files, the leader edit, the baseline, and the test file together; the **baseline re-stamp is the last `agents/**` content change** in the lane |
| **P5 — verification** | Full suite (net-new reds = 0 vs the base), the sealed leg, the census leg, the report-integrity leg, the boot-scan leg | **Gate A** the full suite: net-new deterministic reds **0**; **Gate B** `TestPhaseDDoesNotModifySealedArtifacts` green (the 35 sealed entries untouched); **Gate C** the plane census green with `DISCLOSED_READ_HOLDERS` unchanged at `{"watcher"}`; **Gate D** the report-integrity dynamic test green (aegis's scrutiny guidance found); **Gate E** the registry scan finds all four agents; **Gate F** the tool-categories artifact is byte-unchanged (the F-1 proof); **Gate G** the agents-tree manifest delta re-derived from the actual tree |
| **P6 — staging handoff** | giter derives the next free version **W9-style**, stages the release; **migration delta expected EMPTY** (no `daemon/` change); full provenance sidecars; **NO promote** | The staged artifact verifies (4-way sha, sidecars clean, prompt baseline clean at 161/177-equivalent in the payload); the promote command is **recorded for the user's nonce ceremony and NOT executed**; the live daemon is untouched throughout |

### Commission-expectation → gate map

| Commission verification expectation | proving gate |
|---|---|
| baseline / pin green | **P4** (`--check` exit 0, exactly 177 rows) + **Gate A** |
| boot-scan clean on the new agent set | **P5 Gate E** (registry scan) + the tester's isolated boot-scan leg |
| tool-category allow-list pins | **P5 Gate A** via **T1, T2, T3, T6, T7, T8, T9, T10, T11** |
| staged release full provenance | **P6** (sidecars, 4-way sha, EMPTY migration delta) |
| `SEALED_SHA_BASELINES` untouched + proof leg | **P5 Gate B** |
| no `daemon/` changes | **P6** (EMPTY migration delta) + the lane diff contains only `agents/**` and `tests/**` |
| worker unchanged | **P5 Gate A** via **T25** |
| standard lane, STAGE not promote | **P6** (the promote command is recorded, not run) |

### Alternatives considered

| Alternative | Verdict | Why |
|---|---|---|
| Merge P2 (children) into P1 (aegis) | **Rejected** | The children's prompts are the highest-risk surface (offensive language, sandbox discipline, the "never real data" rule). They deserve their own gate and their own council pass. Merging them into the orchestrator phase would let aegis's prompt settle before the children's legality language is reviewed |
| Defer the installer libraries (P3) past P4 | **Rejected** | The skill files are prompt surface; a library written after the baseline re-stamp would either drift the baseline or force a second re-stamp, breaking the one-commit rule |
| A single combined verification phase (P5) with sub-gates inside | **Viable, and effectively what P5 is** | Listed as sub-gates A–G so each is independently checkable and separately attributable |
| Skip the STAGE step | **Rejected** | The commission's standard lane ends in a staged release with full provenance; the live flip belongs to the user's nonce ceremony |

---

## Trade-offs (cross-cutting)

| # | Trade-off | Choice made | What is given up |
|---|---|---|---|
| T1 | Structural fence (deny) vs doctrinal fence (prose) | **Both** — deny removes the easy path; prose holds the hard path (A6) | The write-deny is *not* a boundary; `bash` remains unconstrained. The plan must say so plainly rather than implying containment |
| T2 | Narrow allow vs usable allow | **Narrow but complete** — 17 categories for aegis, 6 for each child | `aegis` cannot read the DB, the env, or the service registry — deliberately |
| T3 | Implied-member convenience vs roster fidelity | **Trim `knowledge`; accept `chart`** | The roster is `[hacker, cracker, sweeper, worker]` **plus** an implied `charter` (Q1) |
| T4 | Bank-seeded vs file-invoked skills | **Split** — methodology seeded, installers file-invoked | The install skeleton exists in three copies; T23 mitigates the drift |
| T5 | In-repo vs off-repo findings | **Off-repo** | Findings are not PR-reviewable; the summary must travel with the hand-off (Q2) |
| T6 | Broad capability vs bounded capability for children | **Bounded** — children get 6 categories and no `mcp` | Children cannot look up CVEs themselves; aegis supplies context in the dispatch prompt |
| T7 | Full re-audit vs narrow re-verify | **Narrow**, severity-gated at 3 rounds | A fix that regresses an *unrelated* area may not be caught by the narrow pass — mitigated by the regression check inside each re-verify |
| T8 | Fork-and-fidelity vs drift-cost for the install libraries | **Fork, with a pinning test** | Three copies of the skeleton exist; the FENCES block is pinned identical (T23), the tool-specific parts are expected to diverge |
| T9 | Model specialisation vs lane fit | **`agentic` for aegis, `coding` for the children** | Aegis loses the code-specialised lane for harness work — which it delegates to `hacker` by design |

---

## Scalability

### Growth assumptions (for this family)

- **Engagements per project:** 1 active at a time per project; the sandbox model (DP3) assumes **one
  sandbox root per engagement**, so concurrent engagements in the same project are supported (distinct
  `mktemp` roots) but concurrent engagements **against the same target** are not modelled — the
  finding namespace (`F-001`) is engagement-scoped, so two concurrent engagements on one target would
  produce colliding ids.
- **Findings per engagement:** vkey is a C++17 engine with 5 platform hosts; a realistic SAST sweep
  yields **hundreds** of raw scanner hits and, after triage, **tens** of findings. The record is a
  single JSON file — no pagination needed at this scale.
- **Tool invocation volume:** `sweeper`'s scanner suite over a C++ tree is **minutes to tens of
  minutes**; `hacker`'s fuzzing campaign is **minutes to hours**; `cracker`'s bench is **seconds to
  minutes**. ⇒ This is the one place where the **no-watchover-block** decision (DP5/D7) has an
  operational cost: a long campaign is supervised by the family's own END TURN + `proc` tracking +
  the per-instance watchover API, not by an agent-meta timeout.

### Current bottlenecks

| # | Bottleneck | Threshold | Where | Impact |
|---|---|---|---|---|
| 1 | `explore()` spawns an `explorer` instance **per call** (`daemon/tools/knowledge_tools.py:754`) — not gated by team membership | every `knowledge` call | D3/`knowledge_tools.py` | A KB-heavy engagement spawns instances the aegis family cannot address. Mitigation: use `knowledge` sparingly |
| 2 | `/bin/sh` string semantics force loops into script files | every multi-step shell pipeline | `daemon/tools/bash.py:926-934` (D10) | Slower to write, but deterministic and portable — the charter pattern |
| 3 | The `mcp` grant sweeps **every** live MCP server | any deployment with ≥3 servers registered | `daemon/tools/instance.py:467-495` (A7/D8) | Broad tool surface on aegis. Bounded: `plane_*` excluded by the ownership guard (D12) |
| 4 | The write fence does not cover `bash` | always | A6 | The family's read-only guarantee is doctrinal, not structural — the single most important disclosure in the plan |
| 5 | `SEALED_SHA_BASELINES` covers 35 files but **not** the new agents' files | every future edit to aegis's prompt files | B5 | New prompt files are **unsealed** — they are covered by the *baseline* (161→177) but not by the seal. Worth stating: the seal tripwire will not catch drift in the aegis family |

### Scaling characteristics

- **Vertical vs horizontal:** the family is horizontal by design — three analysis lanes run in
  parallel under `system_parallel_queue` (DP5), each independently sandboxed.
- **Stateless vs stateful:** the *lanes* are stateless (a leaf receives a dispatch prompt and returns
  a report). The *engagement* is stateful and lives in the engagement folder (DP4) — which is exactly
  why it must survive across turns and across the re-verify loop.
- **Sync vs async:** the family is async — dispatch, END TURN, report arrives (guide §7, C1). The
  fan-in ladder bounds the "no report ever arrives" failure.

---

## Technical Debt

### Items affecting this design

| # | Debt item | Impact | Severity | Where |
|---|---|---|---|---|
| 1 | **The `bash` write path is unconstrained** | the family's read-only guarantee is doctrinal; the structural deny is a speed bump | **High** | `daemon/tools/bash.py` / A6 |
| 2 | **No read-only filesystem sub-category** | every read-only agent must deny two tools out of a six-tool category; no positive "read-only" grant exists | Medium | `daemon/tools/filesystem.py:10` / A6 |
| 3 | **`innate_skills` silently widen the allow list** | an allow-pin test over the raw meta is not the effective surface; the family must keep innate-mapped categories explicitly listed to stay exact | Medium | `daemon/tools/instance.py:190-235` / D3 |
| 4 | **`tools` parse failure ⇒ all tools allowed** | a malformed `tools` block on a security agent is a privilege escalation, not a lint error | **High** | `daemon/registry.py:656-662` / A9 |
| 5 | **The `watchover` meta key is inert** | copied precedent would create a false-confidence supervision guarantee | Medium | `daemon/registry.py` `AgentMetadata` / D7 |
| 6 | **`explore`/`experience` spawn outside the team gate** | a `deny_spawn` trim cannot close the facade spawn | Medium | `daemon/tools/knowledge_tools.py:386,754` |
| 7 | **No web-search tool category** | CVE lookup is `mcp` (deployment-conditional) + `curl` | Medium | A7/D2 |
| 8 | **The prompt-artifact seal does not cover new agents** | the seal tripwire is blind to aegis-family drift; only the baseline covers it | Low | B5 |
| 9 | **The `mcp` sweep is deployment-dependent** | aegis's CVE capability varies by deployment; the prompt must treat it as conditional | Low | D8 |

### Items NOT affecting this design

- The `20260714_000001_widen_job_queue_type_constraint.sql` fresh-SQLite boot breaker (blueprint) — no
  daemon change in this lane.
- The `tool-categories.json` F-1 trap class — **avoided** by not adding a category (DP2/E2).
- The MCP-gateway Plane bearer plaintext defect (project critical note) — no Plane surface in this
  family; `plane_*` is excluded from the `mcp` sweep (D12).
- The `env-poison` family (ambient `POSTGRES_*` inherited by a boot) — no daemon boot in this lane;
  relevant only to the tester's staged-verification leg, which follows the house scrub discipline.

### Recommended paydown (only what affects this design)

1. **Before P2 ships:** decide and document whether the family may write to the target tree via
   `bash` at all. The recommendation is a hard persona prohibition + the mechanical sandbox-ownership
   check (DP3) — but the plan must state the limitation honestly rather than implying containment.
2. **Before P4 ships:** validate every `tools` block parses (a malformed block is fail-open, A9) —
   add a lane assertion that each meta's `tools` block round-trips through the registry's parser.
3. **At P4:** keep the innate-mapped categories explicitly listed in `tools.allow` so the effective
   surface equals the pinned allow exactly (D3) — this is why T1 pins the allow *and* the innate set.

---

## Open Questions (architect)

**Q1 — the `chart` grant and the widened roster (DP1/DP5).** The commission assigns "report charts"
to `worker`, but worker has no `chart` category and worker must not change (D4). The recommendation is
that **aegis** holds `"chart"`, which gives it the chart tool and — because the spawn gate reads the
raw allow (A1) — the **implied `charter` spawn right**. This makes aegis's effective team
`[hacker, cracker, sweeper, worker] + charter`, i.e. **wider than the commission's stated roster**.
*Decide:* (a) accept the deviation (recommended — the chart artifact exists and the risk is nil), or
(b) drop charts from v1 (zero deviation, scope reduction), or (c) authorise a worker change (explicitly
out of scope). *If (a):* record the implied `charter` in the plan's roster section so the next reader
is not surprised.

**Q2 — findings storage (DP4).** The recommendation is **off-repo** storage under
`$HOME/.local/state/aegis/engagements/<engagement-id>/`, because in-repo storage under
`.agents/shared/` is **committed** and therefore pushable, weakening "private within the project".
*Decide:* (a) off-repo (recommended — zero scope impact), or (b) in-repo, which requires authorising a
`.gitignore` change (**outside** the `agents/` + `tests/` scope) and accepting git-history exposure.

**Q3 — the CVE-lookup surface (DP1).** There is **no web-search tool category**; the recommendation is
`mcp` (the builtin `webfetch` server, deployment-conditional) plus a `bash curl` fallback on `aegis`
only, with CVE context passed to the children in their dispatch prompts. *Decide:* (a) accept for v1
(recommended), or (b) commission a new category — which would trigger the 3-step registration seam +
`tool-categories.json` regeneration (**the F-1 trap class**, B3) and must be scoped as its own lane.

**Q4 — supervision without a `watchover` meta block (DP5).** The per-agent `watchover` key is inert
(D7) and the recommendation is to omit it from all four metas, relying on the family's END TURN +
fan-in ladder + `proc` process tracking, with the per-instance `POST /api/instances/{id}/watchover`
API as the operator lever. *Decide:* (a) accept (recommended), or (b) require an agent-meta supervision
knob — which is a **daemon change** and therefore out of this lane's scope.

---

## References

**In-repo (all read at `1f6898ae2889ff002b4656486f6be3ee1048b649`)**

- `docs/agent-prompt-writing-guide.md` — binding prompt conventions (§1 forbidden internals, §2 canonical home, §3 ≤7 cardinals + the path-token closure, §4 the machine-enforced deny, §5 tone, §6 skills, §7 END TURN + escape valve + report scrutiny, §8 the org-chart rule, §10 the pre-commit checklist) (C1)
- `daemon/tools/_auth.py:35-260` — `TOOL_REQUIRED_AGENTS`, `_check_team_membership`, the implied-member merge and the deny/deny_spawn strip (A1)
- `daemon/tools/instance.py:181-235, 460-495, 540-581, 7279` — innate expansion, the `mcp` sweep + ownership guard, allow/deny resolution, the instance-construction call site (A5, A7, D3, D12, D13)
- `daemon/tools/_tool_registry.py:367+` — the 17 privileged categories (D1)
- `daemon/registry.py:74-118, 258+, 609-789` — `ToolFilter` (allow/deny/deny_spawn), `AgentMetadata` (`extra="ignore"`), registry discovery + the fail-open tools parse (A5, A9, D7)
- `daemon/tools/job_queue.py:1399-1411` — `job_create` with a free-form `agent_id` and no team gate (A3)
- `daemon/tools/filesystem.py:10, 588-1508` — the single six-tool `filesystem` category (A6)
- `daemon/tools/bash.py:917-934` — string commands through `create_subprocess_shell` ⇒ `/bin/sh` (D10)
- `daemon/tools/proc_tools.py:1970-1982` — `proc_run(command, workdir, timeout)` (DP3)
- `daemon/tools/knowledge_tools.py:386, 754` — `experience()`→kb-writer, `explore()`→explorer, both outside the team gate (DP1)
- `daemon/tools/critical_notes.py:21-34, 402-946` — leader-curated; not a hand-off channel (D11)
- `daemon/tools/system.py:355-502` / `db_tools.py:272+` — the non-privileged `system` (`nomask`) and `db` categories (D2)
- `daemon/routers/instances.py:140-200` — the per-instance watchover API (D7, DP5)
- `daemon/tools/prompt_artifacts.py:119-140, 355-377` — the versioned artifact set and the depth-2 collector (B1)
- `scripts/protection/generate_prompt_artifact_baseline.py:19-59, 105-160` — the venv-free generator/verifier (B2)
- `daemon/services/skill_seed_service.py:325-426` — the global bank, the version guard, and the `requires:` ⇒ `capability_check(...)` rule (C2)
- `daemon/services/live_views.py:1357-1406` — the `SHA256SUMS`/SRI checksum precedent (C3)
- `agents/designer/{meta.json,rule.md,workflow.md}`, `agents/sketcher/meta.json`, `agents/critic/meta.json`, `agents/worker/meta.json`, `agents/leader/meta.json`, `agents/ari/meta.json` — the exemplars and the one-line target (A4, A11, C4, C6)
- `agents/charter/skills-template/install-mermaid-cli.{md,lib.sh}` + `agents/charter/{rule.md,workflow.md}` — the only in-repo installer template and the file-invoked mode precedent (C2, C3)
- `agents/architect/skills-template/security-design.md`, `agents/reviewer/skills-template/security-review.md` — adjacent surfaces to cross-reference, not duplicate (C5)
- `agents/.prompt-artifacts.sha256` — 161 rows today (B1)
- `tests/unit/tools/test_plane_agent_surface_census.py:15-21, 62-67` — E3/E4 (B3)
- `tests/unit/test_report_integrity_prompts.py:110-125, 160-162, 232-266` — the dynamic scrutiny check (B4)
- `tests/test_chart_image_delivery_e2e.py:1425-1505, 1508` — `SEALED_SHA_BASELINES` (35) and `TestPhaseDDoesNotModifySealedArtifacts` (B5)
- `tests/unit/agents/test_sketcher_agent.py:129-200, 235-249, 610-630` — the pattern to mirror (B8)
- `tests/unit/test_maintenancer_team_members_within_team.py` — the org-chart fallback test (B8)
- `tests/unit/test_instance_ops_registration.py`, `tests/unit/tools/test_tool_category_artifact.py` — the no-touch confirmations (B6)

**Companion record:** [`research-findings.md`](./research-findings.md) — the consolidated evidence digest
(A1–A11, B1–B8, C1–C6, D1–D14) with full citations.

**House constraints (from the commission and project metadata):** worktree fence
(`/home/nea/ensemble-src-wt-aegis-security-family` only) · `agents/` + `tests/` only · one-commit pin
ceremony · `SEALED_SHA_BASELINES` untouched · v2 deferrals excluded (`forensic`, defender/patcher) ·
NO Docker · NO system packages without reporting · standard lane ending in **STAGE**, never promote ·
first real case = **vkey** (`~/Code/vkey`, C++17 engine + 5 platform hosts; C++ memory safety, IME
keystroke processing, IPC, CI supply chain).

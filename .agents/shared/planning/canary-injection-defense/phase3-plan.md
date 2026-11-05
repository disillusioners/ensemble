# Phase 3: The `canary` Agent + the Aegis Roster and Workflow Edits

## Objective

Author the **`canary` agent** — the adversarial-AI red team that attacks *our own* intake surfaces —
and the two **`aegis`** edits that adopt it.

1. The **canary persona set** — `meta.json` + `soul.md` + `rule.md` + `workflow.md` + `tools_note.md`
   (**5 files**; no `memory.md`/`growth.md`/`tools.md`/`skill.md` — each would move the baseline row
   count), plus `skill-set.yaml` + `skills-template/injection-screen.md` (the **canonical** skill copy,
   authored in P1).
2. The **`aegis` roster one-liner** — `canary` joins `aegis`'s `team_members`.
3. The **`aegis` when-to-engage section** — one appended section in `aegis`'s `workflow.md` telling
   the orchestrator **when** to bring canary in.

**Design posture (from the commission):** canary is **leaf** and **design-lean**; it **composes**
payloads and **hands them to aegis**, and **aegis** spawns the sandboxed targets — preserving the
single-spawner principle. Canary is **report-only**: it never modifies a tested agent.

---

## Entry Criteria

- **Phase 0 exited** (checklist, shape table, D-table, merge-overlap premise).
- **Phase 1's canonical skill body exists** at `agents/canary/skills-template/injection-screen.md`
  (canary owns it) — P3 references it by name and does not fork it.
- **Phase 2's canary doctrine text is approved** (the two rules + the carve-out) — it is applied
  **verbatim** to canary's `rule.md`.
- The **intake-surface list** (P0 task 5) is confirmed first-hand.

**Architect dependency — D1 (spawn topology), D3 (the canary carve-out), D4 (rule-set home), D6
(intake inventory + sandbox definition).**
**Default assumption (D1):** **leaf** — `canary.team_members` is empty/absent; canary composes
payloads and hands them to aegis; **aegis** spawns the sandboxed targets. Under this default the
report-scrutiny guidance is **not** required of canary (the empty-team skip,
`tests/unit/test_report_integrity_prompts.py:242`).
**Default assumption (D6):** the 12-surface map below; **sandbox** = a self-owned instance in the
**dev daemon** (`:8079` / `ensemble_dev`) spawned by `aegis` under its own `parent_id`, torn down
after the campaign; **stated plainly**: there is **no OS/container boundary** — an instance is a DB
row + a LangGraph session in the daemon process, `bash` is unconfined, and `read_file` takes absolute
paths with no boundary check. The guards are **logical only**.
If D1 grants a scoped spawn right (sandbox-project-only + pinned), the meta's team/tools shape and
the report-scrutiny requirement change — both are **single-file** folds (tasks 2 and 9).

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | **Author `agents/canary/meta.json`** — design-lean, per **D1's Option A**: canary holds **zero instance tools** (`team_members: []`) — it **composes** and **hands off**; `aegis` spawns the targets. The `tools.allow` set is **explicitly non-empty** (an empty/absent allow inherits the default universe ⇒ a privilege-escalation shape, the predecessor's R2/R3 trap) and is drawn from the leaf set **⊆ {bash, proc, filesystem, time, help, self}** plus whatever the doctrine needs; `deny ⊇ {write_file, edit_file}` (canary writes only into its own campaign store, and `bash` remains unconfined — the honest limit). It holds **not** the privileged ones: **no** `mcp_full_access`, **no** `job`, **no** `instance_read`/`instance_ops`, **no** `chart` (the ruling that dropped `chart` from this family — it grants the charter spawn right + a transitive write-capable child), **no** plane. `deny_spawn` mirrors the family's `["knowledge"]` default (keeps the KB lookup, drops the implied `explorer`/`kb-writer` spawn right). The `tools` block **round-trips through the registry's parser** (a parse failure sets the filter to "all tools allowed" — a privilege escalation, not a lint error). | P0, P1 | The meta is valid JSON with a non-empty `tools.allow` **inside the leaf set**, zero instance tools, `deny ⊇ {write_file, edit_file}`, no privileged category, a `deny_spawn` entry, and the registry parser round-trips it (the fail-open guard is asserted). |
| 2 | **Author `agents/canary/soul.md`** — the adversarial-AI red-team persona: it red-teams **our own agents and intake channels**, it is **white-hat by construction** (its targets are self-owned sandboxed instances — never a live or busy instance, never an external system, never real user data or real channels), it is **report-only** (it never modifies a tested agent), its findings are **private** (team-internal), and it **owns and evolves the injection-screen rule set** from what it learns. The tone block the guide requires is present. | P0 | The soul states the five posture properties in the persona's own voice; the guide's tone-block row passes. |
| 3 | **Author `agents/canary/rule.md`** — the canonical home of canary's rules. It carries the **five guardrail families in canary's own form** (read-only-on-targets · self-owned-sandbox/zero-external-targets · synthetic-sample-data-only · findings-private · white-hat-ethos) — the family-3 "never transcribe real credential material" form applies to canary too: a finding references **location + type**, never content — **plus** the P2 doctrine text applied verbatim (untrusted-content rule + action-distance rule) **plus** the **canary carve-out / self-hypnosis defense**: canary handles payloads **only as fenced data with provenance**; it reads payload files **through the screen** first; it **never executes a payload — not even one it authored**; it **never pastes payload text unfenced** into its own reasoning, reports or logs; and it **re-fences** any text it quotes back out of a target's transcript (`requirements.md` CAG-12) — **plus** the **self-fencing discipline** (canary screens and fences the hostile material it ingests, including its own corpus). ≤7 cardinals; the families and the carve-out are expressed so that **each anchor is single-site** (the T29 self-proof). | P2 | The file holds **exactly four prompt artifacts**' worth of content (no legacy artifact names); the cardinal count is ≤7; every T29 canary-family anchor and each of canary's **2 carve-out anchors** is single-site; the carve-out is present and distinctive. |
| 4 | **Author `agents/canary/workflow.md`** — the campaign lifecycle: **scope** (which intake surfaces, which target, what counts as a leak) → **compose** (the payload set, each payload carrying its rule ID and its expected effect) → **hand-off** (the payloads go to **aegis**, which spawns the sandboxed target — canary does not spawn; aegis also **fires** and returns **observation bundles staged to files** — paths, not inline text) → **fire** (the payload is delivered through a real intake surface) → **measure** (did the target follow the payload? escalate? attempt a tool call it should not?) → **record** (every campaign logged with payload + target + result) → **evolve** (the rule set evolves via a **git PR** on the canonical copy — never `skill_fix`, which refuses a file skill) → **report** (findings feed aegis's report → fix → re-verify loop). The **staged-observation** layout is the default: the target's output is referenced by **path** and re-fenced before it is read into canary's reasoning — the report never inlines raw payload text. The campaign artifacts live **off-repo** under `$HOME/.local/state/aegis/engagements/<id>/` — **never committed**, discovery by **recorded path, never glob**. | P1, P2 | The workflow names every stage; the spawn hand-off is explicit; the observation layout is **path-based**; the off-repo pattern is stated with the honest caveat ("reachable, not confined; the privacy of the artifacts is discipline, not a boundary"); the git-only evolution path is named. |
| 5 | **Author `agents/canary/tools_note.md`** — the tool posture in the house style: what canary holds, what it deliberately does **not** hold, the **single-spawner** rule (canary composes; aegis spawns), the campaign-store paths, the **canonical-home** declaration for `injection-screen` (guide §2 — the canonical body is canary's; every other copy is a mirror), and the rule-set evolution duty. | P1 | The canonical-home declaration is present; no `.md` path tokens and no bare `agents/`-prefix cross-references (the guide's closure classes). |
| 6 | **Create `agents/canary/skill-set.yaml` + the canonical skill copy** — the manifest entry for `injection-screen` (name/version/auto_load/category/description) and the canonical body at `agents/canary/skills-template/injection-screen.md` (authored in P1). **auto_load is a live decision** and D2's default settles it for canary: **`auto_load: true`** — canary is the persona whose *job* is hostile content, and its rule set must be **reachable by a persona with no skill tools** (canary holds no skill-search surface). The **other** recipients (`aegis`, `worker`, `sweeper`) take **`auto_load: false`** + a **workflow-mandated load**, so the skill is a tool of the trade rather than a per-turn context cost. | P1 | The manifest entry parses; the body exists at the same-dir template path (the seeder's requirement); the `auto_load` choice is recorded with its rationale; the version equals the roster's (the propagation lever). |
| 7 | **Build the intake-surface map + the campaign spec (D6 = Option A)** — one row per surface: the surface ID, its mechanism, the **owning agent set**, whether it is **capped** and by what, and the **screenable** yes/no. The map covers, at minimum: (1) bash stdout/stderr — unconfined, uncapped; (2) `read_file` — absolute paths, no boundary check, 6000-char cap; (3) grep/glob walks; (4) MCP results — verbatim, unbounded; (5) explore/RAG — unbounded; (6) `read_context` — unbounded, **auto-injected** for `worker`; (7) chat-source messages (only bound instances — none by default); (8) job `[JOB_EVENT]` frames (truncated); (9) the `context` dict on spawn/`send_message` (hard-capped 4000 chars); (10) framed child reports (the existing fence precedent); (11) skill injection bodies (unscreened); (12) Plane issue bodies (PM-only); (13) web content. The **v1 campaign** fires the **5 legs a leaf-class target can actually receive** — channels 1, 2, 3 and 5 fired, plus channel 6 (the daemon's existing `[SYSTEM NOTE]` frame) as the **comparison baseline** — and **channels 4/7/8/9/11 are disclosed as NOT covered in v1**, each with its reason (target class). The procedure carries a **five-item mechanical pre-flight**: dev identity asserted · dev DB asserted · a scratch `DATA_DIR` with a non-poisoned pin · self-owned parentage · synthetic payload only. | P0 | The map is complete and each row carries its owning agent set; the **coverage table** names the 5 covered legs and the uncovered channels **with reasons**; the pre-flight is a 5-item checklist in the campaign procedure. It is the **finding record** for a future daemon-side lane (not a v1 change — R8). |
| 8 | **Edit `agents/aegis/meta.json`** — **one line**: `canary` joins `team_members`, producing the 5-set `{hacker, cracker, sweeper, worker, canary}`. The team members are **not** sorted by a fixed convention — the pin asserts the **set**. | — | The edit is **one line**; the file is otherwise byte-identical (the merge-overlap strategy depends on this being minimal). |
| 9 | **Edit `agents/aegis/workflow.md`** — **one appended section**: **when to engage canary** (the trigger conditions — an intake surface is newly exposed, a detection rule is suspected weak, an agent's context shows untrusted content, a new agent joins the family) and **how** (canary composes; aegis spawns the sandboxed target; aegis adjudicates the finding; the rule set evolves by git PR). The section is appended **at the end** (additive at EOF ⇒ a minimal conflict surface against the concurrent memoir lane). | — | The file grows by one appended section; prior content is byte-identical; the trigger conditions and the hand-off are both stated; aegis's existing `[REPORT SANITY:]` guidance (canonical home: its `rule.md`) is untouched. |
| 10 | **Draft the canary-specific test legs** — **T35** (canary meta pins: leaf team under the D1 default, allow/deny exact, **no privileged category, no `mcp_full_access`, no `chart`**, no queue/model grants, the **tools-block round-trip**) and **T36** (campaign-log + report shape: every campaign logs payload + target + result; the report carries the fence/provenance shape and **never pastes payload text unfenced**). Plus the meta/tool pins (allow set exact, deny set, `deny_spawn`, leaf team under the D1 default, the registry round-trip), the campaign-map completeness leg, the canonical-home declaration leg, and the **report-integrity posture** leg (canary is skipped by the empty-team exemption under the D1 default — the leg asserts the *skip*, so a future D1 change flips it deliberately). | 1–9 | Each leg is **mutation-meaningful**; the leaf-team leg is written so that a D1 ruling changes **one** assertion, not the file. |

---

## Gates

| Gate | Criterion |
|------|-----------|
| **G3.1** | The canary tree holds **exactly** the 5 prompt files + `meta.json` + `skill-set.yaml` + the canonical skill copy — no legacy artifact names (each would move the baseline row count). |
| **G3.2** | The guide checklist is satisfied for **every** authored file (tone block, cardinal cap, one canonical home, no `.md` tokens, no bare `agents/` prefixes, no system internals). |
| **G3.3** | The meta round-trips through the registry's parser and holds **no** privileged category, **no** `chart`, **no** `mcp_full_access`. |
| **G3.4** | The aegis edits are **minimal and additive** — `meta.json` one line; `workflow.md` one EOF-appended section. |
| **G3.5** | The posture is stated plainly in the persona: **leaf / design-lean / report-only / private findings / self-owned sandbox only**, and the **no-OS-boundary** limitation is stated rather than implied away. |
| **G3.6** | The intake-surface map is complete (12 surfaces) with owning agent sets — it is the campaign map and the future-lane finding record. |
| **G3.7** | The campaign store pattern matches the family's off-repo ruling (`$HOME/.local/state/aegis/engagements/<id>/`, recorded path, never glob, never committed). |
| **G3.8** | The report-integrity posture is consistent with D1 (skip under the leaf default). |

---

## Verification Commands

```bash
cd /home/nea/ensemble-src-wt-canary-injection-defense

# --- DERIVATION 1: the canary tree is EXACTLY these files (no legacy artifact names) ---
find agents/canary -type f | sort
#   expected 7: meta.json, rule.md, soul.md, tools_note.md, workflow.md,
#               skill-set.yaml, skills-template/injection-screen.md
#   MUST NOT contain memory.md / growth.md / tools.md / skill.md  (each would add a baseline ROW)

# --- DERIVATION 2: baseline arithmetic (canary adds EXACTLY 4 rows) ---
wc -l < agents/.prompt-artifacts.sha256
#   base 177  +  4 (canary soul/rule/workflow/tools_note)  =  181 expected at P4
#   NOTE: meta.json / skill-set.yaml / skills-template contribute ZERO rows.

# --- DERIVATION 3: the aegis edits are MINIMAL + ADDITIVE ---
git diff -- agents/aegis/meta.json | grep -c '^+[^+]'          # expected 1 (the team_members line)
git diff -- agents/aegis/workflow.md | grep '^-' | grep -v '^---' | wc -l   # expected 0 deletions
git diff -- agents/aegis/workflow.md | grep -c '^+[^+]'        # the appended section only

# --- DERIVATION 4: the roster is the 5-set, and the pin target moved ---
python3 -c "import json;print(sorted(json.load(open('agents/aegis/meta.json'))['team_members']))"
#   expected: ['canary','cracker','hacker','sweeper','worker']
grep -n 'test_aegis_team_members_exact' -A2 tests/unit/agents/test_aegis_security_family.py
#   the SHARED pin still asserts the 4-set -> it MUST fold at P4 (it is red until then)

# --- DERIVATION 5: the canary meta's tool posture ---
python3 - <<'PY'
import json
m = json.load(open('agents/canary/meta.json'))
print("allow:", sorted(m["tools"]["allow"]))
print("deny :", sorted(m.get("tools", {}).get("deny", [])))
print("deny_spawn:", m["tools"].get("deny_spawn"))
print("team :", m.get("team_members"))
PY
#   expected: a NON-EMPTY allow; NO mcp_full_access / job / instance_read / instance_ops / chart /
#             plane; deny_spawn == ['knowledge']; team == [] or absent (the D1 leaf default)

# --- DERIVATION 6: the guide's prose closure classes on the NEW files ---
grep -nE '\.md\b|\bagents/' agents/canary/soul.md agents/canary/rule.md \
                            agents/canary/workflow.md agents/canary/tools_note.md
#   MUST print nothing (the .md-token class and the bare-agents-prefix class)

# --- DERIVATION 7: the campaign-map completeness (the 12 surfaces) ---
#   the map lives in canary's workflow.md / tools_note.md; assert each surface ID is named
for s in bash stdout read_file grep glob mcp explore read_context chat job_event context child_report skill plane; do
  printf '%-14s ' "$s"; grep -ci "$s" agents/canary/workflow.md agents/canary/tools_note.md | paste -sd' '
done

# --- DERIVATION 8: the off-repo campaign store (never committed) ---
grep -rn 'engagements' agents/canary/*.md | head
#   the path pattern must be the family's off-repo root; nothing under the repo may hold campaign output
```

---

## Coupling

| Direction | Coupling | Notes |
|---|---|---|
| **in** | **P0** (tight) | checklist, shape table, D-table, the intake-surface list. |
| **in** | **P1** (tight) | the canonical skill body; the mirror roster; the propagation version. |
| **in** | **P2** (tight) | canary's doctrine text and the carve-out — applied **verbatim**. |
| **out** | **P4** (tight) | the canary tree + the two aegis edits are part of the one-commit payload; the legs authored here are assembled into the lane test file; the baseline re-stamp closes on this content. |
| **out** | **P5** | loose — Gate D (report-integrity posture), Gate E (boot-scan, 44 discoverable), Gate H (privacy + the untouched personas). |
| **out** | **P6** | independent. |
| **↔ memoir lane** | (conditional) | The two `aegis` edits are deliberately **minimal + additive** so a future memoir edit to `agents/aegis/**` collides cheaply; the merge-overlap strategy lives in `plan-overview.md`. |

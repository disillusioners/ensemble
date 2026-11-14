# Research: guideline content shapes + allow-list census (guideline-read-tool lane)

Worktree: /home/nea/ensemble-src-wt-guideline-read-tool @ 5316e0cd3 (feature/guideline-read-tool, base = post-guidelines-landing origin/latest). All paths below are relative to that worktree. Read-only research; canonical checkout untouched.

---

## A. Guideline file shape census — docs/guidelines/ (5 files)

Directory listing: README.md, TEMPLATE.md, guideline-live-views-passcode.md, guideline-memoir-reflection-lane.md, guideline-notify-user-bounds.md. **No YAML front matter in any file** (every file starts directly with an H1 on line 1).

### A.1 README.md (157 lines) — AUXILIARY shape (differs from guideline-*)

```
1: # Guidelines for agents-ensemble — the feature proposal
2: 
3: **Status:** CANONICAL — homed 2026-11-13; placement Emperor-ratified at
4: docs/guidelines/ (versioned with the system, promoted with each release);
5: lifecycle now live.
6: 
7: ---
8: 
9: ## 1. What a Guideline is
10: 
11: A **Guideline** is the knowledge document owed by any feature that ships in a
12: state an operator or agent cannot see from the code alone:
```

- No `**Feature:**` line, no `**Audience:**` line, no `# guideline-*` H1.
- H1 (L1) is a descriptive title, NOT a slug.
- Status is a standalone multi-line prose block (L3–L5), value "CANONICAL …".

### A.2 TEMPLATE.md (90 lines) — AUXILIARY + template-placeholder shape

```
1: # Guideline TEMPLATE
2: 
3: > Distilled from ENSEMBLE-30 ("prompt-assent enablement", hand-crafted by
4: > Emperor + Ari, 2026-11-12) — the first guideline. Copy this file, rename
5: > to `guideline-<feature-slug>.md`, fill every section IN ORDER. A section
6: > with nothing to say says "none" — it is never deleted.
7: 
8: ---
9: 
10: # guideline-<feature-slug>
11: 
12: **Status:** draft | reviewed | canonical
13: **Feature:** <one line — what shipped, in which release(s)>
14: **Audience:** operator / agent / both
15: 
16: ## 1. Purpose & doctrine
```

- TWO H1s: L1 `# Guideline TEMPLATE` and L10 `# guideline-<feature-slug>` (placeholder).
- L12's pipes (`|`) are VALUE OPTIONS ("draft | reviewed | canonical"), NOT field separators. Fields are one-per-line here (L12/L13/L14), unlike the guideline-* single-line shape.
- Field values are placeholder text.

### A.3 guideline-live-views-passcode.md (54 lines) — CANONICAL guideline-* shape

```
1: # guideline-live-views-passcode
2: 
3: **Status:** draft · **Feature:** live-views passcode public lane + session-bypass origin gate. Base lane (`feature/live-view-passcode`, merge `09d4c9490`, migration `20261017_193816_create_live_view_passcode_tables.sql`) recorded under CHANGELOG **`[0.22.0]` — 2026-10-18**; the `view_write`/`board_link` Phase-3 surface is CHANGELOG **`[0.26.0]`**; the session-bypass origin-gate rewrite at **v0.29.7** (tag: "views-session-bypass lane: loop-2 closure; both bypass classes dead (re-kill 3/3); no third class found"). · **Audience:** operator / agent
4: 
5: ## 1. Purpose & doctrine
6: 
7: Narrow unauthenticated public sharing of live views via `/views/public/{root}/{rel}`, canonical passcode gate **daemon-side**, proxies **pure passthrough**. The one-line contract (runbook): "The public namespace is **exactly `PathPrefix /views/public`** — nothing narrower and nothing wider, in BOTH deployment states. … Never exempt `/views` or `/api`."
```

### A.4 guideline-memoir-reflection-lane.md (54 lines) — same canonical shape

```
1: # guideline-memoir-reflection-lane
2: 
3: **Status:** draft · **Feature:** Memoir Store v2 "The Reflecting Mind" — dream (reflection), briefing, keepsake, poignancy lanes. v1 store at v0.29.0 (decree 2026-11-06, migration `20261105_102821_create_memoir_tables.sql`); v2 reflection lane at **v0.29.15** (tag: "Memoir Store v2 'The Reflecting Mind' (memoir-v2 lane)"; merge `035f26ea1`; migration `20261109_021154_create_memoir_v2_tables.sql`). · **Audience:** operator / agent
4: 
5: ## 1. Purpose & doctrine
6: 
7: Ari's durable episodic memory. She writes episodes (append-only, instant, never blocking the turn); the DREAM periodically consolidates them into cited claims (`memoir_syntheses`); recall serves the beliefs FIRST, raw episodes after. The persona surface (approved persona + keepsakes) is injected into ari's prompt as a two-zone briefing.
```

### A.5 guideline-notify-user-bounds.md (49 lines) — same canonical shape

```
1: # guideline-notify-user-bounds
2: 
3: **Status:** draft · **Feature:** the `notify_user` tool — the "Ari voice" (privileged `notify-user` category). Commission 2026-10-24 ("if you see the next message need user/me → you can invoke message to discord, if not do in silence"); `feature/ari-notify-user` merge `97652f337`; `notify_audit` migration `20261025_000001_create_notify_audit.sql`; staged verification at v0.27.8. The commission's "v0.26.2+" dating — UNVERIFIED against a surviving CHANGELOG section (no `[0.26.2]`/`[0.26.3]` heading exists in any tree checked); the dates + merge above are the verified anchors. · **Audience:** operator / agent
4: 
5: ## 1. Purpose & doctrine
6: 
7: The deliberate, opt-in message from a non-user-originated turn (self-alarm wake, scheduled fire) to the session's bound registered chat source. Doctrine: **the board is the ALWAYS surface; notify is the MAY voice.** Nothing auto-sends; silence is the default. The signature takes the MESSAGE, never a destination — destination resolves SERVER-SIDE from the session's own `original_source` binding, so an arbitrary destination is structurally impossible. A session never contacted via chat has NO legal target — the tool refuses and the board stays the fallback surface.
```

### A.6 Field-parse map (per file)

| Field | guideline-*.md (3 files) | README.md | TEMPLATE.md |
|---|---|---|---|
| slug | H1 text L1 == filename stem (verified identical, all 3) | none — filename stem `README` is the only option | none (L10 H1 is a `<feature-slug>` placeholder) |
| title | NO dedicated title field. H1 == slug. Closest one-line title = leading clause of `**Feature:**` value (up to first `.` — e.g. "live-views passcode public lane + session-bypass origin gate") | H1 L1 | H1 L1 (`Guideline TEMPLATE`) |
| status | value after `**Status:**` on L3, terminated at the FIRST ` · ` separator → "draft" in all 3 | prose after `**Status:**` on L3 (multi-line; take L3 rest-of-line, fail-soft: "CANONICAL") | value after `**Status:**` on L12 — an options string, not a state |
| one-line purpose | first non-empty paragraph after `## 1. Purpose & doctrine` (= L7 in all 3; single paragraph) | first paragraph under `## 1. What a Guideline is` (L11+, multi-line — would need fold-to-one-line or truncation) | §1 body is placeholder prose ("What the feature IS…") — not a real purpose |
| audience | value after `**Audience:**` on L3 → "operator / agent" | absent | L14 (placeholder) |

**Separator, exact quote:** the guideline-* Status line uses the MIDDLE-DOT character ` · ` (U+00B7, space-padded) between fields: `**Status:** draft · **Feature:** … · **Audience:** operator / agent`. NO pipe separators anywhere in guideline-* Status lines. (TEMPLATE.md L12's `|` are enumerated status options, a different thing entirely.)

**Trivial fail-soft parser recommendation:**
- Files whose H1 does NOT start with `guideline-` (README, TEMPLATE) = auxiliary docs: list them with slug = filename stem, status = text following `**Status:**` on the first line containing it (rest-of-line only), purpose = first paragraph after the first `## 1.` heading if present else "(no purpose section)". Never hard-fail; never invent a Feature/Audience.
- guideline-* files: slug/title from H1 L1 (== filename stem); status = L3 `**Status:**` value cut at first ` · `; purpose = first non-empty paragraph after `## 1. Purpose & doctrine` (take the paragraph, optionally truncate to one line); audience = L3 tail after `**Audience:**` when present.
- Guard rails for the parser: Status line is ONE physical line in guideline-* files (L3 in each), so no multi-line scan needed there; README's status DOES wrap (L3–L5) — rest-of-line on the `**Status:**` line is the fail-soft answer.

---

## B. Agent allow-list census (tools.allow, verbatim)

### B.1 agents/ari/meta.json (53 lines; id "ari" L2)

`tools.allow` array spans L11–L49:

```json
11:    "allow": [
12:      "job",
13:      "question",
14:      "bash",
15:      "filesystem",
16:      "time",
17:      "self",
18:      "help",
19:      "image",
20:      "image-gen",
21:      "context",
22:      "shared_meta_kv",
23:      "project",
24:      "job_messages",
25:      "job_tree",
26:      "job_progress",
27:      "job_inject",
28:      "pause_instance",
29:      "resume_instance",
30:      "system_restart",
31:      "system_upgrade",
32:      "mission",
33:      "service",
34:      "scheduling",
35:      "view-views",
36:      "secrets",
37:      "settings",
38:      "board",
39:      "self-alarm",
40:      "view-write",
41:      "board-link",
42:      "mcp_rpc",
43:      "wedge-capture",
44:      "notify-user",
45:      "pok", "nest-report",
46:      "memoir",
47:      "prompt-reload",
48:      "prompt-assent"
49:    ],
50:    "deny": ["edit_file", "write_file", "watch_job", "watch_jobs"]
```

**Minimal diff:** insert `"guidelines"` after L48 `"prompt-assent"` (append at array end, keeping the one-category-per-line tail style): `      "prompt-assent",\n      "guidelines"\n    ],`. Neighbors: "prompt-assent" before, array-close after. This matches the established tail pattern (memoir → prompt-reload → prompt-assent are the three most recent privileged grants, each appended at the end).

### B.2 agents/leader/meta.json (18 lines; id "leader" L2)

```json
13:  "tools": {
14:    "allow": ["instance", "subtree_messages",
15:    "subtree_status", "self", "attestation", "project", "help", "image", "image-gen", "knowledge", "mcp", "critical_notes", "project_history", "shared_meta_kv", "question", "midflight", "job_pause", "job_resume", "scheduling", "view-views", "secrets", "settings", "mcp_rpc", "wedge-capture", "pok", "nest-report"]
16:  },
```

**Minimal diff:** on L15, change the tail `"wedge-capture", "pok", "nest-report"]` → `"wedge-capture", "pok", "nest-report", "guidelines"]` (append at array end; neighbors "nest-report" before, `]` after). Two-line array (L14 opener wraps to L15) — keep the wrap intact.

### B.3 agents/devops/meta.json (15 lines; id "devops" L2)

```json
11:  "tools": {
12:    "allow": ["bash", "proc", "filesystem", "time", "self", "help", "image", "knowledge", "mcp", "context", "shared_meta_kv", "db", "infra", "project", "dynamic-skill", "question", "midflight", "service", "metrics-read", "nest-report-read", "pok", "nest-report"]
13:  },
14:  "team_members": ["explorer"]
```

**Minimal diff:** on L12, tail `"nest-report-read", "pok", "nest-report"]` → `"nest-report-read", "pok", "nest-report", "guidelines"]` (neighbors "nest-report" before, `]` after).

### B.4 agents/sonic-leader/meta.json (19 lines; id "sonic-leader" L2 — id == dir name, confirmed)

```json
14:  "tools": {
15:    "allow": ["instance", "bash", "filesystem", "time", "self", "help", "context", "shared_meta_kv", "midflight", "knowledge", "image", "pok", "nest-report"],
16:    "deny": ["edit_file", "write_file", "image_save"]
17:  },
18:  "team_members": ["sonic-developer", "sonic-coder", "sonic-reviewer", "sonic-tester", "giter", "devops"]
```

**Minimal diff:** on L15, tail `"pok", "nest-report"],` → `"pok", "nest-report", "guidelines"],` (neighbors "nest-report" before, `],` after; deny list L16 untouched).

### B.5 Sonic-* directory census (DENIED set for the test matrix)

All five present under agents/, each `meta.json` L2 `"id"` == dir name:

| dir | id | role (description L4) |
|---|---|---|
| sonic-coder | `sonic-coder` | One-shot module executor |
| sonic-developer | `sonic-developer` | Planning/integration layer, parallel coder dispatch |
| sonic-leader | `sonic-leader` | Speed-lane peer orchestrator (GRANT target) |
| sonic-reviewer | `sonic-reviewer` | Read-only integration reviewer |
| sonic-tester | `sonic-tester` | Integration test leaf |

DENIED-sonic set = sonic-coder, sonic-developer, sonic-reviewer, sonic-tester (none may hold "guidelines"). NOTE: none of the five currently holds ANY privileged category — sonic-leader's allow contains no member of PRIVILEGED_TOOL_CATEGORIES (its "nest-report" is the non-privileged report-filing category, distinct from "nest-report-read" #18).

---

## C. Precedence check + midflight characterization

### C.1 "guidelines" category collisions — ZERO

- `grep '"guidelines"'` over `agents/**/*.meta.json` (worktree): **no matches**.
- `grep 'guidelines'` over `daemon/tools/**/*.py` (worktree): exactly ONE hit — daemon/tools/bash.py:1871 `**Agent Guidelines:**`, a docstring prose header inside a tool description. NOT a category name, NOT a registration.
- No `@register_tool_category("guidelines")` anywhere in daemon/tools (the same grep would have caught it).
- PRIVILEGED_TOOL_CATEGORIES (daemon/tools/_tool_registry.py:446–823) currently holds EXACTLY 21 entries, "guidelines" absent: `system_upgrade, system-log, ens-db, ens-env, view-views, secrets, settings, board, self-alarm, view-write, board-link, wedge-capture, instance_read, instance_ops, mcp_rpc, notify-user, metrics-read, nest-report-read, memoir, prompt-reload, prompt-assent` (entry #21 prompt-assent closes the frozenset at :822–823).
- Implementation implication: adding "guidelines" as a new privileged category = entry #22, and the registry's SAME-PR RULE (:440–443, :701–707) mandates re-deriving and bumping the exact-set / count / pack-string assertion sites by grep in the same commit (11 exact-set + 8 count + 1 pack string per the last census comment; re-derive, don't trust). Also: the category must get a TOOL_CATEGORIES mapping row (:1407-1409 area, e.g. `"guidelines": "daemon.tools.guidelines"`) or the four new meta.json allow entries emit the boot-time "neither a known category nor a known tool" WARNING (view-views precedent, :1420–1434).

### C.2 What "midflight" is — one line

`midflight` is a NON-privileged, opt-in, general (not sonic-internal) category — registered `@register_tool_category("midflight")` (daemon/tools/midflight_report.py:62), mapped at _tool_registry.py:1409, docstring :58–59 "Opt-in per agent via tools.allow: ["midflight"] — the category is NOT auto-granted through any innate-skill mapping", and NOT a member of PRIVILEGED_TOOL_CATEGORIES; it is held by leader, devops, and sonic-leader (a cross-team QA channel: `mid_flight_report` emits a non-blocking progress event to job watchers).

### C.3 Ceremony-claim calibration

"guidelines = FIRST privileged-category grant to a sonic agent" is **SUPPORTED by this tree**: no sonic-* agent allow list contains any of the 21 PRIVILEGED_TOOL_CATEGORIES members (the nearest thing, sonic-leader's `midflight`, is explicitly non-privileged per C.2), so a "guidelines" grant (#22) to sonic-leader would be the first. The claim holds only if "guidelines" is in fact added to PRIVILEGED_TOOL_CATEGORIES — a non-privileged registration would make the "privileged-category" premise false (then the milestone framing would be just "first category grant", already false via midflight).

---

## Serving note (release payload)

README.md L3–L5 states the placement contract: "homed 2026-11-13; placement Emperor-ratified at docs/guidelines/ (versioned with the system, promoted with each release); lifecycle now live" — consistent with serving docs/guidelines/ from the release payload.

## Sources

- Worktree /home/nea/ensemble-src-wt-guideline-read-tool @ 5316e0cd3 (branch feature/guideline-read-tool):
  - docs/guidelines/{README.md, TEMPLATE.md, guideline-live-views-passcode.md, guideline-memoir-reflection-lane.md, guideline-notify-user-bounds.md} (read in full-first-40-lines detail; quoted above)
  - agents/{ari, leader, devops, sonic-leader, sonic-coder, sonic-developer, sonic-reviewer, sonic-tester}/meta.json
  - daemon/tools/_tool_registry.py (:440–857, :1395–1469), daemon/tools/midflight_report.py (:1–70), daemon/tools/bash.py:1871 (grep hit)
- Greps: `"guidelines"` in agents/**/*.meta.json (0 hits); `guidelines` in daemon/tools/**/*.py (1 prose hit); `midflight`, `PRIVILEGED_TOOL_CATEGORIES` censuses.

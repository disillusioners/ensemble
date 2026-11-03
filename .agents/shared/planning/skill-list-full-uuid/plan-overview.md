# Plan Overview — wi-76d89e11: skill_list full-uuid id-emission contract (lane `skill-list-full-uuid`)

- **Date**: 2026-11-03 (planner-light pass)
- **Base / branch**: `fix/skill-list-full-uuid` @ `c4161335b406b884d1de6c8e7bf4b3d1f7fe2abe` (== origin/latest at lane open); worktree `/home/nea/ensemble-src-wt-skill-list-full-uuid`
- **Mode**: planner-light — **verify, don't redesign.** The architecture ruling (`.agents/shared/planning/skill-list-full-uuid/architecture-ruling.md`, R1–R7) is BINDING input; this plan operationalizes it.
- **Verification status**: ALL dispatcher receipts + ruling receipts independently re-verified read-only at HEAD. **Zero contradictions found.** Two ±4 line-number drifts (content exact) — see §3. Three planner observations added to the verified-out list (§6), none redesigning the fix.
- **Ground rules (binding)**: stage-only (NO live promote, NO live restarts, NO `.env` edits), surgical diff, every safety property preserved (ruling R6 → `risk-register.md`).

---

## 1. Goal

Make every agent-facing skill id that the skill tools **emit** be one that `skill_view` **accepts verbatim**, per source (ruling R1):

| Source | Agent-facing `id` skill_view consumes VERBATIM |
|---|---|
| DB skill | the FULL stored uuid4 (`Skill.id`, 36 chars — `Skill.to_dict()["id"]`) |
| Project-file skill | the ADDRESS `pf:<ns>:<name>` (`mint_address`, model.py:82-84) |

Fix is **render-layer only**: no id minting/scheme change, no `skill_view` resolution change, no schema change, no internal-key remap (the pseudo-id stays THE internal key — R3 hard constraint).

**SCOPE QUALIFIER (council A4 fold — the goal is NOT a global invariant).** This lane fixes the
**skill-tool EMISSION seams** (the 4 sites in §4). It is **NOT** a tree-wide "every agent-facing
skill id is verbatim-consumable" invariant — three surfaces OUTSIDE the 4 sites still render a
non-consumable id, and they are **disclosed, not fixed** (see §5 RESIDUAL-DISCLOSURE rows R1–R3):
the `skill_fix` echo truncation, the `skill_feedback` echo truncation, and the `_render_pf_view`
pseudo-id line. A reviewer reading §1 as a global invariant would raise a false positive against
those three; they are **known, named, and routed**, not regressions. The same qualifier applies to
the commit-message claim in W1 (the ceremony text is the authoritative statement, not the commit
message).

## 2. Confirmed root cause (receipts verified at HEAD c4161335b)

**The defect**: `skill_list`'s DB row renderer truncates the store-originated id to 8 chars — `daemon/tools/skill_tools.py:765`:

```python
764:  skill_id = it.get("id", "")                       # full uuid4 from the store projection
765:  short_id = skill_id[:8] if isinstance(skill_id, str) else "????????"
769:  lines.append(
770:      f"- [{short_id}] {name} — {cat} — {status}{db_suffix}"
771:  )
```

`skill_view` resolves DB ids by **exact PK** (`daemon/repositories/skill/repository.py:241` → `session.get(Skill, skill_id)`) with NO prefix fallback — an 8-char token can never resolve:

- `daemon/tools/skill_tools.py:969-971`: `result = await service.view_skill(skill_id)` → `None` → `ERROR: skill_view: no skill with id '<id>'`.

**Runtime proof (RED-at-BASE anchor)** — `.agents/tester/RESULTS/2026-11-03-wi76d89e11-phase0-runtime-repro.md` (worker e6ee309c, real-tool harness, real services/repos):
- list returned `[a3f15088]` → verbatim `skill_view('a3f15088')` → **`ERROR: skill_view: no skill with id 'a3f15088'`** (verbatim envelope);
- control leg: `skill_view('a3f15088-49a7-47a8-993d-d7074c9005ba')` resolves (that full uuid is the stored `skills.id` PK — uuid4 minted by the MODEL field `default_factory`, `repositories/skill/models.py:161-165`; there is NO repo-side mint — A3 fold, correcting the repro doc's `SkillRepository.create` phrasing);
- all 5 file rows already emit consumable `pf:tester:<name>` addresses (all 5 RESOLVE through the `is_pf_address` branch, skill_tools.py:924);
- adjacent facet: `pf-<hex16>` pseudo-ids are REFUSED by `skill_view` (injection-path render class — the second half of the fix).

**Supporting receipts** (each verified): store projection carries the full id (`daemon/services/skill_store_service.py:235-250`, metadata-only projection); `ProjectFileSkill` is a dataclass with NO `to_dict` (model.py:108-155), so the fused search path's `_json_default` (skill_tools.py:83-99, prefers `to_dict`, falls to `__dict__`) serializes file entries with `"id": "pf-<hex16>"`; `_pf_skill_payload` puts the pseudo-id under the conventional `"id"` key (skill_tools.py:145, consumed at :219 by the file-only degraded path); the injection body id-segment reads `getattr(skill, "id")` = pseudo-id (`daemon/services/skill_injection_service.py:1262`, rendered :1274) while the header of the same message carries the address (:788-794).

## 3. Line-number drift record (verified at HEAD; content exact — no contradiction)

| Receipt | Ruling/dispatch said | Verified at HEAD |
|---|---|---|
| `_pf_skill_payload["id"]` | :141 | **:145** (function def :134, dict :144-160) |
| `_format_injection` id read/render | :1266-1281 | **read :1262, render :1271-1275** (within `_render_file_skill` :771-795 context) |
| feedback dual-form predicate | :1457-1465 | comment :1449-1462, **predicate :1463-1466** |
| `_render_pf_view` address/id lines | :404-405 (ruling receipt) | **:402** (A1 fold; the pre-lane `:399-400` drifted by the lane's own edits) |
| `_format_injection` span | — | def at **:1173** (runs to ~:1332); the id segment (:1262 read, :1271-1275 render) sits INSIDE `_format_injection`, which `_render_file_skill` (:771-795) calls for the body — A1 fold |
| skill_create id ternary | :1119-1125 | :1119-1123 (`created.id[:8]` at :1120), return :1124-1127 |
| file-row append | :823-826 | append expression :822-827 |

**Post-fix line drift (council round-2 fold, 2026-11-03)**: the receipts in this paragraph were captured at the PRE-lane tree; the lane's own edits (plus the W2 import line) shifted the affected surfaces by +1..+2 — `skill_fix echo` :1258 → **:1260**, `feedback display` :1489 → **:1491**, `_render_pf_view` id line :399-400 → **:402**, the `short_id` local :765/:766 → **:767**. Every citation in §5.1 R1–R7 is the CURRENT number.

All other receipts matched exactly (skill_list :764-771; `[:8]` :765; view :913/:924/:969-971; repo :241; models :161-165; `_json_default` :83-99; fused dumps :670; skill_fix refusal :1211; skill_fix echo :1258; feedback display :1489 + full-id pass-through :1491-1492; mint fns model.py:57/:60/:68-79/:82-84/:87-92; REST routers/skills.py:282-308; REST-search DB-only skill_search_service.py:592-600; duck-type :127-130; `_tag_result_sources` :1056-1075).

## 4. Fix inventory — R2-IN, per site (exact before → after)

**4 sites (5 code edits + 1 docstring cluster) in 3 files, plus the P1-6 doc-only corrections in the same files. Zero `.sql`, zero minting changes, zero view changes.**

**⚠️ SHIPPED-CODE DELTA — CORRECTED (council A3 fold).** The "diff-shape expectation stated numerically" that the reviewer checklist used — "**~6 changed lines**" — was **never true** and was uncounted for two reasons: the **N6 local mirror** (the duplicated `_is_project_file_skill` helper + the `SOURCE_PROJECT_FILE` literal, ~16 lines) and the **docstring re-wrap** (the agent-visible docstring rows, ~10 lines). A reviewer checking "~6" against the real delta raises a **false positive**. The **RECOUNT, re-measured after the council folds** (`git diff c4161335b -- daemon/`, worktree at the fold):

| Metric | Value |
|---|---|
| Files touched (shipped code) | **4** (`model.py`, `skill_injection_service.py`, `skill_search_service.py`, `skill_tools.py`) — 3 at the pre-fold state; the council-W2 fold added `skill_search_service.py` (the constant's second consumer) |
| `git diff --numstat` | **+104 / −19** |
| Total changed lines | **123** |
| Non-trivia (code + docstring) | **+71 / −19** = **90** |
| Trivia (blank / comment-only) | **+33 / −0** |

Per-file numstat (post-fold): `model.py` +39/−2 · `skill_injection_service.py` +43/−3 · `skill_search_service.py` +8/−2 · `skill_tools.py` +14/−12. **At the PRE-fold state the same measurement was 3 files / +67 / −10 = 77 changed lines** — the council's A3 figure. The growth is the W2 single-sourcing (a documented constant + its import edges in three consumers) and the W5 import-guard phase in the pack, both of which are documentation-and-wiring rather than behaviour.

*(A6/A7 note: the `short_id` local variable NAME at `skill_tools.py:767` is retained post-fix per adjudication A-4 — minimal diff; the name is local-scope only. It now holds the FULL uuid — see the A7 disclosure row in §5.)*

### P1-1 — skill_list DB bracket token → full uuid
`daemon/tools/skill_tools.py:765` (one token; render :769-771 and the `db_suffix` logic :762 byte-unchanged)

- BEFORE: `short_id = skill_id[:8] if isinstance(skill_id, str) else "????????"`
- AFTER:  `short_id = skill_id  if isinstance(skill_id, str) else "????????"` *(variable name kept — minimal diff, local scope; adjudication A-4)*
- Rendered before → after:
  - `- [a3f15088] repro-db-skill-e5ee09ce — repro — active — source: db`
  - `- [a3f15088-49a7-47a8-993d-d7074c9005ba] repro-db-skill-e5ee09ce — repro — active — source: db`
- Line shape, separators, `db_suffix` additivity: byte-preserved (R4 row 1).

### P1-2 — skill_create success line → full id
`daemon/tools/skill_tools.py:1119-1123` (one token inside the existing None-guard ternary)

- BEFORE: `created.id[:8]` → `✅ Skill 'x' created with id a3f15088.\nUse skill_search() to find it.`
- AFTER:  `created.id`     → `✅ Skill 'x' created with id a3f15088-49a7-47a8-993d-d7074c9005ba.\nUse skill_search() to find it.`
- The `"Use skill_search() to find it."` sentence and the `else "unknown"` fallback stay (shape-preservation, adjudication A-4).

### P1-3 — search file payload `id` re-key + additive `pseudo_id`
`daemon/tools/skill_tools.py:145` inside `_pf_skill_payload` (dict :144-160)

- BEFORE: `"id": getattr(file_skill, "id", None),` (pseudo-id) with sibling `"address"` at :153
- AFTER:
  ```python
  "id": getattr(file_skill, "address", None),
  "pseudo_id": getattr(file_skill, "id", None),
  ```
- All other keys untouched; `pseudo_id` is ADDITIVE (house convention: `_tag_result_sources` "additive keys only … consumers that ignore unknown keys keep working", skill_search_service.py:1064-1067).
- Covers BOTH search render paths (the fused seam and the file-only degraded path) — **but the ATTRIBUTION is TWO DIFFERENT builders**: `_pf_skill_payload` (`skill_tools.py:135`, ONE call site at `:221` — the file-only degraded path) serves the file-only path; the FUSED seam is served by `ProjectFileSkill.to_dict()` (`project_skill_source/model.py`) — see P1-4. The COVERAGE claim is TRUE; the single-builder attribution was FALSE (council W3 fold; planner observation O-2).
- Rendered file-entry JSON before → after: `"id": "pf-6aef39fb305c287e"` → `"id": "pf:tester:whole-tree-base-proven-diff", "pseudo_id": "pf-6aef39fb305c287e"`.

### P1-4 — fused-path serialization: new `ProjectFileSkill.to_dict()`
`daemon/services/project_skill_source/model.py` — new method on the dataclass (R4 row: "add `to_dict()` … `_json_default` (:83-99) already prefers `to_dict()`" — no edit at the `_json_default` site)

- Shape: field-set **exactly** the dataclass fields (19, model.py:137-155) + one additive `pseudo_id` key; `id` carries the address; every other value source-identical to `__dict__` semantics:
  ```python
  def to_dict(self) -> dict[str, Any]:
      d = {f.name: getattr(self, f.name) for f in dataclasses.fields(self)}
      d["pseudo_id"] = d["id"]   # the stable internal key (sha16) — R3 hard constraint
      d["id"] = self.address     # the agent-facing verbatim-consumable id — R1
      return d
  ```
- **Implementation follows adjudication A-5, not this sketch** (A5 fold): the shipped method must defensively COPY the mutable fields (`manifest` dict, `shadows` list — mirroring `to_summary` :181-182 practice); the sketch above is shape-only and would alias the originals.
- Constraint pins: `set(to_dict()) == {f.name for f in dataclasses.fields(ProjectFileSkill)} | {"pseudo_id"}` (a vanishing key = real shape churn — R6-7(b)); `version` is a **property, not a field** — NOT in `to_dict()` (matches `__dict__`, observation O-3); the `.id` ATTRIBUTE stays the pseudo-id (M5).

### P1-5 — injection file-body id segment → address
`daemon/services/skill_injection_service.py` `_format_injection` (def :1173; id read at :1262, rendered :1271-1275) — ONE branch, file-only, discriminated by adopting the sanctioned helper semantics **`_is_project_file_skill`** (skill_search_service.py:118-130: `pf:` address-prefix test FIRST, then the `source == "project-file"` duck-tag — safe both directions; DB `Skill` rows carry neither) — A2 fold:

- BEFORE: file skills render `📋 **Skill: <name>** (id: pf-<hex16>, match score: X)`
- AFTER:  file skills render `📋 **Skill: <name>** (id: pf:<ns>:<name>, match score: X)`
- DB path byte-unchanged (already renders the full uuid); segment layout unchanged; the `if skill_id:` omission branch (:1271/:1276-1279) preserved.
- `track_injection`/`_injected_skills` attribution (ruling R3: :460-465/:643-648) reads the id attribute DIRECTLY — untouched, stays pseudo-id, consistent with the usage table.

### P1-6 — agent-visible docstring corrections (W1 fold — doc-only edits, DEVELOPER-executed in commit 1)
The tool `_full_doc_` surfaces render to agents via tool_help; post-fix they must not teach the truncated shape:

| Site | Current (verified) | Correction |
|---|---|---|
| skill_tools.py:**904** (skill_list `_full_doc_` Returns example) | `- [<short_id>] <name> — <category> — <status>` | `- [<id>] <name> — <category> — <status>` (full uuid token) |
| skill_tools.py:**1160** (skill_create `_full_doc_` Returns example) | `✅ Skill '<name>' created with id <short_id>.` | `… with id <id>.` |
| skill_tools.py:**1163-1164** (skill_create `_full_doc_` explanation) | "Where ``<short_id>`` is the first 8 chars of the skill's UUID4 primary key." | "Where ``<id>`` is the skill's full UUID4 primary key." |
| skill_tools.py:**1126** (retained `Use skill_search() to find it.` pointer) | kept per adjudication A-4 (shape-preservation) | **NO edit — adjudicated retention**: the sentence remains useful discovery guidance, not truncation teaching |

Test-side companion (rides the P2 fold commit, not P1): `tests/tools/test_skill_tools_pf_surface.py:726-729` — `test_pseudo_id_form_accepted` docstring tense ("the ``pf-<hex16>`` pseudo-id the injection path renders") goes stale post-fix; reword to the compatibility framing ("the ``pf-`` pseudo-id form remains accepted — legacy-transcript compatibility, R2g").

## 5. OUT / NO-CHANGE list (R2-OUT + ruled NO-CHANGE + routed)

| Surface | Receipt | Disposition |
|---|---|---|
| skill_list file rows | skill_tools.py:822-827 | **NO CHANGE** — already emits `fs.address`; byte-for-byte preserved |
| skill_view resolution (both branches) | :913-971, repository.py:241 | **NO CHANGE** — exact-PK DB + `lookup_explicit` file branch untouched |
| skill_fix response echo `[:8]` | skill_tools.py:1260 | **OUT — DEFER** (display hygiene lane): caller-held echo, cannot strand the agent — **see §5.1 R1 (council-W1 fold: this surface is now NAMED as a residual-disclosure row, with its receipt)** |
| skill_feedback display echo `[:8]` | skill_tools.py:1491 | **OUT — DEFER** (same class; :1491-1492 already passes the FULL id — function unaffected) — **see §5.1 R2 (council-W1 fold)** |
| skill_feedback dual-form routing | skill_tools.py:1449-1466 | **NO CHANGE — PRESERVED AS INVARIANT** (R2g): `pf:` OR `pf-` → `_feedback_pf_skill`; W5 fail-closed (:744-770 pins); metrics-only writes keyed by the RESOLVED pseudo-id; compatibility path for pre-fix transcripts. M6 pins it. |
| skill_fix `pf-` refusal gap | skill_tools.py:1211 | **OUT — ROUTED** (skill-evolution/fix-dispatch lane): prefix test on `ADDRESS_PREFIX` only → a `pf-<hex16>` falls through to `dispatch_fix` as a bogus DB id; harmless today (evolution lane disabled) and LESS reachable after this fix; belongs to the P7-refusal family (siblings :1201-1206: skill_analyze / skill_evolve / skill_get_metrics + REST POST /{id}/fix) which must move together |
| REST/FE fusion | routers/skills.py:282-308; skill_search_service.py:592-600 | **OUT — REPORT-ONLY** (parked v2): REST shares NOTHING with the tool projection (full uuid via `to_dict()`; REST search DB-only v1 — file corpus invisible at `instance_id=None`) — the dispatcher's "if it shares the projection" premise resolves to: it does not |
| `_render_pf_view` `- id: <pseudo-id>` line under `- address:` | skill_tools.py:**:402** (A1 fold; ruling receipt said :404-405) | **NO CHANGE** (address leads; relabelling = shape churn on a surface the dispatcher did not name) — **see §5.1 R4 (council-A1 fold: NOW NAMED as an agent-facing emission risk, the THIRD residual of the same class)** |
| `ProjectFileSkill.to_summary()` `"id": pseudo-id` | model.py:199-224 | **NO CHANGE — planner observation O-1**: ZERO consumers tree-wide (grep daemon/ + tests/); dead surface, not an emission seam; recorded so future lanes don't mistake it for one — **see §5.1 R6 (council-A6 fold: ROUTED to the display-hygiene lane with R4)** |
| id minting/scheme (`uuid4()` PK default; `mint_pseudo_id`; `mint_address`) | models.py:161-165; model.py:68-84 | **NO CHANGE** |
| schema / migrations | — | **NO CHANGE** — zero `.sql`; migration census delta EMPTY expected at stage (standing rule: delta-empty-legitimate) |
| v2 loader itself, tool categories | — | **NO CHANGE** — no category registration changes ⇒ no `tool-categories.json` regen (v0.28.9 lesson) |

### 5.1 RESIDUAL-DISCLOSURE rows — the surfaces this lane did NOT make verbatim-consumable

**These are NOT regressions and NOT deferrals-without-a-name.** They are the council-flagged
surfaces where the "verbatim-consumable id" goal is **not** achieved, each with its own receipt.
The ceremony text must state them plainly — the commit message is NOT the authoritative record
(see the W1 row below).

| ID | Surface | Receipt | Disposition |
|---|---|---|---|
| **R1** *(W1)* | **`skill_fix` echo truncation** — the recorded-request line renders `skill_id[:8]`. | `daemon/tools/skill_tools.py:1260` (the local `short_id = skill_id[:8] …`), rendered at `:1262` | **DISCLOSE — NOT FIXED.** A caller-held echo of the id the CALLER supplied: the agent already holds the full id it passed in, so the truncation cannot strand it. Belongs to the **display-hygiene lane** (same family as R2). |
| **R2** *(W1)* | **`skill_feedback` echo truncation** — the "Feedback recorded for skill …" line renders `skill_id[:8]`. | `daemon/tools/skill_tools.py:1491` (the local), rendered at `:1526` / `:1530` | **DISCLOSE — NOT FIXED.** Same class as R1 (caller-held echo). Note `:1491-1492` already passes the **FULL** id to the metrics service — the function itself is unaffected; only the echo line is truncated. |
| **R3** *(W1 + A1)* | **THE SHAPE HAZARD** — post-change, an echo truncation of an ADDRESS yields `pf:tester:alpha…` → `pf:te…`, whose first 3 characters **superficially resemble the pseudo-id form `pf-<hex16>`**. | the echo truncations at `:1260` / `:1491` (R1/R2) | **DISCLOSE — NOT FIXED.** The truncated address is *display-only* and is never fed back as an id; the risk is a HUMAN/agent misreading `pf:te…` as a pseudo-id and passing it to `skill_view`, which would refuse it (the pseudo-id form is `pf-` + 16 hex, NOT `pf:` + 3 chars) — a confusing refusal, not a wrong resolution. **Named because the council flagged it**: post-change the two id namespaces are visually adjacent in truncated form. |
| **R4** *(A1)* | **`_render_pf_view` emits a NON-consumable pseudo-id** directly beneath the consumable address — an agent-facing id that `skill_view` REFUSES. | `daemon/tools/skill_tools.py:402` — renders `- id: pf-<hex16>` (the internal pseudo-id) on the line directly under `- address: pf:<ns>:<name>` (`:401`) | **DISCLOSE — NO CHANGE.** **Pre-existing** (not introduced by this lane) and **architect NO-CHANGE** (relabelling = shape churn on a surface the dispatcher did not name). BUT it was **NOT named in this plan's §5 as an agent-facing emission risk** before the A1 fold — it is now, as the **THIRD residual of the same class** as R1/R2 (an agent-visible id that is not verbatim-consumable). Route to the **display-hygiene lane**. |
| **R5** *(A5)* | **`to_dict()` normalises out-of-contract values**: `manifest=None → {}` and `shadows=None → []`, where the old `__dict__` fallback emitted `None`. | `daemon/services/project_skill_source/model.py:193-194` — `to_dict()` body (`d["manifest"] = dict(d.get("manifest") or {})`, `d["shadows"] = list(d.get("shadows") or [])`) | **DISCLOSE ONLY — no change.** Both fields are typed **non-Optional** (`manifest: dict[str, Any]`, `shadows: list[str]`), so the normalisation fires **only on a type violation** — i.e. it is a defensive coercion, not a behaviour change on any in-contract input. Adjudication A-5 (defensive copies) already required the copy; the `or {}` / `or []` fallback is the same guard. |
| **R6** *(A6)* | **`ProjectFileSkill.to_summary()` emits `"id": self.id`** — the pseudo-id — and has **ZERO callers** in `daemon/` and `tests/`. | `daemon/services/project_skill_source/model.py:199` (`to_summary()`) | **ROUTE — do not change.** Dead surface today (planner observation O-1). It is a **future** emission risk if someone wires it into a list surface — route to the **display-hygiene lane** with R4 so the two are fixed together. |
| **R7** *(A7)* | **The local at `skill_tools.py:767` is still NAMED `short_id`** but now holds the **FULL uuid** (the truncation was removed; the name was deliberately retained per A-4/A-8 to avoid diff-widening). | `daemon/tools/skill_tools.py:767` | **DISCLOSE ONLY — no rename.** Local-scope name only. **Named because a future reader may "restore" the truncation** by reading the name as the contract — the value is the full stored uuid and MUST NOT be re-truncated. |

### 5.2 Commit-message honesty (W1 — disclosure, NOT a rewrite)

The pushed commit `0ebd39ef8`'s message over-claims that "no surface teaches the truncated shape
any more". That claim is **contradicted by the evidence**: four agent-visible docstring rows still
render `<short_id>` (`daemon/tools/skill_tools.py:1375`, `:1382`, `:1594`, `:1598`) and the two
echo truncations (R1/R2) are still live.

**House precedent: NO commit rewrite** (a pushed commit's history is not rewritten for a wording
defect). The correction is therefore made **here** and in `decisions.md`, and **the ceremony text
is the authoritative statement** — not the commit message. This row, R1–R3, and the A1 row are the
named corrections the ceremony carries.

## 6. Planner observations from verification (additions, not redesigns)

- **O-1** — `to_summary()` dead surface (above).
- **O-2** — `_file_only_bm25_search` (builds entries at `:221`) is the **ONLY** `_pf_skill_payload` consumer (degraded file-only path) → covered by P1-3; the pin exercises it (adjudication A-6). The FUSED path is NOT a `_pf_skill_payload` consumer — it is served by `to_dict()` (P1-4). *(ATTRIBUTION CORRECTED — council W3 fold: the earlier wording "the second consumer" implied one shared builder serving two consumers.)*
- **O-3** — `version` property vs field distinction for the `to_dict()` field-set pin (§4 P1-4).

## 7. Acceptance criteria — dispatcher clause → plan element

| # | Dispatcher binding clause | Satisfied by |
|---|---|---|
| 1 | skill_list returns ids skill_view accepts verbatim (full uuid) | P1-1 (+ file rows already compliant, pinned as armor); pin D1 legs (ii)/(iii) (`pin-spec.md` §D1) |
| 2 | Do NOT change the id format/minting | §5 NO-CHANGE rows (mint fns, PK default, internal keys); M5 mutation leg pins the attribute/mint-shape invariant |
| 3 | Do NOT change skill_view resolution semantics | §5 NO-CHANGE row (view + repo untouched); pin leg (iii) exercises BOTH view branches unchanged through the new emissions |
| 4 | Keep the rest of the payload byte-identical (no shape churn); changed/additive fields pinned | R4 preservation table realized in P1-1/P1-2 (token-only), P1-3/P1-4 (additive `pseudo_id` only; field-set-equality pin), P1-5 (segment layout); folded :488 pin continues asserting suffix-additivity shape |
| 5 | Pin non-vacuous: RED-at-BASE/GREEN-at-HEAD + id == stored full uuid for BOTH sources | `pin-spec.md` §D1 (iv) str-equal value assertions per source; §PROC RED-at-BASE at c4161335b anchored to the runtime repro RESULTS doc |
| 6 | OUT OF SCOPE: REST/FE fusion (report-only), id format changes, v2 loader itself | §5 OUT/ROUTED rows; REST finding reported in `decisions.md` §Routed |
| 7 | Ground rules: stage-only, no live promote, no live restarts, no .env edits, surgical diff, preserve every safety property | `decisions.md` §Ground rules; `risk-register.md` maps every R6 property → guarding pin |
| 8 | *(council W6)* Named next free version slot: **v0.28.10** — read-only census 2026-11-03: `releases/` = {v0.28.7, v0.28.8, v0.28.9}, highest tag `v0.28.9`, live `v0.28.9`; **re-derive at stage time** per the standard stage-freshness rule (concurrent lanes may consume it) | `decisions.md` §Ground rules (A-0) |

## 8. Deliverable map (this lane's planning set)

| File | Content |
|---|---|
| `plan-overview.md` | this file |
| `implementation-steps.md` | P1/P2/P3 ordered steps + the full P2 sweep enumeration (fold list + verified-unaffected corpus) |
| `pin-spec.md` | concretized pin per ruling R5 (harness, legs, mutations M1-M6(+M2b), RED/GREEN procedure) |
| `decisions.md` | binding rulings recorded, planner adjudications A-0..A-7, gate order |
| `risk-register.md` | R6 safety properties → dependency → guarding pin |

Gate order (binding, from the dispatcher): reviewer council → fresh approver → implementation → code council → tidier → tester functional → **stage (NO promote)**.

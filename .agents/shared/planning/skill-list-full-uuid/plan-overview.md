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
- control leg: `skill_view('a3f15088-49a7-47a8-993d-d7074c9005ba')` resolves (that full uuid is the stored `skills.id` PK, minted uuid4 by `SkillRepository.create`; `repositories/skill/models.py:161-165` = `str(uuid.uuid4())` PK);
- all 5 file rows already emit consumable `pf:tester:<name>` addresses (all 5 RESOLVE through the `is_pf_address` branch, skill_tools.py:924);
- adjacent facet: `pf-<hex16>` pseudo-ids are REFUSED by `skill_view` (injection-path render class — the second half of the fix).

**Supporting receipts** (each verified): store projection carries the full id (`daemon/services/skill_store_service.py:235-250`, metadata-only projection); `ProjectFileSkill` is a dataclass with NO `to_dict` (model.py:108-155), so the fused search path's `_json_default` (skill_tools.py:83-99, prefers `to_dict`, falls to `__dict__`) serializes file entries with `"id": "pf-<hex16>"`; `_pf_skill_payload` puts the pseudo-id under the conventional `"id"` key (skill_tools.py:145, consumed at :219 by the file-only degraded path); the injection body id-segment reads `getattr(skill, "id")` = pseudo-id (`daemon/services/skill_injection_service.py:1262`, rendered :1274) while the header of the same message carries the address (:788-794).

## 3. Line-number drift record (verified at HEAD; content exact — no contradiction)

| Receipt | Ruling/dispatch said | Verified at HEAD |
|---|---|---|
| `_pf_skill_payload["id"]` | :141 | **:145** (function def :134, dict :144-160) |
| `_format_injection` id read/render | :1266-1281 | **read :1262, render :1271-1275** (within `_render_file_skill` :771-795 context) |
| feedback dual-form predicate | :1457-1465 | comment :1449-1462, **predicate :1463-1466** |
| skill_create id ternary | :1119-1125 | :1119-1123 (`created.id[:8]` at :1120), return :1124-1127 |
| file-row append | :823-826 | append expression :822-827 |

All other receipts matched exactly (skill_list :764-771; `[:8]` :765; view :913/:924/:969-971; repo :241; models :161-165; `_json_default` :83-99; fused dumps :670; skill_fix refusal :1211; skill_fix echo :1258; feedback display :1489 + full-id pass-through :1491-1492; mint fns model.py:57/:60/:68-79/:82-84/:87-92; REST routers/skills.py:282-308; REST-search DB-only skill_search_service.py:592-600; duck-type :127-130; `_tag_result_sources` :1056-1075).

## 4. Fix inventory — R2-IN, per site (exact before → after)

**4 sites (5 concrete edits) in 3 files. Shipped-code delta target: ~6 changed lines + one new short method. Zero `.sql`, zero minting changes, zero view changes.**

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
- Covers BOTH consumers of the payload builder: the fused-path file entries and the file-only degraded path (`_file_only_bm25_search` builds entries at **:219** — planner observation O-2).
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
- Constraint pins: `set(to_dict()) == {f.name for f in dataclasses.fields(ProjectFileSkill)} | {"pseudo_id"}` (a vanishing key = real shape churn — R6-7(b)); `version` is a **property, not a field** — NOT in `to_dict()` (matches `__dict__`, observation O-3); the `.id` ATTRIBUTE stays the pseudo-id (M5).

### P1-5 — injection file-body id segment → address
`daemon/services/skill_injection_service.py` `_format_injection` (id read at :1262, rendered :1271-1275) — ONE branch, file-only, discriminated by the sanctioned duck-type (`getattr(skill, "source", None) == "project-file"` — same test as skill_search_service.py:127-130):

- BEFORE: file skills render `📋 **Skill: <name>** (id: pf-<hex16>, match score: X)`
- AFTER:  file skills render `📋 **Skill: <name>** (id: pf:<ns>:<name>, match score: X)`
- DB path byte-unchanged (already renders the full uuid); segment layout unchanged; the `if skill_id:` omission branch (:1271/:1276-1279) preserved.
- `track_injection`/`_injected_skills` attribution (ruling R3: :460-465/:643-648) reads the id attribute DIRECTLY — untouched, stays pseudo-id, consistent with the usage table.

## 5. OUT / NO-CHANGE list (R2-OUT + ruled NO-CHANGE + routed)

| Surface | Receipt | Disposition |
|---|---|---|
| skill_list file rows | skill_tools.py:822-827 | **NO CHANGE** — already emits `fs.address`; byte-for-byte preserved |
| skill_view resolution (both branches) | :913-971, repository.py:241 | **NO CHANGE** — exact-PK DB + `lookup_explicit` file branch untouched |
| skill_fix response echo `[:8]` | skill_tools.py:1258 | **OUT — DEFER** (display hygiene lane): caller-held echo, cannot strand the agent |
| skill_feedback display echo `[:8]` | skill_tools.py:1489 | **OUT — DEFER** (same class; :1491-1492 already passes the FULL id — function unaffected) |
| skill_feedback dual-form routing | skill_tools.py:1449-1466 | **NO CHANGE — PRESERVED AS INVARIANT** (R2g): `pf:` OR `pf-` → `_feedback_pf_skill`; W5 fail-closed (:744-770 pins); metrics-only writes keyed by the RESOLVED pseudo-id; compatibility path for pre-fix transcripts. M6 pins it. |
| skill_fix `pf-` refusal gap | skill_tools.py:1211 | **OUT — ROUTED** (skill-evolution/fix-dispatch lane): prefix test on `ADDRESS_PREFIX` only → a `pf-<hex16>` falls through to `dispatch_fix` as a bogus DB id; harmless today (evolution lane disabled) and LESS reachable after this fix; belongs to the P7-refusal family (siblings :1201-1206: skill_analyze / skill_evolve / skill_get_metrics + REST POST /{id}/fix) which must move together |
| REST/FE fusion | routers/skills.py:282-308; skill_search_service.py:592-600 | **OUT — REPORT-ONLY** (parked v2): REST shares NOTHING with the tool projection (full uuid via `to_dict()`; REST search DB-only v1 — file corpus invisible at `instance_id=None`) — the dispatcher's "if it shares the projection" premise resolves to: it does not |
| `_render_pf_view` `- id: <pseudo-id>` line under `- address:` | skill_tools.py:404-405 (ruling receipt) | **NO CHANGE** (address leads; relabelling = shape churn on a surface the dispatcher did not name) |
| `ProjectFileSkill.to_summary()` `"id": pseudo-id` | model.py:162-183 | **NO CHANGE — planner observation O-1**: ZERO consumers tree-wide (grep daemon/ + tests/); dead surface, not an emission seam; recorded so future lanes don't mistake it for one |
| id minting/scheme (`uuid4()` PK default; `mint_pseudo_id`; `mint_address`) | models.py:161-165; model.py:68-84 | **NO CHANGE** |
| schema / migrations | — | **NO CHANGE** — zero `.sql`; migration census delta EMPTY expected at stage (standing rule: delta-empty-legitimate) |
| v2 loader itself, tool categories | — | **NO CHANGE** — no category registration changes ⇒ no `tool-categories.json` regen (v0.28.9 lesson) |

## 6. Planner observations from verification (additions, not redesigns)

- **O-1** — `to_summary()` dead surface (above).
- **O-2** — `_file_only_bm25_search:219` is the second `_pf_skill_payload` consumer (degraded file-only path) → covered by P1-3; the pin exercises it (adjudication A-6).
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

## 8. Deliverable map (this lane's planning set)

| File | Content |
|---|---|
| `plan-overview.md` | this file |
| `implementation-steps.md` | P1/P2/P3 ordered steps + the full P2 sweep enumeration (fold list + verified-unaffected corpus) |
| `pin-spec.md` | concretized pin per ruling R5 (harness, legs, mutations M1-M6(+M2b), RED/GREEN procedure) |
| `decisions.md` | binding rulings recorded, planner adjudications A-0..A-7, gate order |
| `risk-register.md` | R6 safety properties → dependency → guarding pin |

Gate order (binding, from the dispatcher): reviewer council → fresh approver → implementation → code council → tidier → tester functional → **stage (NO promote)**.

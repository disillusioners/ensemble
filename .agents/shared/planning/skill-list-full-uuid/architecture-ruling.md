# Architecture Ruling — wi-76d89e11: skill_list id-emission contract (skill-list-full-uuid)

Date: 2026-11-03
Ruling instance: architect (controller). Evidence verified read-only at
`/home/nea/ensemble-src-wt-skill-list-full-uuid` @ `c4161335b` (== origin/latest).
Every receipt below was independently re-verified at HEAD; dispatcher Phase-0
receipts 1–11 all CONFIRMED (line numbers re-checked; two had ±0 drift).

Status: **COMPLETE — rulings R1–R7 delivered.** No open questions requiring
user decision. Three findings routed out-of-scope (R7).

---

## R1 — ID-EMISSION CONTRACT (the ruling)

**The contract, per source:**

| Source | Agent-facing `id` value skill_view consumes VERBATIM | Receipts |
|---|---|---|
| **DB skill** | the FULL stored uuid4 (`Skill.id`, 36 chars, exactly as returned by `Skill.to_dict()["id"]`) | skill_tools.py:764-770 (renders `[:8]` today — the defect); skill_store_service.py:235-250 (metadata projection carries full id); skill/models.py:161-165 (PK = uuid4); repository.py:241 (`session.get(Skill, skill_id)` = EXACT PK); skill_tools.py:969 (view → view_skill) |
| **Project-file skill** | the ADDRESS `pf:<ns>:<name>` (`mint_address`, model.py:82) | skill_tools.py:924-926 (`is_pf_address` branch → `lookup_explicit`); model.py:60/:87 (prefix + strict parser) |

**Rejected forms (both directions):** `uuid4[:8]` (falls to DB branch → PK miss →
`ERROR: skill_view: no skill with id '<id>'`, skill_tools.py:971) and `pf-<hex16>`
pseudo-id (does not start `pf:` → DB branch → PK miss → same error).

**Ruling on "Do NOT change the id format itself":** making a project-file skill's
agent-facing `id` field carry the ADDRESS is **NOT a violation** — plainly:

- The binding clause protects the id **SCHEME/MINTING** (uuid4 PK for DB rows;
  `mint_pseudo_id` sha16 for internals). Both are untouched by any in-scope change.
- The dispatcher's own acceptance clause — "an id `skill_view` accepts verbatim" —
  is **unsatisfiable for file skills by any other value**: file skills HAVE no uuid
  (Phase-0 receipt 4; `project_skill_files` PK holds the pseudo-id, overwritten by
  the indexer at project_skill_source/service.py:1455/:1493), and the pseudo-id is
  *rejected* by skill_view. The address is the only verbatim-consumable form.
- `model.py:96-101` deliberately keeps the pseudo-id NON-uuid-shaped so DB tooling
  never misroutes it — surfacing the address as the agent-facing id *strengthens*
  that design intent (agents stop carrying a key that looks like an id but is not
  routable as one).

**Hard constraint (from R3):** the pseudo-id remains the INTERNAL key. The
`ProjectFileSkill.id` **attribute**, the DB rows, and all internal join keys are
NEVER remapped — only the rendered agent-facing string changes.

---

## R2 — SCOPE BOUNDARY (in / out, per surface)

| # | Surface | Receipts | Ruling | Why |
|---|---|---|---|---|
| (a) | skill_list DB truncation | skill_tools.py:765 (`skill_id[:8]`), :768-770 (bracket render) | **IN** | THE defect. Store-originated id, sole source of the id on this surface. One-token fix. |
| (b) | skill_create success line | skill_tools.py:1119-1125 (`created.id[:8]`) | **IN** | Store-originated id; the agent has NO other way to learn the new skill's id (the line even says "Use skill_search() to find it" — a workaround for its own truncation). One token. |
| (c) | skill_fix response line | skill_tools.py:1258 (`skill_id[:8]`) | **OUT — DEFER** (display hygiene lane) | It is an ECHO of a caller-supplied id. The agent already holds the full string in its own prior tool call; the truncation cannot strand it. Contract satisfied transitively. Changing it is 1 token but pure consistency; excluded to keep the diff surgical. Same ruling for skill_feedback's echo at :1489 (which passes the FULL id to the service at :1492 — function unaffected). |
| (d) | skill_search project-file `id` | skill_tools.py:141 (`_pf_skill_payload["id"]` = pseudo-id, used at :219 by `_file_only_bm25_search`) + the FUSED path: skill_tools.py:670 `json.dumps(..., default=_json_default)` → :83-99 falls to `__dict__` on `ProjectFileSkill` (dataclass, NO `to_dict` — model.py:108+), so the agent-visible JSON carries `"id": "pf-<hex16>"` with a sibling `address` key | **IN** | Store-originated id under the conventional `id` key — non-consumable. Two render seams (payload builder + dataclass serialization). Sibling `address` already exists at :150 and via `_tag_result_sources` (skill_search_service.py:1056-1105, entry-level stamp) — the fix re-keys, not re-shapes. |
| (e) | auto-injection file-skill body | skill_injection_service.py:1266-1281 (`_format_injection` id segment reads `getattr(skill, "id")` = pseudo-id); header carries the address at :788-794 | **IN** | The audit clause names injection text explicitly; the body's `(id: pf-<hex16>, ...)` segment is store-originated and non-consumable. Severity LOW (the address sits in the header of the SAME message — workaround in-band), but the clause is unconditional. One branch in the formatter, file-only. |
| (f) | skill_fix `pf-` refusal gap | skill_tools.py:1211 (prefix test on `ADDRESS_PREFIX` only) → pseudo-id falls through to `dispatch_fix` as a bogus DB id | **OUT — DEFER** (skill-evolution/fix-dispatch lane) | Routing gap, not id emission. Currently harmless: the evolution lane is disabled (v0.28.9 record — "skill_fix x3 recorded but NOT dispatched"), and after (d)/(e) land, agents stop seeing pseudo-ids at all, making the gap LESS reachable, not more. Correct fix is one token (`or skill_id.startswith(PSEUDO_ID_PREFIX)` → `_pf_refusal`) but it belongs to the P7-refusal family (mirrored on skill_analyze/skill_evolve/skill_get_metrics + REST POST /{id}/fix — :1201-1206), which should move together. |
| (g) | skill_feedback pseudo-id routing | skill_tools.py:1457-1465 (broad prefix `pf:` OR `pf-` → `_feedback_pf_skill`) | **NO CHANGE — PRESERVED AS INVARIANT** | Deliberate and load-bearing: W5 fail-closed validation (:557-571), metrics-only writes keyed by the RESOLVED pseudo-id (:583-607). It also keeps feedback working against ids quoted from pre-fix transcripts. Removing the `pf-` branch would be a regression, not a cleanup. Verified: the address form IS routable here (:524-538 resolves address → pseudo_id before any write). |
| (h) | REST/FE | routers/skills.py:282-308 (`_skill_to_dict` → `skill.to_dict()` = FULL uuid); REST search DB-only v1 (skill_search_service.py:592-600 instance_id=None ⇒ file corpus invisible) | **OUT** (parked v2) | REST shares NOTHING with the tool projection — it neither truncates nor emits pseudo-ids. No action possible or needed under this lane. |

**Also observed, ruled NO CHANGE:** `skill_view`'s own file document renders
BOTH `- address:` and `- id: <pseudo-id>` (skill_tools.py:404-405 in
`_render_pf_view`). The address LEADS (first metadata line); relabelling the
second line is shape churn on a surface the dispatcher did not name. Keep.
Likewise skill_search low-match rows carry no id for DB entries (by design,
receipt 7) — they are not an id-emission surface.

---

## R3 — COUPLING MAP (who keys on the pseudo-id)

| Consumer | Keys on | Receipts | Survives address-as-agent-id? |
|---|---|---|---|
| `project_skill_files.id` PK | `meta.pseudo_id` at index time | service.py:1455 (`meta_to_row`), :1493 (`_invalid_rows`) | ✅ Yes — indexed from the manifest, never from a rendered string |
| `project_skill_embeddings.skill_id` (NO FK, A1) | pseudo-id; gc joins :115/:173/:212, vector writes :237/:269, reads via `file_embeddings_for(skill_id)` called with the OBJECT's `.id` | models.py:152-165; repository.py:115/:173/:212/:237/:269; service.py:230-258; search consumer skill_search_service.py:1417-1425 | ✅ Yes — **IFF `ProjectFileSkill.id` attribute stays the pseudo-id.** This is the ONE unsafe rewrite: changing the attribute to the address silently misses every embedding row (keyed sha16) and degrades file-skill rerank to neutral 0.0. |
| `project_skill_usage_records.skill_id` | resolved `pseudo_id` | skill_tools.py:583-607 (only write site in the tree); models.py:187-209 | ✅ Yes — `_feedback_pf_skill` resolves address → pseudo-id BEFORE writing (:524-538); inputs in either form land on the same key |
| `track_injection` / `_injected_skills` attribution | `getattr(item["skill"], "id")` | skill_injection_service.py:460-465, :643-648 | ✅ Yes — collector line untouched by the formatter fix; stays pseudo-id, consistent with the usage table |
| skill_fix | prefix refusal on `pf:` | skill_tools.py:1211 | ✅ Yes — an address input gets the CORRECT P7 refusal (`_pf_refusal`); only `pf-` bypasses (gap deferred, R2f) |
| Evolution lane (disabled) | dispatch keyed by DB uuid; file skills excluded by P7 | skill_tools.py:1223-1244 | ✅ Yes — no path from a rendered address into evolution |
| `_feedback_pf_skill` input routing | accepts BOTH `pf:` and `pf-` | skill_tools.py:1457-1465, :478-607 | ✅ Yes — verified both directions; the fix must NOT narrow this |

**Ruling on the alternative (R3's question):** the dispatcher-safe shape is
**"pseudo-id = stable internal key, address = the agent-facing `id`"** — implemented
purely at the RENDER layer. Surfacing BOTH (id=address + additive `pseudo_id` key)
is the belt-and-suspenders variant and satisfies the acceptance clause without
touching any internal. The single UNSAFE alternative — remapping the
`ProjectFileSkill.id` ATTRIBUTE — is prohibited (embeddings + attribution breaks),
and must be pinned against (R5 M5).

---

## R4 — PAYLOAD PRESERVATION (value vs shape)

| Surface | Exact delta | Value change | Shape change |
|---|---|---|---|
| skill_list DB row | `- [{skill_id}] {name} — {cat} — {status}{db_suffix}` — KEEP the bracket form and every separator; the ONLY change is the token inside the brackets grows `uuid[:8]` → full uuid (28 chars) | ✅ the bracket token | ❌ none (line shape, separators, `db_suffix` logic all byte-preserved) |
| skill_list file rows | **UNCHANGED byte-for-byte** (`- [{ns}] {address} — {category} — v{version} — source: project-file`, :823-826) | — | — |
| skill_list headers/counts/invalid-rows sections | UNCHANGED | — | — |
| skill_create success | `created.id[:8]` → `created.id` in the rendered string | ✅ | ❌ none |
| skill_search file payload (`_pf_skill_payload`) | `"id": pseudo-id` → `"id": address`; ADD `"pseudo_id": <pf-hex16>` | ✅ `id` value | ➕ additive key only (house convention: `_tag_result_sources` "additive keys only… consumers that ignore unknown keys keep working" — skill_search_service.py:1066-1071) |
| skill_search fused-path serialization | add `to_dict()` to `ProjectFileSkill` returning the SAME field set as `__dict__` (every field present, nothing dropped) with `id`=address + `pseudo_id`=attribute; `_json_default` (:83-99) already prefers `to_dict()` | ✅ `id` value | ➕ additive `pseudo_id` key; **field-set must equal the dataclass fields — pin it** (a vanishing key would be real shape churn) |
| injection file body id segment | `(id: pf-<hex16>, match score: X)` → `(id: pf:ns:name, match score: X)` — one branch in `_format_injection` discriminated by the sanctioned duck-type `getattr(skill, "source", None) == "project-file"` (same test as skill_search_service.py:130) | ✅ | ❌ none (segment layout unchanged) |
| injection DB body / header / low-match rendering | UNCHANGED (DB already renders the full uuid at :1266-1281) | — | — |
| Everything else (REST, skill_view, skill_feedback, store service, repos, models, migrations) | UNCHANGED | — | — |

**Stale-expectation fold surface (quantified):** exactly ONE existing pin
hardcodes the truncated expectation — tests/tools/test_skill_tools_pf_surface.py:488
`legacy_line = "- [abcdef01] code-review — workflow — active"` against fixture
id `"abcdef0123456789"` (test_dbrows_tag_only_when_file_source_wired, :468).
The pin's PURPOSE is the ` — source: db` suffix additivity, not the truncation;
fold = update fixture/expected string (2 lines). Precedent: v0.28.5 R1
stale-expectation fold ruling.

---

## R5 — PIN SPEC (non-vacuous)

**Home:** new pytest file `tests/tools/test_skill_id_verbatim_contract.py`
(sibling of `test_skill_tools_pf_surface.py` — same harness idioms: `_make_manager`,
AsyncMock store, tmp_path+engine canon fixtures), + shell pack
`test/packs/skill_id_verbatim_contract_unit_test.sh` registered in
`.agents/tester/PACKS.md` with a DERIVED blast-radius note. House conventions:
RED-at-BASE proof, planted-mutation legs RED→GREEN with digests, vacuous-pin
guard (assert the RESOLVED skill is the intended one — never merely "no ERROR"),
and per the v0.28.9 lesson do not hand-edit generated artifacts (N/A here — no
category registration changes, so no tool-categories.json regen).

**The pin (D1 leg — the acceptance clause, executable):**
1. (i) Invoke `skill_list` over a fixture with BOTH sources wired: ≥1 DB row
   (known uuid4 fixture) and ≥1 indexed file skill.
2. (ii) Parse every DB row's bracket token and every file row's address token;
   feed EACH verbatim into `skill_view`.
3. (iii) Assert success AND identity: the returned document's `# <name>` header
   equals the intended fixture skill's name (not merely absence of `ERROR:`).
4. (iv) Assert the DB bracket token EQUALS the stored full uuid (str-equal vs
   the `Skill.id` fixture value, not a regex) and the file token EQUALS
   `mint_address(ns, name)`.
5. **RED-at-BASE:** at c4161335b, legs (iii)/(iv) for DB rows are RED
   (bracket token = 8 chars ≠ stored uuid → `skill_view` errors). File-row
   legs for skill_list are GREEN at BASE (address already emitted — pin them
   anyway as regression armor). Search-payload and injection-body legs are RED
   at BASE (pseudo-id under the `id` key). **GREEN-at-HEAD** after the diff.
6. **Mutation legs (planted, RED→GREEN, digest-proven):**
   - **M1** re-introduce `[:8]` at skill_tools.py:765 → D1 leg RED.
   - **M2** revert `_pf_skill_payload["id"]` to the pseudo-id → search leg RED.
   - **M3** revert the formatter branch (file body renders pseudo-id) → injection leg RED.
   - **M4** revert the skill_create line → create leg RED.
   - **M5 (safety invariant — pins the R3 hard constraint):** plant
     `ProjectFileSkill.id = address` (the UNSAFE rewrite) → embeddings invariant
     pin RED: assert `file_embeddings_for` lookups are keyed by the sha16 form
     (`pf-` + 16 hex) — i.e. assert the attribute keeps `mint_pseudo_id` shape,
     and that a rerank over an indexed fixture still consumes embedding rows.
   - **M6 (safety invariant — pins R2g):** remove the `PSEUDO_ID_PREFIX` branch
     at :1460-1463 → legacy-transcript feedback leg RED (a `pf-<hex16>` input
     from a pre-fix transcript must still record metrics).
7. **Fold:** update test_skill_tools_pf_surface.py:488 expected line + fixture
   id (2 lines) — stale expectation, doctrine-asserted.

---

## R6 — SAFETY PROPERTIES TO PRESERVE

| # | Property | Endangered by the diff? | Mitigation |
|---|---|---|---|
| 1 | skill_view exact-PK resolution unchanged (DB `session.get`; file `lookup_explicit`) | ❌ No — view branch untouched; only EMITTERS change | Pin (iii) exercises view through both branches |
| 2 | No change to id minting/scheme (uuid4 default; `mint_pseudo_id`; `mint_address`) | ❌ No — model mint functions untouched | M5 pins the mint-shape invariant |
| 3 | No schema change (zero `.sql`; no ALTER; the three project_skill tables untouched) | ❌ No — render-layer only | Migration census delta EMPTY expectation at stage (house standing rule: delta-empty-legitimate) |
| 4 | skill_feedback routing semantics unchanged (BOTH forms accepted, W5 fail-closed, metrics-only) | ⚠️ Plausibly — an implementer "cleaning up" the now-rarer `pf-` branch | M6 pins the branch as load-bearing; R2g marks it NO-CHANGE |
| 5 | Fail-closed behaviors intact (project-scope refusals, body-unreadable, P7 refusals) | ❌ No — untouched paths | Existing pf-surface pins already cover (test_skill_tools_pf_surface.py:348/:370/:561) |
| 6 | No REST change | ❌ No — tool layer only; REST file-blind | Assert REST untouched in the diff inventory |
| 7 | No live behavior change beyond the id string rendered to agents | ⚠️ Two plausible endangerments: (a) remapping the `.id` ATTRIBUTE (breaks embeddings rerank silently — the worst failure mode because it degrades, not errors); (b) `to_dict()` dropping a field the `__dict__` path used to emit (vanishing key = shape churn) | (a) M5; (b) pin field-set equality: `set(to_dict()) == {f.name for f in dataclasses.fields(ProjectFileSkill)}` + every value source identical except `id` |
| 8 | Byte-identity of all unaffected payload parts | ❌ No — R4 table keeps separators/suffix logic identical | The folded :488 pin continues to assert the suffix-additivity shape |

---

## R7 — SURPRISE REPORT (premise corrections + routed observations)

**Correction 1 — DB ids were never 8-hex.** The stored PK is a full uuid4
(skill/models.py:161-165); the 8-hex tokens in the defect brief (9e79ccad…) are
DISPLAY truncations in the tool layer (skill_list :765, skill_create :1119,
skill_fix :1258). Consequence: the DB-side fix is a pure RENDER fix — no
migration, no backfill, no data change.

**Correction 2 — file skills have no uuid at all.** Their agent-usable identifier
is the ADDRESS; the `pf-<hex16>` pseudo-id is a sha16 internal key that skill_view
REJECTS. "Return the full uuid" is unsatisfiable for this source; the corrected
contract is per-source (R1 table).

**Minimal corrected fix shape (the whole lane):**
1. skill_list DB bracket token → full uuid (1 token, skill_tools.py:765).
2. skill_create success line → full id (1 token, :1119-1125).
3. File-skill agent-facing `id` → address at exactly two render seams:
   `_pf_skill_payload` :141 (+additive `pseudo_id`) and `ProjectFileSkill.to_dict()`
   (new method; `_json_default` picks it up).
4. Injection file-body id segment → address (1 branch, `_format_injection`).
5. Pins (R5) + the 2-line fold (:488).
Total shipped-code delta: ~4 small sites in 3 files, zero schema, zero minting.

**Out-of-scope-but-observed (report to dispatcher, route separately):**
- **REST shares nothing** with the tool projection (full uuid via `to_dict()`,
  file-blind v1 — routers/skills.py:282-308, skill_search_service.py:592-600).
  The "FE/REST list if it shares the projection" premise: it does not. v2 fusion
  stays parked; nothing to do here.
- **skill_fix `pf-` refusal gap** (:1211): a pseudo-id falls through to
  `dispatch_fix` as a bogus DB id — a standing P7 bypass. Harmless today
  (evolution lane disabled) and LESS reachable after this fix; route to the
  skill-evolution/fix-dispatch lane with its three sibling refusal sites
  (:1201-1206 names them).
- **skill_feedback asymmetry is deliberate** (accepts `pf:` AND `pf-`,
  metrics-only, W5-fail-closed — :1450-1466). Do not "fix" it; it is the
  compatibility path for pre-fix transcripts.
- Minor: `_render_pf_view` renders `- id: <pseudo-id>` beneath `- address:`
  (address leads) — left unchanged; low-match rows carry no DB id by design.

**Deferred (routed):** (c) skill_fix/skill_feedback echo truncations — display
hygiene; (f) the `pf-` skill_fix gap; (h) REST/FE fusion (parked v2); the
`_render_pf_view` `- id:` line relabel. All four are non-blocking for the
acceptance clause.

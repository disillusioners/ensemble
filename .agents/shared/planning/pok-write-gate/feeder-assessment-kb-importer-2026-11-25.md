# Feeder Assessment — kb-importer `pok` grant (re-derivation, 2026-11-25)

**Mandate:** ASSESS-AND-REPORT ONLY. NO grant change, NO row move, NO mutation on
kb-importer, NO POK write. The report recommends; the Emperor decides.

**Lane:** sonic module B (kb-importer) of commission `feeder-grants-dump-health`.
**Branch:** `feature/sonic-kb-importer` @ base `9f464d726c14ea6d070f63ce017f329b1defb254`.
**Worktree:** `/home/nea/ensemble-src-wt-kb-importer`.
**Produced:** 2026-11-25T03:33Z (UTC).

**Fences honoured:** meta.json untouched (kb-importer still holds `pok`); live store
accessed via a `BEGIN READ ONLY` session only (SELECTs); no INSERT/UPDATE/DELETE/DDL;
ambient `POSTGRES_*` vars scrubbed per-variable; password/DSN never printed or recorded.

---

## 0. Snapshot discipline

Every figure below carries its source and snapshot time. Bare counts are never quoted.
- Live store: PostgreSQL `ensemble_prod` @ `10.44.0.2:5432`, user `ensemble` (password
  sourced from `/home/nea/agents-ensemble/.env`, never recorded). Measured
  2026-11-25T03:33Z via read-only session.
- Repo (skill_injection counts): worktree `feature/sonic-kb-importer` @ base
  `9f464d726`, measured 2026-11-25T03:33Z.
- Prior artifacts cited (not re-derived here): feeder assessment
  `.agents/shared/planning/pok-write-gate/feeder-assessment-2026-11-24.md` (2026-11-24),
  census `census-rows-active-2026-11-23.mech-labels.tsv` (2026-11-23 ~12:29Z).

---

## 1. Re-derived active-row set authored by kb-importer (ALL projects)

The census figure "kb-importer = 1 row" is a 2026-11-23 snapshot and is STALE. Live
re-derivation (2026-11-25) shows **5 active rows** across 2 projects (3 on
`83da04de…` agents-ensemble, 2 on `a5f20438…` dev project), plus 1 superseded row
(android-04-hints-and-longpress, re-upserted 2026-11-24). kb-importer also holds 6 total
rows lifetime (5 active + 1 superseded).

Query: `SELECT … FROM project_operating_knowledge WHERE status='active' AND
created_by_agent='kb-importer' ORDER BY created_at` (read-only). Full TSV:
`evidence-rederiv-2026-11-25/kb-importer-active-rows.tsv` (12 columns incl. body and
content_sha256; sha256 of body verified to equal the stored `content_sha256` for all 5).

| # | id | slug | domain | audience | source_tier | created_at (UTC) | access | verify | last_verified | project |
|---|---|---|---|---|---|---|---:|---:|---|---|
| 1 | 77bb3c69-bb35-42d3-b81e-5ff643847146 | agent-01-skill-injection-flag-scope | artifact | both | agent | 2026-11-09T08:09:51Z | 10 | 0 | 2026-11-09T08:09:51Z | 83da04de… |
| 2 | 71efa995-cabf-4347-adf3-4e2ee171377b | android-key-two-finger-early-commit | other | implementation | agent | 2026-11-22T17:08:46Z | 1 | 0 | 2026-11-22T17:08:46Z | a5f20438… |
| 3 | b751f550-217f-4fd2-a30a-df1f8a3040ec | plane-mcp-error-masking | other | operation | agent | 2026-11-24T04:33:30Z | 1 | 0 | 2026-11-24T04:33:30Z | 83da04de… |
| 4 | a6b26b93-4de7-464f-84e4-02624546b35a | mcp-reset-builtin-guard | other | operation | agent | 2026-11-24T04:33:30Z | 1 | 0 | 2026-11-24T04:33:30Z | 83da04de… |
| 5 | 3eda1767-d482-4975-85b0-e56869379363 | android-04-hints-and-longpress | artifact | both | agent | 2026-11-24T10:08:01Z | 2 | 0 | (null) | a5f20438… |

Note: access_count on row 1 is now 10 (the feeder assessment / brief cited 9; the
extra access occurred between 2026-11-24 and 2026-11-25). All 5 rows have
`verify_count = 0` (never verified via the verify path) — `last_verified` is stamped at
creation time, not via a verify call. `source_tier = agent` for all 5 (none curated).

Bodies (quoted — project knowledge; quoting is expected):

- **Row 1** (77bb…): "Agent meta.json skill_injection gates dynamic per-turn skill
  discovery; absent key = false (fail-closed, daemon/registry.py meta.get). 15 dirs true /
  11 logical agents; skill-keeper lacks the flag so its A/B group can never resolve."
  (sha256 `79c0594c…`)
- **Row 2** (71ef…): "Android KeyboardView: a second finger landing while a touch is
  mid-key commits the pending key early (KeyboardView.kt ~479 finish()), typing the
  previous char — a real cause of 'wrong character' on fast two-thumb typing."
  (sha256 `c2448274…`)
- **Row 3** (b751…): "Plane MCP Connection-closed error masks the real cause: unwrap the
  ExceptionGroup swallowed at DEBUG in stdio_wrapper.py:225; heal a corrupted builtin row
  via POST /api/mcp-servers/{id}/reset-builtin (no daemon restart)."
  (sha256 `6f70c179…`)
- **Row 4** (a6b2…): "reset-builtin on a builtin MCP row needs is_builtin=True (else
  403) and a resolver-permitted workspace; a refused resolution returns 403
  BUILTIN_SERVER_PROTECTED and writes nothing to the row." (sha256 `497d2223…`)
- **Row 5** (3eda…): "700ms comma=emoji; 700ms digit=shift symbol; 700ms q/w/r/t/p=1/2/4/5/0
  (no numberRow); 450ms=alternates popup (tracked); 400ms space=cursor; 450ms bksp=repeat;
  layer toggle touch-down, slide-off->LETTERS; holdFired resets need VISIBLE."
  (sha256 `a75f4618…`)

---

## 2. Per-row classification vs the Phase 9 routing map

Routing map (`.agents/shared/planning/pok-write-gate/feeder-assessment-2026-11-24.md` §4
and `CONTENT_CLASS_ROUTES` in `daemon/tools/pok.py`):

| content class | destination | grant category |
|---|---|---|
| knowledge (what something IS) | RAG (knowledge base) | `rag` |
| operational (this project's how-to / rule / trap) | POK (this project) | `pok` |
| governance (curated project contract agents must read) | critical notes or the board | `critical_notes`, `board` |
| episode (lane state, a dated count, a fingerprint) | the board or the shared KV | `board`, `shared_meta_kv` |

Per row:

- **Row 1 — operational (legit POK).** A project-specific rule/trap of this repo's
  daemon: skill_injection gating behaviour and its fail-closed default, plus the
  skill-keeper trap. It is NOT general knowledge (it is meaningless outside this repo)
  and NOT a pure dated count (the count is supporting evidence for the trap, not the
  payload). Classification: operational-how-to → POK is the correct home.
  - Caveat (episode-flavoured): the embedded "15 dirs true / 11 logical agents" is a
    dated snapshot. It is supporting evidence, not the fact's core; re-derived today
    (§4) and confirmed. If the Emperor wants the count removed from the row, that is a
    supersede (not done here — fence).
- **Row 2 — operational (legit POK).** A project-specific trap (Android KeyboardView
  two-finger commit) for the dev project `a5f20438`. Not general knowledge. → POK.
- **Row 3 — operational (legit POK).** A project-specific trap (Plane MCP
  connection-closed masking) with a heal procedure. → POK.
- **Row 4 — operational (legit POK).** A project-specific rule (reset-builtin guards).
  → POK.
- **Row 5 — operational (legit POK).** A project-specific configuration/timing table
  for an Android keyboard. → POK.

**Result: 5/5 active rows classify as operational → POK is the correct destination.**
None is a pure knowledge/episode/governance row; none is MISPLACED under the routing
map. (Row 1 carries an episode-flavoured snapshot inside an operational fact — flagged,
not reclassified.)

---

## 3. The named row (77bb3c69…) — verified live

- id: `77bb3c69-bb35-42d3-b81e-5ff643847146`
- slug: `agent-01-skill-injection-flag-scope`
- domain: `artifact`, audience: `both`, source_tier: `agent`
- created_by_agent: `kb-importer`, created: `2026-11-09T08:09:51Z`
- access_count: **10** (brief/commission cited 9; +1 access between 2026-11-24 and
  2026-11-25)
- verify_count: **0** (never verified via the verify path) — confirms "NEVER_VERIFIED"
- last_verified: `2026-11-09T08:09:51Z` (stamped at creation, not via a verify call)
- body sha256: `79c0594cfc4cb7dbebf1df7978601954b9080199257bd47253203882c5d90499`
  (matches the commission's quoted body byte-for-byte, incl. "15 dirs true / 11
  logical agents; skill-keeper lacks the flag…")

The row is unchanged in substance since the commission; only `access_count` drifted +1.

---

## 4. Staleness re-derivation of the named row's figures

Figures in the row: "15 dirs true / 11 logical agents" (skill_injection flag coverage),
dated 2026-11-09, row never verified.

Re-derived read-only from the repo (worktree `feature/sonic-kb-importer` @ base
`9f464d726`), measured 2026-11-25T03:33Z:

Method: for every `agents/*/meta.json`, read `skill_injection`; count `True` (dirs) and
distinct logical agents (base name with `[v2]` suffix stripped).

- `skill_injection == true`: **15 dirs**
  (aegis, approver, approver[v2], architect, critic, doc-maintainer, maintenancer,
  planner, planner[v2], reviewer, reviewer[v2], sketcher, tidier, tidier[v2], worker)
- distinct logical agents among them: **11**
  (aegis, approver, architect, critic, doc-maintainer, maintenancer, planner, reviewer,
  sketcher, tidier, worker)
- `skill_injection == false`: 19 dirs
- `skill_injection` absent (fail-closed, treated as false): 21 dirs, including
  `skill-keeper` (confirms "skill-keeper lacks the flag so its A/B group can never
  resolve")
- total agent dirs (excl. underscore templates): 53; distinct logical agents: 48

**Verdict: CONFIRMED.** Today's numbers (15 dirs true / 11 logical agents, skill-keeper
lacking the flag) match the row's 2026-11-09 figures exactly. The row's snapshot is not
stale as of 2026-11-25. (This confirms the invariant; it does not make the row
"verified" — verify_count remains 0 and no verify call was made, per fence.)

---

## 5. Disposition (BINDING)

**Recommendation: KEEP the `pok` grant on kb-importer**, and document the reason in the
plan-set doc (this file) and the handoff — NOT as a POK row.

Reasoning (from the evidence above):

1. All 5 of kb-importer's active rows classify as operational (§2) — i.e. they are
   project-specific how-to / rule / trap content, which is exactly what POK exists to
   hold. The grant is therefore exercised for legitimate operational content, not for
   general knowledge (which would belong in RAG) and not for lane-state snapshots (which
   would belong on the board / shared KV). Removing the grant would not remove the need;
   it would only force the content elsewhere or drop it.
2. The named row's only "episode" element (the 15/11 count) is supporting evidence for
   an operational trap and is confirmed current (§4); it is not a pure dated count.
3. The grant's *silent* posture (0 prompt mentions in kb-importer's `.md`/`.yaml`,
   per feeder assessment §1) is a documentation gap, not an evidence gap: the content
   is genuinely operational. The fix for the silent grant is to document the capture
   duty in the operational agents' prompts (the same rail as the kb-writer rider) —
   not to remove a grant that is backing live, legitimate rows.
4. kb-importer does dual-write (feeder assessment §3.2, 4 day-rows in the 3-day
   telemetry window). The named row and rows 3–4 are operational traps whose RAG
   counterparts, if any, are separate concerns; the dual-write volume is low (5 active
   rows lifetime) and the mechanism is not the same-second shape seen for kb-writer.
   The grant stays; the capture-duty guidance is what needs to move.

**Why the plan set is the house-correct home for the reason, and NOT a POK row:**

- A POK write (`pok_upsert`) is a unilateral live-store mutation. This module's fence
  (and the commission's) explicitly forbids any POK write / row move / archive /
  dispute / supersede. Writing the justification into POK would violate the fence and
  would itself be the kind of unreviewed store mutation the write gate exists to
  prevent.
- A POK row's `reason` field is REQUIRED and must name the operational invariant the
  fact protects — it is not a place for disposition rationale or grant decisions. A
  disposition rationale is governance (a curated project contract), whose home under
  the routing map is the critical notes or the board — or, for a commission's working
  record, the plan-set doc. This file IS the plan-set doc.
- The documented reason therefore lives here and in the handoff, where the Emperor can
  read it without any store mutation. If the Emperor wants the grant removed, that is
  a sanctioned change riding the same rail as the kb-writer rider (grant census pin
  `tests/unit/test_pok_grant_pins.py` + resolved-tool-set re-pin + prompt guidance),
  and is out of scope for this assessment.

**If the Emperor instead reads the evidence as MISPLACED** (i.e. treats the named row
or the 5 rows as knowledge/episode rather than operational), the recommended removal
+ routing would be:

- Remove the `pok` category from `agents/kb-importer/meta.json` `tools.allow` (the only
  change to meta.json; out of scope here).
- Row routing per the map:
  - Row 1 (skill_injection scope): if treated as knowledge → RAG
    (`rag_insert_text`); if the count is treated as episode → board / shared KV. The
    invariant part would still need a home; recommend superseding with a tighter
    operational body, not deleting.
  - Rows 2, 5 (Android keyboard): operational traps for the dev project → stay POK
    (would need another author or the grant retained).
  - Rows 3, 4 (Plane MCP): operational traps → stay POK.
- No row is moved/archived/superseded by this assessment (fence); any such action is
  the Emperor's sanctioned follow-up.

---

## 6. Provenance

| artifact | carries |
|---|---|
| `evidence-rederiv-2026-11-25/kb-importer-active-rows.tsv` | the 5 active rows, verbatim bodies + content_sha256 (sha256 of body verified equal to stored content_sha256 for all 5) |
| `feeder-assessment-2026-11-24.md` | grant posture, write volume, dual-write telemetry (cited, not re-derived) |
| `census-rows-active-2026-11-23.mech-labels.tsv` (sha256 `6737281c…`) | the stale "1 row" census figure (superseded by §1's 5 rows) |
| live store (read-only, 2026-11-25T03:33Z) | row set, bodies, counts |
| repo `agents/*/meta.json` (2026-11-25T03:33Z) | skill_injection counts (§4) |

## 7. What was NOT verified (limits)

- No verify call was made on any row (verify_count stays 0) — by fence, no POK
  mutation of any kind.
- Telemetry (tool_usage_daily) was cited from the 2026-11-24 feeder assessment, not
  re-queried; the 3-day retention window means the dual-write evidence is a lower
  bound.
- RAG overlap for kb-importer's rows was not re-derived (the concurrent lane's
  2026-11-24 method exposed only content summaries; see feeder assessment §3.1).
- The superseded row (b5bc…, 2026-11-23) was noted but not re-classified; its
  successor is active row 5.
- No test suite was run (this module has no code change; the deliverable is docs).

# POK Funnel & Label-Agreement — Independent Re-derivation

**Lane:** pok-write-gate, Maintenancer child (READ-ONLY evidence re-derivation).
**Deliverable:** `.agents/shared/planning/pok-write-gate/evidence-rederiv-2026-11-23/funnel-and-agreement.md`
**Snapshot window:** 2026-11-24T02:44Z–2026-11-24T03:25Z (this lane's own DB reads; Figure 1 block is the Maintenancer's 2026-11-24T01:53Z–02:05Z window, pasted verbatim).
**Method:** source-grep over `/home/nea/ensemble-src` (read-only) + SELECT-only SQL against `ensemble_prod` (`PGOPTIONS='-c default_transaction_read_only=on'` on every invocation) + joins over persisted local TSVs.
**Writes:** exactly one file — this one. No DB writes, no `pok_upsert`/`pok_curate`, no git commits, no writes under `/home/nea/ensemble-src`.

---

## FIGURE 1 — Funnel (active → retrieved → injected → cited)

### 1.1 The Maintenancer's measured block (VERBATIM — numbers unmodified)

```
Project: 83da04de-a410-4fb5-9e92-251a99d28a52 (agents-ensemble). Seat: Maintenancer ens_db_postgres_select (SELECT-only) against ensemble_prod. Snapshot window: 2026-11-24T01:53Z–02:05Z (daemon clock). Method: direct SQL on live tables; slug matching by exact kebab-token extraction — regexp '([a-z0-9]+(?:-[a-z0-9]+){1,})' over text bodies, INNER JOIN on token = slug (no substring matching; maximal-run extraction means a slug embedded in a longer kebab run is NOT counted).
- STAGE 1 — active rows: 568. Table project_operating_knowledge, filter project_id + status='active'. Full census: 759 rows total = 568 active + 132 superseded + 59 archived; 0 active pinned. NOTE: relayed claim said 499 (the 2026-11-23 13:5xZ census); the store has grown +69 active since.
- STAGE 2 — rows ever retrieved: 431 of 568 (137 never accessed). Columns access_count (>0) OR last_accessed_at (NOT NULL); the two signals agree exactly (431 by count = 431 by stamp). LIMIT: semantics of access_count (query-only vs query+injection) — 13 of 14 slugs found in injected blocks DO carry stamps while 1 (wtgate-01-isolation-ladder-decides) has access_count=0 despite block presence, consistent with injection NOT bumping access_count; source grep (TASK A) is the ground truth — record its answer here.
- STAGE 3 — rows ever injected into views: 14 distinct active slugs (LOWER BOUND). Source: event table, kind='message_received' AND data ILIKE '%Project Operating Knowledge%' → 36 POK-block delivery events, window 2026-08-24T19:11Z → 2026-11-24T01:59Z. The 14: agentic-bridge-01-four-agent-model-repin, bind-mut-01-scratch-wt-py-resolve, bump-01-preflight-preconditions, disk-03-tag-lineage-unmerged-stage-trees, engine-posture-01-use-engine-flags-truthful, model-vocabulary-01-canonical-aliases, scheduling-01-row-delivery, views-fix10-forwarded-veto-contract, views-origin-gate-contract, views-origin-parser-nonbrowser-spellings, views-verify-01-limiter-gate-pacing, worktree-01-venv-branch-binding, worktree-02-python-version-divergence, wtgate-01-isolation-ladder-decides. LIMITS: (a) injected blocks are truncated in the transcript ("…N more entries not shown") so only the displayed entries are textually present; (b) only deliveries recorded as message_received events are visible — injection paths that bypass message events are invisible; (c) event-table retention starts 2026-08-24 — anything older is gone; (d) live daemon logs add view-level delivery lines ("[POK] operating-knowledge block delivered for <iid> — once-flag (pok_injected) written") but log rotation keeps only ~86 minutes, and no pok_injected key exists in shared_context_metadata (verified by query). VERDICT: MEASURED-AS-LOWER-BOUND.
- STAGE 4 — rows ever cited in output: 518 of 568 active slugs appear as exact kebab tokens in report_injections.content; report-level: 156 of 1,412 project-scoped reports (child instance in project) cite >=1 slug; fleet-wide over all 17,312 reports = same 518 (all citation happens inside this project's report population). Corpus window 2026-08-24T19:00Z → 2026-11-24T01:59Z. LIMITS (load-bearing): slug-mention != knowledge-informed use — enumeration-style reports (POK censuses, cut lists, sweep evidence TSVs quoted into reports) inflate this count wholesale; the relayed "59 across 175 reports" is NOT reproducible without the original population definition (its report-level leg ~156-175 matches this re-derivation's 156, suggesting same shape, different snapshot). VERDICT: MEASURED (definition-laden; row-level 518 and report-level 156 both reported).
END FIGURE 1 BLOCK.
```

### 1.2 TASK A — SOURCE SEMANTICS (the STAGE 2 limit's ground truth)

**Answer:** `access_count` is **query-only**. The injection path never increments it.

| Fact | Evidence (file:line) |
|---|---|
| The ONLY increment of `access_count` in the POK table | `daemon/repositories/pok/repository.py:232` `def bump_access(self, entry_ids, *, now=None) -> int:` → `daemon/repositories/pok/repository.py:247` `row.access_count = (row.access_count or 0) + 1` (docstring `:233` = "Query-path bookkeeping: access_count += 1, last_accessed_at.") |
| The ONLY caller of `bump_access` | `daemon/services/pok_service.py:677` `self._repo.bump_access([e.id for e in outcome.entries])` — inside the read/query path, under the comment `:675-676` `# access bookkeeping (best-effort; never blocks the read).` |
| The injection path only READS `access_count` (for ranking) | `daemon/services/pok_injection.py:198` `-(row.access_count or 0),` inside `_deterministic_rank` (`:186-203`); the docstring `:191` states "then access mass (access_count, then verify_count, both DESC)". No increment, no `bump_access` call. |
| No other increment site | `grep -rn "access_count" daemon/ --include=*.py | grep -v test` returns 10 hits; the only other write-shaped one is `daemon/repositories/memoir/repository.py:442` `SET last_accessed_at = :now, access_count = access_count + 1` — that is the **MEMOIR** table, not POK. `grep -rn "bump_access" daemon/` returns exactly 2 hits (the def + the one caller). |

⇒ **The Stage 2 inference is CONFIRMED by source**: `wtgate-01-isolation-ladder-decides` showing `access_count=0` despite block presence is *expected*, not an anomaly — injection never bumps access_count.

**`pok_injected` flag store:** the **`instances` table's `metadata` JSONB column**, key `"pok_injected"`.

- Declared: `daemon/services/pok_injection.py:113` `POK_INJECTION_FLAG = "pok_injected"` (`:110-112`: "The once-per-instance metadata key. Written by the CONSUMER … The builder NEVER writes it.").
- Written: `daemon/services/instance_messaging.py:4986-4994` → `self._manager._instance_repository.set_metadata(instance_id, POK_INJECTION_FLAG, True)`.
- Store: `daemon/repositories/instance/repository.py:2990` `def set_metadata(self, instance_id, key, value, session=None)` → dialect-aware `jsonb_set(COALESCE(metadata,'{}'::jsonb), …)` on `daemon/repositories/instance/models.py:64-66` `instance_metadata: dict[str, Any] = Field(sa_column=Column("metadata", JSONBType))`.
- Read back: `daemon/services/context_messages.py:810` `_read_instance_metadata, repo, instance_id, POK_INJECTION_FLAG` (fresh read each assembly → revive-safe).
- **Empirical confirmation (this lane, SELECT-only):** `instances` rows with `metadata ? 'pok_injected'` = **2402** (all `"true"`); `shared_context_metadata` rows with `meta_key='pok_injected'` = **0**. ⇒ Stage 3 limit (d)'s "no pok_injected key exists in shared_context_metadata" is CORRECT, and the real home is `instances.metadata`.

**`operating-knowledge block delivered` log line origin:** `daemon/services/instance_messaging.py:5002` — `logger.info(f"[POK] operating-knowledge block delivered for {instance_id[:8]}... — once-flag ({POK_INJECTION_FLAG}) written")`, emitted inside the `try:` that writes the once-flag (`:4985-5006`), immediately after the `set_metadata` call, guarded by `if pok_block_present(_persistent_msgs):` (`:4994`).

**Block persistence sites** (answers Stage 3 limit (b) empirically):

| # | Site | Kind | Evidence |
|---|---|---|---|
| 1 | `event` table, `kind='message_received'` | **NOT the block.** The event payload records the raw inbound message text, NOT the agent's assembled context. | Of 37 events matching `data ILIKE '%Project Operating Knowledge%'` in the Maintenancer's window, **0** carry the block's own header `## Project Operating Knowledge`, and only **1** carries the `[SYSTEM CONTEXT: Project Operating Knowledge]` wrapper — and that one is a *dispatch/report whose prose quotes the phrase*. 0 of 7,481 `message_received` events contain an injected slug line. |
| 2 | `checkpoint_blobs`, `channel='messages'` (msgpack) | **YES — the real injection surface.** The block is a separate HumanMessage with stable id `pok:{project_id}` (`daemon/services/context_messages.py:145-152`), flows into graph state (`daemon/services/instance_messaging.py:4889,4923-4924`), and is checkpointed. | 38,406 blobs across **1,437 distinct threads** carry the literal header `[SYSTEM CONTEXT: Project Operating Knowledge]`; a per-thread extract shows the full block (`## Project Operating Knowledge` + `- <slug> [domain\|audience]: body` lines). |
| 3 | `instances.metadata` JSONB (`pok_injected` = true) | Presence evidence (once-flag), not block content. | 2402 rows (above). |
| 4 | `project_operating_knowledge` | **NO** — the injection path is a pure read/render; no write to the store. | `daemon/services/pok_injection.py` has no repository write call. |
| 5 | `shared_context_metadata` | **NO** | 0 rows for `pok_injected` (queried). |
| 6 | `report_injections.content` | **NO injected block.** 64 rows match `%Project Operating Knowledge%`, but they are *reports that talk about* the POK subsystem (e.g. "the POK injection path is a pure, side-effect-free render pipeline"), not delivered blocks. `grep -rn "pok\|operating_knowledge" daemon/repositories/report_injection/*.py daemon/services/report*.py` → 0 hits. | 64 / 17,337 rows (queried). |

**[SOURCE-SEMANTICS: access_count=query-only, evidence daemon/repositories/pok/repository.py:232+247 (sole increment) called only from daemon/services/pok_service.py:677 (sole caller); injection path only READS it at daemon/services/pok_injection.py:198; pok_injected_flag_store=instances.metadata JSONB key 'pok_injected' (daemon/services/pok_injection.py:113 → daemon/services/instance_messaging.py:4994 → daemon/repositories/instance/repository.py:2990 → daemon/repositories/instance/models.py:64-66; 2402 rows; shared_context_metadata=0), file:line as cited; block_persistence=checkpoint_blobs channel='messages' msgpack (38,406 blobs / 1,437 threads — the ONLY genuine block store) + instances.metadata once-flag; NOT event.message_received (0 of 37 carry the block header; phrase-match contamination), NOT project_operating_knowledge, NOT shared_context_metadata, NOT report_injections (64 prose mentions, 0 blocks)]**

### 1.3 Independent re-measurement of Figure 1 (this lane, SELECT-only, 2026-11-24T02:44Z–03:25Z)

| Leg | Maintenancer (01:53–02:05Z) | This lane (02:44–03:25Z) | Note |
|---|---|---|---|
| STAGE 1 — active rows | 568 | **573** | +5 in ~40 min; store still being written |
| STAGE 2 — ever retrieved (`access_count>0`) | 431 | **431** | **exact reproduction** |
| STAGE 2 — never accessed | 137 | **142** | = 137 + the 5 new rows (all zero-access) |
| STAGE 3 — ever injected (active slugs) | 14 | **384** (see below) | Stage 3 method corrected; see 1.4 |

### 1.4 CORRECTION to Figure 1 STAGE 3 — the event-table leg does not measure injection

The Stage 3 predicate `kind='message_received' AND data ILIKE '%Project Operating Knowledge%'` **reproduces the Maintenancer's count of 36/37 exactly** (37 total; 36 within their window ending 01:59Z; the 37th is 2026-11-24T02:04:03Z) — but it does **not** count POK-block deliveries:

1. The `event.data` payload for a `message_received` event holds the **raw inbound message text**, not the agent's assembled context. The injected block is a *separate* HumanMessage (stable id `pok:{project_id}`, `context_kind="pok"`) added to graph state at `daemon/services/instance_messaging.py:4889` — it never passes through the event write path.
2. Of the 37 matching events: **0** carry the block's own header `## Project Operating Knowledge`; **1** carries the `[SYSTEM CONTEXT: Project Operating Knowledge]` wrapper — and that one is a *dispatch prompt/report that quotes the phrase in prose*. The event at 2026-11-24T02:04:03Z (instance `6b30ed1d`) is the Maintenancer's own dispatch: its match comes from the prompt quoting the SQL predicate and the 14-slug list, not from a block.
3. **0 of 7,481 `message_received` events** contain an injected slug line (e.g. `docs-anchor-01-base-object-pinning`).

**Re-measurement from the true injection surface (`checkpoint_blobs`, `channel='messages'`):**

- Bytea `position()` scan for the header `[SYSTEM CONTEXT: Project Operating Knowledge]`: **38,406 blobs / 1,437 distinct threads**.
- Latest block per thread parsed for `- <slug> [<domain>|<audience>]:` lines → **580 distinct slugs**; **384 of them are ACTIVE slugs** (distinct join, no row multiplication) and 502 exist in `project_operating_knowledge` at any status (179 parsed tokens are not slugs — regex over-eshoots into body text).
- Of the Maintenancer's 14 Stage-3 slugs, **12 appear in the checkpoint-derived set**; missing: `views-origin-gate-contract`, `views-origin-parser-nonbrowser-spellings` (injected outside the checkpoint window or on pruned threads).
- Checkpoint coverage window: `checkpoints` spans **2026-10-26T04:18:11Z → 2026-11-24T03:14:36Z** (29 days) — this is the window over which the 384 lower bound holds.
- The extraction takes the LATEST block per thread, so the figure is a **lower bound** (earlier block versions may carry further slugs).

**FIGURE 1 VERDICT (stages 1, 2, 4 as pasted): MEASURED. STAGE 3 as pasted (14 slugs / "36 POK-block delivery events"): MEASURED-AS-LOWER-BOUND but the *measurement's meaning* is UNSOUND — re-measured on the true surface (checkpoint store): 384 distinct active slugs ever injected over 2026-10-26→2026-11-24, i.e. ~27× the pasted figure, and the 36 "delivery events" are phrase-matches on prose, not deliveries.**

---

## FIGURE 2 — Hand-label vs lexical/mechanical agreement on the borderline band

### 2.1 Sources inspected (all read-only; sha256 recorded)

**Worktree evidence dir** — `/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/`

| file | sha256 | bytes |
|---|---|---|
| `census-mechanics-2026-11-23.md` | `b819401e4b5a0017423d0144143ba6c3effeef3761eea4013820b82c0d4eaf52` | 36,214 |
| `census-rows-active-2026-11-23.mech-labels.tsv` | `6737281cfc7eddfb2f82a1af3317c1560ca465225162b503292a2ddd5d075ab1` | 256,252 |
| `census-rows-active-2026-11-23.tsv` | `b1f5842d60031cc165a5045bf3817101cb745c840b8d5ad2247ea287673c9b59` | 203,858 |

**Canonical sweep dir (READ-ONLY)** — `/home/nea/ensemble-src/.agents/shared/planning/pok-sweep/`

| file | sha256 |
|---|---|
| `cutlist-gap-2026-11-23.md` | `afd009dd1dbc92ce5b57a33d479b73f7a6578758a4320fa730e8a1679528c969` |
| `cutlist-gap-2026-11-23.borderline.tsv` | `50a7ea5d7103d194589cdaff213cea3ef80ffc5c4dd45be4e3a77497d289e88f` |
| `cutlist-gap-2026-11-23.tail.tsv` | `69e2d5dec9512a7837d529ba9f43c71a5eafc53797839fa27e69819cba910df6` |
| `cutlist-gap-2026-11-23.nohome.tsv` | `d386e17eeb439acd2be0ff0fe8b19425bd9105e19eb143e065d82efe45d2c0aa` |
| `cutlist-gap-2026-11-23.neverretrieved.tsv` | `ad2a8e2d10d6c1bd9b42f4bf1afb209932410b7ae263122c47c92fdf97aa714d` |
| `census-active-2026-11-18.json` | `c99d9df3bb296fc099f0d1bfbbac04695744df36505914548358ec562e5d58ef` |
| `census-active-2026-11-18.md` | `03959b07c1454e841a05564bf5f02ccd902ddbd7c90829c1d5c4c1f0d651934e` |
| `census-active-2026-11-23.md` | `07c2d6f6ba5322aa5be447f2a8e2af466e7f775df556becbc1371331ba423e25` |

### 2.2 Do the worktree TSVs carry hand labels? — **NO**

`census-rows-active-2026-11-23.mech-labels.tsv` header (19 columns, exact):

```
slug  id  family_key  domain  audience  source_tier  created_at  updated_at
last_verified  last_accessed_at  access_count  verify_count  pinned  disputed
superseded_by_id  created_by_agent  body_len  body_verbatim_escaped  mech_labels
```

`census-rows-active-2026-11-23.tsv` has **no header row** (raw 499 data rows, 19 fields each, same column order).

There is **no** `hand_label`, `sem_label`, `semantic`, `reviewer`, `final_label`, `adjudicated`, or equivalent column anywhere in the worktree TSVs. The only label-bearing field is `mech_labels` — a `;`-joined set of **mechanical/lexical** tags: `ROLLING:<token>`, `NOFAM`, `ZERO_ACCESS`, `FAMILY_AT_CAP`, `FAMILY_OVERCAP`, `DUPSLUG:<n>`, `NEVER_VERIFIED`. Distribution over the 499 rows (top): `NEVER_VERIFIED` 110; `NOFAM;NEVER_VERIFIED` 96; `FAMILY_AT_CAP;NEVER_VERIFIED` 60; `ZERO_ACCESS;NEVER_VERIFIED` 34; …

### 2.3 Hand-label source hunt (canonical sweep dir + DB history)

The relayed hand labels are the **2026-11-23 08:32Z "wanderer pass 2" semantic pass**: 512/512 rows classified `O`/`G`/`R` = 448/26/38, condemning a 64-row tail.

**The per-row names of that pass were never persisted.** Authoritative statement in the repo — `/home/nea/ensemble-src/.agents/shared/planning/pok-sweep/cutlist-gap-2026-11-23.md:50`:

> "The 08:32Z semantic pass counted **26 G + 38 R = 64** of 512 (12.5%). **Its per-row names were never persisted** (counts only in history 2bf04468) — so this lane RE-DERIVED the tail independently from the local TSVs with a strict, documented criterion, then reconciled"

I fetched that history row directly (SELECT-only) — `project_history.id='2bf04468-fdab-448c-84a2-201ccd2c62e3'`, created `2026-11-23 12:42:33.870981`, `entry_type='milestone'`, `source_agent='leader'`:

- `summary` (varchar(300), **truncated mid-sentence**): "…(19/26 G rows carry a repo handle; 41 legit O rows c"
- `details` (varchar(5000)): the full narrative — **aggregate counts only** (19/26 G rows carry a handle; 41 O rows carry none; handle rule accepts 460 / rejects 52; rolling regex flags 106 of which 15 are genuinely rolling; 23 of 38 true R escape the regex; G∩TOPICAL 16 / R∩TOPICAL 25; R5 no-home 1 G + 10 R) plus **partial** named lists (6 of the 41 no-handle O rows; 4 named G-band rows). `entry_metadata` is `null`.

⇒ **No per-row artifact exists for the 64-row semantic band**, in the tree or in the DB. Any per-row agreement against that band is therefore **UNBACKED-NO-SOURCE**.

**What DOES exist per-row** (hand-labelled, persisted):

| source | rows | label column |
|---|---|---|
| `cutlist-gap-2026-11-23.tail.tsv` | 50 | `class` ∈ {R, G} (25 R + 25 G) — hand re-derivation, strict criterion |
| `cutlist-gap-2026-11-23.borderline.tsv` | 7 classes | `rule_separating_from_clean` carries **named** hand-judged sets: C1b (9 slugs), C2b (7 slugs), C5 (2 slugs) |
| `cutlist-gap-2026-11-23.nohome.tsv` | 32 | class R/G + destination |
| `cutlist-gap-2026-11-23.neverretrieved.tsv` | 109 | verdict classes (rescuable / dead / g-class / badly-worded / mis-slugged) |

### 2.4 Measured agreements (exact predicates, exact commands)

**Join key:** `slug`. **Mechanical axis:** `mech_labels` contains `ROLLING:` (the only lexical detector that discriminates R vs G — `cutlist-gap-2026-11-23.md` §7: "my 25 G rows carry ZERO mechanical tokens — no lexical test can catch them; they are judge-only by construction").

**Predicate (stated once, used for both bands):**
> A hand-labelled row **AGREES** with the lexical axis iff `ROLLING:` is present for a hand-R row and absent for a hand-G row. Disagreement = the complement. The C5 near-dup class is excluded from the R/G axis (it is a redundancy class, not an admissibility class) and reported separately.

**BAND B — the persisted hand-judged borderline band (n=18 named rows).**

| sub-band | hand judgment | n | mechanical result |
|---|---|---|---|
| C1b borderline-R | R-leaning (dated-incident, kernel-vs-state arguable) | 9 | ROLLING present in **8**; absent in 1 (`supervise-01-launcher-loop-is-the-respawner`) → 1 lexical MISS |
| C2b borderline-G | G-leaning (methodology, light project scent) | 7 | ROLLING absent in **5**; present in 2 (`redclaim-01-rederive-mutation-provenance`, `verify-merged-leg-byte-equivalence`) → 2 lexical FALSE-POSITIVES |
| C5 near-dup pair | body-equivalent, distinct slugs | 2 | `DUPSLUG:` fires on **neither** → the mechanical label set cannot express this class at all (0/2) |

> **Band B agreement (R/G axis) = 13/16 = 81.2%** (disagreement 3/16). All 18 rows joined cleanly to the 499-row mechanical TSV (0 missing).

**BAND C — the persisted hand-classified tail (n=50).**

| measure | value | detail |
|---|---|---|
| hand-R rows | 25 | ROLLING present in **20** → 5 lexical misses: `engine-boot-slo-staleness-rule-absent`, `release-journal-pack-01-run-fence`, `test-pack-01-embed-consumers-drift-pin`, `engine-jobs-due-ness-shared-dev-db`, `worktree-fence-pg-role-is-dev-app` |
| hand-G rows | 25 | ROLLING absent in **22** → 3 lexical false-positives: `parallel-01-write-then-verify`, `craft-01-exception-swallow-reachability`, `closure-01-char-level-verify` |
| **agreement** | **42/50 = 84.0%** | disagreement 8/50 |

**Reproductions (this lane's independent computation vs the lane's own claims):**
- `ROLLING:`-flagged rows over the 499-row mechanical TSV = **92** — matches `cutlist-gap-2026-11-23.md` §7 "the mechanical rolling regex (R3's evidence source) flags 92 rows" ✓
- hand-R recall = 20/25 → "it also MISSES 5 of my 25 true-R" ✓
- **DIVERGENCE:** §7 also claims "my 25 G rows carry ZERO mechanical tokens". Measured: **3 of the 25 hand-G rows DO carry a `ROLLING:` token**. The §7 sentence is true only if "mechanical tokens" excludes the rolling axis; as written it is over-broad.

**Exact commands (reproducible):**

```bash
# BAND B/C join + agreement  (hand labels -> mechanical labels, on slug)
cd /home/nea/ensemble-src && python3 - <<'PY'
import re,csv
W='/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/'
S='/home/nea/ensemble-src/.agents/shared/planning/pok-sweep/'
mech={}
with open(W+'census-rows-active-2026-11-23.mech-labels.tsv') as f:
    r=csv.reader(f,delimiter='\t'); next(r)
    for row in r:
        if len(row)>=19: mech[row[0]]=row[18]          # field 19 = mech_labels
hand={}
with open(S+'cutlist-gap-2026-11-23.tail.tsv') as f:
    r=csv.reader(f,delimiter='\t'); next(r)
    for row in r:
        if row: hand[row[0]]=row[1]                    # field 2  = class (R|G)
roll=lambda sl: 'ROLLING:' in mech.get(sl,'')
R=[s for s,c in hand.items() if c=='R']; G=[s for s,c in hand.items() if c=='G']
print('BAND C agreement', sum(roll(s) for s in R)+sum(not roll(s) for s in G),'/',len(R)+len(G))
print('ROLLING census over mech TSV:', sum('ROLLING:' in l for l in mech.values()))
PY

# BAND B named sets (the "named:" lists live in field 3, NOT field 4)
cd /home/nea/ensemble-src/.agents/shared/planning/pok-sweep && \
  awk -F'\t' '$1=="C1b_borderline_R"{print $3}' cutlist-gap-2026-11-23.borderline.tsv
```

### 2.5 The relayed "32 of 64"

**Not reproducible.** No persisted artifact carries per-row labels for the 64-row semantic band, so no per-row agreement against it can be computed. The aggregate hand-vs-mechanical figures that *do* survive (history `2bf04468`) are: 19/26 G rows carry a mechanical handle (19 false-accepts); 41 legit O rows carry none (41 false-rejects); handle rule accepts 460 / rejects 52; rolling regex flags 106, only 15 genuinely rolling; 23 of the 38 true R escape the regex. None of these yields a "32 of 64".

**FIGURE 2 VERDICT (as-relayed 64-row semantic band): UNBACKED-NO-SOURCE — band=64 (per-row labels absent), agreement=NOT-MEASURABLE, source=history `2bf04468-fdab-448c-84a2-201ccd2c62e3` (aggregate counts + partial named lists only; `entry_metadata` null) + `cutlist-gap-2026-11-23.md:50` ("Its per-row names were never persisted"), predicate=n/a (no per-row join is possible).**

**FIGURE 2 VERDICT (BAND B — persisted hand-judged borderline rows): MEASURED — band=18 named rows (R/G axis n=16), agreement=13/16 = 81.2%, source=`cutlist-gap-2026-11-23.borderline.tsv` sha256 `50a7ea5d…` joined on `slug` to `census-rows-active-2026-11-23.mech-labels.tsv` sha256 `6737281c…` field 19 `mech_labels`, predicate="ROLLING: present ⟺ hand label R; ROLLING: absent ⟺ hand label G; C5 excluded (0/2, class not lexically expressible)".**

**FIGURE 2 VERDICT (BAND C — persisted hand-classified tail): MEASURED — band=50, agreement=42/50 = 84.0%, source=`cutlist-gap-2026-11-23.tail.tsv` sha256 `69e2d5de…` joined on `slug` to the same mechanical TSV, predicate=same. R-lexical-misses=5/25, G-lexical-false-positives=3/25.**

### 2.6 Limits of Figure 2

1. The mechanical TSV is a **12:29Z 499-row** snapshot; the store is now 573 active. Join coverage was 100% for all three hand sets (no missing rows) — but any row added after 12:29Z has no mechanical label.
2. The `ROLLING:` tag is one detector among six in `mech_labels`; the R/G boundary is decidable by **no** mechanical tag (by the lane's own §7 finding), so agreement here measures the *rolling* axis only.
3. Both surviving hand-label sets are **re-derivations** (the 50-row tail) or **named subsets** (the 18 borderline rows), produced by the same lane, from the same local TSVs. They are not independent of the mechanical TSV they are joined to.
4. The 18-row borderline band is a *judgment* set, not a random sample — it is enriched for disagreement by construction, so its 81.2% is not a population estimate.
5. The `history 2bf04468` narrative was truncated at the 300-char `summary` boundary; the `details` column held the full text read here, but any further detail beyond 5,000 chars was not persisted.
6. Checkpoint-window effects do not apply to Figure 2 (it is file-based), but they do apply to the Figure 1 Stage-3 correction.

---

## Divergence from relayed claims

| # | Relayed claim | This lane's finding | Impact |
|---|---|---|---|
| 1 | Figure 1 Stage 3: "36 POK-block delivery events" from the event table | The count reproduces exactly (37 total; 36 in-window), but **none of them is a block delivery** — the payload holds the raw task text, and the matches are prose/dispatch quoting the phrase (including the Maintenancer's own dispatch). **0 of 7,481 `message_received` events contain an injected slug line.** | The *number* is right, the *meaning* is wrong. Stage 3's lower bound was not measuring injection. |
| 2 | Figure 1 Stage 3: "14 distinct active slugs ever injected" | Re-measured on the true surface (`checkpoint_blobs`): **384 distinct active slugs** over 2026-10-26→2026-11-24 (1,437 threads, 38,406 blobs). | ~27× larger lower bound. Any funnel arithmetic built on 14 understates injection badly. |
| 3 | Figure 1 Stage 2 limit: "consistent with injection NOT bumping access_count" | **CONFIRMED by source** — `access_count` is incremented at exactly one place (`pok/repository.py:247`), reached only from `pok_service.py:677` (query path); injection only reads it for ranking (`pok_injection.py:198`). | Source ground truth now attached; the `wtgate-01` zero-count row is expected behaviour, not an anomaly. |
| 4 | Figure 1 Stage 3 limit (d): "no pok_injected key exists in shared_context_metadata" | **CONFIRMED** (0 rows) — and the real home is now identified: `instances.metadata` JSONB, **2402 rows**. | Closes the flag's location; Stage 3's "live daemon logs are the only other evidence" gap is filled by the checkpoint store. |
| 5 | Figure 1 Stage 1: "568 active; relayed claim said 499; store has grown +69" | Reproduced with drift: **573 active** at 02:44Z (+5 in ~40 min). Retrieved leg reproduces **exactly (431)**; never-accessed 137→142. | The funnel legs are stable; only the active denominator moves. |
| 6 | Figure 2: hand-vs-mechanical agreement on the 64-row band ("32 of 64") | **UNBACKED-NO-SOURCE** — per-row labels for the 448/512 semantic pass were never persisted (confirmed in-repo at `cutlist-gap-2026-11-23.md:50` and by reading history `2bf04468` directly: aggregate counts + partial named lists only). | The "32 of 64" cannot be recomputed. Measured substitutes: **13/16 (81.2%)** on the 18-row named borderline band, **42/50 (84.0%)** on the 50-row hand tail. |
| 7 | `cutlist-gap-2026-11-23.md` §7: "my 25 G rows carry ZERO mechanical tokens" | **3 of the 25 hand-G rows do carry a `ROLLING:` token** (`parallel-01-write-then-verify`, `craft-01-exception-swallow-reachability`, `closure-01-char-level-verify`). | Minor over-claim in the lane's own write-up; does not change the "judge not filter" conclusion. |
| 8 | Figure 1 Stage 4: "156 of 1,412 project-scoped reports; 17,312 fleet-wide" | `report_injections` total is now **17,337** (store still growing). The 64 `report_injections` rows matching `%Project Operating Knowledge%` are reports *about* POK, **not** injected blocks — so the POK block is not persisted into the report surface. | Stage 4 unchanged in substance; the block-persistence answer is now explicit. |

---

## Reproduction index (commands, in execution order)

```bash
# TASK A — source semantics (read-only greps over the canonical checkout)
cd /home/nea/ensemble-src && grep -rn "access_count" daemon/ --include=*.py | grep -v test
cd /home/nea/ensemble-src && grep -rn "pok_injected" daemon/ --include=*.py
cd /home/nea/ensemble-src && grep -rn "operating-knowledge block delivered" daemon/ --include=*.py
cd /home/nea/ensemble-src && grep -rn "bump_access" daemon/ --include=*.py
cd /home/nea/ensemble-src && grep -rn "POK_INJECTION_FLAG" daemon/ --include=*.py

# TASK B — file inventory + digests
ls -la /home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/
sha256sum /home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/evidence-pok-sweep/*
sha256sum /home/nea/ensemble-src/.agents/shared/planning/pok-sweep/*

# TSV headers
head -1 .../census-rows-active-2026-11-23.mech-labels.tsv | tr '\t' '\n' | nl

# hand-label source: in-repo statement + DB history row (SELECT-only)
grep -n "per-row names were never persisted" /home/nea/ensemble-src/.agents/shared/planning/pok-sweep/cutlist-gap-2026-11-23.md
psql -Atc "SELECT summary, details, entry_metadata FROM project_history WHERE id='2bf04468-fdab-448c-84a2-201ccd2c62e3';"

# flag store (empirical)
psql -Atc "SELECT count(*) FROM instances WHERE metadata ? 'pok_injected'; SELECT count(*) FROM shared_context_metadata WHERE meta_key='pok_injected';"

# checkpoint-surface injection census (the Stage-3 re-measurement)
psql -Atc "SELECT count(DISTINCT thread_id) FROM checkpoint_blobs WHERE channel='messages' AND position('\x5b53595354454d20434f4e544558543a2050726f6a656374204f7065726174696e67204b6e6f776c65646765'::bytea in blob) > 0;"
```

**Environment note (anomaly, reported):** the live PG host returned `ERROR: could not write to file "base/pgsql_tmp/pgsql_tmp…": No space left on device` for a parallel-worker temp file during one bounded checkpoint scan (the same query shape succeeds with `max_parallel_workers_per_gather=0`). All numbers above were re-run under that setting. The PG host's disk pressure is a live condition, distinct from the agent host (`/` at 65%, 82G free).

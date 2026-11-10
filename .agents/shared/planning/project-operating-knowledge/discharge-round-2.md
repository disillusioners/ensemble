# POK — Review Round 2 — Discharge Register

**Round:** fresh-approver iteration 002 (REJECT — 5 new blocking B1'–B5', 12 notes; all 8 round-1 blockers verified genuinely fixed by the approver). **FINAL iteration before the cap.**
**Tree:** v0.29.0 @ 7426e0d8a (worktree `feature/project-operating-knowledge`, fenced dev). Every cited claim re-verified at HEAD before writing. Markdown only; no commits.
**Artifacts revised in place:** `architecture-recommendation.md` (v3) · `decisions.md` (v3) · `plan-overview.md` (v3). Approver tracking: `/home/nea/ensemble-src/.agents/approver/project-operating-knowledge-tracking.md`.

---

## Blocking issues

| # | Blocker | What changed | Doc § | Evidence (HEAD) | Design vs articulation |
|---|---|---|---|---|---|
| **B1'** | Global-KV rejection strawman + self-contradictory | **Retraction + rewrite on TRUE grounds:** (a) TREE-scoped — partition key is `context_key` = tree-root instance id, so knowledge is invisible across trees/jobs; (b) 4096-char row cap; (c) per-tree partitioning forces re-writing the same knowledge per tree; (d) no retrieval/ranking; (e) no supersede/verify lifecycle. The false grounds ("ari-only/human-facing") RETRACTED with the corrected grant fact (28/40 allow-grants; critic DENIES — 29 mentions). **shared_meta_kv promoted to its own layer-map row** (§2.10) — it donates the refresh idiom AND the escape fence the design borrows | arch §2.10, §6.8-KV (§6 item 8), §14; dec A-15.8, A-30 | `daemon/tools/shared_meta_kv_tools.py:4/:37/:78-79/:113-122` (tree-scope, auto-resolved context_key); `daemon/repositories/shared_meta_kv/repository.py:180/:210/:240-242` (≤4096); grants: `grep -l '"shared_meta_kv"'` → 35 raw / **29 mentions among the 40 registered (28 allow + critic deny)**; critic deny at `agents/critic/meta.json:14-15`; self-contradiction anchor = arch §2 row "only per-turn-refreshable pattern" (now §2.16) | **Both.** Rejection grounds replaced (the verdict "not a carrier" unchanged); the map gains a row + the design explicitly credits the two borrowed patterns |
| **B2'** | Terminal lockout at the 64 active-slug cap | **Design choice — archive verb SHIPS IN V1:** `pok_curate(slug, archived=True)` sets status='archived' (CHECK enum already reserved), **frees the active slot**, row stays readable via `pok_query`. Why: supersede is 1-for-1 (nets zero), disputed rows keep active status and count, so without a freeing verb the 65th slug is a PERMANENT capture refusal — unacceptable in a discovery channel. Rejected: auto-evict (violates refuse-never-evict + curator trust), cap raise (delays only), agent-accessible archive (vandalism). §12 terminality row added; the at-cap refusal now names the oldest-least-verified slug AND the archive cure; AC19 pins the full arc (refuse → archive → succeed) | arch §4.3, §4.7, §12; dec A-9, A-27; plan AC19, §4.4 | Structural: v2's tool set had no freeing verb; `status` CHECK already contained `archived` (unreachable in v2 — now reachable); v2 §12 had no terminality row | **Design (changed).** New verb parameter + admission-refusal semantics + risk row |
| **B3'** | No content-rendering/fence contract for a new universal 40-writer verbatim channel | **Design choice — two-layer fence:** (1) WRITE-TIME typed rejection (`pok_body_structural_token`) for newline / line-initial `#` / literal `[SYSTEM CONTEXT:` / ``` fence (operational facts are single-paragraph by design); (2) RENDER-TIME neutralization: fixed list-item template + body through the house `escape_for_context_block` + newline flattening. Doctrine anchored to the canary injection-fencing lane (staged v0.28.15). "Verbatim" redefined as **fenced-verbatim**. §4.5 sentence, §4.9 row, §12 row, AC17 (forged header renders inert; write-time rejection proven) | arch §4.2, §4.5, §4.9, §12; dec A-4, A-29; plan AC17, §4.4 | `daemon/services/context_messages.py:699` (`escape_for_context_block` — narrow `&<>`→`\uXXXX` injection defense); applied by the KV builder at `:1403` (the precedent that "escapes content"); critical-notes contrast (raw render, leader/PM-written — the reason the precedent does not transfer); canary doctrine at `agents/canary/skills-template/` (`injection-screen.md`, `injection-rules.json`, `injection_scan.py`) | **Design (changed).** New write-time validation + render-time fence + AC |
| **B4'** | Layer map omits the prompt-memory artifact family | **Row added** (§2.13): DB-versioned MEMORY-class rows (memory/user/growth/memories) via the prompt-artifacts state machine; prompt surface = **FILENAME INDEX** (`load_recent_memories`), content on demand via `access_memory`; versions via `create_memory_version`. Structural non-carrier: scope = a single agent's persona memory (self-authored), not project knowledge; injection is an index, not content. Together with B1''s shared_meta_kv row (and the DB dynamic-skill tier scoping from the notes) the enumeration claim now closes at 16 rows | arch §2.13, §6.10, §14; dec A-15.10, A-30 | `daemon/loader.py:780-807` (`load_recent_memories`) and `:1490` (prompt-assembly call — filename index); `daemon/tools/access_memory.py:174`; `daemon/services/prompt_version_service.py:1030` (`create_memory_version`); tool-side refs `daemon/tools/prompt_artifacts.py:67/:129`, `daemon/tools/inner_soul.py:39-45` | **Articulation (map completeness).** No design change; the family was never a candidate carrier |
| **B5'** | "ORDERED procedures" — the bar's load-bearing clause — has no mechanism | **Design choice — slug families + atomic adjacent rendering:** convention `<family>-<NN>-<slug>` (zero-padded); slug regex `^(.*)-(\d{2})-(.+)$` groups siblings; **families admit ATOMICALLY** (all members fit the remaining budget or none enters) and **render ADJACENT in slug order at the best member's position — never split**; `pok_query` renders family chains in order. Chosen over a `seq` column: ordering durable in the citation anchor (no renumber txn/collision semantics/hidden state), visible + grep-able; a schema field invites reorder churn. Non-conforming slugs are simply unordered. **The dogfood is re-slugged design-time** (slugs freeze only after dogfood): the deploy spine becomes `pipeline-01/02/03` and injects in order EVERY render; the order statement also remains inside step 03's body (dual mechanism). Bar clause now mechanism-backed (§0, §3, §6) | arch §0, §3, §4.5, §6, §11; dec A-1, A-6, A-11, A-26; plan §4.5 table, AC1, AC18 | Internal mechanism (no code dependency); AC18 asserts adjacency+order on every render and family-atomic admission; AC1 asserts the spine order | **Design (changed).** New rendering rule + admission semantics + re-slug; the walkthrough survives deterministically |

> **⚠ SUPERSEDED 2026-11-10 — Emperor directive (source: Discord).** The **B2' row above** is a HISTORICAL round-2 record and stays byte-intact. Its subject — "Terminal lockout at the 64 active-slug cap" — is superseded: the option that row lists as REJECTED (*"cap raise (delays only)"*) was **taken by directive**. The active-entry cap is now the raised value `[active cap 256 — Emperor directive 2026-11-10]`, so `POK_ACTIVE_CAP_PER_PROJECT` refuses there. Ruling of record: `decisions.md` **ER-2**; the LIVE constant set is single-sourced in `decisions.md` **A-13** (do not restate the numbers elsewhere). Everything else in B2' stands unchanged — the archive verb ships in v1, refuse-never-evict holds, and AC19's refuse → archive → succeed arc is unaffected.

## Non-blocking notes (12 — all folded)

| # | Note | Disposition | Doc § |
|---|---|---|---|
| 1 | §6.2 false receipt — "reference not injected" refuted | **Retracted + corrected:** references ARE rendered bounded (`*(ref: …)*` ≤500c, `context_messages.py:1030-1040` — verified); the rejection rests on field-caps + writer + alert-triage selection | arch §2.1, §6.2, §14; dec A-15.2 |
| 2 | §6.3 convention-read-as-structure (blueprint genre ban is prompt-doc, not code) | **Scoped:** ban cited as convention evidence (build-blueprint.md:25/:33); code-verified grounds (async writer, turn-1 gate, no supersede) carry the rejection | arch §2.8, §6.3; dec A-15.3 |
| 3 | AC3 "rank 1" under-derived | **Fixed:** pinned-boost in `pok_query` (pinned sort above unpinned) + deterministic seeded fixture + BOTH legs asserted (warm embeddings; BM25-only lag) | arch §4.6; dec A-28; plan AC3 |
| 4 | "an attempt is still recorded" names no surface | **Named:** the typed refusal echo + an `ens_system_log` WARNING line; no DB refusal ledger in v1 | arch §4.3; dec A-9; plan AC11 |
| 5 | Once-flag behavior on store-down unspecified | **Decided:** flag writes ONLY on successful render; store-down omission retries next turn | arch §4.5, §4.9; dec A-31; plan AC13 |
| 6 | Dispute-laundering by rewording unnamed | **Named + guarded:** normalized-hash (lowercase + whitespace-collapse) match against any DISPUTED body → typed `pok_disputed_content`; paraphrase residual stated (curator review on open disputes) | arch §4.2, §12; dec A-4; plan AC16 |
| 7 | `pok_verify` deferrable to phase 2 | **Decided: DEFERRED** — staleness operates off write-time (same-content re-upsert refreshes); v1 surface = 3 tools; phase-2 item 2.5 | arch §4.3, §10; dec A-8; plan §5, AC14 |
| 8 | Operational-vs-forensic key rule for the metadata tail (D2 owner) | **Stated:** under the recommended uniform shape NO classification exists (count-only tail, no pointers); the allowlist variant requires a leader-maintained operational-key list stated at D2 time | arch §4.8; dec A-14, D2 |
| 9 | Memoir "three literal ari constants" — grep finds 4 files | **Corrected:** "ari-pinned writer checks at four sites at HEAD" | arch §2.6, §14; dec A-32 |
| 10 | "Five pseudo-incumbents" vs §6's eight; "60–200 chars" vs measured 54–93 | **Corrected (cosmetic):** "every rejected carrier in §6"; dogfood bodies "54–93c measured" | arch §0, §4.2, §11, §14; dec A-32 |
| 11 | DB dynamic-skill tier lacks own map row | **Scoped into §2.9** (files loader + DB skill-bank tier as one row with two tiers; same probability-channel verdict; the DB tier donates the fusion machinery) | arch §2.9 |
| 12 | "7 facts" superset covers the commission's six | **Stated** (one sentence): the Emperor's enumerated examples are a subset; the 7-fact walkthrough is binding | arch §1 |

---

## Receipt-integrity note

The approver's positive record (~45 spot-checks, one false claim = the KV grounds) matches this round's re-verification: the one false claim is retracted in three places (§2.10 receipt, §6.8, §14 corrections) and the corrected facts carry HEAD anchors. All new anchors added this round were verified before writing: KV tree-scope + 4096 cap; 28/40 KV grants + critic deny (29 mentions); `escape_for_context_block` :699/:1403; prompt-memory family (loader.py:780-807/:1490, access_memory.py:174, prompt_version_service.py:1030); critical-note reference render :1030-1040; canary skill artifacts.

## What could not be discharged

**Nothing blocking.** Carried (unchanged, gated): AC12 live metadata re-measurement (prod fenced this round); D4 multi-model corroboration (blocked on the proxy `coding`-alias defect D7). Residuals honestly named rather than hidden: paraphrase dispute-laundering; oblique-prose injection social-engineering (shared with all injected channels — canary-doctrine scope); curator-absence at a full-cap project (loud refusal names the cure).

> **⚠ SUPERSEDED 2026-11-10 — Emperor directive (source: Discord).** The **"64-cap"** qualifier in the residual above is HISTORICAL and the sentence stays byte-intact. The cap itself is now the raised value `[active cap 256 — Emperor directive 2026-11-10]` (`decisions.md` **ER-2**), so the residual reads "curator-absence at a full raised-cap project". The residual's substance is unchanged: the cap is finite, refused-never-evicting, and the loud refusal names the archive cure. Live constant set: `decisions.md` **A-13** (single source of truth).


---

## APPENDIX — Final Fold C1+W5 (council round 3, 2026-11-06)

**Context:** council round 3 verified all five round-2 discharges substantively real (surgical layer fixed in place — 17 text fixes applied by the council, all preserved below); ONE design fold remained blocking. Both councilors state APPROVE after this fold. This appendix discharges it. Markdown only; no commits; HEAD 7426e0d8a.

### C1 — Pinned×family interplay (the flagship AC pair was unsatisfiable as written)

**The defect:** v3 pinned `pipeline-03` while pinned-first rendered it BEFORE 01/02 — violating AC1 (spine 01→02→03) and AC18 (never split); generalized, 4 pins × 3-member families = 12 rows > the 8-entry cap (worst 3,740c > 2,600) — "pinned ⊆ block guaranteed" unenforceable. Same jointly-unsatisfiable-invariants class as round-1 B4, re-opened via the drag route.

**CHOSEN: option (A) family-carries-to-pin.** A pinned member carries its WHOLE family, adjacent, in slug order, to the pinned position; a family pinned via two members renders and counts once. **Why (A) over (C) family-scoped pins:** (A) preserves the member-pin semantics — the curator pins the highest-stakes FACT (the dogfood's own choice, `pipeline-03`), which is the operational mental model; (C) contradicts that choice (pin an abstraction), adds a row-boolean-interpreted-per-family indirection, and still needs the same product bound. The render outcome is identical: the spine elevates, ordered.

**Re-derived bounded arithmetic:** `POK_PIN_CAP_PER_PROJECT = 2` (was 4) × `POK_FAMILY_MAX = 3` (new) = **6 ≤ 8 entries**; entry ceiling 300c (see W5); pinned-tier worst = 6×300 + 80 + 60 = **1,940 ≤ 2,600**; full-block worst = 80 + 8×300 + 60 = **2,540 ≤ 2,600**. **Joint invariant: PIN_CAP × FAMILY_MAX ≤ BLOCK_MAX_ENTRIES → pinned ⊆ block PROVABLE on rows and chars.** New typed admission refusals: `pok_pin_cap` (3rd pin — names current pins + drag), `pok_family_full` (4th family member — names the family, suggests a sibling family or curator archive).

**Docs:** arch §4.2 (family admission bound), §4.3 (pin admission), §4.5 (drag rule + arithmetic), §4.7 (constants), §6 (option C rejection recorded), §11 (pin re-check), §12 (🟡 row), §14; decisions A-6, A-9, A-11, A-13, A-33; plan §4.1, §4.4, §4.5, AC1/AC11/AC18. **Dogfood pin re-check (required edit 4): CONFIRMED** — pinning `pipeline-03` carries the spine ordered to the top tier; member-pin semantics preserved; no slug changes.

### W5 — Escape-aware sizing (folded with C1; same §4.7/§4.9/A-11 surface)

**The defect:** the arithmetic used RAW bodies while the render fence escapes `&<>` → `\uXXXX` (≤6×/char) BEFORE assembly — a legal 240c &<>-dense body renders ~1,440c; two such entries exceed 2,600c → silent truncation of the deterministic-injection guarantee. The KV precedent itself moved its cap AFTER the escape (`context_messages.py:1395-1403`).

**Rule adopted:** `POK_BODY_MAX_CHARS = 240` **counts the ESCAPED body** — write-time validation computes `len(escape_for_context_block(body)) ≤ 240` (the same escape the render applies, `:699`). Raw bodies dense in escapable glyphs shrink correspondingly (worst legal all-escapable raw = 40c); dogfood facts are 54–93c raw with no escapable glyphs — headroom unchanged. Entry ceiling = 300c post-escape incl. template — the C1 arithmetic above is now escape-true by construction; the degradation table gains the explicit row (no render-time silent truncation). **Docs:** arch §4.2, §4.5, §4.7, §4.9, §14; decisions A-4, A-6, A-33; plan §4.4, AC20.

### Advisory dispositions (council-left, decided)

| Advisory | Disposition |
|---|---|
| (i) Archived-slug re-upsert edge | **ADOPTED as suggested:** upsert onto an ARCHIVED slug flips the archived row to `superseded` (forward-only lineage link — **archived rows never resurrect**) and inserts a fresh active row under the same cap/fence/dispute guards; the curator can re-archive. (arch §4.2; decisions A-4/A-33; plan AC16 leg) |
| (ii) "line-initial #" → implementer clarity | **ADOPTED:** the fence rejects "a leading `#` at body start" — the only line-initial position, since newlines are already rejected. (arch §4.2; decisions A-4/A-33) |
| (iii) memoir constant-count scoping | **ADOPTED: scoped to the store** — 4 quoted-`ari` occurrences across 3 files within `daemon/repositories/memoir/` at HEAD (models.py:2, repository.py:1, seed.py:1; both quote styles measured) — the map row (§2.6) and §14 now carry this figure. |

### Council-fix preservation

The council's 17 surgical text fixes are preserved verbatim — :5083 swallow anchor (verified: `except Exception as _persist_exc`), the 28/40-vs-29-mentions KV census (decisions A-15.8 aligned to the same corrected figure), the family-counts-against-64 sentence (arch §4.2), AC1's "8 entries" wording, and the rest. Nothing reverted; this appendix and the v3.1 edits layer on top.

### Verdict

Both councilors' APPROVE condition is discharged: **C1-A with PIN(2)×FAM(3)=6≤8 + W5 escaped-body budget** — AC1/AC11/AC18 mutually satisfiable, pinned ⊆ block provable, and the deterministic guarantee immune to escape-expansion truncation. No new council round; controller delta re-check → final approver iteration.


---

## APPENDIX B — Mechanical Fold F1+F3 (controller re-check, 2026-11-06)

**Context:** the controller re-check verified the C1+W5 core sound and FAILED the set on two named defects (F1 chars-side keystone; F3 exclusion×drag). Both landed here; advisories F4–F8 dispositioned. Markdown only; no commits; HEAD 7426e0d8a. All controller edits preserved.

### F1 — Chars-side keystone: DERIVED, and CHOSEN: Option A

**The defect (confirmed):** "template/markers ≤ 60c" was asserted, never derived, and FALSE — the doc's own slug is exactly 44c; worst render overhead derives to **92c** (`- `2 + `[environment]`13 + ` `1 + slug 44 + ` `1 + `([observed] [stale Nd])` 4 + markers ≤27 (23 realized)) → entry **332c**, not 300c → v3.1's "worst 2,540 ≤ 2,600" and §4.9's "unreachable per arithmetic" were falsified (8×332+140 = 2,796 > 2,600), and no slug-length bound existed anywhere.

**CHOSEN: Option A** — `POK_SLUG_MAX_CHARS = 44` (write-time typed `pok_slug_oversize`), `POK_ENTRY_RENDER_MAX_CHARS = 332` (escaped body 240 + derived overhead 92), `POK_BLOCK_MAX_CHARS` 2,600 → **2,800**. **Why A over B:** B keeps 2,600 but makes the 8-entry guarantee conditional and scopes AC20's tail — weakening the deterministic guarantee the design exists to provide; A keeps rows AND chars simultaneously satisfiable.

**Closed-form worst case (shown, not asserted):**
- overhead = 2 + 13 + 1 + **44 (slug bound)** + 1 + 4 + **27 (closed two-token marker vocabulary: `[observed]` 10 + ` ` + `[stale 999d]` 12 = 23 realized; the 4c slack absorbs ≤7-digit day counts)** = **92c**
- entry = escaped body **240** + 92 = **332c**
- block total = header ≤40 (derived 31) + 8 × (332 + join separator 1) + tail ≤60 = **2,764 ≤ 2,800** (36c slack)
- pinned tier = 2 × 3 = 6 rows × 333 + 140 = **2,138 ≤ 2,800** — the controller's "2,180" figure was conservative; the exact sum is 2,138; conclusion identical.
- **Constant/AC ripple audit:** AC11 rows leg 6≤8 unchanged; AC18 ordering-only (no block numbers); **AC20 re-derived at 332**; AC1/AC3–AC16/AC19 carry no block-size numbers; plan §4.5 sizing consequence re-derived (2,764; dogfood pinned ≈686); §4.9's oversize row now cites the closed form. No other constant or AC silently depends on the old 300/2,600.

**Docs:** arch §0, §4.1, §4.2 (slug bound + `pok_body_oversize`), §4.5 (derived closed form), §4.7 (constants: +`POK_SLUG_MAX_CHARS` 44, +`POK_ENTRY_RENDER_MAX_CHARS` 332, block 2,800), §4.9, §11, §12, §14; decisions A-2/A-4/A-6/A-11/A-13/A-34; plan §4.1, §4.4, §4.5, AC11/AC20.

### F3 — Disputed member of a pinned family: RULED

**Normative sentence (arch §4.5 / A-34 / AC18):** *Excluded rows NEVER render, in any tier — when a pinned family contains an excluded member (disputed / superseded / archived), the family drags its SURVIVING members, contiguous and in slug order, to the pinned position; if the pinned member itself is excluded the family is not dragged; a family with zero surviving members renders nothing; excluded rows are filtered out before admission (no tail count) — never a gap placeholder, never an excluded row.*

Chosen over gap-markers (which would surface status inside the block and add chars) and over family-drop-out (which would lose the surviving steps). §4.9's exclusion stays absolute; AC18 gained the F3 leg.

### Advisories (F4–F8) — dispositions

| Advisory | Disposition |
|---|---|
| F4 — "counts once" ambiguity | **FOLDED:** a doubly-pinned family counts ONCE on **rows AND chars** (arch §4.5, A-11, AC18 F4 leg). |
| F5 — block-total rider | **FOLDED, stated once normatively** in arch §4.5/A-11: pinned tier first, then ranked actives, stopping at the first point where rendered chars would exceed `POK_BLOCK_MAX_CHARS` OR entries would exceed `POK_BLOCK_MAX_ENTRIES`; tail counts remainder. |
| F6 — dedicated oversize refusal | **FOLDED:** `pok_body_oversize` is the typed refusal for AC20's negative leg (arch §4.2/§4.7, plan AC20). |
| F7 — supersession stamp on the sealed round-1 register | **SKIPPED, by judgment:** the round-1 register is a sealed historical record; rewriting it would violate sealed-history immutability. Supersession is carried where it belongs — this appendix + arch §14's corrections list. |
| F8 — AC20 positive-leg slug pin | **MOOT under Option A:** the global `POK_SLUG_MAX_CHARS = 44` write-time bound makes a special positive-leg slug pin unnecessary — stated in AC20. |

### Controller-edit preservation (verified this pass)

`:5083` swallow anchor (arch §2.16 + plan §4.2 — the verified `except Exception as _persist_exc`), the 28/40-vs-29-mentions KV census (arch §2.10/§6.8/§14 + decisions A-15.8), the family-counts-against-64 sentence (arch §4.2), AC1's "8 entries (the 7 dogfood facts; one fact spans two slugs)" wording (plan AC1), and the **A-21–A-25 tombstone (decisions.md, "Deliberately NOT built" section)** — all present; none clobbered.

### Verdict

Both controller defects discharged: **F1 Option A** (slug 44 / entry 332 / block 2,800; closed form 2,764 ≤ 2,800; pinned 2,138 ≤ 2,800) and **F3** (excluded rows never render; survivors drag contiguously). Advisories closed (F4/F5/F6 folded; F7 skipped with rationale; F8 moot). Ready for the spot re-check and the FINAL approver iteration.


---

## APPENDIX C — Leader Disposition Fold (approver cap reached, iteration 003)

**Context:** approver iteration 003 rejected (8 findings); the leader accepted all 8 and decided the two that needed decisions. No further approver round follows. This appendix records the fold. Markdown only; no commits; HEAD 7426e0d8a. Prior folds stand (C1/W5, F1/F3); controller edits preserved (tombstone, `:5083`, 28/40 census, family-counts sentence, AC1 wording).

### Per-blocker disposition

| # | Leader decision | Landed | Docs | Evidence (HEAD) |
|---|---|---|---|---|
| **B1** | **DEFINE THE LHS.** `POK_BLOCK_MAX_CHARS` = FULL rendered block **including the `_make_context_message` wrapper**; re-derive; 2,600→**2,900**; restate pinned tier; AC11 arithmetic updated; no "proven" claim without the wrapper | Content 2,764 + wrapper 47 = **2,811 ≤ 2,900** (89c slack); pinned 2,138+47 = **2,185 ≤ 2,900** | arch §4.5/§4.7/§4.9/§14; dec A-11/A-13/A-35-B1; plan §4.1/§4.4/AC11 | `context_messages.py:75-76` (`CONTEXT_PREFIX` 17 = `[SYSTEM CONTEXT: `, `CONTEXT_SUFFIX` 3 = `]\n\n`); title 27 via `:181-182`; wrapper-cost-subtraction precedent `_SNAPSHOT_DIGEST_WRAPPER_TOKENS` `:205-230` (the in-file working fix) |
| **B2** | **LIVE SEEDING step.** Phase 1 keeps dev dogfood; **Phase 1.5 — live seeding** (owner leader/devops; gated on the next promote carrying the schema): live project row → live 8-slug seeding via the tool lane → fresh live leader-spawn verification. State plainly: dev-true until then | New Phase 1.5 section + table row; **AC21** (live path); AC1 re-scoped to DEV | arch §10/§11/§13; dec A-19/A-35-B2; plan §2/§3/§4.5b/AC1/AC21/§9 | Design (no code dependency); the honest boundary is stated in three docs |
| **B3** | **Atomic contrast family.** Re-slug fact-5/6 into ONE family: `deploy-01-daoduc-backup`, `deploy-02-ensem-dev-primary`; re-check all 8 slugs against the family regex; state one-sided render unreachable | 8 slugs re-listed; contrast family atomic; **regex tightened to `^([a-z][a-z0-9]*)-(\d{2})-(.+)$`** — the old `^(.*)` form mis-parsed `model-catalogs-19-vs-22-not-drift` as family `model-catalogs-19-vs` step `22` (greedy rightmost digit pair) when it must be unordered; the tightened regex no longer matches it — and `daoduc-helm-release-v28-kubectl-patch` never matched either form (digits follow `v`, not `-`); non-family slugs now correctly UNORDERED | arch §4.5/§11/§14; dec A-19/A-26/A-35-B3; plan §4.5/AC18 | Slug-by-slug re-check in arch §11 + plan §4.5; all slugs ≤44c |
| **B4** | **Grant-sweep integrity rules (normative).** (1) watcher meta empty allow = inherit-all — NEVER append; exclude; (2) `nest-report` tail position in every non-empty allow that HAS it — insert `pok` BEFORE (HEAD exceptions: `ari` insert-before-its-entry, memoir stays last; `canary` append-at-end per the default-grant rule); (3) enumerate the sweep + cite the pin test | Rules stated in §7; 40 registered / 39 non-empty allows; watcher inherits `pok` (non-privileged) | arch §7/§14; dec A-16/A-35-B4; plan §4.1-2/§4.4 | `agents/watcher/meta.json` `"allow": []`; `_tool_registry.py:326-340` ("absent/empty allow = everything is potentially allowed … watcher is empty-allow today"); `tests/unit/test_anomaly_report_grant_pins.py` (nest-report-LAST invariant; the establishing ceremony touched 40 meta.json files — registered grants + `_baby_template`/`_mother`/`[v2]` specials) |
| **B5** | **Retrieval concrete.** (a) pinned rows BYPASS the threshold (blueprint-core 1.0 mirror); (b) DELETE the invented 0.4/0.6/0.25 fusion — reuse the shipped critical-notes selector; (c) AC3 asserts ALL 8 slugs retrievable by natural query | `pok_query` = selector reuse (`critical_notes_selector.py` + orchestrator, configurable weights/threshold); pinned bypass; AC3 re-written (all-8 + rank-1 order slug, both legs) | arch §4.6/§4.7/§14; dec A-10/A-12/A-28/A-35-B5; plan §4.2/AC3 | `daemon/services/critical_notes_selector.py` ("compute-fusion selection pipeline"); `critical_notes_selection_orchestrator.py` (`fusion_bm25_weight`/`fusion_vector_weight`/`fusion_threshold` — CONFIGURABLE, not hardcoded; grep for the invented ≥0.25 threshold in the implementation: EMPTY — the shipped defaults ARE 0.4/0.6/0.30 (`critical_notes_selector.py:68-70`, configurable); only the threshold claim was invented) |
| **B6+B8** | **Allowlist variant ships in v1 (resolves D2).** Named allowlist renders verbatim; non-allowlisted 2000c/key + 16384c block + omitted-count tail; NO new read tool; AC6 fail-if-absent/truncated; AC15 replaced; D2 PENDING→DECIDED | Allowlist NAMED (12 key families incl. `ari_plane_tracking_policy` … `staged_release_v0290_ceremony_notes`); §4.8 rewritten; AC6 both legs; AC15 replaced; **D2 DECIDED (allowlist v1; uniform = phase-2 option)** | arch §4.8/§12/§13/§14; dec A-14/A-35-B6/A-36/D2; plan §4.1-3/AC6/AC15/§5-2.7 | Policy-key names from this project's live metadata set (project registry; AC12 re-measures bases). No new tool: exposure surface unchanged by design |
| **B7** | **Name the PG registration surface.** Models imported BEFORE `create_all`; state the pattern + import site; AC9 asserts PG MATERIALIZATION; name same-commit re-pins | `daemon/manager.py` pre-create_all block (~:928-987) named in §4.1/§7 + plan file table; AC9 rewritten (scratch-PG boot shows both tables); re-pins named (real paths: `tests/unit/metrics/test_metrics_vocabulary_amendment.py`, `tests/unit/tools/test_tool_category_artifact.py`) | arch §4.1/§7/§14; dec A-16/A-35-B7; plan §4.2/AC8/AC9 | `daemon/manager.py:928-987` (`# register before create_all` import block — app_settings, resource_monitor, board, project_skill_file, memoir); create_all = PG source-of-truth (`:129/:931/:942/:956/:980`); the two pin tests exist at the real paths (the disposition's bare `tests/unit/test_*` resolves there) |

### Notes folded (from the approver's list)

| Note | Disposition |
|---|---|
| §2 row 1 overstated quantifier ("ALL project-linked agents") | **Corrected:** "all agents whose instance carries a NON-DEFAULT `project_id`" (the system-default project renders the scope guide instead) — arch §2 row 1. |
| Two economics measurements unreconciled (69 keys/279,810B vs 82%/408,809B) | **Reconciled, two bases stated once:** metadata-dict basis 279,810 B; whole-project-JSON basis 408,809 B; 279,810/408,809 = **68.4 %** of the JSON; the "82 %" head-share figure (unstated denominator) is **withdrawn as a computed claim**; AC12 re-measures and recomputes both bases (arch §2 row 3 + §4.8; plan AC12). |
| Recurrence: B5/B6 were iteration-002 notes carried rather than folded; B7 recurs the round-001 registration class | **Acknowledged in the register and closed as decisions:** B5 → A-12/A-28 (selector reuse + pinned bypass); B6 → A-14/A-36 (allowlist, D2 decided); B7 → A-16 + arch §4.1 (PG registration + AC9). No restatement-as-residual remains for any of the three. |

### Status changes

- **D2: PENDING → DECIDED** (allowlist for v1; uniform per-key cap deferred as a phase-2 option). A residual presented as open must be genuinely open — this one is now closed.
- **New phase:** Phase 1.5 — live seeding (B2), owner leader/devops, gated on the next promote carrying the schema; **AC21** is the live-path check; until it passes, the walkthrough is dev-true only.
- **New constant set (final):** the LIVE sizing constant set is **single-sourced in `decisions.md` A-13** — do not restate the numbers here (a 4-way numeric restatement is exactly the fragility that broke when the cap moved; see `decisions.md` ER-2). The set at this round's writing was the ten-entry table in `architecture-recommendation.md` §4.7, and the values have since moved with ER-2 (`[active cap 256 — Emperor directive 2026-11-10]`). Reference A-13 for any value.

### Verdict

All 8 leader dispositions implemented; the two decisions (B3 family shape; B5 retrieval reuse) are landed as normative text with receipts; D2 closed; the live-vs-dev boundary is stated plainly. The set is ready for the scoped spot-check, then commit + report to the Emperor.


---

## APPENDIX D — Emperor-Ruling Fold (docs-only, bounded; 2026-11-07)

**Ruling (verbatim intent, Discord 2026-11-07):** "we should split [operating knowledge vs project record]. Ari you need know operation one (to control the leader, aegis,… teams), also agent like devops need know. The dev team actually need know the implementation major, and operation is also need (not all the case). It's for active injection — if agent want read them they can read any."
**Fold site:** `feature/project-operating-knowledge-ruling` @ 5672f055c (the committed canon rode latest; anchors re-verified at this tree: wrapper `context_messages.py:75-76`, escape `:699`, swallow `:5083`, selector files present, pre-create_all block present). Markdown only; no commits — the giter finalizes. Provenance stamp "Emperor ruling, 2026-11-07, source: Discord" on every amendment site (arch §1/§4.1/§4.2/§4.3/§4.5/§4.5.1/§4.6/§11/§13/§14; decisions ER-1 + amended A-rows; plan §2/§4.1/§4.5/AC22–24/§9).

### What the fold landed

| Ruling element | Landed as | Docs |
|---|---|---|
| **1. SPLIT CONFIRMED** — two surfaces stand (POK vs Related-Project record); **unified-block alternative offered and DECLINED — considered-and-declined, decider: the Emperor** | Recorded in the audience subsection + §6 rejected-alternatives (decision-declined class) + ER-1.1 | arch §4.5.1/§6; dec ER-1/A-3 |
| **2. Audience dimension** — `audience ∈ {operation, implementation, both}` schema field + curator `audience_agents` narrow override; default ACTIVE-injection audiences **operation → {ari, leader, devops} (leader INCLUDED)**, **implementation → {leader, developer}**, **both → every agent on the project**; **per-agent budget (8 / 2,900 full-render) is a PER-AUDIENCE effective slice; audience filter FIRST, then the existing admission semantics within the visible set; every closed-form bound holds per-view (the visible set is a subset — the project-wide worst bounds every view); pin cap stays PER PROJECT (a pinned entry renders in every audience view that can see it); audience mapping by factory-captured agent_id** | Schema fields; §4.5.1 normative admission rule; §4.7 per-view constants; AC22 | arch §4.1/§4.5.1/§4.7; dec A-2/A-6/A-9/A-11/ER-1.2; plan §4.1/AC22 |
| **3. Ari delivery seam** — structural fact recorded (today the Related-Project block injects only into project-linked working agents; Ari receives NO project injection); **Ari holds the OPERATION slice at dispatch time**: the seam sits where Ari's dispatch/work context is assembled for a target project; gates: non-default project, operation audience, same budget/fence/family semantics; **per-dispatch fresh read** (Ari is long-lived/reused — per-dispatch assembly IS the freshness mechanism; `pok_query` mid-lane); stable id `pok:{project_id}` within Ari's instance namespace; **the 8th touch-point class** | New seam subsection + file-table row + phase gates + AC24 | arch §4.5.1/§7/§10; plan §4.2/§4.5b/AC24 |
| **4. Universal passive read — INVARIANT** — `pok_query` returns ALL entries of the project to ANY agent on it, regardless of audience tags; injection is curated, reading is free ("if agent want read them they can read any") | §4.6 invariant; AC3 re-scoped; AC23; C-4 boundary | arch §4.6; dec A-12/ER-1.4/C-4; plan AC3/AC23/§2 |

### Dogfood classification (assigned honestly)

| slug | audience | why |
|---|---|---|
| `artifact-is-container-image` | **both** | artifact truth — universal for anyone touching the project |
| `pipeline-01-merge-master-triggers-image-ci` | **both** | devs must merge-to-master knowingly; ops must know nothing ships without CI (the ruling's own hint case) |
| `pipeline-02-verify-image-tag-digest-feature` | **operation** | post-build verification ritual |
| `pipeline-03-deploy-order-daoduc-backup-first` | **operation** | deploy order — pinned |
| `deploy-01-daoduc-backup` | **operation** | Helm v28 mechanism |
| `deploy-02-ensem-dev-primary` | **operation** | plain-kubectl mechanism |
| `healthz-in-cluster-only` | **both** | environment property — false-negative hazard for anyone probing, devs included |
| `model-catalogs-19-vs-22-not-drift` | **operation** | deploy-drift triage |

Resulting views: **ari/leader/devops → 8; developer → 3 (artifact, CI-trigger, healthz); worker → 3 (both-only)**. Honest note: this payload is operational knowledge — **zero `implementation` facts yet**; future implementation facts slot into the same field.

### Consistency requirements honored

- **Closed-form bounds per-view:** the audience filter PRECEDES admission; each view is a subset of the project set, so the project-wide worst (2,811 ≤ 2,900 full-render; pinned 2,185; rows 6 ≤ 8) bounds every view — stated, not re-derived.
- **Pin cap stays PER PROJECT** (not per audience); a pinned entry renders in every audience view that can see it (stated in §4.3/§4.7/A-9).
- **Metadata-cap companion UNAFFECTED** — stated in §4.8 (the ruling touches POK only; the Related-Project record, its allowlist, and its caps are unchanged).
- **AC legs added:** AC22 (audience-filtered injection: devops 8 / developer 3 / worker both-only / leader all); AC23 (universal read); AC24 (ari seam: dispatch-time operation block; no block on the default project).
- **AC18/AC1 scoped per-view** — the spine 01→02→03 assertion holds fully in the operation view; restricted views render visible family members contiguously.

### Consistency tensions hit (recorded, resolved by rule — not breaks)

1. **Mixed-audience family:** `pipeline` = {01 both, 02 operation, 03 operation}. In a developer's view the family renders ONLY step 01 (audience-invisible members are excluded-for-view — the F3 semantics extended naturally). Intentional (devs need the CI trigger, not the deploy spine); AC18 amended to assert per-view.
2. **Worker audience:** workers/gaia/tester etc. are in NO default audience set → they see `both` entries only. This is the ruling's shape ("dev team need implementation major… not all the case" — the operation slice is for the controlling/ops roles); universal read keeps everything reachable. Flagged for the Emperor's awareness in case a broader default was intended — the design follows the ruling text.
3. **AC2 re-scoped:** the original "worker receives the same block" leg is now false by design (worker sees the both-only view); replaced with the view-correct assertion.

### Verdict

The ruling is folded as amendments with provenance at every site; nothing settled was reopened (canon, storage, fence, bounds, selector reuse, allowlist, sweep rules all stand; the only semantic changes are the audience dimension on ACTIVE injection, the ari seam, and the universal-read invariant). Ready for the giter to finalize (no commits by this pass).

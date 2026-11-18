# POK sweep wave 2, lane B - PHASE-1 COMPLETION SCAN (2026-11-18)

**Input listing (authoritative, exact-at-capture):** `/home/nea/ensemble-src/.agents/shared/planning/pok-sweep/live-active-2026-11-18-post-phase1.json`
**Listing capture time:** 2026-11-18T15:25:26Z - **rows:** 277 (active only) - **whole-file sha256:** `2db52522983a1a1116e6961522ad487f0808d313ec3970cdff9d581e26731408`
**Bodies aggregate (declared == recomputed):** `6dce4bfd4fd8dc700b81533f05f1ffe8a1ed9ab67795dc92334e5e77509f4941` - **per-row body sha256:** 277/277 verified
**Status filter:** `active` - **project:** `83da04de-a410-4fb5-9e92-251a99d28a52`

> **DRIFT CAVEAT.** Store drift after capture: active 278 (kb-writer insert gc-fe-surfaces-span-pin-committed-only at 15:32:56Z) and superseded 62->63. This analysis is bound to the LISTING snapshot (exact-at-capture), not to live store state. Anything inserted after 15:25:26Z is NOT in this analysis; PM should re-pull the listing before executing a merge whose member set could have changed.

**Generation method (machine-generated, not hand-typed).** A python script reads the listing JSON and emits this file; every member id, store slug and member body below is COPIED MACHINE-SIDE from the JSON (no retyping anywhere in the pipeline). Merged-body char counts are computed programmatically and every merged body is asserted <= 240 chars. Required durable-fact phrases are asserted present as literal substrings before a merge is proposed.

**Durable-fact rubric (used for the COVERAGE verdict).** A member's content is a DURABLE FACT if it is a reusable operational assertion - a mechanism, a trap, a rule, a protocol, a code/file anchor, an env var or an exact string. It is NOT durable if it is a dated snapshot (counts, node totals, as-of-date), an inventory of a landed artifact's contents (test names, file lists, key fingerprints), a one-time incident outcome, or a live-status/rolling value (deadlines, open-queue counts). COVERAGE = YES requires the merged body to be <= 240 chars AND to carry every member's durable fact. Content deliberately not carried is listed per cluster as ROUTED OUT with the home where it lives - PM must confirm that home before archiving the member.

**Guardrail (unchanged from the sweep charter).** A row may be archived ONLY if (a) the durable fact is covered by another ACTIVE row, or (b) it is a dated snapshot whose content now lives in a routed home. NEVER on age, NEVER on access_count. Merges here are proposals for PM; this lane performed NO POK writes.


## 1. Result summary

| Metric | Value |
|---|---|
| Active rows scanned | 277 |
| Rows with >=1 completion keyword | 29 |
| (a) durable fact covered by another ACTIVE row | 2 |
| (b) dated snapshot; content now lives in a routed home | 3 |
| (c) JUDGMENT-NEEDED for PM | 4 |
| (K) keyword hit is NOT a self-closing marker - keep active | 20 |

**Method.** Programmatic keyword scan over all 277 bodies (mission set: closed / resolved / deferred / retired / superseded / fixed / landed; extended set: done / complete / shipped / archived / dissolved / no-longer / final / was-true-only), then MANUAL adjudication of every hit. Verdicts:

- **(a)** the row marks its subject closed AND the durable fact it carries is already held by another ACTIVE row (named below) -> archival justified.
- **(b)** the row marks its subject closed AND it is a dated snapshot whose content now lives in a routed home (named below) -> archival justified.
- **(c)** the row LOOKS closed but still carries durable content or an OPEN obligation with no routed home -> JUDGMENT-NEEDED for PM.
- **(K)** the keyword is NOT a self-closing marker (fail-closed, closed commit range, deferred FIX, landed as a verb, complete as an adjective, a negation) -> keep active.

**False-positive classes found in this corpus (the reason K is the largest bucket):** fail-closed semantics (fails closed AT BOOT, fail closed on mismatch); hyphenated fail-closed; closed commit range; deferred naming the deferral MECHANISM rather than a closed episode; landed / shipped / complete / final as verbs or adjectives; FIXED inside a negation (Routed (not fixed ...)); resolved as a metric noun (0 sweep-resolved); superseded describing a RELEASE rather than the row's subject.

**Guardrail (unchanged).** (a)/(b) only justify archival. NEVER on age, NEVER on access_count. This lane performed NO POK writes and NO archiving - it names the verdict for PM.


## 2. Hit table

| # | id | store slug | keywords | verdict | covered by / routed home |
|---|---|---|---|---|---|
| 1 | `77bb3c69-bb35-42d3-b81e-5ff643847146` | `agent-01-skill-injection-flag-scope` | CLOSED | **K** | - |
| 2 | `a883b76b-77cd-45b0-b9d0-b0c9acd8bc24` | `census-pin-lane-ownership-form` | CLOSED | **K** | - |
| 3 | `d00581d4-a4ad-4613-a0f5-136927f5c6d2` | `decisionctl-01-kill-channel` | CLOSED | **K** | - |
| 4 | `bed25def-a69b-489a-9f62-9966b3208b24` | `disk-03-tag-lineage-unmerged-stage-trees` | SUPERSEDED | **K** | - |
| 5 | `ce03ba0c-3ade-450b-8aed-4d0bc26497a8` | `e2s0-01-mirror-claims-and-scratch-db-wall` | RESOLVED | **c** | - |
| 6 | `28889a8b-1234-4f28-88c6-6bce224d12ec` | `evidence-commit-ab-fdc2bb4f-restored` | RESOLVED | **c** | - |
| 7 | `f8a54e0f-a551-4b85-9e70-1d88d647efc6` | `gc-gate-03-filter-side-pin` | SHIPPED | **K** | - |
| 8 | `a3020315-8141-4552-aaba-3f351e5a27ee` | `gc-p4-03-note-drop-turn-end-reminder` | SUPERSEDED, FIXED | **b** | git history c249d7fce |
| 9 | `ccf336d0-2ff7-4764-a062-8666083e0bfc` | `gc-p4-junitxml-is-the-red-source` | COMPLETE | **K** | - |
| 10 | `30291831-3763-4e6a-9df4-a4938e0038dc` | `group-chat-01-design-landed` | COMPLETE | **b** | release tree + git history 56a5f4a00 + the group-chat plan corpus |
| 11 | `e71966f5-75fa-49f6-ba95-01768d3afaba` | `group-chat-02-p1-committed-docs-stale` | ARCHIVED | **K** | - |
| 12 | `8ebed7dc-69fc-4c73-a6e1-95b3d0903234` | `group-chat-p4-01-carrier-reminder-slot-gap` | SUPERSEDED, FIXED | **b** | git history c249d7fce |
| 13 | `3fbd3865-1fa9-45cc-8a85-eb04f3ad450c` | `kv-setkv-replaces-whole-value` | COMPLETE | **K** | - |
| 14 | `7c9b9fa5-f991-4d79-bcc5-92156d3cd50b` | `plan-body-ledger-agreement` | FIXED | **K** | - |
| 15 | `6db7e0fd-a716-4463-8e5f-bfea1a118c87` | `prompt-assent-flip-on-gate` | CLOSED | **K** | - |
| 16 | `6f44fde8-da8c-41b5-b55f-ed0ec4da66e4` | `provider-01-402-wake-kill` | LANDED | **c** | - |
| 17 | `b43c6db1-91cd-429e-ae41-3d1f3f2d065f` | `py-fromimport-staticmethod-importerror` | FIXED | **c** | - |
| 18 | `3087ee94-3800-49f9-8748-4f81f1c4432d` | `quarantine-01-chart-seal-row-is-stale` | WAS-TRUE-ONLY | **K** | - |
| 19 | `bdfb7de6-1fbb-4c4b-8217-42c3389a73a1` | `review-01-shared-helper-blast-radius` | SHIPPED | **K** | - |
| 20 | `783a1451-b41b-47c9-9e5f-1152480a2109` | `review-02-describe-http-error-call-sites` | SHIPPED | **a** | bdfb7de6-1fbb-4c4b-8217-42c3389a73a1 |
| 21 | `d5fde27d-8a2f-477e-b400-4faf1edb6216` | `skill-e6-03-promote-did-not-resolve-backlog` | RESOLVED | **K** | - |
| 22 | `0df359fe-4a6b-4c93-9cae-816798a97ee5` | `skill-e6-04-a4-gauge-grew-not-collapsed` | LANDED | **K** | - |
| 23 | `83a4b2d3-dd41-4f85-b799-11db50aefaee` | `skill-lane-flip-verify-restart` | LANDED | **K** | - |
| 24 | `63f64bbe-4f96-45a7-860e-bafb07aa7fca` | `skillab-02-keeper-rule-zero-traffic-fix` | DEFERRED | **K** | - |
| 25 | `af8815b5-5623-439d-97d7-973bfe7a5c30` | `skillab-03-comparison-counter-semantics` | DEFERRED | **K** | - |
| 26 | `b0611691-7a2e-4756-95ad-882a23131455` | `skillab-manifest-01-no-consumer` | RESOLVED | **K** | - |
| 27 | `ebe4f878-ac79-4d9e-8ccb-ceebedfa1aa4` | `sonic-03-blast-gate-removed` | SUPERSEDED | **a** | 700bd23b-e8b9-4f12-9858-1b133cae1047 |
| 28 | `df5f8463-318a-4373-8c79-5f5bc102cfba` | `sonic-bump-version-selfcommits` | FINAL | **K** | - |
| 29 | `82a3ca1c-07ad-4fc9-9ca6-6bbd65e97b06` | `w3-f18-store-not-callable-typeerror` | FIXED | **K** | - |

## 3. Per-hit adjudication


### 1. `agent-01-skill-injection-flag-scope`

- **id:** `77bb3c69-bb35-42d3-b81e-5ff643847146` - **domain:** `artifact` / **audience:** `both` - **created:** `2026-11-09T08:09:51.697383+00:00` - **body sha256:** `79c0594cfc4cb7dbebf1df7978601954b9080199257bd47253203882c5d90499`
- **keywords matched:** CLOSED
- **body (verbatim, machine-copied from the listing JSON):**
```
Agent meta.json skill_injection gates dynamic per-turn skill discovery; absent key = false (fail-closed, daemon/registry.py meta.get). 15 dirs true / 11 logical agents; skill-keeper lacks the flag so its A/B group can never resolve.
```
- **VERDICT: K**
- Keyword closed comes from fail-closed (a mechanism descriptor). The rows subject is OPEN: skill-keeper lacks meta.json skill_injection so its A/B group can never resolve.


### 2. `census-pin-lane-ownership-form`

- **id:** `a883b76b-77cd-45b0-b9d0-b0c9acd8bc24` - **domain:** `verify` / **audience:** `implementation` - **created:** `2026-11-11T07:55:19.108739+00:00` - **body sha256:** `7c28bb41171217bc58b300478647c8fe554318e2a9e9cd3533d7565ec7851a1c`
- **keywords matched:** CLOSED
- **body (verbatim, machine-copied from the listing JSON):**
```
A census pin counting a lane's own files must be lane-scoped, not global: assert the lane's own file is PRESENT in the tree delta, and pin the lane's closed commit range for exactness. Global equality re-breaks on every later lane.
```
- **VERDICT: K**
- Keyword closed comes from the lane's closed commit range - a range qualifier, not a self-closing marker. Row stays active (also a CL-06 member).


### 3. `decisionctl-01-kill-channel`

- **id:** `d00581d4-a4ad-4613-a0f5-136927f5c6d2` - **domain:** `other` / **audience:** `both` - **created:** `2026-11-09T10:45:09.050155+00:00` - **body sha256:** `25cae2f7d67636d633fcd12dcf9b07bc298e7b45a6800d82721333ffc423b8ee`
- **keywords matched:** CLOSED
- **body (verbatim, machine-copied from the listing JSON):**
```
Decision Module kill channel is the daemon-settings surface (settings:write, DB-backed), never the agent-callable tunable. Read per consult; write then read back and verify, fail closed on mismatch.
```
- **VERDICT: K**
- Keyword closed comes from fail closed on mismatch - fail-closed semantics, not self-closing. The kill-channel rule is live.


### 4. `disk-03-tag-lineage-unmerged-stage-trees`

- **id:** `bed25def-a69b-489a-9f62-9966b3208b24` - **domain:** `environment` / **audience:** `both` - **created:** `2026-11-18T04:49:44.484166+00:00` - **body sha256:** `de55531af2d1de6bd477ea61c372bd9cefc8a95430343ed12bc9c2f702547ab7`
- **keywords matched:** SUPERSEDED
- **body (verbatim, machine-copied from the listing JSON):**
```
Tag lineage check before treating UNMERGED stage worktrees as protected: stage-v0296 was a local-only superseded release; its tag preserves the tree, so superseded stage trees may be removed even if UNMERGED.
```
- **VERDICT: K**
- superseded describes the stage-v0296 RELEASE, not the rows subject. The rule (superseded stage trees may be removed even if UNMERGED) is live.


### 5. `e2s0-01-mirror-claims-and-scratch-db-wall`

- **id:** `ce03ba0c-3ade-450b-8aed-4d0bc26497a8` - **domain:** `environment` / **audience:** `both` - **created:** `2026-11-14T21:04:47.128483+00:00` - **body sha256:** `ab6d4eceda45cec49b2678c4f440109a412bbc0d86accf5a07bbd7ab1a0ca9de`
- **keywords matched:** RESOLVED
- **body (verbatim, machine-copied from the listing JSON):**
```
E2 step-0 fix 9b59bafcd: claims hit resolved ENSEMBLE_PORT_CLAIMS_DIR + per-leg verdicts; refusal legs PASS. Happy env-blocked: no scratch DB ensemble_dev_app can own (rolcreatedb=FALSE), project_future exits 3; needs superuser scratch.
```
- **VERDICT: c**
- Half-closed: the step-0 fix (9b59bafcd) is closed, but the row carries an OPEN blocker - no scratch DB (ensemble_dev_app rolcreatedb=FALSE; project_future exits 3; needs a superuser scratch). JUDGMENT-NEEDED: archive the closed half only after a successor carries the blocker.


### 6. `evidence-commit-ab-fdc2bb4f-restored`

- **id:** `28889a8b-1234-4f28-88c6-6bce224d12ec` - **domain:** `catalog` / **audience:** `both` - **created:** `2026-11-18T08:16:44.945132+00:00` - **body sha256:** `935d37979067667a64acd8ac6c2079304d5cfb815644dda6bcda55136a06f1ae`
- **keywords matched:** RESOLVED
- **body (verbatim, machine-copied from the listing JSON):**
```
evidence-commit skill: fdc2bb4f resolved (winner 9a4a1f54); gen-2 de7a5ffb in group 7a36fb63 = repaired superset, live-surface rules restored, frontmatter stripped. OWED: DB description still pre-FIX text; retire 9a4a1f54 once gen-2 wins.
```
- **VERDICT: c**
- The A/B is resolved, but the row carries an OPEN obligation (OWED: DB description still pre-FIX text; retire 9a4a1f54 once gen-2 wins). JUDGMENT-NEEDED - the obligation must survive in an active row.


### 7. `gc-gate-03-filter-side-pin`

- **id:** `f8a54e0f-a551-4b85-9e70-1d88d647efc6` - **domain:** `verify` / **audience:** `both` - **created:** `2026-11-16T00:43:46.121585+00:00` - **body sha256:** `616ae2e1fa52c545bf7bad6ac6e7a33e9c2863e16ecfe6e9c93bfd544c5f36f6`
- **keywords matched:** SHIPPED
- **body (verbatim, machine-copied from the listing JSON):**
```
gc P3 lesson 2026-11-15: pin the FILTER side when substrate over-includes by design. boot_deliver never called the tag resolver; boot tests pre-advanced the cursor so it shipped. Test: seed a non-target member, no cursor workaround.
```
- **VERDICT: K**
- so it shipped = the defect shipped. The lesson is live (also a CL-10 member).


### 8. `gc-p4-03-note-drop-turn-end-reminder`

- **id:** `a3020315-8141-4552-aaba-3f351e5a27ee` - **domain:** `verify` / **audience:** `both` - **created:** `2026-11-16T10:06:07.988030+00:00` - **body sha256:** `106eca4dd2c78e5a33d8fed5c3dc1790f13cd550d9ee7c6d5f2d0e9ab76b6817`
- **keywords matched:** SUPERSEDED, FIXED
- **body (verbatim, machine-copied from the listing JSON):**
```
SUPERSEDED 2026-11-16: the add/remove_subtask drop is FIXED (c249d7fce). Auditor caution: the inventory pin's no-gap half is a CASE-SENSITIVE 'reminder' docstring scan — a new reminder method whose docstring omits it ESCAPES.
```
- **VERDICT: b** - git history c249d7fce
- SELF-CLOSING: the add/remove_subtask drop is FIXED (c249d7fce) - the episode now lives in git history. Its durable residue (the CASE-SENSITIVE reminder docstring-scan caution) is carried by the CL-14 merged body, so archival is justified IF CL-14 lands first.


### 9. `gc-p4-junitxml-is-the-red-source`

- **id:** `ccf336d0-2ff7-4764-a062-8666083e0bfc` - **domain:** `verify` / **audience:** `both` - **created:** `2026-11-16T07:54:14.071846+00:00` - **body sha256:** `d21d7426907f125f3f3be3c6ec61125da4784f27510be87d89dce25fdbdc45ad`
- **keywords matched:** COMPLETE
- **body (verbatim, machine-copied from the listing JSON):**
```
pytest -r is single-valued: '-rf -rs' yields only SKIPPED; under xdist the test-red FAILED/ERROR summary lines are absent. Harvest BARE-ID red ids from junitxml - it is the only complete red set and its counts match the log summary.
```
- **VERDICT: K**
- the only complete red set - complete as an adjective. The junitxml-is-the-red-source rule is live.


### 10. `group-chat-01-design-landed`

- **id:** `30291831-3763-4e6a-9df4-a4938e0038dc` - **domain:** `artifact` / **audience:** `both` - **created:** `2026-11-17T08:33:48.876784+00:00` - **body sha256:** `54cd89b71fde66b6d66634e5fb1951484d23f6b808610f68f34903f77daa2751`
- **keywords matched:** COMPLETE
- **body (verbatim, machine-copied from the listing JSON):**
```
Group-chat Phase-2 COMPLETE @ 56a5f4a00 (6 approved phases): room store + migration 131, naming cascade, delivery lanes, nudges, tool #23 + joined-room block, UI modal + GC. Next: staged release lane; live promote = Emperor's.
```
- **VERDICT: b** - release tree + git history 56a5f4a00 + the group-chat plan corpus
- Dated snapshot: Group-chat Phase-2 COMPLETE @ 56a5f4a00 with the shipped phase inventory (room store + migration 131, naming cascade, delivery lanes, nudges, tool #23 + joined-room block, UI modal + GC) now living in the release tree / git history / plan corpus. CAUTION: the Next: staged release lane; live promote = Emperors clause is REFRESHABLE-OVERDUE rolling state - route it to KV/board BEFORE archival.


### 11. `group-chat-02-p1-committed-docs-stale`

- **id:** `e71966f5-75fa-49f6-ba95-01768d3afaba` - **domain:** `artifact` / **audience:** `both` - **created:** `2026-11-15T12:40:14.395290+00:00` - **body sha256:** `05b61c81c462f64d8602e11774c2960df0b77f86f80cd74d85b696c287d5b101`
- **keywords matched:** ARCHIVED
- **body (verbatim, machine-copied from the listing JSON):**
```
group-chat P1 @cc44d8416: committed docs carry PRE-ratification 'archived' design; ratified R2 purge wording only in 4 UNCOMMITTED worktree docs (+KV gc_phase2_go); code implements R2 — merge MUST land those docs.
```
- **VERDICT: K**
- committed docs carry PRE-ratification archived design - archived describes the DOCS design, not the rows subject. The row is a LIVE obligation (the merge MUST land those 4 docs).


### 12. `group-chat-p4-01-carrier-reminder-slot-gap`

- **id:** `8ebed7dc-69fc-4c73-a6e1-95b3d0903234` - **domain:** `verify` / **audience:** `both` - **created:** `2026-11-16T10:05:53.348503+00:00` - **body sha256:** `752d5b9d56957941299089931abc78045943d4471ff426466dd2baf02e652080`
- **keywords matched:** SUPERSEDED, FIXED
- **body (verbatim, machine-copied from the listing JSON):**
```
SUPERSEDED 2026-11-16: the 2-path carrier gap is FIXED (c249d7fce). Carrier now on all 5 todo tool paths; manager has 6 reminder methods (add_subtask singular = manager-only, no tool).
```
- **VERDICT: b** - git history c249d7fce
- SELF-CLOSING: the 2-path carrier gap is FIXED (c249d7fce). Durable residue (carrier now on all 5 todo tool paths; manager has 6 reminder methods) is carried by the CL-14 merged body - archival justified IF CL-14 lands first.


### 13. `kv-setkv-replaces-whole-value`

- **id:** `3fbd3865-1fa9-45cc-8a85-eb04f3ad450c` - **domain:** `environment` / **audience:** `operation` - **created:** `2026-11-13T04:50:03.391003+00:00` - **body sha256:** `256509b7690dcde8531a1cf392bf2ffb08bd13d4cfc0a81b51cd3843fd62ffd3`
- **keywords matched:** COMPLETE
- **body (verbatim, machine-copied from the listing JSON):**
```
shared_meta_kv set_kv REPLACES the whole value per key — a partial object clobbers the row. Always rewrite the COMPLETE object (merge prior echo first). 2026-11-13: a census row lost its fields to a partial update; repaired by full rewrite.
```
- **VERDICT: K**
- rewrite the COMPLETE object - complete as an adjective. The set_kv replace-semantics rule is live.


### 14. `plan-body-ledger-agreement`

- **id:** `7c9b9fa5-f991-4d79-bcc5-92156d3cd50b` - **domain:** `verify` / **audience:** `both` - **created:** `2026-11-09T11:57:32.142560+00:00` - **body sha256:** `dc67d7808be60f9051163edaf154702a13a88ceebf1be9a1df7ec7f7cd9de853`
- **keywords matched:** FIXED
- **body (verbatim, machine-copied from the listing JSON):**
```
A plan's ledger can assert FIXED while the BODY still gates on the old predicate. Diff body vs ledger every cycle: body wins; predicate strings must match character-for-character — no marker-grep check detects this mismatch.
```
- **VERDICT: K**
- a plan's ledger can assert FIXED - FIXED describes a ledger CLAIM, not the rows subject. The body-vs-ledger rule is live.


### 15. `prompt-assent-flip-on-gate`

- **id:** `6db7e0fd-a716-4463-8e5f-bfea1a118c87` - **domain:** `deploy` / **audience:** `both` - **created:** `2026-11-18T15:06:50.439055+00:00` - **body sha256:** `8a2bfe88200d722f03c0b3b6450b450a627884f12e18634b3c8c72f2d45028cb`
- **keywords matched:** CLOSED
- **body (verbatim, machine-copied from the listing JSON):**
```
prompt-assent lane DEFAULT-OFF: arm only with ENSEMBLE_PROMPT_ASSENT_ENABLED=1 + executed+ledgered K-3 Discord 1:1-vs-group-DM probe. INVALID env value fails closed AT BOOT; unset boots clean; verbs refuse lane-disabled.
```
- **VERDICT: K**
- fails closed AT BOOT - fail-closed semantics. NOTE: this row is a phase-2 MERGE SURVIVOR (survives prompt-assent-flip-on-gate) - do not touch.


### 16. `provider-01-402-wake-kill`

- **id:** `6f44fde8-da8c-41b5-b55f-ed0ec4da66e4` - **domain:** `environment` / **audience:** `operation` - **created:** `2026-11-12T08:29:06.517028+00:00` - **body sha256:** `b1c4d23ec7362451a1f3aa2cdeb993b3032b29a9d58509448034d4c5d5ed1f6f`
- **keywords matched:** LANDED
- **body (verbatim, machine-copied from the listing JSON):**
```
Provider 402 (insufficient_balance: coding-plan quota + empty wallet) killed 4 turns in 2m10s on 2026-11-12 07:48-07:50Z across 2 projects; each instance landed status=error, no auto-retry. Failover recovered 08:13Z; wallet still owed.
```
- **VERDICT: c**
- The 402 episode is closed (failover recovered 08:13Z), but the row carries an OPEN obligation (wallet still owed) plus a durable mechanism fact (provider 402 insufficient_balance -> turns land status=error, no auto-retry) that no other active row carries. JUDGMENT-NEEDED.


### 17. `py-fromimport-staticmethod-importerror`

- **id:** `b43c6db1-91cd-429e-ae41-3d1f3f2d065f` - **domain:** `artifact` / **audience:** `both` - **created:** `2026-11-15T14:28:07.014702+00:00` - **body sha256:** `6cf6bf8a1831118bfc1ddcbbd575bad1e6c468696f623b479308ed9ba86af795`
- **keywords matched:** FIXED
- **body (verbatim, machine-copied from the listing JSON):**
```
Latent P2 defect fixed: instance_lifecycle.py imported staticmethod _raise_typed_integrity module-level; collision path would ImportError. Gotcha: from-imports cannot reach class attributes; use RoomRepository._raise_typed_integrity.
```
- **VERDICT: c**
- The latent P2 defect is fixed, but the durable GOOTCHA (from-imports cannot reach class attributes; use RoomRepository._raise_typed_integrity) is carried by no other active row. JUDGMENT-NEEDED - successor row with the gotcha, then archive.


### 18. `quarantine-01-chart-seal-row-is-stale`

- **id:** `3087ee94-3800-49f9-8748-4f81f1c4432d` - **domain:** `verify` / **audience:** `both` - **created:** `2026-11-09T11:01:46.042277+00:00` - **body sha256:** `2c7f50fdcf7a40a81e6af1b0fc47b9101c836cd3ee5971b6db33fab3aae72f86`
- **keywords matched:** WAS-TRUE-ONLY
- **body (verbatim, machine-copied from the listing JSON):**
```
QUARANTINE.md 'chart stale-seals x1' row is STALE — chart seal tripwire is GREEN at base 818411107 (35/35 seal rows match). Row was true only at its own base 1fecf7c3f (7 stale rows), repaired by 2026-11-05/07 re-pins.
```
- **VERDICT: K**
- Row was true only at its own base 1fecf7c3f - past-tense scope, not a self-closing marker. The row is a live CORRECTION of a stale QUARANTINE row.


### 19. `review-01-shared-helper-blast-radius`

- **id:** `bdfb7de6-1fbb-4c4b-8217-42c3389a73a1` - **domain:** `verify` / **audience:** `both` - **created:** `2026-11-18T06:33:11.015710+00:00` - **body sha256:** `dc26e4defb235a56dd1ced93fd9ac1ba4a05cbb8dd1b08ebc5e7f7f7a0393228`
- **keywords matched:** SHIPPED
- **body (verbatim, machine-copied from the listing JSON):**
```
A fix inside a shared frontend helper has blast radius across shipped lanes: count call sites, classify shipped vs new, and disclose shipped-copy changes as a deviation entry pinned by a test, never as a lane-local fix.
```
- **VERDICT: K**
- shipped lanes describes shipped surfaces, not the rows subject. The shared-helper blast-radius rule is live.


### 20. `review-02-describe-http-error-call-sites`

- **id:** `783a1451-b41b-47c9-9e5f-1152480a2109` - **domain:** `verify` / **audience:** `both` - **created:** `2026-11-18T06:33:11.025553+00:00` - **body sha256:** `db026b8ac81d682bcadcc159c1c2d667fa6e95756a96f34bea8f24e057160e9f`
- **keywords matched:** SHIPPED
- **body (verbatim, machine-copied from the listing JSON):**
```
describeHttpError (frontend/src/app/services/room.service.ts) has 3 call sites: rooms :200 and messages :247 are SHIPPED, global :311 is NEW. Fixing its 5xx transport-string leak alters shipped copy: a shipped-surface delta.
```
- **VERDICT: a** - bdfb7de6-1fbb-4c4b-8217-42c3389a73a1 (review-01-shared-helper-blast-radius)
- The durable rule it carries (shared-helper blast radius; classify shipped vs new; disclose shipped-copy changes as a deviation entry, never as a lane-local fix) is covered by the ACTIVE row named here. Its residual content (describeHttpError's 3 call sites) is a dated inventory of one PR - lives in git history.


### 21. `skill-e6-03-promote-did-not-resolve-backlog`

- **id:** `d5fde27d-8a2f-477e-b400-4faf1edb6216` - **domain:** `verify` / **audience:** `operation` - **created:** `2026-11-18T05:45:05.637552+00:00` - **body sha256:** `11c792924b977c2ece288155f6ef68c3427a0ca6ebd55b915f2c9abc64f4c7e2`
- **keywords matched:** RESOLVED
- **body (verbatim, machine-copied from the listing JSON):**
```
E-6 'resolve AT PROMOTE' UNMET by the sweep: 0 sweep-resolved since v0.31.2 (04:38:30Z); 2 pairs resolved by operator lane (05:31Z, 05:33Z). The wiring only ARMED the idle-gated sweep; the rest need an idle tick or explicit operator action.
```
- **VERDICT: K**
- resolved is a METRIC noun (0 sweep-resolved); the rows subject is UNMET (resolve AT PROMOTE not satisfied). Keep active.


### 22. `skill-e6-04-a4-gauge-grew-not-collapsed`

- **id:** `0df359fe-4a6b-4c93-9cae-816798a97ee5` - **domain:** `verify` / **audience:** `operation` - **created:** `2026-11-18T05:16:33.253962+00:00` - **body sha256:** `54de7b850a4b3ac08f34682a2fafd1039815d914a8973318ebf507e101957ca2`
- **keywords matched:** LANDED
- **body (verbatim, machine-copied from the listing JSON):**
```
A4 gauge has NOT collapsed: watch #7 40 keys/100 rows, watch #8 44 keys/112 rows (+4/+12) - diverging families minted faster than resolutions landed. Gauge counts ALL rows incl. inactive; test on KEYS count.
```
- **VERDICT: K**
- resolutions landed - landed as a verb. The row records a gauge that has NOT collapsed. Also rolling state (watch #7/#8 counts) - a routing candidate for KV/board, but not a completion marker.


### 23. `skill-lane-flip-verify-restart`

- **id:** `83a4b2d3-dd41-4f85-b799-11db50aefaee` - **domain:** `deploy` / **audience:** `operation` - **created:** `2026-11-10T21:19:10.228544+00:00` - **body sha256:** `45bd19d332093dff53d7f1be7a5c62feedef010236126df3ab252486018cee04`
- **keywords matched:** LANDED
- **body (verbatim, machine-copied from the listing JSON):**
```
Skill lane flips: the live .env edit does not reach a running daemon. Verify the restart landed by reading /proc/<pid>/environ for ENSEMBLE_SKILL_CAPTURE_ENABLED=1, or by journal restart timestamps.
```
- **VERDICT: K**
- Verify the restart landed - landed as a verb. The /proc/<pid>/environ verification recipe is live.


### 24. `skillab-02-keeper-rule-zero-traffic-fix`

- **id:** `63f64bbe-4f96-45a7-860e-bafb07aa7fca` - **domain:** `artifact` / **audience:** `both` - **created:** `2026-11-11T07:25:07.428529+00:00` - **body sha256:** `a15ec9dc12331ea51cf1ae42d61a42b2be15ab7848714935f76124614d26ae8b`
- **keywords matched:** DEFERRED
- **body (verbatim, machine-copied from the listing JSON):**
```
Keeper rule: a FIX on a zero-traffic skill never resolves via A/B (comparisons stays 0); the 'zero uses deactivate bypassing A/B' doctrine has NO implementation. Re-assert: skill_feedback note + deferred skill_fix RE-TARGET; report stall.
```
- **VERDICT: K**
- deferred skill_fix RE-TARGET - deferred describes the deferral MECHANISM, not a closed episode. Keep active (also a CL-16 member).


### 25. `skillab-03-comparison-counter-semantics`

- **id:** `af8815b5-5623-439d-97d7-973bfe7a5c30` - **domain:** `artifact` / **audience:** `both` - **created:** `2026-11-11T07:28:05.656366+00:00` - **body sha256:** `490b5b75cd29334a80f339cee531e0775661bc8544dca386cd01b2f6b598bc4e`
- **keywords matched:** DEFERRED
- **body (verbatim, machine-copied from the listing JSON):**
```
comparisons bumps ONLY on genuine injections (_select_ab_variant→increment_comparison); record_feedback on-miss rows do NOT. No autonomous resolver — re-run deferred FIX manually once real injections accumulate.
```
- **VERDICT: K**
- re-run deferred FIX manually - deferred describes the FIX, not a closed episode. Keep active (also a CL-16 member).


### 26. `skillab-manifest-01-no-consumer`

- **id:** `b0611691-7a2e-4756-95ad-882a23131455` - **domain:** `verify` / **audience:** `both` - **created:** `2026-11-18T06:50:51.609739+00:00` - **body sha256:** `1d3bd0f25479737e5de4a4ca96992d247be47862c1ba3ecdba9a04b94936e64e`
- **keywords matched:** RESOLVED
- **body (verbatim, machine-copied from the listing JSON):**
```
skill_ab_disposition_manifest has ZERO daemon consumers. Sweep uses the plain newer-gen default; declared pairs do NOT auto-divert. 3 of 6 resolved 2026-11-18; 3 remain open, crossing the 7d gate 2026-11-23 ~08:00-09:21Z.
```
- **VERDICT: K**
- 3 of 6 resolved but 3 remain open, crossing the 7d gate 2026-11-23 - the subject is NOT closed. Keep active; the open deadline must be routed to KV/board (also a CL-08 member).


### 27. `sonic-03-blast-gate-removed`

- **id:** `ebe4f878-ac79-4d9e-8ccb-ceebedfa1aa4` - **domain:** `other` / **audience:** `operation` - **created:** `2026-11-14T04:03:47.113895+00:00` - **body sha256:** `0a9703ee2bdfa615fa633e70f6d801650f5a0332f32b0bf2f11392e0c973cdeb`
- **keywords matched:** SUPERSEDED
- **body (verbatim, machine-copied from the listing JSON):**
```
'Daemon work never routes to sonic' + [sonic-blast-radius-v1] gate: SUPERSEDED/REMOVED by Ruling A (staged v0.30.5, feature/sonic-routing-v0305). Quick core-file changes are legitimate sonic work; routing is complexity-only.
```
- **VERDICT: a** - 700bd23b-e8b9-4f12-9858-1b133cae1047 (sonic-02-complexity-routing-doctrine)
- SELF-CLOSING: the Daemon work never routes to sonic + [sonic-blast-radius-v1] gate is SUPERSEDED/REMOVED by Ruling A (staged v0.30.5, feature/sonic-routing-v0305). Its durable replacement rule (Quick core-file changes are legitimate sonic work; routing is complexity-only) is covered by the ACTIVE row named here; the episode lives in the Ruling.


### 28. `sonic-bump-version-selfcommits`

- **id:** `df5f8463-318a-4373-8c79-5f5bc102cfba` - **domain:** `deploy` / **audience:** `operation` - **created:** `2026-11-14T19:26:08.401602+00:00` - **body sha256:** `af6d15042df3cd963c8aeb4f774cf6f0e1eb6acdb7cf782ddd3c2ea2cf0414b9`
- **keywords matched:** FINAL
- **body (verbatim, machine-copied from the listing JSON):**
```
bump_version.py SELF-COMMITS each run ("! New version X") + runs uv sync — skip a claimed slot by running patch TWICE from base and tagging only the final; the intermediate bump commit rides the branch untagged.
```
- **VERDICT: K**
- tagging only the final - final as an adjective. The bump_version self-commit rule is live.


### 29. `w3-f18-store-not-callable-typeerror`

- **id:** `82a3ca1c-07ad-4fc9-9ca6-6bbd65e97b06` - **domain:** `other` / **audience:** `implementation` - **created:** `2026-11-11T12:47:37.793468+00:00` - **body sha256:** `7c778caf134a5235449c7cf0ad4fecadc3ca9dcdf77f3acf18ca81c5e175b38a`
- **keywords matched:** FIXED
- **body (verbatim, machine-copied from the listing JSON):**
```
Routed (not fixed in the bind-race lane): w3 F18 metrics wiring raises TypeError every ~10s — 'InMemoryMachineStore'/'TombstoneStore' object is not callable at daemon/services/metrics_query/wiring/w3.py:234,240 — keeping F18 dark.
```
- **VERDICT: K**
- Routed (not fixed in the bind-race lane) - fixed inside a NEGATION. The subject is an OPEN defect (TypeError every ~10s at daemon/services/metrics_query/wiring/w3.py:234,240, keeping F18 dark). Keep active.


## 4. What PM should do with this scan

- **2 rows** (verdict a) may be archived once PM confirms the named covering row is still ACTIVE and carries the fact.
- **3 rows** (verdict b) may be archived once PM confirms the named home actually holds the content - note the two CL-14-dependent rows (group-chat-p4-01, gc-p4-03): they are only archivable AFTER the CL-14 merged row lands, because that row carries their durable residue.
- **4 rows** (verdict c) must NOT be archived as-is: each carries either an open obligation (DB description still pre-FIX; retire 9a4a1f54 once gen-2 wins; wallet still owed) or a durable fact no other active row holds (the provider-402 no-auto-retry mechanism; the from-import/staticmethod gotcha; the scratch-DB CREATEDB blocker). PM should write a successor carrying that content first, then archive the episode row.
- **20 rows** (verdict K) stay active untouched. Three of them (skill-e6-04, skillab-manifest-01, and the group-chat-01 Next clause) are rolling state and are routing candidates for KV / board / release tree under the durable-vs-rolling-state doctrine - that is a routing decision, NOT an archival one.

*Generated by `_laneB_generate.py` from `live-active-2026-11-18-post-phase1.json` (sha256 `2db52522983a1a1116e6961522ad487f0808d313ec3970cdff9d581e26731408`). No database access; no POK writes.*

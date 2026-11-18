# POK sweep wave 2, lane B - CLUSTER DERIVATION (2026-11-18)

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
| Active rows analysed | 277 |
| Candidate clusters adjudicated | 20 |
| Clusters with COVERAGE = YES (merge proposed) | 10 |
| Clusters with COVERAGE = NO (DO-NOT-MERGE) | 10 |
| Rows freed if all YES clusters land | 11 |
| Active rows NOT in any cluster | 234 |

**Yield-ranked merge list**

| Rank | Cluster | Survivor slug | Members | Rows freed | Merged body chars | Coverage |
|---|---|---|---|---|---|---|

| 1 | CL-01 - P11 git-span / source-gc drift-guard landing shape (near-identical pair) | `land-01-merge-shape-guards` | 2 | 1 | 229 | YES |
| 2 | CL-02 - 9797 listener close order + the teardown never holds 9797 | `shutdown-01-teardown-order` | 3 | 2 | 226 | YES |
| 3 | CL-03 - OpenRouter: existing credential vs missing decision model row | `proxydec-01-openrouter-key-held` | 2 | 1 | 229 | YES |
| 4 | CL-04 - ENSEMBLE_AUTH_* ambient scrub family - the two-member core | `gate-auth-env-302-poison` | 2 | 1 | 240 | YES |
| 5 | CL-05 - Mutation proof must attack the pins OWN property (2-member core) | `mutation-proof-must-target-pin-property` | 2 | 1 | 239 | YES |
| 6 | CL-08 - skill_ab_disposition_manifest: zero consumers + withdrawn claims | `skillab-manifest-01-no-consumer` | 2 | 1 | 217 | YES |
| 7 | CL-09 - snapshot-l3 red-set shape: 2 detectors + 4 invariance guards | `verify-02-snapshot-l3-red-set` | 2 | 1 | 238 | YES |
| 8 | CL-10 - gc P3: pin the FILTER side (cursor-pre-advance blind spot) | `gc-gate-03-filter-side-pin` | 2 | 1 | 238 | YES |
| 9 | CL-13 - Port resolution: daemon PORT source + the 9798 metrics knob (a CORRECTION merge) | `portfence-01-daemon-port-comes-from-port-env` | 2 | 1 | 237 | YES |
| 10 | CL-14 - Todo-tool carrier state + the case-sensitive reminder-pin caution | `gc-p4-03-note-drop-turn-end-reminder` | 2 | 1 | 233 | YES |

**DO-NOT-MERGE clusters (blocked by the 240-char cap, not by lack of redundancy):** CL-04b, CL-05b, CL-06, CL-07, CL-11, CL-12, CL-15, CL-16, CL-17, CL-18


## 2. Clusters with COVERAGE = YES (merge proposed)


### CL-01 (rank 1) - P11 git-span / source-gc drift-guard landing shape (near-identical pair)

- **Survivor slug:** `land-01-merge-shape-guards` (reuse a member slug; retrieval vocabulary preserved)
- **Members:** 2 - **rows freed:** 1 - **merged body length:** 229/240 chars (machine-verified)
- **COVERAGE VERDICT: YES** - merged body <= 240 chars and every member's durable fact is carried (all 6 required phrases present as literal substrings)

**Member bodies (verbatim, machine-copied from the listing JSON)**


- **`land-01-merge-shape-guards`** - id `7d38955d-869b-4453-a5af-157e5ff43ec6` - domain `verify` / audience `both` - created `2026-11-11T06:49:04.417047+00:00` - body sha256 `c22825efbdffd6ee57ca603c71cf8dfde215d73255ed33ac106c42cc26ec1017`
```
Git-span drift guards (TestP11BootNoBehaviorChange) are green ONLY on a --no-ff merged shape; squash/rebase keeps them red by design. Release landings must be merge --no-ff, then verify first-parent merge and empty span diff.
```


- **`source-gc-drift-guard-landing-shape`** - id `26cbdc9d-e2f4-43d9-939d-05e874263a94` - domain `verify` / audience `both` - created `2026-11-11T05:23:29.143835+00:00` - body sha256 `804d5c06cb39f1c0d729fe4386c3dc00d3411d8043f06edbe71d53772c3cb7d7`
```
P11 source-gc drift guards turn GREEN only if the landing commit is a merge on the first-parent chain (git merge --no-ff). Squash/rebase landings leave the span non-empty and the guards stay RED.
```


**Proposed merged body (229/240 chars)**
```
P11 drift guards (TestP11BootNoBehaviorChange) turn GREEN only on a merge --no-ff landing on the first-parent chain; squash/rebase leaves the span non-empty, guards stay RED by design. Verify first-parent merge + empty span diff.
```
**Why this merge:** Both members assert the same rule in different words: the drift guard only greens on a --no-ff first-parent merge; a squash/rebase landing leaves a non-empty span and the guard red. land-01 adds the verification step (first-parent merge + empty span diff); source-gc names the guard family (P11 source-gc). The union fits the 240-char budget with every durable fact intact.


### CL-02 (rank 2) - 9797 listener close order + the teardown never holds 9797

- **Survivor slug:** `shutdown-01-teardown-order` (reuse a member slug; retrieval vocabulary preserved)
- **Members:** 3 - **rows freed:** 2 - **merged body length:** 226/240 chars (machine-verified)
- **COVERAGE VERDICT: YES** - merged body <= 240 chars and every member's durable fact is carried (all 7 required phrases present as literal substrings)

**Member bodies (verbatim, machine-copied from the listing JSON)**


- **`shutdown-01-teardown-order`** - id `41408276-aea2-437b-b2e4-507ea11802ad` - domain `verify` / audience `both` - created `2026-11-11T13:02:11.556814+00:00` - body sha256 `751da316281c02057f7ff5c8aa714779d174ab76b176d243da36b223e15b71db`
```
Daemon wind-down: uvicorn closes the 9797 listener first (server.py:275-276), then drains (60s cap), then runs the api.py lifespan teardown (3987-4506) and manager.shutdown() (manager.py:16514).
```


- **`bind-03-uvicorn-closes-9797-first`** - id `59ea590b-89ee-42c5-b453-43dcd452b033` - domain `artifact` / audience `both` - created `2026-11-11T13:12:19.440326+00:00` - body sha256 `1348999b7d1063bf8d7b6dc5d146e0be4e4fee339cd69d65eb7b96a605fa465c`
```
uvicorn closes the 9797 LISTENING socket FIRST in Server.shutdown(), before the ≤60s drain and lifespan shutdown. The daemon teardown never holds 9797; 9798 closes late (api.py step 19). The 60-75s wind-down premise is FALSE for 9797.
```


- **`shutdown-02-port-hold-truth`** - id `72ef05e8-738d-4d79-afee-b72b034f7b3b` - domain `verify` / audience `both` - created `2026-11-11T13:02:30.129458+00:00` - body sha256 `7178903bccdcafb30d4c9ea376f150ff677ef1d55722764500bb29a7f6f417ad`
```
9797 is NOT held by the daemon wind-down (listener closes first). The 2026-11-11 EADDRINUSE loop was an orphaned ensemble-prod app holding the LISTEN socket; systemd cgroup cleanup freed it at 11:54:52Z.
```


**Proposed merged body (226/240 chars)**
```
Daemon wind-down: uvicorn closes the 9797 LISTENING socket FIRST in Server.shutdown(), before the 60s drain, then the api.py lifespan teardown + manager.shutdown(). Teardown never holds 9797; 9798 closes late (api.py step 19).
```
**Why this merge:** Three rows assert one mechanism from three angles: the close order (shutdown-01), the never-holds-9797 consequence + late 9798 close (bind-03), and the EADDRINUSE misattribution that follows (shutdown-02). Merging the order + the consequence into one row removes the contradiction surface between the teardown holding 9797 and it not holding 9797.

**ROUTED OUT (not carried by the merged body - confirm the home before archiving the member):**

- Line anchors server.py:275-276 / api.py 3987-4506 / manager.py:16514 - file-level anchors retained where they fit; exact line numbers drift (ANCHORS-ARE-LEADS convention).
- The 2026-11-11 EADDRINUSE-loop attribution (an orphaned daemon app child held the LISTEN socket; systemd cgroup cleanup freed it at 11:54:52Z) - dated incident, lives in the daemon log / incident record.


### CL-03 (rank 3) - OpenRouter: existing credential vs missing decision model row

- **Survivor slug:** `proxydec-01-openrouter-key-held` (reuse a member slug; retrieval vocabulary preserved)
- **Members:** 2 - **rows freed:** 1 - **merged body length:** 229/240 chars (machine-verified)
- **COVERAGE VERDICT: YES** - merged body <= 240 chars and every member's durable fact is carried (all 5 required phrases present as literal substrings)

**Member bodies (verbatim, machine-copied from the listing JSON)**


- **`proxydec-01-openrouter-key-held`** - id `fc96dc4b-b385-4bf3-86c9-4f31d34e9e1d` - domain `deploy` / audience `both` - created `2026-11-09T10:53:40.141037+00:00` - body sha256 `63a2b08954b04afc7f5f6c26671fa13d72c6cb1156ae1af0a2ada59cb1b5d197`
```
Proxy store already holds a working OpenRouter key (id=openrouter, len 73, sha16 5add86cdf9f95859). It serves System One (model typesafe/jev-1.13 -> 200). The openrouter decision gap is a missing model row, not a key.
```


- **`sysone-03-openrouter-decision-model-row`** - id `da205d55-4c98-4bed-97b9-dae06f5e1731` - domain `deploy` / audience `both` - created `2026-11-09T11:02:21.801429+00:00` - body sha256 `f60aae86375d6f8e0a2b03c647c33eee0e7fd0455fb412e5548a0e742befe729`
```
OpenRouter decision provider needs NO new key: add a decision-kind model row bound to credentials.id=openrouter with internal_model=typesafe/jev-1.13 (provider-prefixed). The gap was a missing model row, not a credential.
```


**Proposed merged body (229/240 chars)**
```
No new OpenRouter key needed - the proxy store holds a working one (credentials.id=openrouter). The gap is a MISSING MODEL ROW, not a credential: add a decision-kind row bound to that credential, internal_model=typesafe/jev-1.13.
```
**Why this merge:** The two members are the same conclusion from two directions: the credential exists, the decision lane is unconfigured. Merging makes the actionable half (add the model row) the headline and removes a duplicate no-new-key-needed statement.

**ROUTED OUT (not carried by the merged body - confirm the home before archiving the member):**

- Key fingerprint (len 73, sha16 5add86cdf9f95859) - rotating secret inventory, not a durable fact.
- serves System One (-> 200) and model ids must be provider-prefixed - both already carried by the ACTIVE row sysone-02-openrouter-route (ce9f71f3), so no fact leaves the store.


### CL-04 (rank 4) - ENSEMBLE_AUTH_* ambient scrub family - the two-member core

- **Survivor slug:** `gate-auth-env-302-poison` (reuse a member slug; retrieval vocabulary preserved)
- **Members:** 2 - **rows freed:** 1 - **merged body length:** 240/240 chars (machine-verified)
- **COVERAGE VERDICT: YES** - merged body <= 240 chars and every member's durable fact is carried (all 7 required phrases present as literal substrings)

**Member bodies (verbatim, machine-copied from the listing JSON)**


- **`gate-auth-env-302-poison`** - id `c15be838-547b-48b0-9544-c190b2fb7ac5` - domain `verify` / audience `operation` - created `2026-11-14T04:58:54.308369+00:00` - body sha256 `fe95d64c3775d6ea9785e4ff3e8497d048c9e22a5346240db59dffd11d03ea33`
```
Ambient ENSEMBLE_AUTH_ENABLED=true makes API tests importing daemon.api.app 302-fail (request_denied_302): head==base both red, auth-scrub rerun green 26/26. Scrub ENSEMBLE_AUTH_* per-variable with POSTGRES_* at every gate pytest leg.
```


- **`prompt-assent-05-auth-env-scrub-family`** - id `d51b6509-1dfa-4f97-b4e3-60ade9192d38` - domain `verify` / audience `both` - created `2026-11-12T04:04:22.388662+00:00` - body sha256 `8732f03aef7be571b6fc66d131eb5e76332c7d60ae6e1b0c3fa4724c4d4bb898`
```
prompt-assent unit legs: ambient ENSEMBLE_AUTH_ENABLED=true (live shells) leaks via pydantic-settings, failing 2 kill_switch config nodes; scrub the FULL ENSEMBLE_AUTH_* family per-variable, not just ISSUER/DISCOVERY_URL.
```


**Proposed merged body (240/240 chars)**
```
Scrub the FULL ENSEMBLE_AUTH_* family per-variable at every gate leg - never just ISSUER/DISCOVERY_URL. Ambient ENSEMBLE_AUTH_ENABLED=true leaks via pydantic-settings: API tests 302-fail (request_denied_302), 2 kill_switch config nodes red.
```
**Why this merge:** These two members carry the same rule with the same two symptom surfaces (the 302 on API tests; the pydantic-settings leak reddening config nodes). The rule + both symptoms fit the budget. The other two members of the family are NOT merged (see CL-04b).


### CL-05 (rank 5) - Mutation proof must attack the pins OWN property (2-member core)

- **Survivor slug:** `mutation-proof-must-target-pin-property` (reuse a member slug; retrieval vocabulary preserved)
- **Members:** 2 - **rows freed:** 1 - **merged body length:** 239/240 chars (machine-verified)
- **COVERAGE VERDICT: YES** - merged body <= 240 chars and every member's durable fact is carried (all 6 required phrases present as literal substrings)

**Member bodies (verbatim, machine-copied from the listing JSON)**


- **`mutation-proof-must-target-pin-property`** - id `b2d5169e-e5bf-4d9f-9a14-d8175becfe6b` - domain `verify` / audience `both` - created `2026-11-11T08:16:50.526070+00:00` - body sha256 `7a2b242955d082544128892c02ad400232be6d19415e6e93c1209ee103fb65a5`
```
A mutation proof must attack the pin's OWN property: if a repaired pin is deliberately immune to a defect class, removing that class cannot redden it. Inverted proof spec, not a weak pin.
```


- **`commissioned-proof-list-is-not-ground-truth`** - id `e9d9e684-47c7-472e-a1bb-f6ab43702f47` - domain `verify` / audience `both` - created `2026-11-11T08:17:41.995783+00:00` - body sha256 `ddbacb5fe8b116845da6af826ae5c8315d3e1cb83e99c4603beb8aae1c8eb679`
```
Commissioned proof lists are inputs, not ground truth: if the repaired pin is deliberately immune to that defect class, the spec is inverted — re-derive it; don't call the pin weak, don't paper over a non-biting plant.
```


**Proposed merged body (239/240 chars)**
```
A pin deliberately immune to a defect class cannot redden if that class is removed - the proof SPEC is inverted, not a weak pin. Commissioned proof lists are inputs, not ground truth: re-derive the spec, dont paper over a non-biting plant.
```
**Why this merge:** Both members are the same inverted-proof-spec rule: a repaired pin that is deliberately immune to a defect class cannot redden when that class is removed, so the SPEC is inverted - and a commissioned proof list is an input, not ground truth. One row carries both halves.


### CL-08 (rank 6) - skill_ab_disposition_manifest: zero consumers + withdrawn claims

- **Survivor slug:** `skillab-manifest-01-no-consumer` (reuse a member slug; retrieval vocabulary preserved)
- **Members:** 2 - **rows freed:** 1 - **merged body length:** 217/240 chars (machine-verified)
- **COVERAGE VERDICT: YES** - merged body <= 240 chars and every member's durable fact is carried (all 5 required phrases present as literal substrings)

**Member bodies (verbatim, machine-copied from the listing JSON)**


- **`skillab-manifest-01-no-consumer`** - id `b0611691-7a2e-4756-95ad-882a23131455` - domain `verify` / audience `both` - created `2026-11-18T06:50:51.609739+00:00` - body sha256 `1d3bd0f25479737e5de4a4ca96992d247be47862c1ba3ecdba9a04b94936e64e`
```
skill_ab_disposition_manifest has ZERO daemon consumers. Sweep uses the plain newer-gen default; declared pairs do NOT auto-divert. 3 of 6 resolved 2026-11-18; 3 remain open, crossing the 7d gate 2026-11-23 ~08:00-09:21Z.
```


- **`skillab-manifest-02-claims-withdrawn`** - id `e22957fd-6b5d-43ff-b486-2dead9206b69` - domain `verify` / audience `both` - created `2026-11-18T06:47:29.138137+00:00` - body sha256 `f74337fce61dea0fea43f372f93d5e165c0ada2d1e128a39da928ee9e9cc41ac`
```
E-6 manifest: 3 of 6 verified-defective claims withdrawn 2026-11-18 (rows 8f8cee3a, b77128c5, 1b8e4d80 - no finding). Data-only, zero daemon consumers; pin enforces a named evidence id per entry. Never blanket-apply.
```


**Proposed merged body (217/240 chars)**
```
skill_ab_disposition_manifest: ZERO daemon consumers (data-only) - the sweep uses the plain newer-gen default and declared pairs do NOT auto-divert; the pin enforces a named evidence id per entry, never blanket-apply.
```
**Why this merge:** Two members, one subject: the manifest is data-only with no daemon consumer, the sweep uses the plain newer-gen default (declared pairs do not auto-divert), and the pin enforces a named evidence id per entry and is never blanket-applied. Everything durable fits.

**ROUTED OUT (not carried by the merged body - confirm the home before archiving the member):**

- 3 of 6 resolved 2026-11-18 / 3 remain open, crossing the 7d gate 2026-11-23 ~08:00-09:21Z - ROLLING STATE. It must be routed to the KV / board BEFORE the members are archived, or the open deadline is lost.
- 3 of 6 verified-defective claims withdrawn (rows 8f8cee3a, b77128c5, 1b8e4d80 - no finding) - dated inventory of the manifest artifact; the manifest + its evidence-id pin live in the repo.


### CL-09 (rank 7) - snapshot-l3 red-set shape: 2 detectors + 4 invariance guards

- **Survivor slug:** `verify-02-snapshot-l3-red-set` (reuse a member slug; retrieval vocabulary preserved)
- **Members:** 2 - **rows freed:** 1 - **merged body length:** 238/240 chars (machine-verified)
- **COVERAGE VERDICT: YES** - merged body <= 240 chars and every member's durable fact is carried (all 10 required phrases present as literal substrings)

**Member bodies (verbatim, machine-copied from the listing JSON)**


- **`verify-02-snapshot-l3-red-set`** - id `c2915561-aae8-49a1-af92-eb1247c05814` - domain `verify` / audience `both` - created `2026-11-09T11:50:43.012591+00:00` - body sha256 `cddf3c121c018bdb3568212aa0bc0695c438932ac5dd0551aa8ee0557f59fc9a`
```
snapshot-l3 red receipt: of 6 new tests exactly 2 are red-at-base by design (LEG A malformed stage-3 -> reranked[:limit]; LEG B prompt stage2_score field); the other 4 are green-at-base regression guards. Expecting all 6 red is WRONG.
```


- **`verify-03-two-detectors-four-guards`** - id `65759d88-be5d-4524-9fc4-8804931cce30` - domain `verify` / audience `both` - created `2026-11-09T11:51:28.645527+00:00` - body sha256 `8266ffd6189257478abc3ab122a5bc096e8d46e5b8d80ad70e5c6656b8afbac9`
```
snapshot-l3: 4 of 6 new tests are green-at-base invariance guards (declined-stays-empty, selection-follows-pick, exception-path baseline, prompt-window + JSON contract); only 2 are detectors. Deleted FALSE comment = not lost coverage.
```


**Proposed merged body (238/240 chars)**
```
snapshot-l3: of 6 new tests exactly 2 are red-at-base by design (LEG A malformed stage-3 -> reranked[:limit]; LEG B prompt stage2_score); 4 are green-at-base invariance guards. All 6 red is WRONG; a deleted FALSE comment != lost coverage.
```
**Why this merge:** Both members state the same 2-vs-4 split of the 6 new snapshot-l3 tests from opposite sides, and both carry the anti-misreading clause (expecting all 6 red is wrong; a deleted FALSE comment is not lost coverage). One row carries it.

**ROUTED OUT (not carried by the merged body - confirm the home before archiving the member):**

- The named list of the 4 green-at-base guards (declined-stays-empty, selection-follows-pick, exception-path baseline, prompt-window + JSON contract) - inventory of a landed test set; the tests live in the snapshot-l3 lane test files.
- snapshot-l3-02-base-red-set-one (d31fa038) is NOT merged: its content is a dated base-leg baseline (base 818411107, 172 nodes / 3 files, suite red set = exactly 1) - see the completion scan.


### CL-10 (rank 8) - gc P3: pin the FILTER side (cursor-pre-advance blind spot)

- **Survivor slug:** `gc-gate-03-filter-side-pin` (reuse a member slug; retrieval vocabulary preserved)
- **Members:** 2 - **rows freed:** 1 - **merged body length:** 238/240 chars (machine-verified)
- **COVERAGE VERDICT: YES** - merged body <= 240 chars and every member's durable fact is carried (all 9 required phrases present as literal substrings)

**Member bodies (verbatim, machine-copied from the listing JSON)**


- **`gc-p3-04-test-preadvance-blindspot`** - id `9e76cac1-2282-4752-b11c-4e51611247b3` - domain `verify` / audience `both` - created `2026-11-16T00:59:17.059192+00:00` - body sha256 `5c70ff9c9fbffbd69d3fabb986352804be42acc19a47dd777c3bb96ed070fd91`
```
gc P3 F-1 gate lesson: cursor-pre-advance fixtures blind tests to over-inclusion defects — 5 boot tests hid boot_deliver routing all over-included pairs. Boot passes need a no-workaround pin resolving targets before CAS or cursor advance.
```


- **`gc-gate-03-filter-side-pin`** - id `f8a54e0f-a551-4b85-9e70-1d88d647efc6` - domain `verify` / audience `both` - created `2026-11-16T00:43:46.121585+00:00` - body sha256 `616ae2e1fa52c545bf7bad6ac6e7a33e9c2863e16ecfe6e9c93bfd544c5f36f6`
```
gc P3 lesson 2026-11-15: pin the FILTER side when substrate over-includes by design. boot_deliver never called the tag resolver; boot tests pre-advanced the cursor so it shipped. Test: seed a non-target member, no cursor workaround.
```


**Proposed merged body (238/240 chars)**
```
pin the FILTER side on over-inclusion by design - boot_deliver never called the tag resolver; cursor-pre-advance fixtures hide over-inclusion defects. No-workaround pin: resolve targets before CAS/cursor advance; seed a non-target member.
```
**Why this merge:** Two members, one lesson: when the substrate over-includes by design, pin the FILTER side - boot_deliver never called the tag resolver and cursor-pre-advance fixtures hid the defect, so the pin must resolve targets before CAS/cursor advance and seed a non-target member.


### CL-13 (rank 9) - Port resolution: daemon PORT source + the 9798 metrics knob (a CORRECTION merge)

- **Survivor slug:** `portfence-01-daemon-port-comes-from-port-env` (reuse a member slug; retrieval vocabulary preserved)
- **Members:** 2 - **rows freed:** 1 - **merged body length:** 237/240 chars (machine-verified)
- **COVERAGE VERDICT: YES** - merged body <= 240 chars and every member's durable fact is carried (all 7 required phrases present as literal substrings)

**Member bodies (verbatim, machine-copied from the listing JSON)**


- **`portfence-01-daemon-port-comes-from-port-env`** - id `3b219d3e-c497-4382-a3bb-f3a2bb67e48f` - domain `environment` / audience `both` - created `2026-11-11T13:25:41.923959+00:00` - body sha256 `78222fc453475d95f050fe54ed07c2a79a70f0f7eb6f661f0a234909ffdd77a3`
```
daemon.port resolves from config.yaml ${PORT:-8088}, NOT DAEMON_PORT. An ambient PORT in a shell silently binds the live port. The MetricsListener 9798 is not fenced at all (anomaly 07e8410c).
```


- **`portfence-03-metrics-port-env-knob-works`** - id `22e0fb94-e287-4edc-a1ea-36b7aafc298b` - domain `environment` / audience `both` - created `2026-11-11T20:40:36.484171+00:00` - body sha256 `e32047c5414227a2aa0f5caa9d168602f4c10366c4f6ab48e163a6e10d226f45`
```
The metrics port 9798 IS runtime-fenceable via LIMITS_METRICS_LISTENER_PORT (proven on the staged binary: a control boot moved it to 19050 cleanly). It is merely unfenced BY DEFAULT and not covered by PORT/DAEMON_PORT.
```


**Proposed merged body (237/240 chars)**
```
daemon.port resolves from config.yaml ${PORT:-8088}, NOT DAEMON_PORT - ambient PORT binds the live port. MetricsListener 9798 is NOT covered by PORT/DAEMON_PORT but IS fenceable via LIMITS_METRICS_LISTENER_PORT (proven on staged binary).
```
**Why this merge:** This is a CORRECTION merge, not just a redundancy merge: portfence-01 asserts the MetricsListener 9798 is not fenced at all, which portfence-03 falsifies - it IS runtime-fenceable via LIMITS_METRICS_LISTENER_PORT, merely unfenced BY DEFAULT and outside PORT/DAEMON_PORT. Merging removes a live contradiction from the store.

**ROUTED OUT (not carried by the merged body - confirm the home before archiving the member):**

- anomaly 07e8410c (the record of the original not-fenced-at-all claim) - lives in the anomaly log; the claim itself is CORRECTED by the merged body.
- the control-boot detail (a boot moved the metrics listener to 19050 cleanly) - dated proof artifact; proven on the staged binary retains the anchor.


### CL-14 (rank 10) - Todo-tool carrier state + the case-sensitive reminder-pin caution

- **Survivor slug:** `gc-p4-03-note-drop-turn-end-reminder` (reuse a member slug; retrieval vocabulary preserved)
- **Members:** 2 - **rows freed:** 1 - **merged body length:** 233/240 chars (machine-verified)
- **COVERAGE VERDICT: YES** - merged body <= 240 chars and every member's durable fact is carried (all 6 required phrases present as literal substrings)

**Member bodies (verbatim, machine-copied from the listing JSON)**


- **`group-chat-p4-01-carrier-reminder-slot-gap`** - id `8ebed7dc-69fc-4c73-a6e1-95b3d0903234` - domain `verify` / audience `both` - created `2026-11-16T10:05:53.348503+00:00` - body sha256 `752d5b9d56957941299089931abc78045943d4471ff426466dd2baf02e652080`
```
SUPERSEDED 2026-11-16: the 2-path carrier gap is FIXED (c249d7fce). Carrier now on all 5 todo tool paths; manager has 6 reminder methods (add_subtask singular = manager-only, no tool).
```


- **`gc-p4-03-note-drop-turn-end-reminder`** - id `a3020315-8141-4552-aaba-3f351e5a27ee` - domain `verify` / audience `both` - created `2026-11-16T10:06:07.988030+00:00` - body sha256 `106eca4dd2c78e5a33d8fed5c3dc1790f13cd550d9ee7c6d5f2d0e9ab76b6817`
```
SUPERSEDED 2026-11-16: the add/remove_subtask drop is FIXED (c249d7fce). Auditor caution: the inventory pin's no-gap half is a CASE-SENSITIVE 'reminder' docstring scan — a new reminder method whose docstring omits it ESCAPES.
```


**Proposed merged body (233/240 chars)**
```
Carrier now on all 5 todo tool paths; manager has 6 reminder methods (add_subtask = manager-only, no tool). Auditor caution: the pin no-gap half is a CASE-SENSITIVE reminder docstring scan - a new reminder method omitting it ESCAPES.
```
**Why this merge:** Both members are the same remediation episode (both closed by c249d7fce, 2026-11-16) and carry one shared durable residue: the current carrier state plus the auditor caution that the inventory pin no-gap half is a CASE-SENSITIVE reminder docstring scan. The episode markers live in git history; the residue must survive in the merged row.

**ROUTED OUT (not carried by the merged body - confirm the home before archiving the member):**

- Both FIXED by c249d7fce (2026-11-16) episode markers (the 2-path carrier gap and the add/remove_subtask drop) - dated episode; the fix lives in git history c249d7fce.
- SUPERSEDED 2026-11-16 episode markers - dated; same git history.

> **NOTE.** Both slugs are named after the CLOSED episode (slot-gap, note-drop). Reusing either slug as the survivor therefore leaves an episode-named row holding live state. A neutral successor slug (e.g. gc-p4-reminder-carrier-current-state) is cleaner but adds a slug - PM call.


## 3. Clusters with COVERAGE = NO (DO-NOT-MERGE)

Each cluster below is genuinely redundant - the members assert the same fact from different angles - but the union of their durable facts does not fit 240 chars. **Never drop a fact to fit the limit:** the fact the limit would drop is named for each. PM must not merge these; if the redundancy must be reduced, the durable fact has to survive in an active row first (a successor row, or a split of the cluster), not be discarded.


### CL-04b - ENSEMBLE_AUTH_* family - the two members that cannot ride along

- **Members:** 2 - **survivor slug:** n/a (no merge proposed) - **COVERAGE VERDICT: NO - DO-NOT-MERGE**


- **`auth-lane-scrub-ensemble-auth-env`** - id `74245f23-8e20-4ec3-87ea-99bedd7044be` - domain `verify` / audience `both` - body sha256 `4aee95018ee9808277047992228d24cb678c210983dbbaff691deed1977b5d06`
```
Auth ambient scrub: FULL ENSEMBLE_AUTH_* family (7 vars) or NONE — never partial: ambient ENABLED=true → pydantic missing-issuer reds (P4) or preflight refuses scratch bind origin, exit 78 (S5; payload ships auth.enabled:false).
```


- **`gate-langcheck-auth-scrub-false-negative`** - id `ceae2f60-5269-43ba-8c5e-82ecb5ab874d` - domain `verify` / audience `operation` - body sha256 `e506cdb4c1dd3d8ed65757bac8a392b41adb4411136bebf4444228fbb7d47c60`
```
Gate lanes: do NOT scrub ENSEMBLE_AUTH_ISSUER for language_check. Ambient auth.enabled=true + issuer are required by Config(); scrubbing causes 2 false-negative failures. Scrub only for scope-cage route-census files.
```


**Why it cannot merge:** DO-NOT-MERGE. auth-lane-scrub-ensemble-auth-env adds a SECOND symptom surface (the scratch-bind preflight refusing with exit 78, payload shipping auth.enabled:false) and the 7-vars count; gate-langcheck-auth-scrub-false-negative is a COUNTER-rule (do NOT scrub ISSUER for language_check because Config() requires it; scrub only for scope-cage route-census files). Merging those two into the CL-04 body would drop named durable facts.

**Facts the 240-char limit would drop (named - do NOT drop them):**

- the scratch-bind preflight exit-78 refusal + payload ships auth.enabled:false (auth-lane-scrub-ensemble-auth-env)
- the ENSEMBLE_AUTH_* 7-vars count (auth-lane-scrub-ensemble-auth-env)
- the language_check EXCEPTION and its scrub-only-for-scope-cage-route-census-files scoping (gate-langcheck-auth-scrub-false-negative)
- the Config()-requires-issuer rationale and the 2 false-negative failures count (gate-langcheck-auth-scrub-false-negative)


### CL-05b - Mutation proof - the third member that does not fit

- **Members:** 1 - **survivor slug:** n/a (no merge proposed) - **COVERAGE VERDICT: NO - DO-NOT-MERGE**


- **`mutation-01-guards-need-counter-mutation`** - id `887dc716-4cd2-4366-b3c0-e2bc41a25e47` - domain `verify` / audience `both` - body sha256 `12210e15c5a7c0e2135db2af3cbbb6e69f2425bebe0eb47c7d0728641fe06e84`
```
A test green under its own leg's revert is not vacuous if a COUNTER-mutation reddens it. Absence-only and raw-text pins are prose-satisfiable — re-aim at the POSITIVE shape; 'mutation failures: 1' is signal, not flake.
```


**Why it cannot merge:** DO-NOT-MERGE into CL-05. mutation-01-guards-need-counter-mutation adds two durable facts the CL-05 body cannot also carry inside 240 chars: the counter-mutation framing itself (a test green under its own legs revert is not vacuous if a COUNTER-mutation reddens it) and the mutation failures: 1 is signal, not flake clause. Option for PM: keep this row as the counter-mutation framing row and let CL-05 own the inverted-spec rule - the two are related, not identical.

**Facts the 240-char limit would drop (named - do NOT drop them):**

- a test green under its own legs revert is not vacuous if a COUNTER-mutation reddens it (mutation-01-guards-need-counter-mutation)
- absence-only and raw-text pins are prose-satisfiable - re-aim at the POSITIVE shape (mutation-01-guards-need-counter-mutation)
- mutation failures: 1 is signal, not flake (mutation-01-guards-need-counter-mutation)


### CL-06 - Census pins must be lane-scoped (3 near-identical rows)

- **Members:** 3 - **survivor slug:** n/a (no merge proposed) - **COVERAGE VERDICT: NO - DO-NOT-MERGE**


- **`census-pin-lane-ownership-form`** - id `a883b76b-77cd-45b0-b9d0-b0c9acd8bc24` - domain `verify` / audience `implementation` - body sha256 `7c28bb41171217bc58b300478647c8fe554318e2a9e9cd3533d7565ec7851a1c`
```
A census pin counting a lane's own files must be lane-scoped, not global: assert the lane's own file is PRESENT in the tree delta, and pin the lane's closed commit range for exactness. Global equality re-breaks on every later lane.
```


- **`memoir-census-pin-lane-scoped`** - id `67d72ca9-1eaf-4a23-af6c-2fa2388db0d9` - domain `verify` / audience `both` - body sha256 `6fa183ff3f2a5862e34fc70533cca7b11b802790df13e0c4b60a506515eeeaa0`
```
Lane-scoped memoir migration-census pin: lane's own file is in the working-tree delta, delta non-empty, and over LANE_BASE_COMMIT..LANE_COMMIT the delta is exactly one file. Foreign lane files cannot redden it.
```


- **`memoir-census-pin-biting-mutations`** - id `970a600e-f6f1-4a3c-b346-1fd37c565870` - domain `verify` / audience `both` - body sha256 `ed48279b28ae6dbbaed1665ca1b3c472a861bf3a3fdf2b557ae6537703585eee`
```
Memoir census pin's biting mutations: (a) lane's own migration file removed from the working-tree delta -> RED; (b) lane commit range re-pointed at a foreign-lane commit -> RED; both restored byte-identically.
```


**Why it cannot merge:** DO-NOT-MERGE. Three rows assert the same lane-scoped census-pin rule from three angles: the GENERAL form (census-pin-lane-ownership-form), the WORKED INSTANCE (memoir-census-pin-lane-scoped), and the MUTATION PROOF that the instance pin actually bites (memoir-census-pin-biting-mutations). The union is ~415 chars of durable fact - it does not fit 240.

**Facts the 240-char limit would drop (named - do NOT drop them):**

- the global equality re-breaks on every later lane consequence clause
- the two biting mutations (lane own migration file removed from the working-tree delta -> RED; lane commit range re-pointed at a foreign-lane commit -> RED) and their byte-identical restore
- the delta is exactly one file over LANE_BASE_COMMIT..LANE_COMMIT precision

> **NOTE.** Highest-value merge in the corpus by fact redundancy (3 rows, 1 rule) and the one the 240-char cap most visibly blocks. PM may prefer a SPLIT (general rule row + worked-instance row) over a merge - that de-duplicates the rule without losing a fact, unlike a merge.


### CL-07 - Hand-back halt: root cause + self-resolution

- **Members:** 2 - **survivor slug:** n/a (no merge proposed) - **COVERAGE VERDICT: NO - DO-NOT-MERGE**


- **`promote-01-false-halt-self-commits`** - id `0d372d77-a2b7-4f4c-a299-59985c560bd5` - domain `deploy` / audience `operation` - body sha256 `73cb36ae732804fe393ce63afa5f5f330fa735b31471afbcd72d41b6c10c59aa`
```
Hand-back halt root cause: _gate_serving_attribution Layer 1 needs every :9797 LISTEN holder argv-owned, but the daemon's children inherit the listen fd so it always refuses. It self-commits ~9min later via the daemon-side closer.
```


- **`promote-02-handback-halt-self-resolve`** - id `82491ef9-c31f-4bf7-9f9d-5e0bb1ee808f` - domain `deploy` / audience `operation` - body sha256 `b79c7f546dac1d5ba0f50e8161d48540889005428562bd35389a2ba2ef696f21`
```
Hand-back halt (probe rc=3 vs 5s budget) self-resolves: serving_green_commit + sweep closes pending_op ~9-11min later (2nd case 2026-11-18 v0.31.2). Watch: state.json history tail + GET :9797/livez|readyz. No restart; counter untouched.
```


**Why it cannot merge:** DO-NOT-MERGE. The union (~332 chars) exceeds the cap. The two members split the episode: promote-01 gives the ROOT CAUSE (_gate_serving_attribution Layer 1 needs every 9797 LISTEN holder argv-owned, but daemon children inherit the listen fd so it always refuses) and promote-02 gives the SELF-RESOLUTION + watch recipe (serving_green_commit + sweep closes pending_op ~9-11min later; watch state.json history tail + GET livez|readyz; no restart, counter untouched).

**Facts the 240-char limit would drop (named - do NOT drop them):**

- the no restart, counter untouched observation (promote-02-handback-halt-self-resolve)
- the watch recipe state.json history tail + GET livez|readyz (promote-02-handback-halt-self-resolve)
- the probe rc=3 vs 5s budget probe signature (promote-02-handback-halt-self-resolve)


### CL-11 - Release / stage order (bump -> tag -> stage)

- **Members:** 2 - **survivor slug:** n/a (no merge proposed) - **COVERAGE VERDICT: NO - DO-NOT-MERGE**


- **`land-02-stage-order-push-tag-stage`** - id `063d3d90-a211-4b19-a980-1f68b32b2cd3` - domain `deploy` / audience `operation` - body sha256 `0e35f5c811a588e1bf6253a9aadf83824af0298b14de8462112f62ef514105d0`
```
stage.sh _tip_sha reads refs/remotes/origin/latest: tip-identity = tag exists + HEAD == tagged commit, so order is merge, push latest, push tag, THEN stage from a clean dedicated worktree; e2e runs post-push.
```


- **`release-01-bump-tag-then-stage-order`** - id `7079c358-d0dd-41ff-ad89-08f6df7da724` - domain `deploy` / audience `both` - body sha256 `a5736970437547419468d2bcceb74186e188105885cee198d3d89133ef913516`
```
Release order: bump commit -> ANNOTATED tag -> stage worktree DETACHED at tag -> FE REBUILD at that HEAD -> sidecar re-stamp -> stage.sh; tag-before-stage mandatory (git describe --exact-match HEAD == VERSION).
```


**Why it cannot merge:** DO-NOT-MERGE. The union (~370 chars) exceeds the cap: release-01 carries the release chain (bump commit -> ANNOTATED tag -> stage worktree DETACHED at tag -> FE REBUILD -> sidecar re-stamp -> stage.sh, tag-before-stage mandatory via git describe --exact-match) and land-02 carries the tip-identity definition (_tip_sha reads refs/remotes/origin/latest; tip-identity = tag exists + HEAD == tagged commit; merge, push latest, push tag, THEN stage from a clean dedicated worktree; e2e runs post-push).

**Facts the 240-char limit would drop (named - do NOT drop them):**

- the release chain (bump -> ANNOTATED tag -> DETACHED stage worktree -> FE REBUILD -> sidecar re-stamp -> stage.sh)
- stage.sh _tip_sha reads refs/remotes/origin/latest + e2e runs post-push
- git describe --exact-match HEAD == VERSION as the tag-before-stage proof


### CL-12 - prompt-assent: gate owner + Discord-first mint context

- **Members:** 2 - **survivor slug:** n/a (no merge proposed) - **COVERAGE VERDICT: NO - DO-NOT-MERGE**


- **`prompt-assent-01-gate-owner-and-evidence`** - id `f91f7c0b-0fef-4a8b-8cc0-e2a117731c5d` - domain `artifact` / audience `implementation` - body sha256 `c5784fde8fdebfa0b019c373679e6fb32f6af027ecafb16d2c59de0622d53750`
```
prompt-assent P2 (2026-11-10): the three-factor gate's ONE owner is daemon/tools/prompt_assent.py; the window is manager._user_origin_windows; the mint context comes from the window message row's nested metadata["discord"].
```


- **`prompt-assist-01-discord-first-mint`** - id `6cdf61f7-a340-4624-8db8-9342c6a377dc` - domain `verify` / audience `both` - body sha256 `a93fe4193880e9d5b49f8c9e5aae970fa46b217af4baea7b661a9de1581c7ff7`
```
prompt-assent is Discord-first: an api turn ARMS the factor-2 window, but mint/approve need the window row's nested metadata.discord (is_dm/is_group_dm) or refuse mint-requires-private-channel.
```


**Why it cannot merge:** DO-NOT-MERGE. The union (~280 chars) exceeds the cap. f91f7c0b gives the owner triple (owner daemon/tools/prompt_assent.py, window manager._user_origin_windows, mint context = the window message rows nested metadata['discord']); 6cdf61f7 gives the arming asymmetry (an api turn arms the factor-2 window; mint/approve need is_dm/is_group_dm from that metadata or refuse mint-requires-private-channel).

**Facts the 240-char limit would drop (named - do NOT drop them):**

- the refusal string mint-requires-private-channel
- an api turn ARMS the factor-2 window (the api-vs-discord asymmetry)
- the is_dm/is_group_dm field pair as the mint precondition


### CL-15 - Unit-shard gate fences (3 near-identical rows)

- **Members:** 3 - **survivor slug:** n/a (no merge proposed) - **COVERAGE VERDICT: NO - DO-NOT-MERGE**


- **`gc-gate-01-fence-family-exclusions`** - id `5ddf201c-4879-478b-a759-5e6d909237f3` - domain `verify` / audience `both` - body sha256 `67bbd35c0d78255f2b5f2c2988f30631fe86efd995d4229aa9396ba68d17e94a`
```
Gate shards on pinned ensemble_dev: exclude services/resource_{traceback,anomaly} + routers/resources_{traceback,anomalies}; session fences pytest.exit HARD-ABORT unless POSTGRES_DB in {ensemble_dev_test_only,ensemble_test_only}.
```


- **`gc-gate-fence-family-and-xunit2-digest-trap`** - id `1cd099e4-3df3-4715-9cfd-f84a4df588cb` - domain `other` / audience `operation` - body sha256 `e78fed9b58b172dfae2243f5f401609bbd96e6f2eb641a3874d0c48d9de60fec`
```
GC-gate: 5 shard files (resource/anomaly/mission) fence-abort on ensemble_dev — isolate each (RC=1) or xdist dies RC=3. junitxml xunit2 lacks file attr: re-derive ids tree-anchored. Ambient ENSEMBLE_AUTH_* leaks past task fence (both legs).
```


- **`testenv-01-unit-shard-hard-abort`** - id `2f2aca22-1778-48a4-af5e-77ed24e324e8` - domain `environment` / audience `both` - body sha256 `5033dc983ab4f8ae370621fd03aa4fcd749ddaac652667901a117a9ba4a94e62`
```
Unit-shard env: scrub POSTGRES_* PER-VARIABLE, pin nothing. Session fences pytest.exit HARD-ABORT unless POSTGRES_DB is ensemble_dev_test_only/ensemble_test_only; ensemble_dev aborts EXCEPT the prompt-cache fence (allows ensemble_dev).
```


**Why it cannot merge:** DO-NOT-MERGE. Three rows state the unit-shard fence; the union exceeds 240. The overlap core is the exclusion set + the pytest.exit HARD-ABORT condition on POSTGRES_DB, but each member adds a distinct durable fact.

**Facts the 240-char limit would drop (named - do NOT drop them):**

- junitxml xunit2 lacks file attr: re-derive ids tree-anchored (gc-gate-fence-family-and-xunit2-digest-trap)
- Ambient ENSEMBLE_AUTH_* leaks past the task fence (both legs) (gc-gate-fence-family-and-xunit2-digest-trap)
- isolate each of the 5 shard files (RC=1) or xdist dies RC=3 (gc-gate-fence-family-and-xunit2-digest-trap)
- ensemble_dev aborts EXCEPT the prompt-cache fence (allows ensemble_dev) (testenv-01-unit-shard-hard-abort)
- scrub POSTGRES_* PER-VARIABLE, pin nothing (testenv-01-unit-shard-hard-abort)


### CL-16 - Zero-traffic skill A/B stall (3 near-identical rows)

- **Members:** 3 - **survivor slug:** n/a (no merge proposed) - **COVERAGE VERDICT: NO - DO-NOT-MERGE**


- **`skillab-01-zero-traffic-ab-stall`** - id `07df8535-8389-4f53-9d50-86b683b73864` - domain `artifact` / audience `both` - body sha256 `4a790f7c4eb73109de3abc35f9b8e48250e3bddb921f42f43cef6cfaf80a3ad0`
```
Zero-traffic skill FIX opens an A/B group with NO exit: comparisons stays 0, so check_ab_test_resolution returns needs_more_data at skill_evolution_service.py:1462 — BEFORE the extension_count bump (:1536); both gens stay is_active.
```


- **`skillab-02-keeper-rule-zero-traffic-fix`** - id `63f64bbe-4f96-45a7-860e-bafb07aa7fca` - domain `artifact` / audience `both` - body sha256 `a15ec9dc12331ea51cf1ae42d61a42b2be15ab7848714935f76124614d26ae8b`
```
Keeper rule: a FIX on a zero-traffic skill never resolves via A/B (comparisons stays 0); the 'zero uses deactivate bypassing A/B' doctrine has NO implementation. Re-assert: skill_feedback note + deferred skill_fix RE-TARGET; report stall.
```


- **`skillab-03-comparison-counter-semantics`** - id `af8815b5-5623-439d-97d7-973bfe7a5c30` - domain `artifact` / audience `both` - body sha256 `490b5b75cd29334a80f339cee531e0775661bc8544dca386cd01b2f6b598bc4e`
```
comparisons bumps ONLY on genuine injections (_select_ab_variant→increment_comparison); record_feedback on-miss rows do NOT. No autonomous resolver — re-run deferred FIX manually once real injections accumulate.
```


**Why it cannot merge:** DO-NOT-MERGE. The union (~310 chars) exceeds the cap. skillab-01 carries the service anchors (check_ab_test_resolution returns needs_more_data at skill_evolution_service.py:1462, BEFORE the extension_count bump at :1536; both gens stay is_active); skillab-02 carries the unimplemented doctrine + the keeper remedy; skillab-03 carries the counter semantics (comparisons bumps ONLY on genuine injections via _select_ab_variant->increment_comparison; record_feedback on-miss rows do NOT).

**Facts the 240-char limit would drop (named - do NOT drop them):**

- the service anchors skill_evolution_service.py:1462 (needs_more_data) and :1536 (extension_count bump) + both gens stay is_active
- the keeper remedy (skill_feedback note + deferred skill_fix RE-TARGET; report stall)
- comparisons bumps ONLY on genuine injections (_select_ab_variant->increment_comparison); record_feedback on-miss rows do NOT


### CL-17 - P11 drift-guard marker requirement + cross-lane carve-out

- **Members:** 2 - **survivor slug:** n/a (no merge proposed) - **COVERAGE VERDICT: NO - DO-NOT-MERGE**


- **`source-gc-drift-guards-branch-tip-sensitive`** - id `b93a2b9a-72b0-403d-8f9b-2d3f9b316fd4` - domain `verify` / audience `both` - body sha256 `0bc847cc595b3122544a6b223cb9d4f1523cf68fbe8db5400b77bd4c2f97c5d8`
```
P11 drift guards: committed manager.py hunks need a sanctioned marker ('decision', 'memoir', 'group chat') else lane pins RED. Guard diffs COMMITTED span (working-tree blind); pins redden only after commit. Fix: marker comment per hunk.
```


- **`source-gc-02-cross-lane-carve-out-cure`** - id `1a88b07f-1f0e-497d-8383-6f97ff4589bb` - domain `verify` / audience `implementation` - body sha256 `2732c3049ea5019922221fa70c7d92ebb5719607a69c38013931d82748146838`
```
P11 drift-guard cure 7bc6bff74: lane pins red on other sanctioned lanes' manager.py hunks; SANCTIONED_LANE_MARKERS tuple carves them out. Counter-mutation = TEMP COMMIT + soft reset: history pins diff commits, worktree edits invisible.
```


**Why it cannot merge:** DO-NOT-MERGE. The union (~400 chars) exceeds the cap by a wide margin: source-gc-drift-guards-branch-tip-sensitive carries the sanctioned-marker list (decision, memoir, group chat), the COMMITTED-span-only rule and the per-hunk marker fix; source-gc-02-cross-lane-carve-out-cure carries the SANCTIONED_LANE_MARKERS carve-out (7bc6bff74) and the TEMP COMMIT + soft reset counter-mutation method.

**Facts the 240-char limit would drop (named - do NOT drop them):**

- the sanctioned marker tuple (decision, memoir, group chat) + guard diffs COMMITTED span (working-tree blind)
- the SANCTIONED_LANE_MARKERS carve-out and cure sha 7bc6bff74
- the TEMP COMMIT + soft reset counter-mutation method


### CL-18 - Seal-map self-consistency + the stale QUARANTINE row

- **Members:** 2 - **survivor slug:** n/a (no merge proposed) - **COVERAGE VERDICT: NO - DO-NOT-MERGE**


- **`quarantine-01-chart-seal-row-is-stale`** - id `3087ee94-3800-49f9-8748-4f81f1c4432d` - domain `verify` / audience `both` - body sha256 `2c7f50fdcf7a40a81e6af1b0fc47b9101c836cd3ee5971b6db33fab3aae72f86`
```
QUARANTINE.md 'chart stale-seals x1' row is STALE — chart seal tripwire is GREEN at base 818411107 (35/35 seal rows match). Row was true only at its own base 1fecf7c3f (7 stale rows), repaired by 2026-11-05/07 re-pins.
```


- **`sealmap-03-older-base-red-not-attributable`** - id `ffe47734-be35-4088-83d1-e4a45a0df0c0` - domain `verify` / audience `both` - body sha256 `22fcb8f0411db36dc3c0f30d5e254d922a11902756c1678a283b3f1bbfc1285e`
```
Re-pin adjudication: a seal red proven at an older base (1fecf7c3f) is NOT attributable to lanes operating since; the map is self-consistent at base 818411107 (35/35), so HEAD-leg divergence is lane-introduced.
```


**Why it cannot merge:** DO-NOT-MERGE. The union (~300 chars) exceeds the cap. Both members assert that the seal map is self-consistent at base 818411107 (35/35) and that a red proven at an older base (1fecf7c3f) is not attributable to later lanes; quarantine-01 additionally states that the QUARANTINE.md chart stale-seals x1 row is stale.

**Facts the 240-char limit would drop (named - do NOT drop them):**

- the QUARANTINE.md chart stale-seals x1 row verdict + repaired by the 2026-11-05/07 re-pins
- 35/35 seal rows match as the base-map census
- HEAD-leg divergence is lane-introduced


## 4. Mergeable-looking families that are NOT (and why)

The similarity pass produces same-family / same-ordinal pairs that look like duplicates but are different facts. These are the traps in this corpus:

| Family / pair | Why it is NOT a merge |
|---|---|
| `flake-01-ab-control-arm-attribution` vs `flake-01-ab-resolution-staticpool-race` | Same family, same ordinal, opposite subjects: the first is an A/B ATTRIBUTION METHOD (run N=30 on both arms, compare failure RATES); the second is a specific concurrency defect (shared StaticPool SQLite conn + asyncio.to_thread at skill_evolution_service.py:1449). Merging would bury a live flake. |
| `gc-p3-boot-pin-file-node-count` vs `gc-p3-testp11-class-identity` | Both are node-count pins (8 nodes in test_room_boot_f1_pin.py vs 6 nodes in test_source_gc_repository.py) but they pin DIFFERENT files and DIFFERENT counts. The shared lesson (disambiguate same-named classes by node count) is already stated inside gc-p3-testp11-class-identity. |
| `verify-02-snapshot-l3-red-set` vs `snapshot-l3-02-base-red-set-one` | Same lane, overlapping words - but the second is a dated BASE-LEG BASELINE (base 818411107, 172 nodes / 3 files, suite red set = exactly 1), i.e. rolling state, not a second copy of the red-set shape. It belongs in the completion scan, not in a merge. |
| `views-*` (5 rows) | The largest coherent family (origin-gate contract, forwarded-veto contract, non-browser Origin spellings, session-bypass lane tip, verify-POST rate limiting). Every row is a DIFFERENT clause of the same gate - the family reads like one merged row but the union is ~1000 chars and each clause is independently load-bearing. |
| `quarantine-*` (3 rows) | All about QUARANTINE.md, but one is this row is STALE, one is rows must name every red node, one is family rows need node mapping - three distinct rules. |
| `promote-01` / `promote-02` | Look like one story (the hand-back halt) and ARE one story - but the union exceeds 240 (CL-07). |
| `seal-01` / `seal-02` / `seal-03` | The seal family already shows the merge pattern working: phase-2 merged two of these (survivor `seal-02-repin-dict-keeps-base-value`, 5fcb2598). The remaining two are distinct rules. |

**Phase-2 survivors excluded from clustering (per the wave charter):** `5fcb2598 seal-02-repin-dict-keeps-base-value`, `6db7e0fd prompt-assent-flip-on-gate`, `170d495f prompt-assist-04-freshness-leg-second-race` - their absorbed members are already inactive and are NOT in the 277-row listing, so nothing from the phase-2 consolidations is re-proposed here. (6db7e0fd and 170d495f do appear as near-neighbours of prompt-assent rows; those pairings are recorded in CL-12 - none of them clears the cap.)


## 5. Family-cap observations (derived grouping - the service-side family key may differ)

Under the stated derivation (family = the slug prefix before a 2-digit ordinal, else the first hyphen segment), these families hold MORE than 3 active rows, i.e. above the documented POK family cap of 3 active per family:

- **gc** (9 active): `gc-gate-fence-family-and-xunit2-digest-trap`, `gc-p3-boot-pin-file-node-count`, `gc-p3-swallow-advance-class-sweep`, `gc-p3-testp11-class-identity`, `gc-p4-cle-line-tripwire-repin`, `gc-p4-junitxml-is-the-red-source`, `gc-p5-no-packs-row-added`, `gc-p5-room-create-cap-race`, `gc-p5-room-gate-node-baseline`
- **stage** (6 active): `stage-01-live-mode-guards`, `stage-02-boot-smoke-copy`, `stage-ceremony-census-proofs`, `stage-release-gate-sequence`, `stage-sh-live-requires-live-arming-var`, `stage-v0300-worktree-head-nuance`
- **prompt-assent** (5 active): `prompt-assent-01-gate-owner-and-evidence`, `prompt-assent-02-category-registration-seam`, `prompt-assent-03-paths-use-assent-not-assist`, `prompt-assent-04-dm-shield-tool-gate`, `prompt-assent-05-auth-env-scrub-family`
- **rogue** (5 active): `rogue-01-write-quant-method`, `rogue-cleanup-rider-recorded-addendum`, `rogue-fence-ambient-allow-var`, `rogue-p2-corpus-routed-to-v03012-rulings`, `rogue-p2-evidence-locations`
- **sonic** (5 active): `sonic-01-git-grant-boundary`, `sonic-02-complexity-routing-doctrine`, `sonic-03-blast-gate-removed`, `sonic-bump-version-selfcommits`, `sonic-stage-fresh-wt-fe-prebuild`
- **views** (5 active): `views-fix10-forwarded-veto-contract`, `views-origin-gate-contract`, `views-origin-parser-nonbrowser-spellings`, `views-session-bypass-rekill2-dead`, `views-verify-post-rate-limit-pacing`
- **worktree** (5 active): `worktree-01-venv-branch-binding`, `worktree-02-python-version-divergence`, `worktree-03-upstream-auto-set-hazard`, `worktree-deletion-claim-verify-first`, `worktree-env-fence-shape-recheck`
- **gate** (4 active): `gate-auth-env-302-poison`, `gate-langcheck-auth-scrub-false-negative`, `gate-mutation-reproof-restore-protocol`, `gate-whole-tree-slow-skill-sink-test`
- **git** (4 active): `git-01-canonical-detached-diff-trap`, `git-01-commit-msg-before-dashdash`, `git-sha-transcription-discipline`, `git-update-ref-worktree-index-stale`
- **mutation** (4 active): `mutation-01-guards-need-counter-mutation`, `mutation-02-worktree-scratch-venue`, `mutation-03-git-restore-byte-exact-proof`, `mutation-proof-must-target-pin-property`
- **skill** (4 active): `skill-01-off-by-absence-restart-asymmetry`, `skill-02-evolution-switch-precedence`, `skill-03-refusal-observability-split`, `skill-lane-flip-verify-restart`
- **skillab** (4 active): `skillab-01-zero-traffic-ab-stall`, `skillab-02-keeper-rule-zero-traffic-fix`, `skillab-03-comparison-counter-semantics`, `skillab-dev-zero-rows-ro-grant-gap`
- **snapshot-l3** (4 active): `snapshot-l3-01-malformed-json-empty-collapse`, `snapshot-l3-02-base-red-set-one`, `snapshot-l3-04-drift-pin-identity-hazard`, `snapshot-l3-05-decline-byte-identical`
- **verify** (4 active): `verify-01-red-receipt-overlay`, `verify-02-snapshot-l3-red-set`, `verify-03-two-detectors-four-guards`, `verify-plan-ruling-sync-grep-derived`

Every cluster above reduces a family's row count by 1 (or 2). The families still above the cap after the proposed merges are the ones whose members are genuinely different facts - the corpus is wide, not duplicative. PM should treat the cap as a signal to CHECK A FAMILY, not as a merge quota: a family of 5 distinct facts is not a family of 5 copies.


## 6. Execution notes for PM

1. **Order.** Land the YES clusters first; CL-14 and CL-13 are the ones other rows depend on (CL-14's absorbed rows are only archivable after CL-14 lands).
2. **Re-pull before executing.** The store drifted after capture. Re-run the listing pull and confirm the member set is unchanged before writing.
3. **One skill per merge, one row per merge.** A merge writes the survivor row (same slug, new content -> supersede) and archives the absorbed member(s). PM owns every write; this lane wrote none.
4. **Do not merge the NO clusters.** Their named dropped facts are the ones the limit cannot carry.
5. **Watch the routed-out items** in CL-08 (the E-6 7d-gate deadline must reach KV/board before archival) and CL-09 (the 4 green-at-base guard names live in the lane's test files).

*Generated by `_laneB_generate.py` from `live-active-2026-11-18-post-phase1.json` (sha256 `2db52522983a1a1116e6961522ad487f0808d313ec3970cdff9d581e26731408`). No database access; no POK writes.*

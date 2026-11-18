# HANDOFF — DD-9 amendment: agent-scoped persona SELF-RATIFY exemption for "ari"

Branch `fix/persona-self-ratify-ari` · base `a1883aff2` (== origin/latest) · module commit `cc65f5b6b` (REVISION 1: `487b6ad59`) · merge commits `e1f114d81` + `e4b1756c8` (revision 1) · tip at handoff = this commit.
Scope: ONE module — the Memoir Store persona lane's approval guard, its tests, and the adjudication-log row. NOTHING was merged to `latest`, pushed, tagged, promoted, deployed or restarted — the branch stands for operator review.
Worktrees: task/integration `/Users/nguyenminhkha/workdir/ens-persona-self-ratify` (branch `fix/persona-self-ratify-ari`); module `/Users/nguyenminhkha/workdir/ens-persona-self-ratify-mod` (branch `fix/persona-self-ratify-ari-mod`). The main checkout (uncommitted WIP) was never touched.

## Files touched across modules

Union of every module's file list (per-module grouping preserved):

- module `persona-self-ratify` — sonic-coder instance `a7e6497e`, branch `fix/persona-self-ratify-ari-mod` @ `cc65f5b6b`, merged `--no-ff` into the feature branch as `e1f114d81`:
  - `daemon/repositories/memoir/models.py` (+10/-0)
  - `daemon/repositories/memoir/repository.py` (+25/-4)
  - `tests/unit/test_memoir_persona_lane.py` (+78/-0 — ADDITIVE ONLY; every pre-existing test is byte-unchanged)
  - `.agents/shared/planning/ari-memoir-store/decisions.md` (+1/-0 — adjudication row #20 appended after row #19)
- module `persona-self-ratify` — REVISION 1 (the leader's re-dispatch, one of one), same instance `a7e6497e`, branch `fix/persona-self-ratify-ari-mod` @ `487b6ad59`, merged `--no-ff` into the feature branch as `e4b1756c8`:
  - `tests/unit/test_memoir_persona_lane.py` (+20/-0 — ONE added test at EOF; the 8 existing `test_self_ratify_*` tests and the 3 pre-existing mutation-sensitive pins are byte-unchanged)
  - PRODUCTION CODE UNCHANGED by revision 1: `models.py`, `repository.py` and `daemon/routers/memoir.py` are NOT in the revision diff (verified by `git diff --name-only cc65f5b6b..487b6ad59` -> the test file only).
- integration (sonic-developer, this lane):
  - `.agents/shared/planning/sonic-team/persona-self-ratify-ari/HANDOFF.md` (this artifact)

Total: 4 module files + 1 handoff. `git diff --stat a1883aff2..HEAD` before this handoff commit = exactly the 4 files above. No other tree file was touched.

## What changed

BEHAVIOR: `MemoirPersonaRepository.approve()` may now proceed WITHOUT a human session actor when ALL THREE conditions hold — (a) the version's `agent_id` is in `PERSONA_SELF_RATIFY_AGENTS` ({"ari"}), (b) the actor is on the daemon-internal lane (`actor.auth_method == "internal"`), (c) the actor IS that agent (`actor.agent_id == version.agent_id`). Every other actor, every other agent, every other lane is refused exactly as before. `reject()` is byte-identical to today. `submit()` is untouched. The REST route file is untouched.

Implementation shape: `models.py` gains the constant + its comment; `repository.py`'s `_require_human_lane` gained an explicit keyword `allow_self_ratify: bool = False`; `approve()` passes `allow_self_ratify=True`; `reject()` does not.

REVISION 1 (TEST-ONLY): one added test, `test_self_ratify_internal_self_actor_missing_version_raises_human_lane` — an INTERNAL self-actor (`auth_method="internal"`, `agent_id="ari"`) approving a NONEXISTENT version id must raise `HumanLaneRequiredError`, with the CLASS pinned exactly (`type(excinfo.value) is HumanLaneRequiredError`, `version_id` asserted, `"not found"` asserted absent from the message). No production file changed in revision 1 — the three-part condition is exactly as shipped.

THE EXACT WORDING of the models.py addition (byte-exact extract from the merged tree, `daemon/repositories/memoir/models.py`):

```python
#: Agent ids exempt from the human-lane requirement on PERSONA approval,
#: but ONLY when the actor is the agent itself acting through the
#: internal lane (DD-9 amendment, user ruling 2026-11-18, paraphrased:
#: that agent works face-to-face with the user daily, so its review is
#: continuous rather than queued; other agents lack that proximity and
#: keep the gate). Append-only versions keep any drift one row from
#: revert, and the transition vocabulary is unchanged for everyone.
PERSONA_SELF_RATIFY_AGENTS: frozenset[str] = frozenset({"ari"})

```

THE EXACT WORDING of adjudication row #20 (byte-exact extract from the merged tree; it is ONE table line at `.agents/shared/planning/ari-memoir-store/decisions.md:104`, immediately after row #19):

```
| 20 | 2026-11-18 | DD-9 amendment — agent-scoped persona SELF-RATIFY exemption for the agent id "ari" (PERSONA_SELF_RATIFY_AGENTS): a persona version authored by that agent may move draft -> pending_approval -> approved without a human session actor, but ONLY when the actor is the agent itself on the internal lane (auth_method="internal" AND actor.agent_id == version.agent_id). Every other agent keeps the human lane exactly as shipped. The transition vocabulary is unchanged; self-ratification still walks the two steps draft -> pending_approval -> approved; submit() is unchanged; reject() is unchanged; the REST surface stays closed (a bearer actor still gets 403 HUMAN_LANE_REQUIRED). | RULING (user, 2026-11-18, paraphrased only — the verbatim carries persona-lore tokens this repo was swept clean of; the ruling is recorded in the operator's private store): the exemption is granted for that one agent because it works face-to-face with the user daily, so its review is continuous rather than queued; other agents lack that proximity and keep the gate. Mitigations: append-only versions keep any drift one row from revert; daily human proximity means the review is continuous; the core-prompt lane stays token-gated. Surviving invariants: transition vocabulary unchanged (draft->approved still invalid for every agent); the two-step draft->pending_approval->approved; append-only versions; REST scope unchanged; reject unchanged. | implemented as adjudicated — repository.py _require_human_lane gained allow_self_ratify=False keyword (approve passes True, reject does not); models.py gained PERSONA_SELF_RATIFY_AGENTS | daemon/repositories/memoir/models.py; daemon/repositories/memoir/repository.py; tests/unit/test_memoir_persona_lane.py |
```

HARD INVARIANTS — each confirmed with its test and its evidence (all on the MERGED tree):

| # | Invariant | Evidence (test name -> result) |
|---|---|---|
| I1 | Transition vocabulary UNCHANGED: draft->approved stays invalid for EVERY agent | `test_the_vocabulary_mirrors_the_prompt_version_lane` PASS; `test_self_ratify_draft_to_approved_refused_for_both` PASS (refused for "ari" WITH the self actor AND for "leader") |
| I2 | Self-ratification still walks draft -> pending_approval -> approved (two deliberate steps) | `test_self_ratify_exempt_ari_internal_self` PASS — the fixture submits first, then approves |
| I3 | `submit()` unchanged for all agents (a machine may propose) | `submit()` is not in the diff (0 changed lines); `test_submit_is_a_seam_without_a_v1_caller` PASS (exact allowed-caller file set) |
| I4 | `reject()` keeps the human lane for ALL agents, including "ari" | `test_self_ratify_reject_still_requires_human_lane_for_ari` PASS (self actor refused); `test_reject_without_a_human_actor_raises` PASS; `reject()`'s call site is byte-identical (0 changed lines) |
| I5 | The REST route keeps its `prompts:approve` scope; the route file is NOT modified; the exemption is unreachable from HTTP (bearer -> 403 HUMAN_LANE_REQUIRED) | `test_self_ratify_ari_bearer_raises` PASS; `daemon/routers/memoir.py` absent from the diff; `actor_from_request` refuses `auth_method == "internal"` (daemon/repositories/prompt_version/repository.py:739-744) |
| I6 | Nothing auto-approves: the nightly reflection lane keeps composing + submitting only | the only `approve`/`reject` callers of this repository are `daemon/routers/memoir.py:159/191`; the reflection lane's only call is `self._persona_repo.submit(draft.id)` (daemon/services/memoir_persona_gate.py:771); `test_submit_is_a_seam_without_a_v1_caller` PASS pins the exact daemon/ file set |
| I7 | Pointer semantics in `_transition` untouched (approve still moves `memoir_persona.current_version_id` in the same transaction) | `_transition` is NOT in the diff (0 changed lines); `test_self_ratify_exempt_ari_internal_self` PASS asserts the pointer moved to the approved version |
| I8 | The exemption is identity-bound, not lane-bound | `test_self_ratify_ari_internal_not_ari_raises` PASS (internal actor whose agent_id is not the version's agent) |
| I9 | Error-semantics ORDER: a non-human, non-self-ratifying actor gets HumanLaneRequiredError — never ValueError("not found") — including for a nonexistent version id | `test_self_ratify_internal_self_actor_missing_version_raises_human_lane` PASS — the INTERNAL self-actor path, and the ONLY test that reaches the guard's version lookup (`repository.py:271`) AND takes its None branch (a MISSING version), so it is the test that pins the missing-version refusal (mutant proof in the M-H line under "How to verify"). The OTHER internal-lane tests also reach that lookup — `tests/unit/test_memoir_persona_lane.py:257` (`test_self_ratify_non_exempt_leader_internal_raises`) and `:298` (`test_self_ratify_ari_internal_not_ari_raises`) — but on an EXISTING row, so they never take the None branch. The earlier `test_self_ratify_nonexistent_version_raises_human_lane` (BEARER actor) also PASSES but SHORT-CIRCUITS BEFORE the lookup — a bearer actor is not the internal lane (`repository.py:267-270`) — so it does NOT exercise that branch and is not cited as evidence for it |

## What is UNTESTED

- The HTTP surface was NOT exercised end-to-end (no request-level probe). The "bearer -> 403" fact rests on (a) the unit-level bearer refusal test and (b) a source reading of `actor_from_request` (it raises for the internal lane, so an HTTP actor can never satisfy condition (b)). No live/HTTP leg ran in this lane.
- No real-PostgreSQL leg ran: the changed seam is driver-agnostic and the pack plus the suite run on SQLite. The PG corpus pack (`memoir_pg_corpus_test`) was not executed — it needs a disposable PG cluster.
- The nightly reflection lane (compose + submit) was NOT re-exercised end-to-end. The "nothing auto-approves" invariant is proven structurally (no new `approve()` caller) plus by the exact-set caller pin, not by a live reflection run.
- The production internal-lane actor mint (`actor_from_instance`, which is what mints an internal ActorContext carrying `agent_id`) was NOT exercised in a test — the new tests use a local actor double carrying `auth_method` + `agent_id`. The seam between that mint and the guard is covered by source reading only.
- The whole-tree suite was NOT run. Scope was the six persona suites plus the registered pack; a full-tree run is the tester lane's call.
- The `_require_human_lane` static -> instance change was not exercised by any caller outside the class (there are none — grep-proven).

## Known risks

1. THE LORE-CENSUS DIFF IS NOT BYTE-EMPTY, and it cannot be — the commission's own placement instruction causes it. The commission requires the constant "beside PERSONA_VALID_TRANSITIONS" in `models.py`; that block sits ABOVE the file's two pre-existing lore hits, so the insertion shifts them by exactly +9 lines. Measured on the merged tree: hit COUNT 111 = 111 (base = 111); the CONTENT-axis diff (line-number field stripped) is EMPTY; the PER-FILE census is IDENTICAL (same files, same counts); the only literal delta is `daemon/repositories/memoir/models.py` `:188 -> :197` and `:222 -> :231`, both PRE-EXISTING hits moved by +9 lines. ZERO new hits, ZERO removed hits. A byte-clean diff would require placing the constant BELOW line 222 of `models.py` (i.e. away from the vocabulary block the commission names) — that is a deliberate operator act, not a default; on request I will move it.
2. `_require_human_lane` changed from `@staticmethod` to an instance method (it needs the engine for the version lookup). Any external caller invoking it as a static would break; grep proves there is none (only the two `self._require_human_lane(...)` call sites inside the class, at repository.py:233 and :238 on the merged tree).
3. On the internal lane only, `approve()` performs ONE extra read (`get_version`) before the guard decides. It is a point lookup on the primary key; the refusal path is unchanged for bearer/session actors (the lookup is skipped entirely unless the actor is internal).
4. `reject()` deliberately keeps the human lane: an exempt agent cannot reject its own version. That is the commission's instruction (reject byte-identical to today), recorded here so the asymmetry is not read as an oversight.
5. THE LEADER'S THREE-PART CONDITION vs THE BRIEF'S LITERAL "agent-id-only" — stated plainly, with evidence, so the operator can overrule. The brief's literal reading (exemption keyed on the version's agent id alone) was NOT implemented, for two reasons: (i) it turns three EXISTING mutation-sensitive pins RED — they all use agent "ari": `test_approve_without_a_human_actor_raises` (bearer actor), `test_approve_with_an_internal_actor_raises` (internal actor WITHOUT agent_id), `test_approve_with_an_actor_without_the_attribute_raises` (attribute-less actor); all three are byte-unchanged and PASS on the merged tree; (ii) it OPENS THE REST SURFACE — the route is the only `approve()` caller AND the pending list is filtered to `agent_id="ari"` (the `repo.list_pending(agent_id="ari")` call is at daemon/routers/memoir.py:130; :135 is `body=r.body`), so a bearer token holding `prompts:approve` would approve that agent's persona versions, violating "the exemption operates at the repository seam (the daemon-internal path), not by opening the REST surface". Conditions (b)+(c) make the exemption unforgeable from HTTP, because `actor_from_request` refuses `auth_method == "internal"` over HTTP (S16 anti-forgery). The literal agent-id-only wording IS recoverable — it is a one-condition relaxation of (b)+(c) — but it re-opens the REST surface and REDs the three pins above, so it must be an explicit operator decision, not a default. The operator may overrule in a single line; the handoff and the diff make both shapes reviewable.
6. BLAST RADIUS — the v2 persona-PROPOSAL lane DOES route through the SAME exempted `approve()`. That lane's approvals call `MemoirPersonaRepository.approve()` (tests/unit/test_memoir_v2_persona_proposal.py:127/:143/:178), so the exemption REACHES that path: an internal self-actor for the exempt agent would now succeed there as well. Its own enforcement is UNCHANGED (a bearer actor is still refused — the :143 and :178 assertions still pass), but the path now carries the exemption; it is NOT a separate, untouched commission. (Corrected after the reviewer's minor #2 — the earlier wording of this line understated the blast radius.)
7. Not merged to `latest`, not pushed, not tagged, no promote/deploy/restart: the branch stands for operator review.
7b. WORKTREE INDEX CLOBBER (incident, recorded) — during the rev-1 review a concurrent actor left the worktree INDEX holding the BASE blobs while the worktree CONTENT stayed at HEAD (`git status --porcelain` showed 4x `MM`; `git ls-files -s` showed the base blobs in the index; `git diff HEAD --stat` was EMPTY and `git diff --cached --stat` showed the whole commission as a revert). NOTHING was lost: the worktree content equalled HEAD, every commit was intact, and the reflog showed no lost commit. The repair was INDEX-ONLY — `git restore --staged --source=HEAD -- <the four paths>` — with NO commit made for it; afterwards `git status --porcelain` showed only the untracked tester RESULTS file and `git diff HEAD --stat` stayed empty. The lesson, stated so it is not re-learned: `git status -- daemon/ tests/` being clean is a WRITE-TIME statement, not a standing guarantee — a clean porcelain says nothing about the INDEX's blob provenance, and a plain `git commit` from a clobbered index would silently revert the commission.
8. NO APPROVER COLUMN — a self-ratified approval is INDISTINGUISHABLE from a human approval in the row. `MemoirPersonaVersion` records only `id/agent_id/version/body/status/source/submitted_at/approved_at` (daemon/repositories/memoir/models.py:397-404): there is NO approver subject and NO approver auth-method column (contrast the prompt_versions lane, which records the approver identity), and `_transition` writes only the status plus `approved_at`. The exemption therefore creates the FIRST non-human approval path, so the row can no longer be read as evidence that a HUMAN approved. Pre-existing schema property; RECORD ONLY — no schema change, no column and no migration in this lane. Mitigation: versions are append-only and the pointer is re-pointable, so a bad self-ratification stays ONE ROW FROM REVERT (the same mitigation as risk 1); the daily human proximity is the review that does not happen in the row.

## How to verify

Run in the task worktree (the pack resolves the worktree-local `.venv` first):

```
cd /Users/nguyenminhkha/workdir/ens-persona-self-ratify
.venv/bin/python -c "import daemon; print(daemon.__file__)"          # must resolve INSIDE this worktree
.venv/bin/python -m pytest tests/unit/test_memoir_persona_lane.py tests/unit/test_memoir_persona_seed.py \
  tests/unit/test_memoir_v2_persona_proposal.py tests/unit/test_memoir_v2_persona_router.py \
  tests/unit/test_memoir_v2_persona_composition.py tests/unit/test_memoir_v2_jev_classifier.py \
  -q -p no:randomly --override-ini="addopts="
timeout 300 bash test/packs/memoir_persona_lane_unit_test.sh
```

OBSERVED ON THE MERGED TREE (tip `e4b1756c8`, after revision 1):

```
ENV-PARITY : daemon at /Users/nguyenminhkha/workdir/ens-persona-self-ratify/daemon/__init__.py
SUITE      : 90 passed in 4.88s        (same six suites at base a1883aff2: 81 passed in 8.27s -> +9 new tests)
NEW TESTS  : 9 passed, 14 deselected   (pytest -k self_ratify -v)
PACK       : RESULT: PASS (rc=0)
             leg 1 baseline            -> 23 passed      (exit 0)
             leg 2 mutation planted    -> 12 failed, 11 passed  (exit 1 — RED as required)
             leg 3 restored            -> 23 passed      (exit 0)
             Interpreter: /Users/nguyenminhkha/workdir/ens-persona-self-ratify/.venv/bin/python
M-H MUTANT : source-level mutant planted in the worktree (a missing version on the
             internal lane raising a plain ValueError("... not found")) -> 1 failed,
             22 passed: the ONLY failure is the NEW test, so it kills M-H, while
             the 22 pre-existing tests stayed green (the gap the tester reported).
             The tree was restored BYTE-IDENTICAL (sha256
             c82475932c9c92d580d4a1978a587f22a27da09158a08962bcc7b2dc9388df9a before
             and after) and `git status -- daemon/ tests/` is clean.
CENSUS     : hits 111 = base 111; content-axis diff EMPTY; per-file census IDENTICAL;
             only delta = two PRE-EXISTING models.py hits shifted +9 lines (see Known risks 1).
             The commission's /tmp census file was cleaned out of /tmp during the
             lane, so the base leg was re-derived from the BASE COMMIT itself
             (`git grep -nwIi <the three lore tokens> a1883aff2 -- daemon/ tests/ .agents/`):
             the commit is the authoritative base, the temp copy was only a convenience.
```

ENV-PARITY PROOF: a fresh worktree does not carry the gitignored `.venv`; `uv sync` was run inside each worktree and the worktree-local interpreter resolves `daemon` INSIDE the worktree (`.../ens-persona-self-ratify/daemon/__init__.py` and `.../ens-persona-self-ratify-mod/daemon/__init__.py`) — not the main checkout's editable `.pth`. The pack's own interpreter line (above) is the proof.

FULL DIFF (`git diff a1883aff2..e4b1756c8` — 5 files, +458/-4; lore-token-free, verified by grep before embedding):
NOTE: the embedded diff is the branch diff BEFORE this handoff revision, so the HANDOFF.md rows inside it are its FIRST-COMMITTED state (e.g. the pre-correction wording of Known risks #2 and #6). The CORRECTED passages are the body of THIS document, not the embedded copy.

```
diff --git a/.agents/shared/planning/ari-memoir-store/decisions.md b/.agents/shared/planning/ari-memoir-store/decisions.md
index 95c403988..61cd08985 100644
--- a/.agents/shared/planning/ari-memoir-store/decisions.md
+++ b/.agents/shared/planning/ari-memoir-store/decisions.md
@@ -101,6 +101,7 @@ These are **not new decisions by the documenter**. They are the load-bearing cho
 | 17 | 2026-11-05 | design §10.1 seam 1 / flag #14 — the PG materialization path is unspecified as to FAILURE CONTAINMENT: the memoir tsv + GIN statements are DDL executed inside the house's single, all-or-nothing _ensure_postgres_columns (daemon/manager.py:8055 def, body to :10164; statement list :8269; executed by that method's own loop at :9554-9557) which has NO per-statement try/except and is fail-loud by documented design (:8259-8265 — "this method does NOT catch exceptions … the exception propagates and startup aborts … Better to fail loudly at startup than to continue and crash on the first query that references the missing column"). Reality forces: a memoir-specific DDL failure therefore rolls back EVERY other lane's ensure statements in the same transaction (the inherited list spans task.last_heartbeat_at, task.auto_continued_at, source_configs.autostart, instance_ui_prefs.icon_tag, instances.agent_tag, message_queue, mcp_servers, instance_execution_leases, infra_assets GIN, secrets_audit.context and the project-skill-loader tables) AND aborts the whole daemon boot — and because flags #13/#14 make the substrate knob-independent, the kill-switch CANNOT protect the boot. The plan pinned this boot-abort posture with NO risk-register row, which the plan council correctly flagged. | ADOPT NO-CONTAINMENT (Q6, architect ruling after the plan council split — council record 6601b15a, APPROVE-WITH-ADVISORIES 1C/10W/13A with two-model convergence; the decision was expressly routed to the architect). The lane's DDL rides the shared fail-loud ensure; the lane does NOT carry lane-owned containment. BLAST RADIDUM, PLAINLY: a memoir tsv/GIN ensure failure aborts the ENTIRE daemon boot — the whole ensemble is down until an operator fixes it and reboots — and rolls back the inherited lanes' ensure statements committed in the same transaction, because the ensure is ONE transaction. The rollback consequence is FIX-LOSS ONLY (no data loss; every inherited statement is IF NOT EXISTS and idempotent, so the shared ensure re-applies the whole set on the next boot). Per-statement isolation inside the shared ensure is ROUTED OUT as a house-pattern change: it would make the house's single fail-loud seam conditional on a lane and invite N divergent DDL failure policies, and the house docstring's own reasoning — fail loudly rather than continue and crash later on the first query that references the missing column — is on point here, because a missing tsv column is exactly that crash class. The kill-switch governs the SURFACE, not the schema: it does NOT protect the boot. OBLIGATIONS: (i) this row records the blast radius; (ii) the W8 test-induction mechanism (scratch frozen-binary boot with ONE deliberately invalid memoir ensure statement, plus a structural pin asserting no per-statement try/except and a single begin()) PROVES the blast radius inductively instead of asserting it in prose — specified verbatim in architecture-recommendation.md §12; (iii) a risk-register row is REQUIRED in the plan (text specified in §12). FALSIFICATION CONDITION: if the house's install base ever includes a PostgreSQL older than 12 (no generated columns) or managed/multi-tenant PG where a memoir DDL failure is materially more likely, lane-owned containment becomes the correct posture and MUST be re-ruled. | adjudicated — implementation pending (Phase C); the W8 mechanism and the risk-register row are the planner lane's to fold (phase1-plan.md AC3, phase7-plan.md D7.6) | design §10.1 / §14-14; flag-dispositions #14; decisions.md §4 rows 13; architecture-recommendation.md AM-9, §12; plan council record 6601b15a |
 | 18 | 2026-11-05 | phase4 D4.8 as folded — the refusal vocabulary is FROZEN at 18 codes (import-time assertion + unit pins) and the council's mapping routes any unmapped service code to the catch-all: an EMPTY/WHITESPACE memoir_recall query (service code `query_required`, daemon/services/memoir_retrieval.py:374 — the predicate `(query or "").strip()` covers whitespace) is reported as `memoir_internal_error`, i.e. a USER-INPUT error reported as an INTERNAL error. The frozen set's argument family is entirely FIELD-SPECIFIC (reason_required, actor_required, invalid_actor, importance_out_of_range, body_to_long, title_to_long, stars_out_of_bounds, invalid_event_at, k_out_of_range, invalid_window) — no generic argument code exists. | USER-RULED — the user, as OWNER of the memoir tool surface (2026-11-05, verbatim): "as the OWNER of the memoir tool surface, I rule that TRUTHFULNESS OF THE ERROR OUTWEIGHS THE FROZEN COUNT — at pre-release, before anything ships and before any pin/compat burden exists, authorizing a 19th code for a genuine user-input error is cheap; shipping a code that calls a user mistake an 'internal error' is a permanent lie in the contract, and my own future instances will read those errors and mis-learn from them. So: if an existing code (or a best-fit 19th) can say 'empty/invalid query' honestly, take it — authorize the 19th if necessary. Keep 18 ONLY if one of them is genuinely honest for this case." ARCHITECT AUDIT (the reasoning, 18/18 tested for a GENUINELY honest fit): 0 of 18 honest. Every candidate is adjacent-but-dishonest — memoir_reason_required and memoir_actor_required sit in the right 'required-field' family but name the PIN REASON and ACTOR fields (a wrong-field code invites a wrong retry); memoir_k_out_of_range and memoir_invalid_window sit in the right 'argument' family but name a DIFFERENT parameter (k is valid here; recall has no window); memoir_body_too_long / title_too_long / stars_out_of_bounds / importance_out_of_range / invalid_event_at / invalid_actor name other fields; memoir_not_found / not_a_keepsake / keepsake_cap_reached / budget_exceeded / disabled / agent_unknown / not_authorized are domain, identity or knob codes; memoir_internal_error claims a bug where the caller sent an empty string — precisely the permanent lie the ruling forbids. RULING: AUTHORIZE the 19th code **`memoir_query_required`** (the 'required' sub-family, consistent with reason_required/actor_required), mapped IDENTITY from the service's `query_required`. A generic `memoir_invalid_argument` was rejected (a dumping ground that re-creates the same mislabelling class at a nicer label); silent reinterpretation of an empty query as 'most recent' was rejected (masks a caller bug and smuggles memoir_timeline semantics into memoir_recall — the two-tools-two-expectations error flag #10 warns against). CONSEQUENCES, exactly: memoir.py gains the constant + one frozenset member, the import-time assertion 18 -> 19 (:201) and the docstring count (:42); the mapping table gains query_required -> memoir_query_required and the catch-all loses its now-mapped example; `_RECALL_CODE_MAP` gains the identity entry; the unit pack's count pin (:171) AND its test name ('eighteen') flip, and the fallback test (:410) inverts to assert the true code (red-to-green, mutation-meaningful). NOT TOUCHED: the PRIVILEGED_TOOL_CATEGORIES census (#18 of 18), the 5-tool/4-family counts, DYNAMIC/KNOWN_TOOL_NAMES, the prompt-artifact baseline (the ari doctrine does not enumerate the set), MT-01 (it carries the CATEGORY count 18->17, not this one), the memoir mutation matrix (10 legs, no code reference), and any schema/migration artifact. The pin/coverage consequences are carried VERBATIM into the phase-8 ceremony notes (architecture-recommendation.md §13.6). | user-authorized — to be implemented in the code council's wave-4 fix cycle; ceremony notes text handed to the planner's phase-8 fold | USER RULING 2026-11-05 (owner of the memoir tool surface); phase4-plan.md D4.8; daemon/tools/memoir.py:42, 156-201, 340-348; daemon/services/memoir_retrieval.py:374; tests/unit/test_memoir_tools_unit.py:170-171, 410-420; architecture-recommendation.md AM-21, §13 |
 | 19 | 2026-11-05 | Phase C code council (record 003966f4c) — the dev's FIVE in-mandate calls: (i) the agent_id predicate in the PG lexical SQL (repository-scoped bound parameter from the factory-captured identity, repository.py:471-486); (ii) MemoirRepository constructed per assembly (wraps the caller's engine, never builds one, never reads ambient POSTGRES_*); (iii) the dynamic briefing zone excluding keepsakes (already recorded phase-notes-p5.md); (iv) the readyz advisory injected at the route (post-verdict injection is what makes advisory-only structural, api.py:5766-5783); (v) THE SEMANTIC WIRING — the retrieval service resolves a DEFAULT query embedder internally when none is injected (phase-7 ITEM 1), so a production memoir_recall performs a bounded NETWORK embed of the query INSIDE the agent's turn. (i)-(iv) ruled in-mandate UNANIMOUSLY; (v) SPLIT 1-1. | RULING (council, reviewer-resolved): CODE ACCEPTABLE — both councilors agree the code is acceptable; the divergence was bookkeeping only, and the ledger row is the cheaper, invariant-preserving resolution (vs. re-plumbing an injected embedder through the tool factory). PRODUCTION-BEHAVIOR CONSEQUENCE, PLAINLY: every production memoir_recall call performs a default ~5-second-budget network embed of the query INSIDE the agent's turn before hybrid fusion (MEMOIR_QUERY_EMBED_TIMEOUT_S = 5.0, memoir_retrieval.py:112); on ANY exception, timeout, or config-missing the service degrades to lexical-only (the design §7 degradation sentence) and never hangs. WHY ACCEPTABLE: the in-turn query embed is DESIGN-REQUIRED, not a deviation — design §7 ranks by cosine over the query embedding and phase2 D2.3 specifies "embed the QUERY once per call"; the wiring implements the blessed retrieval algorithm. The call is ONCE per recall, bounded by a code constant whose rationale is documented in place (memoir_retrieval.py:106-112: "it sits in an agent's turn, so it gets a SHORT bounded timeout"), degrading on failure, and disclosed at the agent surface by the recall footer's rank-basis and missing-embedding fields (AM-33). ARCHITECT POSITION: NO amendment owed — the budget is documented, bounded, degrading, and design-required; a knob would add surface for no gain (the council's 'cheaper, invariant-preserving resolution' is endorsed). | code acceptable as shipped; recorded per the council ruling; the fix cycle left the semantic wiring UNCHANGED (C-1 d278ee71c, W-1 25ef71634, W-2 f012b4787, V-1 4e30a4f6c, W-3 b925d34eb) | code-review.md @ 003966f4c ('Ruling on the dev's five adjudications', item (v); follow-on requirement 6); design §7; phase2-plan.md D2.3; daemon/services/memoir_retrieval.py:98-112, 302-333; implementation e7a1defa3 (phase 7) on deb4b223d (phase 2); fix cycle b925d34eb |
+| 20 | 2026-11-18 | DD-9 amendment — agent-scoped persona SELF-RATIFY exemption for the agent id "ari" (PERSONA_SELF_RATIFY_AGENTS): a persona version authored by that agent may move draft -> pending_approval -> approved without a human session actor, but ONLY when the actor is the agent itself on the internal lane (auth_method="internal" AND actor.agent_id == version.agent_id). Every other agent keeps the human lane exactly as shipped. The transition vocabulary is unchanged; self-ratification still walks the two steps draft -> pending_approval -> approved; submit() is unchanged; reject() is unchanged; the REST surface stays closed (a bearer actor still gets 403 HUMAN_LANE_REQUIRED). | RULING (user, 2026-11-18, paraphrased only — the verbatim carries persona-lore tokens this repo was swept clean of; the ruling is recorded in the operator's private store): the exemption is granted for that one agent because it works face-to-face with the user daily, so its review is continuous rather than queued; other agents lack that proximity and keep the gate. Mitigations: append-only versions keep any drift one row from revert; daily human proximity means the review is continuous; the core-prompt lane stays token-gated. Surviving invariants: transition vocabulary unchanged (draft->approved still invalid for every agent); the two-step draft->pending_approval->approved; append-only versions; REST scope unchanged; reject unchanged. | implemented as adjudicated — repository.py _require_human_lane gained allow_self_ratify=False keyword (approve passes True, reject does not); models.py gained PERSONA_SELF_RATIFY_AGENTS | daemon/repositories/memoir/models.py; daemon/repositories/memoir/repository.py; tests/unit/test_memoir_persona_lane.py |
 **Columns:** `#` (sequential), `Date` (ISO), `Deviation` (what the design specifies **and** what reality forces — both, not just the problem), `Adjudication` (the architect's ruling, quoted where possible), `Outcome` (implemented as adjudicated / superseded by a later row / withdrawn), `Links` (commit, file, or `design.md` § for the affected text).
 
 **Also record here (same table) if chosen in Phase B:** outcomes for the open items in `design.md` §14 — resolving one *is* an adjudication *if* it changes blessed text. Choosing the keepsake cap integer, for example, fills a delegated blank; it is still worth a row so the number has provenance.
diff --git a/.agents/shared/planning/sonic-team/persona-self-ratify-ari/HANDOFF.md b/.agents/shared/planning/sonic-team/persona-self-ratify-ari/HANDOFF.md
new file mode 100644
index 000000000..3eaf926c9
--- /dev/null
+++ b/.agents/shared/planning/sonic-team/persona-self-ratify-ari/HANDOFF.md
@@ -0,0 +1,324 @@
+# HANDOFF — DD-9 amendment: agent-scoped persona SELF-RATIFY exemption for "ari"
+
+Branch `fix/persona-self-ratify-ari` · base `a1883aff2` (== origin/latest) · module commit `cc65f5b6b` · merge commit `e1f114d81` · tip at handoff = this commit.
+Scope: ONE module — the Memoir Store persona lane's approval guard, its tests, and the adjudication-log row. NOTHING was merged to `latest`, pushed, tagged, promoted, deployed or restarted — the branch stands for operator review.
+Worktrees: task/integration `/Users/nguyenminhkha/workdir/ens-persona-self-ratify` (branch `fix/persona-self-ratify-ari`); module `/Users/nguyenminhkha/workdir/ens-persona-self-ratify-mod` (branch `fix/persona-self-ratify-ari-mod`). The main checkout (uncommitted WIP) was never touched.
+
+## Files touched across modules
+
+Union of every module's file list (per-module grouping preserved):
+
+- module `persona-self-ratify` — sonic-coder instance `a7e6497e`, branch `fix/persona-self-ratify-ari-mod` @ `cc65f5b6b`, merged `--no-ff` into the feature branch as `e1f114d81`:
+  - `daemon/repositories/memoir/models.py` (+10/-0)
+  - `daemon/repositories/memoir/repository.py` (+25/-4)
+  - `tests/unit/test_memoir_persona_lane.py` (+78/-0 — ADDITIVE ONLY; every pre-existing test is byte-unchanged)
+  - `.agents/shared/planning/ari-memoir-store/decisions.md` (+1/-0 — adjudication row #20 appended after row #19)
+- integration (sonic-developer, this lane):
+  - `.agents/shared/planning/sonic-team/persona-self-ratify-ari/HANDOFF.md` (this artifact)
+
+Total: 4 module files + 1 handoff. `git diff --stat a1883aff2..HEAD` before this handoff commit = exactly the 4 files above. No other tree file was touched.
+
+## What changed
+
+BEHAVIOR: `MemoirPersonaRepository.approve()` may now proceed WITHOUT a human session actor when ALL THREE conditions hold — (a) the version's `agent_id` is in `PERSONA_SELF_RATIFY_AGENTS` ({"ari"}), (b) the actor is on the daemon-internal lane (`actor.auth_method == "internal"`), (c) the actor IS that agent (`actor.agent_id == version.agent_id`). Every other actor, every other agent, every other lane is refused exactly as before. `reject()` is byte-identical to today. `submit()` is untouched. The REST route file is untouched.
+
+Implementation shape: `models.py` gains the constant + its comment; `repository.py`'s `_require_human_lane` gained an explicit keyword `allow_self_ratify: bool = False`; `approve()` passes `allow_self_ratify=True`; `reject()` does not.
+
+THE EXACT WORDING of the models.py addition (byte-exact extract from the merged tree, `daemon/repositories/memoir/models.py`):
+
+```python
+#: Agent ids exempt from the human-lane requirement on PERSONA approval,
+#: but ONLY when the actor is the agent itself acting through the
+#: internal lane (DD-9 amendment, user ruling 2026-11-18, paraphrased:
+#: that agent works face-to-face with the user daily, so its review is
+#: continuous rather than queued; other agents lack that proximity and
+#: keep the gate). Append-only versions keep any drift one row from
+#: revert, and the transition vocabulary is unchanged for everyone.
+PERSONA_SELF_RATIFY_AGENTS: frozenset[str] = frozenset({"ari"})
+
+```
+
+THE EXACT WORDING of adjudication row #20 (byte-exact extract from the merged tree; it is ONE table line at `.agents/shared/planning/ari-memoir-store/decisions.md:104`, immediately after row #19):
+
+```
+| 20 | 2026-11-18 | DD-9 amendment — agent-scoped persona SELF-RATIFY exemption for the agent id "ari" (PERSONA_SELF_RATIFY_AGENTS): a persona version authored by that agent may move draft -> pending_approval -> approved without a human session actor, but ONLY when the actor is the agent itself on the internal lane (auth_method="internal" AND actor.agent_id == version.agent_id). Every other agent keeps the human lane exactly as shipped. The transition vocabulary is unchanged; self-ratification still walks the two steps draft -> pending_approval -> approved; submit() is unchanged; reject() is unchanged; the REST surface stays closed (a bearer actor still gets 403 HUMAN_LANE_REQUIRED). | RULING (user, 2026-11-18, paraphrased only — the verbatim carries persona-lore tokens this repo was swept clean of; the ruling is recorded in the operator's private store): the exemption is granted for that one agent because it works face-to-face with the user daily, so its review is continuous rather than queued; other agents lack that proximity and keep the gate. Mitigations: append-only versions keep any drift one row from revert; daily human proximity means the review is continuous; the core-prompt lane stays token-gated. Surviving invariants: transition vocabulary unchanged (draft->approved still invalid for every agent); the two-step draft->pending_approval->approved; append-only versions; REST scope unchanged; reject unchanged. | implemented as adjudicated — repository.py _require_human_lane gained allow_self_ratify=False keyword (approve passes True, reject does not); models.py gained PERSONA_SELF_RATIFY_AGENTS | daemon/repositories/memoir/models.py; daemon/repositories/memoir/repository.py; tests/unit/test_memoir_persona_lane.py |
+```
+
+HARD INVARIANTS — each confirmed with its test and its evidence (all on the MERGED tree):
+
+| # | Invariant | Evidence (test name -> result) |
+|---|---|---|
+| I1 | Transition vocabulary UNCHANGED: draft->approved stays invalid for EVERY agent | `test_the_vocabulary_mirrors_the_prompt_version_lane` PASS; `test_self_ratify_draft_to_approved_refused_for_both` PASS (refused for "ari" WITH the self actor AND for "leader") |
+| I2 | Self-ratification still walks draft -> pending_approval -> approved (two deliberate steps) | `test_self_ratify_exempt_ari_internal_self` PASS — the fixture submits first, then approves |
+| I3 | `submit()` unchanged for all agents (a machine may propose) | `submit()` is not in the diff (0 changed lines); `test_submit_is_a_seam_without_a_v1_caller` PASS (exact allowed-caller file set) |
+| I4 | `reject()` keeps the human lane for ALL agents, including "ari" | `test_self_ratify_reject_still_requires_human_lane_for_ari` PASS (self actor refused); `test_reject_without_a_human_actor_raises` PASS; `reject()`'s call site is byte-identical (0 changed lines) |
+| I5 | The REST route keeps its `prompts:approve` scope; the route file is NOT modified; the exemption is unreachable from HTTP (bearer -> 403 HUMAN_LANE_REQUIRED) | `test_self_ratify_ari_bearer_raises` PASS; `daemon/routers/memoir.py` absent from the diff; `actor_from_request` refuses `auth_method == "internal"` (daemon/repositories/prompt_version/repository.py:739-744) |
+| I6 | Nothing auto-approves: the nightly reflection lane keeps composing + submitting only | the only `approve`/`reject` callers of this repository are `daemon/routers/memoir.py:159/191`; the reflection lane's only call is `self._persona_repo.submit(draft.id)` (daemon/services/memoir_persona_gate.py:771); `test_submit_is_a_seam_without_a_v1_caller` PASS pins the exact daemon/ file set |
+| I7 | Pointer semantics in `_transition` untouched (approve still moves `memoir_persona.current_version_id` in the same transaction) | `_transition` is NOT in the diff (0 changed lines); `test_self_ratify_exempt_ari_internal_self` PASS asserts the pointer moved to the approved version |
+| I8 | The exemption is identity-bound, not lane-bound | `test_self_ratify_ari_internal_not_ari_raises` PASS (internal actor whose agent_id is not the version's agent) |
+| I9 | Error-semantics ORDER: a non-human, non-self-ratifying actor gets HumanLaneRequiredError — never ValueError("not found") — including for a nonexistent version id | `test_self_ratify_nonexistent_version_raises_human_lane` PASS; the guard's version lookup returns None for a missing id, which leaves the exemption inactive |
+
+## What is UNTESTED
+
+- The HTTP surface was NOT exercised end-to-end (no request-level probe). The "bearer -> 403" fact rests on (a) the unit-level bearer refusal test and (b) a source reading of `actor_from_request` (it raises for the internal lane, so an HTTP actor can never satisfy condition (b)). No live/HTTP leg ran in this lane.
+- No real-PostgreSQL leg ran: the changed seam is driver-agnostic and the pack plus the suite run on SQLite. The PG corpus pack (`memoir_pg_corpus_test`) was not executed — it needs a disposable PG cluster.
+- The nightly reflection lane (compose + submit) was NOT re-exercised end-to-end. The "nothing auto-approves" invariant is proven structurally (no new `approve()` caller) plus by the exact-set caller pin, not by a live reflection run.
+- The production internal-lane actor mint (`actor_from_instance`, which is what mints an internal ActorContext carrying `agent_id`) was NOT exercised in a test — the new tests use a local actor double carrying `auth_method` + `agent_id`. The seam between that mint and the guard is covered by source reading only.
+- The whole-tree suite was NOT run. Scope was the six persona suites plus the registered pack; a full-tree run is the tester lane's call.
+- The `_require_human_lane` static -> instance change was not exercised by any caller outside the class (there are none — grep-proven).
+
+## Known risks
+
+1. THE LORE-CENSUS DIFF IS NOT BYTE-EMPTY, and it cannot be — the commission's own placement instruction causes it. The commission requires the constant "beside PERSONA_VALID_TRANSITIONS" in `models.py`; that block sits ABOVE the file's two pre-existing lore hits, so the insertion shifts them by exactly +9 lines. Measured on the merged tree: hit COUNT 111 = 111 (base = 111); the CONTENT-axis diff (line-number field stripped) is EMPTY; the PER-FILE census is IDENTICAL (same files, same counts); the only literal delta is `daemon/repositories/memoir/models.py` `:188 -> :197` and `:222 -> :231`, both PRE-EXISTING hits moved by +9 lines. ZERO new hits, ZERO removed hits. A byte-clean diff would require placing the constant BELOW line 222 of `models.py` (i.e. away from the vocabulary block the commission names) — that is a deliberate operator act, not a default; on request I will move it.
+2. `_require_human_lane` changed from `@staticmethod` to an instance method (it needs the engine for the version lookup). Any external caller invoking it as a static would break; grep proves there is none (only the two `self._require_human_lane(...)` call sites inside the class, at repository.py:231 and :236).
+3. On the internal lane only, `approve()` performs ONE extra read (`get_version`) before the guard decides. It is a point lookup on the primary key; the refusal path is unchanged for bearer/session actors (the lookup is skipped entirely unless the actor is internal).
+4. `reject()` deliberately keeps the human lane: an exempt agent cannot reject its own version. That is the commission's instruction (reject byte-identical to today), recorded here so the asymmetry is not read as an oversight.
+5. THE LEADER'S THREE-PART CONDITION vs THE BRIEF'S LITERAL "agent-id-only" — stated plainly, with evidence, so the operator can overrule. The brief's literal reading (exemption keyed on the version's agent id alone) was NOT implemented, for two reasons: (i) it turns three EXISTING mutation-sensitive pins RED — they all use agent "ari": `test_approve_without_a_human_actor_raises` (bearer actor), `test_approve_with_an_internal_actor_raises` (internal actor WITHOUT agent_id), `test_approve_with_an_actor_without_the_attribute_raises` (attribute-less actor); all three are byte-unchanged and PASS on the merged tree; (ii) it OPENS THE REST SURFACE — the route is the only `approve()` caller AND the pending list is filtered to `agent_id="ari"` (daemon/routers/memoir.py:135), so a bearer token holding `prompts:approve` would approve that agent's persona versions, violating "the exemption operates at the repository seam (the daemon-internal path), not by opening the REST surface". Conditions (b)+(c) make the exemption unforgeable from HTTP, because `actor_from_request` refuses `auth_method == "internal"` over HTTP (S16 anti-forgery). The literal agent-id-only wording IS recoverable — it is a one-condition relaxation of (b)+(c) — but it re-opens the REST surface and REDs the three pins above, so it must be an explicit operator decision, not a default. The operator may overrule in a single line; the handoff and the diff make both shapes reviewable.
+6. Scope note: the exemption is on the PERSONA VERSION lane (`memoir_persona_versions`). The v2 persona-PROPOSAL lane (tests/unit/test_memoir_v2_persona_proposal.py) keeps its own human-lane enforcement and was NOT touched — if the operator intended that surface too, it is a separate commission.
+7. Not merged to `latest`, not pushed, not tagged, no promote/deploy/restart: the branch stands for operator review.
+
+## How to verify
+
+Run in the task worktree (the pack resolves the worktree-local `.venv` first):
+
+```
+cd /Users/nguyenminhkha/workdir/ens-persona-self-ratify
+.venv/bin/python -c "import daemon; print(daemon.__file__)"          # must resolve INSIDE this worktree
+.venv/bin/python -m pytest tests/unit/test_memoir_persona_lane.py tests/unit/test_memoir_persona_seed.py \
+  tests/unit/test_memoir_v2_persona_proposal.py tests/unit/test_memoir_v2_persona_router.py \
+  tests/unit/test_memoir_v2_persona_composition.py tests/unit/test_memoir_v2_jev_classifier.py \
+  -q -p no:randomly --override-ini="addopts="
+timeout 300 bash test/packs/memoir_persona_lane_unit_test.sh
+```
+
+OBSERVED ON THE MERGED TREE (tip `e1f114d81`):
+
+```
+ENV-PARITY : daemon at /Users/nguyenminhkha/workdir/ens-persona-self-ratify/daemon/__init__.py
+SUITE      : 89 passed in 4.77s        (same six suites at base a1883aff2: 81 passed in 8.27s -> +8 new tests)
+NEW TESTS  : 8 passed, 14 deselected   (pytest -k self_ratify -v)
+PACK       : RESULT: PASS (rc=0)
+             leg 1 baseline            -> 22 passed      (exit 0)
+             leg 2 mutation planted    -> 11 failed, 11 passed  (exit 1 — RED as required)
+             leg 3 restored            -> 22 passed      (exit 0)
+             Interpreter: /Users/nguyenminhkha/workdir/ens-persona-self-ratify/.venv/bin/python
+CENSUS     : hits 111 = base 111; content-axis diff EMPTY; per-file census IDENTICAL;
+             only delta = two PRE-EXISTING models.py hits shifted +9 lines (see Known risks 1)
+```
+
+ENV-PARITY PROOF: a fresh worktree does not carry the gitignored `.venv`; `uv sync` was run inside each worktree and the worktree-local interpreter resolves `daemon` INSIDE the worktree (`.../ens-persona-self-ratify/daemon/__init__.py` and `.../ens-persona-self-ratify-mod/daemon/__init__.py`) — not the main checkout's editable `.pth`. The pack's own interpreter line (above) is the proof.
+
+FULL DIFF (`git diff a1883aff2..e1f114d81` — 4 files, +114/-4; lore-token-free, verified by grep before embedding):
+
+```
+diff --git a/.agents/shared/planning/ari-memoir-store/decisions.md b/.agents/shared/planning/ari-memoir-store/decisions.md
+index 95c403988..61cd08985 100644
+--- a/.agents/shared/planning/ari-memoir-store/decisions.md
++++ b/.agents/shared/planning/ari-memoir-store/decisions.md
+@@ -101,6 +101,7 @@ These are **not new decisions by the documenter**. They are the load-bearing cho
+ | 17 | 2026-11-05 | design §10.1 seam 1 / flag #14 — the PG materialization path is unspecified as to FAILURE CONTAINMENT: the memoir tsv + GIN statements are DDL executed inside the house's single, all-or-nothing _ensure_postgres_columns (daemon/manager.py:8055 def, body to :10164; statement list :8269; executed by that method's own loop at :9554-9557) which has NO per-statement try/except and is fail-loud by documented design (:8259-8265 — "this method does NOT catch exceptions … the exception propagates and startup aborts … Better to fail loudly at startup than to continue and crash on the first query that references the missing column"). Reality forces: a memoir-specific DDL failure therefore rolls back EVERY other lane's ensure statements in the same transaction (the inherited list spans task.last_heartbeat_at, task.auto_continued_at, source_configs.autostart, instance_ui_prefs.icon_tag, instances.agent_tag, message_queue, mcp_servers, instance_execution_leases, infra_assets GIN, secrets_audit.context and the project-skill-loader tables) AND aborts the whole daemon boot — and because flags #13/#14 make the substrate knob-independent, the kill-switch CANNOT protect the boot. The plan pinned this boot-abort posture with NO risk-register row, which the plan council correctly flagged. | ADOPT NO-CONTAINMENT (Q6, architect ruling after the plan council split — council record 6601b15a, APPROVE-WITH-ADVISORIES 1C/10W/13A with two-model convergence; the decision was expressly routed to the architect). The lane's DDL rides the shared fail-loud ensure; the lane does NOT carry lane-owned containment. BLAST RADIDUM, PLAINLY: a memoir tsv/GIN ensure failure aborts the ENTIRE daemon boot — the whole ensemble is down until an operator fixes it and reboots — and rolls back the inherited lanes' ensure statements committed in the same transaction, because the ensure is ONE transaction. The rollback consequence is FIX-LOSS ONLY (no data loss; every inherited statement is IF NOT EXISTS and idempotent, so the shared ensure re-applies the whole set on the next boot). Per-statement isolation inside the shared ensure is ROUTED OUT as a house-pattern change: it would make the house's single fail-loud seam conditional on a lane and invite N divergent DDL failure policies, and the house docstring's own reasoning — fail loudly rather than continue and crash later on the first query that references the missing column — is on point here, because a missing tsv column is exactly that crash class. The kill-switch governs the SURFACE, not the schema: it does NOT protect the boot. OBLIGATIONS: (i) this row records the blast radius; (ii) the W8 test-induction mechanism (scratch frozen-binary boot with ONE deliberately invalid memoir ensure statement, plus a structural pin asserting no per-statement try/except and a single begin()) PROVES the blast radius inductively instead of asserting it in prose — specified verbatim in architecture-recommendation.md §12; (iii) a risk-register row is REQUIRED in the plan (text specified in §12). FALSIFICATION CONDITION: if the house's install base ever includes a PostgreSQL older than 12 (no generated columns) or managed/multi-tenant PG where a memoir DDL failure is materially more likely, lane-owned containment becomes the correct posture and MUST be re-ruled. | adjudicated — implementation pending (Phase C); the W8 mechanism and the risk-register row are the planner lane's to fold (phase1-plan.md AC3, phase7-plan.md D7.6) | design §10.1 / §14-14; flag-dispositions #14; decisions.md §4 rows 13; architecture-recommendation.md AM-9, §12; plan council record 6601b15a |
+ | 18 | 2026-11-05 | phase4 D4.8 as folded — the refusal vocabulary is FROZEN at 18 codes (import-time assertion + unit pins) and the council's mapping routes any unmapped service code to the catch-all: an EMPTY/WHITESPACE memoir_recall query (service code `query_required`, daemon/services/memoir_retrieval.py:374 — the predicate `(query or "").strip()` covers whitespace) is reported as `memoir_internal_error`, i.e. a USER-INPUT error reported as an INTERNAL error. The frozen set's argument family is entirely FIELD-SPECIFIC (reason_required, actor_required, invalid_actor, importance_out_of_range, body_to_long, title_to_long, stars_out_of_bounds, invalid_event_at, k_out_of_range, invalid_window) — no generic argument code exists. | USER-RULED — the user, as OWNER of the memoir tool surface (2026-11-05, verbatim): "as the OWNER of the memoir tool surface, I rule that TRUTHFULNESS OF THE ERROR OUTWEIGHS THE FROZEN COUNT — at pre-release, before anything ships and before any pin/compat burden exists, authorizing a 19th code for a genuine user-input error is cheap; shipping a code that calls a user mistake an 'internal error' is a permanent lie in the contract, and my own future instances will read those errors and mis-learn from them. So: if an existing code (or a best-fit 19th) can say 'empty/invalid query' honestly, take it — authorize the 19th if necessary. Keep 18 ONLY if one of them is genuinely honest for this case." ARCHITECT AUDIT (the reasoning, 18/18 tested for a GENUINELY honest fit): 0 of 18 honest. Every candidate is adjacent-but-dishonest — memoir_reason_required and memoir_actor_required sit in the right 'required-field' family but name the PIN REASON and ACTOR fields (a wrong-field code invites a wrong retry); memoir_k_out_of_range and memoir_invalid_window sit in the right 'argument' family but name a DIFFERENT parameter (k is valid here; recall has no window); memoir_body_too_long / title_too_long / stars_out_of_bounds / importance_out_of_range / invalid_event_at / invalid_actor name other fields; memoir_not_found / not_a_keepsake / keepsake_cap_reached / budget_exceeded / disabled / agent_unknown / not_authorized are domain, identity or knob codes; memoir_internal_error claims a bug where the caller sent an empty string — precisely the permanent lie the ruling forbids. RULING: AUTHORIZE the 19th code **`memoir_query_required`** (the 'required' sub-family, consistent with reason_required/actor_required), mapped IDENTITY from the service's `query_required`. A generic `memoir_invalid_argument` was rejected (a dumping ground that re-creates the same mislabelling class at a nicer label); silent reinterpretation of an empty query as 'most recent' was rejected (masks a caller bug and smuggles memoir_timeline semantics into memoir_recall — the two-tools-two-expectations error flag #10 warns against). CONSEQUENCES, exactly: memoir.py gains the constant + one frozenset member, the import-time assertion 18 -> 19 (:201) and the docstring count (:42); the mapping table gains query_required -> memoir_query_required and the catch-all loses its now-mapped example; `_RECALL_CODE_MAP` gains the identity entry; the unit pack's count pin (:171) AND its test name ('eighteen') flip, and the fallback test (:410) inverts to assert the true code (red-to-green, mutation-meaningful). NOT TOUCHED: the PRIVILEGED_TOOL_CATEGORIES census (#18 of 18), the 5-tool/4-family counts, DYNAMIC/KNOWN_TOOL_NAMES, the prompt-artifact baseline (the ari doctrine does not enumerate the set), MT-01 (it carries the CATEGORY count 18->17, not this one), the memoir mutation matrix (10 legs, no code reference), and any schema/migration artifact. The pin/coverage consequences are carried VERBATIM into the phase-8 ceremony notes (architecture-recommendation.md §13.6). | user-authorized — to be implemented in the code council's wave-4 fix cycle; ceremony notes text handed to the planner's phase-8 fold | USER RULING 2026-11-05 (owner of the memoir tool surface); phase4-plan.md D4.8; daemon/tools/memoir.py:42, 156-201, 340-348; daemon/services/memoir_retrieval.py:374; tests/unit/test_memoir_tools_unit.py:170-171, 410-420; architecture-recommendation.md AM-21, §13 |
+ | 19 | 2026-11-05 | Phase C code council (record 003966f4c) — the dev's FIVE in-mandate calls: (i) the agent_id predicate in the PG lexical SQL (repository-scoped bound parameter from the factory-captured identity, repository.py:471-486); (ii) MemoirRepository constructed per assembly (wraps the caller's engine, never builds one, never reads ambient POSTGRES_*); (iii) the dynamic briefing zone excluding keepsakes (already recorded phase-notes-p5.md); (iv) the readyz advisory injected at the route (post-verdict injection is what makes advisory-only structural, api.py:5766-5783); (v) THE SEMANTIC WIRING — the retrieval service resolves a DEFAULT query embedder internally when none is injected (phase-7 ITEM 1), so a production memoir_recall performs a bounded NETWORK embed of the query INSIDE the agent's turn. (i)-(iv) ruled in-mandate UNANIMOUSLY; (v) SPLIT 1-1. | RULING (council, reviewer-resolved): CODE ACCEPTABLE — both councilors agree the code is acceptable; the divergence was bookkeeping only, and the ledger row is the cheaper, invariant-preserving resolution (vs. re-plumbing an injected embedder through the tool factory). PRODUCTION-BEHAVIOR CONSEQUENCE, PLAINLY: every production memoir_recall call performs a default ~5-second-budget network embed of the query INSIDE the agent's turn before hybrid fusion (MEMOIR_QUERY_EMBED_TIMEOUT_S = 5.0, memoir_retrieval.py:112); on ANY exception, timeout, or config-missing the service degrades to lexical-only (the design §7 degradation sentence) and never hangs. WHY ACCEPTABLE: the in-turn query embed is DESIGN-REQUIRED, not a deviation — design §7 ranks by cosine over the query embedding and phase2 D2.3 specifies "embed the QUERY once per call"; the wiring implements the blessed retrieval algorithm. The call is ONCE per recall, bounded by a code constant whose rationale is documented in place (memoir_retrieval.py:106-112: "it sits in an agent's turn, so it gets a SHORT bounded timeout"), degrading on failure, and disclosed at the agent surface by the recall footer's rank-basis and missing-embedding fields (AM-33). ARCHITECT POSITION: NO amendment owed — the budget is documented, bounded, degrading, and design-required; a knob would add surface for no gain (the council's 'cheaper, invariant-preserving resolution' is endorsed). | code acceptable as shipped; recorded per the council ruling; the fix cycle left the semantic wiring UNCHANGED (C-1 d278ee71c, W-1 25ef71634, W-2 f012b4787, V-1 4e30a4f6c, W-3 b925d34eb) | code-review.md @ 003966f4c ('Ruling on the dev's five adjudications', item (v); follow-on requirement 6); design §7; phase2-plan.md D2.3; daemon/services/memoir_retrieval.py:98-112, 302-333; implementation e7a1defa3 (phase 7) on deb4b223d (phase 2); fix cycle b925d34eb |
++| 20 | 2026-11-18 | DD-9 amendment — agent-scoped persona SELF-RATIFY exemption for the agent id "ari" (PERSONA_SELF_RATIFY_AGENTS): a persona version authored by that agent may move draft -> pending_approval -> approved without a human session actor, but ONLY when the actor is the agent itself on the internal lane (auth_method="internal" AND actor.agent_id == version.agent_id). Every other agent keeps the human lane exactly as shipped. The transition vocabulary is unchanged; self-ratification still walks the two steps draft -> pending_approval -> approved; submit() is unchanged; reject() is unchanged; the REST surface stays closed (a bearer actor still gets 403 HUMAN_LANE_REQUIRED). | RULING (user, 2026-11-18, paraphrased only — the verbatim carries persona-lore tokens this repo was swept clean of; the ruling is recorded in the operator's private store): the exemption is granted for that one agent because it works face-to-face with the user daily, so its review is continuous rather than queued; other agents lack that proximity and keep the gate. Mitigations: append-only versions keep any drift one row from revert; daily human proximity means the review is continuous; the core-prompt lane stays token-gated. Surviving invariants: transition vocabulary unchanged (draft->approved still invalid for every agent); the two-step draft->pending_approval->approved; append-only versions; REST scope unchanged; reject unchanged. | implemented as adjudicated — repository.py _require_human_lane gained allow_self_ratify=False keyword (approve passes True, reject does not); models.py gained PERSONA_SELF_RATIFY_AGENTS | daemon/repositories/memoir/models.py; daemon/repositories/memoir/repository.py; tests/unit/test_memoir_persona_lane.py |
+ **Columns:** `#` (sequential), `Date` (ISO), `Deviation` (what the design specifies **and** what reality forces — both, not just the problem), `Adjudication` (the architect's ruling, quoted where possible), `Outcome` (implemented as adjudicated / superseded by a later row / withdrawn), `Links` (commit, file, or `design.md` § for the affected text).
+ 
+ **Also record here (same table) if chosen in Phase B:** outcomes for the open items in `design.md` §14 — resolving one *is* an adjudication *if* it changes blessed text. Choosing the keepsake cap integer, for example, fills a delegated blank; it is still worth a row so the number has provenance.
+diff --git a/daemon/repositories/memoir/models.py b/daemon/repositories/memoir/models.py
+index c0838fd39..d7833e3c8 100644
+--- a/daemon/repositories/memoir/models.py
++++ b/daemon/repositories/memoir/models.py
+@@ -160,6 +160,15 @@ PERSONA_VALID_TRANSITIONS: frozenset[tuple[str, str]] = frozenset({
+     ("draft", "archived"),
+ })
+ 
++#: Agent ids exempt from the human-lane requirement on PERSONA approval,
++#: but ONLY when the actor is the agent itself acting through the
++#: internal lane (DD-9 amendment, user ruling 2026-11-18, paraphrased:
++#: that agent works face-to-face with the user daily, so its review is
++#: continuous rather than queued; other agents lack that proximity and
++#: keep the gate). Append-only versions keep any drift one row from
++#: revert, and the transition vocabulary is unchanged for everyone.
++PERSONA_SELF_RATIFY_AGENTS: frozenset[str] = frozenset({"ari"})
++
+ #: The persona version STATUS vocabulary (the CHECK constraint domain).
+ PERSONA_STATUSES: frozenset[str] = frozenset({
+     "draft",
+@@ -942,6 +951,7 @@ __all__ = [
+     "MemoirSynthesis",
+     "PERSONA_SOURCES",
+     "PERSONA_STATUSES",
++    "PERSONA_SELF_RATIFY_AGENTS",
+     "PERSONA_VALID_TRANSITIONS",
+     "PIN_ACTORS",
+     "StarsListType",
+diff --git a/daemon/repositories/memoir/repository.py b/daemon/repositories/memoir/repository.py
+index a5d698597..0bc10cfa4 100644
+--- a/daemon/repositories/memoir/repository.py
++++ b/daemon/repositories/memoir/repository.py
+@@ -55,6 +55,7 @@ from daemon.repositories.memoir.models import (
+     KEEPSAKE_ADMISSION_PCLASS,
+     PERSONA_SOURCES,
+     PERSONA_STATUSES,
++    PERSONA_SELF_RATIFY_AGENTS,
+     PERSONA_VALID_TRANSITIONS,
+     PIN_ACTORS,
+     MemoirEpisode,
+@@ -222,13 +223,14 @@ class MemoirPersonaRepository:
+ 
+         Raises:
+             HumanLaneRequiredError: when ``actor.auth_method`` is not
+-                ``"session"``. The house discriminator is reused
++                ``"session"`` (and the agent-scoped self-ratify exemption
++                does not apply). The house discriminator is reused
+                 verbatim (AM-17), so the seam is safe by construction
+                 rather than safe by a grep pin.
+             MemoirTransitionError: when the current status does not
+                 permit ``pending_approval -> approved``.
+         """
+-        self._require_human_lane(version_id, actor)
++        self._require_human_lane(version_id, actor, allow_self_ratify=True)
+         return self._transition(version_id, "approved", actor=actor)
+ 
+     def reject(self, version_id: str, actor: Any) -> MemoirPersonaVersion:
+@@ -238,14 +240,22 @@ class MemoirPersonaRepository:
+ 
+     # ── internals ─────────────────────────────────────────────────────────
+ 
+-    @staticmethod
+-    def _require_human_lane(version_id: str, actor: Any) -> None:
++    def _require_human_lane(
++        self, version_id: str, actor: Any, allow_self_ratify: bool = False
++    ) -> None:
+         """Raise the house ``HumanLaneRequiredError`` unless human.
+ 
+         The check is a single equality test on the lane discriminator
+         (``actor.auth_method``) — NOT on the scope claim: a bearer
+         token holding the right scope is still refused, because a
+         machine cannot satisfy the human-only approval rule.
++
++        The agent-scoped self-ratify exemption (DD-9 amendment) applies
++        only when ALL THREE hold: the version's agent is in
++        ``PERSONA_SELF_RATIFY_AGENTS``, the actor uses the internal
++        lane, and the actor's agent id matches the version's agent id.
++        A lookup that finds no version leaves the exemption inactive,
++        so a refusal is never turned into a not-found error.
+         """
+         # Imported lazily: the repository layer keeps no module-load
+         # dependency on the service layer, and the exception object is
+@@ -254,6 +264,17 @@ class MemoirPersonaRepository:
+         from daemon.services.prompt_version_service import HumanLaneRequiredError
+ 
+         auth_method = getattr(actor, "auth_method", None)
++        if (
++            allow_self_ratify
++            and auth_method == "internal"
++        ):
++            version = self.get_version(version_id)
++            if (
++                version is not None
++                and version.agent_id in PERSONA_SELF_RATIFY_AGENTS
++                and getattr(actor, "agent_id", None) == version.agent_id
++            ):
++                return
+         if auth_method != "session":
+             raise HumanLaneRequiredError(
+                 version_id=version_id,
+diff --git a/tests/unit/test_memoir_persona_lane.py b/tests/unit/test_memoir_persona_lane.py
+index 9c578e67e..600137d7a 100644
+--- a/tests/unit/test_memoir_persona_lane.py
++++ b/tests/unit/test_memoir_persona_lane.py
+@@ -224,3 +224,81 @@ def test_submit_is_a_seam_without_a_v1_caller():
+ def test_get_current_is_none_without_a_persona(repo):
+     assert repo.get_current("ari") is None
+     assert repo.get_current("nobody") is None
++
++
++# ── DD-9 amendment: agent-scoped self-ratify exemption ───────────────────
++
++
++class _SelfActor:
++    """An actor carrying both the lane discriminator and an agent id."""
++
++    def __init__(self, auth_method: str, agent_id: str | None = None) -> None:
++        self.auth_method = auth_method
++        self.agent_id = agent_id
++
++
++def test_self_ratify_exempt_ari_internal_self(repo):
++    """(i) exempt 'ari' + daemon-internal self actor -> approved + pointer moves."""
++    draft = repo.create_draft("ari", "# A new self")
++    pending = repo.submit(draft.id, actor=_Actor("bearer"))
++    approved = repo.approve(pending.id, actor=_SelfActor("internal", "ari"))
++    assert approved.status == "approved"
++
++    current = repo.get_current("ari")
++    assert current is not None
++    assert current.id == approved.id
++
++
++def test_self_ratify_non_exempt_leader_internal_raises(repo):
++    """(ii) non-exempt 'leader' + non-session actor -> HumanLaneRequiredError."""
++    draft = repo.create_draft("leader", "# A new self")
++    pending = repo.submit(draft.id, actor=_Actor("bearer"))
++    with pytest.raises(HumanLaneRequiredError):
++        repo.approve(pending.id, actor=_SelfActor("internal", "leader"))
++
++
++def test_self_ratify_leader_session_succeeds(repo):
++    """(iii) non-exempt 'leader' + session actor -> approved."""
++    draft = repo.create_draft("leader", "# A new self")
++    pending = repo.submit(draft.id, actor=_Actor("bearer"))
++    approved = repo.approve(pending.id, actor=_Actor("session"))
++    assert approved.status == "approved"
++
++
++def test_self_ratify_draft_to_approved_refused_for_both(repo):
++    """(iv) draft->approved direct refused for 'ari' (self) and 'leader'."""
++    for agent in ("ari", "leader"):
++        draft = repo.create_draft(agent, "# A new self")
++        actor = _SelfActor("internal", "ari") if agent == "ari" else _Actor("session")
++        with pytest.raises(MemoirTransitionError):
++            repo.approve(draft.id, actor=actor)
++
++
++def test_self_ratify_reject_still_requires_human_lane_for_ari(repo):
++    """(v) reject() still requires the human lane for 'ari' (self actor too)."""
++    draft = repo.create_draft("ari", "# A new self")
++    pending = repo.submit(draft.id, actor=_Actor("bearer"))
++    with pytest.raises(HumanLaneRequiredError):
++        repo.reject(pending.id, actor=_SelfActor("internal", "ari"))
++
++
++def test_self_ratify_ari_bearer_raises(repo):
++    """(vi) 'ari' + bearer actor -> HumanLaneRequiredError."""
++    draft = repo.create_draft("ari", "# A new self")
++    pending = repo.submit(draft.id, actor=_Actor("bearer"))
++    with pytest.raises(HumanLaneRequiredError):
++        repo.approve(pending.id, actor=_SelfActor("bearer", "ari"))
++
++
++def test_self_ratify_ari_internal_not_ari_raises(repo):
++    """(vii) 'ari' + internal actor whose agent_id is NOT 'ari' -> raises."""
++    draft = repo.create_draft("ari", "# A new self")
++    pending = repo.submit(draft.id, actor=_Actor("bearer"))
++    with pytest.raises(HumanLaneRequiredError):
++        repo.approve(pending.id, actor=_SelfActor("internal", "leader"))
++
++
++def test_self_ratify_nonexistent_version_raises_human_lane(repo):
++    """(viii) non-human actor + nonexistent version id -> HumanLaneRequiredError."""
++    with pytest.raises(HumanLaneRequiredError):
++        repo.approve("does-not-exist", actor=_Actor("bearer"))
+```
+
+## Bash-raises used
+
+none. Every command ran inside the 600 s default budget. The two `uv sync` runs and the base-leg suite+pack ran as background `proc` jobs under a 900 s proc timeout — not a bash raise, and no raise was consumed.
+
+## Complexity self-assessment
+
+`low` — and the band stands unchanged from the plan-open assessment.
+
+Reasoning from the plan: ONE module, four files, ONE behavior seam (the persona-version approval guard). No schema, migration, config, env-flag or dependency change; no new caller surface; no new tool or REST surface; the transition vocabulary, `submit()`, `_transition()` and the route file are untouched. The risk was concentrated in three mechanical seams, each of which is now pinned by a test and was checked before the merge: (1) the registered pack's mutation leg, which string-replaces the exact literal line `        if auth_method != "session":` in the repository file and requires exactly one occurrence (verified: exactly one, at repository.py:278); (2) the error-semantics ORDER (a refusal must never become a not-found ValueError); (3) the exact-set caller pin (`test_submit_is_a_seam_without_a_v1_caller`), which forbids a new daemon/ module importing the repository. Each module fit inside one sonic-coder one-shot turn, as predicted.
+
+Recorded honestly: the commission's plan-open `explore()` knowledge read TIMED OUT (the Explorer agent did not answer within its 300 s budget), so the stored-experience input to this lane came from the project operating-knowledge store plus direct source reading. No handover was raised on that basis — the complexity verdict does not depend on the knowledge read. No raised handover was overridden for this commission, so no original-verdict/override attribution applies.
diff --git a/daemon/repositories/memoir/models.py b/daemon/repositories/memoir/models.py
index c0838fd39..d7833e3c8 100644
--- a/daemon/repositories/memoir/models.py
+++ b/daemon/repositories/memoir/models.py
@@ -160,6 +160,15 @@ PERSONA_VALID_TRANSITIONS: frozenset[tuple[str, str]] = frozenset({
     ("draft", "archived"),
 })
 
+#: Agent ids exempt from the human-lane requirement on PERSONA approval,
+#: but ONLY when the actor is the agent itself acting through the
+#: internal lane (DD-9 amendment, user ruling 2026-11-18, paraphrased:
+#: that agent works face-to-face with the user daily, so its review is
+#: continuous rather than queued; other agents lack that proximity and
+#: keep the gate). Append-only versions keep any drift one row from
+#: revert, and the transition vocabulary is unchanged for everyone.
+PERSONA_SELF_RATIFY_AGENTS: frozenset[str] = frozenset({"ari"})
+
 #: The persona version STATUS vocabulary (the CHECK constraint domain).
 PERSONA_STATUSES: frozenset[str] = frozenset({
     "draft",
@@ -942,6 +951,7 @@ __all__ = [
     "MemoirSynthesis",
     "PERSONA_SOURCES",
     "PERSONA_STATUSES",
+    "PERSONA_SELF_RATIFY_AGENTS",
     "PERSONA_VALID_TRANSITIONS",
     "PIN_ACTORS",
     "StarsListType",
diff --git a/daemon/repositories/memoir/repository.py b/daemon/repositories/memoir/repository.py
index a5d698597..0bc10cfa4 100644
--- a/daemon/repositories/memoir/repository.py
+++ b/daemon/repositories/memoir/repository.py
@@ -55,6 +55,7 @@ from daemon.repositories.memoir.models import (
     KEEPSAKE_ADMISSION_PCLASS,
     PERSONA_SOURCES,
     PERSONA_STATUSES,
+    PERSONA_SELF_RATIFY_AGENTS,
     PERSONA_VALID_TRANSITIONS,
     PIN_ACTORS,
     MemoirEpisode,
@@ -222,13 +223,14 @@ class MemoirPersonaRepository:
 
         Raises:
             HumanLaneRequiredError: when ``actor.auth_method`` is not
-                ``"session"``. The house discriminator is reused
+                ``"session"`` (and the agent-scoped self-ratify exemption
+                does not apply). The house discriminator is reused
                 verbatim (AM-17), so the seam is safe by construction
                 rather than safe by a grep pin.
             MemoirTransitionError: when the current status does not
                 permit ``pending_approval -> approved``.
         """
-        self._require_human_lane(version_id, actor)
+        self._require_human_lane(version_id, actor, allow_self_ratify=True)
         return self._transition(version_id, "approved", actor=actor)
 
     def reject(self, version_id: str, actor: Any) -> MemoirPersonaVersion:
@@ -238,14 +240,22 @@ class MemoirPersonaRepository:
 
     # ── internals ─────────────────────────────────────────────────────────
 
-    @staticmethod
-    def _require_human_lane(version_id: str, actor: Any) -> None:
+    def _require_human_lane(
+        self, version_id: str, actor: Any, allow_self_ratify: bool = False
+    ) -> None:
         """Raise the house ``HumanLaneRequiredError`` unless human.
 
         The check is a single equality test on the lane discriminator
         (``actor.auth_method``) — NOT on the scope claim: a bearer
         token holding the right scope is still refused, because a
         machine cannot satisfy the human-only approval rule.
+
+        The agent-scoped self-ratify exemption (DD-9 amendment) applies
+        only when ALL THREE hold: the version's agent is in
+        ``PERSONA_SELF_RATIFY_AGENTS``, the actor uses the internal
+        lane, and the actor's agent id matches the version's agent id.
+        A lookup that finds no version leaves the exemption inactive,
+        so a refusal is never turned into a not-found error.
         """
         # Imported lazily: the repository layer keeps no module-load
         # dependency on the service layer, and the exception object is
@@ -254,6 +264,17 @@ class MemoirPersonaRepository:
         from daemon.services.prompt_version_service import HumanLaneRequiredError
 
         auth_method = getattr(actor, "auth_method", None)
+        if (
+            allow_self_ratify
+            and auth_method == "internal"
+        ):
+            version = self.get_version(version_id)
+            if (
+                version is not None
+                and version.agent_id in PERSONA_SELF_RATIFY_AGENTS
+                and getattr(actor, "agent_id", None) == version.agent_id
+            ):
+                return
         if auth_method != "session":
             raise HumanLaneRequiredError(
                 version_id=version_id,
diff --git a/tests/unit/test_memoir_persona_lane.py b/tests/unit/test_memoir_persona_lane.py
index 9c578e67e..fcdb28102 100644
--- a/tests/unit/test_memoir_persona_lane.py
+++ b/tests/unit/test_memoir_persona_lane.py
@@ -224,3 +224,101 @@ def test_submit_is_a_seam_without_a_v1_caller():
 def test_get_current_is_none_without_a_persona(repo):
     assert repo.get_current("ari") is None
     assert repo.get_current("nobody") is None
+
+
+# ── DD-9 amendment: agent-scoped self-ratify exemption ───────────────────
+
+
+class _SelfActor:
+    """An actor carrying both the lane discriminator and an agent id."""
+
+    def __init__(self, auth_method: str, agent_id: str | None = None) -> None:
+        self.auth_method = auth_method
+        self.agent_id = agent_id
+
+
+def test_self_ratify_exempt_ari_internal_self(repo):
+    """(i) exempt 'ari' + daemon-internal self actor -> approved + pointer moves."""
+    draft = repo.create_draft("ari", "# A new self")
+    pending = repo.submit(draft.id, actor=_Actor("bearer"))
+    approved = repo.approve(pending.id, actor=_SelfActor("internal", "ari"))
+    assert approved.status == "approved"
+
+    current = repo.get_current("ari")
+    assert current is not None
+    assert current.id == approved.id
+
+
+def test_self_ratify_non_exempt_leader_internal_raises(repo):
+    """(ii) non-exempt 'leader' + non-session actor -> HumanLaneRequiredError."""
+    draft = repo.create_draft("leader", "# A new self")
+    pending = repo.submit(draft.id, actor=_Actor("bearer"))
+    with pytest.raises(HumanLaneRequiredError):
+        repo.approve(pending.id, actor=_SelfActor("internal", "leader"))
+
+
+def test_self_ratify_leader_session_succeeds(repo):
+    """(iii) non-exempt 'leader' + session actor -> approved."""
+    draft = repo.create_draft("leader", "# A new self")
+    pending = repo.submit(draft.id, actor=_Actor("bearer"))
+    approved = repo.approve(pending.id, actor=_Actor("session"))
+    assert approved.status == "approved"
+
+
+def test_self_ratify_draft_to_approved_refused_for_both(repo):
+    """(iv) draft->approved direct refused for 'ari' (self) and 'leader'."""
+    for agent in ("ari", "leader"):
+        draft = repo.create_draft(agent, "# A new self")
+        actor = _SelfActor("internal", "ari") if agent == "ari" else _Actor("session")
+        with pytest.raises(MemoirTransitionError):
+            repo.approve(draft.id, actor=actor)
+
+
+def test_self_ratify_reject_still_requires_human_lane_for_ari(repo):
+    """(v) reject() still requires the human lane for 'ari' (self actor too)."""
+    draft = repo.create_draft("ari", "# A new self")
+    pending = repo.submit(draft.id, actor=_Actor("bearer"))
+    with pytest.raises(HumanLaneRequiredError):
+        repo.reject(pending.id, actor=_SelfActor("internal", "ari"))
+
+
+def test_self_ratify_ari_bearer_raises(repo):
+    """(vi) 'ari' + bearer actor -> HumanLaneRequiredError."""
+    draft = repo.create_draft("ari", "# A new self")
+    pending = repo.submit(draft.id, actor=_Actor("bearer"))
+    with pytest.raises(HumanLaneRequiredError):
+        repo.approve(pending.id, actor=_SelfActor("bearer", "ari"))
+
+
+def test_self_ratify_ari_internal_not_ari_raises(repo):
+    """(vii) 'ari' + internal actor whose agent_id is NOT 'ari' -> raises."""
+    draft = repo.create_draft("ari", "# A new self")
+    pending = repo.submit(draft.id, actor=_Actor("bearer"))
+    with pytest.raises(HumanLaneRequiredError):
+        repo.approve(pending.id, actor=_SelfActor("internal", "leader"))
+
+
+def test_self_ratify_nonexistent_version_raises_human_lane(repo):
+    """(viii) non-human actor + nonexistent version id -> HumanLaneRequiredError."""
+    with pytest.raises(HumanLaneRequiredError):
+        repo.approve("does-not-exist", actor=_Actor("bearer"))
+
+
+def test_self_ratify_internal_self_actor_missing_version_raises_human_lane(repo):
+    """MUTATION-SENSITIVE: the internal lane on a missing row is a LANE refusal.
+
+    The self-ratify exemption's lookup must never turn a refusal into a
+    not-found error: a nonexistent version id cannot satisfy the
+    exemption's preconditions (there is no row whose agent could match
+    the actor), so the call fails the human lane — with the house lane
+    error class, not a plain ``ValueError``.
+    """
+    with pytest.raises(HumanLaneRequiredError) as excinfo:
+        repo.approve("does-not-exist", actor=_SelfActor("internal", "ari"))
+    # The CLASS is pinned exactly: HumanLaneRequiredError subclasses
+    # ValueError, so a plain ValueError would satisfy a bare
+    # ``pytest.raises(ValueError)`` — the class pin is what kills the
+    # not-found mutant.
+    assert type(excinfo.value) is HumanLaneRequiredError
+    assert excinfo.value.version_id == "does-not-exist"
+    assert "not found" not in str(excinfo.value)
```

## Bash-raises used

none. Every command ran inside the 600 s default budget. The two `uv sync` runs and the base-leg suite+pack ran as background `proc` jobs under a 900 s proc timeout — not a bash raise, and no raise was consumed.

## Complexity self-assessment

`low` — and the band stands unchanged from the plan-open assessment.

Reasoning from the plan: ONE module, four files, ONE behavior seam (the persona-version approval guard). No schema, migration, config, env-flag or dependency change; no new caller surface; no new tool or REST surface; the transition vocabulary, `submit()`, `_transition()` and the route file are untouched. The risk was concentrated in three mechanical seams, each of which is now pinned by a test and was checked before the merge: (1) the registered pack's mutation leg, which string-replaces the exact literal line `        if auth_method != "session":` in the repository file and requires exactly one occurrence (verified: exactly one, at repository.py:278); (2) the error-semantics ORDER (a refusal must never become a not-found ValueError); (3) the exact-set caller pin (`test_submit_is_a_seam_without_a_v1_caller`), which forbids a new daemon/ module importing the repository. Each module fit inside one sonic-coder one-shot turn, as predicted.

Recorded honestly: the commission's plan-open `explore()` knowledge read TIMED OUT (the Explorer agent did not answer within its 300 s budget), so the stored-experience input to this lane came from the project operating-knowledge store plus direct source reading. No handover was raised on that basis — the complexity verdict does not depend on the knowledge read. No raised handover was overridden for this commission, so no original-verdict/override attribution applies.

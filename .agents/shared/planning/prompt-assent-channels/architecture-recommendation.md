# Architecture Recommendation — prompt-assent Phase 1.5 (designated channels, pinned-approver model)

| Field | Value |
|---|---|
| Date | 2026-11-11 (UTC) |
| Architect instance | architect (controller) — 5 dispatched verification passes, all read-only in the worktree |
| Worktree | `/home/nea/ensemble-src-wt-assent-p15-channels` — branch `feature/assent-designated-channels` @ `efa8aaae28153b8e9c49b86abe19e92be5458ebe` (verified = origin/latest) |
| Passes | structural (A+C) `a599e057` · security (D+E+threat) `92f7d910` · trade-off (B+F) `47ddba59` · resilience (G+H) `b0688719` · plan-consistency sweep `c715a555` |
| Status | **RULING** — binding on implementation for the Phase-1.5 questions |
| Header rule | This artefact wins over every file of this set and over every parent document **for the Phase-1.5 questions**; the parent (`../prompt-assent/architecture-recommendation.md`) still wins for the parent's own questions (D-1). The plan's `decisions.md` rows marked `RECOMMENDED-PENDING-ARCHITECT` are ruled in §12 — the plan lane folds them in place (this lane does not edit the plan set from here). |

---

## 0. Verdict summary

| Item | Verdict | One line |
|---|---|---|
| **A** — the disclosure ("DM path BYTE-IDENTICAL") | **RATIFY-WITH-CHANGES** | The narrowing (behaviour, not bytes) is the only *satisfiable* reading and the matrix states it honestly — but the ledger still asserts the byte claim (BLESSED-3, D-7) and must be amended; the enforceable guarantee is INVARIANT-DM below, not prose |
| **B** — config surface | **RATIFY-WITH-CHANGES — ENV stands** | Decisive axis = risk; the DB tier is agent-writable *without bash*; the one real residual (bash→`.env`→restart) is **Ari-triggerable**, not "operator-controlled" — S-1 must be rewritten and compensated |
| **C** — predicate ownership + ordering | **RATIFY-WITH-CHANGES** | Ordering is **convention, not code shape**; every existing pin is outcome-only and stays green under a wrong order; the group-DM boundary has **no pin that can detect its breach** (M-15 missing) |
| **D** — trust anchor | **RATIFY-WITH-CHANGES** | The nested `metadata["discord"]["user_id"]` anchor is sound and unspoofable by the HTTP path; approve-side pinning **exists** at 4f — but D-6's heading denies it, and the 4f site as specified **revokes the token at 5 attempts** |
| **E** — the two new codes | **RATIFY-WITH-CHANGES** | Mint side provable by code structure (the mint path never consults the budget at all); the **durable** counter is not class-gated at `:2021` — the plan's own A-9/A-10 cells are unreachable as written; census coverage confirmed but two ambiguities remain |
| **F** — open questions | **ALL RULED** | Q-1 NO · Q-2 NO · Q-3 unchanged · Q-4 YES · Q-5 NO; restart-vs-call-time **reconciled** (both true); map-read pinned to direct `os.environ` |
| **G** — the five-leg probe | **RATIFY-WITH-CHANGES** | The leg *set* is right; the *shape* is not executable — two-phase (map unset→set) is mandatory, "ARMED" needs three states, the second human must be a second **account**, and leg 4 cannot distinguish typo from undesignated without Phase A |
| **H** — v0.29.19 staging, v0.29.18 left standing | **RATIFY-WITH-CHANGES** | LEFT STANDING is achievable; **exactly one** coupling exists (promote-time eviction); v0.29.18 survives exactly **two** further promotes, protected by nothing; two build-day traps (`ENSEMBLE_ROLLBACK_SAFE=1`, bump commit subject) are missing from the plan |

Every pass ran adversarially against the plan; all five confirmed their skill loaded (no skill-bank misses, no degraded passes). No pass ran any test, script or build — all verdicts are static, re-derived from the worktree.

---

## 1. The ruling in one paragraph

**Build it — the architecture is sound and the trust model holds** — the extension is genuinely a predicate change on an identity-carrying message row that no non-Discord path can forge, the room+pinned-id conjunction authenticates, cross-surface and cross-pin replay are caught by existing legs, and the DM path's *behaviour* is preservable. But the plan set is **not ready for council as written**: it carries four mutually-corroborated contradictions that all land on the approve-side call site (D-6's heading vs D-8 vs matrix §1.2 vs T2.5), an unenforceable "byte-identical" ledger row, an unpinned group-DM trust boundary, an approve-side algorithm that as specified **revokes the Emperor's token at five re-designation refusals**, and two threats the plan does not contain at all (the token+diff posted into a multi-occupant room; the agent-reachable `.env`+restart path to the "operator-only" map). All fixes are text or one-line code-shape changes; none re-opens the design. §11 is the flat must-change list — 34 rows, 6 of them blocking-class.

---

## 2. Item A — the disclosure to ratify

**Verdict: RATIFY-WITH-CHANGES.**

**2.1 The commission's demand is unsatisfiable as literally worded, and the plan's narrowing is the correct reading of intent.** The mint gate's combined condition at `daemon/tools/prompt_assent.py:1709` *executes on every mint* (a DM merely makes it False), and the 4f call at `:2019` *executes on every approve*. Both sites must be edited (E2/E3 below) — so "byte-identical DM path" cannot mean bytes of `daemon/tools/prompt_assent.py`. The matrix's own statement (`refusal-cell-matrix.md:138-143`) is honest about this. What is **false as written** is the matrix's stronger phrase "*the extension is structurally unable to affect it*" (`:38-40`): it is unable to affect the DM *outcome* only if the designated branch is placed **inside** the `not private` arm of the service predicate (`daemon/services/prompt_version_service.py:460-476`) and **after** the DM short-circuit in the mint gate — a **code-shape** requirement the plan states only as an *order* instruction (`phase2-plan.md:50`).

**2.2 The exact edit surface (re-derived; the plan's anchors were mostly right, three stale — see §11 row 30).**

| # | Site | DM-reachable? |
|---|---|---|
| E1 | `prompt_assent.py` — map resolver beside `_resolve_knobs`/`_resolve_enabled` (`:380-478`) | only if eager (forbidden — see §4) |
| E2 | `prompt_assent.py:1708-1716` — the M-4c block | **YES** |
| E3 | `prompt_assent.py:2018-2026` — the 4f call site | **YES** |
| E4 | `prompt_version_service.py:460-476` — `assent_channel_not_private_code` | **YES** (called at `:2019`) |
| E5 | `prompt_assent.py:195-236` — taxonomy + placed codes (module level; read at `:2060`, `:2107`) | read on all paths |
| E6 | `prompt_assent.py:1712-1714` — the `mint-requires-private-channel` detail string | non-DM refusals — **see 2.4, this edit is now forbidden** |
| E7 | `mint_context.py` — new `resolve_pinned_approver` + `is_designated_assent_channel` (the `:29-50` classifier untouched) | new code only |
| E8 | `daemon/repositories/prompt_assent/designation.py` — NEW | no |
| E9/E10 | census/registration test + rig map control | test-only |

**2.3 What is honestly byte-identical vs provably changed.**

| Claim | Verdict | Evidence |
|---|---|---|
| FE surface (routes + repo) | **TRUE** | absent from F-1..F-12; the FE proof is method-scoped (`test_prompt_assent_fe_unchanged.py`, approve/reject def blocks; base `0efdf106f`) — E4's module-level edit cannot redden it |
| `is_private_assent_channel` | **TRUE as a function** | `mint_context.py:29-50` untouched (the file gains new siblings) |
| DM predicate legs | **TRUE** | same identity checks (`:48-49`) |
| DM cells: code / ledger / budget | **TRUE** | ledger write `:1787-1791` untouched (`mint_is_dm=1`); a DM never reaches a `MISMATCH_CODES` code (`:241-247`) |
| DM cells: **envelope** | **TRUE ONLY IF E6 is dropped** — see 2.4 | the detail string is shared by M-3/M-4/M-5 |
| `prompt_assent.py` / `prompt_version_service.py` / `mint_context.py` bytes | **FALSE** | E1-E7 |

**2.4 Ruling — the envelope stays byte-identical; the wording moves to the new codes.** T2.8 extends the shared `mint-requires-private-channel` detail string, which would change the M-3/M-4/M-5 envelopes and force "same envelope" (`refusal-cell-matrix.md:142`, P-3) to be dropped. That trade buys nothing: the two new codes already carry actionable text. **Ruled: the existing detail string stays byte-identical; the designated-channel wording lives on `approver-not-pinned` / `designation-config-invalid` messages only.** This keeps P-2/P-3/§4 and BLESSED-3's "same envelope" alive at zero cost. (If the plan lane prefers the shared-string edit anyway, then every "envelope" claim must go behaviour-only — one or the other, not both.)

**2.5 The enforceable guarantee — INVARIANT-DM (state verbatim in the plan):**

> For any turn where `is_private_assent_channel(discord_meta)` is `True`, the process MUST NOT read `os.environ["ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS"]`, MUST NOT call the resolver or `parse_designations`, and the outcome (code, envelope, token row, ledger shape, budget counter, attempt counter) MUST equal the v0.29.18 outcome.

Enforced by four independent legs: (i) a **runtime** pin — monkeypatch the resolver to *raise*, run a DM mint, a DM approve and a lane-disabled call, assert no raise and identical outcomes (the plan's P-2 source-shape pin alone is prose-satisfiable per the house's own non-vacuity doctrine, `test_prompt_assent_pins.py:22-26`); (ii) a source-shape pin that the resolver symbol appears in no DM-reachable function; (iii) the map-unset equivalence cell; (iv) the code-shape requirement of §4. A literal function-extraction (DM path in a function the designated path cannot reach) is *medium* cost and recommended as the shape, not required as the proof.

**2.6 The ledger must follow the body.** `decisions.md:68-74` (BLESSED-3 "byte-identical DM path") and `decisions.md:328` (D-7 "byte-for-byte") still assert the byte claim the body has narrowed — the exact `plan-body-ledger-agreement` failure mode. Amend both to the behaviour scope, character-for-character with `refusal-cell-matrix.md:138-143` (replacement text: §11 row 2).

---

## 3. Item B — config surface: ENV vs DB vs operator file vs hybrid

**Verdict: RATIFY-WITH-CHANGES — D-3 (ENV) is RULED.**

**3.1 The decisive fact re-verified.** `settings_set` (`daemon/tools/settings_tools.py:483`) writes any registered managed key (`:515-521`); the `settings` category is held by **Ari** (`agents/ari/meta.json:37` — the minting agent) and **leader** (`agents/leader/meta.json:15`). Two additional, un-cited supports: the registry's `value_type` vocabulary is `int|float|bool|str` only (`daemon/services/settings_service.py:147`; `_coerce_value` refuses others `:754`) — a map cannot be a native managed key; and the resolution ladder is **DB > env** (`settings_service.py:10-11`) — a DB row would make the **agent-writable tier authoritative over the operator's env**, which is precisely the escalation D-3 exists to close. This also settles Q-5 (§7).

**3.2 The 5-axis matrix** (weighted C×.20 S×.20 M×.25 R×.20 Co×.15; risk and cost inverted):

| Option | Complexity | Scalability | Maintainability | Risk | Cost | Weighted |
|---|---|---|---|---|---|---|
| **A — ENV var (plan)** | 5 | 4 | 4 | 4 | 5 | **4.35** |
| C — operator file | 3 | 3 | 3 | 3 | 4 | 3.15 |
| D — hybrid (env + read-only mirror) | 2 | 4 | 2 | 3 | 2 | 2.60 |
| B — restricted read-only DB tier | 1 | 3 | 2 | 2 | 1 | **1.73** |

Decisive axis: **risk**. B is the minting agent's own write surface *without bash*; C's bash exposure is identical to A's (no gain); D's mirror is a permanent staleness/precedence hazard. Flip conditions (both required): a verified per-key agent-write-exclusion in the settings tier, *and* an operator requirement for non-shell call-time-live edits (no tier here is call-time-live).

**3.3 Agent-settability — enumerated, exactly one path.** Settable via: **bash → edit the daemon's `.env` → restart** (32 agent definitions hold bash; Ari's `edit_file`/`write_file` deny at `agents/ari/meta.json:49` is not a boundary while bash is held at `:14`). NOT settable via: `settings_set` (registry-gated, `settings_tools.py:515-517`), HTTP routes (registered keys only, `daemon/routers/settings.py:966-969`, `:1023`; gated `settings:write`; with auth off the route is bash+curl-reachable — which *strengthens* the DB rejection), `proc_run`/`service_start` env (child-process env only), the executor allowlist (reads `os.environ`, cannot write it — at `daemon/tools/upgrade_journal.py:2593`, not `job_queue.py` as the commission guessed), `ens_env_read` (read-only). Zero `os.environ` write sites exist in `daemon/` (only the boot `setdefault` at `__main__.py:48`).

**3.4 The residual is mischaracterized — rewrite S-1.** The restart is **not "operator-controlled"**: Ari holds `system_restart` (`agents/ari/meta.json:31`) and `service` (`:33`); live restarts are provenance-gated, not operator-gated (`daemon/tools/upgrade_tools.py:136`, `:2707-2712`) — armed by a genuine user turn, **direct or inherited**; off-live restarts are free (`:136`); and the launcher re-exports `.env` and respawns the child on exit (`launcher.sh` crash-restart). So an Ari-authored `.env` edit becomes effective at an Ari-triggerable restart during normal operation. **The true boundary**: (i) the map leaves every audited tool surface (bash-only — invisible to the settings audit trail); (ii) even an agent-authored map **cannot mint without the pinned human's daemon-derived turn** (`prompt_assent.py:674-702`) — agent-settability buys *un-vetted channel expansion*, not a rubber stamp. S-1 must say both, and the runbook must not lean on the file-tool deny list.

**3.5 Compensating controls (ruled, with the successor option recorded).** (i) A **boot-time log line** naming the map's entry count and a content-free fingerprint (e.g. first-8 sha256 of the canonical JSON) — any change becomes visible in the boot log the operator already reviews; (ii) runbook rule: re-run probe leg 3 after **any** map edit (the only typo feedback loop — rule 9's valid-format-wrong-id produces no signal at all); (iii) **successor option recorded**: a root-owned map file (ownership, not mode, excludes the agents' uid) closes the bash path entirely and is the designated replacement if this residual is ever withdrawn from acceptance — priced today under "operator file" at 3.15 because it was scored with nea-ownership; under root-ownership its risk rises and it contends with ENV. The Emperor owns the acceptance of T2 (§10) either way.

**3.6 The fail-loud parser's reject list (D-4 ratified with three amendments).** Rules 1, 3 (`object_pairs_hook`), 4/5 (empty≠unset diagnostically, identical behaviourally), 6 (one approver, many channels), 8 (≤64), 9 (no offline existence validation — **verified**: no Discord channel registry exists in `daemon/sources/`; only `allowed_channels` ingestion filtering, which is a different threat model), 10 (non-DM only) — all **confirmed**. Amendments: **(a)** rule 2 tightens to `^[1-9][0-9]{0,31}$` (reject leading zeros and `0` — the adapter's `str()` ids can never carry them; kills one never-match typo class); **(b)** rule 4's "configured but empty" diagnostic **does not exist** — no log line is specified (see §8, probe evidence); **(c)** D-4's justification is corrected: the repair-to-fallback machinery is the **tool-layer resolver** `_resolve_knobs` (`prompt_assent.py:384-455`), not the pydantic class (`daemon/config.py:3246-3301` — plain fields), and it is **not silent** — it warns (`:445-453`) but **echoes the raw value with `%r`** (`:449`), which for a map would log the operator's configuration and violate S-6. That value-echo is a *second, stronger* reason for the dedicated parser; cite it.

**3.7 Restart vs call-time — reconciled, no conflict.** "Call-time read" = the resolver reads `os.environ` per tool call (the kill-switch idiom, `prompt_assent.py:404`, `:466`); "restart-bound" = `os.environ` itself changes only at process start (no runtime writer exists). Both true; the operator procedure is edit `.env` → restart → probe leg 3. **One pin required:** the resolver must read `os.environ` **directly**, never via a pydantic-settings dotenv source — `PromptAssentConfig` is instantiated per call (`:431`) and a dotenv source would **hot-reload the map without a restart**, silently breaking the restart-bound story. D-2.1's "once per tool call, passed to both gates" is corrected to a **cached-lazy resolver** (parse on first non-DM use, memoised per call; a fresh call re-resolves — which is what makes the A-9 re-designation window visible by design).

---

## 4. Item C — predicate ownership + ordering

**Verdict: RATIFY-WITH-CHANGES.**

**4.1 What exists today.** `is_private_assent_channel` (`mint_context.py:29-50`) is a pure function of the nested meta dict with exactly three legs: empty→False (`:46-47`), `is_dm is True` (`:48`, identity check), `is_group_dm is False` (`:48-49`, identity check — `"yes"`/`1` are not private). It never looks at channel/user/guild/thread/the map. **There is no gate-level group-DM / absent-discriminator discriminator today** — the mint gate is one combined condition (`prompt_assent.py:1709`) and the service predicate collapses every non-private shape into one code (`prompt_version_service.py:475`). Phase 1.5 must *split* that collapse; the split is the risk surface.

**4.2 The ordering is convention, not code shape — and no existing pin can detect a breach.** The ordering lives in pseudocode (`refusal-cell-matrix.md:22-34`) and one task row. Per-pin verification: the group-DM pin (`test_prompt_assent_mint_gate.py:84-90`), the absent-discriminator pin (`:92-100`), the guild pin (`:102-115`), the DM pin (`:117-124`), the classifier pin (`test_prompt_assent_group_dm_discriminator.py:113-174` — never touches a gate) and the user-binding pins are **all outcome-only**; an implementation that checks the map *before* the group-DM discriminator ("private **or** designated" — the most natural implementation, named by the plan's own R-2) keeps **every one of them green** while a group DM listed in the map mints. The map validator cannot prevent it: a group-DM channel id is a digit string like any other (D-1.7 admits it).

**4.3 The missing trust-boundary pin — M-15 (blocking).** M-3 uses an absent map, M-11 an invalid one; **no cell covers "group DM whose channel id IS in a VALID map, by the pinned approver ⇒ REFUSE `mint-requires-private-channel`"**. Worse: an implementation that degrades an invalid map to `mint-requires-private-channel` keeps M-3 *and* M-11 green while the group DM mints. **Ruled: add M-15** to the matrix and `phase3-plan.md` T3.2, with an in-file counter-mutation (reorder the legs ⇒ M-15 reddens).

**4.4 Ruling — one classification function, called by both gates.** As planned, P2/T2.2 (mint, in `prompt_assent.py`) and P2/T2.3 (approve, in `prompt_version_service.py`) would each implement legs 2/3 — **two implementations of the same fail-closed ordering**. **Ruled: ONE classification function in `mint_context.py` performs the whole leg ordering (DM → group-DM → absent-discriminator → map) and is the single call for both gates**; the mint gate keeps its DM short-circuit and calls it inside the `not private` arm; the service predicate's designated branch lives inside its `not private` arm. Precedent: `classify_user_origin` (`upgrade_journal.py:4116-4166`) — ordered legs, explicit "checked BEFORE" rationale, fail-closed default.

**4.5 The service predicate's signature (under-specified — ruling).** It needs the map **and** the approving human's id. **Ruled: the id comes from `context.user_id` (`prompt_assent.py:678`, the single identity parse site `:621-633`) — never a second read of `discord_meta["user_id"]`** (a second parse site contradicts the single-home discipline). `context.is_group_dm` (`:689`) is currently a **dead slot** (written, read nowhere); the classification function reads the raw meta dict — either delete the slot or make it the single carrier, but decide it in the plan.

**4.6 Remaining structural guards.** (i) The runtime resolver-raises pin (§2.5); (ii) the absent-identity leg inside the designated branch (§6.3); (iii) `daemon/repositories/prompt_assent/__init__.py` joins the not-touched list — the service imports by submodule path today (`prompt_version_service.py:470-472`); no re-export; (iv) name the future-edit hazard in the plan: the plausible "simplification" is exactly R-2's wording — the counter-mutation in M-15's pin is the tripwire.

---

## 5. Item D — the trust anchor

**Verdict: RATIFY-WITH-CHANGES.**

**5.1 The anchor is sound (verified end-to-end).** `adapter.py:1089` `str(getattr(author,"id",""))` → `:1150` `metadata={"discord": discord_meta}` → `registry.py:1324` → `instance_messaging.py:2387` `message_metadata` → the queue row → `manager.py:11530` `stamp_user_origin_window` → `:6126-6131` the per-instance window → `prompt_assent.py:868-874` reads the row by `window["message_id"]` → `:621-633` reads **only** the nested `metadata["discord"]`. The top-level decoy is pinned (`test_prompt_assent_user_binding.py:122` → `extraction-incomplete`). **No other producer of a `discord`-nested key exists** (Slack/Telegram nest under their own keys); the HTTP path carries **no** metadata (`daemon/models/message.py:78-103`) and stores `{}` — a raw API message **cannot** fabricate the identity. An unregistered source id fails closed (`upgrade_journal.py:4147-4148`). **Type safety:** both sides are `str` by construction (`adapter.py:1083`, `:1089`); comparisons elsewhere are `str()`-normalized string equality (`prompt_version_service.py:416-424`, `:449-456`); both mismatch directions fail closed. **The tool schema carries no identity field** — pinned by exact set equality (`test_prompt_assent_user_binding.py:167-171`). **The room + pinned id together authenticate**: the mint compares the map value for *that* channel against the turning human (cross-pin cell M-14 is the right cell); a stranger in the court cannot mint.

**5.2 The approve-side answer (the commission's spotlight), stated explicitly.** 4e (`prompt_assent.py:1998-2016`) **is** evaluated before 4f (`:2018-2026`) — verified. With two designated channels pinning different approvers (C1→U1, C2→U2): U2 approving U1's C1 token **in C2** is refused by 4d `token-channel-mismatch` (`prompt_version_service.py:416-424`, budget burns — correctly: a real attempt against a live token); **in C1** it is refused by 4e `token-approver-mismatch` (`:433-457`, budget burns). Cross-surface replay (DM⇄channel) is refused by 4d both ways. **The approve leg DOES carry its own pinned-approver check** — 4f's map lookup, which is what makes the A-9 re-designation case fail closed. **The plan does NOT under-cover the approve side semantically; it under-*states* it**: D-6's heading ("with **no new approve-side code**") and body ("only ever ADDS a pass path"; "the only new approve-side outcome is the operator-fault case") contradict D-8's own table (`decisions.md:351-352`), matrix §1.2 (`:57-61`) and cells A-9/A-10. **D-6 must be amended** (§11 row 3) — an implementer reading only the heading builds the wrong 4f.

**5.3 Two residuals to carry.** (i) **The re-designation token state:** after the §6 fix, an A-9 refusal consumes nothing and does not revoke — but the minted token stays live until its 60-minute TTL, and if the operator re-pins the channel back to the original approver, the old token becomes usable again (4e passes, 4f passes). Bounded (the approving human must still be the mint-witnessed human), but the runbook should say it. (ii) **The interaction path** (`adapter.py:1227-1254`) builds `discord_meta` **without `is_group_dm`**; today harmless (`/new` never queues — `registry.py:1135-1147`; absent discriminator fails closed), but Phase 1.5 makes any `is_dm is False` context designation-eligible — **ruled: the classification function accepts only `text`-type turns** (the row carries `type=msg_type`, `instance_messaging.py:2368`).

---

## 6. Item E — the two new codes and the budget

**Verdict: RATIFY-WITH-CHANGES.**

**6.1 What already holds by code structure.** The K-9 in-RAM hourly budget (default 10/hour, per `(relay_instance_id, hour-bucket)`, `prompt_assent.py:274-326`, pinned at `test_prompt_assent_registration.py:329`) burns only `code in MISMATCH_CODES` (`:2107`) — the two new codes outside that set cannot touch it on any path. Stronger than the plan claims: **the mint path never consults the budget at all** — `_mint_refusal` (`:1657-1666`) bypasses `_mismatch_aware_refusal` (`:2089`) for every code. State the stronger fact so no future change adds a mint-side budget leg.

**6.2 The defect — the durable counter is not class-gated (blocking; four independent corroborations).** The 4f site calls `_record_mismatch(token_repo, token_row)` **unconditionally** (`:2021`) *before* the code's class is examined; `_record_mismatch` (`:1174-1209`) increments the durable per-token counter and **revokes the live token at threshold 5** (`:252`/`:255`; 4g `:2028-2036`). Under the plan's own §1.2 algorithm (4f emits both new codes), five re-designation refusals **revoke the Emperor's token** — contradicting A-9/A-10 ("Token burn? no") and AC7. **T2.5's own text makes it worse** — "the four call sites are unchanged in their behaviour" **and** "the two new codes reach no `_record_mismatch` call" are mutually exclusive (the 4f site is `:2021`). **Ruled: the 4f site splits** — it records only on the mismatch-class branch (`code in MISMATCH_CODES`), refusing the two new codes *before* the recording call; the three genuine mismatch sites (`:1970`, `:1991`, `:2011`) are untouched. T2.5/D-2.7/§1.3 rewritten accordingly (§11 row 1), and **Phase 2 owns the edit as a task** (today no task owns it; T3.5 would catch it as an unplanned mid-Phase-3 change).

**6.3 The M-8 contradiction (third corroborated instance).** Cell M-8 expects `extraction-incomplete` for an absent user id, but §1.1's ordering reaches `user_id != map[...]` first ⇒ `approver-not-pinned`; as written **M-8 is unreachable**. **Ruled: M-8 is right** — the live DM path already yields `extraction-incomplete` for an absent id (`test_prompt_assent_lane.py:587`) and approve-4e does too (`:1998-2006`); `approver-not-pinned` means *a known human who is not pinned*, not *no human at all*. Insert an explicit absent-identity leg **inside the designated branch, after "channel in map"** (so undesignated-guild behaviour stays live-identical), emitting the existing `extraction-incomplete`; renumber D-6's legs accordingly; add "the absent-identity leg precedes the pinned comparison" to the ordering-consequences list.

**6.4 Census and placed codes.** The census pin is exact set equality (`test_prompt_assent_registration.py:299-305`, 27 codes today → 29) — discriminating; both sets must gain the two codes in the **same commit** as `prompt_assent.py:195` (the envelope sanitiser at `:2060` drops unknown codes). **`MISMATCH_CODES` is pinned only by membership** (`test_prompt_assent_tool_refusals.py:105-119`) — the plan's T2.7(iii) structural pin is confirmed necessary; **ruled: make it exact equality with an in-file counter-mutation**. **The placed-codes ambiguity is ruled:** the census's `EXPECTED_PLACED_CODES` (the parent's 8) **stays at 8**; the two new codes ship as a **new** placed set (`EXPECTED_PHASE1_5_PLACED_CODES == {approver-not-pinned, designation-config-invalid}`) with its own equality pin, subset pin (`<= REFUSAL_TAXONOMY`), and a referenced-in-module loop mirroring the parent's. Names: **ratified** (`approver-not-pinned`, `designation-config-invalid` — house-shaped, path-neutral); the side ambiguity is resolved by **side-specific detail text** (mint: "this human is not the pinned approver of this room"; approve: "the mint-witnessed human is no longer this room's current pinned approver") at zero taxonomy cost; the INFO line already carries `action=mint|approve` (`:508-543`) so the log disambiguates.

**6.5 The matrix's dangling cell references** ("cell 2c"/"cell 3a" at `decisions.md:351-352` and `refusal-cell-matrix.md:152-153` — no such cells exist) resolve to **M-7** (approver-not-pinned) and **M-9** (designation-config-invalid); fix in place (§11 row 24).

---

## 7. Item F — the open questions, ruled

| # | Question | **Ruling** | Consequence / reasoning |
|---|---|---|---|
| Q-1 | Thread inheritance | **NO — D-9 ruled** | Exact channel-id match only; a thread may be named explicitly (M-12/M-13). A private thread inside a public channel must not inherit a grant its membership doesn't share; ephemeral threads make the surface drift. `adapter.py:935` resolves threads→parent for **ingestion filtering only** — never cite it as authority precedent. The escape hatch (name the thread's own id) exists today at zero code. |
| Q-2 | Multi-approver per channel | **NO — BLESSED-1 upheld; the map shape is the right enforcement point** | Unrepresentable beats forbidden-but-representable (two enforcement sites to audit). A future list value fails `^[1-9][0-9]{0,31}$` ⇒ `designation-config-invalid` — a self-diagnosing migration. |
| Q-3 | The artifact_class gate | **UNCHANGED** | Channel-independent, fires before M-4c (actual anchor `:1681-1686`; the plan's `:1685-1692` is stale). The extension changes *where* a mint may happen, never *what*. |
| Q-4 | The env surface vs restart | **YES — D-3 ruled** | The DB tier is *also* restart-bound (`settings_tools.py:139`, `:379-381`) **and** agent-writable; env strictly dominates. |
| Q-5 | A DB secondary tier | **NO — ruled, with the stronger reason** | The ladder is DB > env (`settings_service.py:10-11`): even a "read-only mirror" tier would put the agent-writable tier in the resolution chain; a pure mirror adds permanent staleness/precedence ambiguity. Refuse both forms. |
| — | Restart vs call-time | **RECONCILED (§3.7)** | Both true; pin the direct `os.environ` read (never a dotenv source — hot-reload hazard); cached-lazy resolver per §4. |
| — | Ledger Q-numbering | **Flagged** | The commission's Q-1/Q-2/Q-3/Q-5 map to ledger Q-1/Q-2/Q-3/Q-5 but the commission's "Q-5 a DB secondary tier" is ledger Q-5 ✓ while **ledger Q-4 is plan-raised** (env surface) — any external "commission Q-4" mis-maps. Part C splits into genuinely-open (Q-1→D-9, Q-4→D-3) vs settled-for-the-record (Q-2, Q-3, Q-5) with a commission-ref column (§11 row 12). |

---

## 8. Item G — the five-leg adapted K-3 probe

**Verdict: RATIFY-WITH-CHANGES — the leg set is right; the shape below is binding.**

**8.1 Two-phase, not one list (mandatory).** Leg 4's expected code (`mint-requires-private-channel`) is **identical** to what a mistyped map key produces in the designated channel — single-phase cannot separate "map says no" from "id typo". **Phase A (map unset):** leg 2 (group DM) → leg 4A (the *designated* channel with the map unset — must refuse) → leg 1 (DM). **Phase B (map set):** leg 3 (designated channel — must mint) → leg 5 (non-pinned user). Only the A/B pair on the *same channel id* isolates the variable.

**8.2 Three armed states, not one bit.** **ARMED-TENTATIVE** (`ENSEMBLE_PROMPT_ASSENT_ENABLED=1` + restart — the probe's precondition; a mint alone grants nothing; the exposure is the room disclosure, §10 NT-1) → **ARMED-OPERATIONAL** (all five outcomes recorded + the Emperor declares reliance) → **DISARMED** (unset + restart ⇒ `lane-disabled`). The probe runs inside ARMED-TENTATIVE and **ends with an explicit disarm-or-ratify step**. The notes must say "**the lane must not be RELIED UPON until the five outcomes are recorded**" — "arming blocked until the probe" is circular as written (the probe requires arming).

**8.3 Per-leg evidence (summary; the full table lives in the build lane's ceremony notes).** Every leg's PASS = the exact code **plus** the token-row shape **plus** the INFO line, captured together: leg 1 → token row `mint_is_dm=1`, `mint_user_id`=probe account, `status='live'`, `ttl_expires_at`=+60min, `attempts=0`; leg 2 → `mint-requires-private-channel`, **no** token row, **no** audit row; leg 3 → `mint_is_dm=0`, `mint_channel_id` == the map key, `mint_user_id` == the map value (a `mint_is_dm=0` row with the **wrong** channel = minted in a thread or wrong room — FAIL); leg 4 → the Phase-A refusal in the same channel; leg 5 → `approver-not-pinned` **with `flags=mismatch=0`** (proves no burn). **Code-family triage is the operator's first move**: `mint-requires-private-channel` is emitted *only* at the M-4c privacy gate (`:1711`) — its presence proves the window was live and the item pending (a window-family code is an availability miss, not a leg failure); `designation-config-invalid` ⇒ configuration failure (the WARNING names every offending key); `mint-live-token-exists` ⇒ cleanup, not probe failure.

**8.4 Preconditions the plan omits.** ≥2 pending CORE `prompt_versions` (Emperor-created — the artifact path is human-session-only) and a **different subject item per minting leg** (a live token blocks re-mint, `:1700-1705`; `force=true` supersedes); operator READ access to `prompt_assent_tokens` and the message rows' `metadata["discord"]`; Discord Developer Mode ON (ids are copy-paste digits). **The second human must be a second ACCOUNT** — a second device shares the `user_id` and degenerates leg 5 into leg 3; the Emperor's own alt is the *strongest* choice (proves the pin binds the account, not the person). **An unrun leg 2/5 is NOT a pass** — arming records the gap as an explicit Emperor acceptance (the multi-occupant discriminator then stays unit-proven only, which is exactly the class K-3 exists for).

**8.5 Hygiene and fail-safe.** Order: refusal-only legs first, minting legs last, one subject item each. The mint response posts the **token + canonical render into the room** (`:1249-1254`) — single-occupant channel for leg 3; consume or TTL-expire the token (60 min) before reuse; **never** paste a live token into a second channel; a bystander's reply between the Emperor's turn and the tool call re-points the window (§10 NT-5) — re-run, do not adjudicate. **The map-parse log line (empty / unset / invalid / N entries) is mandatory probe evidence** — it is the only discriminator between "map says no" and "map is broken/empty", and D-4 rule 4's promised diagnostic currently has no specified log line at all.

**8.6 Where the adapted wording lives (ownership resolved).** A **NEW** `ceremony-notes.md` in **this** directory (the parent set stays frozen, D-1/O-11), superseding the parent's DM-only K-3 text (`../prompt-assent/ceremony-notes.md:236-244`, `:308-309`) from the new side; the **runbook** `docs/runbooks/prompt-assent.md` §"The K-3 live confirmation" (`:160-185`), taxonomy list (`:231`) and troubleshooting rows (`:212`) are **this lane's Phase-4 file (F-11)** — *not* the build lane's (D-1 as written assigns both to the build lane; two owners for one file — §11 row 10). Leg 1 **cannot** stand in for the byte-identity claim: outcome comparison suffices for the live leg; the byte/behaviour claim is carried by INVARIANT-DM's pins (§2.5) and the ceremony record must say so. The T3.5 re-stamp (`agents/ari/tools_note.md`; `grep -c memoir_about` = 0 today) remains the first beneficiary **once armed**, human-session-only, **not** a lane deliverable.

---

## 9. Item H — staging semantics (v0.29.19; v0.29.18 LEFT STANDING)

**Verdict: RATIFY-WITH-CHANGES — D-10 ruled (LEFT STANDING is achievable).**

**9.1 Slot.** `v0.29.19` verified free (remote `ls-remote 'v0.29.19*'` empty; local tag list empty; remote max `v0.29.18`); the annotated-tag peel applied (`v0.29.18` tag object `a4d7233f2` → commit `df4a92732`); `merge-base --is-ancestor v0.29.18^{commit} HEAD` → exit 0. **Build-day rule:** the version is re-derived at bump time (no reservation) and `stage.sh:139-151` requires `git describe --tags --exact-match HEAD == VERSION` — **re-verify the slot immediately before `git tag -a`**, not at the start of the day (three lanes are converging on adjacent slots). On collision: re-bump to the next free patch slot and record it; **never re-cut an existing tag**; push **by name**, never `--tags`; a remote tag is never deleted.

**9.2 Coupling — exactly one mechanism.** Per-mechanism audit with script lines: staging ceremony (`stage.sh:139-151`), release-dir layout (`:390`, per-version path), version bump (`scripts/bump_version.py` — 4 files + CHANGELOG), 4-way sha (`:597`, `:790`, `:227`), `rollback_safe` derivation (`:328-352` — whole-history grep, consults no prior tag), taxonomy census/pack refresh (**nothing under `scripts/upgrade/` reads any of it**), `journal_init`/`state.json` (early-return when `state.json` exists) — **NONE reference v0.29.18's presence or absence**. The one coupling: **`retention_evict`** (`lib.sh:6857-6917`, `RETENTION_KEEP=3` at `lib.sh:375`), called only from `promote.sh:771` and `:927` — **`stage.sh` never evicts**.

**9.3 Eviction math (the plan's D-10 understates).** Staging takes the count 5→6 and **queues nothing**. At the first v0.29.19 promote: evict 3 (v0.29.7, v0.29.13, v0.29.17 — the three oldest non-pinned) ⇒ **v0.29.18 survives**. It becomes the candidate at the **third** promote (after v0.29.20 and v0.29.21) — it survives **exactly two further promotes, protected by nothing** (`retention_evict` pins only `current`/`previous`; a script pin is an upgrade-machinery change with its own floor/stamp bump — out of this lane's scope). **Ruled:** (i) operator rule in the ceremony note — no other promote until the designated-channel decision is made; (ii) the note carries the two-promote survival math and "if evicted, re-stage from `df4a92732` — the tags survive, the option is recoverable at one build+stage cycle"; (iii) D-10's "v0.29.13 is the eviction candidate" is true for the first promote only and **is not a protection** — say so.

**9.4 Two build-day traps the plan omits.** (i) `stage.sh:328-352` greps the **whole** migration history for `DROP TABLE|DROP COLUMN` — it matches (`20250326_000000_initial_schema.sql` etc.) ⇒ **`stage.sh` exits 78 unless `ENSEMBLE_ROLLBACK_SAFE=1` is passed explicitly** (v0.29.15's staging did exactly that); record `rollback_safe=true, source=explicit`. (ii) `bump_version.py` commits with the subject `! New version X`, violating the house `<type>:<desc>` convention — use it as a **file editor** and commit `chore(release): bump version to 0.29.19` (as v0.29.18 did: `d317043aa`).

**9.5 Ordinals and census.** Two refusal codes + one new module + new tests move **none** of the ceremony-asserted ordinals: migration head `20261110_221500` / 128 files ✓ (D-5 holds — no table, no column, no migration), prompt baseline 181 ✓ (the lane touches no `agents/` file), scripts floor/rev `2147c0298` ✓, privileged category / tool vocab / artifact counts ✓. The refusal-taxonomy count 27→29 is a **test-pinned** count, not a ceremony ordinal. The pack file list **must** gain `tests/unit/test_prompt_assent_designated_channel.py` (or the lane's proof never runs in the house gate) and **only** the phase-3 pack — the tree's *other* registered prompt-assent pack (`test/packs/prompt_assent_matrix_test.sh`, `PACKS.md:3480`) is explicitly out of scope and must not gain it; the PACKS.md row-refresh names both rows. **None of it is release-ceremony load-bearing.**

**9.6 The v0.29.19 deliverable** (unchanged in kind from v0.29.18): merged feature; house-subject bump commit; `## [0.29.19]` CHANGELOG section; annotated tag at exact HEAD, pushed by name; `releases/v0.29.19/` with the standard layout; `dist/ensemble-prod` + provenance sidecar; **4-way sha**; the new ceremony notes + the adapted runbook section + the map documentation. **Differs from a v0.29.18 staging because v0.29.18 stands:** release count 6 and a 3-dir first-promote eviction; the v0.29.18 protection statement + survival math; an explicit "the DM-only fallback's release dir exists" note (the inverse of v0.29.17's disposal note); **no disposal step at all**; `rollback_safe` source=explicit; the `armed by` line names the designated-channel probe; all head/floor/baseline ordinals identical. **No promote, no arming, by this lane.** Note: the probe/runbook live **outside the release payload** (`docs/`, `.agents/` are not staged) — a promoted binary carries no copy of its own probe instructions; the ceremony note must point at the repo paths.

---

## 10. Threat model — consolidated NEW threats (the plan missed all of these)

| # | Sev | Threat | The break | Handling (ruled) |
|---|---|---|---|---|
| **NT-1** | 🔴 | **Token + full prompt-diff posted into a multi-occupant room.** The mint response carries the grouped token **and the canonical render** (`prompt_assent.py:1249`, `:1252-1254`); the relay posts it into the channel — disclosure to every occupant and any reading bot, and the bystander's relayed "garbage token" is the **real** token ⇒ durable counter to revoke (5), budget to lockout (10/h) — a lockout DoS on the legitimate Emperor. The plan's content-free discipline (S-6) governs refusals and logs, **never the mint response**. | Options: (i) DM-deliver the token when the context is designated; (ii) redact the render from the in-room response (fetch via DM); (iii) accept-and-record with the Emperor as owner, given his ruling treats the channel as private. **This is an Emperor decision (§13)** — the plan currently makes it implicitly (iii) without recording it. Probe hygiene regardless (§8.5). |
| **NT-2** | 🔴 | **The "operator-only" map is agent-reachable without any restart tool.** Ari holds `bash` (`agents/ari/meta.json:14`) + `system_restart` (`:31`) + `service` (`:33`); `.env` is nea-owned (mode 600 does not separate uids); restarts are provenance-gated (any genuine user turn, direct or inherited) and the launcher re-exports `.env` on crash-restart. Chain: write env var → trigger restart → map armed. Not a rubber stamp (mint still needs the pinned human's turn) but **un-vetted channel expansion** by the minting agent, invisible to the settings audit trail. | S-1 rewritten (§3.4); compensating controls (§3.5): boot-time fingerprint log line + re-probe rule; root-owned file recorded as the successor. Emperor accepts or withdraws (§13). |
| **NT-3** | 🔴 | **The group-DM trust boundary is unpinned.** No cell tests "group DM whose channel id IS in a valid map, by the pinned approver"; every existing pin is outcome-only and stays green under a map-first ordering while the group DM mints (§4.2-4.3). | M-15 added with counter-mutation; one classification function (§4.4); runtime resolver-raises pin (§2.5). |
| **NT-4** | 🔴 | **The 4f durable-counter burn.** As specified, five approve-side new-code refusals revoke the Emperor's live token (`:2021` unconditional `_record_mismatch`, threshold 5) — the plan's A-9/A-10 cells are unreachable as written (§6.2). | The 4f split, owned by Phase 2 (§11 row 1). |
| **NT-5** | 🟡 | **Last-speaker-wins window.** The window is per-instance and re-armed on every non-silent dispatch (`manager.py:6126`, `:11530`); a guild channel is one instance for all humans (`adapter.py:1041-1042`, `:1219`) ⇒ a bystander's reply between the Emperor's turn and the tool call re-points the window; the Emperor's own attempt then refuses `approver-not-pinned`. Fail-closed, but a cheap continuous DoS on the lane. | Runbook rule ("the Emperor must be the last speaker; a bystander's reply within the 600 s freshness bound invalidates the attempt"); probe legs 3/5 carry the discriminator; *future* hardening (not this lane): a per-user window or a "the window row must be the pinned approver's" guard for the designated path. |
| **NT-6** | 🟡 | **M-8 unreachable / absent-id miscode** (§6.3). | The absent-identity leg inside the designated branch. |
| **NT-7** | 🟡 | **Ambient/pack env leakage.** The pack's per-variable scrub list omits the new var (`test/packs/prompt_assent_phase3_unit_test.sh:50-54`) — an ambient operator map leaks into the house gate (exactly R-9's vector); in-process, the default rig's scrub is load-bearing for "existing pins stay green" but is unpinned. | Pack gains `-u ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS` (per-variable — the house glob lesson); pin the default-rig scrub; a `tests/conftest.py` autouse `delenv` is the fallback **only if** any existing pin bypasses the rig (the two passes disagree — the rig claim was verified against the fixture; decide by pinning the scrub, not by trusting either reading). |
| **NT-8** | 🟡 | **One code, two operator mistakes.** A typo'd pinned id (leading zero, wrong length) and a stranger in the room both yield `approver-not-pinned`; a **bot** id pinned makes the room permanently mint-dead with the same code; a DM/group-DM channel id listed in a valid map is **silently ignored** (the legs precede the map) with no diagnostic. | Parser rejects leading zeros (§3.6a); side-specific detail text carries "no pinned approver matched any turn" vs "you are not the pinned approver"; new matrix cells for the bot-pin and the silently-ignored-DM-id shapes; the map-parse log line (§8.5) covers the empty/unset case. |
| **NT-9** | 🟡 | **`ens-env` exposes the map.** Any `ens-env`-holding agent (e.g. `agents/worker/meta.json:32`) can read the live value verbatim — S-6's content-free claim does not govern that tool. | Accept and record (ids are quasi-public, the *policy* is the secret); or add the var to the tool's redaction list. Decide in the plan (§13, minor). |
| **NT-10** | 🟡 | **Parent reconciliation gap.** This lane amends the parent's frozen AR-1 mint binding and its "complete" 27-code taxonomy with **no recorded amendment** anywhere in the set, and never cites the parent's own §8.2 hook that this commission satisfies. | New **D-13** row recording the amendment + the census docstring re-wording (§11 row 8). |
| **NT-11** | 🟢 | Assorted: interaction-path meta lacks `is_group_dm` (§5.3 — the `text`-turn rule); the `private: True` INFO flag (`:1818-1822`) becomes a lie for designated mints (add `private_equivalent` or document); `mint_is_dm=0` proof-by-construction is a **convention** (column default 0 — keep it true explicitly); the mint path never consults the budget for *any* code (state the stronger fact); the probe/runbook live outside the release payload; M-2 has no test anywhere (amend AC2: M-2 is the conjunction of M-1 and A-2 — or add the one-line variant cell); id-namespace collisions S/P/A each mean two things in one set. | §11 rows 13, 14, 25, 26, 27, 28. |

Threats the plan already covers — **confirmed accurate** (with corrected residuals): S-1/D-3 core reasoning (settings tier agent-writable; registry-gated; only ari/leader hold the category), S-2 ordering *as a design property*, S-3 stranger-in-room (daemon-side, per-channel), S-4 cross-surface replay (4d/4e), S-5 typed-invalid config, S-6 *for refusals and logs*, S-7/R-3 *mint side*, R-5 duplicate-key `object_pairs_hook`, R-9 hermetic rig *concept*, R-12/D-9 threads, R-13 one predicate owner, D-7 kill-switch precedence (`:1436-1445` before M-4c), D-5 no-schema-change (as to the current tree).

---

## 11. MUST CHANGE BEFORE COUNCIL (flat list — severity ordered; rows 1-6 are blocking-class)

**Blocking (the council should not see the set without these folded):**
1. **The 4f split** — rewrite T2.5 + D-2.7 + §1.3 (`refusal-cell-matrix.md:70-77`): the `:2021` site records **only** on the mismatch-class branch; the two new codes refuse **before** `_record_mismatch`; the edit is a Phase-2 task (no task owns it today). A-9/A-10 stay "no burn" only after this.
2. **Amend the byte-identity ledger rows** — BLESSED-3 (`decisions.md:68-74`) and D-7 (`:328`) to the behaviour scope, character-for-character with `refusal-cell-matrix.md:138-143`; D-4 rule 10's "'DM path byte-identical' proof" → "'DM path unchanged' proof".
3. **Amend D-6** (heading `:253`, `:288-289`, `:300-303`) and D-8 prose `:362-363` — the approve leg **does** emit two new codes; "no new approve-side code"/"only ever ADDS a pass path"/"only the operator-fault case" are false against D-8's own table, §1.2 and A-9/A-10.
4. **Add M-15** (group DM + valid map naming that channel + the pinned user ⇒ REFUSE) to the matrix and T3.2, with an in-file counter-mutation — the trust boundary is otherwise unpinnable.
5. **State INVARIANT-DM verbatim** (§2.5) + the runtime resolver-raises pin (DM mint, DM approve, lane-disabled) replacing/augmenting the source-shape P-2.
6. **Insert the absent-identity leg** (extraction-incomplete) inside the designated branch (after "channel in map"), renumber D-6's legs, add the ordering-consequence line — makes M-8 reachable and matches the DM/approve-4e behaviour.

**Plan-text defects (fold before council; mechanical):**
7. One classification function in `mint_context.py` owns all legs; both gates call it; the service predicate's designated branch sits inside the `not private` arm; mint gate keeps its DM short-circuit (kills the two-implementation gap in T2.2/T2.3).
8. **Add D-13** — the parent-reconciliation row (AR-1 mint binding + §3 taxonomy amended by this lane; census docstring re-worded; the parent's §8.2 hook cited as satisfied).
9. Envelope ruling applied (§2.4): T2.8 confines the new wording to the two new codes' messages; the existing detail string stays byte-identical; P-3/§4/:142 stand as written.
10. Ownership split: the **runbook is this lane's F-11 (Phase 4 writes it)**; the **build lane owns only the v0.29.19 ceremony notes** — amend D-1 (`:101-104`) and O-11; T4.2-T4.6 already point the right way.
11. D-2.1 → cached-lazy resolver (§3.7); delete the "(the mint leg and the approve leg must never see different maps)" parenthetical (`phase2-plan.md:86`) — false by design (A-9 requires fresh-call re-resolution).
12. Part C split: C1 genuinely-open (Q-1→D-9, Q-4→D-3) vs C2 settled-for-record (Q-2 BLESSED-1, Q-3 O-5/D-5, Q-5 D-3); drop C2's "what changes" column; add a commission-ref column (the Q-numbering mis-map is real).
13. Rename matrix §4 proofs to **PR-1..PR-4** (4 rows + citation sites `plan-overview.md:258`, `phase2-plan.md:35`, `phase3-plan.md:30`); rename plan-overview §3 ids to **I-1..I-9** and §9 acceptance to **ACC-1..ACC-3** (S/P/A each currently mean two things).
14. AC2 amended: M-2 is the conjunction of M-1 + A-2 (or add the one-line variant cell); A-1..A-3 verified covered by existing pins.
15. D-3.6: only the **phase-3 pack** gains the new test file; `prompt_assent_matrix_test.sh` explicitly out of scope; F-10 names both PACKS.md rows.
16. T2.6 extended with the placed-set pins (§6.4): `EXPECTED_PHASE1_5_PLACED_CODES == {two codes}`, subset-of-taxonomy, referenced-in-module loop; T2.7(iii) is an **equality** pin with counter-mutation on `MISMATCH_CODES`.
17. SC-10/P-4 re-worded: the FE *methods* are byte-identical (method-scoped proof); `prompt_version_service.py` **is** edited (F-4) — "no FE file in the diff" is false as stated.
18. P-2 lists **six** pin files (add `test_prompt_assent_lane.py`) and says "none edited — new cells live in the new file" (matches D-3.1/T3.7/AC7; removes the internal contradiction).
19. Add **O-13** (committing map values / real approver ids is out of scope — the runbook documents the *name*, never a value); D-3 `:180` cites it.
20. Phase-2 AC12 / Phase-1 AC7+AC8 / Phase-3 AC10 orphans: add owning task rows or mark them phase-level gates.
21. Parser rule 2 → `^[1-9][0-9]{0,31}$`; D-4's justification corrected (tool-layer resolver, warns-but-echoes-raw-value — the S-6-violating echo is the second reason); D-3 rejected-(b) corrected to name the HTTP `settings:write` surface and the auth-off bash+curl exposure.
22. S-1(iii) rewritten per §3.4 (Ari-triggerable, provenance-gated restart; the true two-part boundary); T4.4's "no agent can designate" statement must not lean on the file-tool deny list.
23. Probe: two-phase shape, ARMED-TENTATIVE/OPERATIONAL/DISARMED + disarm-or-ratify step, second-**account** rule, unrun-leg-not-a-pass wording, single-occupant-channel + token-hygiene rules, the map-parse log line as evidence, ≥2 subject items with one-per-minting-leg (§8).
24. Staging: `ENSEMBLE_ROLLBACK_SAFE=1` in the argv + `source=explicit`; house-subject bump commit; slot re-verification immediately before `git tag -a`; the v0.29.18 protection statement + two-promote survival math in the ceremony note; the "probe/runbook live outside the payload" note (§9).
25. Dangling ids fixed: "cell 2c/3a" → M-7/M-9 (D-6 leg 4c / leg 4a); `risks.md:45` "D-1.5/D-1.6" and `phase2-plan.md:38` "D-1.8" → "phase1-plan.md …"; `doctrine.md`/`risks.md` "AR-1 leg n" → cite `../prompt-assent/architecture-recommendation.md:63`; `decisions.md:219,:335` bare `ceremony-notes.md` → `../prompt-assent/ceremony-notes.md:246-248`/`:308-309`; the freshness-flake cite `:250-253` → `:256-259`.
26. The `private` INFO flag semantics decided (`:1818-1822` — add `private_equivalent` or document the lie); `context.is_group_dm` dead-slot decision; `daemon/repositories/prompt_assent/__init__.py` in the not-touched list (no re-export); the `text`-turn rule for the classification function.
27. Matrix §5 "the other 22 codes" → **23** (27 live − 4 named existing = 23; post-lane 29 − 6 named = 23).
28. `phase4-plan.md:79` "twelve files" → **thirteen** rows (F-1, F-1b, F-2…F-12; F-8 conditional); `tests/conftest.py` **not** added to the binding list (no phase task implies it — the scrub lives in the rig; pin the rig scrub instead, §NT-7).
29. Planning-set manifest row gains this artefact (11th file, distinct from the parent's same-named file — qualify the link).
30. Stale anchors corrected: `_record_mismatch` `:1174-1209`; artifact_class gate `:1681-1886`→`1681-1686`; `PromptAssentConfig` ends `:463`; `adapter.py` channel_id `:1083` / user_id `:1089` (not 1084/1090); `research-findings.md:82` `:1085/:1064/:1076`.
31. Pack env-hygiene note gains the per-variable scrub (`-u ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS`) — never a glob.
32. `risks.md` R-17's mitigation cite needs a 30-second re-check (the sweep's counts table references it but its defect row was truncated out of the delivered table — treat as unverified).

---

## 12. Rulings on the plan's pending decisions (status flips to RULED when the plan lane folds them)

| Decision | Was | **Ruling** |
|---|---|---|
| D-3 (env surface) | RECOMMENDED-PENDING-ARCHITECT | **RULED: ENV** — with §3.4's rewritten residual and §3.5's compensating controls |
| D-4 (parser/validation) | RECOMMENDED-PENDING-ARCHITECT | **RULED** — with the three amendments of §3.6 and the absent-identity leg of §6.3 |
| D-8 (the two code names) | RECOMMENDED-PENDING-ARCHITECT | **RULED: ratified as named** — path-neutral, side-specific detail text; the 4f split is the binding companion |
| D-9 (threads) | RECOMMENDED-PENDING-ARCHITECT | **RULED: NO inheritance** — exact channel-id match |
| D-10 (v0.29.19 / LEFT STANDING) | RECOMMENDED-PENDING-ARCHITECT | **RULED: LEFT STANDING** — with §9.3's protection statement and survival math |
| D-11 (the adapted K-3) | RECOMMENDED-PENDING-ARCHITECT | **RULED** — in the §8 shape (two-phase, three armed states, second-account, triage table) |
| NEW D-13 | — | **RULED: add** — the parent-reconciliation row (§11 row 8) |

---

## 13. Decisions pending — for the Emperor / the leader (not mine to close)

1. **NT-1 — the in-room token/diff disclosure posture.** (i) DM-deliver the token for designated contexts; (ii) redact the render from the in-room response; (iii) accept-and-record (the Emperor's own ruling treats the channel as private — but the plan must *record* the acceptance, it cannot stay implicit). Default if unruled by council time: (iii) with the §8.5 hygiene rules, recorded in the runbook.
2. **NT-2 — acceptance of the bash→`.env`→restart path.** Accept with the §3.5 controls, or commission the root-owned-file successor. Default: accept + controls.
3. *(Minor)* NT-9 — whether `ens-env` redacts the map var. Default: accept and record.

## 14. Open questions / unverified (carried, none blocking)

- The live `.env`'s mode/ownership and this deployment's `ENSEMBLE_AUTH_ENABLED` posture (both read-only-checkable by the plan lane in one command; not checkable from this worktree-only commission).
- The runtime behaviour of `_classify_dm_channel` / the adapter's `is_group_dm` at live level — **that is what the probe is for** (unit-proven only today).
- Whether the Emperor's relay bot is present in a non-designated guild channel (leg 4's precondition — an operator fact; if absent, the leg is INCONCLUSIVE, not a pass).
- The `previous` pin at the first v0.29.19 promote (the two-promote math assumes no intervening promote).
- The sweep's R-17 cite (§11 row 32).
- Commission erratum to carry upward: `EXECUTOR_ENV_ALLOWLIST` lives at `daemon/tools/upgrade_journal.py:2593`, not `job_queue.py`.

## 15. Gaps

None by fan-in escape valve — all five dispatched passes returned complete reports with skills confirmed (`Skill loaded:` first-line on every one; no skill-bank misses, no re-dispatches, no DEGRADED markers). Two inter-pass disagreements were adjudicated in-aggregation rather than re-dispatched, both on verified evidence: the conftest-vs-rig scrub question (resolved to "pin the rig scrub; conftest only as fallback", §NT-7) and the envelope direction (resolved to keep the existing string byte-identical, §2.4 — the option that preserves more guarantees at equal cost).

---

*Evidence base: five read-only verification passes in `/home/nea/ensemble-src-wt-assent-p15-channels` @ `efa8aaae2` (instance ids in the header). All file:line anchors re-derived by the passes from that tree; no tests, builds, or scripts were executed; `/home/nea/ensemble-src` was never read. No channel ids, user ids, tokens or configuration values appear in this artefact.*

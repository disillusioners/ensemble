# Code Review — `gc-fe-surfaces` implementation (group-chat FE fast-follow)

- **Date:** 2026-11-18
- **Commission:** Emperor Discord 2026-11-17 — group-chat FE fast-follow (header browser + per-instance popup + conditional visibility)
- **Review lane:** Reviewer (v2) → governor council, 2 councilors (`agentic`, `coding`), one skill each (`code-review`), strict read-only
- **Mode:** 🔴 Deep-Review (security-sensitive read surface; data-integrity + business-rule + concurrency triggers)
- **Verdict:** **APPROVE-WITH-CONDITIONS** — **0 CRITICAL / 3 WARNING / 12 ADVISORY**
- **Change set (derived independently, dev summary not trusted):** worktree `/home/nea/ensemble-src-wt-gc-fe-surfaces`, branch `feature/gc-fe-surfaces`, base `0181dadbf` → head `5efc8735c`; 35 files, +5421/−94, 24 commits; tree clean before and after the review.

**Context artifacts (frozen, read-only, byte-untouched by this review):** `spec.md` (Emperor spec), `decisions.md` (binding decisions incl. LEADER DISPOSITION), `gate-evidence.md` (dev's P4 ledger).

---

## 1. Lens results

| Lens | Result | Note |
|---|---|---|
| 1 — Security posture | **PASS** | Genuinely single-sourced decision core; complete fail-closed matrix; no predicate duplication/drift; no title/roster leakage for unreadable rooms |
| 2 — Shipped-surface identity | **PASS** | Byte-identity verified by diff vs base; shipped mode never constructs the timer; four shipped spec pins untouched; chat-page diff pure-append |
| 3 — Event-loop + seam discipline | **PASS** | `asyncio.to_thread` closure; pure-DB existence check (never `manager.get_instance`); knob single-branch; D13 zero per-room queries |
| 4 — FE conventions | **PASS** (with advisories) | Signals/`computed`/`inject`/`takeUntilDestroyed`; full polling contract pinned; typed envelope, `tsc` EXIT 0 |
| 5 — Six disclosed deviations | **all six accepted**, two with conditions | None breaches a commission constraint |
| 6 — F-E erratum | **OWED** | One stale clause (`decisions.md` D2.2); home ruled — see §5 |
| 7 — Gate-config isolation | **PASS** | Standalone config, own port 14204, `testMatch` pinned, `reuseExistingServer: false` |

---

## 2. WARNING findings

### W1 — 5xx transport-string leak (URL + status rendered into the pane)
- **Where:** `frontend/src/app/services/room.service.ts:492-493` (fn `describeHttpError` defined at `:464`, body 464–494; generic copy `:493`; typed 404/403/0 branches `:468-482`).
- **Issue:** Angular's `HttpErrorResponse.message` is always populated, so a body-less 5xx falls through to the transport string and renders `Http failure response for <url>/api/rooms?instance_id=…: 500 Internal Server Error` — a URL and status text in the user-visible pane.
- **Fix:** partition the final untyped branch by status class — on ≥500 (and unknown-status-with-transport-message) prefer the server's typed detail from `raw.error` → `detail.message`, else a **fixed subject-specific constant** (no URL, no status text, no raw interpolation); **the raw transport message must never be consulted on that path**; restrict the raw-message fallback to <500.
- **Blast radius (must be disclosed):** `describeHttpError` has exactly **three** call sites, all in `room.service.ts` — `:200` (shipped rooms lane), `:247` (shipped messages lane), `:311` (new global lane). The fix therefore changes **shipped** 5xx copy — a shipped-surface delta under constraint 4, not currently in the lane's deviation ledger. Disclose it in the promotion note.
- **No test reds:** `room.service.spec.ts:505` (stale flag), `room-dialog.component.spec.ts:478` (Retry + mode-preserving retry), `:522` ("Refresh failed" / "last known messages") assert no 5xx text; the e2e 500 leg (`group-chat-rooms-gate.spec.ts:409-430`) asserts structure only and stays compatible; the 403 leg's negative assertion (no "Http failure") remains satisfied.

### W2 — the ancestor-of-owner predicate branch is unexercised (coverage gap, scale-independent)
- **Where:** `daemon/repositories/room/repository.py:1460-1462`; stub `tests/unit/routers/test_rooms_list_api.py:103-117`.
- **Issue:** counter-mutation — delete the `or owner in descendant_set` disjunct and **all 18 cases stay green**. Two independent causes: (1) the stub returns the caller + **direct children only**, while the real facade returns the caller + **all** descendants (`daemon/repositories/instance/repository.py:838-903`), so `descendant_set` never holds a grandchild; (2) no case places the anchor strictly above the owner — the case named "owner-ancestor sees it" has anchor == the room's owner, which rides the *identity* disjunct; the AM-10 leg calls the repository directly with `descendant_ids=set()`, where `owner in set()` is already False.
- **Why it matters:** this disjunct is the **only** route by which a strict ancestor of a room's owner (not himself a member) can read that room — the predicate constraint 1 requires preserved. A regression would be fail-closed (ancestors lose read, no leak), but the suite is blind to it.
- **Fix:** (a) make `_Manager.get_tree_ids_permanent` return the **full permanent subtree**, preserving the real facade's "missing root ⇒ `[]`" guard — cleanest is to delegate to the real `SQLModelInstanceRepository.get_tree_ids_permanent` (the AM-10 leg already instantiates it at `:468-475`); (b) add a **new HTTP leg**: seed `grandparent→parent→child`, room owned by `child`, anchor = `grandparent`, grandparent **not** a member ⇒ the room **must** be listed; assert the **exact set** (W-11 precedent); (c) publish the counter-mutation. **(a) alone leaves the suite green — only (b) makes the disjunct observable.**

### W3 — the quoted leader ruling overstates the anchor-less path (docs-truth)
- **Where:** `.agents/shared/planning/gc-fe-surfaces/decisions.md:477` (also `:92`) quotes *"every row predicate-checked fail-closed via the shared `_require_room_access`"*, but the anchor-less path applies **no predicate** — `list_all_rooms_with_extras` (`daemon/repositories/room/repository.py:1382-1419`) returns every room row unfiltered.
- **Status:** the behaviour matches the architect's S-2 adjudication and the leader's PROCEED; the **quoted text** overstates it (same truthfulness class as AM-1 on D1.6/D1.11).
- **Fix:** do **not** edit frozen `decisions.md`; scope the claim to the **anchor-present** path in this artifact and **route the correction to the leader** — a dev ledger may not self-correct a quoted ruling.

---

## 3. ADVISORY findings

| # | Location | Issue | Fix |
|---|---|---|---|
| A1 | `room.service.ts:298` + `room-dialog.component.ts:132-138` | Composite is **clamp-then-filter**: server clamps the *readable* set at `repository.py:1467-1469`, then FE filters `member_instance_ids.includes(anchor)`; `fetchAllRooms` (`room.service.ts:173-180`) sends **no `limit`** ⇒ server default `Query(200)` binds (max 500, `rooms.py:289-292`) | Optional: request an explicit larger limit in joined mode; server-side joined filtering before clamp conflicts with AM-9 (out of scope) |
| A2 | `room-dialog.component.html:108-116` vs `:144` | Empty branch renders `globalEmptyCopy` ("This instance has not joined any chatroom") with **no** truncation awareness; `globalEmptyCopy` (`room-dialog.component.ts:~166-171`); `globalRoomsTruncated` is consulted at exactly one place (`:144`) | Hoist the notice out of the non-empty `@else`; make the empty copy truncation-aware. Copy nit: `truncationCopy` says "readable rooms truncated at N" but N is `globalRooms().length` — the post-filter *joined* count |
| A3 | `daemon/routers/rooms.py:388` | `_ALLOW_ANCHORLESS_GLOBAL_LIST` is a module constant ⇒ the leader's F-3 follow-up needs a release, not a restart | Read the default from an env var with the constant as fallback |
| A4 | `repository.py:1449-1454` | Unbounded roster `IN`-list over every room id ⇒ bind-param ceiling ⇒ 503 wrapper | Capacity-plan; optionally push the predicate into SQL while keeping `_room_readable` the single oracle |
| A5 | `test_rooms_list_api.py:405` | `test_n9_422_holds_under_knob_off` flips the same process-global knob as `:373` **without** `@pytest.mark.no_xdist` — under xdist the marked twin is skipped while this one runs (inverse of the plan posture) | Add the marker |
| A6 | `test_rooms_list_api.py:533-550` | AM-12 single-branch grep-pin is raw-text; a ternary branch (`x = … if _ALLOW_ANCHORLESS_GLOBAL_LIST else …`) slips both assertions (prose-satisfiable) | Add a counter-mutation leg, or widen to an AST check counting `If` nodes whose test references the knob |
| A7 | `instance-list.html:141` vs `:247` | `.child-row` keys on `depth`, the button set on `parent_id`; `instanceTree` promotes an absent-parent instance to root (`instance-list.component.ts:159-164`) ⇒ cosmetic 32px gap | Key the discriminator on `!instance.parent_id` |
| A8 | `playwright.rooms-gate.config.ts:12-13` | Comment claims pinned sweep/ambient-allow guards, but the `webServer` block sets **no `env`** ⇒ pins nothing (moot: `ng serve` boots no daemon) | Correct the comment to describe the real guard (`reuseExistingServer: false`), matching the metrics-gate precedent |
| A9 | `group-chat-rooms-gate.spec.ts` adjacency row | `test.skip` (daemon-fenced, leader-owned); the ledger records the earlier "4 passed" rows were vacuous | Leader's daemon-fenced run must execute the row before the staged release is gate-complete; unit pins `chat.chatrooms.spec.ts:372/401/432/457` are the only automated adjacency evidence |
| A10 | `repository.py:740-790` | Two-session split leaves a benign TOCTOU; the truncated flag comes from session 1 | Accept as D13; if stricter consistency is wanted, compute aggregates in session 1 for the clamped rows only |
| A11 | `instance-list.component.ts:215-260` | One anchor-less `GET /api/rooms` per instance-list mount; knob-OFF ⇒ 404 + error/Retry (fail-closed, correct but noisy) | None required — by design; knob default is `True` |
| A12 | deviation (d) hunk | `data-gc-room-chat="1"` is verified inert (0 hits at base, exactly 2 at HEAD; no CSS rule, e2e selector, or consumer) | Optional: an HTML comment on the same hunk satisfies the same lexical drift guard with zero live-DOM impact (the lane's own idiom) |

---

## 4. Deviation adjudications — all six accepted; none breaches a commission constraint

| Deviation | Ruling | Reason / conditions |
|---|---|---|
| **(a)** adjacency-skip with unit pin | **ACCEPT WITH CONDITION** | Skip is explicit and reason-tagged (`spec.md:276-277`), does not fake a pass — but the gate's headline assertion is not machine-enforced. Conditions: (i) the promotion note + lane report must **not** cite the e2e gate as header-icon-adjacency evidence; (ii) the leader's daemon-fenced run must execute the row before the staged release is gate-complete; (iii) keep the skipped row visible. Does not breach constraint 5 — the rooms-gate is a lane-invented P4 artifact, not the house chain (architect → council → approver), and the invariant it guards is unit-pinned |
| **(b)** F-A.4 typed-error 403 + structural-500 | **ACCEPT WITH CONDITION** | 403/404/0 correctly typed; the 500 leg is structural-only and the dev recorded the URL leak instead of pinning it — sound, mutation-proven posture. Conditions: (i) land the W1 fix before or with the staged release; if deferred, carry it as an **open product item with an owner**, not only inside a frozen ledger; (ii) disclose the shipped-lane blast radius (`:200`, `:247` are shipped) |
| **(c)** D13 second batched session | **ACCEPT** | Exactly two sessions per path; extras one grouped `COUNT`/`MAX(seq)` each; roster one `IN` query; **zero per-room queries** (code + `test_criterion21`). Buys the W-5 session variant and "a clamped-away row pays for no aggregates" |
| **(d)** `data-gc-room-chat` inert marker | **ACCEPT** (+ non-blocking recommendation) | Inert (0 hits base, 2 at HEAD; no CSS/e2e/consumer). Purpose legitimate: the drift guard's FE-UI sanction requires a `room`/`chat` token on every frontend/ hunk in the sanctioned paths, and the P3 child-row hunk is markerless without it (guard 39/39). Prefer an HTML comment |
| **(e)** user-space playwright chromium | **ACCEPT** | `~/.cache/ms-playwright` (Chrome Headless Shell 148.0.7778.96 + ffmpeg-1011); no apt/system install; **no new npm dependency** (constraint 2 ✓); `ng` from the local devDependency ⇒ no npx registry-fetch hazard |
| **(f)** `gate-evidence.md` additive file | **ACCEPT** (+ one caveat) | New file, 222 lines, all additions; no behaviour / frozen-artifact / dependency change; honestly records the vacuity root-cause and mutation proofs. Caveat: its **"Deviations — None behavioral"** summary (`:63`) flattens its own Revival-3 product finding (`:167`, the 5xx copy) ⇒ that line must reference the named finding, and the finding must reach the lane's final report |

---

## 5. F-E erratum adjudication — **OWED** (unanimous)

**Stale clause:** `decisions.md:186` (D2.2) — *"One timer per open surface, driving **both** lanes … on the same tick. A single shared 10 s tick, not two."* The shipped code satisfies "one timer, not two" (`room.service.ts:343-348`), but the tick body is visibility-gate → in-flight-gate → open-room-gate → `refreshOpenRoom()` = the **conversation lane only**; the rooms list refreshes on open (`loadGlobalRooms`) and on explicit retry (`retryRooms()`), never on the tick. Direct consequence of the AM-5 fold, which D2.2 predates.

**Scope (narrowed):** `decisions.md:201` (D2.4) already records the AM-5 skip and `:203` the cursor policy ⇒ this is a **narrow correction to one stale clause**, not a wholesale "the ledger doesn't describe the behaviour" claim. One-paragraph entry suffices.

**Where it lives:**
1. **PRIMARY — this artifact (§5 above + the lane's final report).** States the clause, the shipped behaviour, and the disposition.
2. **SECONDARY — an ADDITIVE named erratum entry appended to `gate-evidence.md`**, consistent with that file's own lifecycle (P4 Revival `:75`, Revival 2 `:110`, Revival 3 `:120`; the F-F entry `:199` sets the precedent for correcting a stale statement). The same named entry should correct `gate-evidence.md:63` to reference the named 5xx-copy finding rather than deny one exists. *(This review lane does not write the dev-owned ledger — it is outside this commission's write scope; the entry is owed to the lane owner. Ready-to-paste text below.)*
3. **NOT `decisions.md`** — frozen, carries the leader's disposition; nothing is edited in place.
4. **Must be surfaced in the lane's final report to the Emperor:** *"D2.2's 'both lanes on the same tick' clause is superseded by the AM-5 fold — one timer drives the conversation lane only; the rooms list refreshes on open and on explicit retry."*

**Ready-to-paste erratum entry for `gate-evidence.md`:**

> **F-E erratum (2026-11-18, code-review lane).** `decisions.md` D2.2's clause *"driving both lanes … on the same tick"* is superseded by the AM-5 fold: one timer drives the conversation lane only; the rooms list refreshes on open and on explicit retry. `decisions.md` D2.4 (`:201`) and `:203` already record the AM-5 skip and the cursor policy — this entry corrects D2.2's stale clause only; `decisions.md` remains untouched. Separately, this ledger's "Deviations — None behavioral" summary (`:63`) flattens the Revival-3 5xx-copy product finding (`:167`); see code-review finding W1 (ADVISORY→WARNING: `room.service.ts:492-493`, `describeHttpError`).

**W3 is routed separately** — the leader-quote correction is *not* a ledger erratum; it goes to the leader for the record.

---

## 6. Conditions to close before promote

1. **W1** — land the `describeHttpError` server-error branch (body-typed detail first, fixed generic otherwise, transport string never consulted), disclose the shipped-lane blast radius (3 call sites; `:200`/`:247` shipped), and pin the new copy in a test.
2. **W2** — full-subtree stub/real-repo delegation **plus** the grandparent-anchor HTTP leg (exact-set assertion) **plus** the published counter-mutation. The stub change alone leaves the suite green.
3. **W3** — route the leader-quote correction to the leader; this artifact carries the adjudication.
4. **F-E erratum** — additive named entry in `gate-evidence.md` (lane owner) + restatement in the final report to the Emperor; `decisions.md` untouched.
5. **Deviation (a)** — the leader's daemon-fenced run must execute the adjacency row before the staged release is gate-complete; the promotion note must not cite the e2e gate as adjacency evidence.
6. **Deviation (f)** — `gate-evidence.md:63` must reference the named 5xx-copy finding rather than deny one exists.

---

## 7. Evidence base (executed read-only by the council)

`tsc --noEmit -p tsconfig.app.json` → **EXIT 0** · jest, 4 changed FE suites → **65/65** · `pytest tests/unit/routers/test_rooms_list_api.py` → **18 passed** (22/22 incl. scope manifest) · `pytest tests/unit/repositories/test_source_gc_repository.py` → **39 passed** (drift guard 39/39) · census + cage → **14 passed** (after the auth-env correction below) · production build → **zero NET-NEW warnings**.

**Fences respected:** no daemon booted, no `POSTGRES_*` read or written, no DB contacted, no file created/modified/deleted by the council, `git status --porcelain` empty after every run (pytest with `-p no:cacheprovider --override-ini="addopts="`), no `npx`/bare `tsc`, no browser or system-package install.

**Operational note (not a lane defect):** the worktree `.env` carries `ENSEMBLE_AUTH_DISCOVERY_URL=https://door.ensem.dev` vs `ENSEMBLE_AUTH_ISSUER=https://idp.test` ⇒ `AuthConfig` validation fails at import and the two census/cage files error; with both set to `https://idp.test` they pass 14/14. The ledger's "49 passed (with dev env fence)" was produced under a differently-fenced env than the raw worktree `.env` — any future lane running those files from this worktree hits it.

---

## 8. Unverified / owed by others

E2E adjacency row not executed (leader-owned daemon-fenced run) · the W2 counter-mutation was not executable in a read-only lane (18-case enumeration is by inspection) · the W1 fix was not applied (the "no test reds" claim is by assertion inspection plus repo-wide grep) · live room counts were not measured (host fence; ambient `POSTGRES_*` could point at prod — the scale argument rests on enforced invariants, which bound the resident set regardless) · the whole-tree jest baseline (4909 passed / 13 failed) and production-build budget attribution were not re-run.

---

## 9. Divergences surfaced (resolved on substance; dissent preserved)

**D1 — severity of the composite clamp-then-filter / false empty copy.**
`coding`: **WARNING** ("reachable only when an anchor's readable rooms exceed 200 and all joined rooms fall past the boundary"; no data loss, no leak, no fabricated rows). `agentic`: **ADVISORY** — after re-deriving the enforced invariants (one `tree_root_id` per subtree, `room_harness.py:236-256`; ≤8 active rooms per tree, `membership.py:300-310`; **cross-tree joins refused**, `membership.py:248-258`; ≤16 members/room, `membership.py:125`; closed rooms purge at +14 d / idle-active 72 h, `room_gc.py:91-97`), an anchor's readable set ⊆ its own tree's rooms ⇒ the trigger needs **one tree to hold 200+ rooms inside a 14-day window**, one to two orders of magnitude beyond pilot scale. It also corrected its own R1 scale estimate as wrong on two counts.
**Ruling:** the `agentic` reasoning prevails — it alone derived reachability from cited invariants, while `coding` rated severity treating the trigger as live. Factually they **fully agree** (both confirm the composite is readable → clamp(200) → joined-filter, and the empty branch is unguarded). **A1/A2 are therefore ADVISORY — latent, scale-gated**, with an explicit escalation trigger: *the moment one tree's room population inside the 14-day window exceeds 200, A2 becomes a live false-negative in the UI.* Taking the ~5-line template fix now is still recommended — it removes a class of falsehood for negligible cost. The `coding` WARNING stands as dissent to weigh if room growth is expected to outpace the pilot model.

**D2 — scope of the erratum.**
`coding` framed it as "the ledger misstates what the code does"; `agentic` narrowed it to one stale clause with a citation (`decisions.md:201`/`:203` already record the AM-5 skip and cursor policy) and **withdrew** its R1 requirement for a new `errata.md`, accepting `gate-evidence.md` as co-home. **Ruling:** the narrowed, cited scope is adopted; the home converged once the new-file requirement was withdrawn.

---

## 10. Recommendation

**APPROVE-WITH-CONDITIONS.** The commission's #1 constraint holds on two-model consensus — a genuinely single-sourced decision core, a complete fail-closed matrix, no predicate duplication or drift, and byte-for-byte preservation of the live shipped surface. Every condition above is a closure, not a redesign; none requires touching the decision core or the shipped pane structure.

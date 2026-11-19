# Code-Level Approver Spot-Check — `gc-fe-surfaces` (group-chat FE fast-follow)

- **Date:** 2026-11-18
- **Reviewer:** Approver (v2) — code-level spot-check, independent eyes on the FINAL lane state
- **Worktree:** `/home/nea/ensemble-src-wt-gc-fe-surfaces` · branch `feature/gc-fe-surfaces`
- **Base:** `0181dadbf8641c60d7158ad59edfa4a4b36944c8` (v0.31.0) · **HEAD:** `3a76cbd0c4bd9499476d0d43fca2d1b95cd5ef62`
- **Scope:** the FINAL delta after the council's artifact commit `b2a8c3eff` (15 commits), the drift-guard gate-sanitization, the ledger's honesty, the 8 tester disclosures, and staged-readiness.
- **Posture:** code-level only. The plan-level approval (approver 001/002/003 + leader disposition) is DONE and NOT re-litigated here. No product change was re-designed.

## VERDICT: **REJECTED**

**The CODE is sound; the GATE RECORD is not.** Every behavior-bearing fix in the post-council delta is correct and safe; the drift-guard allowlist extension is legitimate, minimal and provably non-vacuous; the tester's counter-mutation is real (independently reproduced); all 8 disclosed items are TRUE in the tree; every staged-readiness fact verifies. **Two blocking defects live in `gate-evidence.md` — the artifact the promotion note will be written from.** Both are additive ledger edits (no code change, no frozen-artifact change, no re-test required); the lane owner can close them in minutes and re-submit.

---

## 1. FINAL-DELTA REVIEW — the behavior-bearing fixes (all correct)

Delta derived independently: `git log b2a8c3eff..HEAD` = 15 commits; `git diff --stat b2a8c3eff..HEAD` = 23 files, +2242/−251.

| Fix | Location (HEAD) | Verdict |
|---|---|---|
| fail-closed on anchor loss | `frontend/src/app/services/room.service.ts:305-317` | **CORRECT.** `joined` mode with a dropped anchor ⇒ NO request (`httpMock.expectNone` pins it), honest error, empty list, `truncated=false`, `errorStatus=null`, `evaluateOpenRoomPresence([])`. Second defence at `room-dialog.component.ts:241-243` (`if (!instanceId) return;`). Pinned by `room.service.spec.ts:441` ("ITEM1"). |
| poll presence evaluation | `room.service.ts:424-441` | **CORRECT.** Poll success ⇒ `openRoomAbsent=false` (a readable room clears the vanished posture; a later vanished poll re-raises it). Poll error ⇒ `stale=true`, and `404 ⇒ openRoomAbsent=true` only. `closeRoom` resets `stale=false` (`:282`). |
| copy gating (A-12) | `room-dialog.component.html:198` | **CORRECT.** `@else if (!isInstanceMode && !selectedRoom())` → `@else if (!isInstanceMode && openRoomAbsent())`. The "room vanished" panel now renders only when the room is *known absent*, not merely unselected — this removes a false-positive panel on the ordinary "global mode, nothing selected" path. |
| truncated-on-closure-fault | `daemon/routers/rooms.py:405-433` | **CORRECT.** `closure_faulted` ⇒ `_shape_rows(rows, truncated or closure_faulted)`. A faulted descendant closure marks the response truncated, so the FE discloses an incomplete set instead of presenting a partial list as complete. Fail-closed in the right direction. |
| stale reset | `room.service.ts:282` | **CORRECT.** |
| loadRoomMap token guard | `instance-list.component.ts:236-255` | **CORRECT.** `roomMapRequestId` guard on BOTH `next` and `error`; a superseded response can no longer overwrite the membership map. |
| `<500` raw.error-only | `room.service.ts:604-608` | **CORRECT + DISCLOSED.** The `<500` path drops `extractErrorDetail(raw.message)` ⇒ a body-less 4xx renders the fixed subject-specific generic copy instead of Angular's transport string (which carried the chatroom URL). Pinned by `room.service.spec.ts:845` ("an untyped <500 status renders the fixed generic copy — no transport string, no URL"). Disclosed as ledger deviation **(b)** at `gate-evidence.md:258`. |

Also verified in the same delta:
- **A3 (knob env-resolved)** — `daemon/routers/rooms.py:157-200`: `_ALLOW_ANCHORLESS_GLOBAL_LIST = _resolve_anchorless_global_list()` with `ENSEMBLE_ALLOW_ANCHORLESS_GLOBAL_LIST` and a strict parse; default `True`. The council's "needs a release, not a restart" finding is genuinely closed.
- **W2 (predicate-branch coverage)** — the disjunct `owner == caller_instance_id or owner in descendant_set` is at `daemon/repositories/room/repository.py:1479`; the grandparent-anchor leg with the exact-set assert is at `tests/unit/routers/test_rooms_list_api.py:956`.
- **W3 substance** — the erratum's claim is TRUE of the tree: the anchor-less path (`rooms.py:367-385`) calls `room_service.list_all_rooms(...)` → `list_all_rooms_with_extras` (`repository.py:1407-1425`) → `select(ChatRoom).order_by(...).limit(...)` with **no predicate**; the anchored path is predicate-checked. The erratum describes the code accurately.
- **tidier trio behaviour-neutrality** — `roomMapLoading` has **zero reads** anywhere in `frontend/src/` (the drop is genuinely dead); the `globalRoomsScope` scoping (`e07972545`) is a *correctness* fix, not a neutral one — the anchor-less mode no longer stores a stale anchor that `:401` would replay on retry.

**No undisclosed shipped-surface change was found.** The two shipped-surface deltas are W1 (5xx copy on all three `describeHttpError` call sites) and (b) (the untyped `<500` path). See note N3 — (b) is also a shipped-lane change but is not *named* as such.

---

## 2. GATE-SANITIZATION CHECK — the drift-guard extension (PASS)

`c479cef98` extends `FRONTEND_UI_LANE_SANCTIONED_FILES` (`tests/unit/repositories/test_source_gc_repository.py:796-805`) with exactly three paths and adds the one missing lane marker:

- `frontend/e2e/group-chat-rooms-gate.daemon.spec.ts` — the G2 daemon-fenced e2e spec
- `frontend/playwright.rooms-gate.daemon.config.ts` — its dedicated playwright config
- `frontend/proxy.gcfe-e2e.conf.json` — its dev-server proxy conf (line-1 `// FE-UI lane marker …` comment added in the same commit)

**Are they exactly the lane's legitimate e2e/gate artifacts?** Yes — the same class as the two artifacts the allowlist already carried (`group-chat-rooms-gate.spec.ts`, `playwright.rooms-gate.config.ts`). All three are the gate's own test tooling, added by `6fe3ad005`.

**Do they escape the production graph?** Verified three ways: `frontend/tsconfig.app.json` includes only `src/**/*.ts` (the e2e/config/json files are outside it); `angular.json` copies assets only from `public/**` (root-level files are not copied into `dist`); and `angular.json`'s `serve.proxyConfig` is `proxy.conf.json` — **not** the sanctioned conf. The proxy conf is referenced by exactly one thing, `playwright.rooms-gate.daemon.config.ts:14` (`--proxy-config proxy.gcfe-e2e.conf.json`), and points at `127.0.0.1:8079` (the fenced dev daemon). No strict-JSON consumer exists over `frontend/*.json` (`package.json` has no validate script; the only `JSON.parse` is in `frontend/scripts/r3-dev-symptom-mirror.cjs` over an unrelated file).

**Is the `//` marker comment safe for its consumer?** Verified independently with the repo's own `jsonc-parser`: `parseErrors: 0`, and the parsed proxy object is identical to the comment-free config. (A strict `JSON.parse` would fail — acceptable, because Angular's dev-server parses proxy confs with `jsonc-parser`.) The tester's claim is TRUE.

**Is the counter-mutation proof real?** Re-derived independently, not taken on trust:
- Guard at HEAD: **39 passed**.
- Removed the three allowlist entries myself ⇒ **2 failed / 37 passed** (`test_the_frontend_ui_lane_drift_is_confined_to_sanctioned_lane_hunks`, `…_at_the_tree_level`) — exactly the tester's claimed pre-fix state.
- Restored byte-identical: PRE == POST `sha256 db4703769794d40eac93d87d0a02f037c9c39177f6e87fcce2ffa8f31a5ec4f6`, `cmp` rc=0, `git diff` empty. (The PRE sha prefix matches the tester's published `db4703769794…`.)

**Does the extension sanction drift the guard should have caught?** No — probed directly. The guard judges *committed* history (`git diff --unified=0 <fixed base>..HEAD -- frontend/`), so I probed in a throwaway detached worktree (removed afterwards; the lane tree was never touched):
- A marker-bearing **new file at an unsanctioned path**, committed ⇒ **2 failed / 37 passed**. The path-bound rule (F-1) holds: a foreign frontend path is an offender regardless of wording. The extension did **not** blanket-sanction `frontend/`.
- The file-state compensator (`…sanctioned_files_carry_their_marker_constants`) requires every allowlisted path to exist and to carry a `room`/`chat` marker — satisfied by all three new entries (guard 39/39 green confirms).

**Conclusion on check 2: PASS.** The extension is minimal, correctly scoped, outside the shipped surface, and the guard keeps its teeth. Two process notes below (N1, N2).

---

## 3. LEDGER HONESTY — `gate-evidence.md` (TWO BLOCKERS)

**Present and correct:** the W2 counter-mutation is PUBLISHED with RED evidence (`:348-356`); the W3 leader correction is present (`:317-324`, substance verified against the code above); the F-E erratum is present and verbatim (`:326-328`, matches `code-review.md:96`); the deviation ledger names the W1 shipped-lane 5xx-copy delta (`:234-242`) and the A7 discriminator change (`:243-257`).

**The stubbed-config skip is not cited as adjacency evidence** — the stubbed run is cited at `:208`/`:339` only for the geometry rows, which genuinely executed under it. That sub-check passes.

### BLOCKER 1 — deviation (a) was DELETED instead of resolved, and the adjacency row is never recorded as EXECUTED (daemon-fenced)

`git show 1079c7365 -- …/gate-evidence.md` shows the close-out addendum's deviation entry **`(a)` — the adjacency-row deviation — was REPLACED** by the unrelated `(b)` entry:

```
-- **(a) — the browser-level adjacency row stays SKIPPED and VISIBLE.** … the promotion note must NOT cite
-  the e2e gate as header-icon-adjacency evidence. The leader's daemon-fenced run … must execute that row
-  BEFORE the staged release is gate-complete. …
+- **(b) — W1 leak removal broadened to the untyped <500 class (item 7).** …
```

Consequences in the tree as it stands:

1. **The ledger carries no `(a)` entry at all** — the deviation list now reads `(b)`, W1, A7. The council conditions that lived in `(a)` — above all **condition (a)(i): "the promotion note must NOT cite the e2e gate as header-icon-adjacency evidence"** — are no longer carried anywhere in the ledger.
2. **`gate-evidence.md:65` still claims the addendum carries "deviation (a)'s adjacency-row wording"** — a reference to an entry that no longer exists. That is a claim the tree does not support.
3. **The adjacency row is nowhere recorded as EXECUTED (daemon-fenced).** Every surviving mention describes it as outstanding: `:66` ("the skip is the header-adjacency row … owned by the leader's daemon-fenced run"), `:91` ("Full real-backend assertion (adjacency) requires the fenced dev daemon … deferred to the leader-run gate"), `:111-118` ("Adjacency row status: DEFERRED-to-leader-gate"), `:150` (F-A table: "header adjacency | … | SKIPPED"), `:339` ("the adjacency row stays skipped"). Yet `6fe3ad005` **executed it against the real daemon and it PASSED** — `frontend/e2e/group-chat-rooms-gate.daemon.spec.ts:89` runs the row live (no skip), measuring the toggle cluster `watchover-btn | room-btn | rooms-browser-btn | think-btn | tools-btn | system-btn` ⇒ `roomIdx=1`, `browserIdx=2`, **adjacency Δ=1** (tester report §3.1/§3.2 leg (a)).
4. **The deletion was premature.** `1079c7365` is dated 15:59; the tester's daemon-fenced execution (`6fe3ad005`) is dated ~17:0x — at the moment `(a)` was deleted it was still an OPEN deviation, not a resolved one. Nothing updated the ledger afterwards (its last touch is `1079c7365`; the tester's doc commit `3a76cbd0c` does not touch it).

**Why this blocks:** the promotion note is written from this ledger. As it stands, the ledger says the adjacency row is unproven and owned by an outstanding leader run, and it no longer carries the condition that forbids citing the stubbed e2e gate as adjacency evidence. The load-bearing evidence (the daemon-fenced PASS) exists only in the tester report. Council condition 5 (deviation (a)) is factually satisfied in the tree and falsely recorded as outstanding in the ledger.

**Concrete fix (additive, ledger only — no code change, no re-test):**
1. Re-add a deviation **(a)** entry to the close-out addendum recording the **daemon-fenced EXECUTION as PASS** (`6fe3ad005`, `group-chat-rooms-gate.daemon.spec.ts:89`, adjacency Δ=1), with the stubbed-config run explicitly marked as **NOT** adjacency evidence.
2. Carry forward council condition **(a)(i)** verbatim ("the promotion note must not cite the e2e gate as header-icon-adjacency evidence") and mark **(a)(ii)** SATISFIED by that run, **(a)(iii)** satisfied (the skipped row stays visible — `group-chat-rooms-gate.spec.ts:279`).
3. Correct `gate-evidence.md:65` so it no longer references a deleted `(a)` entry (or reference the restored one).
4. Update the ledger's `HEAD:` anchor (`:4` still reads `d66b2e260`; the branch HEAD is `3a76cbd0c`) and the `:150` / `:339` rows so they no longer present the adjacency row as outstanding.

### BLOCKER 2 — the W1 section asserts a code behaviour the tree contradicts

`gate-evidence.md:279` (the W1 pin list) states:

> "the `<500` copies (404 / 403 / 0) are UNCHANGED; **a client-side status keeps the raw fallback**."

The tree says otherwise: `room.service.ts:604-608` **removed** the raw fallback from the `<500` path, and `room.service.spec.ts:845-857` now pins the OPPOSITE — "an untyped `<500` status renders the fixed generic copy — no transport string, no URL" (and asserts the copy does **not** contain `422`). The ledger therefore contradicts itself: `:258` (deviation **(b)**) says the raw fallback was dropped; `:279` says it is kept.

There is no reading under which both are true — status `0` is a *typed* branch that renders a fixed copy ("Could not reach the server."), never a raw fallback, so "a client-side status keeps the raw fallback" can only refer to the untyped `<500` class, which no longer consults `raw.message`.

**Why this blocks:** this is the one clause describing the exact path whose shipped-surface blast radius the promotion note must disclose. A reader is told the `<500` path is untouched while the tree shows it changed — and the tester exercised it live (report §3.2 leg (g): 418 ⇒ `Failed to load chatrooms.`, leak=false). A ledger that tells two stories about the same code path cannot serve as the disclosure source.

**Concrete fix:** amend `:279` to match the shipped code — replace "a client-side status keeps the raw fallback" with the actual posture (the raw transport message is never consulted on the `<500` path; the typed 404/403/0 copies are unchanged), and cross-reference deviation **(b)**. Keep the council's own `:329` F-E text untouched (it cites `:63`/`:167` by line, which the append-only discipline preserves).

---

## 4. DISCLOSURE CHECK — the 8 disclosed items (all TRUE)

| # | Disclosure | Verdict — evidence |
|---|---|---|
| a | `chat.scss` carries ONE deleted shipped line (semantics-preserving selector widening); the "pure-append" phrasing must be qualified | **TRUE + QUALIFIED.** `git diff --numstat` = `+6/−1`. The deletion is `.toggle-btn.room-btn {` → `.toggle-btn.room-btn,\n.toggle-btn.rooms-browser-btn {`; the rule's property body is unchanged. The tester qualifies it explicitly: "the lane's blanket 'pure-append' phrasing is imprecise for the SCSS (append + one semantics-preserving selector rewrite)" (§G3b) and disclosure 3. `chat.html` +19/−0 and `chat.component.ts` +27/−0 are genuinely pure-append. |
| b | `/metrics` B4b headroom 134 B | **TRUE.** `/tmp/gcfe_g4_measure_head.json`: `metrics_route_fetch_set_lazy_only_bytes = 184866`; bound `threshold_B4b_route_fetch_set_lazy_only_bytes = 185000` (`bundle-baseline.json:43`, pinned by `metrics.bundle-guard.spec.ts:133`) ⇒ 185000 − 184866 = **134 B**. GREEN, effectively exhausted. |
| c | Popup-title product gap (the instances API exposes no `instance_name`) | **TRUE.** The instances response sets `title=instance_meta.get("title")` (`daemon/routers/instances.py:359/523/585`) and never an `instance_name`; the FE reads `instance.instance_name ?? instance.title` (`instance-list.component.ts:279`); the dialog renders `Chatrooms joined by {{ data.instanceName ?? 'this instance' }}` (`room-dialog.component.html:6`) — matching the disclosed fallback exactly. |
| d | W1 shipped-lane 5xx-copy delta (`room.service.ts:200/:247`) | **TRUE IN SUBSTANCE; the line anchors are stale (note N4).** Exactly three `describeHttpError` call sites, of which `loadRooms` and `loadMessages` are SHIPPED at base (`git show 0181dadbf:…/room.service.ts` → `:113`/`:152`) and `loadGlobalRooms` is NEW. The 5xx copy changed on all three; the typed 404/403/0 copies are unchanged. Verified live by the tester (leg (g), leak=false). |

Also re-derived independently: warn set **identical 18 == 18** (base vs HEAD warn lines are the same 18 warnings; the only substantive delta is the base-attributed `instance-list.scss` amount 10.17 → 10.61 kB); initial bundle `main-*.js` = **217,094 B** (exact byte count from `dist/frontend/browser/main-AUKSIQ4A.js`) = base; chunks **85 = 85**; total browser files **95 = 95**; initial total 5,909,596 B vs base 5,909,018 B (+578 B, inside the ±2048 B limit). A first pass appeared to show a 10th style warning at HEAD (`settings.component.scss`) — **a false positive of my own path-extraction**: it occurs only inside a sass-deprecation source location, never as a budget warning.

---

## 5. STAGED-READINESS — every fact verified

| Axis | Verdict |
|---|---|
| Migration census EMPTY | **VERIFIED.** `git diff --stat 0181dadbf..HEAD -- daemon/migrations/versions/` is empty; HEAD holds **133** `.sql` files; none authored by this lane (the newest, `20261116_083000_add_room_resolution_warnings.sql`, predates it). |
| `agents/` tree untouched | **VERIFIED** — diff empty. |
| No version bump | **VERIFIED** — diff over `pyproject.toml` / `daemon/constants.py` / `daemon/__init__.py` empty; `pyproject.toml:3` and `daemon/__init__.py:3` both read `0.31.0`. The release cut stays downstream. |
| No npm dep changes | **VERIFIED** — `git diff --numstat … frontend/package.json frontend/package-lock.json` is empty (0-byte diff), constraint 2 holds. |
| Bundle / warn set | **VERIFIED** — 217,094 B initial (0 delta), 85 chunks, 95 files, warn set identical 18/18 (details in §4). |

---

## 6. NON-BLOCKING NOTES

- **N1 — the "same-change-per-path" rule was not literally honoured.** The three paths (and their markers) landed in `6fe3ad005`; the allowlist entries landed one commit later in `c479cef98`. The branch tip was therefore RED between those two commits. The tester found, disclosed and closed exactly this (report §G3a "Live-tip finding", disclosure 10), and the final tree is green (39/39, re-run by me). Process deviation, disclosed and closed — not a blocker. Carry forward the rule for any future `frontend/` path.
- **N2 — the marker rule is file-wide for newly added sanctioned paths.** All three new entries are NEW files relative to the guard's fixed base (`858a85e31`), so each diffs as ONE whole-file hunk that contains the line-1 marker; I confirmed a marker-less edit *inside* the sanctioned proxy conf does not redden. This is a pre-existing property of the guard (identical for the two pre-existing e2e artifacts), acceptable here because these are gate artifacts outside the production graph — but worth recording, since the marker rule reads as per-hunk.
- **N3 — deviation (b) is also a shipped-lane change, and is not named as one.** The untyped `<500` path change applies to the shipped rooms and messages lanes too (`describeHttpError` call sites 1 and 2). The `(b)` entry names the change and its attribution but does not state that it, like W1, changes *shipped* copy. The promotion note should carry **(b)** alongside W1 as a shipped-surface delta (or the `(b)` entry should say so).
- **N4 — stale line anchors in the W1 disclosure.** `gate-evidence.md:236-238` and tester disclosure 7 cite the `describeHttpError` call sites as `:200/:247/:311`; at HEAD they are **`:212/:259/:342`** (the tidier pass inserted lines above them). The substance (two shipped lanes + one new) is correct; re-anchor both citations to the HEAD values. Same class applies to the council's own `code-review.md:34`, which is a frozen review artifact and may stand.
- **N5 — the fail-closed anchor branch does not invalidate an in-flight request.** `room.service.ts:305-317` returns before `++this.globalRoomsRequestId`, so a prior in-flight response can still land (its own `reqId` check still passes). Bounded impact: the template's `@else if (globalRoomsError(); as error)` branch (`:100`) wins over the list, so the fail-closed posture survives in the UI — but the `globalRooms` signal is left populated behind the error. Defence-in-depth only; consider incrementing the request id in the fail-closed branch.
- **N6 — `room.service.spec.ts` grew a lot.** The lane's own new-file count (552 + 297 lines across two specs) is proportionate to the surface. No action.
- **N7 — the credential-hygiene incident disclosed at report disclosure 11 is a harness defect, not a repo defect** (an unredacted grep echoed a live secret into a worker transcript; anomaly `ebaad48b`). It belongs to the standing user-gated rotation item and is correctly disclosed. No lane action.

## 7. WHAT I DID NOT DO

- Did not re-litigate any design decision, the plan, or the council's adjudications.
- Did not modify any product code, config, frozen artifact, or the ledger — the lane's tree is byte-identical to `3a76cbd0c` apart from `.agents/tidier/notes.md` (pre-existing agent runtime state) and this additive file.
- Did not run the daemon, the e2e suites, or the production build (I re-derived the bundle/warn facts from the tester's build evidence and the `dist` output already on disk, and re-ran the drift guard myself).

## 8. CLOSING PATH TO APPROVAL

Both blockers are additive edits to `gate-evidence.md` (the lane-owned ledger). `decisions.md`, `spec.md`, `code-review.md` and every other frozen artifact stay untouched. No code change, no re-test, no re-gate: the fix is (i) restore deviation **(a)** as an EXECUTED-and-PASS record with conditions (a)(i)/(ii)/(iii), and (ii) re-anchor the W1 pin sentence at `:279` to the shipped `<500` posture. Re-submit the ledger for a re-read.

---
*[Verification artifacts: drift guard re-run 39/39 at HEAD; independent counter-mutation 2F/37P with PRE==POST sha256 `db4703769794…`; committed-tree probe in a throwaway detached worktree (unsanctioned marker-bearing path ⇒ 2F/37P), worktree removed; bundle/warn facts re-derived from `/tmp/gcfe_g4_*` and `frontend/dist`; ledger/tester/code claims each grep-verified against the tree.]*

---

# Iteration 002 — LEDGER-ONLY RE-CHECK

- **Date:** 2026-11-18 · **Mode:** ledger-only re-check of `acbcd43e0` (`docs(gc-fe-surfaces): ledger correction — approver-001 blockers`; 1 file, +35/−19, zero code / frozen-artifact diff).
- **Scope:** the delta `acbcd43e0` only. No code re-review, no re-test (the ledger commit changed no product code and no test).
- **Basis:** iteration-001's two blockers (ledger-scoped) + notes N3 / N4 / N5.

## VERDICT (iteration 002): **APPROVED**

**Both iteration-001 blockers are closed, and the closure is exact.** The code was already sound and is unchanged; the gate record that the promotion note is written from is now truthful, self-consistent and complete on every point I raised. **The lane may proceed to giter merge + staged release. No live promote (commission constraint 5).**

*Iteration 001's `REJECTED` verdict is superseded by this approval for the lane as it now stands at `acbcd43e0`; it remains in the file as the historical record of why the ledger was corrected.*

## 1. BLOCKER 1 — deviation (a) restored and correctly dispositioned — **CLOSED**

| Requirement | Verification | Result |
|---|---|---|
| (a) restored **byte-for-byte** vs the pre-deletion state (`1079c7365^` = `e079725450c`) | Extracted the 7-line `(a)` block from `1079c7365^:…/gate-evidence.md` and from HEAD, then `diff` — **empty** (identical). Both blocks hash to `sha256 be2f9d06eb5aa7084406a2b3954fab74c5f97a66c208cedb4019ab7c353a6a39`, **the same sha the ledger publishes** for the recovered block. Byte-for-byte restoration is PROVEN, not asserted. | **PASS** |
| **(a)(i) verbatim** — "the promotion note must NOT cite the e2e gate as header-icon-adjacency evidence" | Present character-for-character inside the restored block (`gate-evidence.md:366-367`), and restated in the appended status update as **"STILL STANDS verbatim"**. The prohibition was restored as condition text, not paraphrased away. | **PASS** |
| **(a)(ii) satisfied** via the tester's daemon-fenced execution | Recorded at `:372`: artifact `6fe3ad005`, `frontend/e2e/group-chat-rooms-gate.daemon.spec.ts:89`, toggle cluster `watchover-btn \| room-btn \| rooms-browser-btn \| think-btn \| tools-btn \| system-btn` ⇒ `roomIdx=1`, `browserIdx=2`, **adjacency Δ=1**, PASS. Verified against the tree: `daemon.spec.ts:89` is a **live** `test(...)` with **no** `test.skip` — it is genuinely the executing row, not a skipped copy. | **PASS** |
| **(a)(iii) satisfied** — the stubbed row stays SKIPPED, reason-tagged, **not conflated** with the daemon-fenced PASS | `group-chat-rooms-gate.spec.ts:279` still carries `test.skip(true, 'adjacency requires the daemon-fenced leader gate — unit-pinned at chat.chatrooms.spec.ts (pins at :372,:401,:432,:457) and P2 cases')` — visible, explicit, reason-tagged. The appended **"Truth posture (do NOT conflate)"** paragraph states it explicitly: the stubbed row stays SKIPPED (fixture defect real and root-caused, spec byte-restored `sha256 0914adf0…`), the ADJACENCY EVIDENCE is the daemon-fenced execution, **"Neither may be presented as the other."** | **PASS** |
| the six named rows refreshed cleanly | `:4` HEAD anchor → `a1b2ce44c` with the "this ledger's own correction commit follows it" qualifier; `:65` dangling `(a)` reference → points at the restored EOF section; `:92` "deferred to the leader-run gate" → "**EXECUTED and PASSED** in the daemon-fenced run"; `:113-118` "DEFERRED-to-leader-gate" → "**RESOLVED — the daemon-fenced run EXECUTED the row and PASSED**" + "The stubbed config keeps its explicit skip — NOT adjacency evidence"; `:150` F-A table → "**SKIPPED (stubbed) / PASS (daemon-fenced)**" with the evidence named; `:339` Verify row → the stubbed skip kept, the daemon-fenced PASS named as the evidence. All six verified by reading the new text. | **PASS** |

**No stale adjacency claim survives.** A full sweep of all 17 `adjacen*` occurrences in the corrected ledger returns **zero** rows matching `deferred|outstanding|pending|must execute` outside the restored (a) block's own historical text (which is correct — it is the original condition wording, now followed by its satisfied status). The stubbed-config run is still cited at `:208` for the geometry rows only — never as adjacency evidence.

## 2. BLOCKER 2 — both false clauses gone, posture re-anchored — **CLOSED**

| Clause | Hits at HEAD |
|---|---|
| "a client-side status keeps the raw fallback" (old `:279`) | **0** |
| "The raw-message fallback is retained for <500 only." (second instance, old `:265` — caught by the worker, not by me) | **0** |
| any other claim that a raw-message fallback survives | **0** (a full `fallback` sweep returns no surviving raw-transport claim) |

Re-anchored to the shipped posture: the W1 section now reads "**NO branch consults the raw transport message** (see deviation (b) above)" and the pin sentence reads "the raw transport message is **NEVER consulted on EITHER branch** (>=500 or <500) — the TYPED 404/403/0 copies are byte-equivalent and pinned (deviation (b), :258)". This matches the tree exactly (`room.service.ts:604-608`: the `<500` path consults `raw.error` only) and matches the shipped spec pin (`room.service.spec.ts:845`, "an untyped `<500` status renders the fixed generic copy — no transport string, no URL"). The `:258` cross-reference resolves to the (b) entry.

## 3. NOTES — N3 / N4 / N5 dispositioned

- **N3 — CLOSED.** Deviation (b) is now explicitly named **"a SHIPPED-SURFACE delta, like W1"** and states that it applies to the same three `describeHttpError` call sites — the two SHIPPED lanes plus the NEW global lane — with the instruction "**the promotion note MUST carry (b) alongside W1 as a shipped-surface delta**". Exactly the disclosure the note asked for.
- **N4 — CLOSED.** The anchors are refreshed in both places: the W1 deviation entry (`:236-237` → `:212` rooms / `:259` messages / `:342` global, marked "HEAD anchors") and the fixed-constants table (`:271-273`). I re-derived them against the tree: `room.service.ts:212` = `describeHttpError(err, 'chatrooms')` (shipped `loadRooms`), `:259` = `'messages'` (shipped `loadMessages`), `:342` = `'global-chatrooms'` (new `loadGlobalRooms`) — **all three correct**.
- **N5 — DECLINED BY SCOPE, accepted as a carried advisory.** The fail-closed anchor branch (`room.service.ts:305-317`) still returns before `++this.globalRoomsRequestId`, so an in-flight prior response can still land behind the error state. I confirmed the code is unchanged in this respect. Deferring it is correct scope: N5 is a **code** advisory, not a ledger claim, the ledger makes no assertion about it (grep: zero mentions — nothing is falsely marked fixed), and its impact is bounded — the template's `@else if (globalRoomsError(); as error)` branch (`:100`) wins over the list, so the fail-closed posture survives in the UI. **Carry it as an open advisory into the lane backlog**; it must not be silently closed by this ledger pass.

## 4. PRESERVATION — verified

| Check | Result |
|---|---|
| W3 leader correction intact **verbatim** | **PASS** — the 9-line W3 block diffed byte-for-byte against `a1b2ce44c` (empty diff). |
| F-E erratum intact **verbatim** | **PASS** — the 4-line F-E block diffed byte-for-byte against `a1b2ce44c` (empty diff). |
| First 358 lines **zero-shift** (append-only at EOF) | **PASS** — 358 → 374 lines; all 9 in-place hunks have **equal old/new line counts** (`@@ -1,7 +1,7 @@`, `-62,7 +62,7`, `-89,7 +89,7`, `-110,12 +110,12`, `-147,7 +147,7`, `-233,8 +233,8`, `-255,28 +255,28`, `-336,7 +336,7`) and only the final hunk appends (`@@ -356,3 +356,19 @@`). No line number moved, so **every pre-existing line citation inside the ledger — including the F-E erratum's own `:63` and `:167` — still resolves to the same content** (both spot-verified). |
| Frozen artifacts untouched | **PASS** — `decisions.md` `6d156bc7…`, `spec.md` `ef3ad1bf…`, `code-review.md` `aaeedd40…` — all sha256-identical to their pre-approval values; `plan-review.md` and `approval.md` likewise unchanged. |
| Diff strictly confined to `gate-evidence.md` | **PASS** — `acbcd43e0 --name-only` lists exactly one file; non-ledger files changed in the commit: **0**; `git diff --stat a1b2ce44c..HEAD` touches only `gate-evidence.md`. |
| Drift guard still green at the new tip | **PASS** — re-run at `acbcd43e0`: **39 passed**. The `frontend/` diff against the guard's fixed base is unchanged by the two documentation commits. |

## 5. NON-BLOCKING NOTE (iteration 002)

- **N8 — `:66` retains the pre-correction tense; its pointer target `:208` was likewise not refreshed.** `gate-evidence.md:66` still reads "the skip is the header-adjacency row (explicit, reason-tagged skip **owned by the leader's daemon-fenced run**)" and points at the Revival-3 Verify table (`:205`), whose playwright row (`:208`) reads "4 passed, 1 skipped (35.1s), zero failures; geometry 1050×590 px ×2" without naming the daemon-fenced PASS. This bullet was **not** in the leader's refresh list, and it is **not** a false claim — every clause in it is true (the stubbed run did yield 4 passed / 1 skipped; the skip IS the adjacency row and IS reason-tagged; the daemon-fenced run IS that row's owner, and did execute it). It is a **stale-tense cross-reference**, and it is the one place a reader could still infer that the daemon-fenced run is outstanding. It does **not** contradict the record: the load-bearing statements are refreshed in five places (`:92`, `:113-118`, `:150`, `:339`, and the restored (a) section at `:360-374`), and the prohibition (a)(i) is restored verbatim, so a promotion-note writer reaches the correct conclusion and cannot cite the stubbed gate as adjacency evidence. **Suggested one-clause fix (optional, no re-review needed):** append "— since EXECUTED daemon-fenced (Δ=1, `6fe3ad005`); see 'Deviation (a) RESTORED' at EOF" to `:66`, and add the same pointer to `:208`.

## 6. CLOSING

Iteration-001 blockers 1 and 2 are **closed with exact, independently verified restorations**. The ledger is now truthful on every point I raised, self-consistent, append-only in structure, and confined to the lane-owned file; the frozen artifacts, the code, the tests and the drift guard are all untouched and green. **APPROVE — the lane moves straight to giter merge + staged release (no live promote).** N3 and N4 are closed; N5 and N8 are carried as open, non-blocking advisories.

*Evidence: byte-for-byte restoration diff + sha match (`be2f9d06…`); W3/F-E block diffs (empty); hunk-header zero-shift audit; frozen-artifact sha comparison; `git show --name-only` confinement; 0-hit greps for both false clauses; anchor re-derivation against `room.service.ts`; live/skip inspection of both e2e specs; drift guard re-run 39/39 at `acbcd43e0`.*

## 7. POST-MERGE RE-VERIFICATION (concurrent `origin/latest` absorption)

While this re-check was in flight, a merge landed on the branch between the ledger fix and this commit — `fafa7adc9 merge: absorb origin/latest drift (0181dadbf..3a8ce7c56, v0.31.1+v0.31.2 lanes) into gc-fe-surfaces pre-release` (parents `acbcd43e0` + `3a8ce7c56`; 281 files, +31442/−1118). Because it moved the tip **under** an approval, I re-verified the approval still holds at the post-merge tree rather than assume it:

| Check at the post-merge tip | Result |
|---|---|
| Lane-owned files touched by the merge | **1** — `daemon/routers/rooms.py`, and its ONLY change is **one docstring word** in the module header ("The Emperor's P6 UI direction" → "The user's P6 UI direction"); 1 changed content line, **zero behavioural lines**, so every `rooms.py` line anchor the ledger cites is unmoved. |
| `frontend/` files touched | **0** ⇒ the lane's FE surface, the 217,094 B initial bundle, the 85 chunks, the 18/18 warn set and the B4b 134 B headroom are all unaffected by the absorption. |
| The lane's own planning dir (`.agents/shared/planning/gc-fe-surfaces/`) | **0 files touched** — `decisions.md` `6d156bc7…`, `spec.md` `ef3ad1bf…`, `code-review.md` `aaeedd40…`, `gate-evidence.md` `d0c4c57b…` all sha256-identical to their pre-merge values. |
| Drift guard at the post-merge tip | **40 passed / 0 failed** (the merge added one other lane's `daemon/constants.py` carve-out test; the FE-UI sanction — allowlist, markers, `FRONTEND_UI_LANE_SANCTIONED_BASE`, file-state compensator — is **untouched** by that change). |
| The lane's own BE gate at the post-merge tip | `tests/unit/routers/test_rooms_list_api.py` → **41 passed** (unchanged). |
| `room.service.ts` anchors after the merge | **`:212` / `:259` / `:342` — unchanged** (the file was not touched by the merge). |

**The merge does not invalidate this approval.** It also required no change to either blocker fix.

- **N9 — the ledger's `:4` HEAD anchor now precedes the merge.** `gate-evidence.md:4` reads `HEAD: a1b2ce44c … this ledger's own correction commit follows it`; after the absorption it is followed by that commit **and** by `fafa7adc9`. This is **not** a defect for the ledger's subject — the merge changed zero `frontend/` files and zero behavioural lane lines, so `a1b2ce44c` genuinely identifies the code state the ledger describes — but the anchor is two commits behind the tip. **Optional refresh (no re-review needed):** restate `:4` as the lane's code state rather than a sha, or point it at the merge sha with a note that the merge carried no lane-code or frontend change.

## 8. FINAL DISPOSITION (iteration 002)

**APPROVED.** The ledger correction `acbcd43e0` closes both iteration-001 blockers exactly and completely, with every restoration independently proven byte-for-byte rather than asserted; the three named notes are dispositioned (N3 closed, N4 closed, N5 correctly declined by scope and carried as an open advisory); the frozen artifacts, the code, the tests and the drift guard are untouched and green; and the concurrent `origin/latest` absorption was re-verified to change nothing this approval rests on. **The lane proceeds to giter merge + staged release — no live promote (commission constraint 5).** Open, non-blocking advisories carried forward: **N5** (fail-closed branch does not invalidate an in-flight request), **N8** (`:66` / `:208` stale-tense cross-reference — one-clause pointer optional), **N9** (`:4` anchor predates the merge).

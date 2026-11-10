# Risk Register — prompt-assent

**Commission:** `prompt-assent` — the conversational approval lane (*The Emperor's Word / Assent*).
**Doctrine:** [`doctrine.md`](doctrine.md) — *"FE-UI is a deeper view; the working path is conversation. The Emperor's word, relayed by C2 with provenance, is a first-class approval lane."*
**Companion:** [`decisions.md`](decisions.md) · [`plan-overview.md`](plan-overview.md)

| Field | Value |
|---|---|
| Date | 2026-11-10 (UTC) |
| Worktree | `/home/nea/ensemble-src-wt-prompt-assent` (branch `feature/prompt-assent`, base `0efdf106f`) |
| Sources | `technical-analysis.md` §12 (12 risks), §5, §2, §4, §9; `research/trust-anchors.md` §2, §3; `research/approval-path.md` Q1, Q5 |

---

## Part 1 — Security risk table

Each row maps a threat to the **design point** that addresses it and the **concrete mitigation**
the implementation must carry. "Design point" refers to `decisions.md`.

| # | Threat | Attack description | Design point | Mitigation (must be implemented) | Residual |
|---|---|---|---|---|---|
| **S-1** | **Spoofing** — a non-human obtains an approval | An agent (or a compromised agent) manufactures the appearance of a human turn, or asserts an approver identity it does not own | **BLESSED-2** (3 factors) + **D-8** (attestation) | (i) Factor 2 is armable **only** by `classify_user_origin` (`daemon/tools/upgrade_journal.py:4116-4166`) — the trust anchor is **not** forked; (ii) the window is **cleared** on every non-user-origin dispatch (`daemon/manager.py:6132-6133`), so an agent-originated turn can never inherit an earlier turn's authorization; (iii) the approver identity is derived from **daemon-owned** evidence (message row `source` + adapter metadata), never from tool arguments (R3 analogue, `daemon/repositories/prompt_version/repository.py:602-604`); (iv) the attestation is **not constructible from arguments** — only the assent service mints it, after the gate passes | An agent with in-process code execution (`bash`) could import the attestation module — true of every in-process authority in this tree. The relied-upon boundary is the **three-layer access posture**, not module importability |
| **S-2** | **Replay** — a valid token is reused | The Emperor's token (or a captured one) is echoed again, on the same row or a different row, later or in another turn | **BLESSED-6** (single-use) + **D-4** (transition-then-consume) + **D-1** (token row) | (i) The state-machine CAS (`UPDATE … WHERE version_id=? AND state='pending_approval'`, `daemon/repositories/prompt_version/repository.py:1459-1464`) makes the row **terminal** after one decision — a second approve is a benign `409 ALREADY_APPROVED` analogue; (ii) the token is **consumed only on a successful transition**, and the consume records `consumed_at` + `consumed_by_message_id`; (iii) the token row is bound to the **item** (`version_id`, `agent_id`, `version_tag`, `artifact`, `version`) → a token replayed against a different item is `token-item-mismatch`; (iv) **consumed rows are RETAINED** so "already used" stays answerable (mirroring the promote GC's "keeps one consumed entry", `daemon/tools/upgrade_journal.py:1931-1934`) | Replay of a *superseded* token is refused with `token-superseded`, which is deliberately **distinguishable** from `token-already-used` — an audit reader must understand the difference |
| **S-3** | **Token leak** — the token escapes | The token appears in a transcript, a log, a screenshot, an audit payload, or another agent's context | **BLESSED-3** (content-free provenance) + **D-1** (hash-only storage) | (i) The DB stores **`token_hash = sha256(token)` — never the raw token**; the raw token lives only in the mint response shown to the Emperor; (ii) the audit payload carries **`token_fingerprint = sha256(token)[:12]`** only, mirroring the content-free `body_sha256` discipline (`daemon/repositories/notify_audit/models.py:127-129`); (iii) every terminal path emits **ONE frozen INFO line** — counts and flags only, **never persona or diff content** (`daemon/tools/prompt_reload.py:113-135`); (iv) the tool's own outputs must never echo the raw token into a shared ledger | The token is short enough for a human to read aloud / screenshot. This is accepted: the token's job is **anti-confusion**, not anti-disclosure (see D-3) — factors 2+3 are the real boundary |
| **S-4** | **Relay-agent abuse** — Ari is turned into an attacker | A prompt injection in Ari's context makes it mint tokens, present a fabricated diff, or approve without a human turn | **BLESSED-6** (three factors always) + **J-3** (content-hash binding) | (i) **All three factors are always required** — Ari cannot supply factors 2 and 3: it cannot arm the user-origin window and it cannot put the token into a human-authored message; (ii) the token is bound to the **item**, and (with **J-3**) to the item's **content hash**, so a mutated pending row invalidates the token; (iii) the **re-mint must re-show the diff** — a re-mint without the diff would present a bare token, and the Emperor would have no way to know what he is authorising; (iv) the item identity, the diff, the token and the expiry are all carried in the mint response | Ari remains a **confused deputy by construction**. The lane cannot make Ari trustworthy; it can only make Ari **unable to produce a valid approval without a human turn**. The full ledger chain (token ↔ item ↔ message ↔ human ↔ relay agent) is what makes an abuse detectable after the fact |
| **S-5** | **Self-approval** — an agent approves its own prompt | Ari (or any agent) causes a PENDING row — including **Ari's own** `soul`/`rule`/`workflow` — to be approved without a human | **D-8** (new lane + attestation) + **BLESSED-6** (no self-approval path) | (i) `approve()`/`reject()` **refuse internal actors** (F4) — `daemon/repositories/prompt_version/repository.py:1560-1565`, `:1594-1599` — and **stay that way**; the new lane uses a **separate entrypoint** that requires a typed `AssentAttestation`, not the old one; (ii) the attestation is minted **only after** the 3-factor gate passes, and carries the human message id + human identity derived from daemon state; (iii) a belt-and-braces assertion refuses an attested human identity that resolves to the relay agent or a reserved agent identity — refusal code `self-approval-attempt`; (iv) **the relay path never goes through HTTP** (D-8 option (b) rejected), so there is no session-authenticating endpoint an agent could call | **`self-approval-attempt` is largely UNREACHABLE by construction** — an agent-originated message cannot arm factor 2. It is an **assertion, not a load-bearing gate**. The plan must not mistake it for the real defence |
| **S-6** | **Audit tampering** — the record is altered or lost | An existing audit event is overwritten, deleted, or the new event is not written / is written outside the state-change transaction | **BLESSED-3** (never overwrite) + **DECIDED-1** (payload-only) + **Structural Pin SP-2** | (i) `prompt_audit_events` is **append-only** — `_append_audit_in_conn` INSERTs (`:1502-1511`, impl `:3036+`) and nothing in the tree UPDATEs or DELETEs an audit row; the lane adds keys to a **new** row and touches nothing else; (ii) the audit row is written **inside the same `engine.begin()` as the state UPDATE** (`daemon/repositories/prompt_version/repository.py:1457-1511`) — the provenance and the state change commit together or not at all; (iii) a **grep-verifiable pin** forbids any UPDATE/DELETE against `prompt_audit_events` in this lane (SP-2) — an invariant only a pin keeps; (iv) a **ledger-write failure is fail-closed** (the `notify_user` precedent: *"ledger-write failure ⇒ fail-closed"*, `daemon/tools/notify_user.py:95-97`) — refusal `audit-unavailable`, **no** state change | A future refactor that moves the audit write **out** of the transaction would let provenance and state diverge. That is exactly what pin (iii) exists to catch |

---

## Part 2 — The twelve risks surfaced by the technical analysis

`technical-analysis.md` §12. The **top three** are called out first and explicitly, because they
are the ones most likely to be forgotten once the code is written.

### ⚠ Top three

| # | Risk | Impact | Mitigation | Where it lands |
|---|---|---|---|---|
| **R-1** | **The lane can change Ari's own guardrails.** A chat approval can replace `soul`/`rule`/`workflow` for **any** agent, including Ari. The three factors make this *deliberate*, but the blast radius is a **self-modifying trust boundary**. | **High** — the trust model is self-referential | The item is bound to a token shown **with the diff**; the ledger records the full chain (token ↔ item ↔ message ↔ human ↔ relay agent). The lane is **ari-EXACTLY** and privileged, so it is never default-granted. **D-6**'s freshness bound narrows the window in which a stale authorization can be used. `D-2` (kill-switch default) is the architect's explicit lever to keep the lane dormant until first use | `plan-overview.md` → Open Questions (D-2); `risks.md` Part 1 S-1, S-4 |
| **R-2** | **Window staleness via silent resume.** The dispatch funnel **skips the stamp on a silent resume** (`daemon/manager.py:11503`: `if not silent:`) — *"Silent resume is the ONLY skip (no message is injected; the window keeps the original turn's)"*. A paused→resumed Ari can hold a window stamped in an **earlier** turn. | **High** — an authorization from turn N can authorize an action in turn N+k | **D-6 / J-2** — require `window["stamped_at"]` to be within **≤10 minutes** of the tool call, in addition to `expires_at`; refusal `user-origin-window-stale`. This is a **cheap fourth check** that fails closed | `decisions.md` D-6, J-2; `phase3-plan.md` |
| **R-3** | **Guessing loop** — a 30-bit token plus an attacker who can keep trying | **Medium** — mitigated but not eliminated | **D-3 / J-1** — 8 chars / 40 bits (house value), **or**, if 6 chars is kept: a **per-item attempt counter**, **lockout at 5 mismatches** (the live token is revoked → a fresh mint, and therefore a **fresh human turn**, is required to clear it), and a **global per-relay-instance bound** (≤10 mismatches/hour) so a many-item sweep is bounded even when each item is under its threshold. **Refusals never burn the token** (house doctrine: *"nonce NOT burned"*, `daemon/tools/upgrade_tools.py:3708-3777`) | `decisions.md` D-3, J-1; `phase1-plan.md` (token store) |

### The remaining nine

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| **R-4** | **The window is in-memory** (`daemon/manager.py:2001`): a daemon restart between mint and reply wipes the factor-2 evidence → the reply fails closed with `no-user-origin-turn` | Medium | Correct direction, but it **looks like a user error**. The refusal text must say *"daemon restarted since the token was issued"* — the promote lane has the same wording (`daemon/tools/upgrade_tools.py:3524-3530`) |
| **R-5** | **The factor-3 read can fail** if the message row was wiped at boot | Medium | `assent-verification-unavailable` — **fail closed** on an unreadable row (promote analogue, `daemon/tools/upgrade_tools.py:3524-3530`) |
| **R-6** | **Two repos, two tables, two lane predicates** (D-7) — a partial implementation could leave one surface with a *different* trust rule than the other | Medium | Phase 1 ships the **classic `prompt_versions` surface only**; the persona surface is **explicitly deferred** and **must not** be half-wired. Whatever ships, the two surfaces must not diverge silently. If the architect rules that both ship (D-7 override), the persona lane needs its **own** ledger — persona rows write **no** `prompt_audit_events` |
| **R-7** | **Boot-abort exposure** from any `_ensure_postgres_columns` entry added for this lane | High | **Avoided** by choosing a new table (D-1): a brand-new table rides the `create_all` seam (`daemon/api.py:1142-1143`) and needs **no** ensure entry. The ensure list is **one transaction for the whole statement list** with no per-statement try/except (`daemon/manager.py:10036-10038`) — any entry's failure aborts boot. **Any later "just add a column" change re-opens this** and must be treated as a boot-abort surface |
| **R-8** | **The SAME-PR pin census prose is stale** (`daemon/tools/_tool_registry.py:379-406`) — it enumerates 14 pin files, but grep finds a **15th** (`tests/unit/tools/test_prompt_reload_pins.py:71`), which the prose does not list | Medium — the next lane that copies the prose will miss it and ship a red tree | **Derive the pin set by grep at implementation time** — never copy the comment. The comment itself is corrected **in this change** (it is the artifact that gets copied forward). The three canonical greps are in the registry prose (`daemon/tools/_tool_registry.py:412-417`) |
| **R-9** | **Audit completeness** depends on the payload extension landing in the *same transaction* as the state change (`daemon/repositories/prompt_version/repository.py:1493-1511`). A future refactor could move the audit write out of the transaction | High | A **pin** is warranted (SP-2): assert that the relay payload keys and the state UPDATE are written under one `engine.begin()`. Audit completeness is the doctrine's "with provenance" clause — it must not be refactored apart from the state change |
| **R-10** | **Ari is a confused deputy by construction** — it renders the diff and the token and relays the Emperor's reply. A prompt-injected Ari could present a **stale** or **truncated** diff | High | **J-3 — bind the token to a content hash of the pending row**, so a mutated pending row invalidates the token. Cheap: one hash column on the token row, computed at mint time and re-checked at approve time. **Flagged for the architect** — it is an addition **beyond** the commission's three factors |
| **R-11** | **The pending list is cross-agent.** Ari sees every CORE pending row, including other agents' drafts | Medium | Inherent to the feature. The tool's **INFO line must stay content-free** (`daemon/tools/prompt_reload.py:113-135`); the list body goes to the Emperor's chat, not to the daemon log |
| **R-12** | **E2E acceptance representability.** The owed **T3.5 re-stamp** (`agents/ari/tools_note.md` — VERIFIED `grep -c memoir_about` = **0** today, matching `.agents/tester/RESULTS/2026-11-10-memoir-v2-gate.md:178,278`: *"re-stamp verified NOT applied (versioned-prompt-artifact lane, human session only)"*) is exactly the shape this lane is for | Medium | The acceptance must be **representable** as a **planned acceptance step** (see `plan-overview.md` → Acceptance E2E). It must **not** be executed in this planning lane — the artifact write path is human-session-only by design (`daemon/tools/prompt_artifacts.py` `check_write`, def at `:265`) |

---

## Part 3 — Structural pins

Two invariants that only a **grep-verifiable pin** keeps. They are not tests of behaviour; they
are tests of **structure**, and both must land in the same PR as the code they protect (the
SAME-PR rule, `daemon/tools/_tool_registry.py:366-417`).

### SP-1 — Content-hash binding of the token to the pending row (anti confused-deputy)

**Invariant.** A token minted for a PENDING row is valid **only** for the exact content that was
shown to the Emperor with that token. If the pending row's content changes between mint and
approve, the token is **invalid** (refusal `token-content-mismatch`).

**Why a pin and not just an implementation.** The protection is a *property of the schema +
mint/approve flow*. A future "just refresh the pending content" change would silently remove the
protection, and nothing else in the tree would fail. The pin asserts the **column exists**, that
the mint path **computes** it, and that the approve path **re-checks** it.

**Depends on:** **J-3** — the architect must rule this in or out of Phase 1 scope.

**Pin shape (illustrative — derive the exact grep at implementation time).**
- `prompt_assent_tokens` has a `content_hash` column.
- The mint path writes a **non-empty** `content_hash` for every token row.
- The approve path compares the token row's `content_hash` against the pending row's **current**
  content hash and refuses on mismatch — *before* the state transition.

**Mutation note.** The pin must be **non-vacuous**: a counter-mutation (e.g. removing the
re-check) must redden it. An absence-only grep ("the string `content_hash` exists") is
prose-satisfiable and proves nothing — see the house mutation rule.

### SP-2 — No UPDATE / DELETE ever touches `prompt_audit_events`

**Invariant.** Nothing in the prompt-assent lane — or, ideally, anywhere in the tree — UPDATEs or
DELETEs a row of `prompt_audit_events`. The lane **appends**; it never rewrites history.

**Why a pin.** "NEVER overwrite existing events" is an **invariant** — an absence. Absences are
invisible to behavioural tests (the lane's happy path never overwrites anything, so a green test
suite says nothing). Only a structural pin keeps it. This is the same reasoning the house applies
to the "residue-gate ZERO needs an allowlist row" rule.

**Pin shape (illustrative — derive the exact grep at implementation time).**
- A tree-level grep over the lane's files (and, preferably, the whole prompt subsystem) finds
  **no** `UPDATE`/`DELETE` against `prompt_audit_events`.
- The lane's own write path is an **INSERT** through `_append_audit_in_conn`
  (`daemon/repositories/prompt_version/repository.py:1502-1511`, impl `:3036+`).

**Mutation note.** Same rule as SP-1: the pin must redden under a counter-mutation that inserts
an UPDATE against the table. A "there is no UPDATE in the file" check that passes because the
file is empty is vacuous.

---

## Part 4 — Risk summary for the architect (one page)

| Theme | Top risk | Is it in our control? |
|---|---|---|
| **Authorization** | The lane changes live personas — including Ari's own | Partly. D-2 (kill-switch default) is the architect's lever; the 3 factors are the hard floor |
| **Freshness** | A window from an earlier turn can authorize a later action | Yes — D-6/J-2's 10-minute bound |
| **Entropy** | A short token is guessable | Yes — D-3/J-1 (8 chars) plus the attempt counter + lockout |
| **Confused deputy** | Ari presents a stale diff | Yes — J-3's content-hash binding |
| **Durability** | Evidence vanishes (daemon restart, wiped message row) | Fail-closed by design; the refusal wording must explain it (R-4, R-5) |
| **Boot safety** | A schema change aborts boot | Yes — D-1's new-table rule avoids the ensure list entirely (R-7) |
| **Divergence** | The classic and persona surfaces end up with different trust rules | Yes — D-7's explicit deferral (R-6) |
| **Audit integrity** | Provenance and state change drift apart | Yes — SP-2 plus the same-transaction write (R-9) |
| **Process** | The pin census prose is stale and misleads the next lane | Yes — derive by grep, and fix the prose in this change (R-8) |

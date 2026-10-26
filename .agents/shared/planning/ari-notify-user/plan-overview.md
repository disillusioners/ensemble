# plan-overview.md — `notify_user` (Ari voice)

**Lane:** `feature/ari-notify-user` @ worktree `/home/nea/ensemble-src-wt-notify-user`, base **`7af1d2632d`** == origin/latest (rebased 2026-10-25; zero commits carried; untracked planning dir preserved).
**Fold provenance:** design FROZEN — `architecture-recommendation.md` (D1–D12) + three leader adjudications; design review APPROVE-WITH-ADVISORIES **conditional on this fold**. This overview is a PURE FOLD — no new decisions, no re-planning. **Fold 2 (2026-10-25):** approver iteration-001 REJECTED (2 blockers + 12 notes, all fold-level — `.agents/approver/ari-notify-user-tracking.md`); leader rulings **B1/B2 + N1–N12 folded verbatim** — see `decisions.md` §6. Companion files: `decisions.md` (frozen rulings verbatim + corrections C3/W1–W11 + advisories), `architecture-recommendation.md` (full analysis + edge census + trade-off matrix), `research-a/b/c` (evidence base, file:line).

---

## 1. Objective

The user asked for a small, deliberate voice (commission quote): *"with alarm you should have a small tool, Ari. If you see the next message need user/me -> you can invoke message to discord, if not do in silence."* Today, non-user-originated Ari turns (self-alarm wakes, scheduled fires) have **no chat-delivery path** — by design: their dispatch source is the colon-less `"scheduler"`, so the dispatcher's routing gate (`":" not in source → return`, dispatcher.py:531-535/:691-695) silently discards the response while the session's chat binding (`instance_metadata["original_source"]`, write-once, spawn-inherited) survives unread. The 06:00 alarm fired, Ari finalized the full morning report, the report exists in the job record — and the user saw nothing in Discord until they pinged. Watch-event turns, by contrast, deliver because their wake source is inside the internal-report substitution family (message_processing_pipeline.py:739-794).

This plan delivers ONE focused privileged tool — **`notify_user`, ari-only** — that lets Ari deliberately break silence when the content needs the user and stay silent otherwise. The destination is structurally bound to the session's registered chat source (the signature takes the MESSAGE, never a destination); **nothing auto-sends**; the board remains the ALWAYS surface and notify is the MAY voice. Scope fence: one tool, not a messaging subsystem rewrite; no changes to watch-event or user-originated delivery; no new agent; no push (stage-only).

## 2. Requirements coverage (1→10, reviewer-verified; accepted deviations STATED)

| # | Requirement | Coverage (frozen ruling) | Accepted deviation |
|---|---|---|---|
| 1 | ARI-only allow-list | D7: new privileged category `notify-user`; ari EXACTLY; three-layer gate (category default-deny + `agents/ari/meta.json` allow + in-body `agent_id=="ari"` gate closing the literal-name bypass) | — |
| 2 | Source-bound, message-only signature | D1: resolution chain instance → root fallback → `original_source` → registry → adapter; no destination parameter exists; mapping-table reverse lookup never used | — |
| 3 | Callable from ANY Ari turn; deliberate opt-in | D3/D4: mid-turn direct `adapter.send` inside the never-block wrapper; nothing auto-sends | **(a)** N=1/turn implemented as a **≥60s per-instance interval proxy** |
| 4 | 2000-char consistency with the adapter | D6: tool-side cap BEFORE resolution/dispatch; body param only | **(d)** **2000-char single-chunk policy** (adapter split absorbs formatter headroom; residual disclosed) |
| 5 | Rate-limit / backpressure guard | D5 + **C3** + **B2**: N and M from the `notify_audit` ledger, **commit-before-count** (durable backstop) serialized by a **per-destination-key asyncio.Lock held across the entire gate decision**; restart-durable | **(a)** as above; **(c)** M=6/rolling-hour **per RESOLVED DESTINATION** (`target_hash`), not per session |
| 6 | Full audit trail | D8: `notify_audit` Tier-2 write-ahead; refusals ARE ledgered (the refusal is the security signal); body sha256+length, never cleartext; ledger-write failure **FAIL-CLOSED** — no ledger row, no send, typed refusal `notify_audit_unavailable` (N2) | — |
| 7 | Failures surface / bounded retries / never blocks | D9/D10/D11: typed REFUSED/FAILED envelopes with delivered-vs-unknown explicit; 1 transient retry; tools never raise | **(b)** 10s never-block wrapper scope = **`wait_for(preflight+send+retry, 10.0)`** (W4 — ONE bound everywhere) |
| 8 | Doctrine amendment + re-stamp | D12 + W7: 3 shipped docstring sites + 4 sealed prompt passages + test phrase-pins; 161-row manifest re-stamp + `SEALED_SHA_BASELINES` re-pin SAME PR | — |
| 9 | Unit + boot smoke, no real-LLM | §4 test strategy below (W10 mutation tests + W11 fences + boot smoke) | — |
| 10 | Stage-only, no promote | §5 posture below | — |

## 3. Phases (folded from architecture §8 + reviewer fold items)

| Phase | Name | Objective |
|---|---|---|
| 1 | Tool implementation | `daemon/tools/notify_user.py` per §8 items 1/3/4 (N10): factory + `NOTIFY_AUTHORIZED_AGENT="ari"` + `NOTIFY_CODE_*` frozen-set (+ fold-2 codes `notify_audit_unavailable`, `notify_disabled`) + decorator order (`@register_tool_category` ABOVE `@tool`); resolution chain per D1 (reuse `_resolve_lineage_root`-style walker, depth-capped); pre-flight per D9 order; ledger gates per **C3 commit-before-count spec** (interval = explicit read-before-insert; accepted INSERT commits in its own txn; COUNT in a fresh statement) **serialized by the B2 per-destination-key asyncio.Lock held across the ENTIRE gate decision**, with the **N3 budget predicate** (ONLY accepted rows consume M; ALL refusal classes ledgered, never consume budget) and **N2 FAIL-CLOSED ledger** (no ledger row → no send; typed refusal `notify_audit_unavailable`); **ONE 10s bound** `wait_for(preflight+send+retry)` per W4 with backoff from remaining budget; single transient retry per D10; result contract per D11; config in `NotifyUserConfig` (env prefix `ENSEMBLE_NOTIFY_USER_`, N8) incl. the **N9 kill-switch** (`enabled` default true; `ENSEMBLE_NOTIFY_USER_ENABLED=false` → typed refusal `notify_disabled`) |
| 2 | Registration + pin ceremony (SAME PR) | Per D7: `PRIVILEGED_TOOL_CATEGORIES` entry + `DYNAMIC_TOOL_NAMES` + `KNOWN_TOOL_NAMES` + `CATEGORY_MODULES["notify-user"]`; `agents/ari/meta.json` allow; factory extend BEFORE help/scan/filter; ALL pin bumps in ONE commit — **15→16 post-rebase** (W5: cu removal made it 16@old-base→15@tip) — **re-grep ALL pin sites on the TIP tree at implementation time** (the registry file itself warns never trust a plan's count; W6: reconcile to the **full 12 pin sites, not 9**); new `tests/unit/test_notify_user_registration.py` (instance_ops shape + mutation-verified leg) |
| 3 | Audit ledger | `notify_audit` SQLModel table + ONE migration `.sql` per D8 (unique timestamp prefix; `CREATE TABLE IF NOT EXISTS`; TEXT on both drivers; no inline semicolons in comments) + W1 (`target_hash = sha256(source_id + ":" + external_user_id)`) + W2 **CLOSED by N4** (crash-orphaned `accepted` rows DO count toward M — conservative, under-send biased, never over-send; lightweight sweep marks stale `accepted` rows as terminal `delivery_unknown` — they read as unknown, not false deliveries) + W3 (`source_id` nullable OR post-resolution-only ledgering); **REQUIRED (N1):** import the `NotifyAudit` model BEFORE `metadata.create_all` (factory.py:72-77 pattern) + cover the `daemon/api.py:991-998` late-import path — a missed import = no `notify_audit` table on PG = ledger INSERT fails = budget+audit break; rollback adjudication recorded at stage |
| 4 | Doctrine amendment + re-stamp (SAME PR) | Per D12 + W7: reword the 3 `self_alarm.py` docstring sites (cite by quoted phrase + "as of 7af1d2632d"); amend `tools_note.md` + `workflow.md` passages (AMEND workflow.md patrol line, don't delete); author the `## Notify-User` `tools_note.md` section; update the `test_self_alarm_registration.py` phrase-pins; regenerate the 161-row `agents/.prompt-artifacts.sha256` manifest + re-pin the `SEALED_SHA_BASELINES` row(s) in the SAME commit with dated attribution; **rule.md re-pin precondition** checked (tripwire covers workflow.md AND rule.md); **N11/N12 docstring steering clause** (docs-only: on wake shapes that already deliver, prefer silence — pair the ≥60s interval-proxy disclosure in the SAME docstring block) |
| 5 | Unit test surface | §8 item 7 (N10): registration pins; resolution-chain edge census 1–13 (architecture §4); rate-gate commit-before-count under the **B2 lock** incl. restart-durability sim + **N3 budget predicate** (every refusal class) + **N4 sweep** (stale `accepted` → terminal `delivery_unknown`); timeout path (delivery-unknown envelope, never retried); **N2 fail-closed pin** (ledger write fails → NO send, `notify_audit_unavailable`) + **N9 kill-switch pin** (`notify_disabled`); ari-only literal-name bypass negative control; doctrine phrase-pin updates; **W10 security-property mutation tests** (see §4) |
| 6 | Boot smoke | The §4 **RULED mechanism (B1)**: scratch TELEGRAM `source_configs` row + aiohttp sink (getMe + sendMessage) + PYTHONPATH base-URL shim; RUNNING precondition (pre-RUNNING refusal = negative-case assertion); pinned end-to-end sequence |
| 7 | Stage-only | §5 posture: re-derive version from live census at stage time; stage; **NO promote** |

## 4. Full test strategy

**Unit packs** (`tests/unit/*.py` + `test/packs/*_test.sh` wrappers per house conventions; PACKS.md ledger rows):
- Registration pins (Phase 2): category membership, exact-set allow `{"ari"}`, count **15→16 post-rebase** (re-grepped on tip), extends-order, AST discovery; new registration pin carries a mutation-verified leg (planted-defect → RED → restore → GREEN).
- Resolution chain: edge census 1–13 from architecture §4 (API-origin refuse, scheduler-only session, child-mid-turn, disabled adapter, circuit open, unregistered source, terminal-revived, size, rate, timeout, definite failure, internal error).
- Rate gates per C3 + **B2** + **N3** + **N4**: commit-before-count under the per-destination-key asyncio.Lock; budget predicate (**ONLY accepted rows consume M — ALL refusal classes ledgered, none consume budget**); crash-orphan sweep (stale `accepted` → terminal `delivery_unknown`, still counts toward M); restart-durability simulation (no post-restart burst window).
- Failure paths: `notify_dispatch_timeout` → delivery-UNKNOWN envelope + never retried; `notify_dispatch_failed` post-single-retry; REFUSED envelopes agent-legible (state when the next send is legal); **N2 fail-closed** (ledger write fails → NO send, `notify_audit_unavailable`); **N9 kill-switch off** (`notify_disabled`).
- Ari-only: literal-name bypass negative control; non-ari agent census over `agents/*/meta.json`.

**W10 security-property tests (each WITH a mutation leg):**
1. Gate concurrency (parallel calls through the gate stack).
2. Cross-instance shared-cap (parallel ari sessions → same destination share M).
3. Budget predicate (**N3-extended**: ONLY accepted rows consume M; every refusal class ledgered, none consumes budget).
4. Ledger completeness + **body-never-cleartext** (every attempt ledgered; only hash+length stored).

**Boot smoke (RULED mechanism — approver B1, folded verbatim; also `decisions.md` §6-B1):**
- **Scratch PG16** (fresh-SQLite boot blocked by `20260714_000001` — **re-verify present on tip** before relying on it).
- `systemd-run` transient unit + `/proc/<pid>/cgroup` assertion (**cgroup isolation from the live daemon**).
- **Mechanism (RULED):** scratch PG `source_configs` row of TYPE TELEGRAM (real adapter code, real registry build) + scratch aiohttp sink server answering the Telegram handshake (getMe) AND delivery (sendMessage) + base-URL redirection WITHOUT production-code change via a scratch PYTHONPATH shim prepended in the transient unit's environment (smallest-seam: an import-hook/sitecustomize-style patch of the adapter's API-base constant — NOT a whole-file shadow copy, which drifts). RUNNING precondition stated explicitly: the smoke must drive the scratch source through its handshake to SourceStatus.RUNNING BEFORE the alarm-shaped turn, else D1 preflight correctly refuses with `notify_source_disabled` (that refusal path is itself a negative-case assertion in the smoke). Sequence pinned: scratch source RUNNING → `source="scheduler"` alarm-shaped wake → Ari turn on a scripted/mock LLM node that emits the `notify_user` tool call (**no real-LLM**) → tool executes → sink receives **EXACTLY ONE sendMessage** with expected payload → `notify_audit` rows land (`accepted`→`dispatched`) → teardown clean, cgroup-isolated from the live daemon, scratch PG dropped.

**W11 fences (folded into every execution lane):**
- Scratch-PG-only.
- Per-var env scrub: `env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT` — the `env -u POSTGRES_*` glob is a **silent NO-OP**; never trust it as a fence.
- mktemp FAKE_HOME fence (never resolve live-shaped HOME paths).
- Cgroup assertion (live-daemon isolation proven, not assumed).

## 5. Process / staging posture

- **Stage-only as v0.27.8, RE-DERIVED from the live census at stage time** (v0.27.7 taken by the nest-ops lane; concurrent lanes may move it again — never assume the plan's number).
- `promote.sh` **NEVER invoked**; the user's 3-factor nonce ceremony owns the flip.
- **D8 rollback adjudication recorded VERBATIM at stage**: additive `CREATE TABLE` (the previous tree never references `notify_audit` — verify `git grep notify_audit` = 0 hits at parent) ⇒ inert on rollback ⇒ `rollback_safe=true source=explicit`.
- **No live edits**; live daemon untouched.
- **L1 tip-identity guard discipline (W8):** rebase → re-derive → implement → stage; **NEVER `--allow-stale-stage`**; record the derivation sha at each step.

## 6. Residuals (architecture §6, verbatim)

- **🟡 RESIDUAL (accepted, disclosed):** prompt injection in alarm/schedule content can steer notify_user into an exfil channel — the design cannot distinguish a legitimate notification from injected content. Mitigations: ari-only, N=1/turn, M=6/h, source-bound to the user's OWN registered chat, 2000-char cap, body-hash forensics. The audit trail is **detection, not prevention**. Carry as an explicit disclosure in the tool doc and plan.
- **🟡 Sessions never contacted via chat have no legal target** (by construction — instance_messaging.py:3509-3517 treats missing original_source as expected). Correct refusal; the tool docstring must teach it (board remains the fallback surface).
- **🟡 (fold 2, N4) Crash-orphaned `accepted` rows count toward M** — conservative, under-send biased, never over-send; the rolling hour bounds impact. A lightweight sweep marks stale `accepted` rows (no terminal transition) as terminal `delivery_unknown` for audit truthfulness — they read as UNKNOWN, not false deliveries.

## 7. Drift re-derivation note

All pin sites and doctrine line numbers are **RE-GREPPED on the TIP tree at implementation time** — never trust this (or any) plan's counts or line numbers; the registry file itself warns against it. Known drift already reconciled: **cu tool-category removal ⇒ privileged count 16→15** (the ceremony is 15→16, W5); `agents/ari/tools_note.md` doctrine passage ~:758-764 @ `d4ecdbd3d` → **~:778-779 @ tip** (+15 lines). All doctrine citations in implementation docs and commits: **quoted phrase + "as of `7af1d2632d`"**. **Code seams too (N5, fold 2):** `original_source` stamping on tip = **instance_messaging.py:3755-3772** (not :3512-3539 at the old base) — the tip-relative citation rule extends to CODE citations generally. **Category key (N7):** EXACTLY **`notify-user`** — research-b's "notify" is wrong; never copy the wrong key into `agents/ari/meta.json`. Base for every citation in this plan: `7af1d2632d`.

---

*Fold date: 2026-10-25. Frozen design: D1–D12 + three leader adjudications + C3/W1–W11 (see `decisions.md`). Non-re-litigable.*

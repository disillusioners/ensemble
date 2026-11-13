# guideline-notify-user-bounds

**Status:** draft · **Feature:** the `notify_user` tool — the "Ari voice" (privileged `notify-user` category). Commission 2026-10-24 ("if you see the next message need user/me → you can invoke message to discord, if not do in silence"); `feature/ari-notify-user` merge `97652f337`; `notify_audit` migration `20261025_000001_create_notify_audit.sql`; staged verification at v0.27.8. The commission's "v0.26.2+" dating — UNVERIFIED against a surviving CHANGELOG section (no `[0.26.2]`/`[0.26.3]` heading exists in any tree checked); the dates + merge above are the verified anchors. · **Audience:** operator / agent

## 1. Purpose & doctrine

The deliberate, opt-in message from a non-user-originated turn (self-alarm wake, scheduled fire) to the session's bound registered chat source. Doctrine: **the board is the ALWAYS surface; notify is the MAY voice.** Nothing auto-sends; silence is the default. The signature takes the MESSAGE, never a destination — destination resolves SERVER-SIDE from the session's own `original_source` binding, so an arbitrary destination is structurally impossible. A session never contacted via chat has NO legal target — the tool refuses and the board stays the fallback surface.

## 2. States

- **ON** (default): `ENSEMBLE_NOTIFY_USER_ENABLED` unset or `true`.
- **OFF**: `false` ⇒ typed refusal `notify_disabled` (the N9 kill-switch; cheap insurance against the disclosed prompt-injection exfil residual). The kill-switch refusal is the OUTERMOST gate — it never depends on ledger health, so a hung ledger cannot mask it.
- Per-call states: rate-limited (`notify_rate_limited`), no-legal-target, size-rejected — all ledgered refusals; tools never raise.

## 3. EXACT enablement steps

Env keys (prefix `ENSEMBLE_NOTIFY_USER_`, class `NotifyUserConfig`, `daemon/config.py:3340`): `ENABLED` (**true**) — the kill-switch; `MAX_PER_HOUR` (**6**, ≥1) — M, the rolling-hour cap per RESOLVED DESTINATION (target hash = sha256(source_id + ':' + external_user_id)), counted from the `notify_audit` ledger commit-before-count, calibrated to the 1800s self-alarm floor; `MIN_INTERVAL_SECONDS` (**60**, ≥0) — the ≥60s per-instance interval proxy for N=1/turn (strict turn identity is not tool-visible; turns are serialized per instance); `MAX_CHARS` (**2000**, ≥1) — the D6 size cap, the tool-side rejection bound BEFORE any resolution or dispatch (one notify = one chat message); `TIMEOUT_SECONDS` (**10.0**, >0) — the ONE never-block bound (W4): the whole pipeline (binding resolution + ledger gates + adapter send + the single transient retry) under one `wait_for`; backoff computed from the REMAINING budget. No special restart requirement; access needs no setup — the three access layers ship pre-wired (§4).

## 4. The rules that bite

- **Three access layers, ALL required** (any one alone is bypassable): (1) the `notify-user` category is PRIVILEGED (entry **#16** of 21 in the v0.30.0 tree — the empty-allow inherit universe never reaches it); (2) ONLY `agents/ari/meta.json` carries the allow entry, and the category key is EXACTLY `notify-user` (N7 — never `notify`); (3) the tool body rejects any caller whose `agent_id != "ari"` (a LITERAL tool name in an allow list bypasses the category gate).
- **A rate refusal is BY DESIGN, not an error**: `notify_rate_limited` states when the cooldown ends; refusals NEVER consume budget and NEVER extend the interval — only ACCEPTED rows count (the interval baseline is the last ACCEPTED-class ledger row). An agent (or an operator reading logs) must not treat it as a failure or retry around it — retrying cannot succeed; the refusal is the contract working.
- **Wake shapes decide delivery**: `internal_report`-family wakes already deliver the turn's final response to chat when the session carries a chat binding — prefer SILENCE there (a notify would double-send). On `scheduler`-source wakes (the self-alarm case) there is NO auto final delivery — `notify_user` is the ONLY channel. Either way: at most ONE message per ~minute per instance — never a bulk-send primitive.
- **Timeout ⇒ delivery UNKNOWN**: the 10s tool bound PRE-EMPTS Discord's own 15s REST pre-flight window (disclosed); a slow Discord path surfaces as `notify_dispatch_timeout` before Discord could report definitively — NEVER re-send blindly; the POST may have landed.
- Ledger-write failure yields a typed refusal (N2) — the ledger gates are fail-closed; every attempt INCLUDING every refusal is ledgered.

## 5. Verification & probe

Refusal matrix: kill-switch OFF ⇒ `notify_disabled` on every call regardless of ledger state; a second call inside the 60s interval ⇒ `notify_rate_limited` naming the cooldown; a >2000-char message ⇒ the size rejection BEFORE any destination resolution. Ledger: every attempt lands in `notify_audit` (accepted-class vs refusal-class rows; only accepted rows consume the M=6/hour budget). Envelope hygiene: no `str(exc)`/traceback/repr ever reaches a payload (the PB-F1 leak class) — unexpected exceptions are logged SERVER-SIDE and answered with a generic envelope. An unrun check means the bounds are UNVERIFIED at that release — silence is also the tool's success state, so absence of messages alone proves nothing.

## 6. Evidence discipline

The `notify_audit` table (append-only) is the evidence surface: every attempt, including refusals, with the outcome class. Refusal envelopes carry the typed code + the cooldown end where applicable. No destination is agent-chosen; no exception text is recorded into payloads.

## 7. Rollback

No schema coupling beyond the additive `notify_audit` table. `ENSEMBLE_NOTIFY_USER_ENABLED=false` disables the surface without restarting the surrounding system; the board (always-surface) is unaffected. Old binaries without the tool coexist with the schema (fix-loss-only shape).

## 8. First real use

The self-alarm voice: scheduled fires and wake turns needing the user. The v0.27.8 staged verification (S1 8/8, S2 boot 7/7, S3 8/8, S4 rollback posture; PASS-WITH-NOTES) is the recorded gate; a first-production exercise record — UNVERIFIED in the sources consulted.

## 9. Status

**Status: draft** (2026-11-13); verified against the v0.30.0-era tree (origin/latest @ `ee2b46d3b`). Gaps: the "v0.26.2+" version attribution and the first-production record are UNVERIFIED inline (header, §8).

## 10. Source-of-truth pointers

`daemon/tools/notify_user.py` (module docstring carries the doctrine verbatim); `daemon/config.py` `NotifyUserConfig` (:3340); `daemon/migrations/versions/20261025_000001_create_notify_audit.sql`; `daemon/repositories/notify_audit/`; planning `.agents/shared/planning/ari-notify-user/` (architecture-recommendation, decisions, research-a/b/c); privileged-set pin `daemon/tools/_tool_registry.py` (`notify-user` #16). Runbook: none — `docs/runbooks/` carries no notify runbook (the planning dir + tool docstring are the standing docs). Board: no ENSEMBLE-* id found in-repo for this lane — UNVERIFIED.

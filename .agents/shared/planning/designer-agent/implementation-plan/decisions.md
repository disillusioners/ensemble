# Designer Agent Implementation — Plan-Time Decisions Log

- **Date:** 2026-09-26
- **Status:** Draft (synthesis; wave-2 deliverable)
- **Method:** One sequential `PD-n` register that unifies (A) dispatcher decisions below, (B) the three phase files' hand-off registers (P1 §8 PD-1..PD-4; P2 §12 O-1..O-3; P3 §9 OQ-1..OQ-3 + §13 PR1..PR8) re-numbered into the same register with source citations.
- **Source of truth:** `.agents/shared/planning/designer-agent/architecture-recommendation.md` (ratified 2026-09-26; D1–D6 LOCKED — never re-opened here).
- **Companion:** `plan-overview.md` (this synthesis's overview), `decisions.md` (this file).
- **Scope:** PLACEMENT / PHASING / SEQUENCING calls ONLY. Architecture decisions stay in the arch doc (cross-referenced).
- **Worktree:** `feature/designer-agent-design` @ `e67e5cd85f052896cb80bf22ba03d575f339e025`; docs-only effort.

**Format per row:** `PD-n | decision | rationale (1 line) | source/owner | revisit trigger (if any)`.

---

## PD Register (sequential, single register)

| ID | Decision | Rationale (1 line) | Source / owner | Revisit trigger |
|----|----------|---------------------|----------------|------------------|
| **PD-1** | 🔴 silent-fallback: DEFER behavior change; ADD observability (P1-WP3: WARNING upgrade at `instance_lifecycle.py:1436-1441` + spawn-log `model=`/`source=` extension at `:2006`) | Raising guard changes spawn semantics daemon-wide (37+ agents) beyond designer scope; D2's allowlist entry already removes the designer exposure; fallback already emits a caller `[NOTE]` (`:1467-1481`) — non-silent *evidence* is the requirement; non-silent *failure* is not | P1 §8 PD-1 (worker verdict) | Missed-entry incident; boot-time meta-model lint candidate; P2 comparator resolution ambiguity → open a fail-loud/fail-notice commission |
| **PD-2** | `model_vision` deployment step is IN SCOPE and INSIDE the P1 restart window (P1-WP1 task 3) | Vision routing (`graph.py:7589`) fail-fasts if unset; P2 comparator hard-requires it; same window = one restart, no second one | P1 §8 PD-2 (worker verdict) | If deployment env cannot carry it: comparator verification (P2) blocks |
| **PD-3** | Adopt spawn-log extension (`model=`/`source=` at `instance_lifecycle.py:2006`) | Only existing proof of resolution is the `llm_models` persist log (`:1989`); designer resolves via `llm_model` (Priority 3) which logs nothing (P1 §2 GT-3) | P1 §8 PD-3 (worker verdict) | Log-volume complaint (none expected: +2 fields, +0 lines on happy path) |
| **PD-4** | Leader `workflow.md` anchors are INSERTION POINTS, not existing routes — AUTHOR new blocks at `:242-252` / `:258-262` / `:459-466` (P1-WP5) | No UI/UX/designer routing exists today in `agents/leader/workflow.md` (P1 §2 GT-1 verified via grep); arch doc phrasing reads as pre-existing routes — recording to prevent implementer confusion; **drift correction #5 in `plan-overview.md` §8** | P1 §8 PD-4 (worker verdict) + drift correction | Anchor drift on `latest` before implementation → re-locate by section, not by line |
| **PD-5** | **Placement:** output dir = `implementation-plan/` subdir inside the existing `designer-agent/` feature dir (next to `architecture-recommendation.md`) | Caller's "adjust if better fit" allowance granted; the three phase files + this synthesis live as siblings under the same arch doc — discoverable via single parent-dir grep | dispatcher | Re-organization of the planning tree |
| **PD-6** | **File naming:** `phase1-foundations.md` / `phase2-parallel-builds.md` / `phase3-bootstrap-kms-lite.md` / `plan-overview.md` / `decisions.md`; `verdicts/` subdir for gate artifacts; `bridge-design.md` and `templates/design-spec.md` + `templates/design-review.md` as siblings | Names cluster by phase + role; `verdicts/` subdir establishes a gate-artifact convention (P2-WP5 first user, P3 reuses); templates co-located with the plan that defines them | dispatcher + P2 §3.2 L5 + P1 §3 P1-WP11 | n/a |
| **PD-7** | **Method:** two-wave parallel-by-phase dispatch — 3 phase workers in parallel with pinned inter-phase contracts, then this synthesis worker | Contracts were frozen BEFORE drafting to prevent drift; matches the wave-1 evidence of `plan-overview.md` "wave-1 complete (3/3 phase files verified on disk); wave-2 synthesis worker dispatched" | dispatcher (KV heartbeat note 2026-09-26T15:05:00Z) | n/a |
| **PD-8** | **Dependency inversion (caller-endorsed):** P2-WP4 early self-hosted OD install is MANUAL/OPS-LANE (pre-bootstrap); P3-WP5 install-opendesign skill later GENERALIZES/REPLACES the manual lane | P3's installer skill needs the early manual install as its measured template; install precedes installer; ops-lane availability flagged in O-3 | dispatcher + P2 §5.0 P2-WP4 dependency-inversion note + P3 §0 P3 MAY ASSUME | "Operator installs ad-hoc without going through install-opendesign skill" — meaning the manual lane has atrophied, not generalized |
| **PD-9** | **§10 GAP-3 (`send_message` `images` param): OUT OF SCOPE day-1** — operational mitigation (substrate paths + pixels-at-dispatch via `images=[data_uri]`) stands; revisit trigger = comparator/UX-flow friction evidence | All three phase files independently flagged as not-day-1 (P1 §10, P2 §9, P3 §10); adding the param is a daemon message-API change with non-trivial risk | dispatcher | Comparator/UX-flow friction evidence accumulates → open a param-extension commission |
| **PD-10** | **§7.5a deferred KMS machinery stays deferred — listed, never planned** (policy store; OD Cloud brokering; `kms_rotate`/`kms_revoke`/`kms_audit`; SSE secret/credential event kinds; audit atomicity template; DEK-rotation story) | Ratified D5 is "mint-only KMS-Lite, no policy layer"; §7.5a is explicitly "later, from real usage"; deferring avoids a day-1 surface explosion | dispatcher + P3 §8 Deferred | KMS-Lite gains policy/rotation → revisit in the same commission |
| **PD-11** | **Bridge arbitration:** path→data-URI bridge DESIGN lives at P1-WP10; BUILD/impl lives at P2-WP3 (comparator facade). The two files AGREE on the contract (plan-overview §4.4 verification). | P1-WP10 task 2 marks implementation home as P2's decision; P2-WP3 says "Bridge follows the P1 design document (A6) — WP3 does not re-design; deviations from the P1 design are defects, reported, not silently absorbed"; P3 §0 / P1 §0 / P2 §3.1 A6 all consistent on the split | dispatcher + this synthesis verification | Bridge design vs impl drift → reconcile at the design-doc level |
| **PD-12** | **P2 O-1 — verdict artifact path:** `implementation-plan/verdicts/capture-adopt-or-build.md` is the canonical gate-artifact path; marked **CONFIRMED-by-default** by P2 worker unless caller overrides | P2 §3.2 L5 establishes the `verdicts/` directory convention; first user is the adopt-or-build gate; convention is reusable by P3 for its own gate artifacts (per P3 §3.2 L5) | P2 §12 O-1 (worker choice, dispatcher-confirmed by default) | Override by dispatcher before P2 dispatch |
| **PD-13** | **P2 O-2 — registry category key:** plan specifies `"design": ["image-comparator"]` per arch pattern; implementer MAY rename if a narrower key fits, provided the `_auth.py:35-40` MUST-match rule holds (category name MUST match the `@register_tool_category(...)` string) | The MUST-match rule is the load-bearing one (P2-WP2 AC-1); the specific key string is implementer discretion within that constraint | P2 §12 O-2 (worker choice) | n/a (constraint is the rule, not the key) |
| **PD-14** | **P2 O-3 — ops-lane availability for P2-WP4:** flagged as an EXTERNAL DEPENDENCY; the only WP outside the daemon/agents lane; manual install requires operator host access | P2 §5.0 P2-WP4 explicitly out-of-band; ambient `POSTGRES_*` live-probe trap (3 prior incidents) requires standalone-bash-wrapper + zero-`POSTGRES_*`-survivors verify pattern | P2 §12 O-3 (worker flag) | Operator unable to provision a host → revisit (this WP gates Track B) |
| **PD-15** | **P3 §9 OQ-1 / P3-WP13 — root-key hardening as LANDING WP:** WP13a (lightweight file-perm + age check; no OS-keychain day-1); WP13b (full deferral) is the flip option | Vault infra doesn't exist; OS-keychain integration is platform-specific (macOS Keychain ≠ Linux libsecret ≠ Windows DPAPI) and would explode day-1 surface; lightweight file-perm + age check covers the realistic threat model | P3 §9 OQ-1 (worker verdict, default unless told otherwise) | When KMS-Lite gains policy/rotation (§7.5a) → harden in the same commission (PD-10 revisit) |
| **PD-16** | **P3 §9 OQ-2 / P3-WP8 — raw-row one-time migration as LANDING WP** | Architecturally load-bearing for R3 closure; cannot be deferred because day-1 installs are marker-based but pre-existing rows stay raw without it (silent plaintext persistence); idempotency + audit-line shape are the non-obvious correctness requirements | P3 §9 OQ-2 (worker verdict) | n/a (R3 mitigation is mandatory) |
| **PD-17** | **P3 §9 OQ-3 / P3-WP11 — `[resume]` convention ratification as LANDING WP** | The resume primitive (`job_continue`) already exists unchanged; the convention ratification is comment-level + cross-skill-body documentation; deferring leaves install skills writing ad-hoc resume shapes and breaks the bootstrap loop's predictability | P3 §9 OQ-3 (worker verdict) | n/a (convention is mandatory for the bootstrap loop) |
| **PD-18** | **Cross-phase sequencing gate:** P3 exit (P3 §6 row 8) requires P1's R5 (hand-authored `agents/designer/`) and R6 (`vision` in daemon-global `allowed_models`) acceptance criteria green (read-only check) | P3 inherits P1's contracts via MAY-ASSUME; if P1 hasn't shipped R5/R6, the e2e autonomous cycle cannot run safely | P3 §13 PR7 (worker flag) + this synthesis | P1 fallback to alternative model wiring → revisit (low likelihood; R6 is one-time config edit) |
| **PD-19** | **Phase-internal risk PR6 (WP8 migration atomicity):** HIGH severity, LOW likelihood — mitigation is per-row `BEGIN/COMMIT` transaction with on-error-touched-untouched posture + idempotency + audit lines (P3-WP8 acceptance) | Touches existing `mcp_servers.config` rows in-place; interruption mid-run creates partial state; the chosen mitigation matches house atomicity conventions | P3 §13 PR6 (worker flag) | Migration script exception observed in test → revisit (low likelihood; idempotent design) |
| **PD-20** | **Drift correction #1:** `daemon/instance.py:2998-3004` → `daemon/tools/instance.py:2998-3004` (arch doc §3.5 + P3 §3.5 cite the wrong module) | Grep-verified location; P3 file's §3.5 table references `daemon/instance.py` at one site, but the verified path is `daemon/tools/instance.py` | this synthesis §8 drift row 1 + P3 §3.5 verified | n/a (greppable) |
| **PD-21** | **Drift correction #2:** `invoked_as_tool` stamp site actually at `instance_lifecycle.py:1963-1964`, not the `chart_tools.py:67-145` docstring's cited `:1798-1799` | P2 §4 G3 grep-verified; mechanism confirmed at `chart_tools.py:67-145` but the stamp site is in instance_lifecycle | this synthesis §8 drift row 2 + P2 §4 G3 | n/a (greppable) |
| **PD-22** | **Drift correction #3:** multi-image per message test name is `test_multiple_images_in_one_message` at `tests/unit/test_vision_routing.py:275` (TestEdgeCases class, line 253); arch doc §6 cites `test_vision_routing.py::TestMultipleImages` | Mechanism confirmed at `instance_messaging.py:113-128`; class name differs from arch citation; test exists and passes on the mechanism | this synthesis §8 drift row 3 + P2 §4 G6 | n/a (test name discoverable) |
| **PD-23** | **Drift correction #4:** `daemon/mcp/config.py:260` (HTTP/SSE headers treatment) may drift — P3 §11 spot-verification list flags it as REQUIRED at implement | Arch doc cites `:260`; exact line depends on source layout; P3-WP6 verification at implement | this synthesis §8 drift row 4 + P3 §11 | n/a (spot-verifiable) |
| **PD-24** | P1 implementation: the P1-WP1 restart-protocol deliverable lives at `implementation-plan/restart-protocol.md` | Plan named the deliverable but not its file; phase-lead gap-fill, discoverable beside the phase docs | P1 phase lead (developer[v2]) | n/a |
| **PD-25** | P1-WP2: `parent_map` is a DISTINCT `resolved_source` label (vs `override`) and IS persisted into `instance_metadata["model_override"]` (persistence set = override/parent_map/llm_models) | Without persistence, restart/restore silently re-resolves parent-imposed models to the `llm_model` fallback — a behavior shift "inherit unchanged" would have introduced; independent review verdict: both deviations SOUND, no reader branches on the label | P1 coder-A + review-a (APPROVED-WITH-NOTES) | Label-semantics change proposals → ADR only |
| **PD-26** | Line-number drift accepted: PD-1/PD-3 citations (`:1436-1441`→`:1447`; `:2006`→`:2231-2235`) and restart-protocol.md §5.2/§6 cite pre-commit line numbers (authored in the same commit that shifted them) | ~150-line helper insertion; content greps (`model=vision source=llm_model`, `Spawning instance`) remain authoritative; protocol line-cite refresh absorbed by the next docs pass | P1 phase lead (per review-a finding 4) | P2 doc pass touching restart-protocol.md |
| **PD-27** | Leader workflow.md clipboard-mitigation text cites the mechanism in prose ("chat-path pre-dispatch hook and the tmp-image-to-description converter") instead of raw `path:line` citations | Agent-facing prompt files follow the no-machine-noise convention (docs/agent-prompt-writing-guide.md); mechanism still cited per arch-doc §4.3b | P1 coder-B | n/a |
| **PD-28** | P1-WP8 agent tool API: `image_save(content_b64, content_type, …)` (base64-string input) instead of the plan-text `image_save(bytes|path, …)` | Tool-layer params must be JSON-serializable strings; agents already handle base64 (explain_image precedent); store methods retain bytes semantics | P1 coder-C | P2 comparator bridge needs path-in at tool layer → extend then |

---

## Deferred (from §7.5a — listed, never planned)

These appear here for traceability only; no WP below plans them. Future commissions pick them up:

- Policy store (service allowlist, budget caps, TTL/scoping) and `policy_denied` activation.
- Third-party OD Cloud brokering under one-time human policy approval (`ensemble policy set …`).
- `kms_rotate` / `kms_revoke` / `kms_audit` tool surfaces.
- SSE secret/credential event kinds (no `SseEvent.SECRET_*` exists today).
- Full audit atomicity template (atomic temp+`mv` + torn-write scan per upgrade-journal template).
- DEK-wrap key-rotation story (current day-1 uses a single Fernet master; rotation = re-encrypt every row).
- The `policy_denied` envelope kind firing — schema exists in P3-WP3 but cannot fire from day-1 code (lint-enforced).
- `install-audit.jsonl` growth path beyond one-line-per-issue (actor-stamped diffs; SHA-256 tamper-evidence).
- Future installer skills beyond OD (`install-notion`, `install-figma`, etc.) — only the PATTERN is generalizable from P3-WP5; the individual skills are out of scope.
- Designer-initiated audit cadence without a daemon cron (architectural OQ — owned by the user / leader workflow decisions).
- GAP-3 `send_message images=` param (PD-9).
- Comparator A→C consolidation (recorded as monitoring triggers in P2-WP2; execution deferred until a trigger fires).

**Source:** arch doc §7.5a + P3 §8 Deferred + P1 §7 Deferred + P2 (X1–X6 Out of Scope) + PD-9/PD-10.

---

## Drift Corrections (summary index; full table in `plan-overview.md` §8)

| ID | Summary | Phase | Cross-ref |
|----|---------|-------|-----------|
| PD-20 | PAUSED-rejects-agent-tool-send: `daemon/instance.py:2998-3004` → `daemon/tools/instance.py:2998-3004` | P3 | plan-overview §8 row 1 |
| PD-21 | `invoked_as_tool` stamp site: `chart_tools.py:67-145` docstring `:1798-1799` → `instance_lifecycle.py:1963-1964` | P2 | plan-overview §8 row 2 |
| PD-22 | Multi-image test: `TestMultipleImages` → `test_multiple_images_in_one_message` @ `tests/unit/test_vision_routing.py:275` (TestEdgeCases) | P2 | plan-overview §8 row 3 |
| PD-23 | MCP HTTP/SSE header line: `daemon/mcp/config.py:260` may drift — verify at implement | P3 | plan-overview §8 row 4 |
| PD-4 | Leader `workflow.md` anchors are INSERTION POINTS, not existing routes (no UI/UX routing exists today) | P1 | plan-overview §8 row 5 |

---

*End of decisions.md.*
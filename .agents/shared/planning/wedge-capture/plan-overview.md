# wedge-capture — Plan Overview

Commission: **wedge-capture evidence instrumentation** (agents-ensemble; incident 2026-10-22 follow-up).
Plan set mode: **Mode 1 — greenfield** (the target planning directory was empty at plan time).
Authoritative tree for ALL code reads: `/home/nea/ensemble-src-wt-wedge-capture` (base `639b7129b`, branch `feature/wedge-capture-watchdog`).
HARD FENCE: `/home/nea/ensemble-src` is a **shared checkout with foreign WIP** — never read-write it, never build from it.

**Folding status (architect review folded — SOUND-WITH-CHANGES).** The binding architectural review
(`architecture-recommendation.md`, 388 lines, in this directory) issued **19 amendments (A1–A19)**, of which **six
are 🔴 release-blocking** (A1, A2, A3, A8, A10, A11). **All 19 are folded into the D/T/AC rows below.** The annex
**stays in this directory as the binding annex** — it is referenced, never folded in wholesale, and is **not** edited.
The six blockers land as: **A1** → D4 (rewritten) + **NEW D4b** + T5 + AC4; **A2** → D21 (rewritten) + this overview's
§4.1 ledger + Phase-2 D37/T24 + Phase-3 T39's reconcile note (**SAME-commit** cross-phase contract); **A3** → D23
(rewritten) + D10 + T11/T12/T13/T14 + AC21; **A8** → D9 (the boot-record carrier) + D16 (the effective-mode source) + T8/T9 (rewritten: call-site write, lazy import, empty `__init__`, `mode=` on exactly 5 sites) + AC12/AC13/AC14/AC15; **A10** →
T13 + AC20 (PG-only acceptance) + the staging checklist S-2/S-7; **A11** → D30 (rewritten to the true 13-file table)
+ T23 + RK-9/RK-19.

**One id deviation, disclosed:** the annex's **A6 creates a NEW `D25`** (the readiness-composite row), but this plan
already used `D25` for the watchdog-start decision. The annex's `D25` is authoritative, so the **original** row was
**displaced to `D25b`** — preserved verbatim, not renumbered away — which keeps D26/D27 and the Phase-2 D28 boundary
intact (no cascade, nothing deleted). `D4b` is the annex's own new id and was inserted after D4.

---

## 1. Objective

Deliver an **automatic, evidence-only wedge-capture instrument** that, when the live daemon wedges, records
enough state to **distinguish the two incident shapes** —

- **H1 (CPU/loop-starvation)**: asyncio event-loop starvation at 95–107% of ONE core, observed
  2026-10-22 04:58–05:31Z across 5 boots, correlated with AutoContinue re-executing large-history turns
  (`daemon/services/auto_continue_boot_pass.py`) — **H1 is a HYPOTHESIS, NOT PROVEN**; the instrument must be
  able to **falsify** it;
- **H2 (store/stall)**: checkpoint-store non-reclamation (47h without cleanup, ~96.5k rows / ~13 GB) →
  hard-coded 5-connection saver pool saturation → sentinel read timeouts → readyz flaps → three ~181s stalls
  2026-10-22 07:09–07:45Z **with NO CPU peg**;

— and attributes the **offending turn** (which instance / which task / which thread).

**This commission fixes NOTHING.** It instruments. No CPU-sink change, no history-proportional scrub change,
no checkpoint-store change, no pool-size change. The output is a capture directory on disk plus one additive
metadata row, plus a read-back surface (privileged agent tool + operator CLI + runbook).

---

## 2. Scope

### 2.1 In scope

| # | Item |
|---|------|
| S1 | A **dedicated daemon thread** watchdog (`threading.Thread(daemon=True)`, never a coroutine) that samples process/thread CPU and event-loop lag from OUTSIDE the event loop. |
| S2 | **Three independent triggers**: sustained process CPU, sustained event-loop lag, per-thread CPU concentration. All three ship. |
| S3 | A **capture pipeline** producing a bounded, file-first capture directory (stack dump, per-thread CPU attribution, lag histogram, GC + RSS + pool state, DB-side executing turns, boot epoch + AutoContinue decisions, checkpoint_perf stall-ledger tail, readyz snapshot). |
| S4 | An **additive metadata table** (`wedge_captures`) + its `.sql` migration, written via an **own one-shot** connection — never the saver pool, never a loop bridge. |
| S5 | A **boot record** capturing the effective AutoContinue mode + per-epoch decisions, sourced from what the boot pass actually read. |
| S6 | An **additive `[BOOT_CONTINUE]` log-line extension** recording the effective mode (both passes, including the OFF early-return branch). Never a duplicate line. |
| S7 | A **read-back surface**: a new privileged tool category (`wedge-capture`, 2 read-only tools), an admin API router, an operator CLI, and a runbook. |
| S8 | **Hygiene**: episode de-dup, cooldown, bounded retention (count + age + disk), prune-on-write. |
| S9 | A **stage-only** release at the end (no promote — the user's 3-factor nonce ceremony owns it). |

### 2.2 Out of scope (explicit)

- **Any fix** for either bug. No change to `daemon/graph.py` CPU sinks, `daemon/compaction.py` scrub,
  `LoopDetector`, blob parsing, `daemon/persistence.py` pool sizing, or checkpoint cleanup scheduling.
- **Read-only store diagnostics** — a SEPARATE lane. Not planned here.
- **Query-plan capture** — OUT OF SCOPE per Addendum 2: the `aget` latest-checkpoint read is index-driven
  (`Index Scan Backward` on `checkpoints_pkey`; cold path byte-identical). Only optional per-capture
  `reltuples` / `n_live_tup` drift recording survives.
- **`application_name` tagging** on the daemon's own connections — a **metadata-only NOTE-FOR-ARCHITECT**
  (every daemon connection currently carries an EMPTY `application_name`; `pg_stat_activity` attribution is
  impossible). Not implemented here. The capture's OWN one-shot connection DOES set
  `application_name=wedge-capture` — that is the capture path, not the daemon path.
- **Write-side effects of any kind** on the incident surface. The instrument is strictly read-only against
  production state; it writes ONLY into its own capture directory and its own table.

### 2.3 BINDING CONSTRAINTS (user, non-negotiable — restated verbatim in substance)

> **BC-1 — AUTO-RESUME STAYS ON, DEFAULT ON.**
> Never set `ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART=0` anywhere (`.env`, systemd unit, test fixtures).
> No gating or disabling of auto-continue. The env var remains an **emergency lever only**
> (unset ⇒ ON; read per boot at `daemon/services/auto_continue_boot_pass.py:145-156`).
> The **ONLY** allowed touch is an **ADDITIVE boot log line recording the EFFECTIVE mode**
> (EXTEND the existing `[BOOT_CONTINUE]` lines — never duplicate; **both** passes, incl. the OFF
> early-return branch), surfaced also in the read-back surface.
> The effective mode is read **FROM THE RUNNING PROCESS** (what the boot pass read at its boot),
> **never re-derived** from precedence. **No systemd unit changes.**

> **BC-2 — NO FIXES.** No CPU-sink path changes, no history-proportional scrub / LoopDetector / blob-parse
> changes, no checkpoint-store changes, no pool-size changes. `application_name` tagging is a
> metadata-only NOTE-FOR-ARCHITECT, not implemented here.

> **BC-3 — SEPARATION OF LANES.** Read-only store diagnostics are a separate lane — do not plan them here.

> **BC-4 — STAGE ONLY.** A stage-only release at the end; no promote. The version slot is resolved at
> stage time (do NOT pre-claim).

---

## 3. Phases

| # | Name | File | Objective | Depends on |
|---|------|------|-----------|------------|
| 1 | Watchdog core, triggers, capture pipeline, migration, boot record | `phase1-plan.md` | A thread-based watchdog that detects CPU / lag / concentration wedges, captures bounded evidence to disk, writes one additive metadata row via its own connection, and records the effective AutoContinue mode per boot epoch. | — |
| 2 | Read-back surfaces, CLI, runbook, boot-mode surfacing | `phase2-plan.md` | A privileged read-only agent tool category, an admin API router, an operator CLI, a runbook, and the effective-mode / watchdog-status surface. | 1 |
| 3 | Test packs, isolated boot smoke, staging checklist | `phase3-plan.md` | Unit packs for every trigger/writer/read-back/boot-record path, an ISOLATED boot smoke that proves the instrument fires on a simulated wedge, and the stage-only release checklist. | 1, 2 |

**One developer feature-lane**, one instance, shared context. Phases 2 and 3 continue in the same lane/worktree;
the phase split exists to sequence the *riskier* work (P1) before the *ceremony-heavy* work (P2's
**13-file** pin commit — the "eight files" figure is **STALE per A11** and was swept across the overview in this
revision (**A-F2**); see the folding-status banner).

### 3.1 Phase-boundary note (one deliberate deviation, flagged)

The dispatch assigned the "boot-mode log surface" to Phase 2. This plan puts the **additive `[BOOT_CONTINUE]`
line extension itself in Phase 1** (task T9), because the effective mode is captured **at the moment the boot
pass reads it** (BC-1: "what the boot pass read at its boot") and the capture payload consumes that value.
Deferring the log line to Phase 2 would split the read and the record across phases and make the Phase-1
capture able to cite a mode it had no way to obtain truthfully. Phase 2 owns the **read-back surfacing** of
the effective mode (status endpoint / tool / CLI / runbook). The Phase-1 log line is one additive `logger.info`
at an existing call site plus one in-body record write — no behavioural change.

---

## 4. Coupling Map

Legend: **H** = hard coupling (same file / same contract, must move together) · **M** = medium (shared
contract) · **L** = loose.

| Phase ↓ / Phase → | 1 (core) | 2 (read-back) | 3 (tests) |
|---|---|---|---|
| **1 — core** | — | **H** — the capture directory layout + `wedge_captures` row schema are the contract the router/CLI read. `daemon/api.py` is touched by BOTH P1 (watchdog start) and P2 (router mount) → **one edit site, two phases** → resolve by landing the P2 mount in the same file as the P1 lifespan hook, in separate commits. | **H** — the boot smoke (P3) asserts the capture fires; the trigger thresholds and the capture deadline are P1's contract. |
| **2 — read-back** | **H** — `wedge-capture` privileged category ⇒ `PRIVILEGED_TOOL_CATEGORIES` 15→16 (`daemon/tools/_tool_registry.py:329`), `CATEGORY_MODULES` (`:941`), `KNOWN_TOOL_NAMES` (`:1200`), plus the three existing `EXPECTED_PRIVILEGED_SET` pins. **M** — `daemon/routers/wedge_capture.py` reads the capture dir + the table. | — | **H** — the registration pin pack (P3) asserts the category is registered; the CLI is smoke-tested. |
| **3 — tests** | **H** — the trigger/writer/boot-record packs import P1 modules directly. | **H** — the read-back pack imports P2 modules. | — |

### 4.1 Shared-contract ledger (the things that MUST stay byte-consistent across phases)

| Contract | Owner phase | Consumers |
|---------|--------------|-----------|
| Capture directory layout + `manifest.json` schema | 1 | 2 (router/CLI read), 3 (pack) |
| `wedge_captures` table columns | 1 | 2 (router list/fetch), 3 (pack) |
| Canonical pool counter names — **A2's amended vocabulary**: `pool_checked_out` (**derived** `pool_size − pool_available`, clamped ≥ 0), `pool_size_current`, `pool_max`, `pool_waiting_clients` ← `requests_waiting`, `pool_checkout_timeouts` (**always `None`** + `pool_checkout_timeouts_reason`), `pool_requests_errors` ← `requests_errors`, `pool_counters_source`. **`pool_size_total` is RETIRED.** No `pg_stat_activity` fallback ships. | 1 | 2 (status surface surfaces them, D37/T24), 3 (pack + the T39 reconcile note) — **all in the SAME commit** (A2's CROSS-PHASE CONTRACT) |
| Env knob names + defaults (`ENSEMBLE_WEDGE_*`) | 1 | 2 (status surface renders them), 3 (pack) |
| Canonical trigger reasons (`sustained_cpu` / `sustained_loop_lag` / `thread_concentration` / `manual`) | 1 | 2 (the capture-dir id pattern, D40), 3 (per-leg smoke identification) |
| **Manual-capture slot** — a **depth-1 `queue.Queue`** shared between the Phase-2 router (enqueue side: `put_nowait()`, non-blocking; `queue.Full` ⇒ `capture_busy`) and the Phase-1 watchdog (servicing side: `get_nowait()` on each tick). The **enqueue contract** (202 + pre-allocated id, the `capture_busy` / `manual_rate_limited` / `watchdog_disabled` refusals) and the **servicing contract** (one drain per tick, one capture at a time, the stop event honoured between capture steps) are **one contract across two phases** — a slot whose enqueue side and servicing side disagree produces a 202 for a capture that never happens | **1 (T15 servicing) + 2 (T19 enqueue)** | 3 (the manual-capture pack — T33 — proves the 202, the refusals and the non-inline execution) |
| Effective auto-continue mode vocabulary (`on` / `off` / `unknown`) | 1 | 2 (status surface), 3 (pack) |

---

## 5. Risks

**ID convention for this plan set:** decisions are `D#` (continuous across the three phase files: D1–D27 **+ D4b
+ D25** in Phase 1, D28–D41 in Phase 2, D42–D54 in Phase 3 — see the folding-status note below for the `D25b`
displacement); tasks are `T#` (T1–T18 / T19–T28 / T29–T41); acceptance
criteria are `AC#` (AC1–AC26 / AC27–AC41 / AC42–AC59); success criteria are `SC#` (SC-1–SC-15, above).
**Risks use the `RK-n` prefix** (RK-1–RK-31) — deliberately distinct from the `R1…R12` **requirement** ids used
by the sibling artifact `recurrence-evidence-matrix.md`, which lives in this same directory (so the two documents
do not collide on an id).

| ID | Risk | Impact | Mitigation |
|----|------|--------|------------|
| RK-1 | **The capture path needs the DB it may be capturing exhausted.** The saver pool may be fully checked out at capture time. | high | The capture NEVER touches the saver pool and NEVER bridges to the loop for DB work. Metadata is written over an **own one-shot sync `psycopg` connection** with `connect_timeout` (bounded by `daemon/constants.py:70` `BOOT_DB_TIMEOUT_S` = 10s). Files are written **first**; the row is best-effort. If the row write fails, LIST falls back to a filesystem scan of the capture dir (T19). |
| RK-2 | **The capture runs while the GIL is contested.** During an H1 CPU peg the loop thread holds the GIL; the watchdog thread still runs but only at CPython's switch interval (5 ms default). | medium | The watchdog's own thread body is strictly short (no blocking I/O inside the sampler tick). The expensive work (stack dump, DB probe) happens **after** the trigger, on the same thread, but each step is individually time-boxed and the whole capture has a hard deadline (T6). The per-thread CPU attribution is what makes the GIL contention itself visible. |
| RK-3 | **`faulthandler` and `threading.native_id` have ZERO precedent in this tree.** | medium | Both are stdlib, not third-party. `faulthandler.dump_traceback(file=...)` requires a file object exposing `fileno()` — verify at impl time. `threading.get_native_id()` is documented (3.8+) to return the OS thread id on Linux, but this tree has never used it → the capture records BOTH the `native_id` mapping AND the raw `/proc/self/task/<tid>/comm` name for every thread, so attribution is auditable even if the mapping is wrong. Pin both behaviours in the P3 pack. |
| RK-4 | **Disk-full during capture.** The capture dir can land on a full volume. | medium | Every write is `try/except`-guarded; a write failure degrades that artifact, never the capture. Retention prunes **before** writing a new capture (prune-on-write, T17). The disk cap (512 MB) bounds total usage. A capture that cannot write its manifest is discarded whole (no orphan partial dirs). |
| RK-5 | **Trigger spam.** A sustained wedge would re-trigger every sample tick and produce hundreds of captures. | medium | Cooldown (300s) + episode de-dup by trigger-reason fingerprint (D13; implemented in T5 and T15). A capture already in progress blocks a new one. |
| RK-6 | **The instrument adds measurable overhead to the hot path** (the very thing it measures). | medium | The sampler reads `/proc/self/stat` + `/proc/self/task/*/stat` **once per 1.0s tick** — the same cadence as the existing `resource_monitor` sampler (`daemon/services/resource_monitor/sampler.py:168`, `:294-297`), which already does the same reads. Overhead is one `/proc` sweep per second. The `checkpoint_perf` deque append is O(1) and bounded. |
| RK-7 | **The thread-CPU trigger cannot fire during H2** (no CPU peg ⇒ no concentration). | low (by design) | Accepted: H2 is detected by the **lag** trigger and by the **pool-state** capture. The three triggers are complementary, not redundant — this is exactly why all three ship (D2). |
| RK-8 | **The capture's DB read could itself perturb the store** it is measuring. | medium | The one-shot connection issues only `SELECT`s against `pg_stat_user_tables` / `tasks` / `instances` — no writes, no checkpoint-table scans. It runs on its own connection with a 10s connect timeout and a short statement timeout. |
| RK-9 | **Pin-ceremony breakage.** Adding a privileged category touches a 15-member set asserted in **8 exact-set sites** plus the registry plus three `meta.json` allow-lists plus `KNOWN_TOOL_NAMES`. | high | All **13** pin files land in **ONE commit** (T23) — the house rule. (The "eight files" figure is **STALE per A11**.) If any file is missed, the registration pin pack fails loudly with the diff, and the fix is a single follow-up commit (no partial state is promoted). |
| RK-10 | **The `daemon/constants.py` seal.** `daemon/constants.py` IS in `SEALED_SHA_BASELINES` (`tests/test_chart_image_delivery_e2e.py:1412`, 35 pinned files) — touching it trips the tripwire and forces a re-pin. | medium | **D24: do not touch `daemon/constants.py`.** All new constants live in the new module. `BOOT_DB_TIMEOUT_S` is READ, not modified (`daemon/constants.py:70`). ⇒ **no seal re-pin required.** The staging checklist (P3, T41 step S-5) verifies this by re-running the seal tripwire. |
| RK-11 | **The `agents/*/meta.json` allow-list edits could trip the prompt-artifact baseline.** | low | `VERSIONED_PROMPT_ARTIFACTS` (`daemon/tools/prompt_artifacts.py:119-140`) contains `soul.md / rule.md / workflow.md / tools_note.md / tools.md / skill.md / memory.md / growth.md` — **`meta.json` is NOT in the set**, so editing `agents/{ari,leader,maintenancer}/meta.json` does NOT trip the 161-row baseline. Verified at plan time. |
| RK-12 | **A new router mount breaks the two router manifest cages.** | medium | `tests/integration/test_router_class_manifest.py` (`PINNED_MANIFEST_AUTH_ON` at `:59`, `PINNED_MANIFEST_AUTH_OFF` at `:148`) and `tests/unit/test_router_scope_manifest.py` (`PINNED_SCOPE_MANIFEST` at `:62`) both fail loudly on an undeclared mount family. T20 declares the new family in **both** cages. |
| RK-13 | **The incident's 5-boot shape is invisible to a single capture.** Each boot resets in-process state. | medium | The **boot record** (T8) is persisted per boot epoch (module-level + on-disk JSON under the capture dir's parent) so a capture can cite the effective mode and the AutoContinue decisions for its epoch, and the `[BOOT_CONTINUE]` log lines carry the per-boot history across restarts. |
| RK-14 | **A wedged loop cannot answer the watchdog's own HTTP probe.** | low | The `/readyz` snapshot is read from `app.state.readiness_composite` (the last computed composite — **published** at `daemon/api.py:3657`; the **`None` init** is at `:2455` — N3: each line cites the right event) — an in-memory read the watchdog thread performs directly, no HTTP, no loop. Freshness (age of the composite) is reported alongside; a stale composite is reported AS stale, never as healthy. |

### 5.1 Risk register addendum — binding annex §4 (RK-15 … RK-31, VERBATIM; renumbered and source-credited by the annex)

| RK-15 | Tick-path logging deadlock (handler lock held by a loop thread blocked on a full stdout pipe) | 🔴 critical | A4 (no logging on the tick path) |
| RK-16 | Lag-trigger silently disabled on a fully starved loop (undefined never-fired semantics; AC4 racy) | 🔴 critical | A1 (D4 a–f + D4b) |
| RK-17 | Fabricated/absent pool numbers in H2 evidence (D21 names don't exist in psycopg_pool 3.3.1) | 🔴 critical | A2 |
| RK-18 | Orphan captures leak past every retention cap (D23 caps computed from the table) — unbounded-growth reprise | 🔴 critical | A3 |
| RK-19 | Pin-set drift: any plan naming a subset of the 8 exact-set sites lands a red tree (the stale "exactly 8 files" claim — **STALE per A11** — makes the wrong commit the SPECIFIED outcome) | 🔴 high | A11 + registry-header rule (grep, never trust counts) |
| RK-20 | New-package import inside the boot-pass envelope silently disables auto-resume (settled-item violation) | 🔴 high | A8 (lazy import + empty `__init__` + pack assertion) |
| RK-21 | One-post-per-tick probe: unbounded ready-queue growth (~1800 queued at a 30-min wedge), burst-on-unblock, racy peak | 🟡 high | A1(b) |
| RK-22 | Tiny-threshold boot smokes false-positive during daemon boot itself; cooldown proof indeterminate | 🟡 med | A15(c) per-leg env + reason/timestamp-qualified captures |
| RK-23 | Lag telemetry goes quiet exactly when it matters (samples stop while wedged) | 🟡 med | A15(d) `last_probe_age_s` + `stale`/`never_fired` |
| RK-24 | Missing/mis-ordered `@register_tool_category` ⇒ name-prefix fallback infers `"wedge"` ⇒ strip FAILS OPEN | 🟡 med | A13 attribute pin |
| RK-25 | "One record, both passes" unimplementable in-body (8 return sites; sweep result discarded at `api.py:2661`) | 🟡 med | A8 call-site write |
| RK-26 | ON boot with 0 candidates emits no main-pass `[BOOT_CONTINUE]` line ⇒ mode invisible in logs | 🟡 med | A8 line-site list; record is primary |
| RK-27 | Capture blocks on DNS/NSS or unbounded connect (connect_timeout covers TCP only) | 🟡 med | A5 resolve-at-start + per-step budgets |
| RK-28 | C-extension GIL holder invisible to the instrument | 🟢 med (residual) | A19 runbook disclosure |
| RK-29 | `=false`/`=off`/`=no` env values mean ON (literal-`"0"`-only disable) — record exposes long-standing surprise | 🟢 low | A8 `raw_env_value` verbatim + `mode_semantics` |
| RK-30 | Two router censuses (34 include_router / 100 declared routes) break loudly on the new mount | 🟢 low | A14 |
| RK-31 | `stage.sh` full-history grep ⇒ `CONTAINS_CONTRACT_PHASE=true` at base; un-flagged stage exits 78 (wasted cycle, no harm) | 🟢 low | S-7 keeps explicit `ENSEMBLE_ROLLBACK_SAFE=1` |

### 5.2 Risk register — governor-council fold (RK-32 … RK-37; source labels preserved)

| RK-32 | Fetch-file enumeration drift — the fetch tool lists a capture's files from the filesystem, so an unexpected entry (the `.pending_row.json` sidecar, a stray file) leaks into the evidence payload | 🟡 med | MR-5 (T21 pins the enumeration to D8's fixed file set + sidecar exclusion) |
| RK-33 | The watchdog's own thread inflates the measured CPU concentration (the observer is measured as part of the observed) | 🟡 med | MR-6 (T3 excludes the watchdog's own `native_id` from the per-thread attribution) |
| RK-34 | Concurrent lanes editing `daemon/api.py` move the cited line anchors under this plan | 🟡 med | MR-7 (re-verify/rebase rule — the anchors are re-derived by grep at impl time, and a moved anchor is re-pinned before the edit, not assumed) |
| RK-35 | Line-anchored instructions drift (a cited `:1234` no longer points at the same construct after an unrelated edit) | 🟡 med | MR-8 (phase1 §3.1 carries "re-derive every cited anchor by grep at impl time" as a pinned step) |
| RK-36 | Reader-side torn reads on the read-back surface — the router reads the watchdog's mutable fields directly, so a multi-field payload can mix two instants | 🟡 med | A-MR-a (D37/T19 — every watchdog-derived field is read from a snapshot taken under the watchdog's lock) |
| RK-37 | `POST /capture` behaviour with the kill-switch off was unspecified — an operator flips the emergency off and captures still appear | 🟡 med | A-MR-b (D38/D15 — kill-switch-off ⇒ manual capture REFUSED with the typed reason `watchdog_disabled`; plus the F-16 2/min manual rate cap) |
| RK-38 | An inline manual capture blocks the event loop for up to the 20 s capture deadline — the exact starvation class the instrument exists to catch — while a `to_thread` capture creates a second concurrent-writer class racing the watchdog | 🔴 critical | BLOCKER FOLD (D38/T19/AC27/AC28 — the handler ENQUEUES into a thread-safe slot the watchdog thread services on its next tick and returns 202 immediately; synchronous in-request completion is FORBIDDEN; the lock is never held across capture steps, so `status()` never waits behind a capture) |
| RK-39 | Manual-slot overflow — the depth-1 slot is already occupied (operator double-click; a 2/min rate cap against a slot depth of 1) and the request is silently lost or overwrites a 202-answered request | 🟡 med | B3 (non-blocking `put_nowait` into the depth-1 `queue.Queue`; `queue.Full` ⇒ the typed refusal **`capture_busy`**, nothing enqueued, no 202, and **no coalescing** — the refused operator retries) |

---

## 6. Success Criteria

Measurable, verifiable.

- **SC-1** With `ENSEMBLE_WEDGE_CAPTURE_CPU_PCT=1` / `_LAG_S=0.5` / sustain `=0`, a CPU-burning harness
  **produces ≥1 capture directory within 10s** of the burn starting, containing `manifest.json`,
  `stacks.txt`, `threads.csv`, `lag.json`, `metrics.json`. (P3 boot smoke.)
- **SC-2** With `ENSEMBLE_WEDGE_CAPTURE_LAG_S=2.0` **and `ENSEMBLE_WEDGE_CAPTURE_LAG_SUSTAIN_S=2`**, and an
  artificial event-loop block of **≥ 5 s (6 s in the leg, for margin)**, the **lag** trigger fires **and the capture
  names the lag** in `manifest.json.trigger_reason` (`sustained_loop_lag`) — proving the H2 shape is detectable
  *without* any CPU peg. **Why these numbers (B1):** under D4(b) the first blocked tick re-posts rather than
  accumulating, so a T-second block measures `lag_s ≈ T − one tick` and the over-threshold span is ~1 s less than
  the block; a 6 s block therefore measures `lag_s >= 5.0` and sustains the 2 s window, while the earlier
  `_LAG_S=1` sat on the healthy-tick noise floor and could not fire at all. **A1:** the never-fired case yields a
  **sample** (never a skip, never `0`), the probe keeps **at most one outstanding** post, and the sustain test is
  **wall-time** (D4b) — so the same verdict holds at a 1 s and a 5 s tick cadence.
- **SC-3** With `_THREAD_CONCENTRATION_PCT=50`, a single-threaded burn **fires the concentration trigger** and
  the capture's `threads.csv` attributes ≥85% of process CPU to exactly one `native_id`, with that thread's
  `name` and `comm` present. This is the H1-vs-H2 discriminator. **A15 (negative direction + the live-wedge leg):
  the discriminator also requires the NEGATIVE pair to hold** — in the lag-only leg (loop-thread `time.sleep(3)`)
  `sustained_cpu` **and** `thread_concentration` must **NOT** fire — and a **third leg** (loop-thread CPU spin, no
  await, 3–5 s) must fire `sustained_loop_lag` **+** `thread_concentration` attributed to `loop_thread_native_id`
  and **complete the capture** on that live wedge (a sleep-based block releases the GIL and cannot prove this).
- **SC-4** A capture written while the saver pool is fully checked out **still produces all files on disk** and
  **never blocks on the saver pool** (the pool is not touched — asserted by the writer pack: a spy pool that
  raises on any access is never called).
- **SC-5** The metadata row appears in `wedge_captures` with `trigger_reason`, `capture_dir`, `created_at`,
  `host`, `release_version`, `summary`, `metrics_snapshot` populated; the LIST endpoint returns it; the FETCH
  endpoint returns the full payload.
- **SC-6** A sustained wedge produces **exactly one** capture per cooldown window (300s) — the pack asserts
  10 simulated trigger ticks inside one cooldown window yield **1** capture directory and **1** row.
- **SC-7** Retention: with the count cap set to 3, a 5-capture run leaves **exactly 3** directories and **3**
  rows; the 2 oldest rows are removed and their files deleted. **A3:** the cap is **directory-authoritative**
  (computed by walking the capture dirs, never from the table) — the invariant **holds with the row write
  FORCED to fail**, so an orphan directory can never leak past the cap.
- **SC-8** The **effective AutoContinue mode** is recorded in the boot record and appears in the read-back
  status as `auto_continue_mode`, with values `on` / `off` / `unknown` only. The OFF early-return branch
  (`daemon/services/auto_continue_boot_pass.py:320-327`) records `off` **and** emits the extended
  `[BOOT_CONTINUE]` line — asserted by the boot-record pack.
- **SC-9** The `[BOOT_CONTINUE]` extension is **additive, not duplicative**. **A18 hybrid:** (i) **STATIC** — the
  number of `[BOOT_CONTINUE]` **logger call SITES** in `auto_continue_boot_pass.py` is **unchanged vs the base
  revision** (`639b7129b`); (ii) **RUNTIME, from the 5 named sites** — every `[BOOT_CONTINUE]` record emitted
  **from those 5 sites** carries `mode=` on both passes, asserted as a **positive set assertion** (the set of
  `mode=`-bearing sites **IS** exactly those 5 — the blanket "every emitted record" form was unsatisfiable:
  ~20 emit sites, 5 named). The idle-ON-boot vacuous case is documented; the boot **record** is the primary
  carrier.
- **SC-10** `ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART=0` appears **nowhere** in the diff (`git grep` gate on
  the branch), in any `.env`, in any systemd unit, or in any test fixture — asserted by a dedicated pack.
- **SC-11** **A11: the 13-file pin ceremony** (registry + 8 exact-set pin sites + the NEW registration pack +
  3 `meta.json`) lands in **one commit**; `PRIVILEGED_TOOL_CATEGORIES` has exactly **16** members; the member is
  appended **AFTER `"board-link"`** in every literal; the maintenancer `tools.allow` literal carries the 9th
  entry; the board trailing-array pin is updated; the 4 non-editing gates (frozen-tool-name discovery,
  literal-order chain, neighbour pin, baby-template scan) pass unmodified.
- **SC-12** The seal tripwire (`tests/test_chart_image_delivery_e2e.py::test_phase_d_does_not_modify_sealed_artifacts`)
  passes **unchanged**. **A17 (mechanical form):** the **full T1–T41 change list** ∩ the 35-row
  `SEALED_SHA_BASELINES` is **EMPTY** — `daemon/constants.py` being untouched is one row of that proof, not
  the proof.
- **SC-13** The prompt-artifact baseline check (`python3 scripts/protection/generate_prompt_artifact_baseline.py --check`)
  is **161/161 clean** after the `meta.json` allow-list edits.
- **SC-14** The isolated boot smoke runs in a `systemd-run` transient unit with a **cgroup assertion** (the
  unit's `/proc/<pid>/cgroup` proves isolation from the live `ensemble-main.service`) and a **SIGTERM-only**
  teardown; the live daemon's PID/epoch/`/livez` version are unchanged before and after.
- **SC-15** The staged release's migration delta is **exactly +1** file (**108 → 109** migration files, A10),
  the new table is referenced by **zero** files in the pre-stage tree (`git grep` at the base commit ⇒ 0 hits),
  and the stage log records `rollback_safe=true source=explicit` with the semantic reasoning written out
  (STANDING RULE) — with the **explicit `ENSEMBLE_ROLLBACK_SAFE=1`** in the stage invocation, because
  `stage.sh:316-317` greps the full history and `CONTAINS_CONTRACT_PHASE=true` already holds at base (RK-31).
## 6.1 Contract alignment with the sibling artifact

The sibling artifact `recurrence-evidence-matrix.md` (same directory) §9 defines a **canonical data dictionary**
that it declares "shared by this matrix and the W1 implementation". This plan **adopts** that vocabulary so the
capture payload and the matrix score the same fields. Adopted canonical names:

| Sibling §9 item | This plan's carrier | Where |
|---|---|---|
| `thread_stacks` | `stacks.txt` | Phase 1 D8 / T7 |
| `thread_cpu_attribution` | `threads.csv` — per thread: `native_id`, `name` (Python thread name), `comm` (OS thread name), `cpu_delta_s`, `share_pct`; plus derived `loop_thread_native_id` | Phase 1 D5/D6/D7, T3 |
| `loop_lag_histogram` | `lag.json` — bucketed samples + p50/p95/max | Phase 1 D4, T4 |
| `gc_rss_snapshot` | `metrics.json` | Phase 1 D20, T7 |
| `saver_pool_counters` | `metrics.json` — the **A2-amended** vocabulary (see §4.1): `pool_checked_out` (derived), `pool_size_current`, `pool_max`, `pool_waiting_clients`, `pool_checkout_timeouts` (always `None` + reason), `pool_requests_errors`, `pool_counters_source`. **The sibling's §9 row still lists the pre-A2 names — the reconcile note is recorded in Phase 3 T39 and the sibling file is NOT edited.** | Phase 1 D21 |
| `executing_turns_resume_history` | `db.json` — per executing turn: `instance_id`, `work_id`, `msg_count`, resume chain, `boot_epoch` | Phase 1 T10 |
| `boot_autocontinue_record` | the boot record — `effective_mode`, `raw_env_value`, `boot_epoch`, the `ContinueResult` counters, `decided_at` | Phase 1 D8/T8 |
| `checkpoint_store_census` | `db.json` — `checkpoints_rows`, `checkpoint_blobs_rows`, `checkpoint_blobs_bytes` | Phase 1 T10 |
| `stall_ledger_tail` | `checkpoint_perf_tail.json` | Phase 1 D18/T16 |
| `readyz_snapshot` | `readyz.json` | Phase 1 D19/T7 |
| `trigger_reason` | the metadata column — canonical values **`sustained_cpu`**, **`sustained_loop_lag`**, **`thread_concentration`**, plus `manual` for an operator drill | Phase 1 D3/D8, T5 |
| `captured_at` / `boot_epoch` | the metadata columns — **naive-UTC discipline** (the boot epoch is the process-global naive-UTC value from `daemon/services/boot_epoch.py:70-76`; never compare against `datetime.now(timezone.utc)`) | Phase 1 D10/T11 |

**Reconciled thresholds.** The matrix marks its own numbers **[P] = PROPOSED** and states "the W1
implementation plan owns final trigger defaults". W1's final defaults are: CPU **95%** of one core sustained
**30s**; loop lag **5.0s** sustained **15s**; thread concentration **85%** of process CPU sustained **30s**.
The matrix's R2 row proposes "loop thread ≥ ~80%" — W1's **85%** is inside that band, and because the capture
records the raw `share_pct` per thread, an analyst can score the matrix's R2 row at **either** threshold from
the same capture. No conflict.

**One accuracy correction carried into the plan.** The matrix notes that **`aget_tuple` is untimed** — and that
is true in-tree: the ONLY live `time_saver_op` call site is `daemon/persistence.py:465`
(`state = await time_saver_op("aget", instance_id, saver.aget(config))`), i.e. the **messages-API** `aget` path
— NOT the readiness sentinel's `aget_tuple` (`daemon/services/readiness.py:723`). Therefore the stall-ledger
tail covers the incident's ≥50s `aget` ops but **cannot** see a sentinel stall; sentinel visibility comes from
the `readyz_snapshot.checkpoint_saver` component + the `saver_pool_counters`. Phase 1 D18 and T16 state this.

---

## 7. Assumptions & Open Questions

### 7.1 Assumptions (explicit, flagged — not buried)

| ID | Assumption | If wrong |
|----|------------|----------|
| A-1 | The live daemon's data directory is resolvable in-process via `ENSEMBLE_DATA_DIR > DATA_DIR > ./data` (`daemon/api.py:545-551`). | Capture root falls back to `<resolved data dir>/wedge-captures`; if `app.state.data_dir` is unset the watchdog uses its own resolution and logs the resolved absolute path in every capture manifest. |
| A-2 | `psycopg` (sync) is importable in the shipped binary — `psycopg[binary]>=3.1.0` (`pyproject.toml:32`), locked at `psycopg 3.3.4` / `psycopg-pool 3.3.1` (`uv.lock`). | If the sync API is unavailable, the DB-side sub-capture is skipped (file-first design keeps the rest of the capture intact) and the manifest records `db_status=unavailable`. |
| A-3 | `psycopg_pool.AsyncConnectionPool.get_stats()` exposes the pool counters needed. **Field names are UNVERIFIED against psycopg_pool 3.3.1** (E2 flagged this). | The writer maps defensively: each counter is read by several candidate key spellings and reported `None` when absent — never a wrong number. P3 pins the resolved mapping against the installed version. |
| A-4 | `daemon/services/readiness.py` computes the composite on the loop and stores it at `app.state.readiness_composite` (**published** at `daemon/api.py:3657`; the **`None` init** is at `:2455` — approver-iter-002 sweep: each line cites the right event, per N3); the watchdog thread can read that attribute. | If the attribute is missing, the readyz snapshot records `status=unavailable` (never a fabricated healthy). |
| A-5 | The `/proc` filesystem is mounted (Linux). The tree is Linux-only (`daemon/services/resource_monitor/proc_readers.py` reads `/proc` + `/sys/fs/cgroup` unconditionally). | On a non-Linux host the CPU triggers degrade to unavailable and the lag trigger still works; the manifest records which triggers are available. |
| A-6 | The sibling artifact `recurrence-evidence-matrix.md` is being written by a sibling worker **into this same planning directory**. This plan **references** it; it does not author or edit it. | If the sibling lands with a different verdict-matrix vocabulary, the cross-reference table in `phase3-plan.md` §4 needs a reconcile pass — it is a documentation-only reconcile, no code impact. |
| A-7 | The capture's own one-shot DB connection can read `tasks` / `instances` / `pg_stat_user_tables` under the daemon's DB role. | The DB sub-capture degrades to `status=denied` with the SQLSTATE recorded; everything else survives. |

### 7.2 Open Questions (≤3 — genuinely open after the design pass)

**OQ-1 — Capture directory root: `<data_dir>/wedge-captures/` vs. an operator-specified absolute path.** *(Accurate attribution: the binding annex is **SILENT** on this question. The annex's **A3** shape (directory-authoritative retention rooted under the resolved `data_dir`) and **A8**'s call-site path handling **presuppose** the plan's recommended option — the `data_dir` root — and the governor council **raised no objection** to it. Retained here for the user's visibility, not as a blocking open decision.)*
- **Recommendation:** default to `<data_dir>/wedge-captures/` (resolved from the RUNNING process: `app.state.data_dir`,
  falling back to `ENSEMBLE_DATA_DIR > DATA_DIR > ./data`, `daemon/api.py:545-551`), with an explicit
  `ENSEMBLE_WEDGE_CAPTURE_DIR` override. Rationale: the data dir is already the daemon's writable scratch area,
  it survives upgrades (the release dir is versioned and immutable), and it is inside the existing backup/ignore
  story. The alternative — a sibling of `releases/` under `INSTALL_DIR` — puts writable, potentially large
  artifacts next to the immutable release tree and risks the `INSTALL_DIR` protection layer
  (`docs/runbooks/install-dir-protection-layer1-arm.md`).
- **Alternative:** default under `INSTALL_DIR/var/wedge-captures/`. Rejected here mainly because it couples the
  instrument's writable surface to the protected install dir; if the user prefers operator-visible paths
  outside the data dir, that is a one-line default change, not a redesign.

**OQ-2 — Should the privileged read-back tool be a NEW category (`wedge-capture`, 15→16) or an extension of the
existing `instance_read` category (15 unchanged)?** *(Accurate attribution: the binding annex is **SILENT** on this question. The annex's **A11** (the 13-file ceremony, which includes the three `meta.json` allow-lists) and **A13** (the holders offenders-scan pinned to exactly `{ari, leader, maintenancer}`) **presuppose** the plan's recommended option — the NEW category with the 3-agent allow-list — and the governor council **raised no objection** to it. Retained here for the user's visibility, not as a blocking open decision.)*
- **Recommendation:** NEW category `wedge-capture`, 2 tools (`wedge_capture_list`, `wedge_capture_get`).
  Rationale: the capture payload is a **privileged diagnostic artifact** (stack dumps, per-thread CPU maps,
  process RSS) that a general instance reader has no business fetching, and the allow-list is ari + leader +
  maintenancer — a different population from `instance_read` (maintenancer-only today). A new category keeps
  the privilege boundary auditable and makes the **13-file** pin ceremony mechanical (the "eight files" figure is **STALE per A11** — swept per **A-F2**).
- **Alternative:** add the two tools to `instance_read` and widen its allow-list to ari + leader. Rejected: it
  widens an existing category's blast radius (the instance_read gate is currently maintenancer-scoped), and it
  requires the same **13-file** pin ceremony anyway (the "eight files" figure is **STALE per A11** — swept per **A-F2**). The one real cost of the new category is the 15→16 pin
  churn — which is exactly what the 13-file ceremony exists to make safe.

**OQ-3 — Retention ceiling: 20 captures / 168h / 512 MB — is a 20-capture ceiling enough for the incident
class, or should the default count be higher?** *(Accurate attribution: the binding annex is **SILENT** on the cap VALUES, but its **A3** changes HOW they are computed (directory-authoritative, with the F-05 exclusions) and presupposes the plan's recommended 20/168h/512MB values; the governor council **raised no objection**. Retained here for the user's visibility, not as a blocking open decision.)*
- **Recommendation:** keep 20 captures as the default count cap, with 168h age and 512 MB disk caps.
  Rationale: the incident produced **5 boots** in ~33 minutes plus three 181s stalls — 20 captures is ~4
  incident episodes of headroom, while 512 MB bounds the worst case (a full stack dump of a large process is
  ~1–5 MB). Raising the count to 50 raises the disk ceiling's dominance (2.5 GB) without materially improving
  post-incident forensics, because the *recent* captures are the ones that matter.
- **Alternative:** a 50-capture / 2 GB default, if the operator expects multi-day multi-episode forensics on a
  host with free disk. This is a default-value change only — no structural impact — and both caps are
  env-tunable.

---

## 9. Appendix — machine-checkable D ↔ T map (F-18)

**Accepted** (F-18 asked for this only "if cheap from existing rows" — it is: the map is derived mechanically
from the `D#` references inside each task row, so it cannot drift from the rows it summarises). A decision with no
owning task row is shown as `—`; a task owning several decisions appears under each.

| Decision | Owning task(s) (explicit link) | Enforced in | Decision | Owning task(s) (explicit link) | Enforced in |
|---|---|---|---|---|---|
| `D1` | — | P1 | `D2` | T5 | P1 |
| `D3` | T5, T6 | P1 | `D4` | T4 | P1 |
| `D5` | T3 | P1 | `D6` | T3 | P1 |
| `D7` | — | P1 | `D8` | T6, T21, T26, T31 | P1 |
| `D9` | T8 | P1 | `D10` | — | P1 |
| `D11` | T10, T11 | P1 | `D12` | T11, T12 | P1 |
| `D13` | T5 | P1 | `D14` | — | P1 |
| `D15` | T1, T18 | P1 | `D16` | — | P1 |
| `D17` | T9 | P1 | `D18` | T16 | P1 |
| `D19` | T7 | P1 | `D20` | — | P1 |
| `D21` | T7, T24, T39 | P1 | `D22` | T11 | P1 |
| `D23` | T8, T11, T12 | P1 | `D24` | — | P1 |
| `D25` | T7 | P1 | `D26` | — | P1 |
| `D27` | — | P1 | `D28` | — | P2 |
| `D29` | — | P2 | `D30` | T23, T34 | P2 |
| `D31` | T22 | P2 | `D32` | — | P2 |
| `D33` | — | P2 | `D34` | T25, T37 | P2 |
| `D35` | T25 | P2 | `D36` | — | P2 |
| `D37` | T8, T19, T24, T39 | P2 | `D38` | T1, T15, T19, T26 | P2 |
| `D39` | — | P2 | `D40` | T12, T21 | P2 |
| `D41` | — | P2 | `D42` | — | P3 |
| `D43` | T36 | P3 | `D44` | — | P3 |
| `D45` | — | P3 | `D46` | — | P3 |
| `D47` | — | P3 | `D48` | — | P3 |
| `D49` | — | P3 | `D50` | — | P3 |
| `D51` | T1, T41 | P3 | `D52` | — | P3 |
| `D53` | — | P3 | `D54` | — | P3 |
| `D4b` | T5 | P1 | `D25` | T7 | P1 |
| `D25b` | T17 | P1 |  |  |  |

*Derived mechanically from **both** directions — the `D#` references inside every `| T# |` row **and** the `T#`
references inside every `| D# |` row — of `phase1/2/3-plan.md`; regenerate it after any row edit. Decisions still
showing `—` (27: D1, D7, D10, D14, D16, D20, D24, D26, D27, D28, D29, D32, D33, D36, D39, D41, D42, D44, D45, D46, D47, D48, D49, D50, D52, D53, D54) carry no explicit cross-reference in
either direction — they are enforced through their phase's task set and the acceptance criteria, not via a named
row link.*

---

## 8. Deliverable map

| Deliverable | Where |
|---|---|
| **D-1** Automatic wedge-capture watchdog (plan) | `phase1-plan.md` |
| **D-2** Read-back surfaces + CLI + runbook (plan) | `phase2-plan.md` |
| **D-3** `recurrence-evidence-matrix.md` | **NOT written by this plan** — sibling worker's deliverable, same planning directory. **It has landed** (34 KB, present alongside this plan set as of plan completion; it uses `R1…R12` for its requirements). Referenced and cross-checked as a sibling artifact in `phase3-plan.md` §2 (D53) and in the evidence pack (T39, AC55). **This plan does not edit it.** |
| **D-4** Test packs + isolated boot smoke + staging checklist (plan) | `phase3-plan.md` |

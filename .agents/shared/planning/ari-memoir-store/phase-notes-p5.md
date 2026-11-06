# Phase 5 notes — the wake briefing (wave 3)

**Lane:** `feature/ari-memoir-store` @ `/home/nea/ensemble-src-wt-memoir-store`.
**Wave:** 3 of 5 (parallel with the P3 coder — exclusive file ownership honoured:
this wave touched ONLY `daemon/services/memoir_briefing.py`,
`daemon/services/context_messages.py`, `daemon/services/instance_messaging.py`,
the two `test/packs/memoir_briefing_*.sh` packs + their two pytest suites, and
this notes file).
**Commit:** `1f2faf352` — *feat(memoir): phase 5 — the wake briefing (the seam
builds, the consumer writes the flag)* — pathspec-committed (the P3 lane's
`daemon/api.py` / `memoir_embedding_worker.py` / embedding tests were visible in
`git status` and left completely alone).
**Import resolution proven:** `import daemon` →
`/home/nea/ensemble-src-wt-memoir-store/daemon/__init__.py` (re-proven after
every shell re-entry; interpreter `/home/nea/ensemble-src/.venv/bin/python`).
**No manager constructed; no PG touched; no prod/live path touched.** Every
test engine is a throwaway in-memory SQLite via `StaticPool` +
`check_same_thread=False` (the briefing's reads run in `asyncio.to_thread`
workers; a plain `:memory:` URL hands every thread a FRESH empty database —
found live in this wave, fixed in the fixtures, house precedent
`tests/test_resource_pid_registry.py:579`).

## 1. T5.1–T5.7 outcomes

| Task | Outcome |
|------|---------|
| T5.1 | `CONTEXT_KIND_MEMOIR = "memoir"` added beside the snapshot-digest kind; `_stable_id_for("memoir", agent_id=…)` → `memoir:{agent_id}` (branch + canonical-table row + error-vocabulary update). **NO hoist-set edit** (D5.3/AM-40 verified: `daemon/_content_hardening.py::is_hoisted_injected` is truthy-keyed on `has_context_kind` — there is NO membership set) |
| T5.2 | `daemon/services/memoir_briefing.py` implemented in full: the two-zone renderer (`render_briefing_body`), the byte-pinned surfaces, the dynamic oldest-dropped-first fitter, the render-time backstop (24k ⇒ stable-in-full + dynamic-dropped + disclosure; 48k ⇒ withhold + WARNING), the persona-missing path |
| T5.3 | Seam wiring in `assemble_context_messages`: `_maybe_memoir_briefing_message` (whole-build ≤5 s budget wrapper) → `_assemble_memoir_briefing` (knob → off-loop row read → ari gate → flag check → builder). Wired on BOTH return paths (the `project_already_injected` early path restructured so its three exits share one memoir step — semantics preserved) |
| T5.4 | Consumer-side flag write in `instance_messaging.py` inside `if not is_retry:` AFTER the assemble call, gated on `memoir_block_present(_persistent_msgs, _memoir_agent_id)`, written via the `:4239-4242` `asyncio.to_thread(set_metadata, …)` shape; the agent id comes from the SAME `_proj_row` read the path already performs (no new read) |
| T5.5 | Budget/backstop/affordance pins GREEN; all five mutation legs RED→GREEN (§4) |
| T5.6 | Compaction-survival leg through the REAL `is_hoisted_injected` + the flag-write-site pin (drop the block ⇒ the consumer's predicate goes False ⇒ no write) |
| T5.7 | §3 below states D5.11 exactly |

## 2. AC1–AC7 outcomes

All seven GREEN. AC3's off-loop assertion is behavioral
(`test_flag_read_is_off_loop` — the fake repository records
`threading.get_ident()` per read; all reads ran on non-loop threads), the
builder-never-writes pin is behavioral (`test_builder_never_writes_the_flag` —
zero metadata writes recorded after a full turn-1 assembly), and the deadline
leg pins the exact budget-breach WARNING plus "no block, no write, turn not
delayed" (`test_hung_read_degrades_within_budget`, budget patched to 0.2 s).

## 3. Landing-note discipline (T5.7 / AC7 / D5.11 — stated exactly)

The assembly seam runs on **every** turn (both the first-turn path and the
`project_already_injected` path — the gate is deliberately OUTSIDE the
once-per-instance project gate), so the briefing **does** fire on a
**scheduler-sourced wake**: L4 governs the message **STAMP**, not the context
**BLOCK**, so L4 is not violated and **no source lane changes** (the
bare-HUMAN scheduler stamp is never "fixed"). Once-per-**instance** is
enforced by the `memoir_briefing_injected` flag, **read fresh each assembly**
(revive-safe). Disclosed residual: a long-lived instance's dynamic zone goes
stale with **no signal** — by design (DD-11); the v2 alarm-wake refresh is the
designated fix. Retry turns (`is_retry=True`) skip the whole assemble call in
the messaging path — correct, because a resumed checkpoint already carries
the block.

## 4. Pack inventory + the mutation matrix

| Pack | Result |
|------|--------|
| `test/packs/memoir_briefing_unit_test.sh` | PASS — 17 passed (`tests/unit/test_memoir_briefing_unit.py`) |
| `test/packs/memoir_briefing_seam_test.sh` | PASS — 10 passed (`tests/unit/test_memoir_briefing_seam.py`) |

Every mutation leg: plant → run → capture RED node IDs → `git checkout -- <my
committed file>` → re-run GREEN. The worktree's own files were committed
BEFORE mutating, so restores could never touch foreign work.

| # | Planted defect | RED node IDs | Restore |
|---|----------------|--------------|---------|
| M1 | Ceiling removed (`if stable_chars > CEILING:` → `if False:`) | `test_overage_24k_stable_in_full_dynamic_dropped_exact_disclosure`, `test_overage_boundary_between_ceiling_and_absolute`, `test_n3_char_token_ratio_of_admitted_worst_case` | GREEN 17/17 |
| M2 | Affordance dropped (`parts.append(MEMOIR_AFFORDANCE_LINE)` → `pass`) | `test_block_layout_zones_ordered_affordance_last`, `test_overage_24k_stable_in_full_dynamic_dropped_exact_disclosure`, `test_persona_missing_keepsakes_only_with_exact_prefix` | GREEN 27/27 |
| M3 | Stable-zone truncation attempted (`stable = stable[:CEILING]` planted) — must be UNREACHABLE | `test_overage_24k_stable_in_full_dynamic_dropped_exact_disclosure`, `test_withhold_48k_returns_none_and_warns`, `test_overage_boundary_between_ceiling_and_absolute` | GREEN 17/17 |
| M4 | Withhold/degraded path writes the flag (seam: `if _result is None: set_metadata(...)` planted) | `test_withhold_path_writes_no_flag`, `test_non_ari_gets_no_block`, `test_knob_off_no_block_and_no_flag_write` | GREEN 10/10 |
| M5 | **The builder writes the flag** (AM-22's worst failure mode planted in `_build_memoir_briefing_message`) | `test_builder_never_writes_the_flag`, `test_flag_write_site_guard_block_dropped_stays_unset` | GREEN 10/10 |

Regression (scoped, this box OOMs on farms): wave-1/2 memoir suites 154
passed; the coupled context-messages/snapshot-digest/blueprint/attestation
suites 116 passed + 1 skipped.

## 5. The byte-pinned surfaces (exact bytes)

- Block title: `[SYSTEM CONTEXT: Memoir]` (`MEMOIR_BLOCK_TITLE = "Memoir"`
  under the canonical `_make_context_message` prefix/suffix).
- Keepsake line template (`KEEPSAKE_LINE_TEMPLATE`):
  `- [keepsake] {title} (pinned_by={pinned_by}; provenance={pclass}) {pinned_reason}`
  — `None` title renders `(untitled)`, `None` reason renders empty,
  `None` pinned_by renders `unknown`; the `pclass` is the house provenance
  class captured ONCE per build (AM-24).
- Affordance line (`MEMOIR_AFFORDANCE_LINE`, the block's LAST line):
  `Memory remains queryable mid-session via memoir_recall(query) / memoir_timeline(since?, until?, q?).`
- 24,000-char overage disclosure (`MEMOIR_STABLE_OVERAGE_DISCLOSURE`):
  `[…stable zone is {chars} chars — {over} over the 24,000-char ceiling; pin admission exceeded its budget]`
- 48,000-char withhold WARNING (`MEMOIR_WITHHOLD_WARNING`, logged at WARNING):
  `[Memoir] briefing withheld — stable zone {chars} chars exceeds the 48,000-char absolute bound`
- Persona-missing prefix (`MEMOIR_PERSONA_MISSING_PREFIX`):
  `[persona unavailable — no approved persona version]`

## 6. N-3 — the measurement (not an assumption)

Fixture: deterministic English sized through the house estimator
(`daemon/loader.py:1027`, tiktoken cl100k); keepsakes admitted exactly as
D2.12 admits them (add pins until the cumulative line tokens would exceed
4,000).

```
[N-3 MEASUREMENT] admitted stable worst case: persona=1983t keepsakes=3800t
(12 pins) total=5783t -> shipped stable zone=26079 chars (escaped) |
measured char/token ratio=4.510 (DR-1 assumed 4.0) | 24k disclosure
fired=True | expected dynamic-zone survival at full stable admission=0
chars (≈0 tokens); at HALF stable admission ≈10961 chars of headroom.
```

Reading: cl100k on English prose measures ≈4.5 chars/token; DR-1's
"6,000 tokens ≈ 24k chars" assumed 4.0. At FULL admission the shipped
stable zone (~26k chars) therefore exceeds the 24,000-char ceiling and the
FIRST backstop rung fires by design (stable ships IN FULL, dynamic dropped,
disclosure appended) — the dynamic zone's survival at full admission is
ZERO, not positive. The binding invariant asserted: the admitted worst case
NEVER breaches the 48,000-char withhold bound (≈26k ≪ 48k — the stable zone
always ships, never truncated, never withheld). The backstop is doing
exactly what AM-43 built it for; no code change implied.

## 7. The flag-write site (D5.4/AM-22 proof)

**File/line:** `daemon/services/instance_messaging.py`, inside the
`if not is_retry:` block, immediately after the `if _persistent_msgs:`
confirmation, at the post-edit anchor ≈`:4924-4962` (pre-edit the assemble
call site was `:4877` and the `is_retry` gate `:4826` — both verified before
my edits shifted them). The write:

```python
if memoir_block_present(_persistent_msgs, _memoir_agent_id):
    await asyncio.to_thread(
        self._manager._instance_repository.set_metadata,
        instance_id, MEMOIR_BRIEFING_FLAG, True,
    )
```

The pin: `test_flag_write_site_guard_block_dropped_stays_unset` drops the
memoir block from the returned persistent half → `memoir_block_present` is
False → `fake_repo.metadata_writes == []` (the flag stays unset); mutation
M5 (builder-side write) is caught RED by
`test_builder_never_writes_the_flag`. The consumer-side `memoir_block_present`
accepts the kind match WITHOUT an agent id, so a failed row read cannot
strand the flag when the block is visibly present.

## 8. Anchor sweep — verified values + drift (P-7)

Verified against the tree BEFORE my edits (my own edits then shifted the
post-edit line numbers — noted per item):

| Anchor | Plan value | Verified |
|--------|-----------|----------|
| `assemble_context_messages` | `context_messages.py:1892` | ✓ def at `:1892` pre-edit |
| stamp site (ADR-5 kwargs) | `:175` | ✓ `_make_context_message` sets `injected_message=True` + `context_kind` |
| `CONTEXT_KIND_SNAPSHOT_DIGEST` | `:130` | ✓ |
| `_stable_id_for` def / kv branch | `:395` / `:481` | ✓ both |
| snapshot-digest builder | `:358-392` | ✓ region content matches |
| persistent-half append (template region) | `:2334-2337` | ✓ region verified (skills persistent append); line numbers shift post-edit |
| off-loop row read shape | `:373` | ✓ `await asyncio.to_thread(repo.get, instance_id)` |
| `if not is_retry:` | `instance_messaging.py:4826` | ✓ |
| assemble call site | `:4877` | ✓ |
| `project_injected` write shape | `:4238-4242` | ✓ `asyncio.to_thread(set_metadata, instance_id, "project_injected", True)` |
| persistent-half discard | `graph.py:8524-8533` | ✓ ("_persistent_msgs is intentionally discarded") |
| `_is_project_already_injected` | `graph.py:952` | ✓ — design.md §8.1's `_is_project_injected` confirmed a STALE LEAD (N-5); not "fixed" |
| truthy-keyed hoist | `_content_hardening.py:227-250` | ✓ `is_hoisted_injected` — `has_context_kind(msg)` truthy; NO set |
| house estimator | `loader.py:1027` | ✓ tiktoken cl100k |
| `BOOT_DB_TIMEOUT_S = 10` | `constants.py:70` | ✓ |
| MemoirRepository contract | wave-2 frozen | ✓ `get_approved_persona` / `fetch_keepsakes` / `fetch_latest` imported directly from `daemon.repositories.memoir.repository`; signatures unchanged |
| knob module | `daemon/tools/memoir_knob.py` | ✓ `resolve_memoir_knob(config).enabled`; the config tier is `manager.config` (NOT `daemon/services/memoir_knob.py` — no such file) |

Drift/decisions recorded (all implementation-level, none plan-contradicting):

1. **No `_memoir_repository` on the manager.** Wave 1 registered the memoir
   MODELS but no repository attribute, and the manager is a frozen file for
   this wave. The builder wraps the CALLER'S engine (`manager._engine`) in a
   fresh `MemoirRepository` per build — exactly the repository's own contract
   ("THE ENGINE IS THE CALLER'S"; never builds an engine, never reads ambient
   `POSTGRES_*`). If wave 4/5 later adds a manager-level repository, the
   builder can switch to `getattr(manager, "_memoir_repository", None)` with
   the `_engine` wrapper as fallback — a one-line change, no contract change.
2. **The dynamic zone excludes keepsakes.** `fetch_latest` returns keepsakes
   too (a keepsake is a STATUS on an episode row); rendering a pin in BOTH
   zones pays its tokens twice and blurs which zone carries it. The dynamic
   zone renders non-keepsake episodes only. Within D5.7's silence; recorded
   here per the deviation protocol's recording duty (not a STOP-class
   contradiction).
3. **Knob config tier = `manager.config`.** The seam passes
   `getattr(manager, "config", None)` into `resolve_memoir_knob` — the env
   tier still wins inside the resolver (call-time read per D5.13/AM-27).
4. **Turn-2+ path carries the gate.** D5.13's "a later enable delivers on the
   next turn" requires the gate on the `project_already_injected` path too
   (a knob flipped mid-life must deliver at the next assembly, which for an
   established instance is ALWAYS the already-injected path). The seam
   restructure preserves the path's exact prior semantics (verified by the
   116-test coupled-module regression).

## 9. Left for waves 4–7

- **Wave 4/5 (P4 coder):** the memoir TOOLS (`daemon/tools/memoir.py` — the
  write/pin surface whose admission arithmetic this briefing CONSUMES), the
  category registration + ari allow entry + the tools_note doctrine, and the
  `inner_soul` redirect. The briefing's keepsake line renders the provenance
  class via `capture_provenance_class` already — the tool result side is
  theirs (D4.10).
- **Wave 6/7:** the pack registrations in `.agents/tester/PACKS.md` (both new
  packs here follow the wave-2 house interpreter rule: worktree `.venv` →
  `MEMOIR_PACK_PYTHON` override → FAIL LOUD, no bare `python3` fallback),
  the full/regression + real-PG corpus, the boot smoke, and the Tier-2
  census. N-3's measurement is printed by the unit pack (`-s`); the pack log
  is the durable record.
- **Routed (not this wave):** the wave-1 risk item stands — the new packs
  fall back to `MEMOIR_PACK_PYTHON` on this `.venv`-less worktree; the
  pack-hygiene item from wave 1 covers them.

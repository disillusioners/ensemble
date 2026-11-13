# guideline-memoir-reflection-lane

**Status:** draft · **Feature:** Memoir Store v2 "The Reflecting Mind" — dream (reflection), briefing, keepsake, poignancy lanes. v1 store at v0.29.0 (decree 2026-11-06, migration `20261105_102821_create_memoir_tables.sql`); v2 reflection lane at **v0.29.15** (tag: "Memoir Store v2 'The Reflecting Mind' (memoir-v2 lane)"; merge `035f26ea1`; migration `20261109_021154_create_memoir_v2_tables.sql`). · **Audience:** operator / agent

## 1. Purpose & doctrine

Ari's durable episodic memory. She writes episodes (append-only, instant, never blocking the turn); the DREAM periodically consolidates them into cited claims (`memoir_syntheses`); recall serves the beliefs FIRST, raw episodes after. The persona surface (approved persona + keepsakes) is injected into ari's prompt as a two-zone briefing.

## 2. States

- **v1 master switch** `ENSEMBLE_MEMOIR_ENABLED`: absent/empty ⇒ **ON** + one loud per-boot WARNING; `true/1/yes/on/t/y` ⇒ ON; `false/0/no/off/f/n` ⇒ OFF; anything else ⇒ INDETERMINATE ⇒ **fail-CLOSED OFF**. Read at CALL time — no restart. OFF ⇒ tools refuse `memoir_disabled`, briefing not built, embedding worker parks, `inner_soul` still returns the loud not-recorded notice, DATA UNTOUCHED (not a delete). Deliberately diverges from `notify_user`'s fail-OPEN precedent: a mis-set memoir knob leaves unbounded, append-only, never-hard-deleted first-person rows.
- **v2 lane switches** `ENSEMBLE_MEMOIR_{REFLECTION,POIGNANCY,WAKE_REFRESH,PROPOSAL}_*`: enabled-flags class — unset ⇒ ON + WARNING; garbage ⇒ fail-CLOSED OFF + WARNING.

## 3. EXACT enablement steps

All v2 knob sections are **ENV-ONLY** — no `config.yaml` block, no `load_config` pass-through; a hand-added yaml block is silently ignored (and would shadow the env tier — defect §3-A4). Reflection defaults: syntheses/pass **5**; chars/synthesis **1000** (breach ⇒ row REJECTED `claim_too_long`); LLM calls/pass **4** + constellation **2**; trigger_sum **25**; nightly_cap **30**; cadence — tick budget **60.0s**, call timeout **45.0s**, tick interval **30s**, debounce base **60.0s** doubling to ceiling **960.0s**, force-fire **3600.0s**, scan window **2 days**. Poignancy: `ENSEMBLE_MEMOIR_POIGNANCY_ENABLED` (unset ON + WARNING), `BACKFILL_MAX` **0** (= disabled by default), `TICK_S` **20**, `BATCH_CAP` **10**. Numeric knobs NEVER raise and NEVER resolve to zero — garbage clamps to the constitutional default + WARNING (the classifier exists because a bare `Field(gt=...)` once took the daemon down at boot, house anomaly `33028b27`). The dream runs with no operator action when unset (ON).

## 4. The rules that bite

- **The nightly gate is evaluated FIRST** (V2-24): nightly advances the `reflected_at` watermarks so the in-day branch of the next tick idles — the order is load-bearing; the cadences cannot race.
- **The trigger is DERIVED per tick**, never a rolling counter (restart-durable, self-clearing; a counter would double-count on re-score and die on restart).
- The nightly pass runs inside quiet hours `02:00 ≤ now < 04:00 UTC`, oldest-unreflected-first, capped at 30 episodes, walking the pre-cutover backlog without the 2-day window restriction (C2/V2-27).
- **A misspelled v1 knob NAME is indistinguishable from unset ⇒ the surface is ON** — the only signal is the once-per-boot WARNING (accepted residual D0.8; mitigation is loudness, not prevention).
- "**19**" at v0.29.0 was the REFUSAL-CODE count only — never a category or tool count.

## 5. Verification & probe

- **Tool designation:** the `memoir` category is PRIVILEGED — entry **#19** of `PRIVILEGED_TOOL_CATEGORIES` (`daemon/tools/_tool_registry.py`): #19 **of 19** at v0.29.0; **of 21** today (v0.30.0 tree; `prompt-reload` #20, `prompt-assent` #21 landed after). The SIX tools — `memoir_write`/`memoir_pin`/`memoir_unpin`/`memoir_recall`/`memoir_timeline` + `memoir_about` (`MEMOIR_TOOL_NAMES`, `daemon/tools/memoir.py:170-178`; `memoir_about` handler :1243, schema :803; registered at `_tool_registry.py:315`/`:1924`) — are granted ONLY via `agents/ari/meta.json` `tools.allow: ["memoir"]`, and every tool body carries its own factory-captured `agent_id` gate (a literal tool name in an allow list bypasses the category gate). Tool vocabulary `VOCABULARY_VERSION`: **6** at v0.29.0 → **9** in the v0.30.0 tree (`daemon/checkpoint_metrics.py`).
- Briefing check: the memory block renders at prompt assembly (two zones, §6); the four `memoir_*` PG tables exist (`memoir_episodes` + tsvector/GIN, `memoir_persona`, `memoir_persona_versions`, `memoir_syntheses`).
- An unrun probe means the lane is UNVERIFIED at that release — unset-and-silent is not healthy; read the boot log for the knob WARNING.

## 6. Briefing zones & keepsake governance

Two zones under a hard **24,000-char** ceiling (`MEMOIR_SECTION_CHAR_CEILING = 24000`; tokens plan, chars are absolute — DR-1 supersession of the design's nominal ~12k). STABLE zone: approved persona + ALL keepsakes, rendered in full, never truncated (a DB predating the persona seed renders keepsakes-only under the pinned prefix). DYNAMIC zone: latest episodes, oldest dropped first under the token budget. **Keepsakes:** count cap **12 pins**, zone budget **4,000 tokens** (`MEMOIR_KEEPSAKE_ZONE_TOKEN_BUDGET = 4000`); **REJECT, DON'T EVICT** — at cap a pin is refused, "nothing is ever evicted (DD-3)" (`daemon/repositories/memoir/repository.py:350,559,900,1421,1430`). Pin is the keepsake SET written together (D2.12); a wrong `pinned_by` claim stays detectable (the byte-pinned template renders it identically on every keepsake line). The reflection claim (`memoir_reflection_state`, `claimed_at` + **15-minute STALE-TAKEOVER**, V2-35/SF5) plus the 1/day nightly watermark prevent double-running. A keepsake PROPOSALS zone (≤1,000 chars) rides the briefing (phase 6); a stable zone over 48,000 chars WITHHOLDS the briefing (loud WARNING). Poignancy = async 1–10 scoring of episodes (backfill fenced; `backfill_max=0` = disabled by default).

## 7. Evidence discipline

Rows live in the four PG tables. Both migrations are additive (`CREATE TABLE IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS` on the NEW tables only) and their FKs are INTRA-table: v1 `20261105_102821` carries ONE self-referencing FK (`superseded_by TEXT REFERENCES memoir_episodes(id)`, migration :98); v2 `20261109_021154` carries five (all intra-table). Additive + intra-table is why rollback safety stands — the earlier "zero FK" phrasing (v0.29.0 ceremony annotation) was recorded as imprecise and is corrected here, not repeated. Knob state is retrievable WITHOUT reading logs — `memoir_tool_help`/`memoir_knob_state` carry the resolved truth. Refusals use the typed vocabulary (`memoir_disabled`, `claim_too_long`, `memoir_query_required`, …). Boot emits exactly ONE knob WARNING per process (dedupe keyed on message text; a CHANGED value re-warns).

## 8. Rollback

Fix-loss-only: both migrations additive (`CREATE TABLE IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS` on the NEW tables only); the old binary lacks the lane and coexists with the schema. The switches are the operator rollback lever; data survives (the kill-switch is not a delete).

## 9. First real use

The v0.29.0 go-live decree ("the Memoir Store, Ari's memory, goes live", 2026-11-06) covers the store; first-production exercise of the v2 dream lanes — UNVERIFIED in the sources consulted (no deployment observation recorded in-tree).

## 10. Status

**Status: draft** (2026-11-13); verified against the v0.30.0-era tree (origin/latest @ `ee2b46d3b`, worktree `ensemble-src-wt-stage-v0300`). Gaps: first-real-use and the board id are UNVERIFIED inline (§9, §11).

## 11. Source-of-truth pointers

CHANGELOG store entry under the `[0.28.17]` heading (the v0.28.17 release dir was disposed; content rode v0.29.0); `daemon/tools/memoir_knob.py`; `daemon/config.py` (`MemoirReflectionConfig` :4239, `MemoirPoignancyConfig` :4538, `MemoirWakeRefreshConfig` :4704, `MemoirProposalConfig` :4796); `daemon/services/memoir_reflection_service.py`, `memoir_briefing.py`; `daemon/repositories/memoir/repository.py`; runbook `docs/runbooks/memoir-embedding.md`; planning `.agents/shared/planning/ari-memoir-store/`; board item — UNVERIFIED (no ENSEMBLE-* id found in-repo for the memoir lane).

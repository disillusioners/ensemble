# guideline-prompt-reload-compact-note

**Status:** draft
**Feature:** `prompt_reload(compact, compact_note)` — the compact-note lane; the agent's own distillation of the conversation is ARMED by the tool and later executed as a REQUEST by the agent's own model (the append method). The note is the request, not a layer; the same-model invariant is structural.
**Audience:** operator / agent / both

## 1. Purpose & doctrine

The compact-note lane is the **working-context counterpart of the memoir dream lane — the dream consolidates her episodic memory; the compact note consolidates her working memory.** The tool never compacts and never claims it did: ARMED is not COMPACTED. The note is free text and may name what should die. *The words were always enough; the parameter was not.*

## 2. States

- **unarmed** — no note armed. `prompt_reload()` with no arguments is byte-identical to today's receipt; the auto compaction path is untouched.
- **armed** — a note was armed. The TOOL receipt reports `outcome=armed`, `path=append`, `selection=the model made the final selection`, the note's length and sha256 prefix, and the "CONTEXT NOT YET COMPACTED" line. The compaction has NOT happened yet.
- **consumed** — the agent's own model executed the request and the swap landed AND persisted. There is **no agent-facing receipt for this state**: the tool already returned at arm time. The outcome is reported in the content-free AUDIT line (`prompt_reload_compact_note: … outcome=compacted path=append …`) and in the carrier (`compact_note_last_*`).
- **armed_partial_write** — the carrier write REPORTED a failure but the carrier HOLDS the arm (verified by reading it back). The receipt says so and the note is treated as armed.
- **deferred / refused / append_failed / empty_reply / tool_calls_reply / refused_reply** — honest disclosures; never a success receipt for work not done.

## 3. EXACT enablement steps

- Tool surface: `prompt_reload(compact=True, compact_note="...")`. The note is REQUIRED when `compact=true`; a note supplied with `compact=false` is refused (`prompt_reload_note_requires_compact`) — the coherence rule, not a feature.
- The note is capped at the **configured** cap — the ONE knob this lane introduced: `CompactionConfig.compact_note_max_chars` (default **12,000**; env `COMPACTION_COMPACT_NOTE_MAX_CHARS`; YAML `compaction.compact_note_max_chars`). An over-cap note is refused (`prompt_reload_note_over_cap`) — NEVER silently truncated. The cap is enforced in the tool AND inside the service-layer arm writer, so a non-tool caller cannot bypass it.
- Compaction disabled (`CompactionConfig.enabled=false`) refuses arming (`prompt_reload_compact_disabled`) — arming a note that can never land would strand it.
- The deliberate-compact rate limit is 900 seconds (a HARDCODED module constant, `daemon/constants.py`, NOT a config knob), enforced at arm time (`prompt_reload_compact_rate_limited`, echoing the remaining seconds). A malformed or future-dated rate-limit clock fails OPEN (returns 0, logged at WARNING) — deliberate, so a corrupt clock cannot strand an arm permanently. REAL EXPOSURE: the clock is stamped ONLY on a LANDED compaction, so a NON-LANDING arm can be re-armed each turn (the rate limit does not protect a note that never lands).
- No **required** config.yaml block (the cap knob is optional — it defaults to 12000 and honours the env override; no YAML entry is needed). No migration. No schema change. The arm carrier is `instances.instance_metadata` JSONB.
- **The refusal vocabulary is the house's 14 typed codes** (nine from the original prompt-refresh ladder + five from this lane: `note_required`, `note_over_cap`, `note_requires_compact`, `compact_disabled`, `compact_rate_limited`). The APPEND-path outcomes (`compacted` / `empty_reply` / `tool_calls_reply` / `refused_reply` / `append_failed` / `deferred`) are OUTCOME labels, **not** refusal codes — the census stays 14.
- **The refusal ladder is ORDERED and MASKS.** `note_over_cap` is evaluated before `compact_disabled` and `compact_rate_limited`, so a note that is both over-cap and (disabled / rate-limited) is reported as `note_over_cap` — the remediation advice names the cap, not the disabled/rate-limit state. Every refusal is still true and typed; only the surfaced cause is the one that fires first. The ladder is NOT reordered in this lane (the census re-pin rides a later lane).

## 4. The rules that bite

- **No model call of the daemon's own.** The append path makes NO model call — the compaction is produced by the agent's own next invoke. `COMPACTION_MODEL` governs the auto path only. Do not pin `COMPACTION_MODEL` expecting it to steer the note path; it is ignored there, structurally.
- **The note is the REQUEST, not a layer.** It is never re-rendered into the doc. Its provenance lives in the receipt as length + hash (content-free by doctrine).
- **The receipt never implies a compaction that did not happen.** `compacted` is written only after the swap lands AND persists; a failed persist leaves the arm armed and records `append_failed`.
- **The exchange is CONSUMED.** The tool call, its receipt, the appended request and the model's reply never survive into the tail — else the receipt's "CONTEXT NOT YET COMPACTED" line would sit under the doc that says the compaction happened, and the note would render twice.
- **The swap is ADJACENCY-SCOPED.** It fires only while the request is still the LAST message of the channel (i.e. on the first invoke after the append). A later, unrelated reply — including one in a later turn — never fires it.
- **The consumption crash window.** The swap rides the node's own task commit (the house's mid-turn durability mechanism) AND a mid-superstep persist through the seam. There is a persist-then-consume crash window: a crash between the two leaves the arm ARMED and the note **re-applies at the next append** — a bounded **DOUBLE-APPLY** that re-compacts the **already-compacted** channel. It is BOUNDED by the adjacency gate (the request must still be the channel's LAST message) and by the landed-only rate limit (the 900 s clock is stamped only on a LANDED compaction). The receipt reports `compacted` ONLY after the swap lands AND persists; a failed persist leaves the arm armed and records `append_failed`. (The window itself — the un-rolled-back crash — stays an accepted FOLLOW-UP; the disclosure names its measured consequence.)
- **Two model names, two surfaces.** The **agent-facing RECEIPT** names the model the compaction is **SIZED from** (`model_configured=` — the configured model at append time, which model answers being unwitnessable at append time). The **server-side AUDIT line** names the model that **SERVED** the turn (`agent_model=` — the effective model, the vision switch included). **The announced span is an ESTIMATE**, sized from the configured model: in the normal case it equals the compacted span exactly, but under FAILOVER (the reply served by a different model's window) it **MAY DIFFER** from the span that is actually compacted — the estimate is a best-effort announcement, not a promise.
- **H3 (estimate disclosure).** The append side announces the span it expects to compact; the swap side compacts the span it actually selects. The two are computed by the SAME selection recipe over the SAME (exchange-stripped) message set, so they agree **by construction for the normal case**. They CANNOT be symmetrized: the tools node runs BEFORE any reply exists, so it cannot know which model will answer — and under failover the answering model's window differs from the configured one. The mechanism is a known FOLLOW-UP; the DISCLOSURE is here so no reader treats the announcement as a promise.
- **The ceiling is skipped, not applied**, on this path (the gate is `n_sections > 0`; with `sections=[]` it never fires). The only size bound is the 600-token `GLOBAL_OVERVIEW_TOKEN_CAP` — the request's size instruction is the primary control, the cap is the backstop. The honest-truncation marker names the cap and the absence of detail (no "SECTION DETAIL below remains complete" claim).
- **The tail is spliced verbatim**, K = the house's preserved set, no knob.
- **The exchange strip is ANCHORED.** EVERY ``prompt_reload`` exchange (the tool-call AIMessage and all of its receipts) AND every stale compaction-request HumanMessage is removed from the tail source — a channel can hold a STALE exchange (a failed attempt whose arm stayed armed) next to the current one, and a first-match strip would leave the CURRENT exchange behind. The request under reply is never in the tail source, so it can never be removed by mistake.
- **KNOWN LIMIT — the refusal classifier (disclosed, not softened).** The daemon tells a compaction from a text refusal with a 120-character head scan for 12 refusal markers; it cannot ask the model twice. **Two-sided cost:** a genuine refusal OUTSIDE the 12 markers is misread as a compaction, and a compaction whose OPENING reads like a refusal is misread as a refusal (the safe side — no swap fires, the arm stays armed). **Fail-open direction, stated plainly: a refusal outside the 12 markers lets the swap fire, the arm is CONSUMED, and the audit row says ``outcome=compacted``.**
- **The exchange is consumed as a WHOLE group.** When the ``prompt_reload`` call was batched with other tools, the parallel sibling tool's result is CONSUMED WITH THE GROUP — it is removed from the context, not summarized into it and not carried into the tail.

## 5. Verification & probe

- Run the lane's pack: `bash test/packs/prompt_reload_compact_note_unit.sh` (459 nodes / 12 files) — PASS.
- The census pin: `tests/unit/tools/test_prompt_reload_refusals.py` asserts `len(REFUSAL_CODES) == 14` (re-derived by grep from the source frozenset).
- The graph-seam pins live in `tests/unit/tools/test_compact_note_graph_wiring.py` (append, swap, exchange-consumption, adjacency, refusal, audit fields, ceiling-skip, tail identity, zero decision calls).
- The golden artifact: the plain receipt's captured value is pinned by its sha256 (`0592b4a0…`), so a silent edit of the golden REDDENS the pin. **The auto-path compacted-DOC golden (AC13.2 / L8) is NOT present in the tree** — that e2e file was never landed; the auto path is unchanged by construction, but no auto-doc golden artifact exists to prove it.
- The ownership-chain validator: 76 checks / 0 FAIL / exit 0.

## 6. Evidence discipline

- The receipt is content-free: the note's LENGTH and sha256 PREFIX only, never the note body (the note may carry intimate empire content).
- The arming INFO line is content-free (length + digest prefix only).
- The append-outcome AUDIT line is content-free: the effective model id, the override's applied/configured state, the note's length + digest prefix, the spliced-tail count and the doc id — never conversation content, never note text.
- The note never reaches an agent-readable log.

## 7. Rollback

- fix-loss-only: rolling back removes the new code; the auto path is untouched; the arm carrier (`instance_metadata` JSONB) is pre-existing; no migration, no schema change.

## 8. First real use

Has not happened yet — the lane must not be relied upon until it does.

## 9. Status

draft

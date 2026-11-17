# guideline-prompt-reload-compact-note

**Status:** draft
**Feature:** `prompt_reload(compact, compact_note)` — the compact-note lane; the agent's own distillation of the conversation is ARMED by the tool and later executed as a REQUEST by the agent's own model (the append method). The note is the request, not a layer; the same-model invariant is structural.
**Audience:** operator / agent / both

## 1. Purpose & doctrine

The compact-note lane is the **working-context counterpart of the memoir dream lane — the dream consolidates her episodic memory; the compact note consolidates her working memory.** The tool never compacts and never claims it did: ARMED is not COMPACTED. The note is free text and may name what should die. *The words were always enough; the parameter was not.*

## 2. States

- **unarmed** — no note armed. `prompt_reload()` with no arguments is byte-identical to today's receipt; the auto compaction path is untouched.
- **armed** — a note was armed (and not yet consumed). The receipt reports `outcome=armed_pending_seam` plus the note's length and sha256 prefix; the compaction has NOT happened yet.
- **consumed** — the agent's own model executed the request and the swap landed. The receipt reports `outcome=compacted` (only then).
- **deferred / refused / append_failed / empty_reply** — honest disclosures; never a success receipt for work not done.

## 3. EXACT enablement steps

- Tool surface: `prompt_reload(compact=True, compact_note="...")`. The note is REQUIRED when `compact=true`; a note supplied with `compact=false` is refused (`prompt_reload_note_requires_compact`) — the coherence rule, not a feature.
- The note is capped at 12,000 characters. An over-cap note is refused (`prompt_reload_note_over_cap`) — NEVER silently truncated.
- Compaction disabled (`CompactionConfig.enabled=false`) refuses arming (`prompt_reload_compact_disabled`) — arming a note that can never land would strand it.
- The deliberate-compact rate limit is 900 seconds (a HARDCODED module constant, `daemon/constants.py`, NOT a config knob), enforced at arm time (`prompt_reload_compact_rate_limited`, echoing the remaining seconds).
- No config.yaml block. No migration. No schema change. The arm carrier is `instances.instance_metadata` JSONB.

## 4. The rules that bite

- **No model call of the daemon's own.** The append path makes NO model call — the compaction is produced by the agent's own next invoke. `COMPACTION_MODEL` governs the auto path only. Do not pin `COMPACTION_MODEL` expecting it to steer the note path; it is ignored there, structurally.
- **The note is the REQUEST, not a layer.** It is never re-rendered into the doc. Its provenance lives in the receipt as length + hash (content-free by doctrine).
- **The receipt never implies a compaction that did not happen.** `compacted` is written only after the swap lands.
- **The ceiling is skipped, not applied**, on this path (the gate is `n_sections > 0`; with `sections=[]` it never fires). The only size bound is the 600-token `GLOBAL_OVERVIEW_TOKEN_CAP` — the request's size instruction is the primary control, the cap is the backstop. The honest-truncation marker names the cap and the absence of detail (no "SECTION DETAIL below remains complete" claim).
- **The tail is spliced verbatim**, K = the house's preserved set, no knob. The compaction exchange is CONSUMED, not preserved — it must not appear in the tail (else the compaction text renders twice).

## 5. Verification & probe

- Run the lane's pack: `bash test/packs/prompt_reload_compact_note_unit.sh` (52 nodes) — PASS.
- The census pin: `tests/unit/tools/test_prompt_reload_refusals.py` asserts `len(REFUSAL_CODES) == 14` (re-derived by grep from the source frozenset).
- The two VERIFY-ONLY pins (`test_prompt_reload_tool.py`, `test_prompt_reload_pins.py`) pass unedited.
- The ownership-chain validator: 82 checks / 0 FAIL / exit 0.

## 6. Evidence discipline

- The receipt is content-free: the note's LENGTH and sha256 PREFIX only, never the note body (the note may carry intimate empire content).
- The arming INFO line is content-free (length + digest prefix only).
- The note never reaches an agent-readable log.

## 7. Rollback

- fix-loss-only: rolling back removes the new code; the auto path is untouched; the arm carrier (`instance_metadata` JSONB) is pre-existing; no migration, no schema change.

## 8. First real use

Has not happened yet — the lane must not be relied upon until it does.

## 9. Status

draft

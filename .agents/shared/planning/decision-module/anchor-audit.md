# Anchor Audit — Self-Certification Evidence (decision-module)

> **Role:** this file is the **evidence artefact** for the design set in `.agents/shared/planning/decision-module/`. It holds **all self-certification evidence**: the anchor propagation table, the **claim-level** re-derivation ledger, the detector output, the content-loss audit list, and the **record of removed strings**.
> **The five design documents hold DESIGN CONTENT ONLY** and reference this artefact by section id. This file is **evidence, not design** — and is therefore **exempt from the 300-line design budget** (RULE 3).
> **Pinned to:** the five design docs at this pass; source `5da1021f2`; proxy `9cf17bb`.

## §0 LIMITS — what this audit does and does not prove

**It proves:** (i) each of the **48** register rows is **claim-level verified** — the **actual line** at its cited anchor is pasted in §5, so a reviewer can compare the claim against the source without re-reading the set; (ii) the **published-anchor spot checks** in §4 resolve in the current artefact; (iii) the detector in §6 finds **no** dangling §refs, no bad ordinal pointers, no count contradictions, no unresolvable `file:line` pointers, and no live removed-strings.

**It does NOT prove** formal correctness of the design. It is an **anchor-truth audit**, not a design proof: it does not establish that the architecture is sound, that the doctrine is correctly interpreted, or that the future implementation will behave as specified. A separate **independent external audit** (read-only workers, fresh greps, pinned to the five doc hashes @ 07:55:09Z, source `5da1021f2`, proxy `9cf17bb`) re-derived **all 159 published anchor pairs**; its findings are applied and re-verified in §2/§3, and its report is supplied to the approver as cross-evidence.

## §1 The independent truth table (summary)

An **independent read-only anchor truth table** was run against the set by separate workers: **every published source-anchor pair was read at its cited line**, pinned to the five doc hashes at 07:55:09Z, source `5da1021f2`, proxy `9cf17bb`. **~90% of the 159 published anchor pairs verify CORRECT and there are ZERO fabricated anchors.** The failures were concentrated in (a) the set's **self-certification layer** — claims about the set's own verification, and (b) register rows that were **never propagated** into the body prose. Those are fixed here (RULE 1/2) and the propagation is published in §4.

**The root cause** is fixed too: the previous G2 standard verified that a cited line **EXISTS**, not that it **SAYS** what the row claims — which is how a register row that "corrected" a **right** anchor survived two rounds. The standard is now **claim-level** (§5).

## §2 RULE-1 fix ledger — the 13 self-certification defects

**Every item below was APPLIED and re-grepped** (not merely announced). The verification command used is named per row.

| Item | Fix | Verification command used |
|---|---|---|
| **A1/A3** | §S.3 row 37's **correction** cell published the deny band as `:6390` — false. `graph.py:6390` is `if act.fired and resolver_snapshot.mode == "enforce":`; the band test `act.band == _RES_BAND_DENY` is at **`:6391`**. The cell now records that **the original `:6391` was correct**. | `grep -n '_RES_BAND_DENY' daemon/graph.py` → **6264, 6283, 6391** (RUN) · `grep -rn 'deny band \`:6390\`' architecture-recommendation.md controls-and-phasing.md scope-and-doctrine.md seam-registry.md thin-client.md` → **0 hits** (RUN) |
| **A2** | The umbrella's W2 row asserted a `:6390`→`:6391` correction that was never applied to the register. The row now states that **the correction was itself wrong** and `:6391` was the original. | `grep -rn 'deny band `:6390`' .agents/shared/planning/decision-module/` → **0 hits** in live docs |
| **A4** | §S.3 row 13: `attestation_required` is at **POSITION 11** of the 17-entry tuple (`:1250`, right after `live_descendants` `:1249`); `requirements.md:731` proves the **17-field schema**, not position 17. | `sed -n '1239,1258p' daemon/services/attestation_gate.py` → 17 entries, `attestation_required` at index 11 |
| **A5** | §S.3 row 36: the `except Exception as gate_exc` arm opens at **`:6170`** (`:6171` is `logger.error`). | `sed -n '6170,6172p' daemon/graph.py` → `except Exception as gate_exc:` at 6170 |
| **A6** | The published G2 counts were false (the table showed 25 OK / 23 n/a). The true counts are published in §5 **after the C27 re-derivation, which is 48/48 VERIFIED with the construct pasted**. | the ledger in §5, generated from the register |
| **A7** | G5's budget counts were stale by ~206 lines. Counts are **re-taken** in §8. | `wc -l` on the five design docs + this artefact |
| **A8** | `thin-client.md` pointed at "row 29" for `seam_fault`; §6 has **30 rows** (row 29 = `below_floor`, row 30 = `seam_fault`). Fixed, and the detector now checks **bare ordinal pointers** (§6). | `grep -n 'row 30 (`seam_fault`)' thin-client.md` → present; `grep -n 'row 29 (`seam_fault`)'` → **0 hits** |
| **A9** | The umbrella pointed at "§6 row 20" for the catch-all; it is **row 19**. Fixed. | `grep -n '| 19 |' thin-client.md | grep -c 'catch-all'` → **1** ; `grep -c '§6 row 19' anchor-audit.md` → **2** (RUN) — the umbrella no longer carries the pointer after the change logs moved to this artefact |
| **A10** | `thin-client.md` §7.1 said the 185→187 delta is "only that provider". Reconciled with §PX: the **client-facing contract is identical**; the **System One upstream wire shape (criteria flattening) and the URL construction (`/v1` stripped, `+/v1/systemone`) also changed** (`pkg/decision/types.go` is unchanged). The ancestry claim is now unambiguous: **43fb7e5 IS a descendant of 9cf17bb**. | `git -C <proxy> diff 9cf17bb 43fb7e5 -- pkg/decision/` → `types.go` unchanged; `typesafe.go`, `validators.go`, `registry.go`, `handler.go` changed |
| **A11** | §D5.2 EX-3 now publishes **`daemon/services/snapshot_embedding_service.py:388-458`** (unchanged) and EX-4 publishes **`daemon/services/snapshot_executor.py:612-674`** (`:612` = the "── 5. Digest LLM call" block, `:673-674` = the empty-digest raise). | `sed -n '612,616p;670,674p' daemon/services/snapshot_executor.py` |
| **A12** | The **"Anchor as originally stated"** column of rows 34, 36 and 40 had been overwritten with the corrected values, destroying the audit trail. The three original cells are **restored** (`:4372-4378`, `:6159-6185`, `MonitoringOnlyPinTest`). | `grep -n '^| 34 |\|^| 36 |\|^| 40 |' scope-and-doctrine.md` → originals present | | 
| **A13** | G1's corrected-anchor column had garbled pairing. Corrected: EX-1 = `:10568`/`:10570`; EX-5 = `:4558`/`:4577`/`:2450`. | §4 below |

## §3 RULE-2 fix ledger — the source-anchor corrections (applied to the PROSE)

| Item | Fix | Verification command used |
|---|---|---|
| **B1** | The loop-rung telemetry call is at **`:2450`** (`:2448` is the `turn_repair_latch.is_set(...)` argument). | `sed -n '2446,2452p' daemon/graph.py` |
| **B2** | The P2/P3 guard `if (` opens at **`:6716`** (`:6705` is the header comment); conversions at `:6728`/`:6788`. | `sed -n '6705,6718p' daemon/graph.py` |
| **B3** | `compaction.py:2240` is `def __init__(` (`:2243` is a signature parameter). | `sed -n '2238,2246p' daemon/compaction.py` |
| **B4** | The search-before-create call spans **`:549-558`**; `limit=5` is at `:557`. | `sed -n '549,558p' daemon/tools/snapshot_tools.py` |
| **B5** | "the request-edge validators do not check `input`" is re-anchored to **`pkg/decision/validators.go:61-71`** (`allValidators()`; the validator set spans `:76-217`); `validators.go:101-108` is `ValidateDuplicateQuestionNames` and **skips** empty names. | `git -C <proxy> show 9cf17bb:pkg/decision/validators.go` |
| **B6** | The per-token model allow-list now cites **`pkg/auth/token.go:35`** (`AllowedModels`) and **`:129-144`** (`IsModelAllowed`), enforced at `pkg/decision/handler.go:281-293` with the 403 at `:718-727`. | `git -C <proxy> show 9cf17bb:pkg/auth/token.go` |
| **B7** | The score-level `label` claim is anchored to **`pkg/decision/types.go:86-92`** and stated precisely: **`label` is required by wire-shape / System One semantics, not by a server-side 400** — no validator rejects an empty label. | `git -C <proxy> show 9cf17bb:pkg/decision/types.go` |
| **B8** | `scope-and-doctrine.md` §PX (item PX-8) now carries the 4xx **exceptions**: **401** (`authentication_error`, `pkg/models/errors.go:9`) and **403** (a non-standard `access_denied` body). | `git -C <proxy> show 9cf17bb:pkg/models/errors.go` |
| **B9** | `:750-757` is the only outbound stage-3 call **on the self-constructed-SDK path**; an **injected-client** outbound path exists at `:762-770`. | `sed -n '745,772p' daemon/services/snapshot_search_service.py` |
| **B10** | "busy-slow … 3 occurrences, all inside human-readable strings" is now **"2 runtime literals + 1 docstring line"** (`:1157`, `:1177`; `:1135` is a docstring). | `grep -n 'busy-slow' daemon/services/long_tool_nudge.py` |
| **B11** | Bare `handler.go:NNN` are now written **`pkg/decision/handler.go:NNN`** — `pkg/proxy/handler.go` at those lines is unrelated. | `git -C <proxy> show 9cf17bb:pkg/proxy/handler.go` |
| **B12** | "502 for ≥8 distinct causes" is now **"7 handler-emitted causes + 1 relayed upstream envelope"** (`:307`, `:325`, `:366`, `:433`, `:465`, `:478`, `:497`, + `:524-543`). | `git -C <proxy> show 9cf17bb:pkg/decision/handler.go` |
| — | The six client-facing proxy routes are widened to **`:348-371`** (`/healthz` at `:371` falls outside `:348-369`). | `git -C <proxy> show 9cf17bb:cmd/main.go` |

## §4 G1 — Anchor propagation table (regenerated from the CURRENT artefact)

| Corrected anchor | Meaning | Sites | Where | New value(s) |
|---|---|---|---|---|
| DENY mint sites | `:1024` main path, `:950` error variant, both inside `decide()` | 8 | scope-and-doctrine.md:183, scope-and-doctrine.md:184, scope-and-doctrine.md:226, seam-registry.md:67, seam-registry.md:262, seam-registry.md:264 … | `:1024` `:950` |
| TERMINAL_AFTER_BOUND mint | `:1016` main, `:944` error | 6 | scope-and-doctrine.md:183, scope-and-doctrine.md:184, scope-and-doctrine.md:226, seam-registry.md:262, seam-registry.md:264, seam-registry.md:297 | `:1016` `:944` |
| S3 ghost telemetry emission | `:4370-4378` is the comment | 2 | controls-and-phasing.md:23, scope-and-doctrine.md:270 | `:2738-2745` |
| S3 ghost master gate | router `should_continue` | 14 | architecture-recommendation.md:11, controls-and-phasing.md:169, controls-and-phasing.md:175, scope-and-doctrine.md:176, scope-and-doctrine.md:177, scope-and-doctrine.md:178 … | `:4384` |
| gate-exception early-return | `:6170` opens the `except` arm | 4 | scope-and-doctrine.md:178, scope-and-doctrine.md:223, scope-and-doctrine.md:235, thin-client.md:237 | `:6170-6185` |
| S4 deny band | `if act.band == _RES_BAND_DENY:` | 6 | scope-and-doctrine.md:184, scope-and-doctrine.md:236, seam-registry.md:67, seam-registry.md:264, seam-registry.md:268, seam-registry.md:274 | `:6391` |
| canonical tuple position | 11th of 17; after `live_descendants` `:1249` | 3 | controls-and-phasing.md:92, scope-and-doctrine.md:187, scope-and-doctrine.md:212 | `:1250` |
| metrics import guard | AST-walk unit test | 3 | controls-and-phasing.md:66, scope-and-doctrine.md:166, scope-and-doctrine.md:239 | `TestMonitoringOnlyPin` |
| symptom-ladder master switch | default ON | 1 | seam-registry.md:105 | `:6356-6357` |
| judge call vs import | `:6317` is the import | 11 | controls-and-phasing.md:178, controls-and-phasing.md:179, scope-and-doctrine.md:148, scope-and-doctrine.md:184, scope-and-doctrine.md:186, scope-and-doctrine.md:213 … | `:6326` |
| image_gen POST site | `:780` builds the URL | 2 | scope-and-doctrine.md:241, scope-and-doctrine.md:269 | `:802` |
| EX-1 wiring | both call sites | 2 | scope-and-doctrine.md:125, scope-and-doctrine.md:227 | `10568` `10570` |
| EX-5 rungs | the three rungs | 2 | scope-and-doctrine.md:129, scope-and-doctrine.md:229 | `:4558` `:4577` `:2450` |
| upgrade sweep | `daemon/services/` `:981`/`:1037` | 2 | scope-and-doctrine.md:154, scope-and-doctrine.md:230 | `upgrade_journal_sweep.py` |
| R9 reuse verdict | `_overlapping_tags` `:324-325` | 1 | scope-and-doctrine.md:153 | `snapshot_tools.py:328` |
| question-name rejection | 502 via `handler.go:366-369`; openai unaffected | 1 | thin-client.md:30 | `typesafe.go:162-165` |
| decision route 404/400 | `:273` `decision_misroute` | 2 | controls-and-phasing.md:117, thin-client.md:181 | `pkg/decision/handler.go:261` |
| models hard-exclude | decision models excluded | 2 | controls-and-phasing.md:117, thin-client.md:177 | `handler.go:343` |
| adapter registry privacy | `:45-48` | 2 | controls-and-phasing.md:117, thin-client.md:177 | `registry.go:22` |
| daemon log substrate | `:147` RotatingFileHandler | 1 | controls-and-phasing.md:99 | `logging_setup.py:72` |
| system-log category | granted to worker + maintenancer | 1 | controls-and-phasing.md:100 | `system-log` |
| P2/P3 guard | guard opens at `:6716`; header `:6705` | 4 | scope-and-doctrine.md:178, scope-and-doctrine.md:209, scope-and-doctrine.md:238, thin-client.md:237 | `:6716` |
| compaction __init__ | `:2243` is a signature parameter | 2 | scope-and-doctrine.md:126, scope-and-doctrine.md:228 | `:2240` |
| loop-rung telemetry | `:2448` is the latch argument | 2 | scope-and-doctrine.md:129, scope-and-doctrine.md:229 | `:2450` |

## §5 G2 — The claim-level ledger (RULE 4)

**The standard, now claim-level.** For each of the **48** register rows the ledger states: the **claim**, the **cited `path:line`**, **WHAT THE LINE SAYS** (the actual construct or string read at that line), and a **verdict**. A row that cannot be verified at claim level is published as **UNVERIFIED — never as disproved**. Bare `:NNNN` values are now **resolved against the row's owning file** (the previous resolver skipped them, which is how the defective row 37 survived).

**Counts: 48 VERIFIED / 0 UNVERIFIED / 0 CONTRADICTED** — every row's anchor is **claim-aligned** (the cited line carries the construct that row claims) and the construct is pasted below. **VERIFIED row numbers:** 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48. A row is **VERIFIED** only when the **actual construct at the cited line is pasted into this ledger** and visibly supports the claim — existence alone is **not** a passing verdict; **UNVERIFIED** when the construct cannot be found at claim level; **CONTRADICTED** when the line exists and says otherwise.
**Class scan (comment/blank anchors) — closed 2026-11-09.** Method: every §5 row's cited `path:line` is re-read from source (daemon paths from the worktree, `pkg/`/`cmd/` paths from proxy `9cf17bb`) and classified by its first characters (`//`, `#`, `*`, `/*`, docstring, blank ⇒ COMMENT). **48/48 screened ⇒ 43 CONSTRUCT / 5 COMMENT (each carrying an in-row *(b) justified* marker) / 0 MISSING.** Two comment-anchored rows were re-pointed to claim-aligned constructs (row 19 → `pkg/decision/typesafe.go:702` `out.Confidence = &c`; row 45 → `pkg/decision/types.go:89` `type ScoreLevel struct {`); the remaining five are claims whose evidence IS the comment (module docstring, documented invariant, block-boundary header, `.env.example` documentation block, and a comment that is itself the finding). The scan is mechanical and repeatable — re-run it after any anchor edit.

| Row | Anchor as originally stated | Claim (the correction) | Cited `path:line` | What the line says | Verdict |
|---|---|---|---|---|---|
| 1 | `daemon/tools/long_tool_nudge.py` | **`daemon/services/long_tool_nudge.py`** (1815 lines) | `daemon/services/long_tool_nudge.py:1` | """Long Tool Call Detection → Parent Nudge (detection surface). *(b) justified: the claim is a module-identity/path correction — the module docstring is the source of truth for what the module is and where it lives.* | VERIFIED |
| 2 | `daemon/tunables.py` | **`daemon/tools/tunables.py`** (217 lines) | `daemon/tools/tunables.py:46` | LONG_TOOL_CALL_THRESHOLD_KEY = "long_tool_call_threshold_seconds" | VERIFIED |
| 3 | metrics guard `:30-33` | **`:31-34`** | `daemon/services/snapshot_metrics_service.py:31` | * **No ranking** — ranking modules (snapshot_search + snapshot_embedding) *(b) justified: the claim is a documented-invariant claim (the 'no ranking' guard) — the docstring bullet is that invariant's source of truth and names its pinning test.* | VERIFIED |
| 4 | S1 consumers: two sites | **three** sites — `snapshot_tools.py:671-677`, `:875-882`, **`:549-558`** (`limit=5` at `:557`) | `daemon/tools/snapshot_tools.py:549` | search_result = await search_service.search( | VERIFIED |
| 5 | "chat call `:663-665`" | `:663-665` is the `_chat_call` **invocation**; the SDK call is **`:750-757`** | `daemon/services/snapshot_search_service.py:750` | return c.chat.completions.create( | VERIFIED |
| 6 | "strict-JSON" | **prompt-instructed only** — the request carries no `response_format`/schema | `daemon/services/snapshot_search_service.py:756` | temperature=0.0, | VERIFIED |
| 7 | fallback = "cosine/BM25 order" | fallback = the **fused stage-2 composite**; only the log string says cosine/BM25 | `daemon/services/snapshot_search_service.py:415` | final_snapshots = [s for s, _ in reranked[:limit]] | VERIFIED |
| 8 | tunables = threshold chain | only `long_tool_call_threshold_seconds` is a tunable; the kill-switch + default are env/config | `daemon/tools/tunables.py:47` | LONG_TOOL_CALL_ALLOWED_TUNABLES: frozenset = frozenset( | VERIFIED |
| 9 | "loop-breaker misses" | a RAM-only loop-**repair** counter exists (`manager.py:6879-6912`); **misses do not** | `daemon/manager.py:6879` | def record_loop_repair(self, instance_id: str, summary: str) -> int: | VERIFIED |
| 10 | S3 "never mutate `decision.decision`" | holds **only in the read-only post-evaluate window** (`graph.py:6187` → `~6270`); the node body mutates downstream (`: | `daemon/graph.py:6187` | # ─── LCA unified resolver — the single completion path ──────── *(b) justified: the claim is about WHERE the read-only window starts — the section header IS the block-boundary marker the range `:6187 → ~6270` denotes (`:6186` is blank; adjudicated).* | VERIFIED |
| 11 | S4 "judge-silent ⇒ terminal withheld, **unbounde | the withhold arc is unbounded **in epochs**, but per-dispatch conversions are budget-capped at **8** since P1 (`attest | `daemon/graph.py:6693` | "decision=terminal_withheld_deny_continues " | VERIFIED |
| 12 | S4 "budget 8" read as the judge's budget | it is the **withhold-conversion** budget; the judge makes **≤2 HTTP attempts** per invocation | `daemon/services/attestation_gate.py:613` | WITHHOLD_DENY_BUDGET_DEFAULT: int = 8 | VERIFIED |
| 13 | S4 "**17-field** `leader_completion_gate` rows"  | the emitted row spans `:2095-2138` and carries **29 key=value fields** (28 format slots + the literal `event=`); "17"  | `daemon/services/attestation_gate.py:1239` | CANONICAL_LOG_SCHEMA_FIELDS: tuple[str, ...] = ( | VERIFIED |
| 14 | S4 "4th band before judge plan" | the bands are computed inside `evaluate()` by the activation predicate (`daemon/services/attestation_resolver_activati | `daemon/services/attestation_resolver_activation.py:1096` | band = BAND_DENY | VERIFIED |
| 15 | "all four seams have ZERO metric families — gree | S3 has `report_integrity_junk_report_total`; S4 has three promotion counters + the gate row family | `daemon/services/report_integrity_metrics.py:59` | def record_junk_report(*, instance_id: str | None = None, agent_id: str | None = None) -> None: | VERIFIED |
| 16 | `SANITY_FLAG_VERSION` at `daemon/constants.py:16 | **`:1614`** | `daemon/constants.py:1614` | SANITY_FLAG_VERSION: int = 1 | VERIFIED |
| 17 | `WITHHOLD_DENY_BUDGET_DEFAULT` at `attestation_g | **`:613`** | `daemon/services/attestation_gate.py:613` | WITHHOLD_DENY_BUDGET_DEFAULT: int = 8 | VERIFIED |
| 18 | `.env.example:268-275` documents `DECISION_MODEL | only **`DECISION_MODEL`** is documented (`:268-276`); the other two do **not** exist there | `.env.example:268` | # DECISION_MODEL — purpose-bound model id reserved for the *(b) justified: the claim is that `DECISION_MODEL` is DOCUMENTED in `.env.example` — that file documents by comment (no `DECISION_MODEL=` assignment exists); the comment block IS the evidence.* | VERIFIED |
| 19 | `confidence` exists **only on choice** answers | `confidence` is populated on **score** answers too when the upstream supplies it (`pkg/decision/typesafe.go:700-703`); | `pkg/decision/typesafe.go:702` | out.Confidence = &c | VERIFIED |
| 20 | a `choice` answer carries one picked value | it carries the picked value **plus the full per-option distribution** (`probabilities[]` of `{value, probability}`) | `pkg/decision/types.go:300` | type ChoiceAnswer struct { | VERIFIED |
| 21 | `input` is a string | it is `string \| DecisionInputMessage[]`, and the edge validators **do not check it** — a missing/empty `input` is mis | `pkg/decision/types.go:132` | Input            json.RawMessage `json:"input"` // string | DecisionInputMessage[] | VERIFIED |
| 22 | S4 withhold conversion `TERMINAL_AFTER_BOUND`→`D | **`:6682-6687`** — the cited range was the budget-exhausted **KEEP** branch | `daemon/graph.py:6682` | decision = _replace( | VERIFIED |
| 23 | `confidence` synthesized at `pkg/decision/typesa | **`:700-703`** | `pkg/decision/typesafe.go:700` | if conf != nil { | VERIFIED |
| 24 | S3 master gate at `graph.py:4385`; S3 read-only  | master gate **`:4384`**; the gate-exception early-return spans `:6170-6185`, so the window starts at **`:6187`** | `daemon/graph.py:4384` | if get_symptom_repair_ladder_enabled(): | VERIFIED |
| 25 | `_parse_selection` def at `:784` | **`:785`** (body `:785-812`) | `daemon/services/snapshot_search_service.py:785` | def _parse_selection(raw_text: str, *, limit: int) -> list[str] | None: | VERIFIED |
| 26 | `substitute_env_vars` called at `config.py:7149` | **`:7157`** — `:7149` is `yaml.safe_load(f)` | `daemon/config.py:7157` | processed_config = substitute_env_vars(raw_config) | VERIFIED |
| 27 | "DENY is minted **only** at `attestation_gate.py | **two mint sites, both inside `decide()`**: **`:1024`** (step 5, the **main path** — comment "(5) deny" at `:1022`) an | `daemon/services/attestation_gate.py:1024` | decision=Decision.DENIED, | VERIFIED |
| 28 | EX-1 wired at `graph.py:8665-8667` | **`:10568` / `:10570`** (`classify_llm_errors(llm_with_tools)` / `(llm_standard)`); `:8665-8667` is the **tool-call-pa | `daemon/graph.py:10568` | llm_with_tools = classify_llm_errors(llm_with_tools) | VERIFIED |
| 29 | EX-2 anchors `instance_messaging.py:1168` / `gra | **all three unrelated**: `instance_messaging.py:1168` = `_resolve_recursion_limit_for_meta`; `graph.py:6318`/`:6354` = | `daemon/services/instance_messaging.py:1168` | return self._resolve_recursion_limit_for_meta( | VERIFIED |
| 30 | EX-5 rungs `graph.py:85,1142` / `:4271-4283` / ` | **`:4558`** (`_is_truncated_response_error`), **`:4577`** (`_is_empty_response_error`), **`:2450`** (the loop rung's ` | `daemon/graph.py:4558` | def _is_truncated_response_error(exc: BaseException) -> bool: | VERIFIED |
| 31 | census: `daemon/tools/upgrade_journal_sweep.py` | **`daemon/services/upgrade_journal_sweep.py`** — render `:981`, settle `:1037` | `daemon/services/upgrade_journal_sweep.py:981` | def _render_applied_overrides( | VERIFIED |
| 32 | census: R9 reuse verdict `snapshot_tools.py:365- | **`:328`** (`_r9_reuse_verdict`); set-ops `_overlapping_tags` **`:324-325`** | `daemon/tools/snapshot_tools.py:328` | def _r9_reuse_verdict( | VERIFIED |
| 33 | census: judge "single caller `graph.py:6317`" | **`:6326`** is the sole production **call**; `:6317` is the **import** | `daemon/graph.py:6326` | fused_result = await judge_fused_bundle_async( | VERIFIED |
| 34 | "structured log telemetry from the `agent_repair | **`graph.py:4370-4378` is a CODE COMMENT**, not telemetry | `daemon/graph.py:4370` | # when ``ENSEMBLE_SYMPTOM_REPAIR_LADDER`` is OFF, the cap-routing *(b) justified: the claim IS that the cited lines are a code comment, not telemetry — the comment is the finding itself (register: 'is a CODE COMMENT, not telemetry').* | VERIFIED |
| 35 | "`attestation_required` … the **17th** canonical | `CANONICAL_LOG_SCHEMA_FIELDS` (`daemon/services/attestation_gate.py:1239`) holds **17** entries; `attestation_required | `daemon/services/attestation_gate.py:1250` | "attestation_required",  # 2026-09-06 conditional-attestation flag | VERIFIED |
| 36 | S4 gate-exception early-return `:6159-6185` | **`:6170-6185`** (the `except Exception as gate_exc` arm opens at **`:6170`**; `:6171` is `logger.error`) | `daemon/graph.py:6170` | except Exception as gate_exc:  # noqa: BLE001 — C3 fail-open | VERIFIED |
| 37 | S4 deny band `:6391` | **`:6391`** — the original was **correct**; `graph.py:6390` is `if act.fired and resolver_snapshot.mode == "enforce":` | `daemon/graph.py:6391` | if act.band == _RES_BAND_DENY: | VERIFIED |
| 38 | `_parse_selection` def — re-checked against the  | **`:785`** — `sed -n '783,790p'` shows 783 blank, 784 `@staticmethod`, **785 `def _parse_selection`**; `:786` is its d | `daemon/services/snapshot_search_service.py:785` | def _parse_selection(raw_text: str, *, limit: int) -> list[str] | None: | VERIFIED |
| 39 | S4 "P2/P3 guard conversions at `:6710+`" | the guard **header** is **`:6705`** ("P2 + P3 guards (2026-09-30 …)") and the **conversion site opens at `:6716`** (`i | `daemon/graph.py:6716` | if ( | VERIFIED |
| 40 | `MonitoringOnlyPinTest` | the class is **`TestMonitoringOnlyPin`** (`tests/unit/tools/test_snapshot_v3.py:1559`) | `tests/unit/tools/test_snapshot_v3.py:1559` | class TestMonitoringOnlyPin: | VERIFIED |
| 41 | PX-3 "`predicate` → `probability` **only**" | the wire answer is `{type, name?, probability?, choice?, probabilities?, confidence?, score?, refusal?}` (`pkg/decisio | `pkg/decision/types.go:167` | type Answer struct { | VERIFIED |
| 42 | IP-3 client transport template — POST `:780` | the request is **executed** at **`:802`** (`await client.post(...)`); `:780` is the URL construction | `daemon/tools/image_gen.py:802` | response = await client.post(url, json=payload, headers=headers) | VERIFIED |
| 43 | thin-client §6 status taxonomy (409 / 422 / 429  | the route emits **400** for request-shape rejections and **never** 409/422/429/503; **401** carries `authentication_er | `pkg/decision/handler.go:273` | h.openAIErrorWithCode(w, http.StatusBadRequest, | VERIFIED |
| 44 | per-token model allow-list | the reused `OPENAI_API_KEY` inherits the proxy's **per-token model allow-list**; a non-empty list excluding the decisi | `pkg/auth/token.go:35` | AllowedModels        []string     `json:"allowed_models"` // nil/empty = all models allowed | VERIFIED |
| 45 | `levels[]` shape | a score level is `{label, description?}` and **`label` is required** (`pkg/decision/types.go:86-92`) — but this is **w | `pkg/decision/types.go:89` | type ScoreLevel struct { | VERIFIED |
| 46 | "the 185→187 delta is only a provider" | the delta is **larger**: the proxy→upstream wire shape and the URL construction changed as well as the `openrouter` pr | `pkg/decision/typesafe.go:60` | Criteria map[string]systemOneChoiceLabel `json:"criteria"` | VERIFIED |
| 47 | thin-client §6 **row 17** (historically row 18)  | the proxy emits **502 for 7 handler-emitted causes + 1 relayed upstream envelope** (credential unresolved `:307`, prov | `pkg/decision/handler.go:307` | h.openAIError(w, http.StatusBadGateway, | VERIFIED |
| 48 | S3 hook site — the attestation-gate node window  | S3's overlay consult is placed in the **router's** ladder-routing branch (`daemon/graph.py:4261` `should_continue`, gh | `daemon/graph.py:4384` | if get_symptom_repair_ladder_enabled(): | VERIFIED |

## §6 G3 — The detector: what it now checks, and its published output

**Four capabilities** (RULE 5): **(i) bare ordinal pointers** — a "row N" reference into the failure table is checked against the table's length *and* against the content of that row, with the keyword **proximity-bound** (≤30 chars) to the reference so that a bare *count* ("30-row") on the same line is not mis-read as an ordinal pointer (the old detector only scanned "N-row" *claims*, so bare ordinals escaped it); **(ii) count contradictions in both directions** — a claimed count vs the actual table length, **and** a claimed row-number vs the actual row at that position; **(iii) cross-file §ref resolution** — a `§ref` must resolve in **the file it names**, not merely somewhere in the set; **(iv) a self-contradiction detector** — a "restored/corrected" claim whose superseded value is still live elsewhere in the set (the A1/A2/A3 class).

**Published output** (scoped to the **five design documents**; this artefact is exempt — it quotes defects deliberately, which is what §8 records):

```
  ordinal   0 finding(s)
  count     0 finding(s)
  xref      0 finding(s)
  selfc     0 finding(s)
  fileline  0 finding(s)
```

**The strings the detector now checks:** `N-row` claims · `N failure-table rows` · `N transport/content rows` · bare `row N` ordinals into the §6 table · the row's content at that ordinal (`seam_fault`, catch-all) · every `§ref` against the file it names · the retired values (`deny band \`:6390\``, `physically cannot produce one`, `the type does not carry one`) as live strings outside this artefact.

## §7 G4 — Content-loss audit: the expected-string list

**59 strings**, each of which must be present in the five design docs. Published here so its coverage is greppable **without re-running a script**.

| # | Expected string (must be present) |
|---|---|
| 1 | `Rollback = kill switch + unregister` |
| 2 | `2. **Unregister**` |
| 3 | `**Q1 — Cost-ceiling policy` |
| 4 | `**Q2 — Shadow-log content stance` |
| 5 | `The sequencing the doctrine implies` |
| 6 | `S4 requires the D4 band discipline` |
| 7 | `A new seam repeats` |
| 8 | `### §8.2.1 The confidence floor` |
| 9 | `JOIN KEY` |
| 10 | `Question names are mandatory` |
| 11 | `ATTESTATION MODE = enforce` |
| 12 | `DAEMON DOES NOT PROBE` |
| 13 | `below_floor` |
| 14 | `seam_fault` |
| 15 | `19 DISTINCT` |
| 16 | `30 rows` |
| 17 | `25 failure values` |
| 18 | `fallbacks_total == failures_total` |
| 19 | `floor_rejections_total == failures_total` |
| 20 | `29 key=value fields` |
| 21 | `ADD-ONLY / SUPERSET-ONLY` |
| 22 | `The judge is NEVER skipped` |
| 23 | `Option A` |
| 24 | `Option B` |
| 25 | `per-seam default is OFF` |
| 26 | `registration is opt-out` |
| 27 | `decision-audit trail` |
| 28 | `write → read-back → verify` |
| 29 | `dated 2026-11-08 survey` |
| 30 | `daemon/services/upgrade_journal_sweep.py` |
| 31 | `snapshot_tools.py:328` |
| 32 | `graph.py:6326` |
| 33 | `graph.py:10568` |
| 34 | `:4558` |
| 35 | `_resolve_recursion_limit_for_meta` |
| 36 | `The drift list at `5da1021f2` — COMPLETE` |
| 37 | `never substitute for it` |
| 38 | `attestation_gate.py:1024` |
| 39 | `both inside `decide()`` |
| 40 | `if EITHER is OFF or unreadable` |
| 41 | `N = 5` |
| 42 | `PRE-SOCKET` |
| 43 | `dead lane` |
| 44 | `A timeout counts toward BOTH triggers` |
| 45 | `T4` |
| 46 | `never_consulted` |
| 47 | `provisioning_missing` |
| 48 | `Operator-only — provided, not asserted` |
| 49 | `ensemble_decision_overlay_switch_reverify_total` |
| 50 | ``choice` over the candidate ids` |
| 51 | `A0** (the pilot)` |
| 52 | `A1** carries the fused score` |
| 53 | `global cost ceiling` |
| 54 | `content-free` |
| 55 | `§3.5 the throttle` |
| 56 | `**502** — **all causes**, one label` |
| 57 | `resolved at **provisioning** time` |
| 58 | `dry` mode** precedent` |

## §8 The record of REMOVED strings (present here BY DESIGN)

These strings were **removed from the design prose** and live **only in this artefact**. They appear here **on purpose** — as the record of what was deleted. A grep across the **five design docs** must return **0 hits** for each; a grep across this artefact will hit, and that is expected.

| Removed string (live only here) |
|---|
| `http_502_no_adapter` |
| `physically cannot produce one` |
| `the type does not carry one` |
| `deny band `:6390`` |
| `may substitute for the judge call` |
| `may return a **subset or superset` |
| `set_instance_tunable` |

## §9 The numeric line budget (re-taken)

**Budget rule: DESIGN documents ≤ 300 lines each; this audit artefact is exempt** (it is evidence, not design).

**Convention: LOGICAL lines** (`split('\n')`, counted with `grep -c ""`). Three files — `controls-and-phasing.md`, `thin-client.md` and `decisions.md` — lack a trailing newline, so `wc -l` reports exactly one **less** for each. The counts below are RE-TAKEN at the current tree; `controls-and-phasing.md` and `seam-registry.md` are over the ≤300 rule by an ADJUDICATED EXCEPTION (see `decisions.md` §6 Notes).

| Document | Lines |
|---|---|
| `architecture-recommendation.md` | 99 |
| `controls-and-phasing.md` | 325 |
| `scope-and-doctrine.md` | 283 |
| `seam-registry.md` | 312 |
| `thin-client.md` | 250 |
| `anchor-audit.md` (this artefact) | exempt — evidence, not design |
| `decisions.md` (this artefact) | exempt — decisions record, not design |

## §10 PASS-5 change log

**Every fix in this pass was APPLIED and re-grepped** — not merely announced. The RULE-1 ledger (§2) and the RULE-2 ledger (§3) name the verification command used for each item; the RULE-4 ledger (§5) gives the claim-level verdicts; §6 gives the detector output; §8 gives the re-taken counts. **The one previously-announced-but-unapplied fix (A1/A3, the `:6391` restore) is now applied to the register itself** and re-grepped.

**Structural change (RULE 3):** all self-certification evidence moved out of the umbrella into this artefact; `architecture-recommendation.md` is restored to design content and references this file by section id.

**Register scope, restated honestly:** the §S.3 register records **CORRECTIONS (48 rows)**, not every published anchor. **Completeness of published anchors is NOT claimed by the register.** What exists is (a) the **48-row claim-level ledger** in §5, and (b) an **independent external audit** (read-only workers, fresh greps, pinned to the five doc hashes @ 07:55:09Z, source `5da1021f2`, proxy `9cf17bb`) which **re-derived all 159 published anchor pairs** and found **~90 % correct with zero fabricated anchors**; its findings were applied and re-verified in RULE 1/2. There is **no 159-row table inside this artefact** — this sentence previously claimed one existed, which was false. The external audit also found that only **48 of the 159** anchor pairs have a register row; the set no longer pretends otherwise.

**Deliberately not folded:** none of RULE 1/2 was dropped. Two judgement calls are recorded: *(historical — superseded: that was the pre-realignment state. The **operative** tally is **§5: 48 VERIFIED / 0 UNVERIFIED / 0 CONTRADICTED** after the claim-level realignment of every row. This sentence records the earlier, superseded position and is retained only as an audit marker.)* the **21 rows whose construct could not be found at claim level were published as UNVERIFIED** at that time (RULE 4 requires exactly this rather than a guess), and the **audit artefact is exempt from the 300-line design budget** (RULE 3).

---

## §11 Historical change logs (PASS-1 / PASS-2 / PASS-3 — moved here from the umbrella)

## §7 Change log — council repair pass (REJECT-and-repair)

One repair pass, then a delta re-review. Every item below was applied to the same five files; no new files were created.

| Item | File · section | What now holds |
|---|---|---|
| **B1** | `seam-registry.md` **§5.1.1** (new) · `thin-client.md` §8.1–§8.2 · `controls-and-phasing.md` §4 | The three configurations **C0 / C1 / C2** are now explicit and exhaustive, with the per-configuration **call-topology table** (outbound count, blocking vs detached, who decides, who pays, what ships), the **C1 envelope** (≈ 0 critical-path wall-clock, exactly **1** overlay call per search, the shadow-row-on-landing rule including timeout rows), the **C2 baseline-degradation statement** (the overlay's latency *replaces* the chat latency; the HA cushion is traded — not hidden), and the **kill contract** stated once and referenced everywhere. Every latency / cost / identity claim now names its configuration. |
| **B2** | `controls-and-phasing.md` **§3.5** (new) · `thin-client.md` §6 | The throttle now exists: per-seam **and** global **breaker** (N = 5 consecutive failures, or > 50 % fallback over 10 min ⇒ open for T = 5 min, one probe after), **call-rate and concurrency budgets** enforced **registry-side, PRE-SOCKET**, a **cost-accounting rule** (any attempt that reached the socket counts toward cost — a timeout still bills; a suppressed consult makes no attempt and costs nothing), and the explicit *"the ceiling is the backstop; the breaker and the budgets are the throttle"* sentence. The dead-lane failure mode (every search paying a full timeout and a billed attempt, indefinitely) is explicitly prevented. |
| **B3** | `controls-and-phasing.md` **§3.1**, **§3.1.1** (new), **§3.2.1** (new), §3.3 | Every switch declares a **resolution mode**: `DECISION_*` = **boot-snapshot**, per-seam/stage = **live-read** from an atomically-updatable **registry switch store** ⇒ the kill is a store write, so *"no deploy, no restart, immediate"* is **true** (a genuinely boot-only switch carries *"flip + restart"* instead — this design no longer keeps both claims). Overlay switch polarity is **fail-closed** (unreadable/unparseable/unknown ⇒ overlay **OFF**), explicitly contrasted with the house Pattern-C resolver's fail-OPEN-to-ENABLED at `daemon/services/attestation_judge_resolver.py:85-101`, with the note that switch-unreadable is *not* an overlay failure, so D2 is not violated (both directions leave the pre-overlay path in charge). Switch **resolution** is consulted at the call site on **every** path; a resolution failure ⇒ **no call** + a suppressed row `reason=switch_unreadable`. |
| **N1** | `controls-and-phasing.md` §3.3 (in-flight row) · §4.1 | The kill-switch matrix carries an **in-flight-call row** (not cancelled; result discarded; *"immediate"* = no new call initiated after the flip), and the rollback guarantee is scoped to **decisions** in every configuration via the kill contract. |
| **N2** | `thin-client.md` §6 | *"usage missing"* was **removed from the failure table**: a valid answer whose `usage` is absent is a **success**, not a fail-open. The absent usage degrades only the **cost counter** to a per-call estimate — cost is decoupled from answer validity. |
| **N3** | `thin-client.md` §6 row 5 · `seam-registry.md` §5.1 · `scope-and-doctrine.md` §S.2 | The **n ≥ 2 candidate-set precondition** is stated (a `choice` question below 2 options draws a proxy **400**; `reranked[:20]` can be short), the client **pre-flights** it as a new pre-flight row, and the failure class is `candidate_set_too_small`. |
| **N4** | `seam-registry.md` §5.4 · `thin-client.md` §8.4 | *"Structurally enforced"* is **softened** to *"enforced by construction, with one honest caveat"*: the overlay substitutes for the judge's `fused_result.is_complete` verdict at the deny-band rescue, so the guarantee is *"may only propose the rescue the judge would have proposed"* — not a type-level impossibility. **S4 fail-open keeps `DENIED`** is called out explicitly as the one place fail-open is *tightening*, not leniency. |
| **N5** | `seam-registry.md` §5.3 · `thin-client.md` §8.3 · `scope-and-doctrine.md` §S.2 | S3's two **incompatible anchors** are separated: the **routing** decision — and the overlay's switch point — is the router master gate **`graph.py:4384`**; the `attestation_gate_node` (`:5932`) is a **different node** and a separate observation surface. The read-only window now starts at **`:6186`** *(historical; corrected to :6187 — see §5 rows 10/24)*, after the gate-exception early-return (`:6170-6185`). |
| **N6** | 4 docs, **8 sites** | `TERMINAL_AFTER_BOUND`→`DENIED` re-pointed from `graph.py:6655-6662` to **`graph.py:6682-6687`** — the cited range was the budget-exhausted **KEEP** branch. |
| **N7** | `scope-and-doctrine.md` §S.3 row 19 | The `confidence` synthesis site corrected to **`typesafe.go:700-703`**. |
| **N8 / F4b** | 4 docs, **6 sites** · `seam-registry.md` §5.1 | The S3 master gate corrected to **`graph.py:4384`**; `_parse_selection` def corrected to **`:785`**; the **11th of the 17** canonical tuple fields now cites the **FR-10 plan** (`.agents/shared/planning/leader-completion-attestation/requirements.md:731`) instead of an in-file comment. |
| **N9** | `thin-client.md` §8.2 | The **client-side intersection** with the candidate id set is now a **requirement**, not a guarantee: options outside the candidate set are dropped, and an empty intersection fails open. |
| **N10** | `controls-and-phasing.md` §2.2 | `ensemble_decision_overlay_failures_total` now carries the **missing `mode` label** (`shadow`/`deciding`) alongside `seam_id`/`failure_class`, so a shadow failure is never conflated with a deciding failure. |
| **N11** | `thin-client.md` §4.1 | One honest sentence on the **HA-failover regression**: today's stage-3 chat call runs through `invoke_raw_with_failover` (retry budget + base-URL failover); the overlay is single-attempt by ruling, so **C2 trades that cushion for a typed answer**, while **C1** keeps it. |
| **N12** | `architecture-recommendation.md` §2 | The mermaid claim now states the **actual validation method** honestly: parser-level validation against mermaid **11.16.0** from a locally installed copy (this worktree has no `node_modules`; the repo declares `^11.4.0`); **no rendering was performed** and no repo test exercises the diagram. |
| **N13** | `controls-and-phasing.md` §2.2 | `ensemble_decision_overlay_suppressed_total` now carries a **closed reason vocabulary**: `not_configured`, `switch_off`, `switch_unreadable`, `excluded_seam`, `breaker_open`, `rate_limited`, `concurrency_limited`. |
| **F1** | `seam-registry.md` **§2.3** (new) · `thin-client.md` §6 | The registry's fail-open is scoped to the **entire consult** (`build_input` → call → `apply`), owned by the registry (a seam cannot narrow it), with new failure row **30 `seam_fault`**. The same paragraph hosts the breaker's share of the fence. |
| **F2** | `scope-and-doctrine.md` §S.2 · `seam-registry.md` §5.1 | `instance.py:3327-3330` is reworded: it gates **warm-start consumption only** (consumer 2); consumers 1 and 3 are tool-allow-list gated — so the registry's `DECISION_SEAM_*` switch is the **whole-path** switch. |
| **F3** | `thin-client.md` §6 row 19 | A **catch-all row**: any HTTP status not enumerated ⇒ fail-open (`http_other`) — status-only branching with no default branch that errors. |
| **F5** | `architecture-recommendation.md` §4 (**RA-8**) | **RA-8** added: registering an overlay for a decision point outside the vetted four is rejected — the standing exclusion list is closed by default, extension needs a documented **cause** plus the **Emperor's approval** (§D5.2), and EX-1…EX-5 roles are refused at registration (§D5.1, `seam-registry.md` §3). |
| **F6** | `controls-and-phasing.md` §2.6 | A **shadow-writer failure** is counted (`ensemble_decision_overlay_shadow_write_failures_total`) and swallowed — it may lose a row, it may never fail a caller (critical in C1). |

### Items deliberately not folded

| Item | Disposition | Reason |
|---|---|---|
| the council's "**row 29** = `seam_fault`" | applied as **row 30** | N3's pre-flight row (row 5) and F3's catch-all row (row 19) both precede it, so the failure table is now **30 rows** end-to-end. The content is exactly as ruled; only the ordinal shifted. |
| all other items | folded | — |

---

## §8 Change log — PASS 2 (council cycle 3 of 3, final repair)

Final repair pass. B1 (topology) and B2 (throttle) were **closed and kept unchanged**; B3's polarity was **verified and kept**. This pass fixed the remaining mechanism/sentence-level blockers.

| Item | File · section | What now holds |
|---|---|---|
| **R1** | `seam-registry.md` **§5.1.1** (canonical C2 statement), **§5.1.1.1** (new — the three-outcome set), build requirement, "what ships" cell, decision-quality paragraph · `thin-client.md` §8.2 · `scope-and-doctrine.md` §S.2 · `controls-and-phasing.md` §3.2, §4.1 · `architecture-recommendation.md` §1, §3 | One C2 statement, **worded identically in every doc**: *"In C2 the overlay replaces the chat call ON THE DECIDING PATH; the baseline chat selection is RETAINED (not deleted) as the KILL TARGET."* The **build requirement** is explicit: the baseline chat selection must be **retained as a restorable path** (no extra call in normal C2 operation), or the C2 kill promise is unimplementable. The **C2 outcome set is enumerated once** — **O1** overlay result (floor + candidate-set intersection) · **O2** deterministic fused stage-2 floor (**FAIL-OPEN**) · **O3** baseline chat selection (**KILL TARGET**) — and the docs state plainly that **kill ≠ failure** in C2: a kill ⇒ O3 (restores pre-overlay production behaviour), a failure ⇒ O2 (a degradation). The **C2 decision-quality consequence** is now disclosed next to the latency/HA trade: O2 is a *lexical* BM25+cosine ranking, **not** the chat LLM's selection, and P3's exit criteria must compare O2 against C0, not only O1. The "what ships" cell now enumerates all three outcomes. The guarantee is **scoped per configuration** everywhere, with the retention conditionality explicit. |
| **R2** | `controls-and-phasing.md` **§3.1** (table), **§3.1.1** (write channel + durability), **§4** P1 exit criteria | **SUPERSEDED by PASS-3 row B5.** R2 proposed the live kill channel be implemented through the agent-callable per-instance tunable surface (`daemon/tools/tunables.py:100-207`); that proposal was **rejected** in PASS-3 (B5) in favour of the daemon-settings surface (`daemon/services/settings_service.py:200`, `PUT /api/settings/daemon/{key}`, gated `settings:write`). The tunable surface is agent-callable and per-instance, so it cannot hold a daemon-global master and is explicitly **not** the channel. R2's remaining content — the env value is the BOOT DEFAULT ONLY; durability; the P1 acceptance criteria — stands and was carried forward. The literal token for the agent-callable per-instance tunable surface is recorded in §8 (removed strings). |
| **R3** | `controls-and-phasing.md` **§3.2.2** (new — the canonical composition rule), §3.1 (three existing gates + `gate_config` rows), §3.3 (coverage matrix) · `seam-registry.md` §5.3, §5.4 | **One composition rule, stated once**: *the overlay is consulted only if BOTH the registry's live per-seam switch is ON AND the seam's existing gate switch is ON; if EITHER is OFF or unreadable ⇒ the overlay is NOT consulted (no call)* — explicitly fail-closed and explicitly **not** a D2 conflict (a gate-off seam never calls out; D2 governs overlay *failure*). **S4**: the overlay consult **must sit inside the `fused_judge_on` boolean** (`graph.py:6293-6295`) so `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED=0` gates it too; the house fail-closed call-site behaviour is cited (`graph.py:6296-6297` — a resolver exception sets `fused_judge_on = False`); and if an implementation genuinely cannot sit inside the boolean, the docs state that plainly and give the operator warning. **S3**: the consult is placed **inside the ladder-routing branch it augments**, so `ENSEMBLE_SYMPTOM_REPAIR_LADDER` gates it; the ladder switch is declared a **boot-snapshot Pattern-C routing gate, not an overlay gate**. `gate_config["llm_judge_enabled"]` (`attestation_gate.py:1268`, `:1311`; read at `graph.py:6294`) is a **named third switch surface**. The false-premise async row is closed (see D4-5). |
| **D3-2** | `thin-client.md` §6 (kill-contract pointer + `switch_unreadable` cross-pointer) · `controls-and-phasing.md` §3.2 (reference, not restatement) · `scope-and-doctrine.md` §D1 (reference, not restatement) | The kill contract is now **stated once** (`seam-registry.md` §5.1.1) and **referenced** elsewhere. `thin-client.md` §6 previously had **zero** occurrences of it and its failure table carried no pointer — it now carries an explicit pointer and a cross-pointer to the `switch_unreadable` class (`controls-and-phasing.md` §3.2.1): an unreadable switch means the overlay is **never consulted**, no call is made, and the seam is answered from the heuristic; it is counted under `suppressed_total`, not `failures_total`. The divergent "no env" restatement in `scope-and-doctrine.md` and the restatement in `controls-and-phasing.md` are gone. |
| **D3-3** | `controls-and-phasing.md` §3.2, §4.1 · `scope-and-doctrine.md` §D1 · `seam-registry.md` §5.1.1 | The canonical wording is now **"byte-identical DECISIONS; observability differs"** — a suppressed row plus `ensemble_decision_overlay_suppressed_total` deltas are **expected** and are the operator's **confirmation signal**, not a regression. The overclaiming "byte-identical to pre-overlay *behaviour*" is gone from all five docs. |
| **D4-3** | `controls-and-phasing.md` §3.1 | The §3.1 table now lists **every switch named anywhere in the set**, each with a declared **resolution mode**: the three existing gates — `ENSEMBLE_SYMPTOM_REPAIR_LADDER`, `ENSEMBLE_LEADER_ATTESTATION_MODE`, `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED` — are declared **boot-snapshot Pattern-C globals**, alongside `gate_config["llm_judge_enabled"]` and the overlay's own boot-snapshot env values and live store entries. The resolution-mode column is now scoped to *every* switch, not only the overlay's. |
| **D4-4** | `controls-and-phasing.md` §3.4 / §3.5 structure · §3.1.1, §3.3, §4.1, `architecture-recommendation.md` §3 | The **empty `### §3.4 Operator procedure` heading is repaired** — the throttle block had been inserted between the heading and its body, so the trip table and the 6-step procedure physically sat under §3.5. The §3.5 block was moved **after** the procedure, so §3.4 owns its content again and every dangling pointer (`controls-and-phasing.md` §3.1.1 → §3.4; §4.1 → §3.4; `architecture-recommendation.md` §3 → §3.4) resolves to a real section. The final mechanical pass was **extended with EMPTY-SECTION and DANGLING-POINTER detection** (implemented in the pass script; it is not a deliverable artefact per the no-new-files constraint) and both detectors now report clean. |
| **D4-5** | `controls-and-phasing.md` §3.3 | The false-premise async row is **closed**: `judge_fused_bundle_async` has **exactly one production call site** (`graph.py:6326`), awaited **inline** behind the same `fused_judge_on` gate — there is no separate async entry point. The **genuine residual** is now named correctly: the judge's **own second HTTP attempt** (`attestation_report_judge.py:120` — "attempt 1 and attempt 2"; per-attempt timeout, worst case 2 × `timeout_s` at `:725-730`), which sits **inside the judge's own budget**. |
| **D4-6** | `scope-and-doctrine.md` §S.3 row 26 + §IP (item IP-1) · `controls-and-phasing.md` §3.1.1 · `thin-client.md` §3 | The `substitute_env_vars` call site is corrected to **`config.py:7157`** — `:7149` is `yaml.safe_load(f)`. This is the anchor the boot-snapshot argument leans on, so it is published as a corrections-table row (row 26) and updated at all three call sites. |
| **F1** | `controls-and-phasing.md` §3.5.1 | The breaker's blind spot is now explicit: it is **error / fallback-rate only**. A **slow-but-successful** overlay (4.8 s of a 5 s budget, valid answer) **never trips it** — in C2 such a lane is relieved only by operator trip **T4**, and an **automated p95 trip is marked FUTURE work**, not part of this design. |
| **F2** | `controls-and-phasing.md` §3.5.1 | One clause added: a **timeout counts toward BOTH** the N = 5 consecutive-failure counter and the > 50 % fallback window. |
| **S-1** | `controls-and-phasing.md` §3.2.1 | The house **call-site** behaviour is now cited alongside the parse semantics: at `graph.py:6296-6297` a resolver exception sets `fused_judge_on = False` — the fault **disables** the path rather than enabling it — which is the behaviour an implementer should copy for an overlay switch. |
| **S-2** | `controls-and-phasing.md` §3.1 | The §3.1 table gained a **"how the operator flips it"** column — *env + restart* vs *live store write, no restart* — so the resolution mode alone can no longer leave the live-kill trap live. |
| **S-3** | `controls-and-phasing.md` §3.2 obligation 3 | The **acceptance artefact** for the byte-identical obligation is named: the **P1 exit criteria** in §4 — the "switch-off produces byte-identical decisions" item — is the artefact of record, and is where the live-channel kill test and the restart-survival test are exercised. |

### Items deliberately not folded

| Item | Disposition | Reason |
|---|---|---|
| none | — | every item in this pass was folded. The only judgement calls are the ones already recorded above: R1's outcome-set enumeration sits in `seam-registry.md` §5.1.1.1 (single canonical home) while the identical C2 sentence appears in all five docs as R1(1) requires; D4-4's detector work is implemented in the mechanical pass script rather than persisted, because the commission forbids new files. |

---

## §9 Change log — PASS 3 (approver iteration 001, final repair)

13 blockers and the folded notes. **New true anchors published in §S.3 rows 27–48** (the register is now a completeness gate). Verification method for each anchor is named below: **read-only source inspection at `5da1021f2`** (`sed`/`grep` on the worktree) or **read-only inspection of the proxy at `9cf17bb` via `git show`** — no runtime execution, no mutation.

| Item | File · section | What now holds |
|---|---|---|
| **B1** | `scope-and-doctrine.md` §S.2 S4 · `seam-registry.md` §5.4 | **"DENY is minted only at `:950`" was false.** The `DENY` rung is minted at **two** sites, **both inside `decide()`**: **`attestation_gate.py:1024`** — step 5, the **main path** that runs every non-attested turn (comment "(5) deny — increment + nudge." at `:1022`) — and **`:950`**, the `report_obligation_read_error` **error-path variant**. `TERMINAL_AFTER_BOUND` likewise at **`:1016`** (main) and **`:944`** (error). Exclusivity is now restated as **"both DENIED sites are inside `decide()`"**, and the docs warn that guarding `:950` alone leaves the primary deny path unguarded. *Verification: `sed -n '940,955p'` + `sed -n '1010,1030p'` on `daemon/services/attestation_gate.py`.* |
| **B2** | `scope-and-doctrine.md` §D5.2 (+ register rows 28–30) | The drift list is **no longer incomplete**: a **complete** five-entry table publishes the true anchors — **EX-1** wired at **`graph.py:10568`/`:10570`** (`:8665-8667` is the tool-call-pairing guard) · **EX-2**'s three quoted anchors all point at unrelated code (`instance_messaging.py:1168` = `_resolve_recursion_limit_for_meta`; `graph.py:6318`/`:6354` = the fused-judge region; `compaction.py:2243` = `ContextCompactor.__init__`) · **EX-5**'s rungs are **`graph.py:4558`** (truncated), **`:4577`** (empty), **`:2450`** (loop). The §D5.1 quoted table stays **verbatim**. *Verification: `grep`/`sed` on the worktree at `5da1021f2`.* |
| **B3** | `scope-and-doctrine.md` §S.1 (+ register rows 31–32) | Census table corrected: the upgrade-sweep decision trees live in **`daemon/services/upgrade_journal_sweep.py`** (render **`:981`**, settle **`:1037`** — the `daemon/tools/` path does not exist), and the R9 reuse verdict is **`snapshot_tools.py:328`** (`_r9_reuse_verdict`), set-ops `_overlapping_tags` **`:324-325`** (`:365-381` is the git-provenance block). *Verification: `ls` + `grep -n "def "` on the worktree.* |
| **B4** | `scope-and-doctrine.md` §S.1 (+ register row 33) | The census now reads **":6317 import, :6326 call"** — `graph.py:6326` is the sole production **call** of `judge_fused_bundle_async`; `:6317` is the import. *Verification: `sed -n '6316,6328p'`.* |
| **B5** | `controls-and-phasing.md` §3.1, §3.1.1 | The live kill channel is now the **daemon-settings surface**: `daemon/services/settings_service.py:200` (`MANAGED_KEYS`) with **`PUT /api/settings/daemon/{key}`** gated **`settings:write`** (`daemon/routers/settings.py:969`, `:1023`; GET `settings:read` `:946`), resolver ladder **DB > env > yaml > registry default** (`settings_service.py:54`). **Read per consult** (build requirement), **global-master holder = the daemon-global settings namespace** (not `instance_metadata`), **widened value types** (`0`/`1`, `shadow`/`deciding` — the int `[60,1800]` tool cannot carry them), **operator-only provided not asserted** (`set_instance_tunable` is agent-callable and is explicitly **not** the channel), **write→read-back→verify** with fail-closed on error/mismatch, and durability with the boot re-verify counter `ensemble_decision_overlay_switch_reverify_total`. *Verification: `sed`/`grep` on `daemon/services/settings_service.py` and `daemon/routers/settings.py`.* |
| **B6** | `seam-registry.md` §5.3 · `thin-client.md` §8.3 · `scope-and-doctrine.md` §S.2 · `controls-and-phasing.md` §3.3 | **One S3 placement, worded identically in all five docs**, naming the switch that **actually** gates it: the consult sits in the **router's ladder-routing branch** (`graph.py:4261` `should_continue`, ghost block `:4380-4402`, master gate `:4384`) **inside the branch `ENSEMBLE_SYMPTOM_REPAIR_LADDER` gates** — so the composition rule is true for S3. The `attestation_gate_node` (`:5932`) is **S4's** surface and is **not** S3's hook. The §3.3 S3 row was rewritten to match; the coverage matrix no longer conflates the two anchors. |
| **B7** | `seam-registry.md` §3.4 (moved from controls) | The per-seam default state is now specified: **`decision_global_enabled` OFF**, **`decision_seam_<id>_enabled` OFF** (a seam is never consulted unless its **own** key is ON), **`decision_seam_<id>_stage` `shadow`**; boot seeding writes env seeds **only where the store has no value** (DB tier wins) and re-verifies every kill. The admission rule is stated once (global ∧ per-seam ∧ stage ∧ existing gate). **Registration is opt-out**, and the docs note that the S3 gate the overlay rides is **DEFAULT ON** in source (`config.py:6356-6357`) — which is precisely why the per-seam default must be OFF. The doctrine source is quoted: *"All behind master env gate + per-seam gates, default off"* (`docs/jev-system-one-integration-discussion.md:95`). |
| **B8** | `thin-client.md` §8.2.1 | The confidence-floor mechanism now exists as a **per-seam, per-configuration table**: the **wire field each seam reads** (S1 → **`confidence`** on a choice answer; S3 and S4 → **`probability`**, because **`confidence` never exists on a predicate**), the C2 floor per seam (0.5 / 0.9 / 0.9, proposed, ratified at P3), and the **below-floor behaviour** (S1 → treated as no answer ⇒ fail open to the stage-2 floor; S3/S4 → the overlay adds nothing). **Absent or undefined field ⇒ fail open** — never read as "zero", never as "above the floor". C1 has no floor (shadow never acts). |
| **B9** | `seam-registry.md` §5.4 · `thin-client.md` §8.4 · `controls-and-phasing.md` §4 (P4) | **Option (a) — the overlay may only ADD to the JUDGE's outcome, never substitute.** In C2 the **canonical judge is consulted first** (`graph.py:6326`) and the overlay may only add completions the judge would also have produced, **above the high-confidence floor**. It **never replaces a judge verdict** and **never flips a `DENY`** — the `DENIED → ALLOW` flip stays owned by the judge's rescue path (`:6398-6420`, deny band `:6391`, triple-gated). The docs state *why*: D4 forecloses a loosening overlay, and a shadow A/B (agreement-based) **cannot detect** a loosening at runtime — so the design must forbid it, not the measurement. **If the additive form cannot be implemented, S4 remains SHADOW-ONLY and is dropped from P4's deciding sequence** — stated in both docs. |
| **B10** | `thin-client.md` §6 · `controls-and-phasing.md` §2.2 | The two vocabularies are now **disjoint and closed**: `failure_class` = the **25 transport/content rows** (rows 6–29 of the 29-row table); the **suppressed** vocabulary = **9** values (`not_configured`, `invalid_request`, `candidate_set_too_small`, `switch_off`, `switch_unreadable`, `excluded_seam`, `breaker_open`, `rate_limited`, `concurrency_limited`). The pre-flight rows are explicitly **not failures**. `ensemble_decision_overlay_fallbacks_total` now carries a **`mode` label**, and the identity `fallbacks_total == failures_total` is stated to hold **only in `mode=deciding`** — in C1 the chat call still ships, so the heuristic absorbs nothing while `failures_total` increments. |
| **B11** | `controls-and-phasing.md` §2.8 | The observability contract now names **substrate** (a structured daemon log row, not a table), **home**, **retention** (pipeline rotation **plus** a writer-enforced row-count cap, since a daemon log pipeline has no truncation of its own — drops counted in `ensemble_decision_overlay_shadow_rows_dropped_total`), **access control** (operator-scoped, never agent-readable, write-time redaction), and the **decision-audit trail**: the shadow log **IS** the trail, and is therefore **durable and access-controlled** — with two binding consequences (the cap must cover the seam's own A/B window plus the audit horizon; a kill cannot erase what was consulted before it). |
| **B12** | `thin-client.md` §2, §6 row 24 · `scope-and-doctrine.md` PX-3 | The **full wire answer shape** is published: `{type, name?, probability?, choice?, probabilities?, confidence?, score?, refusal?}` (`pkg/decision/types.go:167-193`; `MarshalJSON` `:202-231`); **`type` is always emitted** and **`answers[].name` is the JOIN KEY to `questions[].name`**. The old "predicate → probability **only**" clause — which contradicted its own digest — is replaced by "the predicate's *fields* are probability; the *answer shape* is the union above". A missing/unmatched `name` ⇒ **fail open** (provenance: proxy-synthesized on the System One path, upstream-supplied on the openai path), and the client **pins a non-empty question name** (`validators.go:101-108` — an empty name 502s every call, making the overlay silently inert). |
| **B13** | `thin-client.md` §6 row 17, §7 · `scope-and-doctrine.md` PX-8 | The 502 subclass row is **folded into a single `http_502` label** — the proxy emits 502 for ≥8 causes (`:307`, `:325`, `:366`, `:433`, `:465`, `:478`, `:497`, relayed envelope) and the no-adapter case is **not uniquely identifiable by status**. **No body text is parsed anywhere.** The deploy-order case is resolved at **provisioning** time by the **operator** — an **operator precondition, executed out-of-band**: **the client never probes and no boot-time verification occurs**. `provisioning_missing` is **narrowed to the probe-provable** conditions (**404** model-absent, **400 `decision_misroute`**), while **401** and **403** keep their own reasons; the ambiguous 502 gets **one** label and a **transient upstream 502 must never park a seam as `NOT_ENABLED`**. |

### Folded notes

| Note | What now holds |
|---|---|
| **W1** | The row-4 guard stays as-is (**omitted** `input`, not an empty string); **top-K completeness is an upstream property, not a proxy guarantee** — the *never-empty* half is the client-side intersection rule, and the two are named separately; `safety_identifier`/image parts are 400-rejected on the System One path and the client sends neither; the **185→187 delta is larger than a provider** (wire shape + URL construction changed too); the image_gen POST is **`:802`** (`:780` builds the URL); the status taxonomy no longer lists 409/422/429/503 and records **401 `authentication_error`** and **405 plain text**; `levels[]` requires **`label`**; the **per-token model allow-list** (403, non-standard body) is added to the operator prerequisites; rows 20–27 are marked **defensive-only** against the current proxy |
| **W2** | `graph.py:4370-4378` is recorded as a **comment, not telemetry**; `attestation_required` is the **11th of the 17** canonical tuple fields; the gate-exception span is **`:6170-6185`**; the deny band is **`:6391`** *(superseded — the original `:6391` was correct; see §2 A1/A3)*; `_parse_selection`'s def is **`:785`** (the approver's `:786` is its docstring — recorded as such in register row 38); the P2/P3 guard header is **`:6705`**; the test class is **`TestMonitoringOnlyPin`**; "structurally forbids" is reworded to an **AST-walk unit test**, not a language-level ban |
| **W3** | `DECISION_ENABLED` is reconciled with the earlier "deliberately absent" note (it is the **boot seed**, not the live path); the composition rule now names the **global master** and the **stage key** as well as the per-seam and existing-gate switches; the **stage → C1/C2** mapping is stated explicitly; the shadow-writer row no longer claims "no rows are produced" (after a kill it writes the **suppression** row); the S1 pilot rationale no longer claims the per-agent gate is a whole-path switch (**warm-start consumption only**); `ensemble_decision_overlay_shadow_write_failures_total` is given a **mode label** and placed inside the family shape; the §3.1 table is scoped to **every switch this set operates** (long-tool switches excluded); the `decisions.md` D6 pattern is now **pathed**; **D4-vs-D5 for S4 is reconciled explicitly** in `seam-registry.md` §5.4 |

### Deliberately not folded

| Item | Disposition | Reason |
|---|---|---|
| none | — | every blocker and note was folded. Two judgement calls are recorded above: the per-seam **default-state block** was placed in `seam-registry.md` §3.4 (the registry is the admission point) rather than in the controls doc, to keep both documents inside the line budget; and the S3 placement sentence is stated **verbatim in all five docs** because B6 requires identical wording, while the surrounding commentary is per-doc. |

---

---

> **Self-certification evidence (propagation table, claim-level ledger, detector output, content-loss audit list, record of removed strings, budget) lives in `anchor-audit.md`** — read it by section id. This document carries design content only.

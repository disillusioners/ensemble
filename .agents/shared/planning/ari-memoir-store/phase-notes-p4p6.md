# Phase notes — Memoir Store v1 (wave 4 of 5 — phase 4 + phase 6)

**Lane:** `feature/ari-memoir-store` @ `/home/nea/ensemble-src-wt-memoir-store`,
base `a8d1720cb` (wave 3), phase-4/6 commits `8f538cd69` → `d9b36ad8a` →
`575cfc296`.
**Status:** PHASE 4 (the tool category + the tools + the ceremony) and
PHASE 6 (the inner_soul ari-only redirect) COMPLETE.

---

## 1. Anchor sweep (re-grepped at the implementation HEAD, 2026-11-05)

P-7 discipline: the recorded anchors are LEADS. Every anchor these phases
cite was re-grepped at use time.

| Anchor | Cited | Verified | Note |
|---|---|---|---|
| `daemon/tools/_tool_registry.py` — `CATEGORY_MODULES["notify-user"]` | `:1112` | **`:1136`** | DRIFT (+24 lines — the metrics-read entry and its comment block were added after the cite). |
| `CATEGORY_MODULES["metrics-read"]` | `:1125` | **`:1149`** | DRIFT (+24). The memoir entry was appended right after it. |
| `PRIVILEGED_TOOL_CATEGORIES` literal | `:650-653` | **`:379-694`** | DRIFT (the literal is long; the frozenset opens at `:379`). The `metrics-read` entry was LAST; `"memoir"` is now the LAST member. |
| the registry's stale prose census | `:324-331` | **`:336-343`** | DRIFT (+12). Corrected in the ceremony commit. |
| the notify-user entry comment | `:629-631` | **`:641-646`** | DRIFT (+12). Corrected in the ceremony commit. |
| the metrics-read entry comment | `:648-650` | **`:660-665`** | DRIFT (+12). Corrected in the ceremony commit. |
| `daemon/tools/instance.py` — the metrics-read import | `:292` | **`:293`** | +1 (the memoir import took `:292`… no: the metrics import stayed at `:292`; the MEMOIR import was added at `:293`). |
| `daemon/tools/instance.py` — the metrics-read build call | `:6657` | **`:6657`** | EXACT. The memoir extend was added immediately after it (`:6672-6707`), i.e. BEFORE `create_help_tool` (`:7212`), `scan_tools_for_full_docs` (`:7217`) and `_apply_tool_filter` (`:7220`). |
| `daemon/tools/inner_soul.py` — `_RAG_TARGETS` | `:303` | `:303` | EXACT. |
| `_should_redirect_to_rag` | `:316` | `:316` | EXACT. |
| `_format_rag_redirect` | `:365` | `:365` | EXACT. |
| the single-flow call site | `:716` | **`:774`** | DRIFT (+58 — wave-3's files grew the module). |
| the compound-flow call site | `:777` | **`:839`** | DRIFT (+62). |
| `agents/ari/meta.json` — `tools.allow` | 34 entries | 34 entries | EXACT — the ceremony appended `"memoir"` as the 35th (LAST). |
| `agents/.prompt-artifacts.sha256` | 161 lines | 161 lines | EXACT — the re-stamp changed ONE line (`agents/ari/tools_note.md`). |
| MT-01 (`test/packs/metrics_read_mutation_matrix_test.py`) | `:92-93` | `:92-93` | EXACT. |
| `_is_metrics_authorized` (the fail-closed shape) | `metrics_read.py:403-414` | `metrics_read.py:403-414` | EXACT — the memoir gate mirrors it. |
| `daemon/tools/notify_user.py` — the auth gate | `:702` | `:702` | EXACT — the exemplar's CODE (agent gate first, knob second), not its comment. |

**Drift found:** 6 (all line-number drifts, all re-grepped and recorded
here — none is a deviation).

---

## 2. The decisions taken inside the phase (recorded, not re-litigated)

| # | Decision | Why |
|---|---|---|
| P4-1 | The tool layer does NOT re-derive the repository's bounds. Every write bound (body/title/importance/stars/event_at) is enforced by the repository and MAPPED onto the frozen vocabulary (`_WRITE_CODE_MAP`); the tool layer only pre-validates the two things the vocabulary names for the CALL (the keepsake reason and the keepsake actor claim). | D4.8 + the plan's "the tool layer never re-derives the reason" + "do not re-derive bounds in the tool layer" (the dispatch brief). One authority per bound. |
| P4-2 | `query_required` (the recall service's empty-query refusal) has NO frozen counterpart, so the tool maps it onto `memoir_internal_error` with the TRUE message preserved ("the recall query must not be empty"). The vocabulary is FROZEN at 18 codes (D4.8/W10) and the tool never invents a 19th. | The plan rejects "reusing ill-fitting codes for distinct failure modes"; the catch-all + the verbatim message is the only option that keeps the set exact. The mapping is documented in the module docstring and asserted by the pack (`test_a_blank_recall_query_falls_back_to_the_catch_all`). |
| P4-3 | The v1 tool-lane recall is LEXICAL-ONLY: the service is constructed with NO query embedder, so the verbatim footer says `semantic=off` (and still counts `missing_embedding`). Wiring an embedder would put an embedder symbol inside `daemon/tools/memoir.py` — which AM-12/D4.11 forbids ("the embedder is reachable ONLY from the worker module"). The plan nowhere instructs the tool to wire a query embedder, so this is the plan's own consequence, not a local decision. | AM-12 is 🔴 and the purity pin is ASSERTABLE; the semantic recall lane needs an injection point v1 does not build (the manager/app.state has no query-embedder handle, and both files are frozen to this wave). **ROUTED to wave 5/P7 + the tester**: the tool-lane recall is lexical-only until a manager-side embedder handle exists. |
| P4-4 | `memoir_unpin` reads the episode BEFORE the write (one read + one write) so it can distinguish "no such episode" (`memoir_not_found`) from "not a keepsake" (the idempotent no-op). `memoir_pin` reads the keepsake set before and after the write so the result can carry `re_pinned` and the pin stamp. | D4.9's result shapes (a re-pin note; an idempotent unpin) need those facts; the repository exposes no single-episode read. Both reads are off-loop (`asyncio.to_thread`). |
| P4-5 | The purity pin strips DOCSTRINGS via AST before the needle scan. The module's prose NAMES the forbidden symbols (the pin documents what it forbids) — the pin is about REFERENCES, not about words. The AST pass also asserts: no import of any `*embedding*` module, no `store_embedding`/`.embeddings`/`EmbedFn`/`embed(` reference, exactly ONE `repo.insert_episode` / `set_pin` / `clear_pin` call site. | A raw grep would force the prose to go silent about the very contract it documents. The AST pin is STRONGER (it catches a planted call, an import or a name) and it is mutation-proven (the pack's M5 plants an inline embed ⇒ the pin goes RED). |
| P6-1 | The P6 branch is keyed on `agent_id == "ari"` EXACTLY, at BOTH call sites, with ONE shared module-level helper. The compound arm keeps the `(redirected to memoir)` label (D6.5) — the label line is therefore the second edited line inside that branch. | D6.1/D6.4/D6.5. The diff census is asserted by the pack: exactly 1 added import, 1 added helper def, 2 added `if agent_id == "ari":` branches, and the EXACT removed-line set (2 lines, both in the compound arm). |
| P6-2 | The knob is read at CALL TIME inside the helper (`memoir_enabled(getattr(manager, "config", None))`), so the config-file tier participates (the knob's config tier is the CALLER's, per wave 1's P0-1). | The knob module's own contract (D0.5/AM-27: never boot-cached) + the helper needs no extra plumbing (the factory's `manager` is already in scope at both call sites). |

---

## 3. THE CEREMONY RECORD (T4.4 — one commit, `d9b36ad8a`)

### 3.1 The grep-derived pin set (derived AT the ceremony, never from prose)

```
grep -rn "EXPECTED_PRIVILEGED_SET" tests/ daemon/ --include=*.py
  → 6 DEFINITION literals (board:70, wedge_capture:72, self_alarm:76,
    notify_user:55, metrics_read:91, instance_ops:88 — the 6 COMPARISON
    asserts consume the definitions and need NO edit) + 1 stale docstring
    (test_service_tool_flag_off_byte_identical.py:819) + the registry's
    own prose mentions
grep -rn "len(PRIVILEGED_TOOL_CATEGORIES)" tests/ --include=*.py
  → 6 count pins (17 → 18)
grep -rn "PRIVILEGED_TOOL_CATEGORIES == frozenset" tests/ --include=*.py
  → 5 inline exact-set pins (the "memoir" entry appended to each)
the ari allow-trailing-set pin → tests/unit/test_board_registration.py
```

**= 18 EDIT sites across ELEVEN pin files.** Every one was bumped in the
ONE ceremony commit, together with the registration, the allow-array
append, the prose corrections, the MT-01 lockstep and the re-stamp.

### 3.2 The stale prose censuses — corrected (with the TRUE figures)

| Site | Before | After |
|---|---|---|
| `daemon/tools/_tool_registry.py` (the SAME-PR RULE block) | "SIXTEEN assertion sites across TEN files" | "EIGHTEEN EDIT sites across ELEVEN files" (+ the file list now names `test_metrics_read_registration.py`, and the block says "five inline frozenset literals … plus EXPECTED_PRIVILEGED_SET definition literals at SIX phase-local pins … plus a len(...) count assertion at SIX phase-local pins") |
| the notify-user entry comment | "the TEN files carrying the SIXTEEN assertion sites (10 exact-set + 5 count + 1 allow-array trailing-entry pin — re-grepped on tip 9098df432, ari-voice lane)" | "the ELEVEN files carrying the EIGHTEEN assertion sites (5 exact-set + 6 EXPECTED_PRIVILEGED_SET definitions + 6 count + 1 allow-array trailing-entry pin — re-derived by grep at the memoir ceremony, 2026-11-05)" |
| the metrics-read entry comment | the same stale text | the same correction |
| `tests/integration/test_service_tool_flag_off_byte_identical.py:819` | "The exact-set pin lives at TEN sites (5 EXPECTED_PRIVILEGED_SET constants + 5 inline frozenset literals — 15 EDIT sites plus the 1 VERIFY-ONLY ari trailing-entry pin in total)" + an 8-file list | "The exact-set pin lives at ELEVEN files (5 inline frozenset literals + 6 EXPECTED_PRIVILEGED_SET definition literals + 6 count assertions + 1 ari trailing-entry pin — 18 EDIT sites in total; the 6 COMPARISON asserts that consume the definitions need no edit)" + the 11-file list |

### 3.3 MT-01 — the lockstep (W4)

`test/packs/metrics_read_mutation_matrix_test.py:92-93`
— find `"assert len(PRIVILEGED_TOOL_CATEGORIES) == 17"` → **18**
— replace `"assert len(PRIVILEGED_TOOL_CATEGORIES) == 16"` → **17**
(NEVER both to 18 — that would make the mutation vacuous.)

**Acceptance check (by hand, byte-copy → plant → RED → restore → GREEN):**
planting the replace-string (the count pin 18 → 17) turns
`tests/unit/test_board_registration.py::TestPrivilegedCategory::test_board_is_the_ninth_entry`
RED — the EXACT red marker the matrix table names — and the file restores
byte-identically.

**Acceptance check (in the metrics lane's OWN harness):** the full matrix
(`test/packs/metrics_read_mutation_matrix_test.py`) was run on the
ceremony tree with a throwaway `.venv` shim (the worktree has no `.venv`;
the shim is a `pyvenv.cfg` + `bin/python` + `lib` symlink set and is
REMOVED afterwards):
`RESULT: PASS (26 RED/GREEN legs + 1 FOLLOW leg(s))` — with
`MT-01-count-pin: OK (RED via test_board_is_the_ninth_entry)` and
`post-run manifest: every touched file is byte-identical`.
(The FIRST attempt failed 53 legs for an environment reason only: a
symlinked `.venv/bin/python` resolves no site-packages ("No module named
pytest"), so every leg's pack was RED in BOTH states. The shim was
rebuilt with the `pyvenv.cfg` + `lib` symlinks and the matrix then passed
in full — the ceremony was never implicated.)

### 3.4 The re-stamp (D4.13)

`scripts/protection/generate_prompt_artifact_baseline.py` (the house tool,
regenerating — never hand-editing):
`PROMPT-BASELINE: GENERATED — … (161 artifact(s))` — ONE line changed
(`agents/ari/tools_note.md`), the count stays **161** (`meta.json` is not
in the baseline, so the allow entry ships without a re-stamp).

### 3.5 The post-ceremony census

* every count pin reads 18 (6/6);
* every exact-set literal — the registry's own + the 11 pin files' —
  carries the SAME eighteen members (the registration pack derives the
  expected set BY AST and cross-checks every pin file against it);
* `"memoir"` is the LAST member of the registry's literal AND the LAST
  entry of ari's allow array;
* the MT-01 literals are lockstepped and non-vacuous (asserted).

---

## 4. The FC9 measurement (T4.6 — measure, don't judge)

**The measurement point (the plane lane's mechanism, re-used):** the app's
OWN lifespan, entered in-process on a throwaway local-PostgreSQL
database, resolving ari's toolset through `create_instance_tools` and
ari's prompt through `load_and_cache_prompt`, counted with the daemon's
own `estimate_tokens` (tiktoken `cl100k_base`).

**The fence (house precedent F1–F11, adapted to this lane's env rules —
NO `env -i`, NO `env -u POSTGRES_*` glob):** scratch root
`mktemp -d /tmp/memoir-fc9.XXXXXX`; `ENSEMBLE_SELF_ENV=sandbox` pinned;
`ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned; every `POSTGRES_*`
OVERRIDDEN BY NAME to the scratch database; a FAKE `HOME`; a scratch
`ENSEMBLE_DATA_DIR`; the target asserted (`SELECT current_database()` ==
the scratch name) BEFORE the first statement; the scratch role + database
DROPPED at teardown; **no port bound at all** (the harness runs the
lifespan in-process); the live install's `state.json` sha256 read BEFORE
and AFTER (identical: `c2b87241…`); `current ->` unchanged
(`releases/v0.28.12`); no `.env` written anywhere.

**The BEFORE leg** temporarily restores the PRE-ceremony ari agent files
(`meta.json` + `tools_note.md`) so the resolved toolset and the prompt are
the genuine pre-grant ones; the restore is verified (the tree is clean
again afterwards).

| Leg | resolved tools | system-prompt tokens | tool-schema tokens (compact) | **total (compact)** |
|---|---|---|---|---|
| BEFORE (pre-ceremony) | 115 | 27 427 | 20 743 | **48 170** |
| AFTER (the ceremony tree) | 120 (+5 memoir) | 28 544 (+1 117) | 21 722 (+979) | **50 266** |

**The numbers (the pre-committed rule, not adjusted at execution time):**

```
growth_tokens = present − baseline = 50 266 − 48 170 = 2 096
growth_pct    = 2 096 / 48 170 = 0.043513 (4.3513 %)
(a) growth_tokens ≤ max(10 000, 0.25 × baseline) = max(10 000, 12 042.5) = 12 042.5 → 2 096 ≤ 12 042.5  PASS
(b) growth_pct ≤ 0.25                                                             → 0.0435 ≤ 0.25     PASS
VERDICT: PASS — no breach, NO narrowing needed.
```

The plain-`json.dumps` form (the other recorded form) gives baseline
51 461 → present 53 741, growth 2 280 (4.4305 %) — the same verdict.

**The decomposition:** the prompt grows by **1 117** tokens (the
`tools_note.md` doctrine section + the memoir category's section in the
prompt's tool listing) and the tool schemas by **979** compact tokens
(`memoir_write` 462, `memoir_timeline` 159, `memoir_pin` 136,
`memoir_recall` 123, `memoir_unpin` 99).

**Disclosed measurement caveats (both CONSERVATIVE for the bounds):**
1. the scratch HOME has no `project-experience.md`, so the measured
   prompt is slightly SMALLER than a live ari session's ⇒ the baseline is
   a lower bound (the 10 000-token absolute floor dominates anyway);
2. no MCP tool names were bound to the measurement instance — ari holds no
   `mcp` category, so its RESOLVED TOOLSET is unaffected (the recorded
   toolset grew by exactly the 5 memoir tools).

**The harness recipe** (the fence script + the harness) is preserved in
the lane's evidence; it is deliberately NOT committed as a repo script
(it is a one-shot measurement harness, not a shipped test) — the pack
`test/packs/memoir_tools_unit_test.sh` carries the durable behaviour
proof.

---

## 5. The mutation matrix (phase 4 + phase 6) — every leg proven

| # | The planted defect | Pack | The RED id(s) | Restored |
|---|---|---|---|---|
| M1 | the gate order inverted (the knob read ahead of the auth gate) | `memoir_tools_unit_test` | `TestTheGateOrder::test_an_unauthorized_caller_is_refused_before_any_knob_or_db` | GREEN (43) |
| M2 | the keepsake actor-required branch disabled | `memoir_tools_unit_test` | `TestTheRefusalMatrix::test_keepsake_without_actor_is_refused` | GREEN |
| M3 | the admission bypassed at the tool layer (the write drops the keepsake flag) | `memoir_tools_unit_test` | `TestWritePinUnpin::test_a_keepsake_write_records_the_claim_and_provenance` | GREEN |
| M4 | the budget FINAL RE-CHECK removed (the service's own guard) | `memoir_tools_unit_test` | `TestTheReads::test_the_footer_tips_the_envelope_and_the_tool_says_so` | GREEN |
| M5 | an inline embed planted in the write body | `memoir_tools_unit_test` | `TestTheWritePurity::test_the_module_holds_no_embedder_symbol` | GREEN |
| P6-M1 | the ari branch disabled | `memoir_inner_soul_redirect_test` | `ari-not-memoir:pattern` (+ the sibling ari ids, `knob-off-notice`, `census-branches`) | GREEN |
| P6-M2 | the knob OFF falls back to the RAG redirect | `memoir_inner_soul_redirect_test` | `knob-off-notice` (+ `knob-garbage-notice`) | GREEN |
| P6-M3 | the branch condition widened (the blast radius) | `memoir_inner_soul_redirect_test` | `non-ari-byte-identity:event` (+ the 5 sibling corpus ids, `census-branches`) | GREEN |

---

## 6. The regression record

| Suite | Result |
|---|---|
| the registration family (notify-user, self-alarm, board, wedge-capture, instance-ops, metrics-read, secrets, upgrade, attestation) | **203/203 green** |
| `tests/integration/test_service_tool_flag_off_byte_identical.py` | **9/9 green** (the privileged-set pin) |
| `tests/integration/test_maintenancer_spawn_resolves_tools.py` | **13/13 green** |
| `tests/unit/test_memoir_registration.py` (the ceremony pin pack) | **20/20 green** |
| `tests/unit/test_memoir_tools_unit.py` | **43/43 green** |
| the inner_soul family (`test_inner_soul_db.py`, `test_memory_integration.py`, `test_prompt_cache_invalidation.py`) | 50 passed / **16 PRE-EXISTING reds** — the FAILED set is byte-identical on the base leg and on the HEAD leg ⇒ **NET-NEW REDS = 0** (those 16 are base-proven: the memory-integration fixtures hand the tool a Mock manager whose agent-path content is a MagicMock and `_load_growth_rules` regexes it) |
| the metrics lane's own mutation matrix (26 legs + 1 FOLLOW) | **PASS** (see §3.3) |

**Env note (the standing env-poison family):** the agent shell's ambient
`POSTGRES_*` points at PRODUCTION (`10.44.0.2/ensemble_prod`) and
`ENSEMBLE_SELF_ENV=live`. Every DB-touching or boot-touching command in
this wave carried its variables INLINE (the scratch database) or scrubbed
them PER VARIABLE (never the `env -u POSTGRES_*` glob — a silent no-op).
The two new packs scrub `ENSEMBLE_SELF_ENV` per variable as well (the
inner_soul family aborts hard on a `live` identity).

---

## 7. Disclosures

1. **The tool-lane recall is lexical-only** (P4-3) — the v1 semantic
   recall lane needs an embedder injection point that does not exist
   (AM-12 forbids putting one in the tool module). The footer discloses
   it (`semantic=off` + `missing_embedding`). ROUTED to wave 5/P7.
2. **`query_required` maps onto `memoir_internal_error`** (P4-2) with the
   true message preserved — the vocabulary is frozen at 18.
3. **The project_knowledge classification redirects ari to the memoir
   guidance** (it is one of the five knowledge classifications the
   redirect arms, and the plan's AC3 says 5/5). This means a
   project-shaped request can reach ari's memoir store where the RAG-era
   behaviour REJECTed it — the plan's binding behaviour, recorded here
   because it is a semantic consequence, not a defect.
4. **The FC9 measurement's two caveats** (§4) — a conservative baseline.
5. The inner_soul branch reuses the existing redirect POSITION (the
   redirect runs BEFORE the REJECT check — the pre-existing order), so
   the `project_knowledge` arm is taken for ari as it is for every other
   agent.

---

## 8. What is left for wave 5 (P7) + the tester

* **P7's own gates** (the scratch/frozen-binary drill, the boot smoke,
  the migration census, the PG legs) — untouched by this wave.
* **The semantic recall lane** (P4-3) — needs a manager-side query
  embedder handle; a one-line manager change plus a service-construction
  change, both OUT of this wave's file set (the manager is frozen here).
* **The PACKS.md registration** for the two new packs (explicitly
  excluded from this wave; `.agents/tester/PACKS.md` is the tester's).
* **The prompt-artifact SEALED re-pin** — the phase-8 ceremony's job
  (this wave's re-stamp touches the 161-line baseline only).
* The **wave-5 briefing cross-check**: the tools_note doctrine says the
  briefing shows the keepsake zone + recent episodes; the briefing's own
  provenance line (wave 5) is the other half of D4.10 — the TOOL-result
  provenance class landed here.

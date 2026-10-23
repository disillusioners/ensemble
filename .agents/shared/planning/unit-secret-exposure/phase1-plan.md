# Phase 1 — Generator Strip (adopt-unit.sh + unit template)

## 1. Objective

`scripts/upgrade/adopt-unit.sh` mints a unit with ZERO `.env`-derived `Environment=` lines: the POSTGRES_* key-set loop, the minting, the `__ENV_LINES__` splice, and the template token are removed SYMMETRICALLY (template token AND splice case together); dead code and stale "secret env" hygiene prose are removed; the keep-list (D3) survives verbatim. Generated units keep their static directives only.

## 2. Decisions

| ID | Decision | Rationale (verified @9fc95176b8) | Rejected alternatives |
|----|----------|----------------------------------|------------------------|
| D1 | Strip at the generation seam — zero-env units | D-Bus exposure (man 5 systemd.exec:2703-2708; live census returns POSTGRES_PASSWORD to plain users); full runtime redundancy (launcher re-exports .env last, launcher.sh:1649); live unit frozen write-once (:535-539) never re-rendered (promote.sh:418-428/:652-656, restart.sh:234-239) | EnvironmentFile= (second secret file + rotation coupling + layer1-arm doc amendment); LoadCredential= (app-side changes, no precedent) — see decisions.md D1 |
| D2 | KEEP torn-token guard :431-435 verbatim | Now defends against stale/hand-edited template tokens; test :372 keeps passing | Removing it with the splice (loses a defensive refusal for hand-edited templates) |
| D3 | KEEP-list preserved (PORT+ADR-014 :297-301, `_dotenv_value` :273-285, `_splice_line` + 5 tokens, MALLOC :110, umask 077 :568, install -m 0600 :573, provenance stamp, clobber :535-539, `_stage_restart_unit_env` :441-458) | livez verify :467 needs $PORT; hygiene independent of content; decisions.md D3 table | — |

## 3. Tasks

Dev rule (R1, **standing doctrine per W1/D6**): locate every edit by the exact content string below (**PRIMARY**), re-grep before editing — line numbers are **HINTS ONLY**.

| ID | Task | Outcome (testable) | Touchpoints (verified anchors) | Depends on | Risks carried | AC |
|----|------|--------------------|--------------------------------|------------|----------------|----|
| T1.1 | Remove POSTGRES_* key-set loop + minting + empty-ENV_LINES refusal from `_read_config`. Delete: comment `# PORT + POSTGRES_* only (never OPENAI_* etc. — minimal secret surface).` (:302), `ENV_KEYS=""`/`ENV_LINES=""` inits (:304), the whole `for key in PORT $(…POSTGRES_…)` loop :305-309, the `[ -n "$ENV_LINES" ] \|\| { … exit 78 }` refusal :311-314. KEEP launcher exec-check :292-296 and PORT read + ADR-014 numeric refusal :297-301 untouched. Amend the names-only log :315 (`config: user=… env keys(names only):$ENV_KEYS …`) to the zero-env contract, e.g. `config: user=$ADOPT_USER port=$PORT launcher=$LAUNCHER (unit carries NO env from .env — zero-env contract; runtime env supplied by launcher.sh)` — key NAMES only, no values | `_read_config` reads exactly one key (PORT); no ENV_LINES variable exists; refusal count for missing-POSTGRES is gone (missing POSTGRES is no longer a generator concern) | scripts/upgrade/adopt-unit.sh:290-316 | — | R1 | AC1.1, AC1.2 |
| T1.2 | Remove the `__ENV_LINES__` splice case SYMMETRICALLY with T1.4: delete the case arm `*__ENV_LINES__*)` + `out="${out}${ENV_LINES}"` (:417-419) inside `_generate_unit`'s read loop. KEEP the torn-token guard :431-435 and its comment verbatim (D2). KEEP `_splice_line` :407-425 and the 5 remaining splices | `_generate_unit` output contains no spliced env lines; a hand-edited template carrying any `__TOKEN__` still refuses (guard intact) | scripts/upgrade/adopt-unit.sh:400-438 | — | R1, R5 | AC1.1, AC1.3 |
| T1.3 | Remove dead `_systemd_env_quote` function + its comment block (:320-328) — zero remaining callers after T1.1 | `grep -n '_systemd_env_quote' scripts/` → no hits | scripts/upgrade/adopt-unit.sh:320-328 | T1.1 | R1 | AC1.4 |
| T1.4 | Template: delete the `__ENV_LINES__` line (:112); rewrite the header token doc rows :15-16 (remove the env-lines token row; the "Token literals are deliberately NOT spelled out" note :18-20 stays); rewrite the allocator-cap comment :106-109 to drop "The env-lines token below splices ONLY the staged PORT/POSTGRES_* keys" — keep the MALLOC_ARENA_MAX rationale and the drop-in-override sentence. KEEP `Environment=MALLOC_ARENA_MAX=2` (:110) byte-exact (opt_o7 fence literal + directive pins) | Template contains no `__ENV_LINES__`; MALLOC directive byte-identical; header doc describes a zero-env template | scripts/systemd/ensemble-daemon.service:15-16, :106-112 | — | R1, R7 | AC1.1, AC1.5 |
| T1.5 | Remove DRY_RUN masking: delete the masked-section banner `echo "──── generated unit (secret env value masked) ────────"` (**:550**) and the `sed -E 's/^(Environment=POSTGRES_PASSWORD=).+$/\1***masked***/'` pipe (**:551**) — *anchors corrected per W1 (round-1 said :551/:552; reviewer spot-check agreed on :550/:551)*; print `$UNIT_CONTENT` raw under a neutral banner (e.g. `generated unit (zero-env contract)`). Amend the header DRY_RUN doc :63 ("prints … with secret env MASKED" → "prints the sequence and the generated unit") | DRY_RUN preview prints the unit unmasked (nothing secret remains to mask); no sed-masking code remains | scripts/upgrade/adopt-unit.sh:550-551, :63 (W1-corrected) | T1.1 | R1, R2 | AC1.1, AC1.6 |
| T1.6 | Rewrite the hygiene prose that claims the unit carries secret env: header generation description :22-27 (drop "reading config … POSTGRES_*" implication — PORT-only, zero-env unit); SECRET HYDROMETRY section :65-68 → rewritten as the ZERO-ENV CONTRACT (unit carries no .env-derived env; 0600 install retained as defense-in-depth hygiene; live remediation pointer to the P3 runbook); log clause :580 `step (d).1: unit installed → $DEST (0600 — carries secret env)` → `(0600 — hygiene; unit carries no secrets)`; LEDGER_UNIT_FILE string :579 likewise | `grep -rn 'carries secret env\|SECRET HYGIENE\|secret env value masked\|SECRET HYDROMETRY' scripts/` → zero hits on live surfaces | scripts/upgrade/adopt-unit.sh:22-27, :63, :65-68, :579-580 | T1.1, T1.5 | R4 | AC1.6 |
| T1.7 | Keep-list verification sweep (read-only after edits): PORT read + refusal :297-301 intact; livez verify uses `$PORT` (:467+); umask 077 :568; `install -m 0600` :573; clobber refusal :535-539 exit 78; provenance `__GENERATED_NOTE__` splice intact; `_stage_restart_unit_env` :441-458 byte-untouched; remaining 5 token splices intact | Keep-list greps all pass (checklist in AC1.7) | scripts/upgrade/adopt-unit.sh (various, D3 table) | T1.1-T1.6 | R1 | AC1.7 |

## 4. Acceptance Criteria

| ID | Criterion |
|----|-----------|
| AC1.1 | DRY_RUN generation from a fixture `.env` (PORT + all 5 POSTGRES_* keys) emits a unit containing ZERO of: `Environment=PORT=`, `Environment=POSTGRES_`, `__ENV_LINES__`; and containing `Environment=MALLOC_ARENA_MAX=2` in the comment-stripped haystack |
| AC1.2 | `grep -n 'ENV_LINES' scripts/upgrade/adopt-unit.sh` → zero hits; `grep -n 'POSTGRES_' scripts/upgrade/adopt-unit.sh` → zero functional hits (comments/prose rewritten per T1.6) |
| AC1.3 | Torn-token guard :431-435 present verbatim; a hand-planted `__TOKEN__` in a throwaway template copy still triggers the refusal |
| AC1.4 | `grep -rn '_systemd_env_quote' scripts/` → zero hits |
| AC1.5 | Template byte-diff vs parent shows exactly: token line removed, two comment blocks rewritten, nothing else; `Environment=MALLOC_ARENA_MAX=2` byte-identical |
| AC1.6 | Live-surface grep (`carries secret env`, `secret env value masked`, `SECRET HYDROMETRY`, `minimal secret surface`) → zero hits in scripts/ + scripts/systemd/ |
| AC1.7 | Keep-list sweep green (D3 table): all 9 items verified present/untouched |

# unit-secret-exposure — DEV EVIDENCE (implementation lane)

Author: implementation coder. Worktree `/home/nea/ensemble-src-wt-unit-secret-exposure`,
branch `fix/unit-secret-exposure`. Base for every base-diff: **`9fc95176b8dcf6c98c6dd55f346ebe4e4916cb7d`**
(= `origin/latest` at lane start). Plan set: `.agents/shared/planning/unit-secret-exposure/`.

**Consumer: the tester lane.** Everything below is reproducible with the recorded commands.

---

## 0. Commit map (lane branch, no merge/tag/stage/push — giter owns those)

| # | Commit | Content |
|---|--------|---------|
| 1 | `09874acf0` | **Step 0** — plan set (8 files) + approver amendments N1/N2/N3 + `.agents/approver/unit-secret-exposure-tracking.md` + `.agents/approver/active.md` pointer (diff verified pointer-only before staging) |
| 2 | `7f38606c9` | **P1** — generator strip (`scripts/upgrade/adopt-unit.sh`, `scripts/systemd/ensemble-daemon.service`) |
| 3 | `470908e60` | **P3** — docs contract amendment + ONE-TIME live-remediation runbook (template INERT comment, `systemd-adoption.md`, `install-dir-protection-layer1-arm.md`) |
| 4 | `e17de4154` | **P5** — bounded `.env` mode-0600 (`scripts/deploy.sh`, `scripts/upgrade/stage.sh`, adopt-unit comment-only) |
| 5 | `92108f2cd` | **P4** — test-pack rework + mutation legs + census + dev-evidence |
| 6 | `f4bb01bb8` | **docs fold** — verifier C1/C2 (sweep inventory accuracy + behavioral-pin disclosure wording) |
| 7 | `769130f5c` | **docs** — leader decisions (D7 re-opened proxy-side + N3 stage-owner precondition documented + deferred mechanical guard) |
| 8 | `eb90a600c` | **review-advisory fold** — W1 silent-root comment truth-fix, W2 `umask 077` write-window fences (stage.sh + deploy.sh), A2 D9 pins (pack 75 → 77 asserts), A3/A4/A6/A7/A8 precision |
| 9 | `13b56895d` | **tidier quality pass 1** — residual drift fixes (stale umask claims, post-strip adopt-unit/stage.sh anchors, W1 cross-uid framing, pack counts 75 → 77) |
| 10 | *(this commit)* | **tidier quality pass 2** — 3-worker review fixes (comment truth in stage.sh/deploy.sh/adopt-unit.sh, error-message accuracy, mutation-dead demo-guard mode pin fixed + verified RED-capable, doc anchor corrections) |

Phase order note: P5 was implemented **before** P4 per the plan's phase table (P4 depends on P1 **and** P5 — the mode pins APPEND to the file P4 edits; coupling map + A7').

---

## 1. Step 0 — approver amendments N1 / N2 / N3 (folded into the plan set)

- **N1 — SC3 scope fix** (`plan-overview.md` §7 SC3): rescoped from "repo-wide" to **live surfaces only** — `scripts/`, `scripts/systemd/`, `docs/runbooks/` — EXCLUDING `.agents/shared/planning/` **and** `docs/investigations/` (historical archives keep legitimate frozen mentions, e.g. `docs/investigations/2026-10-18-o8-stale-subprocess-sweep-and-killmode-audit.md:140`; the earlier wording was literally unsatisfiable).
- **N2 — two stale-prose sites + needles**: (a) `docs/runbooks/systemd-adoption.md:50` (A2-matrix row-4 coverage cell "… + **secret masking** + zero mutations") added to **T3.2** touchpoints and rewritten to zero-env wording; (b) `tests/test_adopt_unit.sh:16-20` (pack header "substituted User/port/PG … secret masking") added to **T4.7** touchpoints and rewritten; (c) **T3.6** needle list extended with `secret masking|substituted User/port/PG` and its scope extended to include `tests/`.
- **N3 — stage-as-install-owner precondition**: D5a/A3' extended to the **stage** path — `stage.sh:692-693` (pre-fold :684-689) rewrites the live `.env` via `sed > tmp && mv -f` (inode replacement re-owns the file to the staging uid; a cross-uid stage + the new `chmod 600` would brick the launcher's `.env` read → D4 boot-block family). Tonight's lane stages as `nea` (the install owner). Recorded in `decisions.md` D5 (Safety ¶) + `phase5-plan.md` D5a + `plan-overview.md` §8, where the "Runtime effect on the running service: ZERO" claim is now **conditioned** on that precondition.

---

## 2. P1 — generator strip (AC1.1–AC1.7)

All verified against the tree AFTER the edits:

| AC | Result | How |
|----|--------|-----|
| AC1.1 | PASS | DRY_RUN from a fixture `.env` (PORT + all 5 `POSTGRES_*`): `Environment=PORT=` / `Environment=POSTGRES_` / `__ENV_LINES__` all ABSENT; `Environment=MALLOC_ARENA_MAX=2` PRESENT (comment-stripped haystack); exit 0 |
| AC1.2 | PASS | `grep -n 'ENV_LINES' scripts/upgrade/adopt-unit.sh` → 0 hits; `grep -n 'POSTGRES_' scripts/upgrade/adopt-unit.sh` → 0 hits |
| AC1.3 | PASS | Torn-token guard present **verbatim** (`# torn-substitution guard: no __TOKEN__ may survive` + the refusal); hand-planted `__TORN_TOKEN_TEST__` in a throwaway template copy → exit 78 + "unsubstituted template token(s) survived generation" |
| AC1.4 | PASS | `grep -rn '_systemd_env_quote' scripts/` → 0 hits |
| AC1.5 | PASS | Template diff vs base = exactly: env-lines token row removed, ZERO-ENV CONTRACT paragraph added, allocator-cap comment rewritten, `__ENV_LINES__` line removed, `Environment=MALLOC_ARENA_MAX=2` byte-identical |
| AC1.6 | PASS | `carries secret env` / `secret env value masked` / `SECRET HYDROMETRY` / `minimal secret surface` → 0 hits in `scripts/` + `scripts/systemd/` |
| AC1.7 | PASS | Keep-list sweep (D3, 9 items): PORT read + ADR-014 refusal ✓ · `_dotenv_value` ✓ · `_splice_line` + **5** remaining splices ✓ · torn-token guard ✓ · `Environment=MALLOC_ARENA_MAX=2` byte-exact ✓ · `umask 077` ✓ · `install -m 0600` ✓ · `__GENERATED_NOTE__` provenance splice ✓ · clobber refusal exit 78 ✓ · `_stage_restart_unit_env` **byte-untouched** (comment-only in P5/T5.3) ✓ |

Commands:
```
bash -n scripts/upgrade/adopt-unit.sh            # SYNTAX OK
grep -n 'ENV_LINES\|POSTGRES_' scripts/upgrade/adopt-unit.sh     # 0 hits
grep -rn '_systemd_env_quote' scripts/                          # 0 hits
grep -rn 'carries secret env\|secret env value masked\|SECRET HYDROMETRY\|minimal secret surface' scripts/ scripts/systemd/   # 0 hits
```

---

## 3. P2 — launcher seam verification (AC2.1–AC2.4) — **VERIFY-ONLY, ZERO launcher.sh diffs**

`git diff --stat 9fc95176b -- launcher.sh` → **empty** (zero diffs attributable to P2). `launcher.sh:144-145` (launchd prose) **byte-untouched** per D2b.

- **T2.1 single env source**: `load_env_file` (`launcher.sh:146-189`) is the only env-ingest path — per-key `export "$key=$value"` at `:186`; missing file → loud WARN + inherited env (`:148-150`). Call site `load_env_file "$INSTALL_DIR/.env"` at **`:1649`** inside `main()`, **env-first** — `resolve_binary` is invoked later at `:1665`, so env precedes binary resolution. Structural-precedence comment `:1646-1647` ("Exports win over the binary's own .env load … precedence is structural").
  **A10 precision:** `run_app.py`'s only-unset `.env` load is **frozen-binary-only** (`sys.frozen` gate, `:9`) and **quote-unaware** (naive `split('=', 1)` + strip, no quote handling) — the structural precedence guarantee lives **solely** in the launcher path (`:1649` + `:186`).
  **Once-per-process shape:** `load_env_file` is called ONCE per launcher process, **OUTSIDE `run_loop`**; child restarts inside the loop re-inherit the exports without re-reading `.env` (comment `:1657`).
- **T2.2 MALLOC triangle (no inversion)**: template static directive `Environment=MALLOC_ARENA_MAX=2` seeds the unit env; launcher `: "${MALLOC_ARENA_MAX:=2}"` + `export` (`:1658-1659`) is **default-only** (empty-as-unset); a `.env`-staged value wins via `:1649`. Post-strip the unit rung carries ONLY this static directive → effective chain unchanged.
- **Zero-consumer census for the emptied unit rung** (arch §4.1): no daemon / supervision-classify / journal-sweep / proc_tools / stop-ensemble / promote-rollback / plist / deploy-psql consumer depends on unit-env PORT/POSTGRES_* — each reads `.env` **files** or ambient launcher exports, never the unit's stored env list. The rung's only consumer was the D-Bus reader (the defect itself).
- **T2.4 comment sweep**: `grep -n 'unit' launcher.sh` → only `:9` ("sourced unit BY CONTRACT" — launchd/systemd) and `:1337` ("unit-testable seam") — **no stale unit-env claims**.
- **T2.5 greps**: `grep -rn 'EnvironmentFile' scripts/ launcher.sh tests/` → **0 hits**. Repo-wide minus `docs/` → exactly **1** hit: `test/packs/hard_reboot_drill_e2e_test.sh:707` (the acknowledged **A8 precedent**, `systemd-run --property=EnvironmentFile=` — untouched). Both plists **byte-identical** to parent.

---

## 4. P3 — docs + remediation runbook (AC3.1–AC3.7)

- **AC3.1** — template INERT comment states the zero-env contract; the 0600 install is framed as defense-in-depth hygiene that does **NOT** gate the D-Bus surface.
- **AC3.2** — `systemd-adoption.md`: §3(d) env-sourcing rewritten (PORT-only read, zero-env unit, "0600 — hygiene; the unit carries no secrets"); **4-rung precedence contract** inserted ("Env sourcing & precedence", :182 — re-anchored post-fold; was :168 before the N3 precondition block) — `.env` (launcher `load_env_file`, LAST-wins) > unit `Environment=` (**EMPTY BY DESIGN**; only the static MALLOC_ARENA_MAX) > `systemctl set-environment` (inherited — vacuous in-repo) > code defaults (`launcher.sh:1658-1659`; `config.yaml` defaults) + the A10 / once-per-process sentences; missing-`.env` INTENDED fail-fast with A4 conditions (exit 75 **conditional on `ensemble.json` declaring postgres**; **no** config-absent refusal code — absent/corrupt json → silent SQLITE auto-derive, flagged as a known residual) + A5 exit taxonomy (75 = PG unreachable / 78 = auth refused + auth-config misconfig / config-absent = no code) + "**watch the journal, not unit state**" + restart-storm NON-ISSUE cite.
- **AC3.3** — `install-dir-protection-layer1-arm.md`: post-strip premise, stale ref `launcher.sh:1558` → **`:1649`**, rotation scope shrunk to `.env` (D8), A6 ownership-consequence sentence + the stage-ownership sentence.
- **AC3.4** — checklist `(f)` verified **NOT stale → untouched**; fence literals unchanged vs parent (`MALLOC_ARENA_MAX=2` ×3, `adoption/unit-regeneration` ×1); `tests/test_opt_o7_checklist_fence.py` 8/8 green.
- **AC3.5** — NEW **§5a "ONE-TIME LIVE REMEDIATION"** (documentation only; user decides; no live action tonight): (a) drop-in with the **LONE `Environment=`** full-list reset (the per-variable empty-string form is named **WRONG** and explained), scratch-unit empirical check BEFORE live application, verification gate = **NO `KEY=VALUE` entries** in `systemctl show -p Environment` (the bare `Environment=` line on an emptied list is EXPECTED — not a failure), notes (i)–(iv), honest running-process caveat (exposure closes at daemon-reload; the process keeps inherited env until its next restart); (b) re-adoption composite = **§5 teardown THEN §3(d) adoption** (A2) with the exit-78 clobber precondition, the **stop-the-interim-script-mode-daemon-before-`enable --now`** step, and the unit-backup hazard; **W6 census** (all `ensemble-*.service` units enumerated + `systemctl show -p Environment` on each); recommendation (a) now + (b) at the next window + explicit "user decides — no live action tonight". **Key names only — no values anywhere.**
- **AC3.6 — T3.6 sweep inventory (final, N2-extended scope `scripts/ scripts/systemd/ docs/runbooks/ tests/`)** — **4 hits** (MEASURED by re-running the exact T3.6 needle list against the final tree — the earlier "7 hits" figure was the pre-P4 count), all adjudicated:
  1. `docs/runbooks/install-dir-protection-layer1-arm.md:33` — **our own negation** ("carries **NO** inline secret env") — whitelisted (negation, not a stale claim).
  2. `tests/test_adopt_unit.sh:417` (was :398; shifted by the A2 D9 pins) — the base `:377` needle was **MOVED and INVERTED, not eliminated**: base asserted `assert_contains "preview announces the masked section" "generated unit (secret env value masked)"`; P4 replaced it with `assert_not_contains "no masked-section banner (masking removed with the minting)" "secret env value masked" "$H_OUT"` — the same literal now lives inside a **negative needle**, where it is the assertion that PROVES masking removal. (The other three pack sites — `:17`, `:18`, `:374` — WERE eliminated by P4.) **EXPECTED hit — whitelisted; it is NOT an unexplained hit.**
  3. `tests/integration/test_module_lane_isolation.py:464` — "webhook_secret masking" — **unrelated mechanism** (webhook HMAC secret), whitelisted like the `oauth2-auth.md` D9 baseline entry.
  4. `tests/unit/test_mcp_server_crud.py:1239` — "a config containing a secret env key" — **unrelated mechanism** (MCP server config), whitelisted.
  → **4 hits = 3 whitelisted (our own negation + 2 unrelated mechanisms) + 1 EXPECTED (the pack's moved-and-inverted negative needle) → zero unexplained hits.**
- **AC3.7** — fix-delivery path stated in §5a: `scripts/` does **NOT** ship in the staged payload; the fixed generator reaches operators via an updated checkout post-merge.

---

## 5. P5 — bounded `.env` mode-0600 (AC5.1–AC5.6)

Framing (A6): **defense-in-depth HYGIENE, NOT threat-model closure** — the exclusion delta is **∅** for every account class (`/home/nea` mode 750; the `nea` group has no supplementary members → 644→600 excludes nobody new).

**5 write sites, chmod 600 strictly AFTER each write (R6 order-sensitivity):**

| # | Site | Write | chmod | adjacency |
|---|------|-------|-------|-----------|
| 1 | `scripts/deploy.sh` | installed-copy `cp` | `run "chmod env 0600" -- chmod 600 "$INSTALL_DIR/.env"` | +3 ✓ |
| 2 | `scripts/deploy.sh` | demo-guard generate-`mv` (`.env.prod.demo` only) | `&& chmod 600 "$ENV_SOURCE"` in the same chain | +1 ✓ |
| 3 | `scripts/upgrade/stage.sh` | marker rewrite-`mv` | `chmod 600 "$ENV_FILE"` | +3 ✓ |
| 4 | `scripts/upgrade/stage.sh` | marker append-`mv` | `chmod 600 "$ENV_FILE"` | +3 ✓ |
| 5 | `scripts/upgrade/stage.sh` | create path `> "$ENV_FILE"` | `chmod 600 "$ENV_FILE"` | +3 ✓ |

- **AC5.1** — all 5 sites covered; `chmod` failure on stage paths is **loud** (`_warn` + `exit 1`) rather than silent.
- **AC5.2** — static byte-adjacency pins green **and** the W5 mutation leg proven (below). **Behavioral pin: NOT deferred** — see §6.
- **AC5.3** — `_stage_restart_unit_env` functionally byte-identical (comment-only delta; `cat "$tmp" > "$ENV_FILE"` untouched).
- **AC5.4** — ownership precondition + deploy-ownership + **stage-ownership (N3)** notes carried in `decisions.md` D5 / `phase5-plan.md` D5a / `layer1-arm.md`.
- **AC5.5** — hygiene framing present everywhere; `.env.bak-2026-10-20T21:30:40Z` (644, live-dir one-off) and the existing 644 `.env.prod.demo` disclosed as morning items (D10.6 / D10.7).
- **AC5.6** — live-boundary disclosure (stage-time chmod touches the LIVE `.env`, mode-only) present in `phase5-plan.md`, `decisions.md` D5, `plan-overview.md` §8.

**Residual (D5c):** `.env.bak-2026-10-20T21:30:40Z` (644, install dir) = accepted residual tonight (live-dir, unreachable by repo code) → morning item D10.6. The **existing** 644 `.env.prod.demo` stays 644 until an explicit chmod or delete-then-regen → morning item D10.7.

---

## 6. P4 — tests (AC4.1–AC4.4)

### 6.1 The reworked pack — `tests/test_adopt_unit.sh`

```
timeout 300 bash tests/test_adopt_unit.sh
→ PASS=77 FAIL=0   (rc=0; re-run at the final fold tree eb90a600c — the A2 D9 pins appended in the final fold grew the pack 75 → 77)
```
Sections: (a) pid-alive refusal · (b) host guards · (c) polkit · (d) dest/launcher/port · naming table · DRY_RUN happy path · %h-under-home · **MUTATION LEG** · **MODE PINS**.

- **T4.1** — the 8 breaking asserts replaced: the 3 substituted-env asserts → zero-env pins (`Environment=PORT=`, `Environment=POSTGRES_`, `__ENV_LINES__`); the masking assert → "no masked-section banner"; the banner assert → "preview announces the zero-env section" (`generated unit (zero-env contract)`). **The WorkingDirectory pin survived** (the :349 hazard avoided). "real password NEVER echoed" **kept** (still meaningful hygiene backstop).
- **T4.2** — the PG-quoting-arm section removed (`_systemd_env_quote` no longer exists → its masking assert died with it).
- **T4.3** — fixture-extension clause **struck** (false premise, W7): the fixture already writes all six keys, so the negatives run against maximal input. Positive control `Environment=MALLOC_ARENA_MAX=2` (comment-stripped) kept.
- **T4.4** — mutation leg, both legs recorded (§7).
- **T4.7** — pack header: N2 prose rewritten + **pin disclosure** (real-systemd mutation = NAMED SKIP, §19 precedent `test_release_journal.sh:2474-2517`; 4-rung precedence = **DOC-PINNED**; optional cheap executable pin noted as a nice-to-have, NOT taken — the cheap form available is the in-pack "generator output is independent of ambient env" check, which the mutation leg already demonstrates via the fenced copies).
- **T5.4** — mode-pin block **appended** (P5 owns it): 5 static byte-adjacency pins + the W5 mutation leg.

### 6.2 T4.5 census — packs run, results, base-diff evidence

| Pack | Command | HEAD | BASE `9fc95176b` (identical command) | verdict |
|------|---------|------|-----------------------------------|---------|
| `tests/test_adopt_unit.sh` | `timeout 300 bash tests/test_adopt_unit.sh` | **PASS=77 FAIL=0** (re-verified at the final fold tree; the pre-fold run was 75/75 — the A2 D9 pins added 2 asserts) | **PASS=62 FAIL=13** (same command against a `git archive` base extraction, HEAD pack copied in — recorded pre-fold at the 75-assert pack) | **FAIL@parent / PASS@HEAD** — the 13 REDs are exactly the new pins |
| `tests/test_release_journal.sh` | `timeout 850 bash tests/test_release_journal.sh` | **338 passed, 5 failed** | **338 passed, 5 failed** — identical failure names | **pre-existing, 0 net-new** |
| `tests/test_stage_freshness_guard.sh` | `timeout 800 bash tests/test_stage_freshness_guard.sh` | **110 passed, 8 failed** | **110 passed, 8 failed** — identical failure names | **pre-existing, 0 net-new** |
| `tests/test_supervision_classify.sh` | `timeout 300 bash tests/test_supervision_classify.sh` | **PASS=237 FAIL=0 SKIP=0** | not needed (green) | green (stub-driven, zero systemd contact) |
| `tests/unit/test_connect_timeout.py` | `.venv/bin/python -m pytest tests/unit/test_connect_timeout.py -q` | **9 passed** | not needed (green) | green |
| `tests/test_opt_o7_checklist_fence.py` | `.venv/bin/python -m pytest tests/test_opt_o7_checklist_fence.py -q` | **8 passed** | not needed (green) | green |

**Base-diff method (both sides identical):**
```
git archive 9fc95176b | tar -x -C "$(mktemp -d -t base-diff.XXXXXX)"
cd <base> && bash tests/test_release_journal.sh        # same command, same flags
cd <base> && bash tests/test_stage_freshness_guard.sh # same command, same flags
cd <base> && cp <HEAD>/tests/test_adopt_unit.sh tests/ && bash tests/test_adopt_unit.sh
```
**The 13 RED-at-base pins** (house FAIL@parent / PASS@HEAD proof): 3 zero-env pins (PORT / POSTGRES_ / — the `__ENV_LINES__` output pin stays GREEN at base because base's generator CONSUMED that token; its RED leg is the template-copy injection, §7) · masking-banner (×2) · mutation baseline + restore (×2) · the 5 mode pins · mode-pin mutation baseline + restore (×2).

**The 5 pre-existing `test_release_journal.sh` failures (identical at base):** `f2 gate: guard refusal fires, not the F2 one` · `cap boundary: count=3 in-window reads 3` · `count=2 in-window reads 2` · `count_rollback after stale window resets then counts → 1` · `9 evicts the true-oldest (2020 ISO, not pinned)` — ledger/cycle-count assertions, **untouched by this lane** (no adopt-unit / stage / deploy / template / docs surface).

**The 8 pre-existing `test_stage_freshness_guard.sh` failures (identical at base):** `(vi)` override event journaled ×3 · `(vii-c)` override reason=dirty-build · `(viii-f)` override reason=provenance-malformed · `(x)` override event IS in the durably-created journal · `(x)` refusal event IS in the durably-created journal — provenance/override journaling, unrelated to the `.env` mode change.

**Zero edits outside `tests/test_adopt_unit.sh`** (T4.5): `git diff --stat 9fc95176b -- tests/ test/` shows the pack file only.

### 6.3 NAMED-SKIP — two substrate packs NOT run tonight (disclosed, not silently skipped)

Per this lane's hard constraints (**no systemctl mutations**, no live contact) two census packs are **not executed** on this live systemd host; they are verified **byte-untouched vs base** instead:

- `tests/test_supervision_e2e.sh` — on a systemd host with a reachable user manager its fences OPEN and it **writes real user-manager unit files** into `$HOME/.config/systemd/user/`, runs `daemon-reload` on the **user** bus and starts/stops transient scopes (`systemd-run --user --scope`). That is systemctl-mutation contact → **not run**. Verified: `git diff --stat 9fc95176b -- tests/test_supervision_e2e.sh` → empty. Its only `Environment=` hit (`:185 Environment=PORT=$port`) is the pack's **own hand-written fixture unit** (per T4.5) — **not** generator output.
- `test/packs/hard_reboot_drill_e2e_test.sh` — performs `sudo -n systemctl stop "$UNIT"` (simulated reboot leg), creates scratch PG roles on the local cluster and reads live unit properties → **absolutely not run tonight**. Verified byte-untouched vs base; its `:707 --property="EnvironmentFile=…"` remains the acknowledged **A8 precedent** (intact).

(§19 NAMED-SKIP precedent: `test_release_journal.sh:2474-2517`.)

### 6.4 T4.6 — boot-smoke fixture SPEC (for the tester phase; **not a runnable fixture tonight**)

*Scratch install, zero live contact.* Fixture shape the tester should build:

1. **Fence** — `WORK="$(mktemp -d -t boot-smoke.XXXXXX)"`; `FAKE_HOME="$WORK/home"`; `INSTALL_DIR="$FAKE_HOME/agents-ensemble"`; export `HOME="$FAKE_HOME"`. **Never** resolve a live-shaped HOME-derived path (cn 5fe71cb9); escape-guard the fixture before any destructive step.
2. **Port mapping (T4.6 / A6')** — the root `config.yaml` daemon block interpolates **only `PORT`** for the port line (`port: ${PORT:-8088}`); the **host** line interpolates `HOST`; the daemon never reads `DAEMON_PORT`. Therefore the worktree fence's `DAEMON_PORT=8079` **must be mapped**: `export PORT="$DAEMON_PORT"` — the in-house precedent `frontend/scripts/boot-e2e-auth-daemon.sh:154-155`.
3. **DB** — a **scratch** PostgreSQL (throwaway role + DB + schema) with dummy values; **key names only** in any artifact.
4. **Env scrub — per-variable, NEVER `env -u POSTGRES_*`** (cn 44789113 — that idiom silently unsets NOTHING): scrub/set `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT` individually, and assert `POSTGRES_DB != ensemble_prod` before any step.
5. **Assertions** — (i) the generated/minted preview unit contains **zero** `Environment=POSTGRES_` and zero `Environment=PORT=`; (ii) the daemon boots green against the scratch PG on `$PORT` (`/livez` 200).
6. **Out of scope** — no live install dir, no live unit, no promote/rollback, no `systemctl` on the live host.

### 6.5 T5.4 — behavioral pin (D5b): **INCLUDED, not deferred**

Condition: "the behavioral pin is included ONLY IF the existing FAKE_REPO stage harness already exercises the marker-rewrite path". **It does.** Probe (temporary instrumented copy of the harness, deleted immediately — the repo file is untouched):

```
PROBE file=/tmp/stage-freshness.*/home/agents-ensemble/.env          mode=644 marker=0   ← harness's OWN fixture (created by the harness, not by stage.sh)
PROBE env=/tmp/stage-freshness.*/tip-sbx-override/.env                mode=600 marker=1
PROBE env=/tmp/stage-freshness.*/tagtrap-sbx/.env                      mode=600 marker=1
PROBE env=/tmp/stage-freshness.*/virgin-sbx/.env                       mode=600 marker=1
PROBE env=/tmp/stage-freshness.*/short-absent-sbx/.env                 mode=600 marker=1
PROBE env=/tmp/stage-freshness.*/dirty-sbx/.env                        mode=600 marker=1
PROBE env=/tmp/stage-freshness.*/malformed-ovr-sbx/.env                mode=600 marker=1
```
Six sandbox install `.env` files carry **marker=1** (`ENSEMBLE_SELF_ENV` staged by the real `stage.sh`) **and mode=600** → the marker-rewrite/append path is exercised by the harness AND the new chmod executes there (a failing chmod would `exit 1` and fail the stage). The `home/agents-ensemble/.env` at 644 is the harness's own pre-created fixture, never touched by `stage.sh`. **No edit to `test_stage_freshness_guard.sh` was made or is needed** (T4.5 forbids edits outside the adopt pack; the behavioral coverage comes from the existing harness).

---

## 7. Mutation legs — RED/GREEN records (house rule)

### 7.1 T4.4 zero-env legs (generator/DRY_RUN level; **the repo template is never mutated**)

Vehicle: `adopt-unit.sh` + `lib.sh` + the template **copied** into `$WORK` (the generator resolves its template SELF_DIR-relative, so both files must be copied to self-contain). Dummy-only values, clearly marked.

```
### T4.4 ZERO-ENV MUTATION LEGS
-- leg 1 GREEN (baseline)
   GREEN: no 'Environment=POSTGRES_' in preview
-- leg 2 RED (planted minted line in TEMPLATE COPY)
   RED as designed: planted line DETECTED by the zero-env pin
-- leg 3 GREEN (template copy restored)
   GREEN: pin recovered after restore
```
Planted line: `Environment=POSTGRES_PASSWORD=DUMMY_MUTATION_PW_NOT_A_SECRET` — appended to the **template copy** only. (It is not a `__TOKEN__`, so the torn-token guard correctly does **not** fire — the leg proves the pins detect minted **content**, not merely tokens.) The same three legs run **inside** the pack (section "MUTATION LEG") and are part of the pack total (75 at fold-time; 77 after the A2 D9 pins).

### 7.2 T5.4 mode-pin leg (static byte-adjacency; `stage.sh` **copy**)

```
### T5.4 MODE-PIN MUTATION LEG
-- leg 1 GREEN (baseline copy)
   GREEN: create-site chmod present within +1..+3
-- leg 2 RED (create-path chmod 600 removed from COPY)
   RED as designed: pin detects the removed chmod
-- leg 3 GREEN (restored)
   GREEN: pin recovered after restore
```
Removal command (on the copy): `sed -i '/^    chmod 600 "$ENV_FILE" || { _warn "failed to chmod 600 \$ENV_FILE"; exit 1; }/d'` — the 4-space indent matches **only** the create-path chmod (the two mv-path chmods indent 8). The same three legs run inside the pack (section "MODE PINS").

---

## 8. Live-contact attestation (SC8)

- **No** live unit edits, **no** live `.env` edits, **no** `systemctl` mutations, **no** restarts, **no** instance surgery, **no** writes to any live/prod DB.
- Write surface = `/home/nea/ensemble-src-wt-unit-secret-exposure` only. The shared checkout `/home/nea/ensemble-src`, `ensemble-src-wt-wedge-capture`, `ensemble-src-wt-nestops-shakedown`, every `*wt-stage*` worktree and every paused lineage were **not touched**.
- The only host-systemd reads were **read-only** property reads performed by the two substrate packs' **source inspection** (not executed) and by `systemctl --version`-style probes — **no mutating systemctl call was issued by this lane**.
- The P5 stage-time chmod touches the LIVE install `.env` **at real stage time** (mode-only, ∅ exclusion delta) — **W2 disclosure**, carried in `phase5-plan.md`, `decisions.md` D5 and `plan-overview.md` §8; the leader carries it into the morning report.
- Test fences: `mktemp` workdirs + `FAKE_HOME`; **per-variable** POSTGRES_* scrub/set (never `env -u POSTGRES_*`); no real-LLM nodes; no real secret values in any artifact (key names + clearly-marked DUMMYs in throwaway copies only).

---

## 9. Plan deviations (each with justification)

| # | Deviation | Justification |
|---|-----------|---------------|
| D1 | **P5 implemented before P4** | The plan's phase table puts P5 "before P4" (P4 depends on P1 **and** P5; A7' reconciles the shared `test_adopt_unit.sh` ownership). P5's functional edits landed in `e17de4154`; P5's *pins* are appended by P4, as the plan specifies. |
| D2 | **Two census packs NOT executed** (named-skip, §6.3) | They mutate systemd (user-manager unit files + `daemon-reload` + transient scopes) and the drill pack runs `sudo systemctl stop` + scratch-PG work on the **live** host — forbidden by this lane's hard constraints. Both verified **byte-untouched vs base** instead; the substrate legs remain unexercised tonight (disclosed). |
| D3 | **`stage.sh` chmod failure is loud** (`_warn` + `exit 1`) rather than best-effort | The plan fixes the *placement* (after each write) but not the failure mode. A silently-skipped chmod would leave the posture unenforced — exactly the umask-reset regression P5 exists to close. *(Cross-uid loudness corrected per W1 @eb90a600c, refined by the tidier robustness review: a cross-uid NON-root stage USUALLY fails loudly one step earlier — at the temp-file create or the mv, both of which need write permission on the install DIR, not ownership of the file — but this is NOT guaranteed: a group/other-writable install dir lets the mv succeed SILENTLY and re-own the `.env`; a cross-uid ROOT/sudo stage does NOT fail here — the mv re-owns the live `.env` to root and the chmod SUCCEEDS under root, so the stage exits 0 SILENTLY and the failure surfaces only POST-restart, as the unreadable-`.env` boot-block family (exit-75 retry loop; the journal is the only signal). The mitigation is the §3(d) stage-ownership PRECONDITION (N3), not stage-time loudness — see the stage.sh comment and runbook §3(d).)* |
| D4 | **`deploy.sh` demo-guard site restructured** to an `&&` chain (`sed && mv && chmod`) | Required to satisfy T5.4's byte-adjacency spec (chmod within ≤3 lines of the write) while keeping the **loud-failure** path (the `else` branch re-creates the original `rm -f` / `_log FAILED` / `exit 1`). Behavior on failure is unchanged; the success path additionally pins 0600. |
| D5 | **Behavioral pin (D5b) INCLUDED via the existing harness** rather than deferred | The condition (harness already exercises the marker-rewrite path) is met — proven by probe (§6.5). Deferral would have been wrong; no new plumbing and no edits outside the adopt pack were needed. |
| D6 | **T4.7's "optional cheap executable pin" NOT taken** | The cheap form available (generator output independent of ambient env) is already demonstrated by the fenced-copy mutation legs (the fenced copy runs under the same ambient env as the repo copy and produces byte-identical, zero-env output). The doc-pin statement is in the pack header as specified; taking the extra pin would have added redundancy without new signal. |
| D7 | **One extra stale-prose site fixed beyond the N2 list** — `systemd-adoption.md` DRY_RUN prose ("prints … with the PG password masked", two places) | It becomes false after P1 and sits in the §3 area T3.2 already rewrites; the N2 needles would **not** have caught it (it says "PG password masked", not "secret masking"). Fixed in the T3.2 pass and disclosed here. |

---

## 10. Open items routed to the tester

1. **Re-run the packs** with the recorded commands; expect `test_adopt_unit.sh` **77/77** (75 pre-fold + the 2 A2 D9 pins appended in `eb90a600c`), and the two pre-existing-failure packs to reproduce **338/5** and **110/8** (base-identical).
2. **Boot-smoke fixture**: build per §6.4 (DAEMON_PORT→PORT mapping, scratch PG, FAKE_HOME, per-variable scrub) and confirm the generated unit carries **zero** `Environment=POSTGRES_` / `Environment=PORT=`.
3. **Doc-content gates**: re-run the T3.6 sweep (N2-extended needles + `tests/` scope) — expect **exactly 4 hits**: 3 whitelisted (our own negation + 2 unrelated mechanisms) + 1 EXPECTED (the pack's moved-and-inverted negative needle at `tests/test_adopt_unit.sh:417` (was :398; shifted by the A2 D9 pins), which is the assertion that proves masking removal). Do **NOT** count that pack needle as an unexplained hit; any hit beyond those 4 is unexplained.
4. **D9 gate**: confirm the only non-`docs/` `EnvironmentFile=` hit repo-wide is `test/packs/hard_reboot_drill_e2e_test.sh:707` (A8 precedent).
5. **Mutation legs**: the tester may re-run the §7 legs standalone (commands in-file) and confirm RED/GREEN.
6. **Not verifiable tonight** (needs a substrate): the real-systemd mutation path (named skip) and the two substrate packs (§6.3) — do not count them as PASS; keep them SKIP-with-disclosure.
7. **Morning items unchanged by this lane** (D10): live remediation (user decision — §5a), `.env.bak-2026-10-20T21:30:40Z` chmod/removal, existing 644 `.env.prod.demo` chmod, deferred-key-rotation coupling note update, silent-SQLite refusal-hardening candidate, executor `--setenv` prefix fence.

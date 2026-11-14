# Rogue-Containment P2 — Component E2: Mutation Pins + Sanctioned Boot + (D) Measurement + CHANGELOG Catch-Up + S-Gates + Sequencing

**Feature:** rogue-containment-p2 (residual-hardening lane on the merged v0.29.22 containment work)
**Component:** E2 — pins/verification/measurement integration leg (B-residual DSN fence + A-residual holder identity + A1 hygiene + (D) adjudication arming + CHANGELOG + S-gates + sequencing)
**Mode:** Mode 1 — design only. NO code, NO test runs, NO repo writes except this file. Parallel sibling component plans (B2 fence, A2 holder) are being authored concurrently in this directory; this plan pins against the COMMISSIONED behavior and flags every anchor that could not be verified against a landed sibling.
**Worktree:** `/home/nea/ensemble-src-wt-rogue-contain`, branch `feature/rogue-port-containment`, tip `bf4bfde6e` (== `origin/latest` base per the lane claim `wt.claim.rogue-contain`). Canonical `/home/nea/ensemble-src` is FORBIDDEN for writes.
**Governing analysis:** `.agents/shared/planning/bind-race/rogue-port-containment-analysis.md` — §5.4 (burst-window defer + trigger), §5.5 (exit-78 reuse; unit `RestartPreventExitStatus=78` at `scripts/systemd/ensemble-daemon.service:110`, verified), §7 (hardening set), §8 H2 (post-Lane-1 U inferred, never measured), §9 items 1+6. Analysis wins on conflict; conflicts disclosed in § CONTRADICTIONS.
**Prior-lane inputs:** `rogue-containment/component-E-regression-pins-and-sequencing.md` (E-i/E-ii/E-iii vocabulary, sanctioned-lane census, harness contract), `rogue-containment/harness/boot_rogue_mirror.sh` (105 lines, read in full).
**S-gate vocabulary note:** the prior lane defined NO S1..Sn (its stage verification was sequencing item 10 + E-i/ii/iii). This file defines S1..S9 fresh from the house stage legs.

---

## § E2 PIN MATRIX (mutation-proven, non-vacuous)

House mutation discipline is BINDING (`mutation-01-guards-need-counter-mutation` POK): every pin is a POSITIVE shape that reddens under a NAMED counter-mutant; absence-only pins are prose-satisfiable and rejected at review. Fresh M-series per family: **BF-** (B-fence), **AH-** (A-holder), **HY-** (A1-hygiene) — continuing the existing M1–M6 (port-ownership) / P-M1..P-M13 / B-M8..B-M14 numbering without collision.

**Anchor corrections (verified by reading the files):** `test_db_resolution_guard.py` = 10 tests ✓ (extends); `test_port_ownership.py` = 26 ✓ (extends); `test_port_ownership_pins.py` = 18 ✓ (extends); `test_pkill_overlay_guard.py` = 13 ✓; `test_pkill_overlay_pins.py` = 10 ✓; `test_main_entry.py` lives at **`tests/test_main_entry.py`** (27 tests — NOT `tests/unit/` as the commission path said); the conftest session fence is `tests/conftest.py:912-919` ✓ (autouse `ENSEMBLE_PORT_CLAIMS_DIR` → tmp for ALL pytest runs; dedicated pins pass explicit tmp dirs and are unaffected — every new claims-dir-touching pin MUST pass its own tmp dir, same as the existing pins, so the fence neither masks nor breaks it); `tests/support/wedge_smoke.py:102-103` `free_port()` re-rolls off 9797/7979 ✓ — every pin/mirror leg that binds a real scratch socket uses `free_port()` or an explicit scratch port.

### Family BF — B-residual prod-DSN name fence (extends `tests/unit/test_db_resolution_guard.py`, 10 → ~18)

The commissioned fence: a **prod-NAME predicate on the RESOLVED DSN** in boot preflight, firing in BOTH the tier-1 (ensemble.json present) and tier-2 (env-driven, json absent) branches; refuses NON-live boots whose resolved DSN targets `ensemble_prod` with exit 78 + marker + fail-closed; sanctioned live/promote carve-out. This closes the "copied-ensemble.json residual" hole (prior lane B §6.3 / risk 12) AND the env-wins override hole: `EnsembleConfig.get_postgres_url()` (`daemon/ensemble_config.py:114-127`) lets ambient `POSTGRES_*` override the json's values, so today a tier-1 json with a scratch DB name + ambient `POSTGRES_DB=ensemble_prod` resolves to the PROD DSN unchallenged.

| Pin | Positive shape asserted | Counter-mutant that reddens it | Non-vacuity control |
|---|---|---|---|
| **BF-1 tier-1 name refusal** | `ensemble.json` present, postgres-shaped, resolved DSN db-name == prod name ⇒ `SystemExit(78)` + marker line naming tier=tier-1 + the prod-name predicate; fires PRE-TCP: `create_engine` sentinel NOT called (pattern of existing `test_guard_does_not_call_create_engine`) | **BF-M1**: delete the tier-1 prod-name branch ⇒ guard returns ⇒ engine constructed ⇒ sentinel called ⇒ RED | Control: tier-1 json with SCRATCH db name ⇒ no refusal ⇒ green |
| **BF-2 sanctioned carve-out non-refusal** | Live/promote-shaped input (prod-name DSN + the B-worker's carve-out evidence, e.g. install-provenance field or explicit var — NAME PER B-WORKER, pin asserts the chosen mechanism) ⇒ NO refusal, proceeds | **BF-M2**: invert/remove the carve-out check ⇒ live-shape input refuses ⇒ RED | Control: non-carve-out identical input ⇒ refuses (this is BF-1) |
| **BF-3 env-wins closure** | Tier-1 scratch-name json + ambient `POSTGRES_DB=ensemble_prod` (env WINS in `get_postgres_url`) ⇒ resolved DSN carries prod name ⇒ REFUSE. The pin resolves through the REAL `get_postgres_url` path (or the same env-precedence logic), never the json's static field | **BF-M3**: predicate reads the json's static `db` instead of the resolved DSN ⇒ sees scratch name ⇒ no refuse ⇒ RED | Positive control: same shape with ambient `POSTGRES_DB=ens_e2e_scrub_probe` ⇒ NO refusal AND the resolved URL carries the scratch name ⇒ proves the env-override mechanism itself still works for sanctioned names (credential-rotation flows preserved, risk R2 below) |
| **BF-4 ALLOW-knob hardening** | The tier-1 fence's sanctioned mechanism honors the allow var EXACTLY per the B-worker's chosen inheritability semantics (ambient-inherited `ENSEMBLE_DB_AMBIENT_ALLOW` NOT honored for tier-1, or honored only when explicitly forwarded) — pin asserts the CHOSEN polarity either way, character-for-character against the plan body | **BF-M4**: flip the knob's inheritability polarity ⇒ one direction of the pin reddens ⇒ RED | Control: the OTHER polarity leg stays green |
| **BF-5 auth/cli path closure** | The `daemon/auth/cli.py:150` exemption (verified: `create_postgres_engine(ensemble_config)` called there with NO guard, docstring "Documented exemption") is CLOSED — either the guard runs on that path (behavioral pin: prod-name input through the cli factory ⇒ exit 78) or, if the exemption survives by leader decision, a structural pin asserts the exemption is EXPLICIT (comment + reason), not accidental | **BF-M5**: remove the (new) guard call / exemption annotation ⇒ RED | Control: sanctioned input through the same path proceeds |
| **BF-6 fail-closed internal error** | Guard-internal error inside the NEW tier-1 name-resolution branch (e.g. malformed json DSN field) ⇒ exit 78 + loud log, never proceed (extends existing `test_guard_internal_error_fails_CLOSED_exit_78` to the new branch) | **BF-M6**: wrap the new branch in try/except-return ⇒ internal error proceeds ⇒ RED | Control: well-formed scratch input proceeds |
| **BF-7 predicate-string pin** | `inspect.getsource` asserts the new prod-name predicate literals + the resolved-DSN call, character-for-character (extends existing `test_predicate_strings_present`; POK `plan-body-ledger-agreement`: the strings here MUST match the B2 component plan's predicate strings character-for-character — diff at review) | **BF-M7**: any pinned literal drifts ⇒ RED | — |
| **BF-8 existing-pin flip** | The existing `test_guard_does_not_fire_tier1` pins a SQLITE tier-1 json (`'{"database":"sqlite"}'`) — it survives UNCHANGED (sqlite has no DSN). The flip is SEMANTIC and lands as a NEW test: postgres-shaped tier-1 with prod name now refuses (BF-1) while the same tier-1 with scratch name still passes. Both asserted side-by-side so the flip cannot silently regress either direction | **BF-M8**: reintroduce the old unconditional tier-1 return ⇒ BF-1 reddens ⇒ RED | The existing sqlite test stays green throughout |

### Family AH — A-residual socket-holder identity (new `tests/unit/test_port_holder_identity.py`; gate-level pins extend `test_port_ownership_pins.py` 18 → ~24)

The commissioned gate upgrade: port gate learns `/proc/net/tcp`(+tcp6) → inode → `/proc/*/fd` → pid → exe/cwd holder attribution; positive proof of a live FOREIGN holder ⇒ exit 78 + holder marker REGARDLESS of claim; degrade-never-refuse on unverifiable; fast-refuse decision per the A-worker's chosen shape; promote-shape same-install non-refuse PRESERVED. New module assumed at `daemon/services/port_holder.py` (exact path per A2 sibling — interface anchor).

| Pin | Positive shape asserted | Counter-mutant that reddens it | Non-vacuity control |
|---|---|---|---|
| **AH-1 foreign-holder-without-claim refusal** | A REAL scratch holder process (python subprocess binding a `free_port()` socket, cwd = scratch install dir) + NO claim file ⇒ /proc walk attributes it ⇒ `try_claim`/gate refuses exit 78 + holder marker (pid/exe/cwd/install/port present) | **AH-M1**: make the inode→pid walk return None (stub the attribution) ⇒ no refusal ⇒ boot proceeds ⇒ RED | Control: same holder on the SELF install-dir cwd ⇒ non-refuse (AH-5) |
| **AH-2 holder marker contents** | The refusal marker line + durable refusal record carry pid, exe, cwd, install_dir, port as POSITIVE content assertions (regex/field checks, not absence) — extends the existing WARNING line shape at `port_ownership.py:605-623` with the holder-walk fields | **AH-M2**: drop any one field from the marker template ⇒ that pin reddens ⇒ RED | Control: well-formed marker leg green |
| **AH-3 degrade-never-refuse, direction 1** | Holder walk UNVERIFIABLE (unreadable `/proc/net/tcp` fixture) ⇒ NO refusal; loud WARNING; boot proceeds (extend the E-ii M5 degrade pattern to the holder path) | **AH-M3**: make unverifiable ⇒ refuse (degrade→refuse flip) ⇒ RED | Control: verifiable-foreign leg refuses (AH-1) — proves degrade is not always-refuse |
| **AH-4 degrade-never-refuse, direction 2 (holder-only positive proof)** | Claim file ABSENT or STALE (dead pid) + holder walk POSITIVE-foreign ⇒ refuses via HOLDER evidence alone (the new additive path — today a stale claim means take-over) | **AH-M4**: gate the refusal on claim-presence only (revert to claim-exclusive) ⇒ AH-4 reddens ⇒ RED | Control: no holder + stale claim ⇒ take-over proceeds (existing E-ii M2 semantics preserved) |
| **AH-5 same-install hand-over non-refuse PRESERVED** | Holder with cwd == self install_dir + live pid ⇒ NO refusal (readyz proceeds; exit 0) — the promote-shape pin RE-AIMED at the holder path | **AH-M5**: treat same-install holder as foreign ⇒ refuses ⇒ RED (the AH analogue of P-M6) | Control: foreign-cwd holder refuses (AH-1) |
| **AH-6 fast-refuse semantics** | Per the A-worker's CHOSEN shape, ONE of: **(6a) immediate-78** — gate exits 78 pre-bind; pin extends `test_gate_refusal_never_enters_bind_retry` (holder refusal never enters the retry loop); **(6b) after-retry-ladder** — holder refusal fires within/after the bind-retry budget with marker; pin asserts the ladder-window behavior. BOTH variants specified here so E2 does not block on the sibling; the landed variant's pin ships, the other is deleted WITH a matrix note | **AH-M6**: rewire the chosen shape to the rejected one (immediate↔ladder) ⇒ the corresponding pin reddens ⇒ RED | Control: sanctioned boot still binds within budget |
| **AH-7 pre-TCP invariant (holder path)** | The holder refusal fires BEFORE any bind/listen attempt by the daemon-under-test — asserted structurally (gate ordering, pattern of `test_gate_refusal_never_enters_bind_retry`) + behaviorally (holder port never sees a second bind) | **AH-M7**: move the holder check after bind ⇒ RED | Control: happy path binds normally |

Pid-recycle discipline inherited: holder attribution pins use the existing `pid_alive_with_starttime` verification (`port_ownership.py:297`) — an AH pin asserts a mismatched-starttime holder is treated as stale (degrade), reddening under **AH-M8** (remove the starttime check on the holder path — the AH analogue of P-M2).

### Family HY — A1-hygiene (extends `test_pkill_overlay_guard.py` 13 → ~16, `test_pkill_overlay_pins.py` 10 → ~13)

Commissioned riders: port token **7979** into the bash.py overlay predicate; proc_tools mirror; refusal-text drift fix. Verified anchors: `daemon/tools/bash.py:2044` `for port_num, port_pat in (("9797", r"\b9797\b"), ("9798", r"\b9798\b"))` — 7979 ABSENT ✓; the bash refusal text at `bash.py:1350-1360` advertises `set ENSEMBLE_FENCED_TEST_CONTEXT=1 in the tool env (env={"ENSEMBLE_FENCED_TEST_CONTEXT": "1"})` — but `test_env_overlay_fence_is_IGNORED_B6` pins the overlay channel as IGNORED, so the advertised remedy is DEAD ✓ (real drift); `proc_tools.py:2587` DELEGATES to the bash predicate (`_pkill_overlay_violation(command, None)`) and its own text at `:2602-2610` correctly says "in the daemon environment" only ✓; **no test pins the refusal TEXT today** (grep verified: only source files match) ⇒ NO pin flip needed — the text fix lands with a NEW text pin.

| Pin | Positive shape asserted | Counter-mutant | Control |
|---|---|---|---|
| **HY-1 7979 token refusal** | `_pkill_overlay_violation("fuser -k 7979/tcp") == "7979"`; whole-word negative `"19799"` passes; fence-ambient still allows it | **HY-M1**: remove the `("7979", r"\b7979\b")` tuple ⇒ RED | non-port kill commands stay green |
| **HY-2 proc-lane coverage (delegation mirror)** | The proc lane refuses the 7979 token THROUGH THE DELEGATION (proc_tools imports the bash predicate — structural assert on the import + behavioral assert on the shared function; no duplicated predicate to drift) | **HY-M2**: break the delegation (proc predicate stops seeing bash tokens) ⇒ RED | proc scratch-sentinel commands pass |
| **HY-3 bash refusal-text correctness** | The bash refusal string NO LONGER advertises the dead env-overlay remedy; it names the AMBIENT channel only ("in the daemon environment"), matching the B6-pinned semantics — a text-content pin (substring asserts both ways: forbidden phrase absent, ambient phrase present) | **HY-M3**: restore the dead `env={...}` advertisement ⇒ RED | — |
| **HY-4 proc refusal-text stays ambient-only** | The proc text (`proc_tools.py:2602-2610`) asserts ambient-only remedy (already correct; pinned to prevent drift) | **HY-M4**: add an overlay remedy to proc text ⇒ RED | — |

**Mutation-matrix artifacts:** extend BOTH existing files — `tests/bind_race_rogue_containment_mutation_matrix.md` gains the BF-*/HY-* mutant rows (B-scoped files: `ensemble_config.py`, `bash.py`, `proc_tools.py`), `tests/bind_race_port_ownership_mutation_matrix.md` gains AH-* rows (`port_ownership.py` + the new holder module). Each row: file/line edited, expected RED set, sha256 PRE==POST restore proof, venue discipline (scratch worktree, `PYTHONPATH=<venue>` + `daemon.__file__` verified inside the venue, per-var scrub, `--timeout=90`) — exactly the existing format.

---

## § FENCED MIRROR EXTENSION (RED-leg safety — NON-NEGOTIABLE)

Harness read in full: `.agents/shared/planning/rogue-containment/harness/boot_rogue_mirror.sh` (105 lines). Already encoded: umask 077 (`:6`), FAKE_HOME + 2-stage escape guard (`:9-13`), per-variable PG scrub incl. DATABASE_URL/PG* (`:16-17`), explicit sweep pin (`:19`), PG_TEST_HOST-required scratch PG (`:23-26`), scratch PORT/HOST/DAEMON_PORT (`:29-31`), PG_TEST→POSTGRES translation + forbidden-name guard (`:34-52`), scratch ENSEMBLE_DATA_DIR + SELF_ENV=sandbox (`:43-46`), legs refuse-foreign/db-poison/happy (`:59-83`), TERM-only teardown (`:94`), redacted capture (`:100-101`).

**Harness gaps this lane CLOSES (extension requirements):**

1. **Unconditional `exit 0` (`:105`)** — no leg can FAIL the harness today. Extension: per-leg expected-exit code + marker-assertion block; mismatch ⇒ harness exits 1 with the leg name. Legs get: `refuse-foreign → 78 + PORT OWNERSHIP REFUSED marker`; `db-poison → 78 + DB RESOLUTION REFUSED marker`; `happy → readyz 200 + exit 0`; plus the three new legs below.
2. **Daemon-under-test provenance is unpinned** — `ROGUE_MIRROR_CMD` default (`:55`) uses the canonical checkout's venv interpreter with `-m daemon`, which resolves `daemon` CWD-dependently. Extension: harness asserts `python -c "import daemon; print(daemon.__file__)"` resolves INSIDE THE WORKTREE before any leg (the mutation-matrix venue discipline), else FATAL 99. Interpreter from `/home/nea/ensemble-src/.venv` is acceptable as a binary; the MODULE must be the worktree's.
3. **FAKE_HOME guard is 2-of-4 stages** (`tests/upgrade_test_lib.sh:69-102` has 4): add (a) export-took assert (`[ "$HOME" = "$FAKE_HOME" ]`) and (b) resolved live-install parent under /tmp assert.
4. **§7.6 rider — harness-local scratch config template with a LITERAL port.** Verified `config.yaml:113` = `port: ${PORT:-8088}` — the repo-root file interpolates ambient PORT, so an `ENSEMBLE_CONFIG` pointing at a repo-root copy is NOT a real fence. The harness ships `harness/scratch-config.yaml` (or `tests/support/`) with literal `port: 18790`-class values and exports `ENSEMBLE_CONFIG=<scratch>`; the template is GENERATED into the scratch dir with the leg's actual port substituted (a template with one literal baked is brittle across re-rolls). **The repo-root `config.yaml` is NOT touched** — the PORT-interpolating dev lanes depend on the interpolation (analysis §7.6 disposition; prior component-E §7.6 concurs). If a reviewer still wants the repo-root file literalized: leader decision point, default NO.
5. **readyz wait** — the fixed `sleep 8` (`:90`) becomes a bounded readyz poll (budget ~45s) so slow scratch PG boots don't false-red the happy leg.

**New legs (each: expected exit + marker asserts + live-untouched invariant):**

- **(a) `tier1-name-refusal`** — scratch `ensemble.json` (postgres-shaped) whose RESOLVED DSN carries the prod name in a shape the predicate catches: scratch-generated json with `db: "ensemble_prod"` and `POSTGRES_HOST` pointing at a scratch host:scratch-CLOSED-port. **INVARIANT (loud): the refusal must fire PRE-TCP — the prod DB name exists only as a STRING in scratch config; `POSTGRES_HOST` never points at any real host; no packet can reach a live `ensemble_prod` even if the guard is mutated red, because the host is a closed scratch port.** Expected: exit 78 + marker naming tier-1/prod-name; zero TCP (discriminated by the exit code itself — a guard-miss boot would die on connection-refused with a NON-78 code, which the harness treats as leg FAILURE, not pass).
- **(b) `foreign-holder`** — plant a scratch python socket-holder (binds the leg's scratch port, cwd = a scratch install dir) → boot → expected exit 78 + holder marker line asserting pid/exe/cwd tokens present; teardown TERMs the holder (verify it exits; never SIGKILL). Lane-ordering: this leg lands WITH the A2 holder code — before that, the leg is not registered (the harness leg-selector refuses unknown legs loudly rather than silently skipping).
- **(c) `same-install-handover`** — holder whose cwd == the daemon-under-test's own resolved install dir → boot → expected NON-refuse: readyz 200, exit 0 on TERM, no refusal marker; claim taken/overwritten. Preserve pin for the promote shape.

**Live-untouched invariant (every leg, asserted by the harness before AND after):** ports 9797/9798/7979 unbound-or-unchanged (port probes); DB name never `ensemble_prod`/`ensemble_dev`/`ensemble_prod_test` (existing `:52` guard retained); `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned explicitly (never `env -u` on it); umask 077; SIGTERM-only teardown; captures redacted (name + sha256 prefix only, F7).

---

## § SANCTIONED-BOOT ACCEPTANCE (the non-negotiable gate: guards must not break house lanes)

Re-derived from the prior component-E § SANCTIONED-LANE ENUMERATION — read in full this session; its 22 listed rows collapse to **16 distinct lanes** (rows 12/14/18/20/21/22 were duplicates of 3/7/8/10/9/6). The 16: (1) live boot + promote hand-over window; (2) promote/restart/rollback chain; (3) `dev.sh` on 8079; (4) pytest transient-unit boots (`wedge_smoke.py`); (5) tester staged-binary scratch mirror (`boot_v2.sh`-shaped); (6) DR-2 exit-78 drill (`docs/runbooks/upgrade-drills.md:164-191`, PORT 8377); (7) `scripts/test_builtin_mcp_servers.sh` (18088); (8) shell packs sourcing launcher.sh (`run_tests_scrubbed.sh`); (9) soak/pack fences (`dbpool_gate_ladder.sh`, soak packs); (10) live boot tier-1; (11) stage.sh no-boot; (12) manual PG smoke 8199; (13) deploy.sh; (14) Makefile install; (15) frozen entry points (`run_app.py` + `ensemble.spec`); (16) interpreter-certification pack fences.

**Acceptance criteria per the E-iii shape, re-asserted under BOTH new fences:**

- **Scratch-port shape:** scratch port (18790 or `free_port()` re-roll — never 9797/7979/7979-adjacent whole-words), claim absent ⇒ take claim ⇒ bind succeeds ⇒ readyz 200 within budget ⇒ claim cleaned on graceful shutdown; no refusal marker.
- **Scratch-PG-name shape:** scratch DB `ens_e2e_scrub_probe` NEVER trips the prod predicate — in tier-2 (env-driven) AND tier-1 (scratch json postgres-shaped with the scratch name) variants. This is BF-3's positive control executed end-to-end.
- **Scratch-config shape:** literal-port scratch config via the §7.6 harness template (ambient PORT cannot leak through interpolation).
- **Holder-shape:** the A-holder gate must not misattribute scratch holders on ANY of the 16 lanes — the decisive rows re-affirmed: (3) dev.sh install_dir=None ⇒ non-foreign (D6); (4) transient boots source-resolve None ⇒ non-foreign; (5) mirror sandbox self-consistent; (15) frozen path derivation resolves staged→live install root ⇒ same-install ⇒ non-refuse (promote hand-over preserved — prior risk 6, critical).
- **Named E2E acceptance lane (carried):** the tester-style staged-binary scratch mirror boot during staged verification — the only lane that boots the ACTUAL frozen artifact and exercises take-claim + the frozen path derivation. This lane's S5 runs the mirror legs AGAINST the staged artifact where the artifact boundary allows (the harness tier-1-name leg and same-install leg are pure env/file shapes; the foreign-holder leg plants a real process and is safe against the staged binary).

**Per-lane trip-risk verdicts from the prior E carry over UNCHANGED except where a new fence could plausibly trip:** row 1/2/15 (promote chain + frozen entries) re-verified under BF-2 (carve-out) and AH-5 (same-install holder); row 10 (live tier-1 boot) re-verified under BF-1/BF-2 — a live tier-1 json with prod-name DSN must NOT refuse; row 6 (DR-2 drill) — its deliberate exit-78 comes from CONFIG refusal AFTER a successful claim/bind; verify the new fences don't pre-empt it with a DIFFERENT 78 (marker text discriminates). Developer task list: one trip-risk re-affirmation line per lane, receipt-cited.

---

## § D MEASUREMENT (ADJUDICATION ARMING)

**Purpose:** arm or retire the analysis §5.4 burst-window trigger with DATA (hole H2: post-Lane-1 U inferred ~30–39s, never measured; observed incident U was 87–95s ⇒ the aged-window reset fired 13×, abort unreachable).

**Constants (verified this session):** `launcher.sh:57-59` — `BUDGET_MAX_CRASHES=5`, `BUDGET_WINDOW_S=600`, `BUDGET_UPTIME_RESET_S=600`; crash backoff ladder 10+20+40+80+160 = 310s ⇒ abort requires **U ≤ 58s** (310 + 5U ≤ 600); `bind_retry.py:167` `BIND_RETRY_MAX_WAIT_S = 30.0` (the in-code ceiling preserving the constraint); bind-retry exhaustion exits **1** (crash track — verified `bind_retry.py:88-91` and module docstring), so the measurable legacy per-cycle exit is 1.

**Tester-executed harness measurement (fenced mirror):**

1. **Loop shape:** scratch port held by a scratch holder (the mirror's own plant pattern); daemon boots AGAINST the held port, N ≥ 5 cycles; per-cycle **U = wall-clock child start → exit** (`date +%s%N` around the child).
2. **Knob/leg ordering (load-bearing):** the daemon must boot on the LEGACY crash path — the A2 fast-refuse OFF (via its knob, exact name per A2 sibling, e.g. `ENSEMBLE_PORT_FAST_REFUSE=0`, OR the leg runs on the pre-A2 tip). **A per-cycle exit of 78 INVALIDATES the leg** (fast-refuse leaked on — the harness records it as leg failure, never as data). Expected legacy exit per cycle: 1.
3. **Invoked DIRECTLY (`python -m daemon`), never via launcher.sh** — we measure the DAEMON's U; the launcher's own budget logic is the thing being adjudicated and must not contaminate the loop. Full fence set on every cycle (the harness env block).
4. **Record:** per-cycle U (ms), exit code, cycle count, holder port, the fence digest; RESULTS file at `.agents/tester/RESULTS/<YYYY-MM-DD>-rogue-p2-burst-window-U-measurement.md` (house convention) with the raw table + the verdict line.
5. **Verdict rule (the arming predicate, restated verbatim from analysis §5.4):** measured median U **> 58s** ⇒ trigger FIRES — promote the sliding-window burst fix to its own staged lane (launcher.sh + manifest `launcher_sha256` + twin pins). Measured median U **≤ 58s** ⇒ trigger DORMANT. Either way **NO launcher.sh edits in THIS lane.**

**Architect input frame (the measurement doubles as this evidence base):** if the A2 fast-refuse lands (exit 78 + unit `RestartPreventExitStatus=78` at `scripts/systemd/ensemble-daemon.service:110:110`, verified; `classify_exit` 78→refuse at `launcher.sh:3588-3592`, verified — the analysis cited :3555-3562, drifted ~30 lines at current tip, same function), then a port-squat boot REFUSES instead of crash-looping, and systemd will not restart on 78 — **the port-squat crash-loop class cannot form via systemd restart**, which may OBSOLETE the launcher sliding-window fix outright. The evaluation (obsoletes vs still-needed for non-systemd lanes) is the architect's, powered by: (a) the measured U, (b) whether every realistic squat path now terminates in 78, (c) the launcher's remaining exposure (exit-1 crash loops from OTHER causes still exist — bind-retry exhaustion exits 1). This lane provides evidence and routing, NOT the decision.

**Constraint carried (prior E risk 18, critical):** A2's gate must not lengthen the failed-boot path U past 58s — the measurement leg verifies the POST-A2 refused-boot U too (one extra cycle with fast-refuse ON: refusal should be near-instant, U ≪ 58s — this cycle is the direct evidence that Lane-1's constraint survived the new gate).

---

## § CHANGELOG CATCH-UP

**Verified state (read this session):** Keep-a-Changelog 1.1.0; headings `## [X.Y.Z] — YYYY-MM-DD` with `### Added|Changed|Fixed — <lane summary> (\`feature/branch\`; commits/merge refs)` + bold-led bullets; exemplar `[0.29.19]` bind-race entry at `CHANGELOG.md:119-126` ✓ (its commit `f3214eb7a` is already documented there — NOT part of the catch-up). **Containment silence verified:** grep for claim-before-bind/port-ownership/overlay guard/DB-resolution/rogue across CHANGELOG.md ⇒ ZERO hits. **Gap is WIDER than the commission stated:** headings jump `[0.29.21]` → `[0.30.2]` — `[0.29.22]`, `[0.29.23]`, `[0.30.0]`, `[0.30.1]` headings are ALL absent (verified by heading census), while the git tags exist. Tag annotations (verified): `v0.29.22` = "rogue-process containment: port-ownership pkill guard + spawn-boundary env scrub + rogue-write hygiene; zero migrations" — **THE containment ship version**; `v0.29.23`/`v0.30.0`/`v0.30.1` = sonic lanes. All 7 containment commits verified in-history and `git tag --contains` ⇒ first contained by **v0.29.22**: `5c7c83cd9` (seam scrub bash+service_spawner), `67b3b86e5` (overlay word-boundary + channels), `97f3181dd` (N9 proc import fail-open), `f5d4076be` (claim-before-bind + marker + lifespan release), `5261254a0` (claims-dir override + pytest fence), `5d9fbf8b2` (B1 host-global claims + B2 proc scrub), `1ec2afb1b` (rider-(d) metrics derive + managed-key retirement + DB-resolution guard).

**Decision — consolidated catch-up subsection (RECOMMENDED), not per-version backfill.** Rationale: per-version backfill requires AUTHORING four missing version headings whose non-containment content (sonic lanes) would have to be reconstructed — invention risk directly against the "flag, don't invent" instruction; the absence is a systemic documentation gap for the LEADER to rule on, not for this lane to silently paper over. The consolidated form: under this lane's own release heading, a `### Fixed — rogue-containment catch-up (retro-record; shipped in v0.29.22, undocumented)` subsection, clearly marked catch-up, dated at THIS lane's release, naming v0.29.22 as where each shipped, with the 7 commit refs grouped by mechanism (claim-before-bind / overlay guard / seam scrub / metrics derive + DB-resolution guard). Plus the **new-work entry template** for this lane's own delta (B2 fence + A2 holder + A1 hygiene), following the exemplar shape exactly: `### Added|Fixed — <one-line outcome> (\`feature/rogue-port-containment\`; <commit/merge refs>)` + bold-led bullets per mechanism + zero-migration/rollback statement (both new fences are additive logic ⇒ expected `rollback_safe=true source=explicit (fix-loss-only)`; any schema need = STOP + flag, per prior E sequencing).

**Flag to leader (do not fix here):** the four missing headings `[0.29.22]`–`[0.30.1]`; whether to backfill them (and from what evidence) is the leader's call. This lane's catch-up references v0.29.22 BY TAG so the record survives regardless of whether headings are ever backfilled.

---

## § S-GATES (this lane's explicit S1..S9 — prior lane defined none)

| # | Gate | Pass criterion | Executor |
|---|---|---|---|
| **S1** | sha 4-way equality | release dir == dist == manifest == sidecar, sha256-identical binary + sidecar witness recorded | tester |
| **S2** | CArchive duplicate-prefix census | full-prefix `uniq -d` ⇒ empty (no duplicate members) | tester |
| **S3** | PROMPT-BASELINE | both trees match the DECLARED baseline count at stage time — **NOT a hardcoded number**: the prior lane's "181/181" is STALE (current denominator **217** per CHANGELOG [0.30.7]/[0.30.8]); read the declared count from the release payload/state and assert tree==tree==declared | tester |
| **S4** | measured interpreter | measured interpreter recorded in the verification doc (expected cpython-3.14 class; record what is measured) | tester |
| **S5** | frozen-bundle boot smoke + logging-path re-assertion | staged artifact boots in the fenced mirror (happy leg) AND a cheap evidence-based re-assertion that `install_root_logging()` imports resolve on the frozen `run_app → main(run_preflight=False) → _serve_with_bind_retry` path (analysis §9 item 6 — satisfiable-by-evidence since v0.29.19, live through multiple promotes; re-asserted because this lane touches boot again): the happy-leg log shows the tagged root-handler output in the file log | tester |
| **S6** | mutation matrices green | ALL families' legs RED under their named counter-mutants (BF-M1..M8, AH-M1..M8, HY-M1..M4) with sha256-verified restores + clean-tree control green; matrices updated in both artifacts | tester (execution) / developer (authoring) |
| **S7** | fenced-mirror legs green | all legs (existing refuse-foreign/db-poison/happy + new tier1-name-refusal/foreign-holder/same-install-handover) pass their expected-exit + marker assertions; harness provenance assert (daemon.__file__ in worktree) passes; live-untouched invariants verified pre+post | tester |
| **S8** | sanctioned scratch-boot acceptance green | the 16-lane census trip-risk re-affirmations recorded + the named E2E lane (staged-binary mirror) reaches readyz 200 with claim take + clean shutdown; scratch names never trip the prod predicate | tester (+ developer for unit-level lanes) |
| **S9** | (D) measurement recorded | RESULTS file exists at the conventioned path with N ≥ 5 cycles, per-cycle U + exit codes, the verdict line (U > 58s ⇒ trigger fires / ≤ 58s ⇒ dormant), and the fast-refuse-ON single-cycle U evidence | tester |

---

## § SEQUENCING

1. **Parallel code phases:** B2 prod-DSN fence (`ensemble_config.py` + preflight sites + auth/cli decision) ∥ A2 holder identity (`port_holder.py` + gate integration + fast-refuse knob) ∥ A1 hygiene (7979 token + refusal text) — independent files; A1 is tiny and can ride either phase. Constraint: A2 and B2 both touch boot preflight ordering in `daemon/__main__.py` — the merge order must keep `install_root_logging()` first and the DB preflight/port-gate ordering explicit (prior E sequencing step 3 pattern).
2. **Pins land WITH each code phase:** BF-* with B2; AH-* + HY-* with A2/A1. No pin-only-after-the-fact batch.
3. **Mutation-matrix artifacts updated** (both files; venue discipline; sha restores) — after pins.
4. **Fenced-mirror harness extension** (expected-exit plumbing, provenance assert, FAKE_HOME 4-stage, scratch-config template, new legs a/b/c) — legs (b) gated on A2 landing.
5. **(D) measurement** — MUST run with fast-refuse OFF (knob or pre-A2 tip): schedule the measurement leg BEFORE enabling fast-refuse by default, or parameterize the leg; either way the S9 leg-degeneracy rule (exit 78 = invalid) enforces the ordering at runtime.
6. **Sanctioned-boot acceptance run** (16-lane re-affirmation + E2E lane).
7. **CHANGELOG catch-up + new-work entry** (consolidated shape per § CHANGELOG).
8. **Reviewer** (plan-body/ledger predicate agreement: BF-7 strings vs the B2 plan body, character-for-character).
9. **Tester gates S1–S9** (S1–S5 against the staged artifact at stage time).
10. **Stage cut** (giter; version slot from the LIVE census at stage time — do not assume; refs floor = current latest at stage time).
11. **NO PROMOTE** — the ceremony is the Emperor's.

**Role split:** developer = code + pins + matrix rows + trip-risk re-affirmations; tester = mirror legs, measurement, S1–S9 execution; giter = stage cut; reviewer between 7 and 9.

---

## § FOOT-GUNS (bake into developer/tester tasks)

1. **Never 9797/9798/7979** — scratch ports via `free_port()` re-roll (`tests/support/wedge_smoke.py:102-103`) or explicit 18790-class.
2. **`ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned EXPLICITLY** — never `env -u` on it (2026-10-31 sweep-killed-live incident); harness `:19` already correct, keep it.
3. **FAKE_HOME fence, 4-stage** — harness upgraded from 2-stage to the full `tests/upgrade_test_lib.sh:69-102` guard.
4. **Per-variable PG scrub** — `env -u POSTGRES_*` is a silent no-op (bash does not glob env names); harness `:16-17` already per-variable, keep it; any NEW script leg copies the block verbatim.
5. **umask 077** (`:6`); F7 redacted captures (name + sha256 prefix only).
6. **No live mutation; no promote; no launcher.sh edits (either way — the (D) verdict routes, never edits); no repo-root config.yaml change; no pkill** (the guard itself refuses it — fenced legs use TERM on specific PIDs they own).
7. **No bare `npx`/`tsc` outside `frontend/`**; **no bare `python` in the worktree** (no `.venv` there — use the canonical venv binary as interpreter with the worktree as module root, provenance-asserted).
8. **Mirror legs never resolve the real `ensemble_prod`** — prod name appears only as a scratch STRING; tier-1-name leg's `POSTGRES_HOST` is a closed scratch port (pre-TCP invariant).
9. **Conftest session fence** (`tests/conftest.py:912-919`) — every claims-dir pin passes its own tmp dir; never rely on ambient claims resolution under pytest.
10. **Plan-body/ledger predicate agreement** (POK) — BF-7 pinned strings match the B2 component plan character-for-character; diff at review; the body wins.
11. **Daemon-under-test provenance** — harness asserts `daemon.__file__` resolves in the worktree before any leg (module-identity foot-gun; the canonical checkout is read-FORBIDDEN for writes and never the code under test).
12. **Frozen/staged smokes bind the LIVE install root via path derivation** (prior E foot-gun 10) — planted claims/holders use explicit install_dir/cwd values; never rely on "scratch" resolving to a scratch install root.

---

## § RISKS + LEADER DECISION POINTS

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| 1 | BF-2 carve-out misdetection refuses the LIVE boot at promote (live json carries prod-name DSN) | critical | low | Carve-out pinned (BF-2) + trip-risk rows 1/2/10/15 re-affirmed + S5 staged-artifact boot before any ceremony |
| 2 | Env-wins closure breaks credential-rotation flows that legitimately rely on `POSTGRES_*` overriding json values | medium | medium | BF-3 positive control: scratch-name env override still RESOLVES and proceeds — the fence refuses the prod NAME only, never the override mechanism |
| 3 | Holder /proc walk races (pid recycle between inode read and fd scan) ⇒ stale attribution | medium | low | starttime verification on the holder path (AH-M8) + degrade-never-refuse (AH-3) |
| 4 | Fast-refuse default-ON before the (D) measurement runs ⇒ legacy path unmeasurable, S9 degenerate | medium | medium | Sequencing step 5 ordering + the leg-degeneracy rule (exit 78 = invalid leg) enforcing it at runtime |
| 5 | CHANGELOG catch-up drifts into invention (reconstructing missing headings' other content) | medium | low | Consolidated decision; v0.29.22 referenced BY TAG; missing-heading backfill routed to leader |
| 6 | New pins silently masked by the conftest claims fence or by pytest's tmp redirection ⇒ vacuous green | medium | low | Pins pass explicit tmp dirs (existing pattern) + counter-mutant legs prove sensitivity (mutation-01 discipline) |
| 7 | A2 gate lengthens failed-boot U past 58s (Lane-1 constraint) | critical | low | The fast-refuse-ON measurement cycle gives direct U evidence; gate exits pre-bind (AH-7) |
| 8 | tier1-name-refusal leg accidentally contacts a REAL postgres (host misconfig) | high | low | POSTGRES_HOST hardcoded to a closed scratch port in the leg; harness pre+post live-untouched probes; pre-TCP invariant makes contact impossible even on guard-miss |

**Leader decision points (this file adds; do not re-derive sibling-owned ones):**

1. **CHANGELOG shape** — consolidated catch-up (recommended) vs per-version backfill of the four missing headings; and whether to backfill `[0.29.22]`–`[0.30.1]` at all.
2. **The "9798 separable rider" is STALE** — rider (d) metrics derive ALREADY SHIPPED in v0.29.22 (`1ec2afb1b`; `derive_metrics_port` at `daemon/services/metrics_listener.py:75`, wired `daemon/api.py:2736`). Confirm NO re-work in this lane unless the B2 worker surfaces a concrete residual gap.
3. **AH-6 fast-refuse shape** (immediate-78 vs after-retry-ladder) — A2 proposes, leader ratifies; both pin variants are specified above so E2 does not block.
4. **BF-2 carve-out mechanism** (install-provenance field in `ensemble.json` vs explicit env var) — B2 proposes, leader ratifies; BF-2/BF-4 pin whichever lands.
5. **(D) routing after measurement** — if U > 58s: burst-window promoted to its own lane per §5.4; if fast-refuse is judged to obsolete the launcher fix: the trigger may be RETIRED with the measurement as evidence. The decision (and any future launcher lane) is the leader/architect's; this lane records, routes, never edits `launcher.sh`.

---

## § CONTRADICTIONS (commission/inputs vs verified reality — verified reality wins, disclosed)

1. **Commission:** "NO [0.30.0]/[0.30.1] headings exist" — TRUE but INCOMPLETE: `[0.29.22]` and `[0.29.23]` headings are also missing (heading census verified). The catch-up anchor version (v0.29.22) is itself among the missing.
2. **Commission:** "possible separable rider = 9798 derive-from-main-port fence" — ALREADY SHIPPED (v0.29.22, commit `1ec2afb1b`, `derive_metrics_port` live). Nothing to plan; flagged as leader decision point 2.
3. **Commission path** `tests/unit/test_main_entry.py` — actual location `tests/test_main_entry.py` (27 tests, count matches the commission's 27).
4. **Commission:** "~16 rows — re-derive from prior component-E" — the prior file lists 22 rows collapsing to 16 distinct (its own note); the 16 distinct carried above.
5. **Prior E S-gate expectation "PROMPT-BASELINE 181/181"** — STALE: current denominator 217 (CHANGELOG [0.30.7]/[0.30.8]). S3 asserts measured==declared-at-stage-time, never a hardcoded count.
6. **Commission:** "pin flip if test_pkill_overlay_pins.py pins the text" — verified it does NOT pin the text (pins target the `_pkill_overlay_violation` predicate only) ⇒ NO flip; the text fix lands with NEW pins HY-3/HY-4.
7. **Analysis line drift:** §5.5 cites `classify_exit` at `launcher.sh:3555-3562`; verified at `:3588-3592` at tip `bf4bfde6e` (same function, ~30-line shift) — cited at verified positions in this plan.

---

## § ASSUMPTIONS / OPEN QUESTIONS

- **Assumption:** B2's prod-name predicate matches the DB NAME (exact `ensemble_prod` or a name family) and its sanctioned carve-out is detectable from config/install-provenance — the exact predicate literals and knob names are B2's interface; BF-7 pins them character-for-character once landed (this plan's § E2 BF rows are written mechanism-first so only the literals need backfilling).
- **Assumption:** A2's holder module lands at `daemon/services/port_holder.py` with a fast-refuse knob; pins AH-1..AH-8 reference the gate at `run_port_ownership_gate` (`port_ownership.py:587`) regardless of the module path.
- **Assumption:** 7979 is a live-shaped port (commission + the wedge_smoke never-reroll list treat it as such); its provenance (which surface binds it) was NOT re-derived here.
- **Open question:** does the tier-1 fence extend to non-prod anomalies (foreign user/host in the resolved DSN), or strictly the prod DB name? Commission scope says prod-NAME — pinned that way; broader scope is leader's call.
- **Open question (carried from prior E):** per-port refusal-marker file vs single file — unaffected by this lane; carried.

---

## § CROSS-REFERENCES

- Governing analysis: `.agents/shared/planning/bind-race/rogue-port-containment-analysis.md` (§5.4, §5.5, §7, §8 H2, §9.1/9.6).
- Prior lane: `.agents/shared/planning/rogue-containment/component-E-regression-pins-and-sequencing.md` (+ component-A/B plans, plan-overview.md).
- Harness: `.agents/shared/planning/rogue-containment/harness/boot_rogue_mirror.sh` (extended by this lane; extension requirements in § FENCED MIRROR EXTENSION).
- Sibling (in flight, this directory): the B2 fence component plan and A2 holder component plan — interface anchors flagged in § ASSUMPTIONS.
- Pins/files: `tests/unit/test_db_resolution_guard.py` (10), `tests/unit/test_port_ownership.py` (26), `tests/unit/test_port_ownership_pins.py` (18), `tests/unit/test_pkill_overlay_guard.py` (13), `tests/unit/test_pkill_overlay_pins.py` (10), `tests/test_main_entry.py` (27), `tests/conftest.py:912-919`, `tests/support/wedge_smoke.py:102-103`.
- Matrices: `tests/bind_race_rogue_containment_mutation_matrix.md`, `tests/bind_race_port_ownership_mutation_matrix.md` (both extended with BF-/AH-/HY- rows).
- Code anchors: `daemon/ensemble_config.py:114-190` (get_postgres_url + guard), `daemon/services/port_ownership.py:587-644` (gate), `daemon/tools/bash.py:2044` + `:1350-1360`, `daemon/tools/proc_tools.py:2580-2610`, `daemon/auth/cli.py:150`, `daemon/__main__.py:440-470` (gate call site), `daemon/services/bind_retry.py:88-91,167`, `launcher.sh:57-59,3588-3592,3848`, `scripts/systemd/ensemble-daemon.service:108-110`, `config.yaml:113`, `daemon/services/metrics_listener.py:75`, `daemon/api.py:2736-2744`.
- CHANGELOG: exemplar `[0.29.19]` at `CHANGELOG.md:119-126`; heading census `[0.29.21]`→`[0.30.2]` gap; tag `v0.29.22` annotation (containment ship record).
- Exemplars: `tests/bind_retry_mutation_matrix.md` (mutation-artifact shape), `boot_v2.sh` (fenced-boot ancestor), `tests/upgrade_test_lib.sh:69-102` (FAKE_HOME 4-stage).

---

*End of component E2 plan. Design only — no code, no tests run, no repo writes except this file.*

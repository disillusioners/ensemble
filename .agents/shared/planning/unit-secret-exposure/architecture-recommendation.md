# unit-secret-exposure — Architecture Validation & Enrichment (architecture-recommendation)

Date: 2026-10-23 (overnight stage-only lane)
Architect instance: this lane's architect controller; analysis dispatched to 3 dimension workers (security-design, data-flow-design, resilience-design), all read-only against this worktree @ `9fc95176b8` (branch `fix/unit-secret-exposure`).
Input: the full plan set in this directory (plan-overview + phase1-5 + decisions D1-D11).
Secret hygiene honored: key names + value lengths only; no live unit / live `.env` content was read by any analyst.

> *Tidier re-anchor note (2026-10-24, quality pass @ `eb90a600c`): anchors below were verified by this review against the pre-strip base `9fc95176b8`. Adopt-unit / stage.sh anchors that describe the FINAL tree are re-pointed in place (post-strip positions), and the W2-fold `umask 077` fences are annotated at §3.1. The `RestartPreventExitStatus` template anchor (:97 → :100 per the W1 adjudication, decisions.md spot-check) sits at :110 post-strip. Base-time analysis anchors inside §2.2/§3.1 rows keep their plan-time positions; superseding records live in decisions.md (D5, D5c, W-folds).*

---

## 1. VERDICT: **APPROVE-WITH-AMENDMENTS**

The D1 strip ruling is **sound and correctly targeted**; the zero-env unit contract, the precedence model, and the stage-only lane posture all validate against the code. The amendments below are corrections to reasoning text, runbook mechanics, and scope gaps — **none reverses the design**. Two amendments are load-bearing for the morning remediation (**A1** — the drop-in reset form is wrong as planned and could silently fail its own verification gate; **A2** — the re-adoption citation points at the wrong section) and one for the stage step (**A9** — the rollback-adjudication input as briefed by the caller is factually wrong about the payload delta).

Confidence: **High**. The assumption that would flip A1's direction: if systemd 255 deduplicates repeated `Environment=` assignments at parse time into the manager's stored list (unverifiable read-only on this host — no observable instance exists). The amendment does not depend on that assumption — it selects the form that is provably correct under both behaviors.

---

## 2. Threat-model boundary statement

### 2.1 What the strip CLOSES

Exactly **one surface**: the manager's stored unit `Environment=` list, re-served over D-Bus (`org.freedesktop.systemd1` Unit `Environment` property; `systemctl show -p Environment`) to **any local uid** — the system bus socket is `srw-rw-rw-` (world-connectable) and property reads are unauthenticated; the unit file's `install -m 0600` (`adopt-unit.sh:556` post-strip) demonstrably does **not** gate this (the manager parses as root and re-serves the parsed list). Man 5 systemd.exec:2703-2708 verified on this host (systemd 255.4): unit environment is "exposed to unprivileged clients via D-Bus IPC … not suitable for passing secrets."

- Access class closed: **local, non-`nea`, non-root users** — for whom this was the **only** reachable secret surface on the host (see 2.2).
- Temporal boundary: **future generations only.** The live Sep-30 unit is a frozen write-once snapshot (clobber refusal `adopt-unit.sh:518-521` exit 78 post-strip; never re-rendered by promote/restart/rollback) — the live exposure persists until the user's morning remediation (D10.1).
- Post-strip the unit's stored env = `Environment=MALLOC_ARENA_MAX=2` only (template :110) — no secret.

### 2.2 What the strip does NOT close (each verified, with the honest access class)

| # | Surface | Readable by | Strip/0600 effect |
|---|---------|-------------|-------------------|
| 1 | Install `.env` (`/home/nea/agents-ensemble/.env`) — 644, 46 keys incl. `POSTGRES_PASSWORD` (len 29), `OPENAI_API_KEY` (len 67), `SYSTEM_ENCRYPTION_KEY` (len 44), 3× `*_EMBEDDING_API_KEY` (len 164) | **`nea`-uid + root only**, before AND after 0600. Shield = `/home/nea` mode **750** with `nea` group having **no supplementary members** (the install dir itself is 775 — one directory level from world traversal) | **Class unchanged.** 644→600 excludes **nobody** new |
| 2 | `/proc/<launcher|daemon>/environ` — launcher exports **every** `.env` key (`launcher.sh:186`; daemon child inherits at :1525) | **Owner + ancestors/descendants + root** (environ is 0400 owner-only; `ptrace_scope=1` further bars non-ancestor same-uid peers). Correction to the commission's premise: not broadly readable by arbitrary same-uid processes; the practical class (`nea`-uid or root) equals the `.env` file's | **Unchanged — not a regression, not closed.** The Fernet master key + API keys live here (broader than the unit ever carried) |
| 3 | `.env.bak-2026-10-20T21:30:40Z` (install dir) — **644** (commission said 10-10; actual **10-20**) | `nea`-uid + root | **Untouched by P5's three write sites** — residual stale 2nd copy of the full secret set |
| 4 | Repo copies: `.env.prod` 600, `.env.prod.demo` **644** (regenerated by `deploy.sh:245-249` `mv -f` under umask 022), `.env.backup` 600 | `nea`-uid + root | `.env.prod.demo` not covered by P5 *(superseded per D5c: the demo-guard generation site is now chmod'd + umask-fenced — FUTURE generations land 0600; the EXISTING 644 file remains morning item D10.7)* |
| 5 | Live `ensemble-main.service` until remediation | any local uid (D-Bus) | Closed only by the user's morning decision |

**Bottom line for the reviewer:** the strip + P5 move the needle for exactly one account class (local non-`nea`/non-root), and for that class the closure is total. P5 is **defense-in-depth hygiene** (re-homing insurance, umask-regression insurance, archive-copy hygiene) — it must **not** be presented as closing a threat-model surface (see A6).

### 2.3 Rejected-alternatives adjudication (right outcome; one reason to correct)

- **EnvironmentFile=** — **empirically, it WOULD also close the D-Bus exposure** (verified first-hand on this host, systemd 255.4: `ssh.service`/`cron.service`/`networkd-dispatcher.service` all show empty `Environment=` + populated `EnvironmentFiles=`; only literal `Environment=` directives appear in the property). The plan's rejection is therefore **not** right for the reason a reader might assume. D1's written reason "creates a SECOND secret file" is **inaccurate** (`EnvironmentFile=$INSTALL_DIR/.env` points at the same file the launcher reads). The genuinely load-bearing reasons — which the ledger must record (A7): (i) the D9 house invariant; (ii) **systemd's env-file parser diverges from `launcher.sh` `load_env_file`** (`:146-189`: one-layer quote stripping, `export ` prefix, comment handling vs man 5 systemd.exec:2715-2767) — a silent value-divergence risk between two parsers reading the same file; (iii) changed missing-`.env` failure mode (systemd refuses the unit start vs today's launcher WARN + boot-block).
- **LoadCredential=** — rejected for the right reasons (app-side `$CREDENTIALS_DIRECTORY` reshape; no in-house precedent — repo grep hits only this plan's own text; and re-exporting to env would re-create surface #2 anyway).
- **No cheaper mechanism exists** (door closed): systemd 255 has no per-variable hide-from-D-Bus directive; `UnsetEnvironment=` is exec-time only (man :2816-2818); restricting the system bus itself is host-wide and breaks non-root `systemctl` for everyone. **The strip is the cheapest closure.**

---

## 3. Load-bearing call scrutiny

### 3.1 (a) stage.sh chmod as the load-bearing 0600 mechanism — **VALIDATED with a mechanism correction**

- **Order-sensitivity (R6) confirmed:** the marker rewrite re-creates the `.env` inode on all three paths (`sed > tmp && mv -f` at `stage.sh:684-685` / `:688-689`; create `> "$ENV_FILE"` at `:691-696`); the temp file is created at default umask (no `umask` directive in stage.sh/deploy.sh), so `mv -f` **silently resets any prior 600**. chmod must follow each write — the plan's insertion points are correct.
- **Mechanism correction (A6):** `deploy.sh:311` `cp` to a **new** destination creates it with the **source's** mode masked by umask (not a fresh umask-derived mode); to an existing destination it leaves the mode untouched. The live install `.env` 644 **originates from the source** — `.env.prod` regenerated at `deploy.sh:245-249` via `mv -f` under umask 022 (and `.env.prod.demo` sits at 644). chmod at :311 is still correct; the plan's explanatory text is wrong and the source-side path is uncovered. *(Superseded per D5c/W2: the demo-guard generation site is now covered — `chmod 600` rides the `mv` chain and the block is umask-fenced, deploy.sh :251-269 post-fold.)*
- **Env-ownership ruling (D5a) confirmed, with a consequence sentence to add:** never `chown` before a `User=` flip is right; additionally, a future **root-owned** `.env` at 600 would make the next launcher start unable to read it (`launcher.sh:148-150`) and recreate the D4 missing-env family. Ownership==User is a precondition of the 600 posture.

### 3.2 (b) Drop-in remediation semantics — **the caveat framing is right; the reset FORM is wrong as planned**

- **Running-process vs manager-config split: CORRECT.** `daemon-reload` re-reads unit files into the manager's merged view (`systemctl show -p Environment` reflects it immediately); the running daemon's environ was copied at fork/exec and persists until next restart. Verified semantics, exactly the plan's framing.
- **"Dead weight" caveat: CONFIRMED.** The minted key-set is derived from `.env` itself (`adopt-unit.sh:305` seds PORT + `POSTGRES_*` names from the file — nothing else), so every unit-minted key was `.env`-defined and the launcher's unconditional export (`:186`) overrode it at every start. The unit values were effective **nowhere** at runtime — visible only to the manager/D-Bus. The running daemon "loaded from `.env` anyway" is accurate.
- **🔴 The reset form is the defect (A1, dual-confirmed):** the plan proposes per-variable empty assignments (`Environment=POSTGRES_PASSWORD=` …) citing man :2685-2687 — but that range is the **option-level list reset** (lone `Environment=`, "the list of environment variables is reset"); the per-variable rule is :2683-2685 (later overrides earlier). Under the per-variable form: (i) the var is **present-but-empty, not unset**; (ii) whether the manager's stored list drops the earlier non-empty entry is **unverified** (no observable duplicate-assignment unit exists on this host; systemd's `Environment` property is built from the accumulated assignment list); (iii) under either behavior the plan's own gate ("no longer lists POSTGRES_*") fails — the key stays listed, and if systemd retains both entries the **plaintext stays exposed** while the runbook reports success.
- **The provable form: a LONE `Environment=` in the drop-in** (full-list reset). Safety, verified: the live Sep-30 unit's only `Environment=` lines are the six minted ones (post-Sep-30 template deltas are exactly `After=postgresql.service` + `Environment=MALLOC_ARENA_MAX=2` + comments — git diff `36b3952c4..HEAD`; both postdate Sep-30, so absent from the live unit); all six are launcher-supplied from `.env`; clearing `MALLOC_ARENA_MAX` is harmless (`launcher.sh:1658-1659` default-only re-establishes 2). Verification gate becomes clean: `systemctl show -p Environment ensemble-main` → empty. Include a morning scratch-unit empirical check before live application (cheap, zero live risk). Post-re-adoption the lone reset is an idempotent no-op-to-harmless (the regenerated unit sets only MALLOC, which the launcher re-establishes); optional removal at re-adoption.
- **Drop-in vs re-adoption trade-off framing: VALIDATED** — with the citation fix (A2) and the unwritten composite step (A2): `systemd-adoption.md` §5 :398-434 is the **rollback-to-script-mode** recipe, not re-adoption; re-adoption = §5 teardown **then** §3(d) adoption, with the exit-78 clobber refusal (`:534-539`) as the hard teardown precondition. *(Anchors re-pointed post-strip @eb90a600c: runbook §5 is now :458-497; the clobber refusal is `adopt-unit.sh:518-521`; the composite re-adoption sequence is now spelled as runbook §5a.(b) :623 — the §5a remediation section this amendment called for exists.)* §5's script-mode interim start keeps the downtime window near-zero; going removal→adopt directly widens it. The composite must stop the interim script-mode daemon before `enable --now` (port conflict) — spelled nowhere (A2). Regenerated unit = drop-in replacement + gains `After=postgresql.service` + `MALLOC_ARENA_MAX=2`, loses the six minted lines.

---

## 4. Enrichment findings

### 4.1 Precedence contract (4-rung) — **SOUND, confirmed with three precisions**

- `load_env_file` per-key export at `launcher.sh:186` is **unconditional** → true last-wins; called once per launcher process at `:1649` **outside** `run_loop` — child restarts inside the loop re-inherit exports without re-reading `.env` (comment :1657 states this; worth one sentence in the runbook).
- `run_app.py:30-31` `if key not in os.environ` corroborates only-sets-unset — but it is **frozen-binary-only** (`sys.frozen` gate :9) and quote-unaware (:21-28): the structural guarantee lives solely in the launcher path (A10).
- The `set-environment` rung is **vacuous in-repo** (zero producers/consumers repo-wide).
- **Emptying the unit rung is sound: ZERO consumers depend on unit-env carrying PORT/POSTGRES_* (full census, all cited):** daemon (env via launcher regardless of rung); supervision classify (reads only `INVOCATION_ID` from `/proc/$pid/environ`, `lib.sh:4654-4655`); journal sweep; proc_tools (tracking id); stop-ensemble.sh (positional PORT + `.env` FILE reads, explicit "not the environment" rationale :283-300); promote/rollback (`resolve_env` :590-615 reads the `.env` FILE); watchdog plist lane; deploy psql (ambient+file); layer1 drop-in candidate (static HOME only). The rung's only "consumer" was the D-Bus reader — the defect itself. Hostshell-style probes reading the launcher MainPID `/proc/environ` see `.env` values regardless of the unit rung (launcher exports them) — nothing depends on unit-env PG keys.
- **MALLOC triangle: no inversion** (default-only `:=` treats empty-as-unset; `.env`-staged value wins; effective chain unchanged).

### 4.2 Missing-.env fail-fast — **intended and correctly directed, but the mechanism is conditional (A4) and the taxonomy needs precision (A5)**

- **Exit taxonomy (actual code):** 75 = PG unreachable (non-auth `OperationalError/DBAPIError/ConnectionError`, `daemon/__main__.py:258-259` → `:273-279`); **78 = auth refused** (SQLSTATE 28P01/28000/28P02, `:260-272`) **and** auth-config misconfig (`:330-342`); **"config-absent" has NO exit code** — POSTGRES_* reads fall back to `ensemble.json` per key (`:128-132`), non-postgres config skips preflight entirely (`:213-215`), and **absent or corrupt `ensemble.json` silently auto-derives + persists a SQLITE config** (`daemon/ensemble_config.py:90-96` / `:77-79`).
- **So D4's "no POSTGRES_* → exit 75" holds only when `ensemble.json` declares postgres** — which IS the installed norm (persisted at first boot, `ensemble_config.py:66-67/:94-95`). On a fresh-dir or corrupt-config install, a missing `.env` now yields a **SILENT SQLITE BOOT on an empty DB** — arguably a worse silent failure than stale creds. Known residual, out of tonight's scope; carry as a morning-report item (A4).
- **Loudness:** 78 → launcher exits 78 (`launcher.sh:1565-1567`), `RestartPreventExitStatus=78` (template :97) → unit **failed**, visible in `systemctl status`. 75 → unit stays **"active"** while the launcher retries internally; signals are journal-only (launcher WARN `env file not found` :149-150 names the surface; preflight names the creds). No single ".env missing" message — acceptable; add the one doc sentence "watch the journal, not unit state" (A5).
- **Restart-storm: NON-ISSUE (verified)** — tempfail backoff 5s start ×2, cap 60s (`launcher.sh:55-56`, ladder `:1349-1370`, arm `:1569-1585`), interruptible sleep, one-time notify, no max-retry by design (ADR-011). ≈2 journal lines per ≤60s steady state. No amendment required.

### 4.3 Fix-delivery path — **unstated in the plan (A3)**

`scripts/` does **NOT** ship in the staged payload (stage.sh:395-425; header inventory :5-16); `adopt-unit.sh` runs from a **repo checkout** (`:97`); deploy.sh never invokes it. The fixed generator reaches operators only via an **updated checkout post-merge** — the staged release does not carry it. The runbook and morning-decision framing must say so.

---

## 5. NUMBERED AMENDMENTS (for the developer/planner to fold — marked)

| # | Severity | Amendment |
|---|----------|-----------|
| **A1** | 🔴 load-bearing | **T3.5 + D10.1 — replace the per-variable empty-string resets with a LONE `Environment=` full-list reset** in the drop-in (man :2685-2687 option-level reset, not :2683-2685 per-variable override). Reword the verification gate to "`systemctl show -p Environment ensemble-main` → empty". Add: (i) morning scratch-unit empirical check before live application; (ii) note the reset also clears `MALLOC_ARENA_MAX` — harmless, launcher `:1658-1659` re-establishes; (iii) drop-ins sort after the main unit; a future drop-in re-adding values re-creates the exposure; (iv) post-re-adoption the drop-in is an idempotent no-op-to-harmless, optional removal. |
| **A2** | 🔴 load-bearing | **T3.5(b) + D10.1 — fix the re-adoption citation:** `systemd-adoption.md` §5 :398-434 is rollback-to-script-mode, NOT a re-adoption recipe. Re-adoption = §5 teardown → **§3(d) adoption**, with the exit-78 clobber refusal (`adopt-unit.sh:534-539`) as the hard teardown precondition; add the unwritten composite step (stop the interim script-mode daemon before `enable --now` — port conflict); note §5's script-mode interim keeps the downtime window near-zero. Add the unit-backup hazard note: a "backup" of the rm'd unit re-persists the POSTGRES_PASSWORD-bearing artifact at a new path — forbid, or mandate root-owned 0600 + delete after verification. |
| **A3** | 🟡 | **Runbook + morning framing — state the fix-delivery path:** scripts/ does not ship; the fixed generator reaches operators via an updated checkout post-merge; the staged release does not carry it. |
| **A4** | 🟡 | **D4 + T3.2 — state the mechanism precisely:** boot-block exit 75 is conditional on `ensemble.json` declaring postgres (the installed norm); no "config-absent" refusal exists; absent/corrupt json → silent SQLITE auto-derive+persist (`ensemble_config.py:90-96/:77-79`). Flag the silent-SQLite lane as a known residual; morning-report candidate for a future refusal hardening commission. |
| **A5** | 🟡 | **T3.2 exit-taxonomy precision:** 75 = PG unreachable; 78 = auth refused AND auth-config misconfig; config-absent = no code. 78 → unit failed (RestartPreventExitStatus :97); 75 → unit "active", journal-only. Add one sentence: "watch the journal, not unit state" for 75-loops. Restart-storm verified non-issue (backoff 5s×2 cap 60s) — optionally cite it. |
| **A6** | 🟡 | **D5/phase5 — reframe P5 as defense-in-depth hygiene, NOT threat-model closure** (exclusion delta = ∅ for every account class: home 750 + empty `nea` group). Fix the mechanism text (`cp` to new dest = source mode masked by umask; the live 644 originates from `.env.prod` regeneration at `deploy.sh:245-249`, umask 022; `.env.prod.demo` 644). Add a coverage decision for the uncovered copies (`.env.bak-2026-10-20T21:30:40Z` 644; `.env.prod.demo` 644): chmod in the same bounded change or name them accepted residuals. Add the ownership-consequence sentence (root-owned 600 `.env` → launcher can't read → D4 family). |
| **A7** | 🟡 | **D1 — correct the EnvironmentFile= rejection reasons:** drop "creates a second secret file" (same-file form exists; it WOULD close the D-Bus exposure — verified empirically); record the load-bearing reasons: D9 invariant, systemd-vs-launcher env-parser divergence (silent value-divergence risk), changed missing-`.env` failure mode. Outcome unchanged. |
| **A8** | 🟡 | **D9 + AC gates — widen the grep scope:** the baseline claim is false — third hit `test/packs/hard_reboot_drill_e2e_test.sh:707` (systemd-run `--property=EnvironmentFile=`, a working in-house precedent) lives in `test/` (singular), outside the planned `scripts/ launcher.sh daemon/ tests/` gate. Scan the whole repo minus docs/history with an explicit whitelist; acknowledge the precedent in D1. |
| **A9** | 🔴 stage-step input | **Rollback-adjudication input correction — see §6.** "All payload trees byte-identical vs v0.27.5" is FALSE at this base: agents/ carries the pre-existing gaia rider (9 files; v0.27.5 is an ancestor). |
| **A10** | 🟢 | **T2.1 verification record — add one sentence:** `run_app.py`'s only-unset `.env` load is frozen-binary-only (:9) and quote-unaware (:21-28); the structural precedence guarantee lives solely in the launcher path (`:1649`+`:186`). |
| **A11** | 🟢 | **Morning-decision list completeness (D10) — add:** `.env.bak` hygiene (name the actual file `.env.bak-2026-10-20T21:30:40Z`, 644); `.env.prod.demo`/source-regeneration mode hygiene; deferred-key-rotation coupling update (post-remediation the unit no longer needs rotation edits — update the standing critical-note coupling when remediation lands; D8 handles the doc side); silent-SQLite auto-derive lane as a possible future commission. Note: the pending origin/tester/wedge-capture-verification docs-only leader sweep is leader-owned, not this lane's scope. |
| **A12** | 🟢 cosmetic | **Anchor corrections (fold into D6):** config.yaml `daemon.port` is :112 (not :111-113); `adopt-unit.sh` cat-preserves-mode comment is :453 (cat at :454), D3 cites :456. |

---

## 6. ROLLBACK-SAFETY ADJUDICATION INPUT (for the stage step — house STANDING RULE, explicit semantic reasoning)

**What this change set ships:** NOTHING in any payload tree. The lane touches `scripts/` (generator + stage/deploy chmod — **does not ship**, stage.sh:395-425), `docs/runbooks/`, and `tests/` only. `launcher.sh` is untouched (P2 is verify+doc only). `.env` never ships (ADR-014/m6, stage.sh:20-21).

**Expected manifest delta vs v0.27.5** (corrected — supersedes the "all payload trees byte-identical" briefing):
- `agents/` **CHANGES** — 9 files, the pre-existing **gaia rider** merged into latest BEFORE this lane (v0.27.5 is an ancestor; merge-base verified). Inherited base content, NOT this lane's delta.
- `frontend/`, `plugins/`, `config.yaml`, `launcher.sh` — byte-identical.
- `ensemble-prod` — rebuilt (version bump + base-tip daemon content; this lane contributes no daemon/ code).
- Manifest scalar delta: `version`, `binary_version`, `staged_at`, `binary_sha256`, `agents_tree_sha256` (+ agents per-file map).

**Migration delta: EMPTY.** 109 .sql at this tip AND at tag v0.27.5 (`git diff v0.27.5..HEAD -- daemon/migrations/versions/` empty); this change set adds zero .sql. (House records said 108 at v0.27.3 — the 109th, wedge-capture, is already inside v0.27.5.)

**Runtime effect on the running service: ZERO.** No daemon/ or launcher.sh changes; the live unit is frozen write-once and never re-rendered by promote/restart/rollback; P5 chmod changes file MODE only — the running daemon already holds its inherited env. A promote of this release restarts the daemon against the same live unit + same `.env` → identical runtime env (launcher re-exports).

**Old-binary compatibility on rollback:** rollback = symlink flip to v0.27.5; schema unchanged (empty delta) → v0.27.5 binary compatible with the identical schema; `agents/` reverts to v0.27.5's tree = exactly the pre-promote live state (normal inherited-content rollback semantics, not a defect of this lane). No down-migration exists or is needed.

**Verdict input: `ENSEMBLE_ROLLBACK_SAFE=1` is legitimate** — this lane contributes zero schema mutation and zero payload-tree change; the only payload delta vs v0.27.5 is inherited base content (gaia rider) already carried by `latest`.

---

## 7. Worker evidence index

- **security-design** (`bda936f0`): boundary table (7 surfaces, access classes verified via bus-socket mode, home/install-dir modes, group membership, ptrace_scope); EnvironmentFile=/D-Bus empirical check on this host; P5 exclusion-delta analysis; 9 plan-error findings.
- **data-flow-design** (`3c842bcd`): 4-rung precedence verification (all rungs, with the once-per-process and frozen-only precisions); 12-consumer census (verdict: no dependency on unit-env PG/PORT); MALLOC triangle; drop-in/re-adoption env-flow semantics; 5 plan-error findings.
- **resilience-design** (`3be086f3`): exit-taxonomy from code (75/78/no-config-absent-code + silent-SQLITE lane); restart-storm verified non-issue; payload table from stage.sh; migration count 109==109; re-adoption sequence + failure modes + directive-set diff via template git history; 7 findings.

**Unverified items (all disclosed by the workers):** systemd parse-time dedupe of duplicate `Environment=` assignments (decides whether the per-variable reset form ever could have worked — A1 renders it moot); whether the live unit's `Environment=` set is exactly the six minted keys (inferred from template git history `36b3952c4..HEAD` + Sep-30 freeze; the morning scratch-unit check confirms before live application); live `ensemble-demo.service` zero-env claim (mode-consistent, content not read); `nea` sudo NOPASSWD (caller-stated).

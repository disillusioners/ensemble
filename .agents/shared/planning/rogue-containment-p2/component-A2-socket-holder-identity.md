# Rogue-Containment P2 — Component A2: Bind-Time Socket-Holder Identity (+ A1 Hygiene)

**Feature:** rogue-containment-p2 (residual-hardening lane; incident wi-2454ede3, 2026-11-11 bind-race restart loop)
**Component:** A2 — bind-time socket-holder identity: the gate learns what ACTUALLY holds the socket; refuses (exit 78 + holder-identity marker) on positive proof of a live FOREIGN holder regardless of claim presence; plus the A1 hygiene riders (7979 port token, bash refusal-text drift).
**Mode:** Mode 1 — design only. NO code, NO test runs, NO repo writes except this file (and its lane commit). Parallel siblings in this directory: `component-B-tier1-prod-dsn-fence.md` (B2, prod-DSN refusal), `component-E2-pins-verification-measurement.md` (E2, pins integration + S-gates + (D) measurement). Disjoint files; cross-referenced by filename only.
**Worktree:** `/home/nea/ensemble-src-wt-rogue-contain`, branch `feature/rogue-port-containment`, tip `bf4bfde6e720d889bb5dc00dfe9490ceb5dbafc1` (verified at read time). Canonical `/home/nea/ensemble-src` is FORBIDDEN for writes.
**Governing authority:** `.agents/shared/planning/bind-race/rogue-port-containment-analysis.md` (FINAL rev 2) — §5.2 (refuse-on-positive-proof + holder-identity marker + MANDATORY COMPANION alert), §5.4 (Lane 4 defer + trigger — interaction evaluated in §6.4), §5.5 (exit 78 reuse; never 75, never a new code), §8 H1 (same-install hole), §9 (leader decisions). Where this file contradicts the analysis, the analysis wins and the conflict is recorded in §14.
**Prior-lane inputs (read in full, anchors re-verified at tip bf4bfde6e):** `rogue-containment/component-A-port-ownership.md` (house style + fix-round-2 addenda B1/B3/B5/N3/N8), `rogue-containment/component-E-regression-pins-and-sequencing.md` (16-lane sanctioned census), `rogue-containment/harness/boot_rogue_mirror.sh`, `daemon/services/port_ownership.py` (649 lines), `daemon/services/bind_retry.py` (437 lines), `daemon/__main__.py` bind flow, `daemon/tools/bash.py` overlay, `daemon/tools/proc_tools.py` guard, the three pin files, `scripts/watchdog-watcher.sh`, `launcher.sh` refusal/notify sites.

---

## 1. Objective

Close the two refusal-blindness holes the shipped phase-1 claim gate left open:

1. **Foreign holder WITHOUT a claim is invisible** (`H-no-claim`): the shipped gate (`run_port_ownership_gate`, `daemon/services/port_ownership.py:587-649`) decides on claim-file install-dir STRING EQUALITY only — no `/proc/net/tcp`, `ss`, or `lsof` anywhere in the module (`/proc` reads today are refusal-time forensics: `_read_proc_exe` :283, `_read_proc_cwd` :290). A foreign holder that never wrote a claim drives the 30 s bind-retry (`bind_retry.py:305`, budget exhaust → `BIND_FAILURE_EXIT_CODE=1` at :178/:393) → launcher restart → the incident's 63-cycle loop. **Close:** bind-time holder discovery (pure `/proc`, no `ss`/`lsof` dependency) with hard degrade-never-refuse semantics; refuse exit 78 + holder-identity marker ONLY on positive proof of a live foreign holder.
2. **Same-install rogue is invisible (`H1`, analysis §8):** a rogue in the SAME install dir executing the live's own binary is "non-foreign" by install-dir equality. **Close (this plan's position, leader-ratified at LDP-1):** cheap claim-lineage — the claim records the claimant's parent (launcher-session) identity; legitimate hand-over = same-lineage sequential claimants; a same-install holder of a DIFFERENT lineage is refused. Genuinely cheap (~one JSON field extension + one normalized parent-cmdline comparison + degrade rules), ceremony-invisible (zero launcher.sh / unit / .env / migration surface), and it preserves the pinned promote-shape non-refuse (old-format claims without lineage fields remain non-foreign — backward compat).

Plus the **mandatory companion for the whole refusal family** (analysis §5.2 "MANDATORY COMPANION"; §9.4): the launcher's refuse branch has NO `_notify_once` (`launcher.sh:3847-3848` logs + `exit 78`; the tempfail branch :3853+ does notify; `_notify_once` itself is LOG-ONLY, :3509-3514) ⇒ every exit-78 refusal is a SILENT-DOWN. This lane GROWS the refusal family (claim-refusal + A2 holder-refusal + B2 prod-DSN refusal + the existing auth-config/DB-auth refusals) — the family alert is planned here in §5.8 and §3 Phase 3, on the repo's own daemon-independent watch pattern (`scripts/watchdog-watcher.sh`, ADR-025(b)).

And the **A1 hygiene riders** (§5.9): the demo port 7979 joins the overlay port tokens; the drifted bash refusal text stops advertising the agent-settable fence channel that B6 killed.

Binding principle (analysis §6): **launcher.sh = ZERO touches, unit template = ZERO touches, live .env = ZERO touches** — all containment lands in the daemon binary. Exit code contract: **78 only** (§6.2).

## 2. Scope

- **In scope:** the holder-discovery module + gate decision upgrade + claim-lineage (H1 close, LDP-1-gated) + fast-refuse shape (LDP-2) + the refusal-family operator alert (LDP-3) + A1 hygiene (a: 7979 token; b: refusal-text drift) + the AH-/HY-/lineage pin definitions handed to E2's matrices + the harness-leg contract notes E2 needs (leg (b)/(c) holder shapes).
- **Out of scope (siblings):** B2 prod-DSN fence (`component-B-tier1-prod-dsn-fence.md`) — this plan only ASSIGNS its refusal to the shared family-marker convention; E2 pins integration, S-gates, (D) measurement, CHANGELOG consolidation (`component-E2-pins-verification-measurement.md`).
- **Out of scope (routed, analysis wins):** Lane 4 sliding-window burst fix — DEFERRED with its trigger (§5.4); the interaction of A2's fast-refuse with that trigger is EVALUATED in §6.4 but the fix itself is not planned here. Lane 5 teardown budget. Boot-sweep disarm investigation (§7.2). Watchdog INSTALLATION on this host (§13 LDP-4 — named, not built: it is an operator-gated deployment action, not code).
- **Hard constraints:** plan ONLY component A2 + A1 hygiene. NEVER touch canonical `/home/nea/ensemble-src`. NEVER push. NO launcher.sh / unit-template / live-.env edits (binding principle). NO pkill anywhere in the design (containment refuses boots; it does not kill processes). Scratch-port discipline in every test (never 9797/9798/7979; `free_port()` at `tests/support/wedge_smoke.py:102-109` already re-rolls off 9797/7979).

## 3. Phases

| # | Name | File(s) | Objective | Depends on |
|---|------|---------|-----------|------------|
| 1 | Holder discovery module | `daemon/services/port_holder.py` (NEW; path per E2 §ASSUMPTIONS interface anchor) | Pure-function `/proc/net/tcp{,6}` → LISTEN inode → `/proc/*/fd` scan → holder pid → exe/cwd → install-dir attribution; injectable `/proc` root; degrade-never-refuse on every unverifiable step; two-sample confirmation | — |
| 2 | Gate decision upgrade + claim lineage | `daemon/services/port_ownership.py` (extend `run_port_ownership_gate` :587-649, `PortClaim` :74, `try_claim` :367, `is_live_foreign` :346, `write_refusal_marker` :535); `daemon/__main__.py` NOT reordered (gate call :449-461 unchanged) | Socket-holder evidence consulted pre-bind; positive-foreign ⇒ refuse 78 + holder marker regardless of claim; lineage layer for the same-install zone (H1 close, LDP-1); fast-refuse shape per LDP-2; claim format v2 (additive, backward-compatible) | Phase 1 |
| 3 | Refusal-family operator alert (mandatory companion) | `port_ownership.py` (marker payload `family`/`source` fields; reader extension :473-532); `daemon/__main__.py` auth/DB preflight sites (best-effort family-marker writes); `scripts/watchdog-watcher.sh` (NEW watched channel — NOT launcher.sh, not sha-pinned) | The whole exit-78 refusal family writes ONE durable record shape; exactly-once next-boot surfacing generalized (B5 pattern); the while-DOWN alert rides the repo's existing daemon-independent file-watch seam | Phase 2; B2 must adopt the record shape (cross-reference §16) |
| 4 | A1 hygiene + pins + matrices | `daemon/tools/bash.py` (:2044 token, :1350-1360 text); `tests/unit/test_pkill_overlay_pins.py`; `tests/unit/test_port_holder_identity.py` (NEW); `tests/unit/test_port_ownership_pins.py` (gate-level extensions); matrix rows handed to E2 | 7979 token + drift fix land WITH their pins; AH-* (E2 contract AH-M1..M8 + additive AH-M9+) and HY-* and LH-* (lineage) defined here | Phases 1-3 for AH-*; hygiene independent |

Sequencing note (mirrors E2 §SEQUENCING): A2 and B2 both touch boot-preflight ordering in `daemon/__main__.py` — merge order must keep `install_root_logging()` first (:447) and the DB-preflight → port-gate ordering explicit; A2's changes are INSIDE `run_port_ownership_gate`, so the `__main__` call block :449-461 stays byte-stable in this component.

## 4. Decisions (frozen for this component; leader-ratification points flagged)

| ID | Decision | Rationale (receipt-cited) | Rejected alternatives |
|----|----------|---------------------------|----------------------|
| D-A2-1 | **Holder discovery is pure `/proc`** — `/proc/net/tcp` + `/proc/net/tcp6` parse → LISTEN-state rows (st == `0A`) on the target port → socket inode → `/proc/<pid>/fd/*` readlink `socket:[inode]` scan → holder pid → `/proc/<pid>/exe` + `/proc/<pid>/cwd` attribution. NO `ss`/`lsof`/`ps` subprocess dependency. | Zero new dependencies (binary-swap-only lane); `/proc` is the module's existing evidence family (`_read_starttime` :263, `_read_proc_exe` :283, `_read_proc_cwd` :290); `ss`/`lsof` availability is host-dependent and container-CI-hostile | `ss`/`lsof` subprocess parsing (availability + parsing drift + CI absence); netlink dumps (root-ish, overkill) |
| D-A2-2 | **Refuse on positive proof of a live FOREIGN holder REGARDLESS of claim presence** (claim absent, stale, or non-attributable). "Positive proof" = BOTH resolved non-None (holder install-dir AND self install-dir) AND different AND holder liveness verified by `pid_alive_with_starttime` (reuse :297). This upgrades the stale-claim semantics: today stale ⇒ take-over; under A2, stale claim + positive-foreign HOLDER ⇒ refuse (E2 pin AH-4). | Analysis §5.2 shape point 2 ("Refuse ONLY on positive proof of a live foreign owner"); commission item 2 ("regardless of claim presence or install-dir equality"); closes the 63-cycle driver class | Claim-presence-gated refusal (leaves the no-claim driver open — the incident shape) |
| D-A2-3 | **Degrade-never-refuse on EVERY unverifiable step**: missing/unparseable `/proc/net/tcp{,6}` (container CI), permission-denied exe/cwd, `<unreadable>` placeholders, reparented holder (ppid == 1 / parent unreadable) in the claim-less path, fd-scan races, IPv6-table absence, hex-decode anomalies ⇒ NO refusal; loud holder-identity WARNING (observability without verdict); boot proceeds to `bind_socket_with_retry` unchanged. Only POSITIVE PROOF refuses. | Analysis §5.2 point 3 ("Unknown ⇒ retry, never refuse") applied to the new evidence class; E2 pins AH-3 (direction 1) and AH-M8 (starttime discipline); container-CI foot-gun (house rule: `/proc` reads tolerate absence) | Refusing on unattributable holders (would false-refuse nginx-style squatters AND container CI — violates positive-proof-only) |
| D-A2-4 | **H1 close = claim-lineage (option i), LDP-1-gated.** Claim v2 adds `ppid`, `ppid_cmdline_sha` (sha256 over the normalized `(argv[0] basename, argv[-1] basename)` tuple), `ppid_cmdline_head` (≤120 chars, forensics). Decision upgrade INSIDE the same-install zone (install-dirs equal, holder alive): lineage fields present + self-parent readable + hash MISMATCH ⇒ refuse; lineage fields absent (old-format claim) OR self-parent unreadable ⇒ unknown ⇒ non-foreign (backward compat: every pre-A2 claim file, and therefore the pinned promote-shape non-refuse leg, stays green). | Commission item 4 option (i): "only if genuinely cheap and ceremony-invisible" — it is: ~40 lines of decision logic + additive JSON fields; zero ceremony-visible surfaces; promotes hand over under the same launcher cmdline (`systemctl restart` mints a NEW launcher pid but the SAME cmdline ⇒ hash matches ⇒ non-foreign); `pid+starttime` staleness already gates everything before lineage is consulted (a dead holder never reaches the lineage check) | (ii) re-declare H1 (presented as LDP-1 alternative with the amputation path — if the leader picks (ii), Phase 2's lineage tasks drop out and the rest of this plan stands alone); per-boot nonces WITHOUT launcher identity (a nonce cannot distinguish hand-over from rogue without something to compare against — the launcher session IS the comparison anchor) |
| D-A2-5 | **Lineage comparison = normalized parent-cmdline tuple hash, NOT ppid equality.** `systemctl restart` / unit restart mints a new launcher pid; a hash over `(argv[0] basename, argv[-1] basename)` (e.g. `("bash", "launcher.sh")`) survives launcher-pid churn while still separating launcher-shaped parents from agent-shell parents. Forgery bound: a same-uid adversary can forge anything on disk or spawn a launcher-shaped parent — that is the ALREADY-DOCUMENTED same-uid residual (`port_ownership.py` module docstring :29-31); lineage raises the bar from "accidental rogue" (the incident class: ambient `PORT` inheritance, agent error) to "deliberate claim-forging adversary" (out of containment scope). | Verified restart shapes: launcher run-loop keeps the same ppid; `systemctl restart` does NOT (new launcher process) — pid-equality would false-refuse it; basename-tuple survives both; the launcher script path is stable at the install root across releases (releases swap the BINARY, not the launcher) | ppid+starttime equality (breaks `systemctl restart`); full-cmdline hash (breaks relative-vs-absolute invocation of the same script); cgroup membership (shifts across launcher restart cycles; systemd-run unavailable — critical note 2026-10-31) |
| D-A2-6 | **Fast-refuse = IMMEDIATE exit 78 on positive proof, BEFORE `bind_socket_with_retry`** (E2 variant 6a). The refusal never enters the retry ladder. | Commission item 3 + analysis §5.5 ("restarting cannot fix this"): a positively-identified foreign install cannot free the port on any human timescale; every retry cycle burns burst budget and feeds the loop the incident actually showed; the 30 s ladder exists for TRANSIENT hand-over contention, which is precisely the case positive-proof excludes (same-install / lineage-match / unresolved ⇒ NOT positive proof ⇒ ladder path unchanged). Gate sits pre-bind (`__main__.py` gate call :449-461 precedes :488-489), so the failed-boot U shrinks instead of growing — the Lane-1 constraint (`U ≤ 58 s`, bind ceiling 30.0 hard-clamped, `bind_retry.py:167-168`) is PRESERVED by construction | After-ladder refusal (E2 variant 6b — burns 30 s per cycle and reintroduces the burst-budget feeding the fast path exists to stop; kept as the specified alternative should the leader ratify it at LDP-2) |
| D-A2-7 | **Two-sample confirmation for socket-evidence refusals**: a positive-foreign socket finding is re-confirmed once (~250 ms later) before refusing; a holder that vanished between samples ⇒ no proof ⇒ proceed (bind succeeds or retries transiently). Claim-evidence refusals (claim file, lineage) are NOT re-sampled — the file is durable evidence. | TOCTOU honesty: discovery→exit has a window; a holder that dies in that window would be refused for a port that is now free. Two samples kill the dominant vanish-race at ~zero cost; the residual window (holder dies between sample 2 and exit) is accepted and ATTRIBUTED (the marker names a pid that really held the port seconds earlier) | Single-sample refuse (cheap but noisier against dying holders); re-sample loop until stable (unbounded) |
| D-A2-8 | **Refusal-family alert = ONE durable record shape + generalized exactly-once surfacing + the watchdog file-watch leg** (LDP-3). Family members: port-ownership claim refusal, A2 holder refusal, B2 prod-DSN refusal, auth-config refusal (`__main__.py:288-340`), DB-auth refusal (:175-268). The existing pair — `write_refusal_marker` (:535-570) + `surface_refusal_marker` exactly-once-with-rotation (:473-532; lifespan reader at `daemon/api.py:727-742`) — is the TEMPLATE; new `family`/`source` fields make it the FAMILY record; the auth/DB preflight sites gain best-effort writes (data-dir resolution per the `api.py:722-727` precedence; unresolvable ⇒ log-only degrade). The while-DOWN channel extends `scripts/watchdog-watcher.sh`'s daemon-DOWN watch-set (header :33-44, P2.3 B4 / ADR-025(b) — "file-watches two daemon-DOWN channels, readable without the daemon process") with the family marker file: latched, one notify per occurrence, `_notify()` :361-367. watchdog-watcher.sh is NOT launcher.sh, carries NO manifest sha (only `launcher_sha256` is pinned — `scripts/upgrade/lib.sh:3055,4076-4084`), so this is ceremony-invisible. | Verified: launcher refuse branch :3847-3848 has no notify and `_notify_once` is log-only (:3509-3514) ⇒ a 78 is a silent-down (analysis §5.2 MANDATORY COMPANION: "must ship in the same release"); the repo's OWN daemon-independent alert pattern is the watchdog's file-watch seam — extending it is the designed move (ADR-025(b)), not a new mechanism; self-alarm/notify_user are manager-wired agent tools (ari-only, `daemon/tools/self_alarm.py` header) — UNAVAILABLE at the refusal moment (pre-lifespan) | systemd-notify status text (NotifyAccess defaults to `main` — child-process notifications are dropped; unit-dependent ⇒ violates zero-unit-touch); Discord/webhook push (new dependency + secret plumbing — rejected in prior component A D8); critical-notes direct write (service not wired pre-lifespan); self-alarm (ari-only + manager-wired — dead at refusal moment) |
| D-A2-9 | **A1(a): 7979 becomes a port token in the shared overlay predicate** (`bash.py:2044` tuple gains `("7979", r"\b7979\b")`). NO proc_tools.py edit is needed: `proc_run` imports and calls the SAME `_pkill_overlay_violation` (`proc_tools.py:2585-2587`) — the shared predicate IS the mirror (verified). Rationale: 7979 is the DEMO live port (`scripts/deploy.sh:118` `PORT=7979`; 3-env topology "dev 8079 / demo 7979 / live 9797", `scripts/ensemble-prod.plist:15`); name-patterns cover `ensemble-demo` but a port-shaped kill (`fuser -k 7979/tcp`) currently passes. | Commission item 6(a); demo is a live-shaped daemon in the sanctioned topology — gating its port equals 9797/9798 treatment; one-table edit keeps bash/proc symmetric by construction | Editing proc_tools separately (duplicates the predicate; drifts); a config-driven port list (config surface for a containment invariant is over-engineering) |
| D-A2-10 | **A1(b): the bash refusal text (:1350-1360) is corrected to the ambient-only channel**, modeled on proc_tools' already-correct text (:2602-2613): drop the `env={"ENSEMBLE_FENCED_TEST_CONTEXT": "1"}` tool-overlay sentence. B6 made the fence ambient-only (`bash.py:1331-1345` strips the key from the overlay; predicate `del env` :2036) — the text advertises a channel that silently does nothing, steering agents into a dead end during a real incident. **Pin-flip answer (verified): NO existing test pins this text** — `test_pkill_overlay_pins.py` (10 tests, :6-54) pins the pure predicate function, not the bash return string; a repo-wide grep for the refusal text in `tests/` finds zero hits. So there is NO pin to flip; the plan ADDS a positive pin (§8 HY family). | Commission item 6(b) + verified drift; proc_tools text is the in-repo correct exemplar | Leaving the text (agents keep following dead instructions); making the overlay channel work again (re-opens the B6 self-fence hole — explicitly rejected by fix-round 2) |

## 5. Holder-Identity Design

### 5.1 The discovery walk (new module `daemon/services/port_holder.py`)

Pure functions; every function takes an injectable `/proc` root (default `"/proc"`) so tests build synthetic trees under `tmp_path` (container-CI-safe; absent tables ⇒ degrade, never exception).

```
find_listener_holders(proc_root, port, family) -> list[HolderRef]
  1. read /proc/net/tcp  (IPv4) and /proc/net/tcp6 (IPv6); either file absent
     or unparseable ⇒ that family contributes nothing + a loud DEBUG/WARNING
     (degrade, D-A2-3); BOTH absent ⇒ return [] with reason="proc-tables-absent".
  2. filter rows: state == 0A (TCP_LISTEN) AND local port (hex column) == port.
     (Only LISTEN blocks bind; TIME_WAIT/SYN_RECV do not. Hex decode is pinned — §8 AH-13.)
  3. collect socket inodes (column 'inode').
  4. scan /proc/<pid>/fd/* for readlink == "socket:[<inode>]"; record pid.
     Race windows: fd gone mid-scan ⇒ skip; pid gone mid-scan ⇒ skip (no proof
     fabricated from a half-read).
  5. self-exclusion: skip os.getpid() defensively (gate runs pre-bind, so self
     cannot be the holder; the skip is belt-and-braces for re-entrancy).
  6. for each holder pid: HolderRef(pid, starttime=_read_starttime(pid),
     exe=readlink(/proc/<pid>/exe), cwd=readlink(/proc/<pid>/cwd),
     ppid=field 4 of /proc/<pid>/stat) — each read independently
     "<unreadable>"-tolerant (the module's existing convention, :283-294).

attribute_install_dir(holder: HolderRef) -> Optional[Path]
  a. exe path shape "<install>/releases/<v>/..."> (frozen): walk ancestors to
     the first directory containing the install-root marker (launcher.sh) ⇒
     install root.
  b. else cwd contains the source-tree marker (launcher.sh or pyproject.toml +
     daemon/) ⇒ cwd as install root (dev/source boots).
  c. else None (unattributable ⇒ non-foreign, D-A2-3).
  Reuses the SELF-resolution machinery semantics for the holder side
  (`resolve_self_install_dir`, port_ownership.py:198-227 — no canonical-live
  fallback, N3) — attribution is by PATH MARKERS, never by env (a foreign
  process's environ is not consulted).

confirm_holder(holder: HolderRef, proc_root) -> bool
  re-reads pid_alive_with_starttime(holder.pid, holder.starttime) — a recycled
  pid between walk and attribution ⇒ mismatch ⇒ discard (no proof).
```

Cost: one `/proc/net/tcp{,6}` read (~KBs) + one `/proc/*/fd` scan (O(processes); live-host scale ≈ hundreds ⇒ tens of ms) — negligible against the Lane-1 budget constraint (§6.3).

### 5.2 Decision upgrade in the gate (`run_port_ownership_gate`, port_ownership.py:587-649)

Order inside the gate (survives fix-round-2's shape):

1. `surface_refusal_marker(claims_dir)` — unchanged (:604).
2. Port validation — unchanged (N8, :606-615).
3. **NEW — socket-holder discovery**: `find_listener_holders` + attribution. For each attributed holder, evaluate foreignness:
   - **Positive-foreign** (D-A2-2): holder install-dir AND self install-dir both non-None, different, holder liveness confirmed ⇒ **two-sample confirm (D-A2-7)** ⇒ still positive ⇒ refuse (exit 78, holder marker — §5.4 payload) BEFORE any bind. E2 pins AH-1/AH-2/AH-4/AH-7.
   - **Same-install holder**: hand to the lineage layer (§5.3) when LDP-1 ratifies; without lineage fields resolvable ⇒ non-foreign ⇒ fall through (today's behavior). E2 pin AH-5 preserved.
   - **Unattributable / unverifiable**: loud holder-identity WARNING (pid/exe/cwd as far as read) + proceed (D-A2-3). E2 pin AH-3.
   - Claim-and-socket agreement: if a claim exists and its pid IS the discovered holder, the claim evidence and socket evidence describe the same process — one refusal verdict, marker `source` names both.
4. Claim path — existing `try_claim` (:367) unchanged in shape; with lineage fields when LDP-1 ratifies (§5.3). Refusal ⇒ marker + `sys.exit(78)` (:618-637 unchanged in effect).
5. Degrade/unknown ⇒ loud warning + proceed (:638-642 unchanged).

### 5.3 The lineage layer (H1 close — LDP-1-gated; amputable without touching §5.1/§5.2)

Claim v2 fields (ADDITIVE; old readers ignore them; `read_claim` :322-343 already tolerates absent keys):

```json
{ "install_dir": "...", "pid": ..., "starttime": ..., "port": ..., "host": "...",
  "claimed_at": "...", "version": "...", "uid": ...,
  "ppid": 3610110, "ppid_cmdline_sha": "<sha256 of ('bash','launcher.sh')>",
  "ppid_cmdline_head": "bash /home/nea/agents-ensemble/launcher.sh" }
```

Decision (same-install zone only — install-dirs equal, holder alive via pid+starttime):

| Evidence state | Verdict |
|---|---|
| Claim has NO lineage fields (pre-A2 file) | unknown ⇒ **non-foreign** (backward compat; every existing claim + the pinned promote-shape leg unchanged) |
| Self parent cmdline unreadable (orphaned gate call, container oddity) | unknown ⇒ **non-foreign** |
| `ppid_cmdline_sha` == hash(normalized self-parent tuple) | lineage match ⇒ **non-foreign** (hand-over: launcher run-loop OR `systemctl restart` — same cmdline, new pid) |
| Hash mismatch (both sides resolved) | **foreign ⇒ refuse 78** + marker (source="claim-lineage") |

Applies symmetrically to the claim-holder (recorded lineage) — and ONLY there. The claim-less socket path does NOT refuse on lineage (D-A2-3: a live `/proc` parent read of a foreign holder is reparent-hazard-prone — the incident's own rogue outlived its spawning shell and re-parented to pid 1; recorded-at-boot lineage is the only reparent-immune evidence). Claim-less same-install holder ⇒ loud attributed WARNING + today's retry path — an H1 residual, NARROWED (§12, SC-7).

Closed by this layer, explicitly:
- The 63-cycle shape with a same-install claim-less rogue stays open ONLY in its narrowest form (§5.3 table row 1/2 degrade); every claim-present same-install rogue is refused; every foreign-install holder (claim or not) is refused (§5.2).
- **Side-defect closed:** today a same-install rogue OVERWRITES the live's claim (`try_claim` unlinks + rewrites on same-install, :405-409) mid-flight. Under lineage, the rogue is refused BEFORE the take — the live's claim survives.

### 5.4 The holder-identity refusal marker

Reuses `write_refusal_marker` (:535-570) with the payload EXTENDED (analysis §5.2 marker spec: pid / exe / cwd / install / port — all present today; additions are additive):

```json
{ "refused_at": "...", "port": ..., "host": "...", "exit_code": 78,
  "family": "port-ownership", "source": "socket-holder" | "claim" | "claim-lineage",
  "holder": { "pid": ..., "exe": "...", "cwd": "...", "install_dir": "..."|null,
              "ppid": ..., "ppid_cmdline_head": "..." },
  "self_install_dir": "..."|null,
  "evidence": "positive-install-mismatch" | "lineage-mismatch" }
```

`holder.install_dir` is the ATTRIBUTED value and may be `null` when attribution failed — but attribution failure ⇒ non-foreign ⇒ no refusal (D-A2-3), so a written marker always carries an attributed foreign install or a lineage mismatch. Surfacing: the existing exactly-once reader (:473-532; lifespan call `api.py:727-742`) logs the new fields when present (reader extension, Phase 3).

### 5.5 Two-sample confirmation (D-A2-7) — precision

Sample interval 250 ms (matches `try_claim`'s race-retry sleep, :374 — house constant). Both samples must independently yield a positive-foreign attribution for the SAME (pid, starttime). A changed pid or vanished holder between samples ⇒ no proof ⇒ proceed. Claim-path refusals are not re-sampled (durable file evidence).

### 5.6 Degrade-never-refuse inventory (pin-backed; E2 AH-3 direction + AH-M8)

| Unverifiable step | Behavior |
|---|---|
| `/proc/net/tcp` + `tcp6` absent/unparseable (container CI) | no discovery; loud WARNING; proceed to bind-retry (today's behavior) |
| one family table absent (IPv4-only host / IPv6-only row) | parse what exists; the absent family contributes nothing |
| holder exe/cwd permission-denied | attribution None ⇒ non-foreign; WARNING carries raw pid |
| holder pid recycled between walk and confirm | starttime mismatch ⇒ discard ⇒ no proof |
| holder vanished between the two samples | no proof ⇒ proceed |
| reparented holder in the CLAIM-LESS path | lineage not consulted claim-less (§5.3) ⇒ install-dir rule only |
| fd-scan partial read (fd dir vanished mid-scan) | skip that pid; never fabricate |

### 5.7 What this does NOT close (honest residuals)

- **Claim-less same-install holder** (rogue from an OLD pre-gate binary, or a claim-deleting rogue): refused NOT; loud attribution + retry loop as today. Requires the holder's own claim to have lineage to compare — unprovable claim-less without the reparent-hazard false-refuse (D-A2-3/D-A2-5).
- **Same-uid adversary** forging claim lineage: already the documented containment boundary (`port_ownership.py:29-31`); lineage raises the bar, does not move the boundary.
- **Non-ensemble squatter** (nginx et al.): unattributable ⇒ non-foreign ⇒ retry (positive-proof-only; D-A2-3). The WARNING now NAMES it (attribution observability), which today's loop never did.
- **9798 metrics-port fence**: B-phase rider (d) already shipped (derive + retire, prior lane); A2 records claims for whichever port the gate is invoked on, generically — unchanged boundary (prior component A §6.3).

### 5.8 Refusal-family operator alert (mandatory companion — D-A2-8)

- **Record shape:** one JSON family record (§5.4 fields + `family`), written best-effort by EVERY exit-78 refusal site: port-ownership (:636 — existing call, gains fields), A2 holder path (same call), B2 prod-DSN (B2 adopts; cross-ref §16), auth-config (`__main__.py:333-340` loop — one record per refusal batch, best-effort), DB-auth (:263-268). Data-dir resolution follows the `api.py:722-727` precedence (ENSEMBLE_DATA_DIR > DATA_DIR > ./data); unresolvable/unwritable ⇒ log-only (the WARNING line already carries the identity — degrade, never block the exit).
- **Exactly-once surfacing (next boot):** `surface_refusal_marker` (:473-532) already reports-once + rotates `.seen-<ts>`; extended to carry `family`/`source` in the ERROR line. Both the gate (:604) and the lifespan (`api.py:727-742`) call it — every entry path surfaces.
- **While-DOWN channel:** `scripts/watchdog-watcher.sh` gains a THIRD daemon-DOWN watched source: the family marker file under the claims/data dir — same pattern as its existing launcher-state and releases/state.json watches (header :33-44, latch in `.watchdog-alerts-state`, one `_notify` :361-367 per occurrence). watchdog-watcher.sh is NOT launcher.sh; it is not sha-pinned (only `launcher_sha256` is, `lib.sh:4076-4084`); its live install is USER-GATED (U6 ladder) — the file names that itself.
- **Honest deployment caveat (LDP-4):** the watchdog is a launchd-plist macOS shape (`scripts/ensemble-watchdog-watcher.plist`) and NO scheduling for it exists on this Linux host (no crontab entry, no systemd user timer — verified). So on THIS host the while-down reality stays: journald marker line + `systemctl status` + the marker file awaiting next boot. The watchdog leg activates wherever a watcher is installed. Installing a systemd-timer variant on this host is an OPERATOR decision this lane names but does not build.

### 5.9 A1 hygiene riders

**(a) 7979 token** — one-tuple edit at `bash.py:2044`; proc_tools inherits via the shared predicate (:2585-2587 — verified, no second edit). Pins: `test_port_family_refused_unfenced` + list-form + ambient-fence + B6-overlay legs all gain 7979 assertions (§8 HY).

**(b) refusal-text drift** — `bash.py:1350-1360` rewritten to the proc_tools exemplar shape (:2602-2613): names the DAEMON-AMBIENT channel only, drops the `env={...}` tool-overlay instruction. NO pin flip needed (verified: no test pins the text); a NEW positive pin locks the corrected shape (§8 HY-3/HY-4).

## 6. Placement and Exit-Code Contract

### 6.1 Placement

UNCHANGED call site: `daemon/__main__.py:449-461` (gate after `install_root_logging()` :447, before `bind_socket_with_retry` :488-489). All A2 logic lives INSIDE `run_port_ownership_gate` (port_ownership.py:587-649) + the new module. Marker observability order is already correct (logging installed before the gate — D5 of the prior lane). The refusal exits pre-bind ⇒ it never enters the retry ladder (D4/P-d4 preserved; E2 pin AH-7). B2-merge ordering note in §3.

### 6.2 Exit-code contract — 78 ONLY (analysis §5.5; commission item 7)

- 78 = refuse = "restarting cannot fix this"; unit `RestartPreventExitStatus=78` (`scripts/systemd/ensemble-daemon.service:110`, verified) keeps systemd down — QUIET + alerted beats the 63-cycle crash-loop + alerted.
- 75 is FORBIDDEN here: budget-exempt (`launcher.sh:3853` `_notify_once "tempfail-75"`) ⇒ unbounded slow loop — the same class, less visible.
- A NEW code is FORBIDDEN: lands in `*→crash` and loops unless launcher.sh learns it (sha-pinned, ceremony-critical) AND the unit lists it. Never piecemeal (§5.5).
- `BIND_FAILURE_EXIT_CODE = 1` (`bind_retry.py:178`) and the 30.0 hard ceiling (:167-168) are UNTOUCHED.

### 6.3 U-budget interaction (Lane-1 constraint)

The failed-boot uptime U must stay ≤ 58 s (burst abort reachability; ceiling coupling note `bind_retry.py:155-166`). A2 makes the fast path SHORTER (refusal pre-bind, before any retry sleep). The degrade paths add only a `/proc` walk (~tens of ms) + one 250 ms sample before PROCEEDING (not before exiting) ⇒ U unchanged for every non-refusing boot. E2's U-measurement cycle (§ measurement) measures this lane's U directly.

### 6.4 Lane-4 interaction (commission item 3 — presented for the architect's obsolescence evaluation)

With A2 shipped (claim gate + holder gate + lineage), every ENSEMBLE-holder loop driver converts to exit 78: foreign-claim (shipped phase 1), foreign-holder-no-claim (§5.2), same-install lineage-mismatch (§5.3). The remaining infinite-loop classes are (a) non-ensemble squatters (degrade-by-design, D-A2-3) and (b) same-install claim-less holders (§5.7 residual, loops WITH attribution). Fast-refuse therefore removes the loop CLASS the Lane-4 sliding-window fix was insuring against — the fix's trigger ("measured post-Lane-1 `U > 58 s` OR a loop recurs", analysis §5.4) loses its main live class. NOT obsoleted: the H2 U-measurement itself (E2 § measurement runs it) — the trigger stays armed, now expected to stay silent. Position: Lane 4 STAYS DEFERRED, trigger unchanged; this plan presents the interaction so the architect can evaluate obsolescence with the loop-class table above.

## 7. Lane-by-Lane Compatibility Walk (sanctioned lanes; census = prior component-E §SANCTIONED-LANE ENUMERATION, 16 distinct rows + the p2 harness)

Decisive question per lane: does holder discovery / lineage ever misattribute a sanctioned holder as foreign?

| # | Lane | Verdict under A2 | Why (receipt) |
|---|------|------------------|---------------|
| 1 | Live boot + promote hand-over window | **SAFE** | Holder = old daemon, same install, alive ⇒ lineage match (same launcher cmdline across releases — the launcher path is stable at the install root; releases swap the binary only) ⇒ non-foreign (D-A2-5). Even where a claim is momentarily absent, the socket holder is same-install ⇒ non-foreign ⇒ bind-retry covers the transient (30 s, `bind_retry.py:167`). `systemctl restart` (new launcher pid) still matches by cmdline hash. |
| 2 | Promote / restart / rollback chain (`promote.sh`, `adopt-unit.sh` — PORT from staged .env only) | **SAFE** | Staged resolves to the LIVE install root (frozen path derivation foot-gun — prior A D2/R6) ⇒ same-install ⇒ lineage-gated, and the lineage evidence matches (both children of launcher-shaped parents). The deliberate PORT setter (`manager.py` promote executor) untouched. |
| 3 | `dev.sh` boots 8079 (`dev.sh:141-142`) | **SAFE** | Port 8079 ⇒ different claim key ⇒ live claims never consulted. Discovery on 8079 finds dev holders only; dev-vs-dev same tree ⇒ same-install ⇒ non-foreign. Restart overlap: both children of `bash dev.sh` ⇒ lineage match (when ratified) ⇒ retry as today. |
| 4 | Pytest transient-unit boots (`wedge_smoke.py:451-477,552-586`; `free_port` :102-109 re-rolls off 9797/7979) | **SAFE** | Free ports ⇒ no listener at gate time ⇒ discovery returns [] ⇒ zero behavior change. Session claims fence (`tests/conftest.py:912-919`) pins claims to tmp. |
| 5 | Tester staged-binary scratch mirror (`boot_v2.sh`-shaped: umask 077, per-var scrub, FAKE_HOME, sweep pin 0, sandbox, PORT=18790) | **SAFE** | 18790 ≠ live ports; sandbox self-consistent install root; discovery finds nothing on a free scratch port. Holder-path pins (E2 legs b/c) plant holders ONLY on the leg's scratch port. |
| 6 | DR-2 exit-78 drill (`docs/runbooks/upgrade-drills.md:164-191`: scratch SBX, PORT=8377, fake HOME) | **SAFE (marker-text discrimination)** | The drill's 78 comes from CONFIG refusal AFTER claim+bind succeed. A2's marker carries `family`/`source` — the drill asserts ITS marker text; A2's refusal would only fire on a foreign 8377 holder, which the drill's scratch env does not have. |
| 7 | `scripts/test_builtin_mcp_servers.sh` (PORT=18088 pinned after .env source) | **SAFE** | 18088 unique claim key; no foreign holder expected; if a stale sibling held 18088 it would be same-install (same repo source root) ⇒ non-foreign ⇒ retry (unchanged). |
| 8 | Shell packs sourcing launcher.sh (`run_tests_scrubbed.sh`) | **SAFE** | No daemon boot; scrub semantics untouched by A2. |
| 9 | Soak/pack fences (`dbpool_gate_ladder.sh:103-105`, `soak_gate_mcp_warmup_acceptance.sh:388-389`) | **SAFE** | No live-port contact; A2 adds no env reads to these lanes. |
| 10 | Live boot tier-1 (`ensemble.json` present) | **SAFE** | Live holder on 9797 = self-lineage ⇒ non-foreign; discovery attributes the holder only when a DIFFERENT boot starts — and then the verdict is correct-by-construction (same install ⇒ lineage-gated; foreign install ⇒ refuse is the DESIRED close). |
| 11 | Stage.sh no-boot (`stage.sh:424`) | **SAFE** | No daemon boot ⇒ no gate run. |
| 12/14/18/19/20/21/22 | Duplicates of 3/7/8/10/9/6 | **SAFE** | — |
| 13 | Manual PG smoke 8199 (`tests/manual/maintenance_console_smoke.sh:12`) | **SAFE** | 8199 scratch; no live contact. |
| 15 | Deploy.sh (ownership-scoped stop, no boot) | **SAFE** | No port-based logic; A2 adds none. |
| 16 | Makefile install | **SAFE** | No daemon boot. |
| 17 | Frozen entry points (`run_app.py`, `ensemble.spec`) | **SAFE** | Gate runs on both entry paths (inside `_serve_with_bind_retry`); holder discovery is `/proc`-only — works identically frozen (no subprocess dependency — D-A2-1's reason). |
| H | p2 harness legs (E2: refuse-foreign / db-poison / happy + NEW tier1-name-refusal / foreign-holder / same-install-handover) | **SAFE with two contracts** (see §14 C1 + §15 Q1) | Leg (b) foreign-holder: scratch holder cwd = scratch install ⇒ foreign ⇒ refuse 78 (AH-1). Leg (c) same-install-handover: holder cwd == self resolved root ⇒ same-install ⇒ **leg setup must spawn holder and daemon from the SAME parent cmdline** (both children of the harness script/pytest runner) so the lineage layer (if ratified) reads match ⇒ non-refuse (AH-5 green). Existing mirror legs: the refuse-foreign leg's planted-claim PATH is pre-B1 (plants at `$ENSEMBLE_DATA_DIR/port-claims`; B1 gate reads `ENSEMBLE_PORT_CLAIMS_DIR`/XDG//tmp) — the harness must set `ENSEMBLE_PORT_CLAIMS_DIR` explicitly (§14 C1). |

## 8. Regression-Pin Skeleton (mutation discipline: every pin names its mutant; E2 integrates into `tests/bind_race_port_ownership_mutation_matrix.md`)

House rule (POK `mutation-01-guards-need-counter-mutation`): positive shapes only; a counter-mutation must redden each pin. E2 pre-reserved AH-M1..M8; this plan satisfies them and adds AH-M9+ (lineage) + HY-M1..M4 (hygiene). New unit file per E2's anchor: `tests/unit/test_port_holder_identity.py`; gate-level extensions in `tests/unit/test_port_ownership_pins.py` (18 → ~26).

| Pin | Positive shape (green) | Counter-mutant (RED) |
|-----|------------------------|----------------------|
| AH-1 | Synthetic `/proc` tree (tmp proc-root): LISTEN row on scratch port + matching `socket:[inode]` fd in a synthetic holder pid whose cwd = scratch install dir; gate refuses exit 78; marker written | **AH-M1** stub attribution to None ⇒ no refusal ⇒ RED |
| AH-2 | Marker line + record carry pid/exe/cwd/install/port POSITIVE content (field assertions) incl. `family`/`source` | **AH-M2** drop a field from the template ⇒ RED |
| AH-3 | proc-tables-absent fixture ⇒ NO refusal, loud WARNING, boot proceeds | **AH-M3** flip degrade→refuse ⇒ RED (control: AH-1 refuses — not always-degrade) |
| AH-4 | Claim ABSENT or stale (dead pid) + positive-foreign holder ⇒ refuse via HOLDER evidence alone | **AH-M4** gate refusal on claim-presence ⇒ RED (control: no holder + stale claim ⇒ take-over, prior M2 semantics intact) |
| AH-5 | Same-install holder (cwd == self root), live pid, lineage-shaped parent ⇒ NO refusal (readyz proceeds) — promote shape RE-AIMED at the holder path | **AH-M5** same-install ⇒ foreign ⇒ RED (AH analogue of prior M6) |
| AH-6 (6a landed) | Holder refusal exits pre-bind; extends `test_gate_refusal_never_enters_bind_retry` (bind_retry never called on the holder-refuse path) | **AH-M6** rewire to post-ladder ⇒ RED (deleted with a matrix note if leader ratifies 6b) |
| AH-7 | Structural: refusal fires before any bind/listen (gate ordering) + behavioral: holder port never sees a second bind | **AH-M7** move holder check after bind ⇒ RED |
| AH-8 | Holder starttime mismatch (recycled pid fixture) ⇒ treated stale ⇒ degrade, no refusal | **AH-M8** remove holder-path starttime check ⇒ RED |
| AH-9 (lineage, LDP-1) | Claim v2 WITH matching lineage hash ⇒ non-foreign (take) — promote-shape pin RE-EXPRESSED under the v2 format | **AH-M9** invert lineage comparison ⇒ refuse ⇒ RED |
| AH-10 (lineage) | Claim v2 lineage MISMATCH (both sides resolved) ⇒ refuse 78, source="claim-lineage" | **AH-M10** skip lineage check ⇒ take ⇒ RED |
| AH-11 (lineage) | Old-format claim (no lineage fields), same-install, holder alive ⇒ non-foreign (backward compat; prior promote-shape pin stays green unflipped) | **AH-M11** treat absent-lineage as foreign ⇒ RED |
| AH-12 (lineage) | Self-parent unreadable ⇒ non-foreign (degrade) | **AH-M12** unreadable ⇒ foreign ⇒ RED |
| AH-13 (parser) | Synthetic tcp/tcp6 tables: hex port decode correct; st==`0A` filter (a `06` TIME_WAIT row does NOT match); IPv6-only holder found via tcp6 | **AH-M13** break hex decode or drop the state filter ⇒ RED |
| AH-14 (two-sample) | Holder present in sample 1, absent in sample 2 ⇒ NO refusal (proceed) | **AH-M14** single-sample refuse ⇒ RED |
| HY-1 | `_pkill_overlay_violation("fuser -k 7979/tcp") == "7979"` (+ list-form + ambient-fence-allow + B6-overlay-refuse variants) | **HY-M1** remove the 7979 tuple entry ⇒ RED |
| HY-2 | proc_run inherits the 7979 token (shared predicate — one call through `proc_tools` guard shape) | **HY-M2** same mutant as HY-M1 ⇒ RED (proves no proc_tools fork exists) |
| HY-3 | bash refusal text contains the daemon-ambient channel sentence and does NOT contain "tool env" / `env={` | **HY-M3** revert text to the drifted wording ⇒ RED |
| HY-4 | proc_tools refusal text unchanged-correct (ambient-only sentence present) | **HY-M4** add the overlay sentence to proc_tools ⇒ RED |
| LH-1 (alert family) | Auth-config / DB-auth refusal sites write the family record best-effort; unresolvable data dir ⇒ log-only (no crash, no exit change) | **LH-M1** remove the best-effort guard (raise on write failure) ⇒ RED |
| LH-2 (alert family) | `surface_refusal_marker` ERROR line carries family/source fields; rotation still exactly-once (prior `test_surface_refusal_marker_reports_once_and_rotates` extended) | **LH-M2** drop fields / double-report ⇒ RED |
| LH-3 (watchdog leg) | Watchdog watch-set includes the family marker path; latch fires once per occurrence (mirrors `tests/test_watchdog_watcher.sh` harness shape) | **LH-M3** remove the watch leg ⇒ RED |

Non-vacuity controls: clean-tree green legs accompany every family (E2 S6 owns the matrix execution; sha256 PRE==POST restore proof per the existing matrix format).

## 9. Task Breakdown (developer-executable, ordered, single-owner)

| ID | Task | Outcome (testable) | Touchpoints | Depends on | Risks |
|----|------|--------------------|-------------|------------|-------|
| T1 | Create `daemon/services/port_holder.py`: `find_listener_holders`, `attribute_install_dir`, `confirm_holder`, `HolderRef` — injectable proc-root, degrade inventory (§5.6), two-sample helper | Unit-importable; green on synthetic `/proc` fixtures (AH-1/3/8/13/14 legs) | `daemon/services/port_holder.py` (NEW) | — | R1, R2 |
| T2 | Unit tests for the walk: synthetic tcp/tcp6 tables (hex decode, `0A` filter, IPv6), fd-scan races, recycled pid, absent tables | `tests/unit/test_port_holder_identity.py` green; container-CI safe (no real `/proc` dependency) | `tests/unit/test_port_holder_identity.py` (NEW) | T1 | R2 |
| T3 | Gate integration in `run_port_ownership_gate`: discovery hook, decision upgrade (D-A2-2), two-sample confirm, fast-refuse 6a (D-A2-6), marker payload `family`/`source`/`evidence` (§5.4) | Holder refusal exits 78 pre-bind with full marker; degrade paths proceed; gate pins AH-1..AH-8 green | `daemon/services/port_ownership.py` | T1 | R1, R3 |
| T4 | Claim v2 + lineage layer (LDP-1-GATED — skip entire task if leader picks option ii): additive fields, writer + reader tolerance, decision table (§5.3), lineage pins AH-9..AH-12 | Old-format claims behave byte-identically; v2 claims refuse lineage-mismatch; promote-shape pins green | `daemon/services/port_ownership.py`; `tests/unit/test_port_ownership_pins.py` | T3 (LDP-1) | R4 |
| T5 | Refusal-family record: extend `write_refusal_marker`/`surface_refusal_marker` payloads; best-effort writes at auth-config (:333-340) + DB-auth (:263-268) preflight sites; family fields in the exactly-once reader | LH-1/LH-2 green; every 78 site writes the record or degrades log-only | `port_ownership.py`; `daemon/__main__.py` (two preflight sites, additive only); B2 adopts the shape (cross-ref) | T3 | R5 |
| T6 | Watchdog file-watch leg (D-A2-8): family-marker source in the daemon-DOWN watch-set, latched | LH-3 green (watchdog harness shape); `bash -n` clean; NO launcher.sh/unit bytes touched | `scripts/watchdog-watcher.sh` | T5 | R6 |
| T7 | A1(a) 7979 token (`bash.py:2044`) + HY-1/HY-2 pins | 7979 refused via bash AND proc_run; 9797/9798 legs unchanged | `daemon/tools/bash.py`; `tests/unit/test_pkill_overlay_pins.py` | — | — |
| T8 | A1(b) refusal-text correction (`bash.py:1350-1360`) + HY-3/HY-4 pins | Corrected text pinned positively; no test flip needed (verified none exists) | `daemon/tools/bash.py`; new pins file or `test_pkill_overlay_pins.py` | — | — |
| T9 | Harness contract notes for E2: leg (b)/(c) holder-shape requirements (§7 H row) + the C1 claims-dir fix in `boot_rogue_mirror.sh` (set `ENSEMBLE_PORT_CLAIMS_DIR`) | E2's S7 legs executable as specified; harness refuse-foreign leg actually refuses | `.agents/shared/planning/rogue-containment-p2/` notes ONLY (harness edit is E2/developer scope, flagged C1) | T3 | R7 |
| T10 | Zero-migration + zero-ceremony verify: `git diff --stat` shows NO `daemon/migrations/versions/` file, NO launcher.sh/unit-template/live-.env byte, gate call block `__main__.py:449-461` unchanged | `git status`/`git diff` evidence in the lane report | — | T1-T8 | — |

## 10. Migration Expectation

**ZERO migrations.** Code-only: one new module, additive JSON fields on a filesystem claim file (no SQLModel table), additive marker-payload fields, one tuple entry + one string edit in bash.py, one watchdog watch leg. No `*.sql` in `daemon/migrations/versions/` (119 files, zero exceptions — repo rule). No `rollback_safe` adjudication change (additive logic ⇒ expected `rollback_safe=true source=explicit (fix-loss-only)`). **If any design step turns out to need a DB table: STOP and flag to the leader** (changes rollback_safe + ceremony scope).

## 11. Risks

| ID | Risk | Impact | Mitigation |
|----|------|--------|------------|
| R1 | Discovery misattributes a sanctioned holder as foreign ⇒ false 78 on a legitimate boot | critical (breaks a lane silently) | Positive-proof-only (D-A2-3): BOTH install-dirs resolved + different + liveness confirmed + two-sample; same-install never refuses without ratified lineage; 16-lane walk §7 re-affirmed per lane; AH-5/AH-3/AH-8 pins |
| R2 | `/proc` shape drift or container absence breaks the walk | medium (degrade to today's behavior) | Degrade-never-refuse inventory §5.6 (every step); synthetic-fixture tests (no live `/proc` dependency); AH-3 pin |
| R3 | Gate latency regresses the pre-bind path (U budget) | low | Walk is O(processes), tens of ms; two-sample only on the POSITIVE path before an exit (250 ms once); §6.3 accounting; E2 U-measurement measures it |
| R4 | Lineage false-refuse on an unrecognized-but-legitimate parent shape | high (LDP-1-gated) | Absent-lineage/unreadable-parent ⇒ non-foreign (AH-11/AH-12); basename-tuple survives pid churn and invocation style; amputation path: leader picks option (ii) ⇒ T4 drops out cleanly |
| R5 | Auth/DB preflight marker writes misbehave before data-dir resolution | medium (new failure mode at the earliest boot phase) | Best-effort only: write failure ⇒ log-only, exit code NEVER changes (LH-1); reuses the `api.py:722-727` precedence chain |
| R6 | Watchdog leg dead-ends (watcher not installed on this host) | medium (alert gap persists on THIS host) | Named honestly (LDP-4): in-daemon channels (journald marker + exactly-once next boot) remain primary; watchdog leg activates wherever installed; systemd-timer variant named as operator decision, NOT built here |
| R7 | Harness drift (C1): mirror refuse-foreign leg plants claims at the pre-B1 path ⇒ leg silently stops refusing | medium (false-green S7) | §14 C1 fix (explicit `ENSEMBLE_PORT_CLAIMS_DIR`); T9 hands the contract to E2; the leg-selector refuses unknown legs loudly (E2 § harness) |
| R8 | fast-refuse misfires during a legitimate cross-install same-port window | low (topology forbids it) | Each sanctioned install has its OWN port (8079/7979/9797; `lib.sh:1377-1378` refuses sandbox-on-demo-port "own-port discipline"); cross-install same-port is itself the misconfiguration the fence exists to catch — loud refusal + attribution is the correct verdict; residual disclosed (§15) |

## 12. Success Criteria (measurable)

- SC-1: A boot pointed at a scratch port held by a positive-foreign synthetic holder (claim absent) exits **78** pre-bind, within the U budget (≤ 58 s wall, measured by E2's cycle), with a marker line + record carrying pid/exe/cwd/install/port/family/source. (AH-1/2/6/7)
- SC-2: proc-tables-absent ⇒ NO refusal; loud WARNING; boot reaches readyz on the scratch port. (AH-3)
- SC-3: Stale claim + positive-foreign holder ⇒ refuse via holder evidence alone; no holder + stale claim ⇒ take-over (prior semantics byte-identical). (AH-4 + prior M2)
- SC-4: Same-install holder (lineage-shaped) ⇒ readyz, no refusal, claim taken — promote shape preserved under BOTH claim formats. (AH-5, AH-9, prior promote-shape pin unflipped)
- SC-5: (LDP-1 ratified) same-install lineage-MISMATCH ⇒ refuse 78, source="claim-lineage"; old-format claims never lineage-refuse. (AH-10/11)
- SC-6: Every exit-78 family site writes (or degrades log-only) the family record; next boot surfaces it exactly once with family/source. (LH-1/2)
- SC-7: The H1 residual table (§5.7) is documented in-code (module docstring) and in the mutation matrix; the claim-less same-install holder behavior is WARNING-attributed.
- SC-8: `fuser -k 7979/tcp` refused by bash AND proc_run unfenced; the bash refusal text names ONLY the daemon-ambient channel. (HY-1..4)
- SC-9: `git diff --stat`: zero launcher.sh / unit-template / live-.env bytes; zero migration files; `__main__.py:449-461` gate block unchanged.
- SC-10: All AH-/HY-/LH- mutants REDDEN their pins with sha256-verified restores + clean-tree control green (E2 S6).
- SC-11: Live 9797/9798/7979 and `ensemble_prod` untouched by every test leg (scratch ports only; conftest claims fence; G5 DB fence).

## 13. Leader Decision Points (map to analysis §9)

| # | Decision | This plan's position | What ratification changes |
|---|----------|----------------------|---------------------------|
| LDP-1 | **H1: close via claim-lineage (option i) vs re-declare residual (option ii)** (analysis §8 H1; commission item 4) | **CLOSE (option i)** — genuinely cheap (T4 ~1 task, additive JSON + one comparison), ceremony-invisible (zero launcher/unit/env/migration), preserves every prior pin via backward compat, and closes the claim-overwrite side-defect. Argued in D-A2-4/D-A2-5. | Ratify (i) ⇒ T4 + AH-9..12 in scope. Pick (ii) ⇒ T4/AH-9..12 amputated; H1 documented as narrowed residual (claim-present same-install rogues keep today's non-refuse); everything else in this plan stands unchanged |
| LDP-2 | **Fast-refuse timing: immediate-78 (6a) vs after-ladder (6b)** (commission item 3; E2 AH-6 specified both) | **6a IMMEDIATE** (D-A2-6): foreign proof cannot free the port; the ladder only feeds the burst budget; 78 pre-bind is quieter than exit-1 looping and the unit already stays down | Ratify 6a ⇒ AH-6 pin = pre-bind shape. Pick 6b ⇒ AH-6 swapped per E2's variant note; §6.3/§6.4 accounting updated (ladder burns 30 s per cycle again) |
| LDP-3 | **Operator-alert channel for the refusal family** (analysis §9.4; commission item 5) | **Family record + generalized exactly-once + watchdog file-watch leg** (D-A2-8) — built from the repo's OWN daemon-independent alert seam (ADR-025(b)); no new dependency; no launcher/unit surface | Confirm ⇒ T5/T6 in scope. Alternatives pre-rejected with reasons (D-A2-8 table); a push channel (Discord webhook) would be a NEW dependency decision above this lane |
| LDP-4 | **Watchdog installation on THIS host** (deployment, not code) | Named, NOT built: the watchdog is launchd-shaped, unscheduled here (verified); while-down alerting on this host stays journald + marker + next-boot surface | Operator-gated (U6 ladder): a systemd-timer variant is a separate ops lane; this plan only ensures the watched FILE exists |
| LDP-5 | **Lane-4 obsolescence evaluation** (analysis §5.4/§9.1; commission item 3) | Presented in §6.4: A2 removes the ensemble-holder loop classes; the trigger measurement (H2) stays armed and is expected to stay silent | Architect rules whether Lane 4 stays deferred-with-trigger (this plan's position) or is formally closed |
| (carried) | §9.3 exit code 78 reuse | §6.2 — no new point | — |

## 14. Contradictions (verified during evidence gathering)

1. **C1 — harness claims-dir drift vs B1 (verified):** `.agents/shared/planning/rogue-containment/harness/boot_rogue_mirror.sh` refuse-foreign leg plants the claim at `$ENSEMBLE_DATA_DIR/port-claims/<host>-<port>.json` — the pre-fix-round-2 install-scoped location. The SHIPPED gate reads the B1 host-global dir (`resolve_claims_dir`, `port_ownership.py:108-125`: `ENSEMBLE_PORT_CLAIMS_DIR` > `$XDG_RUNTIME_DIR/ensemble-port-claims` > `/tmp/ensemble-port-claims-<uid>`) and the harness never sets `ENSEMBLE_PORT_CLAIMS_DIR` ⇒ the planted claim is INVISIBLE to the gate ⇒ the refuse-foreign leg as written would NOT refuse. Fix (T9/E2): export `ENSEMBLE_PORT_CLAIMS_DIR="$SCRATCH_DIR/claims"` in the harness and plant there (mirrors the conftest fence, `tests/conftest.py:912-919`). Flagged for E2's S7 so the leg is not falsely green.
2. **C2 — commission "mirror in proc_tools.py guard :2580-2610" (non-contradiction, recorded for accuracy):** proc_tools does NOT carry its own predicate copy — it imports and calls the shared `_pkill_overlay_violation` (`proc_tools.py:2585-2587`). The 7979 token needs ONE edit (bash.py:2044); proc_tools inherits by construction (HY-2 proves it). No second edit site exists.
3. **C3 — "check whether test_pkill_overlay_pins.py pins that text" (verified answer):** it does NOT — the file (54 lines, 10 tests) pins the pure predicate only; a repo-wide tests/ grep for the refusal string finds zero hits. No pin flip; a NEW positive pin is added instead (HY-3/HY-4). Recorded so the developer does not hunt for a flip target.
4. **C4 — analysis marker-line anchor drift (non-load-bearing):** analysis §5.2's companion cite "launcher.sh:3814-3816" resolves at tip bf4bfde6e to the refuse case at `launcher.sh:3847-3848` (same branch, same shape: `_log` + `exit 78`, no `_notify_once` — :3853 tempfail DOES notify). E2's cite `port_ownership.py:605-623` for the marker line resolves to :623-635. Cited at verified lines here.

## 15. Assumptions & Open Questions

- **Assumption:** sanctioned topologies never share a port across installs (8079/7979/9797 own-port discipline; `lib.sh:1377-1378`). If a host deliberately co-hosts two installs on ONE port, A2's foreign-holder refusal fires on the second booter — treated as correct-by-design (loud + attributed), disclosed here as the R8 residual.
- **Assumption:** the launcher script path is stable at the install root across releases (releases swap binaries under `releases/`, not the launcher). If a future lane relocates launcher.sh WITHIN the same install root, recorded lineage hashes go stale ⇒ unknown ⇒ non-foreign (degrade; no false refuse) and the promote hand-over still works via staleness/staleness+retry.
- **Assumption (LDP-1):** parent-cmdline basename-tuple is an acceptable lineage identity. Stronger identity (full argv hash) false-refuses invocation-style variance; weaker (ppid equality) false-refuses `systemctl restart`. The chosen point is argued, not proven against all future parent shapes — the degrade rows make every miss non-refusing.
- **Open question (E2 coordination):** does E2's leg (c) holder-spawn shape guarantee the same parent cmdline for holder and daemon-under-test (§7 H-row contract)? If a leg needs a DIFFERENT-parent same-install holder, it must plant a v2 claim with a MATCHING recorded lineage instead of relying on live parents.
- **Open question:** family-record location — co-located with the claims dir (current `write_refusal_marker(claims_dir, ...)` call, :636 — N2 convention) vs the data dir (`api.py:722-727` precedence). This plan RECOMMENDS keeping the claims-dir co-location for the port family and having auth/DB sites write the SAME path via the claims-dir resolver (one namespace, one reader); flagging in case B2 prefers the data dir — ONE namespace must win (reader :490 resolves one path).
- **Open question (carried):** per-port family records vs single file — prior A §15 open question stands; with `family`/`source` fields a single rotating file is sufficient (records overwrite, prior refuses live in `.seen-*` rotation) — recommend single-file + rotation, leader may split per-port later without format change.

## 16. Cross-References

- **Governing analysis:** `.agents/shared/planning/bind-race/rogue-port-containment-analysis.md` — §5.2 (shape + MANDATORY COMPANION), §5.4 (Lane-4 defer/trigger), §5.5 (exit 78), §6 (binding principle), §7 (hardening), §8 H1, §9 (decisions 1/3/4).
- **Prior lane:** `rogue-containment/component-A-port-ownership.md` (mechanism being extended + fix-round-2 addenda B1/B3/B5/N3/N8), `component-B-spawn-boundary-scrub.md`, `component-E-regression-pins-and-sequencing.md` (16-lane census), `rogue-containment/harness/boot_rogue_mirror.sh` (C1).
- **p2 siblings (this directory):** `component-B-tier1-prod-dsn-fence.md` — B2 adopts the family-record shape for the prod-DSN refusal (its §4.8 exit-78 mechanics); ordering contract in its §9.1. `component-E2-pins-verification-measurement.md` — integrates AH-M1..M8 (as specified here) + AH-M9..M14/HY-*/LH-* into the matrices, runs the S-gates and the (D) U-measurement.
- **Code anchors (verified at bf4bfde6e):** `daemon/services/port_ownership.py` (:74, :108, :198-227, :263, :283-294, :297, :322, :346, :367, :441, :473-532, :535-570, :573, :587-649); `daemon/services/bind_retry.py` (:155-178, :181, :305, :381-393); `daemon/__main__.py` (:77-88, :175, :211-212, :263-268, :288-340, :391, :447, :449-461, :488-489); `daemon/api.py` (:720-751); `daemon/tools/bash.py` (:1331-1360, :1431, :1979-1985, :2021-2047); `daemon/tools/proc_tools.py` (:2580-2613); `launcher.sh` (:3509-3514, :3847-3855); `scripts/watchdog-watcher.sh` (:33-44, :361-367, :428-464, :610); `scripts/systemd/ensemble-daemon.service` (:110-111); `scripts/deploy.sh:118`; `scripts/ensemble-prod.plist:15`; `tests/conftest.py:912-919`; `tests/support/wedge_smoke.py:102-109`; `tests/unit/test_port_ownership.py` (26 tests); `tests/unit/test_port_ownership_pins.py` (18 tests); `tests/unit/test_pkill_overlay_pins.py` (54 lines).
- **Exemplars (mutation discipline):** `tests/unit/test_ari_pm_terminology_pins.py`, `tests/bind_retry_mutation_matrix.md`, `tests/unit/agents/test_aegis_security_family.py:2527-2564`, `tests/upgrade_test_lib.sh:69-102` (FAKE_HOME fence).

---

*End of component A2 plan. Design only — no code, no tests run, no repo writes except this file and its lane commit. All anchors re-verified at worktree tip bf4bfde6e on 2026-11-14.*

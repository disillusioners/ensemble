# Phase 3: The Three Installer Libraries

## Objective

Implement the **three installer libraries** — the executable half of the self-install skills:

```
agents/hacker/skills-template/fuzzing-toolchain.lib.sh
agents/cracker/skills-template/credential-bench.lib.sh
agents/sweeper/skills-template/sast-toolchain.lib.sh
```

Each library is a standalone, POSIX-shell-safe implementation of the charter install skeleton —
**hardened** with the sha256 verification of the fetched release asset that the charter template omits
— so that a lane can bring up its own toolchain inside the engagement sandbox with **no Docker, no
system packages, and no remote fetch-and-execute**.

---

## Entry Criteria

- **Phase 2 exited** — the three skill bodies exist, each declaring the FENCES block, the pre-flight
  detect step, the install-root idiom, the checksum step, the staged-config-then-promote flow, the
  readiness contract, the lock discipline, the attempt cap and the reversal section. **This phase
  implements against those bodies; it does not re-invent the contract.**
- The charter template is the working reference:
  `agents/charter/skills-template/install-mermaid-cli.{md,lib.sh}` — the only in-repo installer
  (C2/C3), including its `READINESS_PROBE` exit contract (0 warm / 1 cold / 2 in-progress), its
  BSD-portable `mkdir`-lock, and its `install` → `verify` → `promote` ordering.
- The checksum precedent to combine: `daemon/services/live_views.py:1357-1406` (hex `SHA256SUMS` +
  W3C-SRI base64).
- **No venv is required for this phase** — the libraries are shell; the *verification* of them happens
  in Phase 4/5.

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | **Write the shared skeleton into each of the three libraries** — identical structure in all three, specialised below the FENCES block: config path resolution, the pre-flight prereq check emitting `HARD_BLOCKER: <missing prereq>` and stopping (never auto-installing), the install-root resolution with **absolute-path capture**, the attempt counter, the lock acquire/release, the staged write + verify + promote flow, the readiness probe, the reversal section, and the `set -eu` header. | Phase 2 exited | All three share the same function names and the same order of operations. The skeleton is the charter's, not a new invention. |
| 2 | **Implement the FENCES block identically in all three** — NO Docker · NO system packages / apt · NO remote fetch-and-execute (no unpinned fetch-and-run CLI calls; `npx --no-install` is the only sanctioned npx form) · no plaintext secrets in any file, report or command echo · no environment-variable **values** in any report (names only) · honest-stop `HARD_BLOCKER` · idempotent (a warm host takes a verify-only fast path). **AM-8:** the library FENCES header uses the **neutral heading/comment form — no provenance parenthetical and no "per <agent> pattern, job <id>" shape** (charter's own `.lib.sh` header carries that annotation; it violates guide §10 and escapes today's 3-token pin list — T18's extended list now covers it). **AM-10 (secondary check):** each `.lib.sh` carries a FENCES header mentioning the **tool-agnostic keywords** — this is explicitly **NOT byte-identity** against the `.md` bodies' block (charter's `.lib.sh` header is a different, library-scoped 3-bullet block, so byte-identity with the `.md` block would be false against the precedent). | 1 | The `.md` bodies' FENCES **bullet lists** are byte-identical across the three (T23 primary, AM-10 scope); each `.lib.sh` FENCES header names the tool-agnostic keywords (T23 secondary, AM-10); the heading forms carry **no provenance annotation** (AM-8). |
| 3 | **Implement the sha256 release-asset verification** — after fetching an asset, compute its sha256 and compare against the expected digest from the upstream `SHA256SUMS` file (hex) and/or the W3C-SRI base64 form; a mismatch ⇒ **abort, delete the staged file, do not promote**. The expected digests are recorded per pinned tool version in the library's config block. | 1, 2 | Every library has a checksum step that runs **before** promotion; the mismatch path is a non-zero exit with no promoted artifact. This closes the charter template's checksum gap (C3). |
| 4 | **Implement the readiness probe** per tool, returning **0 warm / 1 cold / 2 in-progress** — `0` only on real evidence (the tool binary is present at an absolute path, is executable, and the tool answers a version/evidence query), `1` when cold, `2` when the install lock is held by another process. The lock is `mkdir`-based (BSD-portable, no hard `flock`). On `0` the library exports the tool's absolute path for the caller. | 1 | Each library exposes a probe with the three-state contract; a cold host returns 1, never 0; a lock-held host returns 2. |
| 5 | **Implement the staged → verify → promote flow with a release lock whose release runs on failure** — the config is written to a `mktemp` location with a trap; promotion (`mv`) happens **only after** verification passes; the lock is released on every exit path (including the failure path); the attempt counter caps self-heal at 2. | 1 | A failed verification leaves **no** promoted artifact and **no** held lock. The trap-based release is the charter precedent ("lock release MUST run on failure"). |
| 6 | **Specialise `fuzzing-toolchain.lib.sh`** — *(the P3 carrier of **AM-13**: this task's sandbox-relative-write-only property is the library half of the copy-then-build data-flow contract — the library never writes outside the sandbox root it is handed, so the target tree is never a write target from the library side; the persona/workflow halves live in P1/P2)* — the toolchain bring-up for a C++17 keystroke-stream engine: the harness build (`clang`/`clang++` with `-fsanitize=fuzzer` for the **libFuzzer** primary path; the **AFL++** secondary path for non-instrumented / black-box builds), the compiler-toolchain detection with an absolute path capture, corpus-directory creation inside the engagement sandbox (the library creates **only** sandbox-relative paths — it must never write outside the sandbox root it is given), and the campaign launch/stop surface. **AM-12: AFL++ parallel mode MUST be OFF** (or the harness traps and kills its children on exit) — detached/setsid grandchildren are a known `proc` residual (`proc_tools.py:1680-1690`) and would escape both the 10-tracked-process cap and the explicit teardown. | 1–5 | The library never writes outside the sandbox root passed to it; the two fuzzing paths are distinguishable; the build flags are pinned, not floating; **AFL++ runs single-core (parallel mode off) or the harness guarantees child reaping on exit.** |
| 7 | **Specialise `credential-bench.lib.sh`** — the bench bring-up for the protection-strength lane: the tool detection (hashcat / john), the **synthetic-corpus discipline** (the library creates only corpora it is asked to generate from a supplied generator; it never downloads wordlists or real-world hash sets — that would breach the sample-data-only rule), the bench invocation surface, and the result-parsing surface. | 1–5 | No network fetch of any wordlist/hash corpus is possible from this library; the corpus input path is always inside the sandbox. |
| 8 | **Specialise `sast-toolchain.lib.sh`** — the scanner bring-up for the SAST lane: `semgrep`, `gitleaks`, `cppcheck`, `clang-tidy` (needs `compile_commands.json` — the library **detects and reports** its absence as a `HARD_BLOCKER` rather than generating one), `trivy` for the CI supply chain leg; the SARIF/JSON output surface, written into the sandbox `out/` directory. | 1–5 | Each scanner's absence is reported per-tool, not as a blanket failure; the output destination is sandbox-relative. |
| 9 | **Shell-safety pass** — the libraries are invoked through the `bash` tool, where a **string** command runs under `/bin/sh` (D10). Therefore: **POSIX-sh constructs only** — no arrays, no `[[ ]]`, no process substitution, no `local -a`, no `mapfile`; `set -eu` at the top; every pipeline and loop lives **in the file**, never in an inline command string; scripts are invoked by absolute path. Then run `sh -n` on each library as a syntax check. | 1–8 | `sh -n <lib>` exits 0 for all three; a grep for the GNU-only constructs returns zero hits. |
| 10 | **Reversal + honesty pass** — each library's reversal (uninstall) section removes what it installed and leaves the host as it found it; no report line contains a secret or an environment-variable **value**. | 1–9 | The reversal section is present and explicit in all three; the report surfaces carry names only. |

---

## Gates

| Gate | Requirement | How it is checked |
|---|---|---|
| **G3.1 — FENCES identical** | The three **`.md` bodies' FENCES bullet lists** are byte-identical (T23 primary, AM-10 scope: bullet list only, checksum bullet in, per-tool content out), and each `.lib.sh` carries a FENCES header naming the tool-agnostic keywords (T23 secondary — explicitly not byte-identity vs the `.md` block). | T23 (the pytest assertion lands in P4; the manual diff is the author-side check) |
| **G3.2 — checksum before promote** | Each library verifies the fetched asset's sha256 before any promotion, and aborts cleanly on mismatch. | Per-library inspection against task 3 |
| **G3.3 — readiness contract** | Each library exposes a probe returning 0 warm / 1 cold / 2 in-progress, with a `mkdir`-based lock. | Per-library inspection against task 4 |
| **G3.4 — release on failure** | The lock is released on every exit path; a failed verification promotes nothing. | Per-library inspection against task 5 |
| **G3.5 — attempt cap** | The self-heal attempt counter is capped at 2. | Per-library inspection against task 1 |
| **G3.6 — reversal present** | Each library has a mandatory reversal (uninstall) section. | Per-library inspection against task 10 |
| **G3.7 — POSIX-sh clean** | `sh -n` passes on all three; no GNU-only construct is present. | `sh -n` + the construct grep |
| **G3.8 — sandbox containment** | Each library writes only inside the sandbox root it is handed; no absolute path outside it is a write target. | Per-library inspection against tasks 6–8 |
| **G3.9 — no network corpus** | The credential bench cannot fetch wordlists/real hash sets. | Task 7 inspection |
| **G3.10 — nothing else written** | Only the three `.lib.sh` files changed in this phase. | `git status --porcelain` |
| **G3.11 — guide checklist holds for the skill bodies** | The Phase-0 checklist rows targeting the three skill bodies still hold **after** the libraries land (the bodies and the libraries agree), and the §3 closure grep over the three `skills-template/` directories is clean or every survivor is on the enumerated exemption list (the `.lib.sh` read targets). | The Phase-0 checklist's skill-body columns + the closure grep |

---

## Verification Commands

```bash
cd /home/nea/ensemble-src-wt-aegis-security-family

# the phase's file set
ls -la agents/hacker/skills-template/fuzzing-toolchain.lib.sh \
       agents/cracker/skills-template/credential-bench.lib.sh \
       agents/sweeper/skills-template/sast-toolchain.lib.sh

# POSIX-sh syntax check (the bash tool runs string commands under /bin/sh — D10)
for f in agents/hacker/skills-template/fuzzing-toolchain.lib.sh \
         agents/cracker/skills-template/credential-bench.lib.sh \
         agents/sweeper/skills-template/sast-toolchain.lib.sh; do
  sh -n "$f" && echo "sh -n OK: $f"
done

# GNU-only construct sweep (must be empty)
grep -n '\[\[\|mapfile\|readarray\|local -a\|<\(' \
  agents/hacker/skills-template/fuzzing-toolchain.lib.sh \
  agents/cracker/skills-template/credential-bench.lib.sh \
  agents/sweeper/skills-template/sast-toolchain.lib.sh || true

# the mandatory contract elements, per library
for f in agents/hacker/skills-template/fuzzing-toolchain.lib.sh \
         agents/cracker/skills-template/credential-bench.lib.sh \
         agents/sweeper/skills-template/sast-toolchain.lib.sh; do
  echo "--- $f"
  grep -c 'FENCES' "$f"                      # fences block present
  grep -c 'sha256\|shasum' "$f"              # checksum step
  grep -c 'readiness_probe' "$f"             # readiness probe
  grep -c 'HARD_BLOCKER' "$f"                # honest-stop envelope
  grep -c 'REVERSAL\|uninstall\|reversal' "$f"  # reversal section
done

# W1 (plan council): T23's PRIMARY pin target is the three .md bodies' FENCES BULLET LISTS
# (AM-10/D-1: byte-identity lives in the .md bodies, bullet-list-only scope — checksum bullet IN,
# per-tool content OUT). The author-side check extracts the bullet list from each .md and asserts
# a single distinct block. The .lib.sh headers are checked ONLY for keyword presence (secondary,
# explicitly NOT byte-identity vs the .md block).
for f in agents/hacker/skills-template/fuzzing-toolchain.md \
         agents/cracker/skills-template/credential-bench.md \
         agents/sweeper/skills-template/sast-toolchain.md; do
  awk '/^## Fences/{f=1;next} /^## /{f=0} f && /^- /' "$f"   # extract the FENCES bullet list per .md
done | sort -u | wc -l     # PRIMARY: expect 1 (one distinct bullet list across the three .md bodies)
for f in agents/hacker/skills-template/fuzzing-toolchain.lib.sh \
         agents/cracker/skills-template/credential-bench.lib.sh \
         agents/sweeper/skills-template/sast-toolchain.lib.sh; do
  grep -c 'Docker\|system packages\|fetch-and-execute\|HARD_BLOCKER\|idempotent' "$f"   # SECONDARY: keyword presence only
done                        # every library must score > 0 — NOT a byte-identity check

# shellcheck, if present on this host (optional — report if absent, do not install)
command -v shellcheck >/dev/null 2>&1 && shellcheck -s sh agents/*/skills-template/*.lib.sh || echo "shellcheck absent — not installed, no system packages installed"
```

> **House note:** `shellcheck` is optional. If it is absent, **report the absence and move on** — do
> **not** install it (no system packages without reporting first; no Docker on this host). The
> mandatory syntax check is `sh -n`, which needs nothing beyond the base system.

---

## Coupling

- **Tight with:** P2 — the bodies authored in P2 declare the contract; these libraries implement it.
  Any disagreement is resolved by editing **both** (the bodies are prompt surface and are hashed by the
  baseline, so a divergence must be fixed before the P4 re-stamp).
- **Tight with:** P4 — the libraries are part of the one commit and are hashed by the baseline re-stamp
  (as `skills-template/*` files they contribute **zero** baseline rows, but they are still inside
  `agents/**`, so they must be final **before** the re-stamp).
- **Loose with:** P1 — aegis's skill body is the structural template; no direct code dependency.
- **Independent of:** P5/P6 — P5 verifies and P6 ships; neither depends on this phase's internals.

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| **P3-R1** | The three installer artifacts drift — the FENCES content diverges, or one gains a check the others lack. | Medium | Medium | T23 (AM-10/D-1) pins the **three `.md` bodies' FENCES bullet lists** byte-identical (PRIMARY; the author-side extract → `sort -u \| wc -l` == 1 check above); the `.lib.sh` headers are keyword-presence-checked only (SECONDARY — explicitly not byte-identity, per the charter precedent where the `.lib.sh` header is a different, library-scoped block). Divergence is visible in seconds. The mitigation is a **test**, deliberately, not a shared file (a shared library would have to be reachable by path — fragile across worktree vs installed tree, and a prompt-prose hazard). |
| **P3-R2** | A library performs a **remote fetch-and-execute** (running a downloaded binary without verification) — exactly the hygiene the charter template guards. | **High** | Medium | The checksum step (task 3) runs **before** promotion and aborts on mismatch; the FENCES block forbids unpinned fetch-and-run; the promoted artifact is always an absolute path to a verified binary. |
| **P3-R3** | A library installs outside user-space (system paths, `apt`, `pip install --user` into a system prefix) — a house-rule breach. | **High** | Low | The install root is resolved to a user-space path with absolute-path capture; the FENCES block forbids system packages; the `HARD_BLOCKER` path stops instead of escalating privileges. |
| **P3-R4** | The libraries assume GNU shell features and break under `/bin/sh` (the `bash` tool's string-command semantics). | Medium | Medium | Task 9's POSIX-only constraint + `sh -n` on all three + the GNU-construct grep. |
| **P3-R5** | The `cracker` library pulls a real wordlist or hash corpus — breaching the sample-data-only rule through the back door. | **High** | Low | Task 7 forbids any corpus download; the corpus input path is always sandbox-relative and self-generated; Gate G3.9 checks it explicitly. |
| **P3-R6** | A library writes outside the sandbox root (e.g. a fixed `/tmp/aegis-…` path). | Medium | Medium | Gate G3.8: every write target is derived from the sandbox root handed to the library; no absolute write target appears in any library. |

---

## Effort

**≈ 1 – 2 working days** — the heaviest engineering phase of the lane. Three libraries, each with the
full skeleton plus a tool-specific specialisation; the checksum verification and the three-state
readiness probe are the two parts with no charter precedent to copy and therefore the two that need
the most care.

---

## Exit Criterion

Phase 3 is done when **all eleven gates (G3.1–G3.11) are green**:

1. the three **`.md` bodies' FENCES bullet lists** are byte-identical (AM-10 scope) and each `.lib.sh`
   carries a keyword-naming FENCES header (secondary — not byte-identity); the heading forms carry no
   provenance annotation (AM-8);
2. each library verifies the fetched asset's sha256 **before** promoting anything, and aborts cleanly
   on mismatch;
3. each library exposes a readiness probe with the 0/1/2 contract and a `mkdir`-based lock;
4. the release lock is released on every exit path and a failed verification promotes nothing;
5. the attempt counter caps self-heal at 2;
6. each library carries a mandatory reversal section;
7. `sh -n` passes on all three and no GNU-only construct is present;
8. every write target is derived from the sandbox root handed to the library;
9. the credential bench has no path to fetch a wordlist or real hash corpus;
10. `git status --porcelain` shows changes only under the three `skills-template/` directories;
11. the Phase-0 checklist rows for the three skill bodies still hold, and the closure grep over the
    three `skills-template/` directories is clean or every survivor is on the enumerated exemption list.

Phase 4 then lands the whole lane in **one commit** with the regenerated baseline.

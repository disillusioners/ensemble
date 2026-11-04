---
version: 1.0.0
category: execution
auto_load: false
---

# fuzzing-toolchain — provision the coverage-guided fuzzing toolchain

I provision the LOCAL toolchain the offensive lane uses to fuzz a C++17 engine that ingests raw untrusted keystroke streams: the **libFuzzer primary path** (sanitizer-plus-fuzzer instrumentation built with the clang toolchain) and the **AFL++ secondary path** for non-instrumented / black-box targets. Everything is user-space — no Docker, no system packages, no system toolchain touch.

This skill is **file-invoked**: it is read from its owning lane's skill directory and never enters the shared skill bank.

## Fences

- **Write-target classes.** Every write lands in exactly one class: class (i) the persistent user-space toolchain root (`~/.local/opt/aegis-toolbench`) at install time — idempotent, checksum-verified; class (ii) the per-engagement sandbox root at run time — copy-then-build, the target tree is never a write target; class (iii) everything else — target codebases included — is never written, at any time, by any path.
- **NO Docker.** Docker is absent from this host and must never be installed or used; the toolchain is user-space only.
- **NO system packages.** No `apt`, no `sudo`, no system-prefix installs, no system Node touch; every dependency resolves user-space, and a missing prerequisite is a stop, never an install.
- **NO remote fetch-and-execute.** No unpinned fetch-and-run calls of any kind: a fetched asset is downloaded, sha256-verified, and only then promoted; `npx --no-install` is the only sanctioned package-runner form (it resolves an installed copy or refuses loudly — it never fetches).
- **sha256 before promote.** The fetched release asset's sha256 is computed and compared against the pinned expected digest before any promotion; a mismatch aborts, deletes the staged file, and promotes nothing; an unset pin refuses the install outright.
- **No plaintext secrets.** No plaintext secrets in any file, report, or command echo; secret-shaped material is referenced by class and location, never by value.
- **No environment-variable values.** Reports carry variable names only — never a value.
- **HONEST-STOP on hard blockers.** A missing prerequisite emits `HARD_BLOCKER: <prereq>` and stops — no retries, no fallback install, no privilege escalation.
- **Idempotent.** A warm host takes a verify-only fast path: no install call, no state write.

## Pre-flight detect (mandatory first step)

```sh
# Prereq check — every missing one is a HARD_BLOCKER, never auto-install
command -v curl        # release-asset download
command -v sha256sum || command -v shasum   # checksum verification
command -v clang       # libFuzzer primary path (system toolchain prereq)
command -v clang++     # harness builds
```

If any is missing, emit the structured envelope and stop:

```
HARD_BLOCKER: <missing prereq>
```

No retries, no fallback install, no privilege escalation. The compilers are prerequisites, not install targets — this skill never installs a system toolchain.

## Resolve the toolchain (steps)

### Step 0 — Source the lib + warm-probe fast path (idempotency gate)

```sh
# Source the library (repo-root-relative form; on an install tree use the
# absolute path under the agent's skill directory), then take the fast
# path: a warm probe means the toolchain is already provisioned —
# verify-only, install nothing.
. agents/hacker/skills-template/fuzzing-toolchain.lib.sh
if aegis_readiness_probe; then
    echo "probe warm — verify-only fast path, no install"
    exit 0
fi
# rc=1 cold or rc=2 install-in-progress-other falls through to install
```

### Step 1 — Install root + absolute-path capture

```sh
AEGIS_TOOLBENCH_ROOT="${AEGIS_TOOLBENCH_ROOT:-$HOME/.local/opt/aegis-toolbench}"
mkdir -p "$AEGIS_TOOLBENCH_ROOT"/{bin,lib,share}
# Non-interactive shells do not source ~/.bashrc — capture absolute
# paths explicitly, never rely on PATH lookups.
CLANG_BIN="$(command -v clang)"
AFLPLUS_BIN="$AEGIS_TOOLBENCH_ROOT/bin/afl-fuzz"
```

### Step 2 — Fetch the pinned release asset (AFL++, major-pinned)

```sh
AFLPP_VERSION="4.10c"
AFLPP_TARBALL_URL="https://github.com/AFLplusplus/AFLplusplus/releases/download/v4.10c/aflplusplus-4.10c.tar.gz"
AFLPP_SHA256="pin-before-first-install"   # from the vendor's SHA256SUMS; REQUIRED
```

The sha256 pin is **mandatory**: fill it from the vendor's checksum file at pin time. An unset or placeholder pin makes the library refuse the install (`HARD_BLOCKER: missing sha256 pin`) — an unpinned fetch is never an acceptable state.

### Step 3 — Download → verify → promote

```sh
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"; aegis_release_install_lock' EXIT INT TERM
curl -fsSL -o "$STAGE/aflpp.tar.gz" "$AFLPP_TARBALL_URL"
# sha256 gate BEFORE any promotion — mismatch aborts, deletes, promotes nothing
aegis_verify_sha256 "$STAGE/aflpp.tar.gz" "$AFLPP_SHA256" || {
    echo "CHECKSUM MISMATCH — staged asset discarded, nothing promoted"
    exit 1
}
tar -xzf "$STAGE/aflpp.tar.gz" -C "$STAGE"
cp "$STAGE"/aflplusplus-*/afl-fuzz "$AEGIS_TOOLBENCH_ROOT/bin/afl-fuzz" 2>/dev/null || true
chmod 0755 "$AEGIS_TOOLBENCH_ROOT/bin/afl-fuzz"
```

When the vendor ships source-only for a release, the pre-built binary is unavailable — that is a `HARD_BLOCKER: no verified release binary for <tool>@<pin>`, never a source-build from an unpinned tree.

### Step 4 — Staged config → verify → promote (probe-visible config LAST)

```sh
TMPCFG="$(mktemp)"
printf 'AFLPP_BIN=%s\nAFLPP_VERSION=%s\nCLANG_BIN=%s\n' \
    "$AFLPLUS_BIN" "$AFLPP_VERSION" "$CLANG_BIN" > "$TMPCFG"
# verify: the probe answers with real evidence before the config is promoted
aegis_verify_install "$TMPCFG" || { echo "VERIFY FAILED — config discarded"; exit 1; }
mv "$TMPCFG" "$HOME/.config/aegis-toolbench/fuzzing.env"
trap - EXIT
aegis_release_install_lock
```

The probe-visible config is written **only after** verify passes — a config written before verify would let a broken partial install satisfy the probe forever (false-warm, cold-detect never re-fires).

### Step 5 — Attempt cap + lock lifecycle

Self-heal runs at most **TWICE per session**; beyond the cap the cold path stops attempting and reports. The install lock is directory-based and released on **every** exit path — success, verify failure, checksum mismatch — via the trap above. A lock older than the staleness window is treated as abandoned and cleared.

## Campaign surface (run time — class (ii) only)

```sh
# libFuzzer primary: instrumented build inside the sandbox build tree
"$CLANG_BIN" -fsanitize=fuzzer,address -g -O1 \
    -o "$SANDBOX_ROOT/builds/fuzz_keystroke" "$SANDBOX_ROOT/builds/harness.cpp"
# bounded campaign as a tracked background process; parallel mode OFF
"$SANDBOX_ROOT/builds/fuzz_keystroke" "$SANDBOX_ROOT/corpus/" \
    -max_total_time=3600 -artifact_prefix="$SANDBOX_ROOT/out/"
# AFL++ secondary: single-core, no fork-server children beyond the harness
"$AFLPLUS_BIN" -i "$SANDBOX_ROOT/corpus/" -o "$SANDBOX_ROOT/out/afl" \
    -- "$SANDBOX_ROOT/builds/target" 2>&1 > "$SANDBOX_ROOT/logs/afl.log"
```

Fuzzer parallel mode stays **off** — a detached child that outlives the harness escapes both the ten-process cap and the explicit teardown (a known residual of the process lane).

## Failure path (lock release is mandatory)

On ANY failure exit — `HARD_BLOCKER` prereq, checksum mismatch, install error, verify failure — the lock release runs before stopping (the trap carries it). The session attempt counter is deliberately NOT reset on failure: it is what caps self-heal at 2 so a host that can never succeed stops flailing.

## Reversal

```sh
# 1. Remove the toolchain pieces this skill installed
rm -f "$HOME/.config/aegis-toolbench/fuzzing.env"
rm -f "$AEGIS_TOOLBENCH_ROOT/bin/afl-fuzz"
# 2. Remove the session state + lock residue
rm -f "$HOME/.cache/aegis-toolbench/fuzzing-session-state"
rm -rf "$HOME/.cache/aegis-toolbench/fuzzing-install.lock"
# 3. The system clang/clang++ were prerequisites — untouched, left in place
# 4. The library itself stays with the agent tree it ships in
```

## READINESS_PROBE (contract)

The three-state probe lives in the library; this skill gates everything on it.

| Signal | Source | Why |
|---|---|---|
| 1. Config exists + parses | the install's own config file | PATH lookups are unreliable in non-interactive shells |
| 2. Recorded binaries are absolute + executable | the config + `[ -x ]` | a moved or partial install must not read as warm |
| 3. The binaries answer a version/evidence query | real execution | existence is not evidence |

Exit codes: `0` warm (paths exported, ready to campaign) · `1` cold (invoke this skill behind the lock) · `2` install-in-progress-other (another lane holds the lock — degrade this turn, retry next).

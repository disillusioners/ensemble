---
version: 1.0.0
category: execution
auto_load: false
---

# sast-toolchain — provision the static-analysis scanner suite

I provision the LOCAL scanner suite the static-analysis lane sweeps with: **semgrep** (taint/injection patterns), **gitleaks** (secret detection — detect-only posture), **cppcheck** + **clang-tidy** (C/C++ memory safety and undefined behavior), and **trivy** (dependency + CI supply chain). All user-space, all release-asset installs with pinned majors.

This skill is **file-invoked**: it is read from its owning lane's skill directory and never enters the shared skill bank.

## Fences

- **Write-target classes.** Every write lands in exactly one class: class (i) the persistent user-space toolchain roots at install time — the toolbench root (`~/.local/opt/aegis-toolbench`) plus its state root (`~/.cache/aegis-toolbench`), its config root (`~/.config/aegis-toolbench`) and its staging root (a private `mktemp -d` scratch dir); every one of those roots is env-overridable, and every one of them is a class-(i) path — idempotent, checksum-verified; class (ii) the per-engagement sandbox root at run time — copy-then-build, the target tree is never a write target; class (iii) every other user path — the target codebase included — is never written, at any time, by any path.
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
# cppcheck/clang-tidy note: the compile database (compile_commands.json)
# is DETECTED, never generated — absence is reported per-tool, not fixed
```

If a prereq is missing, emit the structured envelope and stop:

```
HARD_BLOCKER: <missing prereq>
```

No retries, no fallback install, no privilege escalation.

## Resolve the toolchain (steps)

### Step 0 — Source the lib + warm-probe fast path (idempotency gate)

```sh
# Source the library (repo-root-relative form; on an install tree use the
# absolute path under the agent's skill directory), then take the fast
# path: a warm probe means the suite is already provisioned —
# verify-only, install nothing.
. agents/sweeper/skills-template/sast-toolchain.lib.sh
if aegis_readiness_probe; then
    echo "probe warm — verify-only fast path, no install"
    exit 0
fi
# rc=1 cold or rc=2 install-in-progress-other falls through to install
```

### Step 1 — Install root + absolute-path capture

```sh
AEGIS_TOOLBENCH_ROOT="${AEGIS_TOOLBENCH_ROOT:-$HOME/.local/opt/aegis-toolbench}"
AEGIS_STATE_HOME="${AEGIS_STATE_HOME:-$HOME/.cache/aegis-toolbench}"
AEGIS_CONFIG_HOME="${AEGIS_CONFIG_HOME:-$HOME/.config/aegis-toolbench}"
# every class-(i) root is env-overridable and created before any write
# lands in it — a config promote into a missing root is a failure, never
# a surprise directory elsewhere
mkdir -p "$AEGIS_TOOLBENCH_ROOT"/{bin,share} "$AEGIS_STATE_HOME" "$AEGIS_CONFIG_HOME"
# Non-interactive shells do not source ~/.bashrc — capture absolute
# paths explicitly, never rely on PATH lookups.
SEMGREP_BIN="$AEGIS_TOOLBENCH_ROOT/bin/semgrep"
GITLEAKS_BIN="$AEGIS_TOOLBENCH_ROOT/bin/gitleaks"
CPPCHECK_BIN="$AEGIS_TOOLBENCH_ROOT/bin/cppcheck"
CLANG_TIDY_BIN="$(command -v clang-tidy)"   # system toolchain prereq — detected, never installed
TRIVY_BIN="$AEGIS_TOOLBENCH_ROOT/bin/trivy"
```

`clang-tidy` ships with the system clang toolchain — it is a **prerequisite**, detected by absolute path; this skill never installs a system toolchain.

### Step 2 — Fetch the pinned release assets (major-pinned)

```sh
SEMGREP_VERSION="1.x-latest-pinned"
SEMGREP_URL="<pinned-release-asset-url>"
SEMGREP_SHA256="pin-before-first-install"   # from the vendor's SHA256SUMS; REQUIRED

GITLEAKS_VERSION="8.x-latest-pinned"
GITLEAKS_URL="<pinned-release-asset-url>"
GITLEAKS_SHA256="pin-before-first-install"

CPPCHECK_VERSION="2.x-latest-pinned"
CPPCHECK_URL="<pinned-release-asset-url>"
CPPCHECK_SHA256="pin-before-first-install"

TRIVY_VERSION="0.x-latest-pinned"
TRIVY_URL="<pinned-release-asset-url>"
TRIVY_SHA256="pin-before-first-install"
```

The sha256 pins are **mandatory**: fill each from the vendor's checksum material at pin time. An unset or placeholder pin makes the library refuse that tool's install (`HARD_BLOCKER: missing sha256 pin`) — an unpinned fetch is never an acceptable state, and a vendor without a verified binary for the pin is a `HARD_BLOCKER: no verified release binary for <tool>@<pin>`.

### Step 3 — Download → verify → promote (per tool)

```sh
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"; aegis_release_install_lock' EXIT INT TERM
curl -fsSL -o "$STAGE/gitleaks.tar.gz" "$GITLEAKS_URL"
# sha256 gate BEFORE any promotion — mismatch aborts, deletes, promotes nothing
aegis_verify_sha256 "$STAGE/gitleaks.tar.gz" "$GITLEAKS_SHA256" || {
    echo "CHECKSUM MISMATCH — staged asset discarded, nothing promoted"
    exit 1
}
tar -xzf "$STAGE/gitleaks.tar.gz" -C "$STAGE"
cp "$STAGE"/gitleaks "$GITLEAKS_BIN"
chmod 0755 "$GITLEAKS_BIN"
# (same download → sha256-verify → promote shape for semgrep, cppcheck, trivy)
```

### Step 4 — Staged config → verify → promote (probe-visible config LAST)

```sh
# staged config lives INSIDE the staging root (a class-(i) path) — never
# a bare mktemp in /tmp (that would be a class-(iii) write)
TMPCFG="$STAGE/staged-config"
printf 'SEMGREP_BIN=%s\nGITLEAKS_BIN=%s\nCPPCHECK_BIN=%s\nCLANG_TIDY_BIN=%s\nTRIVY_BIN=%s\n' \
    "$SEMGREP_BIN" "$GITLEAKS_BIN" "$CPPCHECK_BIN" "$CLANG_TIDY_BIN" "$TRIVY_BIN" > "$TMPCFG"
# verify: every recorded binary answers a version/evidence query before promotion
aegis_verify_install "$TMPCFG" || { echo "VERIFY FAILED — config discarded"; exit 1; }
# the mv is GUARDED: a failed promote removes the staged config and stops
# — it never leaves a stray file and never writes the probe-visible path
mv "$TMPCFG" "$AEGIS_CONFIG_HOME/sast.env" || {
    rm -f "$TMPCFG"
    echo "HARD_BLOCKER: config promote failed — probe stays cold"
    exit 1
}
trap - EXIT
aegis_release_install_lock
```

The probe-visible config is written **only after** verify passes — a config written before verify would let a broken partial install satisfy the probe forever.

### Step 5 — Attempt cap + lock lifecycle

Self-heal runs at most **TWICE per session**; beyond the cap the cold path stops attempting and reports. The install lock is directory-based and released on **every** exit path — success, verify failure, checksum mismatch — via the trap above. A lock older than the staleness window is treated as abandoned and cleared.

## Sweep surface (run time — class (ii) only)

```sh
# scanners run against the COPIED tree in the sandbox; output via script
# redirection into the sandbox out directory — never the target tree.
# EVERY output destination is guarded by aegis_scanner_output_ok BEFORE
# its scanner runs: an out-of-sandbox destination (".."-traversal, a
# symlinked parent, or a compound symlink-then-".." path) is a
# HARD_BLOCKER and the sweep does not run. The out root is created
# first so a missing parent is never misread as an out-of-sandbox
# refusal. Each write then acts on the CANONICAL destination resolved
# once by aegis_canonical_output_path — the raw path is never handed
# back to the kernel at the write, so no second resolution runs between
# the check and the write and a post-guard swap of the raw path cannot
# redirect the write.
mkdir -p "$SANDBOX_ROOT/out"
aegis_scanner_output_ok "$SANDBOX_ROOT/out/semgrep.json" "$SANDBOX_ROOT" || {
    echo "HARD_BLOCKER: scanner output destination refused — the sweep does not run"
    exit 1
}
_out="$(aegis_canonical_output_path "$SANDBOX_ROOT/out/semgrep.json" "$SANDBOX_ROOT")" || {
    echo "HARD_BLOCKER: scanner output destination refused — the sweep does not run"
    exit 1
}
"$SEMGREP_BIN" scan --config <pinned-pack> --json \
    "$SANDBOX_ROOT/builds/src" > "$_out" 2>&1
aegis_scanner_output_ok "$SANDBOX_ROOT/out/gitleaks.json" "$SANDBOX_ROOT" || {
    echo "HARD_BLOCKER: scanner output destination refused — the sweep does not run"
    exit 1
}
_out="$(aegis_canonical_output_path "$SANDBOX_ROOT/out/gitleaks.json" "$SANDBOX_ROOT")" || {
    echo "HARD_BLOCKER: scanner output destination refused — the sweep does not run"
    exit 1
}
# --redact: secret VALUES never enter the JSON report (the no-plaintext
# fence) — the finding names the rule and the location only
"$GITLEAKS_BIN" detect --redact --source "$SANDBOX_ROOT/builds/src" \
    --report-format json --report-path "$_out"
aegis_scanner_output_ok "$SANDBOX_ROOT/out/cppcheck.xml" "$SANDBOX_ROOT" || {
    echo "HARD_BLOCKER: scanner output destination refused — the sweep does not run"
    exit 1
}
_out="$(aegis_canonical_output_path "$SANDBOX_ROOT/out/cppcheck.xml" "$SANDBOX_ROOT")" || {
    echo "HARD_BLOCKER: scanner output destination refused — the sweep does not run"
    exit 1
}
"$CPPCHECK_BIN" --enable=all --xml \
    "$SANDBOX_ROOT/builds/src" 2> "$_out"
# clang-tidy leg runs ONLY when the build's compile database is present:
if [ -f "$SANDBOX_ROOT/builds/compile_commands.json" ]; then
    aegis_scanner_output_ok "$SANDBOX_ROOT/out/clang-tidy.txt" "$SANDBOX_ROOT" || {
        echo "HARD_BLOCKER: scanner output destination refused — the sweep does not run"
        exit 1
    }
    _out="$(aegis_canonical_output_path "$SANDBOX_ROOT/out/clang-tidy.txt" "$SANDBOX_ROOT")" || {
        echo "HARD_BLOCKER: scanner output destination refused — the sweep does not run"
        exit 1
    }
    "$CLANG_TIDY_BIN" -p "$SANDBOX_ROOT/builds" "$SANDBOX_ROOT/builds/src"/* \
        > "$_out" 2>&1
else
    echo "clang-tidy: no compile database in scope — reported, not generated"
fi
aegis_scanner_output_ok "$SANDBOX_ROOT/out/trivy.txt" "$SANDBOX_ROOT" || {
    echo "HARD_BLOCKER: scanner output destination refused — the sweep does not run"
    exit 1
}
_out="$(aegis_canonical_output_path "$SANDBOX_ROOT/out/trivy.txt" "$SANDBOX_ROOT")" || {
    echo "HARD_BLOCKER: scanner output destination refused — the sweep does not run"
    exit 1
}
# trivy has NO secret-redaction flag at the pinned major (verified against
# its documented flag set — the matched secret VALUES are masked by default; what a
# finding still carries is the CONTEXT lines around the match), so the
# trivy report is quarantined sandbox material: never transcribed, never
# quoted; findings cite location and type only.
"$TRIVY_BIN" fs --scanners vuln,secret,misconfig \
    "$SANDBOX_ROOT/builds" > "$_out" 2>&1
```

The compile database is **detected, never generated**: its absence is a per-tool gap in the report — generating build metadata would mean running the target's build, which is not this lane.

## Failure path (lock release is mandatory)

On ANY failure exit — `HARD_BLOCKER` prereq, checksum mismatch, install error, verify failure — the lock release runs before stopping (the trap carries it). The session attempt counter is deliberately NOT reset on failure: it caps self-heal at 2 so a host that can never succeed stops flailing.

## Reversal

```sh
# 1. Remove the toolchain pieces this skill installed
rm -f "$AEGIS_CONFIG_HOME/sast.env"
rm -f "$SEMGREP_BIN" "$GITLEAKS_BIN" "$CPPCHECK_BIN" "$TRIVY_BIN"
# 2. Remove the session state + lock residue
rm -f "$AEGIS_STATE_HOME/sast-session-state"
rm -rf "$AEGIS_STATE_HOME/sast-install.lock"
# 3. The system clang-tidy was a prerequisite — untouched, left in place
# 4. The library itself stays with the agent tree it ships in
```

## READINESS_PROBE (contract)

The three-state probe lives in the library; this skill gates everything on it.

| Signal | Source | Why |
|---|---|---|
| 1. Config exists + parses | the install's own config file | PATH lookups are unreliable in non-interactive shells |
| 2. Recorded binaries are absolute + executable | the config + `[ -x ]` | a moved or partial install must not read as warm |
| 3. The binaries answer a version/evidence query | real execution | existence is not evidence |

Exit codes: `0` warm (paths exported, ready to sweep) · `1` cold (invoke this skill behind the lock) · `2` install-in-progress-other (another lane holds the lock — degrade this turn, retry next).

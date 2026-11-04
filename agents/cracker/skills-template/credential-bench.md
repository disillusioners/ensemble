---
version: 1.0.0
category: execution
auto_load: false
---

# credential-bench — provision the protection-strength bench toolchain

I provision the LOCAL toolchain the protection-strength lane uses to bench credential-protection schemes: **hashcat** and **john** (John the Ripper), both user-space, both release-asset installs with pinned majors. The bench itself runs on **synthetic corpora only** — this library has no path that fetches a wordlist or a real-world hash set, by design.

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
```

If any is missing, emit the structured envelope and stop:

```
HARD_BLOCKER: <missing prereq>
```

No retries, no fallback install, no privilege escalation.

## Resolve the toolchain (steps)

### Step 0 — Source the lib + warm-probe fast path (idempotency gate)

```sh
# Source the library (repo-root-relative form; on an install tree use the
# absolute path under the agent's skill directory), then take the fast
# path: a warm probe means the toolchain is already provisioned —
# verify-only, install nothing.
. agents/cracker/skills-template/credential-bench.lib.sh
if aegis_readiness_probe; then
    echo "probe warm — verify-only fast path, no install"
    exit 0
fi
# rc=1 cold or rc=2 install-in-progress-other falls through to install
```

### Step 1 — Install root + absolute-path capture

```sh
AEGIS_TOOLBENCH_ROOT="${AEGIS_TOOLBENCH_ROOT:-$HOME/.local/opt/aegis-toolbench}"
mkdir -p "$AEGIS_TOOLBENCH_ROOT"/{bin,share}
# Non-interactive shells do not source ~/.bashrc — capture absolute
# paths explicitly, never rely on PATH lookups.
HASHCAT_BIN="$AEGIS_TOOLBENCH_ROOT/bin/hashcat"
JOHN_BIN="$AEGIS_TOOLBENCH_ROOT/bin/john"
```

### Step 2 — Fetch the pinned release assets (major-pinned)

```sh
HASHCAT_VERSION="6.2.6"
HASHCAT_URL="https://hashcat.net/files/hashcat-6.2.6.7z"
HASHCAT_SHA256="pin-before-first-install"   # from the vendor's signed checksum; REQUIRED

JOHN_VERSION="1.9.0-Jumbo-1"
JOHN_URL="https://www.openwall.com/john/k/john-1.9.0-jumbo-1.tar.xz"
JOHN_SHA256="pin-before-first-install"      # from the vendor's signed checksum; REQUIRED
```

The sha256 pins are **mandatory**: fill them from the vendor's signed checksum material at pin time. An unset or placeholder pin makes the library refuse the install (`HARD_BLOCKER: missing sha256 pin`) — an unpinned fetch is never an acceptable state. Where a vendor ships source-only with no verified binary for the pin, that is a `HARD_BLOCKER: no verified release binary for <tool>@<pin>` — never a source build from an unpinned tree.

### Step 3 — Download → verify → promote

```sh
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"; aegis_release_install_lock' EXIT INT TERM
curl -fsSL -o "$STAGE/hashcat.7z" "$HASHCAT_URL"
# sha256 gate BEFORE any promotion — mismatch aborts, deletes, promotes nothing
aegis_verify_sha256 "$STAGE/hashcat.7z" "$HASHCAT_SHA256" || {
    echo "CHECKSUM MISMATCH — staged asset discarded, nothing promoted"
    exit 1
}
# unpack to the staging dir, then promote the binary into the toolbench root
7z x -o"$STAGE/hashcat" "$STAGE/hashcat.7z" >/dev/null 2>&1 || {
    echo "HARD_BLOCKER: 7z unpacker absent — never install one; report"
    exit 1
}
cp "$STAGE"/hashcat/hashcat* "$AEGIS_TOOLBENCH_ROOT/bin/" 2>/dev/null || true
chmod 0755 "$HASHCAT_BIN"
```

### Step 4 — Staged config → verify → promote (probe-visible config LAST)

```sh
TMPCFG="$(mktemp)"
printf 'HASHCAT_BIN=%s\nHASHCAT_VERSION=%s\nJOHN_BIN=%s\nJOHN_VERSION=%s\n' \
    "$HASHCAT_BIN" "$HASHCAT_VERSION" "$JOHN_BIN" "$JOHN_VERSION" > "$TMPCFG"
# verify: the binaries answer a version/evidence query before promotion
aegis_verify_install "$TMPCFG" || { echo "VERIFY FAILED — config discarded"; exit 1; }
mv "$TMPCFG" "$HOME/.config/aegis-toolbench/credential-bench.env"
trap - EXIT
aegis_release_install_lock
```

The probe-visible config is written **only after** verify passes — a config written before verify would let a broken partial install satisfy the probe forever.

### Step 5 — Attempt cap + lock lifecycle

Self-heal runs at most **TWICE per session**; beyond the cap the cold path stops attempting and reports. The install lock is directory-based and released on **every** exit path — success, verify failure, checksum mismatch — via the trap above. A lock older than the staleness window is treated as abandoned and cleared.

## Bench surface (run time — class (ii) only, synthetic corpora only)

```sh
# The corpus comes from the sandbox generator — NEVER a download.
# The guarded write is the ONLY sanctioned corpus path: the corpus path AND
# the generator are checked against the engagement sandbox root before
# anything is written, so an out-of-sandbox path is a HARD_BLOCKER and
# nothing is created.
aegis_write_synthetic_corpus \
    "$SANDBOX_ROOT/corpora/synth.hashes" "$SANDBOX_ROOT" \
    "$SANDBOX_ROOT/builds/gen_corpus.sh" || {
    echo "HARD_BLOCKER: corpus write refused — the bench does not run"
    exit 1
}
# strength bench, bounded, tracked; --benchmark measures scheme cost safely
"$HASHCAT_BIN" --benchmark -m <mode-from-target-scheme> \
    > "$SANDBOX_ROOT/out/hashcat-bench.txt" 2>&1
"$JOHN_BIN" --test-format=<format-from-target-scheme> \
    > "$SANDBOX_ROOT/out/john-bench.txt" 2>&1
```

There is **no** code path in this library that fetches a wordlist, rule set, or real-world hash corpus: the corpus input path is checked against the engagement sandbox root by `aegis_corpus_path_ok` before anything is written — an out-of-sandbox path (or a generator from outside the sandbox) is refused with `HARD_BLOCKER` and nothing is created — so only generator-produced material enters the bench.

## Failure path (lock release is mandatory)

On ANY failure exit — `HARD_BLOCKER` prereq, checksum mismatch, unpack error, verify failure — the lock release runs before stopping (the trap carries it). The session attempt counter is deliberately NOT reset on failure: it caps self-heal at 2 so a host that can never succeed stops flailing.

## Reversal

```sh
# 1. Remove the toolchain pieces this skill installed
rm -f "$HOME/.config/aegis-toolbench/credential-bench.env"
rm -f "$AEGIS_TOOLBENCH_ROOT/bin/hashcat" "$AEGIS_TOOLBENCH_ROOT/bin/hashcat.bin"
rm -f "$AEGIS_TOOLBENCH_ROOT/bin/john" "$AEGIS_TOOLBENCH_ROOT/bin/john.bin"
# 2. Remove the session state + lock residue
rm -f "$HOME/.cache/aegis-toolbench/credential-bench-session-state"
rm -rf "$HOME/.cache/aegis-toolbench/credential-bench-install.lock"
# 3. The library itself stays with the agent tree it ships in
```

## READINESS_PROBE (contract)

The three-state probe lives in the library; this skill gates everything on it.

| Signal | Source | Why |
|---|---|---|
| 1. Config exists + parses | the install's own config file | PATH lookups are unreliable in non-interactive shells |
| 2. Recorded binaries are absolute + executable | the config + `[ -x ]` | a moved or partial install must not read as warm |
| 3. The binaries answer a version/evidence query | real execution | existence is not evidence |

Exit codes: `0` warm (paths exported, ready to bench) · `1` cold (invoke this skill behind the lock) · `2` install-in-progress-other (another lane holds the lock — degrade this turn, retry next).

# KMS-Lite Root-Key Hardening (P3-WP13a)

| Field | Value |
|---|---|
| **Plan ref** | [`.agents/shared/planning/designer-agent/implementation-plan/phase3-bootstrap-kms-lite.md`](../agents/shared/planning/designer-agent/implementation-plan/phase3-bootstrap-kms-lite.md) §P3-WP13 |
| **Code** | `daemon/util/key_hardening.py` (gate logic) |
| **Day-1 verdict** | WP13a — lightweight file-perm + age hardening. OS-keychain integration deferred. |
| **Revisit trigger** | When KMS-Lite gains §7.5a policy / rotation (PD-15 revisit). |

## TL;DR

The KMS-Lite mint-time gate refuses to issue a handle when the configured
key source violates the contract below. Two env vars control the gate:

| Env var | Required? | Default | Purpose |
|---|---|---|---|
| `SYSTEM_ENCRYPTION_KEY_FILE` | optional | — | Path to a file holding the Fernet key. When set, the file is the key source; perm + age checks fire. |
| `SYSTEM_ENCRYPTION_KEY` | optional | — | Inline key (legacy / quickstart). Perm / age checks are impossible. Day-1 accepts with a residual-risk note. |
| `SYSTEM_ENCRYPTION_KEY_MAX_AGE_DAYS` | optional | `90` | Maximum acceptable age of the key file in days. Files older than this trigger `KEY_FILE_STALE`. |

**Prefer the file source.** The inline path is day-1-only; see [§4 Residual risk — inline key](#4-residual-risk--inline-key) below.

## 1. The contract

The mint-time gate (called from `daemon.services.kms_lite.kms_request` and
`kms_attach` before any encrypted blob is written) invokes
`daemon.util.key_hardening.validate_key_source()`. The result is
structured:

| `result.source` | When | `result.ok` |
|---|---|---|
| `"file"` | `SYSTEM_ENCRYPTION_KEY_FILE` set, file present + readable | `True` iff **all** of: mode `≤ 0o600` (no group/other bits), mtime younger than `SYSTEM_ENCRYPTION_KEY_MAX_AGE_DAYS` (default 90 days). |
| `"inline"` | `SYSTEM_ENCRYPTION_KEY` set, no file path | `True` with a residual-risk note. |
| `"none"` | Neither env var set | `False` (refusal; KMS-Lite raises `KMSUnavailableError` on its existing unset path independently). |

A file source refusal carries a list of stable violation codes; pin
equality in alerting and log greps:

| Code | Meaning | Fix |
|---|---|---|
| `KEY_FILE_MISSING` | File path does not exist. | Provision the file or correct `SYSTEM_ENCRYPTION_KEY_FILE`. |
| `KEY_FILE_NOT_READABLE` | File exists but the daemon process cannot read it (perms flipped between the existence check and the stat; or a NUL byte in the path). | Re-chmod and restart the daemon. |
| `KEY_FILE_PERMS` | Mode is wider than `0o600` (group or other bits set). | `chmod 600 <key-file>`. |
| `KEY_FILE_STALE` | File mtime is older than the configured threshold. | Rotate the key — see [§3 Rotation](#3-rotation). |

Multiple violations may be reported in one refusal (e.g. world-readable
AND stale).

## 2. Provisioning a compliant key file

Generate the key with Fernet (so it matches what KMS-Lite expects) and
stash it in a `0o600` file:

```bash
# 1. Generate the Fernet key (URL-safe base64, 32 bytes of entropy).
KEY=$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')

# 2. Stash it in a root-owned, owner-only file.
sudo install -m 0600 -o root -g root /dev/null /etc/ensemble/kms.key
echo "$KEY" | sudo tee /etc/ensemble/kms.key >/dev/null
# The tee above will create the file as root:root 0o600 only if you used
# install -m 0600 first; otherwise run chmod 600 explicitly:
sudo chmod 600 /etc/ensemble/kms.key

# 3. Point the daemon at it (and remove any inline shadow).
echo 'SYSTEM_ENCRYPTION_KEY_FILE=/etc/ensemble/kms.key' | sudo tee -a /etc/ensemble/ensemble.env
sudo systemctl restart ensemble-live.service

# 4. Verify — the gate should be ok now. (Optional: see §5 verification recipe.)
```

**Equivalent with `openssl`:**

```bash
KEY=$(openssl rand -base64 32 | tr -d '\n=' | head -c 44)  # 32 bytes → ~44 base64 chars
# ^ Note: Fernet requires a URL-safe base64 32-byte key. openssl rand -base64
# emits standard base64; the KMS-Lite store parses via cryptography.fernet
# which is permissive about the exact chars but strict about length. The
# `Fernet.generate_key()` recipe above is the safest path.
```

> **Important:** never store the key in a `~/.bash_history` line, an
> env-file with mode `0o644`, or in a git-tracked config. The 90-day
> age threshold assumes you have a key-rotation runbook; without one,
> set `SYSTEM_ENCRYPTION_KEY_MAX_AGE_DAYS=36500` (≈100 years) as a
> stopgap and plan a rotation pass.

## 3. Rotation

Day-1 has no rotation surface (§7.5a deferred). The 90-day age gate is
a "reminder", not a live rotation: when `KEY_FILE_STALE` fires, the
operator MUST:

1. Generate a new key (Fernet or equivalent — see §2).
2. Write it to a NEW file path (so the old file is no longer the
   active source).
3. Restart the daemon with the new `SYSTEM_ENCRYPTION_KEY_FILE`.
4. Re-mint any in-flight credentials — day-1 store is in-memory only,
   so all handles minted with the old key are LOST on restart. Active
   MCP server configs must be re-installed (the §7 sequence re-mints
   automatically).

A future commission will add a real rotation primitive; for day-1, the
manual recipe above is the contract.

## 4. Residual risk — inline key

When the operator sets `SYSTEM_ENCRYPTION_KEY` directly (no file path),
the gate returns `ok=True` with a note that surfaces the residual risk:

> The key may appear in process listings, shell history, or env dumps.

Concrete exposure paths:

* **`/proc/<pid>/environ`** — readable by the same UID; if the daemon
  is compromised, the key is one read away. A file with mode `0o600`
  is owned by root, so the same attacker still needs root.
* **`/proc/<pid>/cmdline`** — does NOT carry env vars (separate from
  environ). Safe in this dimension.
* **Shell history** — if the operator `export`s the key in an
  interactive shell, `~/.bash_history` carries it. A file with
  `0o600` and a one-time write avoids this.
* **Container env dumps** — `docker inspect`, `kubectl describe pod`,
  and the OpenDesign env-var pass-through all inline-leak.
* **Crash dumps / core files** — env-var material can land in core
  dumps depending on the runtime. File content does not.

Day-1 accepts inline because the bootstrap flow needs the daemon
running before any key file can be provisioned. Operators SHOULD move
to the file source as soon as the daemon is up.

## 5. Verification recipe

After provisioning (or after a restart), confirm the gate is happy:

```bash
# From a Python REPL with the daemon's env exported:
.venv/bin/python -c '
from daemon.util.key_hardening import validate_key_source
r = validate_key_source()
print(r.source, r.ok, r.message)
for v in r.violations:
    print("  ", v)
for n in r.notes:
    print("  note:", n)
'
```

Expected output (file source, all green):

```
file True key hardening ok: file source valid (mode=<=0o600, age<90d)
```

Expected output (inline only):

```
inline True key hardening ok-with-note: inline key (no file)
  note: inline SYSTEM_ENCRYPTION_KEY is set; file-level perm/age checks are not possible. ...
```

Expected output (file source, refusal):

```
file False key hardening refusal: file source failed [KEY_FILE_PERMS]; see violations list
  KEY_FILE_PERMS: SYSTEM_ENCRYPTION_KEY_FILE='/etc/ensemble/kms.key' mode=0o644 is wider than 0o600 (group/other bits set)
```

## 6. OS-keychain integration — deferral rationale (§7.5a revisit trigger)

Day-1 deliberately does NOT integrate with the OS keychain:

* **macOS Keychain**, **Linux libsecret**, and **Windows DPAPI** all
  require platform-specific code paths. Day-1 cross-platform surface
  is small by design — the OS-keychain expansion would triple it.
* **No rotation / policy layer** (§7.5a deferred) — a keychain key
  without rotation is just an `0o600` file with extra steps. Wait
  until the policy engine exists.
* **Threat model is "unattended daemon, world-readable key file"** —
  the realistic exposure today. `0o600` + 90-day age covers it.
  OS-keychain would harden a different threat (laptop theft) that the
  current deployment surface does not face.

Revisit when **all** of these land:

1. §7.5a policy / TTL / rotation primitives are in the codebase.
2. The platform target is fixed (today the daemon targets
   `linux+macOS`; pinning to one makes DPAPI vs. Keychain a real
   choice).
3. A rotation runbook exists (so the keychain key can be rotated
   without manual `security delete-generic-password` ceremony).

PD-15 owner: open a new commission when all three are satisfied.

## 7. Failure-mode summary

| Scenario | Gate result | KMS-Lite outcome |
|---|---|---|
| `KEY_FILE_PERMS` | `ok=False` w/ violation list | `KMSUnavailableError` — mint refused, daemon stays up. |
| `KEY_FILE_STALE` | `ok=False` w/ violation list | `KMSUnavailableError` — mint refused, daemon stays up. |
| `KEY_FILE_MISSING` | `ok=False` w/ violation list | `KMSUnavailableError` — mint refused, daemon stays up. |
| `KEY_FILE_NOT_READABLE` | `ok=False` w/ violation list | `KMSUnavailableError` — mint refused, daemon stays up. |
| Inline key only | `ok=True` w/ residual-risk note | Mint proceeds (day-1 accepts inline). |
| File valid (0o600 + fresh) | `ok=True` | Mint proceeds. |
| Neither env var set | `ok=False` w/ `KEY_NOT_SET` | `KMSUnavailableError` — the existing unset path raises independently; the gate signals the same fact with a structured code so the escalation envelope can deduplicate. |

The gate never crashes the daemon. On any internal error
(`OSError`, `ValueError` from a malformed path), the function returns
`ok=False` with a structured violation — the caller decides whether
to raise or fall through to its own unset-key check.

## 8. Where to look in the code

* `daemon/util/key_hardening.py` — `validate_key_source()` and the
  `KeyHardeningResult` dataclass.
* `daemon/services/kms_lite.py` — the mint-time gate hook (the
  `kms_request` and `kms_attach` paths call `validate_key_source()`
  first and raise `KMSUnavailableError` on `ok=False`).
* `daemon/sources/credentials.py:12-17` — the canonical env-var
  constants (`SYSTEM_ENCRYPTION_KEY_ENV`,
  `_LEGACY_SOURCE_CREDENTIAL_KEY_ENV`); the hardening module imports
  `SYSTEM_ENCRYPTION_KEY_ENV` so the env-var contract stays
  single-sourced.
* `tests/unit/services/test_key_hardening.py` — the acceptance suite
  (20 cases; tmp_path + monkeypatch, no PG/DB).

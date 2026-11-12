"""Port-ownership contract (rogue-containment component A, fix-round 2).

Single daemon-side mechanism that runs BEFORE the listener bind and refuses
the port (exit 78 + holder-identity marker line) ONLY on positive proof of a
live FOREIGN owner. Claim absent/stale/same-install/write-failed/unknown
=> take-and-bind / degrade / retry — never refuse.

Claims directory (fix-round 2 / B1 — HOST-GLOBAL, PER-UID)
-----------------------------------------------------------
The iter-1 shape read/wrote a claims file under the *install root*, so a
foreign daemon from another install was INVISIBLE: each install only ever
saw its own directory. Two processes on the same host and port therefore
never met, and the ownership gate could not refuse the incident's rogue.

The claims directory is now host-global and per-uid:

  1. ``ENSEMBLE_PORT_CLAIMS_DIR`` (explicit override — tests / fenced
     mirrors; no boot ever writes the live install's data dir),
  2. ``$XDG_RUNTIME_DIR/ensemble-port-claims`` (mode 0700),
  3. ``/tmp/ensemble-port-claims-<uid>`` (mode 0700).

Every daemon of the same uid on this host sees the same claim namespace.
Claims are keyed by a NORMALIZED port key: wildcard and loopback hosts all
collapse to the ``any`` bucket (a daemon bound to 0.0.0.0:9797 and one
bound to 127.0.0.1:9797 contend for the same socket), other hosts keep
their literal, lowercased value. File name: ``<bucket>-<port>.json``.

Residuals (documented, NOT closed here):
  * SAME-UID claim deletion — any process running as the same uid can
    unlink/overwrite another daemon's claim file. The containment boundary
    is the uid, not the process tree.
  * SAME-INSTALL rogue (H1) — a rogue process executing from the SAME
    install directory resolves the same ``install_dir`` and is therefore
    non-foreign by design (D2). The claim protects across installs.
  * PORT=0 — an ephemeral-port bind has no stable claim key; the gate
    REFUSES to claim (loud ERROR) rather than writing a meaningless
    ``*-0.json`` file (fix-round 2 / N8).

Claim file: filesystem JSON (zero migration, D7). No SQLModel table.
Install-root resolution reuses ``_resolve_install_dir`` and NEVER falls
back to the canonical LIVE root for a dev/unresolved self-env (fix-round 2
/ N3 — that fallback is what accreted claim debris inside the live
install).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: Durable refusal-marker file name (co-located with the claims dir — N2).
REFUSAL_MARKER_FILENAME = "port-ownership-refusal.json"

#: Port range accepted by the claim contract (N8).
MIN_CLAIM_PORT = 1
MAX_CLAIM_PORT = 65535

#: Hosts that collapse into one claim bucket: a wildcard bind and any
#: loopback bind contend for the same TCP port on the same host.
_WILDCARD_HOSTS = frozenset({"", "*", "0.0.0.0", "::", "[::]", "::0"})
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


@dataclass
class PortClaim:
    install_dir: Optional[str]
    pid: int
    starttime: Optional[int]
    port: int
    host: str
    claimed_at: str
    version: Optional[str] = None
    uid: Optional[int] = None


@dataclass
class ClaimResult:
    kind: str  # "taken" | "refused" | "degraded" | "unknown" | "invalid"
    claim: Optional[PortClaim] = None
    holder_pid: Optional[int] = None
    reason: str = ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _str_or_none(v: Any) -> Optional[str]:
    if v is None:
        return None
    return str(v)


# ---------------------------------------------------------------------------
# Claims directory (host-global, per-uid) — B1
# ---------------------------------------------------------------------------


def resolve_claims_dir() -> Path:
    """Resolve the host-global per-uid claims directory.

    Precedence: ``ENSEMBLE_PORT_CLAIMS_DIR`` override > XDG runtime >
    ``/tmp/ensemble-port-claims-<uid>``. The override exists for tests and
    fenced mirrors so no boot writes a shared/live location.
    """
    override = os.environ.get("ENSEMBLE_PORT_CLAIMS_DIR")
    if override and str(override).strip():
        return Path(str(override).strip())
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg and str(xdg).strip():
        xdg_path = Path(str(xdg).strip())
        if xdg_path.is_dir():
            return xdg_path / "ensemble-port-claims"
    return Path(f"/tmp/ensemble-port-claims-{os.getuid()}")


def ensure_claims_dir(path: Path) -> Optional[str]:
    """Create the claims dir at mode 0700. Returns an error string or None.

    A pre-existing directory owned by this uid is re-chmodded to 0700 so a
    squatted world-readable path cannot widen the namespace. A directory
    we do not own is used as-is (best effort; the failure mode is visible
    in the gate log, never a boot refusal).
    """
    try:
        created = not path.exists()
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        st = path.stat()
        if st.st_uid == os.getuid():
            wanted = 0o700
            if (st.st_mode & 0o777) != wanted or created:
                try:
                    os.chmod(path, wanted)
                except OSError as exc:  # pragma: no cover - defensive
                    logger.warning(
                        "port-ownership: could not chmod claims dir %s to 0700: %s",
                        path,
                        exc,
                    )
        return None
    except OSError as exc:
        return str(exc)


def _claims_dir_for(self_install_dir: Optional[Path]) -> Path:
    """Return the claims directory.

    ``self_install_dir`` is accepted for call-site compatibility but is
    DELIBERATELY unused: the claims namespace is host-global, not
    install-scoped (B1). The install identity lives in the claim payload
    (``install_dir``), which is what foreign-ness is decided on.
    """
    del self_install_dir  # host-global namespace by design
    return resolve_claims_dir()


def resolve_data_dir() -> Path:
    """Back-compat alias: the refusal marker is co-located with the claims
    dir (N2), so the "data dir" for marker purposes IS the claims dir."""
    return resolve_claims_dir()


def resolve_refusal_marker_path(claims_dir: Optional[Path] = None) -> Path:
    base = Path(claims_dir) if claims_dir is not None else resolve_claims_dir()
    return base / REFUSAL_MARKER_FILENAME


# ---------------------------------------------------------------------------
# Self install-dir resolution (N3 — never fall back to the live root)
# ---------------------------------------------------------------------------


def _source_tree_root() -> Optional[Path]:
    """Repo-root identity for a source-mode (dev/pytest) daemon.

    ``daemon/services/port_ownership.py`` -> ``parents[2]``. Only returned
    when the root actually looks like this source tree, so a frozen
    extraction dir or an unrelated layout yields ``None``.
    """
    try:
        root = Path(__file__).resolve().parents[2]
    except (OSError, IndexError):  # pragma: no cover - defensive
        return None
    if (root / "daemon" / "__init__.py").is_file():
        return root
    return None


def resolve_self_install_dir() -> Optional[Path]:
    """Resolve THIS daemon's install identity.

    Uses the existing staged-install machinery only. Fix-round 2 / N3:
    there is deliberately NO ``_canonical_install_root("agents-ensemble")``
    fallback here — that fallback made every dev/unresolved boot claim the
    LIVE install root (the source of the live ``data/port-claims`` debris).
    A dev/unresolved boot now resolves to its own source-tree root, which
    is both honest identity and correctly FOREIGN to a live install.
    """
    try:
        from daemon.tools.upgrade_tools import (
            _resolve_install_dir,
            _self_env_marker,
        )

        resolved = _resolve_install_dir(_self_env_marker())
        if resolved is not None:
            return resolved
    except Exception as exc:  # noqa: BLE001 — resolution is advisory
        logger.debug("port-ownership: install-dir resolution unavailable: %s", exc)
    root = _source_tree_root()
    if root is not None:
        return root
    logger.warning(
        "port-ownership: self install dir UNRESOLVED — the claim records "
        "install_dir=None and foreign-ness cannot be established this boot "
        "(D6: non-foreign)."
    )
    return None


# ---------------------------------------------------------------------------
# Claim keying + validation
# ---------------------------------------------------------------------------


def _normalize_claim_host(host: Any) -> str:
    """Collapse wildcard/loopback hosts into one bucket (B1 keying)."""
    if host is None:
        return "any"
    h = str(host).strip().lower()
    if h in _WILDCARD_HOSTS or h in _LOOPBACK_HOSTS:
        return "any"
    return h


def validate_claim_port(port: Any) -> Optional[str]:
    """Return a refusal reason for an unclaimable port, else None (N8)."""
    try:
        p = int(port)
    except (TypeError, ValueError):
        return f"non-integer port {port!r}"
    if p == 0:
        return "port 0 is an ephemeral bind — a claim file is meaningless"
    if p < MIN_CLAIM_PORT or p > MAX_CLAIM_PORT:
        return f"port {p} outside the claimable range {MIN_CLAIM_PORT}..{MAX_CLAIM_PORT}"
    return None


# ---------------------------------------------------------------------------
# Liveness
# ---------------------------------------------------------------------------


def _read_starttime(pid: int) -> Optional[int]:
    """Field 22 of /proc/<pid>/stat (jiffies since boot). None if unreadable."""
    try:
        with open(f"/proc/{pid}/stat", "r", encoding="utf-8") as f:
            data = f.read()
    except (OSError, IOError, ValueError):
        return None
    idx = data.rfind(")")
    if idx < 0:
        return None
    fields = data[idx + 1 :].split()
    # starttime is field 22 overall => index 19 in the post-comm list.
    if len(fields) < 20:
        return None
    try:
        return int(fields[19])
    except (ValueError, IndexError):
        return None


def _read_proc_exe(pid: int) -> str:
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except (OSError, IOError):
        return "<unreadable>"


def _read_proc_cwd(pid: int) -> str:
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except (OSError, IOError):
        return "<unreadable>"


def pid_alive_with_starttime(pid: int, starttime: Optional[int]) -> bool:
    if starttime is None:
        return False
    st = _read_starttime(pid)
    if st is None:
        return False
    if st != starttime:
        return False
    # exe readability as an additional staleness signal (fail-open to stale).
    try:
        os.readlink(f"/proc/{pid}/exe")
    except (OSError, IOError):
        return False
    return True


# ---------------------------------------------------------------------------
# Claim file IO
# ---------------------------------------------------------------------------


def _claim_path(claims_dir: Path, host: str, port: int) -> Path:
    return Path(claims_dir) / f"{_normalize_claim_host(host)}-{int(port)}.json"


def read_claim(path: Path) -> Optional[PortClaim]:
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, IOError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return PortClaim(
            install_dir=data.get("install_dir"),
            pid=int(data["pid"]),
            starttime=data.get("starttime"),
            port=int(data["port"]),
            host=str(data["host"]),
            claimed_at=str(data.get("claimed_at", "")),
            version=data.get("version"),
            uid=data.get("uid"),
        )
    except (KeyError, ValueError, TypeError):
        return None


def is_live_foreign(claim: Optional[PortClaim], self_install_dir: Optional[Path]) -> bool:
    if claim is None:
        return False
    if not pid_alive_with_starttime(claim.pid, claim.starttime):
        return False  # stale
    if claim.install_dir is None or self_install_dir is None:
        return False  # D6: unresolved => non-foreign
    if str(claim.install_dir) == str(self_install_dir):
        return False  # same install
    return True


def _version() -> Optional[str]:
    try:
        from daemon import __version__

        return str(__version__)
    except Exception:  # noqa: BLE001
        return None


def try_claim(
    claims_dir: Path,
    host: str,
    port: int,
    self_install_dir: Optional[Path],
    *,
    retries: int = 3,
    sleep_s: float = 0.25,
    version: Optional[str] = None,
) -> ClaimResult:
    invalid = validate_claim_port(port)
    if invalid is not None:
        # N8: never write a claim file for an unclaimable port.
        return ClaimResult(kind="invalid", reason=invalid)

    path = _claim_path(claims_dir, host, port)
    dir_error = ensure_claims_dir(Path(claims_dir))
    if dir_error is not None:
        return ClaimResult(kind="degraded", reason=f"claims_dir create failed: {dir_error}")

    self_install_str = _str_or_none(self_install_dir)
    starttime = _read_starttime(os.getpid())
    claim_to_write = PortClaim(
        install_dir=self_install_str,
        pid=os.getpid(),
        starttime=starttime,
        port=int(port),
        host=str(host),
        claimed_at=_now_iso(),
        version=version if version is not None else _version(),
        uid=os.getuid(),
    )

    for attempt in range(retries + 1):
        existing = read_claim(path)
        if existing is not None:
            if is_live_foreign(existing, self_install_dir):
                return ClaimResult(kind="refused", claim=existing, holder_pid=existing.pid, reason="foreign live owner")
            # stale or same-install: take over
            try:
                path.unlink(missing_ok=True)
            except OSError as e:
                return ClaimResult(kind="degraded", reason=f"unlink failed: {e}")

        # atomic create
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if attempt < retries:
                time.sleep(sleep_s)
                continue
            return ClaimResult(kind="unknown", reason="concurrent claim race unresolved")
        except OSError as e:
            return ClaimResult(kind="degraded", reason=f"open failed: {e}")

        # write payload
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(asdict(claim_to_write), f, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
        except OSError as e:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return ClaimResult(kind="degraded", reason=f"write failed: {e}")

        return ClaimResult(kind="taken", claim=claim_to_write, reason="claimed")

    return ClaimResult(kind="unknown", reason="retries exhausted")


def release_claim(claims_dir: Path, host: str, port: int, pid: int) -> bool:
    """Remove a claim owned by ``pid`` (pid-gated).

    NOTE (fix-round 2 / B3): the DAEMON no longer calls this. Releasing the
    claim from the lifespan shutdown happens while the process is still
    alive (uvicorn still owns the listener and is draining), which re-opens
    the very window the claim exists to close. Stale claims self-heal via
    ``pid`` + ``starttime`` liveness on the next boot. The helper stays for
    tests and manual operator cleanup only.
    """
    path = _claim_path(claims_dir, host, port)
    claim = read_claim(path)
    if claim is None:
        return False
    if claim.pid != pid:
        return False  # never delete a successor's claim
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Refusal marker: writer (B8/D8) + reader (B5 — fix-round 2)
# ---------------------------------------------------------------------------


def _marker_sort_safe_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def surface_refusal_marker(claims_dir: Optional[Path] = None) -> bool:
    """Surface a refusal marker left by a PRIOR boot, once (B5).

    A refusing daemon cannot report its own refusal past the exit; the
    NEXT boot therefore reports it:

      * one loud structured ERROR line carrying the marker content
        (holder identity, refusal timestamp, port/host, self install),
      * rotation to ``port-ownership-refusal.json.seen-<utc-ts>`` so the
        same record is never reported twice.

    Operator-facing channel: the line lands in the daemon's journald unit
    log (``journalctl -u <unit>``) and in the file log; the systemd unit's
    status shows the last lines (``systemctl status <unit>``).

    Returns True when a marker was surfaced.
    """
    path = resolve_refusal_marker_path(claims_dir)
    try:
        if not path.is_file():
            return False
    except OSError:  # pragma: no cover - defensive
        return False
    raw = ""
    data: Any = None
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, IOError, ValueError):
        data = None
    if isinstance(data, dict):
        holder = data.get("holder") if isinstance(data.get("holder"), dict) else {}
        logger.error(
            "PORT-OWNERSHIP REFUSAL FROM A PRIOR BOOT: refused_at=%s "
            "port=%s host=%s exit_code=%s holder_pid=%s holder_exe=%s "
            "holder_cwd=%s holder_install=%s self_install=%s marker=%s",
            data.get("refused_at"),
            data.get("port"),
            data.get("host"),
            data.get("exit_code"),
            holder.get("pid"),
            holder.get("exe"),
            holder.get("cwd"),
            holder.get("install_dir"),
            data.get("self_install_dir"),
            path,
        )
    else:
        logger.error(
            "PORT-OWNERSHIP REFUSAL MARKER unparseable at %s (raw=%r) — "
            "a prior boot refused the port and the record is corrupt",
            path,
            raw[:2000],
        )
    seen = path.parent / f"{path.name}.seen-{_marker_sort_safe_ts()}"
    try:
        os.replace(str(path), str(seen))
    except OSError as exc:  # pragma: no cover - defensive
        logger.warning("port-ownership: could not rotate refusal marker %s: %s", path, exc)
    return True


def write_refusal_marker(
    data_dir: Path, holder: Any, self_install_dir: Optional[Path], port: int, host: str
) -> bool:
    path = Path(data_dir) / REFUSAL_MARKER_FILENAME
    holder_pid = None
    holder_install = None
    if isinstance(holder, PortClaim):
        holder_pid = holder.pid
        holder_install = holder.install_dir
    elif isinstance(holder, dict):
        holder_pid = holder.get("pid")
        holder_install = holder.get("install_dir")
    else:
        holder_pid = getattr(holder, "pid", None)
        holder_install = getattr(holder, "install_dir", None)
    payload = {
        "refused_at": _now_iso(),
        "port": port,
        "host": host,
        "holder": {
            "pid": holder_pid,
            "exe": _read_proc_exe(holder_pid) if holder_pid is not None else "<unknown>",
            "cwd": _read_proc_cwd(holder_pid) if holder_pid is not None else "<unknown>",
            "install_dir": holder_install,
        },
        "self_install_dir": _str_or_none(self_install_dir),
        "exit_code": 78,
    }
    try:
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def clear_refusal_marker(data_dir: Path) -> bool:
    path = Path(data_dir) / REFUSAL_MARKER_FILENAME
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def run_port_ownership_gate(host: str, port: int) -> None:
    """Run the gate.

    Order (fix-round 2):
      1. surface any refusal marker left by a PRIOR boot (B5),
      2. validate the port — PORT=0 / out-of-range REFUSES THE CLAIM
         loudly and the boot proceeds with no ownership enforcement (N8),
      3. claim; on positive proof of a live FOREIGN owner: marker line +
         durable refusal record + ``sys.exit(78)``. This exit happens
         BEFORE ``bind_socket_with_retry`` — the refusal never enters the
         retry loop (D4, pinned).
      4. degrade/unknown => loud warning, proceed without enforcement.
    """
    self_install_dir = resolve_self_install_dir()
    # The claims dir is host-global; the install identity is passed for
    # call-site symmetry and is deliberately NOT the namespace key (B1).
    claims_dir = _claims_dir_for(self_install_dir)
    surface_refusal_marker(claims_dir)

    invalid = validate_claim_port(port)
    if invalid is not None:
        logger.error(
            "port-ownership gate REFUSED TO CLAIM: %s — no claim file will "
            "be written for host=%s; boot proceeds WITHOUT ownership "
            "enforcement for this port.",
            invalid,
            host,
        )
        return

    result = try_claim(claims_dir, host, port, self_install_dir)
    if result.kind == "refused":
        holder = result.claim
        holder_pid = result.holder_pid
        if holder is not None:
            holder_pid = holder.pid
        logger.warning(
            "PORT OWNERSHIP REFUSED: port=%s host=%s holder_pid=%s "
            "holder_exe=%s holder_cwd=%s holder_install=%s self_install=%s "
            "claims_dir=%s",
            port,
            host,
            holder_pid,
            _read_proc_exe(holder_pid) if holder_pid is not None else "<unknown>",
            _read_proc_cwd(holder_pid) if holder_pid is not None else "<unknown>",
            getattr(holder, "install_dir", None) if holder is not None else None,
            self_install_dir,
            claims_dir,
        )
        write_refusal_marker(claims_dir, holder, self_install_dir, port, host)
        sys.exit(78)
    if result.kind in ("degraded", "unknown", "invalid"):
        logger.warning(
            "port-ownership gate degraded: %s — proceeding without ownership enforcement",
            result.reason,
        )
    if self_install_dir is None and result.kind == "taken":
        logger.warning(
            "port-ownership: claim recorded install_dir=None — foreign-ness "
            "cannot be established from this boot (D6 residual)."
        )
    # passing boot: clear any stale refusal marker
    clear_refusal_marker(claims_dir)

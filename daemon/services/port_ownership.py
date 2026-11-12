"""Port-ownership contract (rogue-containment component A).

Single daemon-side mechanism that runs BEFORE the listener bind and refuses
the port (exit 78 + holder-identity marker line) ONLY on positive proof of
a live FOREIGN owner. Claim absent/stale/same-install/write-failed/unknown
=> take-and-bind / degrade / retry — never refuse.

Claim file: filesystem JSON under ``<install_root>/data/port-claims/<host>-<port>.json``.
No SQLModel table, no migration (D7).

Reuse the existing install-root resolution machinery
(``daemon.services.promote_txn_hold._resolve_install_root`` →
``daemon.tools.upgrade_tools``) — never re-derive.
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


@dataclass
class PortClaim:
    install_dir: Optional[str]
    pid: int
    starttime: Optional[int]
    port: int
    host: str
    claimed_at: str
    version: Optional[str] = None


@dataclass
class ClaimResult:
    kind: str  # "taken" | "refused" | "degraded" | "unknown"
    claim: Optional[PortClaim] = None
    holder_pid: Optional[int] = None
    reason: str = ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _str_or_none(v: Any) -> Optional[str]:
    if v is None:
        return None
    return str(v)


def resolve_self_install_dir() -> Optional[Path]:
    """Reuse the existing machinery; never re-derive."""
    try:
        from daemon.services.promote_txn_hold import _resolve_install_root

        return _resolve_install_root()
    except Exception:  # noqa: BLE001
        return None


def _claims_dir_for(self_install_dir: Optional[Path]) -> Path:
    # rogue-containment iter-1 D3: ENSEMBLE_PORT_CLAIMS_DIR overrides
    # the claims directory entirely (used by tests + fenced mirrors so
    # no boot ever writes the live install's data/port-claims).
    override = os.environ.get("ENSEMBLE_PORT_CLAIMS_DIR")
    if override and str(override).strip():
        return Path(str(override).strip())
    if self_install_dir is not None:
        return Path(self_install_dir) / "data" / "port-claims"
    # Fallback when install root is unresolved (dev/source/pytest):
    # use the data dir precedence, mirroring api.py lifespan.
    data_dir = Path(
        os.environ.get("ENSEMBLE_DATA_DIR")
        or os.environ.get("DATA_DIR")
        or "./data"
    )
    return data_dir / "port-claims"


def resolve_data_dir() -> Path:
    # rogue-containment iter-1 D3: honor ENSEMBLE_PORT_CLAIMS_DIR for the
    # refusal marker too (keeps marker + claims co-located under the override).
    override = os.environ.get("ENSEMBLE_PORT_CLAIMS_DIR")
    if override and str(override).strip():
        return Path(str(override).strip()).parent
    return Path(
        os.environ.get("ENSEMBLE_DATA_DIR")
        or os.environ.get("DATA_DIR")
        or "./data"
    )


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


def _claim_path(claims_dir: Path, host: str, port: int) -> Path:
    return claims_dir / f"{host}-{port}.json"


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
    path = _claim_path(claims_dir, host, port)
    try:
        claims_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return ClaimResult(kind="degraded", reason=f"claims_dir create failed: {e}")

    self_install_str = _str_or_none(self_install_dir)
    starttime = _read_starttime(os.getpid())
    claim_to_write = PortClaim(
        install_dir=self_install_str,
        pid=os.getpid(),
        starttime=starttime,
        port=port,
        host=host,
        claimed_at=_now_iso(),
        version=version if version is not None else _version(),
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
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
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


def write_refusal_marker(
    data_dir: Path, holder: Any, self_install_dir: Optional[Path], port: int, host: str
) -> bool:
    path = Path(data_dir) / "port-ownership-refusal.json"
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
    path = Path(data_dir) / "port-ownership-refusal.json"
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def run_port_ownership_gate(host: str, port: int) -> None:
    """Run the gate. sys.exit(78) on refused; degrade otherwise. Clears any
    stale refusal marker on a passing (non-refused) boot."""
    self_install_dir = resolve_self_install_dir()
    claims_dir = _claims_dir_for(self_install_dir)
    result = try_claim(claims_dir, host, port, self_install_dir)
    if result.kind == "refused":
        holder = result.claim
        holder_pid = result.holder_pid
        if holder is not None:
            holder_pid = holder.pid
        logger.warning(
            "PORT OWNERSHIP REFUSED: port=%s host=%s holder_pid=%s "
            "holder_exe=%s holder_cwd=%s holder_install=%s self_install=%s",
            port,
            host,
            holder_pid,
            _read_proc_exe(holder_pid) if holder_pid is not None else "<unknown>",
            _read_proc_cwd(holder_pid) if holder_pid is not None else "<unknown>",
            getattr(holder, "install_dir", None) if holder is not None else None,
            self_install_dir,
        )
        write_refusal_marker(resolve_data_dir(), holder, self_install_dir, port, host)
        sys.exit(78)
    if result.kind in ("degraded", "unknown"):
        logger.warning(
            "port-ownership gate degraded: %s — proceeding without ownership enforcement",
            result.reason,
        )
    # passing boot: clear any stale refusal marker
    clear_refusal_marker(resolve_data_dir())

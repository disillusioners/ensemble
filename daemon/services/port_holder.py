"""Bind-time socket-holder identity (rogue-containment p2, component A2).

Pure ``/proc`` inspection: discovers which process ACTUALLY holds a LISTEN
socket on a given port, attributes its install directory, and confirms the
finding across two FULL re-walks. No ``ss``/``lsof``/``psutil`` — the only
variant that is tmp-procfs-injectable for tests and frozen-binary-safe.

Degrade-never-refuse on every unverifiable step (D-A2-3): missing /
unparseable tables, permission-denied exe/cwd/fd dirs, recycled pids,
holders that vanish between samples all yield NO refusal — only POSITIVE
proof (both install dirs resolved, different, liveness confirmed) refuses.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

#: Sample interval for the two-sample confirmation (house constant — mirrors
#: ``try_claim``'s race-retry sleep).
CONFIRM_REWALK_DELAY_S = 0.25

#: TCP LISTEN state in /proc/net/tcp{,6}.
_TCP_LISTEN_STATE = "0A"


@dataclass
class HolderRef:
    pid: int
    starttime: Optional[int] = None
    exe: str = "<unreadable>"
    cwd: str = "<unreadable>"
    ppid: Optional[int] = None
    inode: Optional[str] = None
    install_dir: Optional[str] = None
    ppid_cmdline_head: Optional[str] = None


def _read_starttime(pid: int, proc_root: str) -> Optional[int]:
    try:
        with open(os.path.join(proc_root, str(pid), "stat"), "r", encoding="utf-8") as f:
            data = f.read()
    except (OSError, IOError, ValueError):
        return None
    idx = data.rfind(")")
    if idx < 0:
        return None
    fields = data[idx + 1 :].split()
    if len(fields) < 20:
        return None
    try:
        return int(fields[19])
    except (ValueError, IndexError):
        return None


def _read_ppid(pid: int, proc_root: str) -> Optional[int]:
    try:
        with open(os.path.join(proc_root, str(pid), "stat"), "r", encoding="utf-8") as f:
            data = f.read()
    except (OSError, IOError, ValueError):
        return None
    idx = data.rfind(")")
    if idx < 0:
        return None
    fields = data[idx + 1 :].split()
    if len(fields) < 2:
        return None
    try:
        return int(fields[1])
    except (ValueError, IndexError):
        return None


def _read_exe(pid: int, proc_root: str) -> str:
    try:
        return os.readlink(os.path.join(proc_root, str(pid), "exe"))
    except (OSError, IOError):
        return "<unreadable>"


def _read_cwd(pid: int, proc_root: str) -> str:
    try:
        return os.readlink(os.path.join(proc_root, str(pid), "cwd"))
    except (OSError, IOError):
        return "<unreadable>"


def _read_cmdline_head(pid: Optional[int], proc_root: str) -> Optional[str]:
    if pid is None:
        return None
    try:
        raw = open(os.path.join(proc_root, str(pid), "cmdline"), "rb").read()
    except (OSError, IOError):
        return None
    if not raw:
        return None
    text = raw.decode("utf-8", "replace")
    parts = [p for p in text.split("\x00")]
    if parts and parts[-1] == "":
        parts = parts[:-1]
    if not parts:
        return None
    head = " ".join(parts)
    if len(head) > 120:
        head = head[:120]
    return head


def _read_fd_links(pid: int, proc_root: str) -> List[str]:
    """Return readlink targets of /proc/<pid>/fd/*.

    Raises OSError/IOError when the fd dir is unreadable (EACCES /
    vanished) so the caller can degrade loudly instead of fabricating.
    """
    fd_dir = os.path.join(proc_root, str(pid), "fd")
    out: List[str] = []
    for name in os.listdir(fd_dir):
        try:
            link = os.readlink(os.path.join(fd_dir, name))
        except (OSError, IOError):
            continue
        out.append(link)
    return out


def _parse_tcp_table(text: str) -> List[tuple]:
    """Parse a /proc/net/tcp{,6} table into (port_int, state, inode) rows."""
    rows: List[tuple] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if not parts or parts[0] == "sl":
            continue
        if len(parts) < 10:
            continue
        local_address = parts[1]
        st = parts[3]
        inode = parts[9]
        if ":" not in local_address:
            continue
        port_hex = local_address.rsplit(":", 1)[-1]
        try:
            port_dec = int(port_hex, 16)
        except ValueError:
            continue
        rows.append((port_dec, st.upper(), inode))
    return rows


def _read_table(proc_root: str, filename: str) -> Optional[List[tuple]]:
    """Return parsed rows, or None when the table is absent/unparseable."""
    path = os.path.join(proc_root, "net", filename)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except (OSError, IOError):
        return None
    try:
        return _parse_tcp_table(text)
    except Exception:  # pragma: no cover - defensive
        return None


def find_listener_holders(proc_root: str = "/proc", port: int = 0, exclude_pid: Optional[int] = None) -> List[HolderRef]:
    """Discover processes holding a LISTEN socket on ``port``.

    Reads BOTH /proc/net/tcp and /proc/net/tcp6 (dual-stack / v4-mapped
    rows live ONLY in tcp6). Degrades (returns []) when both tables are
    absent/unparseable. Permission-denied fd dirs are skipped with a
    WARNING (never fabricated). Self (os.getpid() by default) is excluded.
    """
    proc_root = str(proc_root)
    try:
        port_int = int(port)
    except (TypeError, ValueError):
        return []

    listener_inodes: set = set()
    any_table = False
    for fname in ("tcp", "tcp6"):
        rows = _read_table(proc_root, fname)
        if rows is None:
            logger.warning("port-holder: /proc/net/%s absent or unparseable — degrade", fname)
            continue
        any_table = True
        for port_dec, st, inode in rows:
            if st != _TCP_LISTEN_STATE:
                continue
            if port_dec != port_int:
                continue
            if not inode or inode == "0":
                continue
            listener_inodes.add(inode)

    if not any_table:
        logger.warning("port-holder: /proc/net/tcp and tcp6 both absent — no discovery (degrade)")
        return []
    if not listener_inodes:
        return []

    if exclude_pid is None:
        exclude_pid = os.getpid()

    try:
        pids = sorted(int(p) for p in os.listdir(proc_root) if p.isdigit())
    except (OSError, IOError):
        logger.warning("port-holder: cannot list %s — degrade", proc_root)
        return []

    holders: dict = {}
    matched_inodes: set = set()

    for pid in pids:
        if pid == exclude_pid:
            continue
        if matched_inodes == listener_inodes:
            break
        try:
            fd_links = _read_fd_links(pid, proc_root)
        except (OSError, IOError):
            logger.warning(
                "port-holder: fd dir for pid %s unreadable — inode unresolved to pid (permission)", pid
            )
            continue
        for inode in list(listener_inodes - matched_inodes):
            if f"socket:[{inode}]" in fd_links:
                starttime = _read_starttime(pid, proc_root)
                exe = _read_exe(pid, proc_root)
                cwd = _read_cwd(pid, proc_root)
                ppid = _read_ppid(pid, proc_root)
                hr = HolderRef(
                    pid=pid,
                    starttime=starttime,
                    exe=exe,
                    cwd=cwd,
                    ppid=ppid,
                    inode=inode,
                )
                hr.ppid_cmdline_head = _read_cmdline_head(ppid, proc_root)
                holders[pid] = hr
                matched_inodes.add(inode)
                break

    for hr in holders.values():
        hr.install_dir = _str_or_none_path(attribute_install_dir(hr, proc_root))

    return list(holders.values())


def _str_or_none_path(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return str(value)
    except Exception:  # pragma: no cover - defensive
        return None


def attribute_install_dir(holder: Any, proc_root: str = "/proc") -> Optional[Path]:
    """Attribute an install root to a holder (path markers only, never env).

    a. exe: walk ancestors to the first dir containing ``launcher.sh``.
    b. else cwd: if it contains ``launcher.sh`` OR (``pyproject.toml`` +
       ``daemon/``), cwd is the install root.
    c. else None (unattributable ⇒ non-foreign).
    """
    exe = getattr(holder, "exe", None)
    if isinstance(holder, dict):
        exe = holder.get("exe", exe)
    if exe and exe not in ("<unreadable>", None):
        try:
            cur = Path(exe).parent
        except (OSError, ValueError):
            cur = None
        if cur is not None:
            for _ in range(32):
                try:
                    if (cur / "launcher.sh").is_file():
                        return cur
                except OSError:
                    pass
                if cur.parent == cur:
                    break
                cur = cur.parent

    cwd = getattr(holder, "cwd", None)
    if isinstance(holder, dict):
        cwd = holder.get("cwd", cwd)
    if cwd and cwd not in ("<unreadable>", None):
        try:
            cwd_path = Path(cwd)
            if (cwd_path / "launcher.sh").is_file():
                return cwd_path
            if (cwd_path / "pyproject.toml").is_file() and (cwd_path / "daemon").is_dir():
                return cwd_path
        except OSError:
            pass
    return None


def confirm_holder(holder: Any, proc_root: str = "/proc", port: int = 0, delay: float = CONFIRM_REWALK_DELAY_S) -> bool:
    """Two-sample confirmation (D-A2-7, sharpened §5.5).

    Sample 2 RE-RUNS ``find_listener_holders`` (full re-walk) and confirms
    ALL THREE: same (pid, starttime), same inode still present in a freshly
    parsed LISTEN row, unchanged attribution. A pid alive whose socket
    closed does NOT confirm. Claim-evidence refusals are not re-sampled.
    """
    try:
        pid = int(getattr(holder, "pid", None) if not isinstance(holder, dict) else holder.get("pid"))
    except (TypeError, ValueError):
        return False
    starttime = getattr(holder, "starttime", None) if not isinstance(holder, dict) else holder.get("starttime")
    inode = getattr(holder, "inode", None) if not isinstance(holder, dict) else holder.get("inode")
    install_dir = getattr(holder, "install_dir", None) if not isinstance(holder, dict) else holder.get("install_dir")

    time.sleep(delay)
    fresh = find_listener_holders(proc_root, port)
    match = None
    for h in fresh:
        if h.pid == pid and h.starttime == starttime and h.inode == inode:
            match = h
            break
    if match is None:
        return False
    if install_dir is None or match.install_dir is None:
        return False
    if str(install_dir) != str(match.install_dir):
        return False
    try:
        from daemon.services.port_ownership import pid_alive_with_starttime

        if not pid_alive_with_starttime(pid, starttime):
            return False
    except Exception:  # pragma: no cover - defensive
        return False
    return True

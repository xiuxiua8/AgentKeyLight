"""Find the agent process that ran a hook, and later tell whether it still runs.

A hook is started by Codex or Claude Code, sometimes through a shell. The first
ancestor that is not a shell is the agent. Its pid and start time identify it
even if the pid is later reused, so a crashed or killed agent never leaves a
light behind.
"""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass

_PROC_PIDTBSDINFO = 3
_SHELLS = {"sh", "bash", "zsh", "dash", "fish", "ksh", "tcsh", "csh"}


class _BSDInfo(ctypes.Structure):
    _fields_ = [
        ("pbi_flags", ctypes.c_uint32),
        ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * 16),
        ("pbi_name", ctypes.c_char * 32),
        ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


_libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
_libproc.proc_pidinfo.argtypes = [
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_uint64,
    ctypes.c_void_p,
    ctypes.c_int,
]
_libproc.proc_pidinfo.restype = ctypes.c_int


@dataclass(frozen=True)
class Process:
    pid: int
    parent: int
    name: str
    started: float


def process(pid: int) -> Process | None:
    info = _BSDInfo()
    size = ctypes.sizeof(info)
    if (
        pid <= 0
        or _libproc.proc_pidinfo(pid, _PROC_PIDTBSDINFO, 0, ctypes.byref(info), size) != size
    ):
        return None
    return Process(
        pid=pid,
        parent=info.pbi_ppid,
        name=info.pbi_comm.decode(errors="replace").lstrip("-"),
        started=info.pbi_start_tvsec + info.pbi_start_tvusec / 1e6,
    )


def agent(start: int | None = None) -> Process | None:
    """The nearest ancestor of this process that is not a shell."""
    pid = os.getppid() if start is None else start
    for _ in range(8):
        current = process(pid)
        if current is None or current.pid <= 1:
            return None
        if current.name not in _SHELLS:
            return current
        pid = current.parent
    return None


def running(pid: int, started: float) -> bool:
    current = process(pid)
    return current is not None and abs(current.started - started) < 0.01

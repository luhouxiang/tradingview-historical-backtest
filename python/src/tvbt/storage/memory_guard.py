"""Cooperative local-engine protection; measured committed memory, not cache size."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path

MIB = 1024 * 1024


def memory_usage() -> tuple[int, int]:
    """Return process private/RSS bytes and available physical bytes."""
    if os.name == "nt":
        from ctypes import wintypes

        class Status(ctypes.Structure):
            _fields_ = [("length", wintypes.DWORD), ("load", wintypes.DWORD)] + [
                (name, ctypes.c_ulonglong)
                for name in (
                    "total",
                    "available",
                    "page_total",
                    "page_available",
                    "virtual",
                    "virtual_available",
                    "extended",
                )
            ]

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                (name, ctypes.c_size_t)
                for name in (
                    "peak_ws",
                    "ws",
                    "peak_paged",
                    "paged",
                    "peak_nonpaged",
                    "nonpaged",
                    "pagefile",
                    "peak_pagefile",
                    "private",
                )
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        status = Status()
        status.length = ctypes.sizeof(status)
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        if not kernel.GlobalMemoryStatusEx(ctypes.byref(status)) or not psapi.GetProcessMemoryInfo(
            kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb
        ):
            raise OSError("Cannot measure engine memory for resource protection")
        return int(counters.private), int(status.available)
    # The supported CI/runtime platforms are Windows and Linux.
    pages = int(Path("/proc/self/statm").read_text().split()[1])
    available = next(
        int(line.split()[1]) * 1024
        for line in Path("/proc/meminfo").read_text().splitlines()
        if line.startswith("MemAvailable:")
    )
    import mmap

    return pages * mmap.PAGESIZE, available


def check_memory() -> None:
    limit = int(os.environ.get("TVBT_CHAN_MEMORY_LIMIT_MB", "2048"))
    if limit < 128:
        raise ValueError("TVBT_CHAN_MEMORY_LIMIT_MB must be at least 128")
    used, available = memory_usage()
    if used >= limit * MIB or available < 512 * MIB:
        raise MemoryError(
            f"缠论计算已触发内存保护：进程 {used // MIB} MiB，上限 {limit} MiB，"
            f"系统可用 {available // MIB} MiB；请释放内存后重试。未生成不完整结果。"
        )

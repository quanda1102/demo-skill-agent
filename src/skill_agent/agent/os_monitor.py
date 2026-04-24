"""Read-only OS monitoring helpers — no elevated privileges required."""
from __future__ import annotations

import json
import platform
import time
from typing import Any

import psutil


def _safe_proc_info(proc: psutil.Process, fields: list[str]) -> dict[str, Any] | None:
    try:
        info = proc.as_dict(attrs=fields)
        # cpu_percent needs a second sample; give it a tiny window
        return info
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return None


def _os_info() -> dict[str, Any]:
    u = platform.uname()
    boot_ts = psutil.boot_time()
    uptime_s = int(time.time() - boot_ts)
    return {
        "system": u.system,
        "node": u.node,
        "release": u.release,
        "version": u.version,
        "machine": u.machine,
        "processor": u.processor,
        "python_version": platform.python_version(),
        "uptime_seconds": uptime_s,
        "uptime_human": _fmt_uptime(uptime_s),
        "cpu_count_logical": psutil.cpu_count(logical=True),
        "cpu_count_physical": psutil.cpu_count(logical=False),
    }


def _cpu_info(interval: float = 0.5) -> dict[str, Any]:
    freq = psutil.cpu_freq()
    load = psutil.getloadavg()  # 1, 5, 15 min averages
    return {
        "cpu_percent_overall": psutil.cpu_percent(interval=interval),
        "cpu_percent_per_core": psutil.cpu_percent(interval=None, percpu=True),
        "load_avg_1m": round(load[0], 2),
        "load_avg_5m": round(load[1], 2),
        "load_avg_15m": round(load[2], 2),
        "freq_current_mhz": round(freq.current, 1) if freq else None,
        "freq_max_mhz": round(freq.max, 1) if freq and freq.max else None,
    }


def _memory_info() -> dict[str, Any]:
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    return {
        "ram_total_mb": _to_mb(vm.total),
        "ram_available_mb": _to_mb(vm.available),
        "ram_used_mb": _to_mb(vm.used),
        "ram_percent": vm.percent,
        "swap_total_mb": _to_mb(sw.total),
        "swap_used_mb": _to_mb(sw.used),
        "swap_percent": sw.percent,
    }


def _disk_info() -> list[dict[str, Any]]:
    results = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        results.append({
            "device": part.device,
            "mountpoint": part.mountpoint,
            "fstype": part.fstype,
            "total_gb": round(usage.total / 1024**3, 2),
            "used_gb": round(usage.used / 1024**3, 2),
            "free_gb": round(usage.free / 1024**3, 2),
            "percent": usage.percent,
        })
    return results


def _network_info() -> dict[str, Any]:
    io = psutil.net_io_counters()
    # net_connections requires elevated privileges on macOS — degrade gracefully
    status_counts: dict[str, int] | None = None
    try:
        conns = psutil.net_connections(kind="inet")
        status_counts = {}
        for c in conns:
            s = c.status or "NONE"
            status_counts[s] = status_counts.get(s, 0) + 1
    except psutil.AccessDenied:
        status_counts = None
    result: dict[str, Any] = {
        "bytes_sent_mb": _to_mb(io.bytes_sent),
        "bytes_recv_mb": _to_mb(io.bytes_recv),
        "packets_sent": io.packets_sent,
        "packets_recv": io.packets_recv,
        "errin": io.errin,
        "errout": io.errout,
        "dropin": io.dropin,
        "dropout": io.dropout,
    }
    if status_counts is not None:
        result["connection_status_counts"] = status_counts
    else:
        result["connection_status_counts"] = "unavailable (requires elevated privileges on this OS)"
    return result


def _top_processes(top_k: int, sort_by: str) -> list[dict[str, Any]]:
    attrs = ["pid", "name", "username", "status", "cpu_percent", "memory_percent", "memory_info", "cmdline"]
    # first pass: seed cpu_percent (needs two calls with interval)
    for proc in psutil.process_iter(["cpu_percent"]):
        pass
    time.sleep(0.3)

    procs: list[dict[str, Any]] = []
    for proc in psutil.process_iter(attrs):
        info = _safe_proc_info(proc, attrs)
        if info is None:
            continue
        rss_mb = None
        if info.get("memory_info"):
            rss_mb = _to_mb(info["memory_info"].rss)
        cmdline = info.get("cmdline") or []
        procs.append({
            "pid": info.get("pid"),
            "name": info.get("name"),
            "username": info.get("username"),
            "status": info.get("status"),
            "cpu_percent": round(info.get("cpu_percent") or 0.0, 2),
            "memory_percent": round(info.get("memory_percent") or 0.0, 2),
            "rss_mb": rss_mb,
            "cmdline": " ".join(cmdline[:6]) if cmdline else "",
        })

    key = "memory_percent" if sort_by == "memory" else "cpu_percent"
    procs.sort(key=lambda p: p.get(key) or 0.0, reverse=True)
    return procs[:top_k]


# ── public entry point ────────────────────────────────────────────────────────

def collect_os_metrics(
    include: list[str] | None = None,
    top_k: int = 10,
    sort_processes_by: str = "cpu",
) -> str:
    """
    Collect read-only OS metrics and return a JSON string.

    include: any subset of ["os_info","cpu","memory","disk","network","top_processes"].
             Defaults to all sections.
    top_k: how many top processes to return (max 50).
    sort_processes_by: "cpu" or "memory".
    """
    sections = set(include or ["os_info", "cpu", "memory", "disk", "network", "top_processes"])
    top_k = min(max(1, top_k), 50)

    result: dict[str, Any] = {}

    if "os_info" in sections:
        result["os_info"] = _os_info()
    if "cpu" in sections:
        result["cpu"] = _cpu_info()
    if "memory" in sections:
        result["memory"] = _memory_info()
    if "disk" in sections:
        result["disk"] = _disk_info()
    if "network" in sections:
        result["network"] = _network_info()
    if "top_processes" in sections:
        result["top_processes"] = _top_processes(top_k, sort_by=sort_processes_by)

    return json.dumps(result, ensure_ascii=False, indent=2)


# ── helpers ───────────────────────────────────────────────────────────────────

def _to_mb(b: int) -> float:
    return round(b / 1024**2, 2)


def _fmt_uptime(seconds: int) -> str:
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if d:
        parts.append(f"{d}d")
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)

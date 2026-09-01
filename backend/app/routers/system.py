"""Служебная информация о системе (только admin): окружение (docker/VM/bare metal),
uptime + CPU/MEM/DISK.

Источники: /proc, /sys/class/dmi/id, cgroup v1/v2, systemd-detect-virt (если есть).
Значения кэшируются на 30 с (CPU меряется сэмплом /proc/stat за 0.5 с).
"""
import asyncio
import os
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path

from fastapi import APIRouter, Depends
from ..security import require_role

router = APIRouter(prefix="/api", tags=["system"])

# время импорта модуля ≈ старт приложения (uvicorn поднимает его один раз)
APP_STARTED_AT = time.time()

CACHE_TTL = 30
_cache: list = [0.0, {}]

# DMI-подсказки → человекочитаемое имя виртуализации
VM_HINTS = [
    ("kvm", "KVM"), ("qemu", "KVM/QEMU"), ("proxmox", "Proxmox (KVM)"),
    ("virtualbox", "VirtualBox"), ("innotek", "VirtualBox"),
    ("vmware", "VMware"),
    ("virtual machine", "Hyper-V (Microsoft)"), ("microsoft corporation", "Hyper-V (Microsoft)"),
    ("hyperv", "Hyper-V"),
    ("amazon ec2", "Amazon EC2"), ("google compute", "Google Cloud"),
    ("google", "Google Cloud"), ("alibaba", "Alibaba Cloud ECS"),
    ("tencent", "Tencent Cloud"), ("baidu", "Baidu Cloud"),
    ("openstack", "OpenStack"), ("ovirt", "oVirt/RHV"),
    ("red hat virtualization", "oVirt/RHV"), ("bochs", "Bochs"),
    ("parallels", "Parallels"), ("apple virtual", "Apple Virtualization"),
]

_DMI_JUNK = {"", "system product name", "to be filled by o.e.m.", "default string",
             "unknown", "none", "system manufacturer"}


def _docker_info() -> tuple[bool, str]:
    in_docker = os.path.exists("/.dockerenv")
    container_id = ""
    try:
        with open("/proc/self/cgroup") as f:
            cgroup = f.read()
        if not in_docker:
            in_docker = ("docker" in cgroup or "containerd" in cgroup or "kubepods" in cgroup)
        m = re.search(r"docker[-/](?:sha256:)?([0-9a-f]{12,64})", cgroup)
        if m:
            container_id = m.group(1)[:12]
    except Exception:
        pass
    return in_docker, container_id


def _dmi_name() -> str:
    """Первое осмысленное DMI-имя: product_name → sys_vendor → board_name."""
    for f in ("/sys/class/dmi/id/product_name", "/sys/class/dmi/id/sys_vendor",
              "/sys/class/dmi/id/board_name"):
        try:
            with open(f) as fh:
                val = fh.read().strip()
        except Exception:
            continue
        if val.lower() not in _DMI_JUNK:
            return val
    return ""


def _virt_label(dmi: str) -> str:
    d = dmi.lower()
    for hint, label in VM_HINTS:
        if hint in d:
            return label
    return ""


def _detect_env() -> tuple[str, str]:
    """(env, env_label): docker | vm | bare_metal — автоопределение."""
    # 1) контейнер (docker/containerd/k8s)
    in_docker, cid = _docker_info()
    if in_docker:
        return "docker", f"Docker{(' ' + cid) if cid else ''}"
    # 2) VM
    dmi = _dmi_name()
    virt = ""
    if shutil.which("systemd-detect-virt"):
        try:
            out = subprocess.run(["systemd-detect-virt"], capture_output=True, text=True, timeout=3)
            v = out.stdout.strip().lower()
            if v and v != "none":
                virt = _virt_label(v) or v
        except Exception:
            pass
    if not virt:
        try:
            with open("/proc/cpuinfo") as f:
                if re.search(r"^flags:.*\bhypervisor\b", f.read(), re.M):
                    virt = _virt_label(dmi) or "виртуализация"
        except Exception:
            pass
    if not virt:
        virt = _virt_label(dmi)
    if virt:
        return "vm", f"VM ({virt})"
    # 3) bare metal
    return "bare_metal", f"Bare metal{(' — ' + dmi) if dmi else ''}"


def _cpu_times() -> tuple[int, int]:
    """(idle, total) из первой строки /proc/stat."""
    with open("/proc/stat") as f:
        parts = f.readline().split()[1:]
    vals = [int(x) for x in parts]
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)  # idle + iowait
    return idle, sum(vals)


def _cpu_pct() -> float:
    i1, t1 = _cpu_times()
    time.sleep(0.5)
    i2, t2 = _cpu_times()
    dt, di = t2 - t1, i2 - i1
    if dt <= 0:
        return 0.0
    return max(0.0, min(100.0, round(100.0 * (1 - di / dt), 1)))


def _cgroup_mem() -> tuple[int, int] | None:
    """(used, total) из cgroup; None — если лимиты/файлы недоступны (host-view)."""
    try:
        # cgroup v2
        with open("/sys/fs/cgroup/memory.current") as f:
            used = int(f.read().strip())
        with open("/sys/fs/cgroup/memory.max") as f:
            mx = f.read().strip()
        total = 0 if mx == "max" else int(mx)
        if total:
            return used, total
    except Exception:
        pass
    try:
        # cgroup v1
        with open("/sys/fs/cgroup/memory/memory.usage_in_bytes") as f:
            used = int(f.read().strip())
        with open("/sys/fs/cgroup/memory/memory.limit_in_bytes") as f:
            total = int(f.read().strip())
        if total < (1 << 60):  # 2^60 ≈ «безлимита» в v1
            return used, total
    except Exception:
        pass
    return None


def _mem() -> dict:
    cg = _cgroup_mem()
    if cg:
        used, total = cg
    else:
        mi = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                mi[k] = int(v.strip().split()[0]) * 1024  # kB -> B
        total = mi.get("MemTotal", 0)
        used = total - mi.get("MemAvailable", 0)
    return {
        "used_gb": round(used / 1024 ** 3, 1),
        "total_gb": round(total / 1024 ** 3, 1),
        "pct": round(100.0 * used / total, 1) if total else 0.0,
    }


def _disk() -> dict:
    du = shutil.disk_usage("/")
    return {
        "used_gb": round(du.used / 1024 ** 3, 1),
        "total_gb": round(du.total / 1024 ** 3, 1),
        "pct": round(100.0 * du.used / du.total, 1) if du.total else 0.0,
    }


async def _db_info() -> dict | None:
    """БД: размер и доля на диске + аптайм и соединения.

    PostgreSQL (отдельный контейнер): up — pg_postmaster_start_time, conns —
    pg_stat_activity; cpu/ram процесса postgres из-за контейнера недоступны
    (только `docker stats` с хоста). SQLite: up — аптайм процесса (БД в нём).
    """
    from ..config import settings as cfg
    url = cfg.DATABASE_URL
    try:
        size: int
        up_s: int | None = None
        conns: int | None = None
        if url.startswith("postgresql"):
            import asyncpg
            dsn = url.replace("postgresql+asyncpg://", "postgresql://")
            conn = await asyncio.wait_for(asyncpg.connect(dsn, timeout=5), timeout=6)
            try:
                size = await conn.fetchval("select pg_database_size(current_database())")
                up_s = int(await conn.fetchval(
                    "select extract(epoch from (now() - pg_postmaster_start_time()))::bigint"))
                conns = int(await conn.fetchval("select count(*) from pg_stat_activity"))
            finally:
                await conn.close()
        else:
            m = re.search(r"///(.+?)\??$", url)
            p = Path(m.group(1)) if m else None
            if not p or not p.is_file():
                return None
            size = p.stat().st_size
            up_s = int(time.time() - APP_STARTED_AT)  # БД живёт в процессе ядра
        du = shutil.disk_usage("/")
        return {
            "bytes": int(size),
            "size_gb": round(size / 1024 ** 3, 3),
            "pct": round(100.0 * size / du.total, 2) if du.total else 0.0,
            "up_s": up_s,
            "conns": conns,
        }
    except Exception:
        return None


async def _gather() -> dict:
    in_docker, container_id = _docker_info()
    env, env_label = _detect_env()
    return {
        "env": env,
        "env_label": env_label,
        "in_docker": in_docker,
        "container_id": container_id,
        "uptime_s": int(time.time() - APP_STARTED_AT),
        "cpu_pct": _cpu_pct(),
        "mem": _mem(),
        "disk": _disk(),
        "db": await _db_info(),
        "platform": f"{platform.system()} {platform.release()}",
    }


@router.get("/system/info")
async def system_info(user=Depends(require_role("admin"))):
    ts, cached = _cache
    if time.time() - ts < CACHE_TTL and cached:
        out = dict(cached)
        out["uptime_s"] = int(time.time() - APP_STARTED_AT)  # uptime держим живым
        return out
    try:
        info = await _gather()
    except Exception:
        info = dict(cached) if cached else {"env": "unknown", "env_label": "—",
                                            "in_docker": False, "container_id": "",
                                            "uptime_s": 0, "cpu_pct": 0.0,
                                            "mem": {"used_gb": 0, "total_gb": 0, "pct": 0},
                                            "disk": {"used_gb": 0, "total_gb": 0, "pct": 0},
                                            "db": None, "platform": ""}
    _cache[0], _cache[1] = time.time(), info
    return info

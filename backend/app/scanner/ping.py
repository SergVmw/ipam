"""Обход сети: fping, nmap или TCP-проба. Метод выбирается в Настройках (auto/fping/nmap/tcp).

Возврат sweep(): {ip: {"mac": str|None, "vendor": str|None}}
MAC и vendor даёт только nmap — и только для хостов на том же L2-сегменте (ARP).
"""
import asyncio
import re
import shutil
from ipaddress import ip_address, ip_network

# exit 1 у fping — НЕ ошибка: «не все хосты ответили» (норма при свипе).
# exit 0 — все живы, 2+ — фатальная ошибка.
# Кроме того, в stderr fping пишет диагностический шум вида
# «ICMP Host Unreachable from X for ICMP Echo sent to Y» — это не ошибка.
_FPING_OK_EXIT = (0, 1)
_FPING_ERROR_MARKERS = ("fping:", "invalid option", "permission denied", "can't create", "cannot create")

_MAC_RE = re.compile(r"^[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}$")


def _fping_error(stderr: str) -> bool:
    low = stderr.lower()
    if not low:
        return False
    return any(m in low for m in _FPING_ERROR_MARKERS)


def _parse_ips(text: str) -> set[str]:
    alive: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        try:
            ip_address(line)
            alive.add(line)
        except ValueError:
            continue
    return alive


def _resolve_method(method: str) -> str:
    # auto: nmap в приоритете — он отдаёт MAC + vendor; fping быстрее, но без MAC
    if method == "auto":
        if shutil.which("nmap"):
            return "nmap"
        if shutil.which("fping"):
            return "fping"
        return "tcp"
    if method in ("fping", "nmap", "tcp"):
        return method
    return "auto"


def _parse_nmap(out: str) -> dict[str, dict]:
    """Обычный вывод nmap -sn (НЕ -oG: в grepable-формате нет строк MAC Address!).

      Nmap scan report for 10.0.0.5
      Host is up (0.044s latency).
      MAC Address: 00:11:22:33:44:55 (VMware)
    """
    res: dict[str, dict] = {}
    cur: str | None = None
    up = False
    mac: str | None = None
    vendor: str | None = None

    def flush() -> None:
        nonlocal cur, up, mac, vendor
        if up and cur is not None:
            res[cur] = {"mac": mac, "vendor": vendor}
        cur, up, mac, vendor = None, False, None, None

    for line in out.splitlines():
        if line.startswith("Nmap scan report for "):
            flush()
            spec = line.split()[-1]
            if spec.startswith("(") and spec.endswith(")"):
                spec = spec[1:-1]
            try:
                ip_address(spec)
                cur = spec
            except ValueError:
                cur = None
        elif line.startswith("Host is up"):
            up = True
        elif line.startswith("Host is down"):
            up = False
            cur = None
        elif line.startswith("MAC Address:") and up and cur is not None:
            parts = line.split()
            if len(parts) >= 3 and _MAC_RE.match(parts[2]):
                m = parts[2].lower()
                if m != "00:00:00:00:00:00":  # нулевой MAC (туннель/контейнер) не сохраняем
                    mac = m
                if len(parts) >= 4:
                    v = " ".join(parts[3:]).strip()
                    if v.startswith("(") and v.endswith(")"):
                        v = v[1:-1].strip()
                    vendor = v or None
        if line.startswith("Nmap done"):
            flush()
    flush()
    return res


async def sweep(cidr: str, method: str = "auto", timeout_ms: int = 500, rate: int = 500,
                ports: list[int] | None = None, diag: dict | None = None) -> dict[str, dict]:
    """Вернёт {ip: {"mac": mac|None, "vendor": vendor|None}} живых хостов в сети cidr.

    Реальные ошибки (нет бинарника, нет прав, тайм-аут) поднимаются как RuntimeError.

    diag — опциональный dict, который sweep заполняет для ЛОГА СКАНЕРА:
    method (разрешённый), exit_code, stderr_tail, alive_ips, duration_ms и т.п.
    """
    import time as _time
    t0 = _time.perf_counter()

    def _finish(extra: dict | None = None) -> None:
        if diag is not None:
            diag["duration_ms"] = int((_time.perf_counter() - t0) * 1000)
            if extra:
                diag.update(extra)

    m = _resolve_method(method)
    if diag is not None:
        diag.update(method=m, method_requested=method, cidr=cidr)
    if m == "fping":
        fping = shutil.which("fping")
        if not fping:
            _finish({"error": "fping не установлен (выбран явно)"})
            raise RuntimeError("fping не установлен (выбран явно)")
        # -i (интервал, мс) эквивалентен лимиту pings/sec; -G не во всех версиях fping
        interval_ms = max(1, 1000 // max(1, rate))
        proc = await asyncio.create_subprocess_exec(
            fping, "-a", "-g", cidr, "-t", str(timeout_ms), "-i", str(interval_ms),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        alive = _parse_ips(out.decode(errors="ignore"))
        stderr_tail = err.decode(errors="ignore").strip()
        if diag is not None:
            diag.update(
                exit_code=proc.returncode,
                stderr_tail=stderr_tail[-800:] or None,
                alive_ips=sorted(alive),
                fping_bin=fping,
            )
        if alive:
            _finish()
            return {ip: {"mac": None, "vendor": None} for ip in alive}
        if proc.returncode not in _FPING_OK_EXIT or _fping_error(stderr_tail):
            _finish({"error": f"fping (exit {proc.returncode})"})
            raise RuntimeError(f"fping (exit {proc.returncode}): {stderr_tail[-300:] or 'без деталей'}")
        _finish()
        return {}
    if m == "nmap":
        if not shutil.which("nmap"):
            _finish({"error": "nmap не установлен (выбран явно)"})
            raise RuntimeError("nmap не установлен (выбран явно)")
        parsed = await nmap_sweep(cidr, rate, diag=diag)
        _finish()
        return parsed
    if ports is not None and diag is not None:
        diag["ports"] = list(ports)
    alive = await tcp_probe(cidr, timeout_ms, ports or [22, 80, 443, 3389])
    if diag is not None:
        diag["alive_ips"] = sorted(alive)
    _finish()
    return {ip: {"mac": None, "vendor": None} for ip in alive}


async def nmap_sweep(cidr: str, rate: int, diag: dict | None = None) -> dict[str, dict]:
    """nmap -sn (только host discovery, без портов). На L2-сегменте отдаёт MAC + vendor.

    ВАЖНО: без root/NET_RAW (типичный docker) nmap делает host discovery
    TCP-пробами и пишет в stderr «Host discovery results are unreliable without
    root» — живыми считаются хосты с ОТКРЫТЫМИ ПОРТАМИ (лог сканера это покажет).
    """
    args = ["nmap", "-sn", "-n", "--no-stylesheet",
            f"--min-rate={max(1, rate)}", "--max-retries", "1", cidr]
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    hosts_n = ip_network(cidr).num_addresses
    limit = max(60.0, (hosts_n // max(1, rate)) * 3 + 60)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=limit)
    except asyncio.TimeoutError:
        proc.kill()
        if diag is not None:
            diag["error"] = f"nmap: тайм-аут ({int(limit)} с)"
        raise RuntimeError(f"nmap: тайм-аут ({int(limit)} с)")
    stderr_tail = err.decode(errors="ignore").strip()
    if diag is not None:
        diag.update(exit_code=proc.returncode, stderr_tail=stderr_tail[-800:] or None)
    parsed = _parse_nmap(out.decode(errors="ignore"))
    if diag is not None:
        diag["alive_ips"] = sorted(parsed.keys())
    if parsed:
        return parsed  # нашли живых — результат готов, exit-код не важен
    if proc.returncode != 0:
        if diag is not None:
            diag["error"] = f"nmap (exit {proc.returncode})"
        raise RuntimeError(f"nmap (exit {proc.returncode}): {stderr_tail[-300:] or 'без деталей'}")
    return parsed


async def tcp_probe(cidr: str, timeout_ms: int, ports: list[int]) -> set[str]:
    """Fallback без fping: TCP-соединение на несколько портов. Только для сетей до /22."""
    hosts = [str(h) for h in ip_network(cidr).hosts()]
    if len(hosts) > 4096:
        raise RuntimeError("fping не установлен — TCP-fallback работает только для сетей до /22. Установите fping.")
    timeout = timeout_ms / 1000.0
    sem = asyncio.Semaphore(200)

    async def probe(ip: str) -> str | None:
        for port in ports:
            try:
                async with sem:
                    _r, w = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout)
                w.close()
                try:
                    await w.wait_closed()
                except Exception:
                    pass
                return ip
            except Exception:
                continue
        return None

    results = await asyncio.gather(*(probe(h) for h in hosts))
    return {r for r in results if r is not None}

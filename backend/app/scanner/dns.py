"""Reverse DNS (PTR) через dnspython, fallback — системный резолвер (/etc/hosts)."""
import asyncio
import socket


async def resolve_ptrs(ips: list[str], servers: list[str] | None = None, timeout: float = 2.0) -> dict[str, str]:
    """ip -> hostname (только найденные)."""
    if not ips:
        return {}
    result: dict[str, str] = {}
    try:
        import dns.asyncresolver

        resolver = dns.asyncresolver.Resolver(configure=not servers)
        if servers:
            resolver.nameservers = servers
        resolver.timeout = timeout
        resolver.lifetime = timeout

        async def one(ip: str) -> None:
            try:
                rec = await resolver.resolve(ip, "PTR")
                name = str(rec[0].target).rstrip(".")
                if name:
                    result[ip] = name
            except Exception:
                pass

        await asyncio.gather(*(one(ip) for ip in ips))
    except Exception:
        pass

    # fallback: OS-резолвер (видит /etc/hosts и локальные зоны)
    missing = [ip for ip in ips if ip not in result]
    if missing:
        def gethbyaddr(ip: str) -> str | None:
            try:
                return socket.gethostbyaddr(ip)[0]
            except Exception:
                return None

        names = await asyncio.gather(*(asyncio.to_thread(gethbyaddr, ip) for ip in missing))
        for ip, name in zip(missing, names):
            if name:
                result[ip] = name
    return result

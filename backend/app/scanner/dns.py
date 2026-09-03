"""Reverse DNS (PTR) с логированием: какой DNS-сервер отвечал и ответил ли.

Высокоуровневый Resolver.resolve() сам перебирает серверы и НЕ сообщает, кто
реально ответил (и молча падает по таймаутам). Поэтому здесь каждый настроенный
DNS-сервер опрашивается отдельно (dns.asyncquery), а исход пишется в двух местах:

  * logging.getLogger("ipam.scanner.dns") — построчно в stdout контейнера
    (по каждой попытке: ip, сервер, ответил ли, rcode, время ответа);
  * возвращаемая агрегированная статистика кладётся в запись «Лог сканера»
    (вкладка в UI, поле "dns") — по каждому серверу: сколько запросов получил,
    сколько раз ответил, с каким rcode, сколько таймаутов, средний RTT.

Различие, важное для диагностики:
  * «сервер ответил, но записи PTR нет» (NXDOMAIN / NOERROR без ответа) —
    это НЕ «сервер не ответил»; в логе rcode, а не таймаут;
  * «сервер не ответил» (timeout / сетевая ошибка) — считается отдельно.

Секция DNS в логе сканера присутствует ВСЕГДА (поле "dns"), чтобы было видно,
какие серверы настроены и выполнялся ли PTR вообще:
  * enabled=false — «разрешать hostname (PTR)» выключено в Настройках;
  * enabled=true, attempted=0 — PTR не требовался (у всех живых hostname известен);
  * enabled=true, attempted>0 — реальные запросы и их результат по серверам.

Если PTR не найден ни одним DNS — доп. попытка через системный резолвер
(socket.gethostbyaddr), который дополнительно видит /etc/hosts.
"""
import asyncio
import logging
import socket
import time

log = logging.getLogger("ipam.scanner.dns")


def _parse_ns(ns: str) -> tuple[str, int]:
    """'10.0.0.2' / '10.0.0.2:5353' / '[2001:db8::1]:53' -> (host, port)."""
    ns = (ns or "").strip()
    if not ns:
        return "", 0
    port = 53
    if ns.startswith("["):  # [IPv6]:port
        end = ns.find("]")
        if end != -1:
            host = ns[1:end]
            rest = ns[end + 1:]
            if rest.startswith(":") and rest[1:].isdigit():
                port = int(rest[1:])
            return host, port
    if ns.count(":") == 1:  # IPv4:port (голый IPv6 без скобок не режем)
        host, _, p = ns.rpartition(":")
        if p.isdigit():
            return host, int(p)
    return ns, port


def _dedupe(items: list[tuple[str, int]]) -> list[tuple[str, int]]:
    seen: set[tuple[str, int]] = set()
    out: list[tuple[str, int]] = []
    for h, p in items:
        if h and (h, p) not in seen:
            seen.add((h, p))
            out.append((h, p))
    return out


def _nameservers(servers: list[str] | None) -> tuple[str, list[tuple[str, int]]]:
    """Реальный список DNS к опросу: (mode, [(host, port), ...]).

    mode='custom' — серверы из Настроек; mode='system' — nameservers
    системного резолвера (/etc/resolv.conf), как dnspython configure=True.
    """
    if servers:
        return "custom", _dedupe([_parse_ns(s) for s in servers])
    try:
        import dns.asyncresolver

        return "system", _dedupe(
            [_parse_ns(s) for s in (dns.asyncresolver.Resolver(configure=True).nameservers or [])]
        )
    except Exception as e:
        log.warning("dns: не удалось прочитать системные резолверы: %s", e)
        return "system", []


def _ptr_of(resp) -> str | None:
    """Первая PTR-запись в ответе (hostname без точки на конце) или None."""
    try:
        import dns.rdatatype

        for rrset in resp.answer:
            if rrset.rdtype == dns.rdatatype.PTR:
                for rd in rrset:
                    return str(rd.target).rstrip(".")
    except Exception:
        return None
    return None


def _mkstat(host: str, port: int) -> dict:
    return {
        "server": f"{host}:{port}",
        "host": host,
        "port": port,
        "queries": 0,      # сколько запросов отправлено серверу
        "answered": 0,     # сколько DNS-ответов получено (rcode любой)
        "ok": 0,           # ответил и в ответе была PTR-запись (ответ принят)
        "empty": 0,        # ответил, но записи нет: NXDOMAIN / NOERROR без ответа
        "refused": 0,      # ответил REFUSED
        "servfail": 0,     # ответил SERVFAIL
        "timeouts": 0,     # НЕ ответил за timeout
        "errors": 0,       # прочие ошибки (сеть и т.п.)
        "rtt_sum_ms": 0.0,
        "rtt_n": 0,
    }


def _row_dict(st: dict) -> dict:
    return {
        "server": st["server"],
        "port": st["port"],
        "queries": st["queries"],
        "answered": st["answered"],
        "ok": st["ok"],
        "empty": st["empty"],
        "refused": st["refused"],
        "servfail": st["servfail"],
        "timeouts": st["timeouts"],
        "errors": st["errors"],
        "rtt_avg_ms": round(st["rtt_sum_ms"] / st["rtt_n"], 1) if st["rtt_n"] else None,
    }


def dns_config_stats(servers: list[str] | None, enabled: bool = True) -> dict:
    """«Пустая» статистика без запросов (для лога сканера).

    Нужна, когда PTR-резолв выключен (enabled=False) либо когда резолвить
    нечего (attempted=0) — чтобы в логе всё равно было видно, какие DNS
    настроены и использовались бы.
    """
    mode, configured = _nameservers(servers)
    return {
        "enabled": enabled,
        "mode": mode,
        "configured": [f"{h}:{p}" for h, p in configured],
        "attempted": 0,
        "resolved_by_dns": 0,
        "resolved_by_fallback": 0,
        "unresolved": 0,
        "by_server": [_row_dict(_mkstat(h, p)) for h, p in configured],
    }


async def resolve_ptrs(ips: list[str], servers: list[str] | None = None,
                       timeout: float = 2.0) -> tuple[dict[str, str], dict]:
    """PTR-резолв с логированием серверов.

    Возвращает (ip -> hostname для найденных, статистика DNS для лога сканера).

    servers — список "host[:port]" пользовательских DNS (из Настроек/окружения);
    пусто/None — системный резолвер (как dnspython Resolver(configure=True),
    т.е. nameservers из /etc/resolv.conf).
    """
    if not ips:
        # резолвить нечего — но вернём конфигурацию DNS, чтобы секция
        # «DNS» в логе сканера была видна и в таком скане
        return {}, dns_config_stats(servers, enabled=True)

    ips = list(dict.fromkeys(ips))  # без дублей
    mode, configured = _nameservers(servers)

    by_server: dict[str, dict] = {}
    for host, port in configured:
        st = _mkstat(host, port)
        by_server[st["server"]] = st

    result: dict[str, str] = {}
    failed: list[str] = []

    if configured:
        try:
            import dns.asyncquery
            import dns.exception
            import dns.message
            import dns.rcode
            import dns.reversename

            def _outcome(ip: str, st: dict, rcode_text: str | None,
                         name: str | None, rtt_ms: float) -> None:
                """Одна попытка (ip, сервер) -> строка в stdout-лог."""
                if name:
                    log.info(
                        "dns: ip=%s сервер=%s ОТВЕТИЛ rcode=%s rtt_ms=%.1f ptr=%s",
                        ip, st["server"], rcode_text, rtt_ms, name)
                elif rcode_text in ("REFUSED", "SERVFAIL"):
                    log.warning(
                        "dns: ip=%s сервер=%s ответил, но rcode=%s (запись не получена)",
                        ip, st["server"], rcode_text)
                elif rcode_text:
                    log.info(
                        "dns: ip=%s сервер=%s ответил rcode=%s, записи PTR нет",
                        ip, st["server"], rcode_text)
                else:
                    log.warning("dns: ip=%s сервер=%s НЕ ОТВЕТИЛ", ip, st["server"])

            async def one(ip: str) -> None:
                q = dns.message.make_query(dns.reversename.from_address(ip), "PTR")
                for host, port in configured:
                    key = f"{host}:{port}"
                    st = by_server[key]
                    st["queries"] += 1
                    t0 = time.perf_counter()
                    try:
                        resp = await dns.asyncquery.udp(q, host, timeout=timeout, port=port)
                        rtt_ms = (time.perf_counter() - t0) * 1000
                        st["answered"] += 1
                        st["rtt_sum_ms"] += rtt_ms
                        st["rtt_n"] += 1
                        rcode_text = dns.rcode.to_text(resp.rcode())
                        name = _ptr_of(resp)
                        _outcome(ip, st, rcode_text, name, rtt_ms)
                        if name:
                            st["ok"] += 1
                            result[ip] = name
                            return
                        if rcode_text == "REFUSED":
                            st["refused"] += 1
                        elif rcode_text == "SERVFAIL":
                            st["servfail"] += 1
                        else:  # NXDOMAIN / NOERROR без записи — сервер жив, записи нет
                            st["empty"] += 1
                        # записи нет на этом сервере — пробуем следующий из списка
                        continue
                    except dns.exception.Timeout:
                        rtt_ms = (time.perf_counter() - t0) * 1000
                        st["timeouts"] += 1
                        _outcome(ip, st, None, None, rtt_ms)
                        continue  # сервер не ответил — следующий
                    except Exception as e:
                        rtt_ms = (time.perf_counter() - t0) * 1000
                        st["errors"] += 1
                        log.warning("dns: ip=%s сервер=%s ошибка: %s", ip, key, e)
                        continue
                failed.append(ip)

            await asyncio.gather(*(one(ip) for ip in ips))
        except ImportError:
            log.warning("dns: dnspython не установлен — только системный резолвер")
            failed = list(ips)
        except Exception as e:
            log.warning("dns: сбой PTR-резолва: %s", e)
            failed = list(ips)
    else:
        failed = list(ips)

    # fallback: OS-резолвер (видит /etc/hosts и локальные зоны)
    # failed = IP, которые не разрешились ни одним DNS-сервером (или DNS не был доступен)
    resolved_by_fallback = 0
    if failed:
        def gethbyaddr(ip: str) -> str | None:
            try:
                return socket.gethostbyaddr(ip)[0]
            except Exception:
                return None

        names = await asyncio.gather(*(asyncio.to_thread(gethbyaddr, ip) for ip in failed))
        for ip, name in zip(failed, names):
            if name:
                resolved_by_fallback += 1
                result[ip] = name
                log.info("dns: ip=%s имя найдено системным резолвером (/etc/hosts?): %s", ip, name)

    # ---- агрегированная статистика для «Лога сканера» (UI) ----
    resolved_by_dns = len(result) - resolved_by_fallback
    stats: dict = {
        "enabled": True,
        "mode": mode,
        "configured": [s["server"] for s in by_server.values()],  # все настроенные (host:port)
        "attempted": len(ips),
        "resolved_by_dns": resolved_by_dns,
        "resolved_by_fallback": resolved_by_fallback,
        "unresolved": len(ips) - len(result),
        "by_server": [_row_dict(st) for st in by_server.values()],
    }

    # краткая сводка в stdout-лог (одна строка на скан)
    _log_summary(stats)
    return result, stats


def _log_summary(stats: dict) -> None:
    src = ("пользовательские серверы: " + ", ".join(stats["configured"])) if stats["mode"] == "custom" \
        else "системный резолвер (/etc/resolv.conf)"
    ok_lines = [s["server"] for s in stats["by_server"] if s["answered"] and s["queries"]]
    dead = [s for s in stats["by_server"] if s["queries"] and not s["answered"]]
    srv_note = f"ответил: {', '.join(ok_lines) or '—'}"
    if dead:
        srv_note += f"; НЕ ответил: {', '.join(s['server'] for s in dead)}"
    unused = [s["server"] for s in stats["by_server"] if not s["queries"]]
    if unused:
        srv_note += f"; не опрашивался: {', '.join(unused)}"
    log.info(
        "dns: резолв %d IP (%s): найдено %d (DNS), %d (OS-резолвер), нет %d. %s",
        stats["attempted"], src,
        stats["resolved_by_dns"], stats["resolved_by_fallback"],
        stats["unresolved"], srv_note,
    )
    for s in dead:
        log.warning("dns: сервер %s НЕ ОТВЕТИЛ: timeouts=%d errors=%d",
                    s["server"], s["timeouts"], s["errors"])

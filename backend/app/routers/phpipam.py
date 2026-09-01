"""Массовый импорт из phpIPAM (REST API, v1.3–v1.8+).

Схема phpIPAM API (документация: doc/API/api_documentation.md):
  1. В phpIPAM создаётся API-приложение (Administration → Edit API settings → Apps)
     с правом "Read". Его имя входит в URL: /api/<app>/vlans/ ...
  2. Аутентификация: POST /api/<app>/user/ + Basic user:pass → токен
     (сессия, срок по настройкам приложения, обычно 6 часов).
  3. Запросы: заголовок phpipam-token: <токен>. Ответ: {"code","success","data"}.

Сценарий: «Проверить» → «Предпросмотр» (dry-run) → «Применить». Только admin.
"""
import asyncio
import base64
import json
import logging
import re
import ssl
import urllib.request
from ipaddress import ip_address, ip_network

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Ip, Subnet, Vlan, utcnow
from ..schemas import PhpIPamIn
from ..security import require_role
from ..service import audit

log = logging.getLogger("ipam.phpipam")
router = APIRouter(prefix="/api/import", tags=["import"])

TIMEOUT = 20
MAX_ISSUES = 50


# ---------------------------------------------------------------------------
# HTTP-клиент (urllib, синхронно — вызывается через to_thread)
# ---------------------------------------------------------------------------

def _ctx(insecure: bool):
    return ssl._create_unverified_context() if insecure else None


def _http_error(e: urllib.error.HTTPError) -> RuntimeError:
    body = ""
    try:
        body = re.sub(r"\s+", " ", e.read().decode(errors="ignore").strip())[:300]
    except Exception:
        pass
    return RuntimeError(f"HTTP {e.code} {e.reason}" + (f" — ответ сервера: {body}" if body else ""))


def _conn_error(e: Exception, app_name: str | None = None) -> str:
    msg = str(e)
    if "CERTIFICATE_VERIFY_FAILED" in msg or "self-signed" in msg:
        return (f"SSL: сертификат phpIPAM не доверен — включите в форме "
                "«самоподписанный сертификат (не проверять SSL)»")
    if "Invalid application id" in msg:
        tried = f" (искали: «{app_name}»)" if app_name else ""
        return (f"Приложение не найдено в phpIPAM{tried}. Проверьте имя ПОЛНОСТЬЮ "
                "(регистр важен) в phpIPAM: Administration → Edit API settings → Applications; "
                "вместо имени можно ввести числовой ID приложения из того же списка")
    return f"phpIPAM недоступен или ошибка API: {msg}"


def _auth(api_root: str, username: str, password: str, insecure: bool) -> str:
    """POST /api/<app>/user/ + Basic → сессионный токен."""
    basic = base64.b64encode(f"{username}:{password}".encode()).decode()
    req = urllib.request.Request(api_root + "user/", method="POST",
                                 headers={"Authorization": "Basic " + basic})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ctx(insecure)) as r:
            j = json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raise _http_error(e)
    if isinstance(j, dict):
        if j.get("success") is False:
            raise RuntimeError(f"аутентификация: {j.get('message') or 'ошибка'} (code {j.get('code')})")
        tok = (j.get("data") or {}).get("token")
        if tok:
            return tok
    raise RuntimeError("аутентификация: токен не получен (проверьте имя приложения, пользователя и пароль)")


def _get(url: str, token: str, insecure: bool = False):
    req = urllib.request.Request(url, headers={"phpipam-token": token, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ctx(insecure)) as r:
            j = json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raise _http_error(e)
    if isinstance(j, dict):
        if j.get("success") is False:
            raise RuntimeError(f"phpIPAM API: {j.get('message') or 'ошибка'} (code {j.get('code')})")
        if "data" in j:
            return j["data"]
    return j


def _get_try(urls: list, token: str, insecure: bool):
    """Попробовать несколько URL-вариантов (напр. vlans/ → vlan/)."""
    last = None
    for u in urls:
        try:
            return _get(u, token, insecure)
        except RuntimeError as e:
            last = e
    raise last


def _api_root(base: str, app: str) -> str:
    b = base.strip().rstrip("/")
    if not re.match(r"^https?://", b):
        b = "https://" + b
    if b.endswith("/api"):
        b = b[:-4].rstrip("/")
    return f"{b}/api/{app.strip().strip('/')}/"


def _php_cidr(ps: dict) -> str | None:
    """phpIPAM отдаёт либо 'subnet': 'a.b.c.d', 'mask': '24', либо готовый CIDR."""
    s = str(ps.get("subnet") or "").strip()
    if not s:
        return None
    if "/" in s:
        return s
    mask = ps.get("mask")
    if mask not in (None, "", "0"):
        return f"{s}/{mask}"
    return None


# ---------------------------------------------------------------------------
# проверка соединения
# ---------------------------------------------------------------------------

@router.post("/phpipam/check")
async def phpipam_check(data: PhpIPamIn, user=Depends(require_role("admin"))):
    root = _api_root(data.base_url, data.app)
    try:
        token = await asyncio.to_thread(_auth, root, data.username, data.password, data.insecure)
        vlans = await asyncio.to_thread(_get_try, [root + "vlans/", root + "vlan/"], token, data.insecure)
        subnets = await asyncio.to_thread(_get, root + "subnets/", token, data.insecure)
        if not isinstance(vlans, list) or not isinstance(subnets, list):
            raise ValueError("неожиданный ответ API (возможно, неверное имя приложения)")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, _conn_error(e, data.app))
    return {"ok": True, "base": root, "vlans": len(vlans), "subnets": len(subnets)}


# ---------------------------------------------------------------------------
# импорт (предпросмотр / применение)
# ---------------------------------------------------------------------------

async def _fetch_all(root: str, data: PhpIPamIn) -> tuple:
    token = await asyncio.to_thread(_auth, root, data.username, data.password, data.insecure)
    vlans, phpsubs = (await asyncio.gather(
        asyncio.to_thread(_get_try, [root + "vlans/", root + "vlan/"], token, data.insecure),
        asyncio.to_thread(_get, root + "subnets/", token, data.insecure),
    ))
    if not isinstance(vlans, list) or not isinstance(phpsubs, list):
        raise HTTPException(400, "Неожиданный ответ phpIPAM API")
    return vlans, phpsubs, token


async def _run_import(root: str, data: PhpIPamIn, do_apply: bool,
                      db: AsyncSession, user) -> dict:
    issues: list[str] = []

    def issue(msg: str):
        if len(issues) < MAX_ISSUES:
            issues.append(str(msg)[:200])

    try:
        vlans, phpsubs, token = await _fetch_all(root, data)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, _conn_error(e, data.app))

    our_vlans: dict[str, Vlan] = {v.name: v for v in (await db.execute(select(Vlan))).scalars().all()}
    our_vids: dict[int, Vlan] = {v.vid: v for v in our_vlans.values()}
    our_subnets: dict[str, Subnet] = {s.cidr: s for s in (await db.execute(select(Subnet))).scalars().all()}

    rep = {
        "vlans_new": 0, "vlans_existing": 0, "vlans_dup_skip": 0,
        "subnets_new": 0, "subnets_update": 0, "subnets_skip": 0,
        "subnets_overlap_skip": 0,
        "ips_new": 0, "ips_update": 0, "ips_skip": 0, "ips_unused_skip": 0,
    }

    # --- VLAN: phpIPAM id → наш id (по имени → по vid → создать).
    # Дубли внутри phpIPAM (один VID/имя встречается дважды в его списке)
    # считаются отдельно: первый — создаётся/принимается, повторные — пропуск.
    vlan_map: dict = {}
    claimed_names: set = set()
    claimed_vids: set = set()
    max_vid = max([v.vid for v in our_vids.values() if v is not None] + [1])
    for pv in vlans:
        try:
            name = str(pv.get("name") or "").strip() or (f"VLAN {pv.get('number')}" if pv.get("number") else "")
            # ВАЖНО: в phpIPAM номер VLAN (802.1Q) — в поле «number»;
            # «vlanId» — это внутренний ID записи phpIPAM, не номер VLAN!
            vid_raw = pv.get("number")
            if vid_raw in (None, ""):
                vid_raw = pv.get("vid")  # запасной вариант на другие версии
            vid = int(vid_raw) if vid_raw not in (None, "", "0") else None
        except (TypeError, ValueError):
            continue
        if not name:
            continue
        in_core = (name in our_vlans) or (vid and vid in our_vids)
        is_dup = (name in claimed_names) or (vid and vid in claimed_vids)
        if in_core and not is_dup:
            our_id = our_vlans[name].id if name in our_vlans else our_vids[vid].id
            rep["vlans_existing"] += 1
        elif is_dup:
            rep["vlans_dup_skip"] += 1
            if name in our_vlans:
                our_id = our_vlans[name].id
            elif vid and vid in our_vids and our_vids[vid] is not None:
                our_id = our_vids[vid].id
            else:
                our_id = -1
        else:
            rep["vlans_new"] += 1
            if vid is None:
                issue(f"VLAN «{name}»: без номера в phpIPAM — будет выдан свободный VID")
            claimed_names.add(name)
            if vid:
                claimed_vids.add(vid)
            if do_apply:
                vvid = vid
                if not vvid or vvid in our_vids:
                    vvid = min(max_vid + 1, 4094)
                    our_vids[vvid] = None  # зарезервировали
                    max_vid = vvid
                v = Vlan(vid=vvid or 1, name=name, descr=str(pv.get("description") or "") or None)
                db.add(v)
                our_vlans[name] = v
                our_vids[v.vid] = v
                await db.flush()
                our_id = v.id
            else:
                our_id = -1  # preview: будет создан
        vlan_map[pv.get("id")] = our_id

    # --- сети
    claimed_cidrs: list = []  # CIDR, уже заявленные в этом прогоне (точный предпросмотр пересечений)
    parsed: list[tuple, str] = []
    for ps in phpsubs:
        cidr_raw = _php_cidr(ps)
        if not cidr_raw:
            rep["subnets_skip"] += 1
            issue(f"не удалось разобрать сеть «{ps.get('subnet')}» — пропущена")
            continue
        try:
            net = ip_network(cidr_raw, strict=False)
        except ValueError:
            rep["subnets_skip"] += 1
            issue(f"некорректная сеть «{cidr_raw}» — пропущена")
            continue
        cidr = str(net)
        name = str(ps.get("name") or "").strip() or cidr
        # описание сети: phpIPAM «description» → «comment». Если в phpIPAM
        # пусто — для НОВОЙ сети ставим CIDR (чтобы сеть всегда была с
        # описанием); у существующей сети ручное описание не затираем.
        descr = (str(ps.get("description") or "").strip()
                 or str(ps.get("comment") or "").strip()
                 or None)
        vlan_id = vlan_map.get(ps.get("vlanId"))
        if vlan_id == -1:
            vlan_id = None  # VLAN будет создан при apply

        parsed.append((ps, cidr))
        if cidr in our_subnets:
            s = our_subnets[cidr]
            if name != s.name or (descr and descr != (s.descr or "")):
                rep["subnets_update"] += 1
                if do_apply:
                    s.name = name
                    if descr:
                        s.descr = descr
            else:
                rep["subnets_skip"] += 1
        else:
            # отношение к существующим/уже импортированным сетям:
            # ВЛОЖЕНИЕ (master-сеть/подсети, как в phpIPAM) — нормально, создаём;
            # дубль и ЧАСТИЧНОЕ пересечение — пропускаем
            clash = None
            for other in list(our_subnets) + claimed_cidrs:
                try:
                    o = ip_network(other)
                except ValueError:
                    continue
                if net == o:
                    clash = f"{cidr} — дубль (уже есть в ядре) — не создаётся"
                    break
                if net.subnet_of(o) or o.subnet_of(net):
                    continue  # вложение: master/подсеть
                if net.overlaps(o):
                    clash = f"{cidr} частично пересекается с {other} — не создаётся"
                    break
            if clash:
                rep["subnets_overlap_skip"] += 1
                issue(clash)
            else:
                rep["subnets_new"] += 1
                claimed_cidrs.append(cidr)
                if do_apply:
                    s = Subnet(cidr=cidr, name=name, vlan_id=vlan_id, descr=descr or cidr)
                    db.add(s)
                    await db.flush()
                    # IP: унаследованные от родительской сети переезжают сюда,
                    # отсутствующие — материализуем (иначе сеть «знает» только
                    # импортированные IP и показывает 100% занятости)
                    from ..service import resync_subnet_ips
                    await resync_subnet_ips(db, s.id, s.cidr)
                    our_subnets[cidr] = s

    # кэш IP перечитываем ПОСЛЕ создания сетей — с учётом свежематериализованных строк
    our_ips: dict[str, Ip] = {i.ip: i for i in (await db.execute(select(Ip))).scalars().all()}

    # --- IP-адреса (опционально; только по сетям, существующим в ядре после импорта)
    if data.import_ips:
        for ps, cidr in parsed:
            if cidr not in our_subnets:
                issue(f"{cidr} — новая сеть, её IP {'будут созданы при применении' if do_apply else 'в предпросмотре не считаются'}")
                continue
            try:
                ips = await asyncio.to_thread(_get_try,
                                              [f"{root}subnets/{ps.get('id')}/ipaddresses/",
                                               f"{root}subnets/{ps.get('id')}/addresses/"],
                                              token, data.insecure)
            except Exception as e:
                rep["ips_skip"] += 1
                issue(f"не удалось получить IP {cidr}: {e}")
                continue
            net = ip_network(cidr, strict=False)
            for pi in ips if isinstance(ips, list) else []:
                ip_str = str(pi.get("ip") or "").strip()
                try:
                    ip_obj = ip_address(ip_str)
                except ValueError:
                    rep["ips_skip"] += 1
                    issue(f"некорректный IP «{ip_str}» в {cidr}")
                    continue
                if ip_obj not in net:
                    continue
                # тег IP в phpIPAM (1=unused, 2=used, 3=reserved по стандарту;
                # надёжнее — tag_name). «unused» не трогаем — иначе все сети
                # «заняты» целиком, хотя заняты единицы
                tag_name = str(pi.get("tag_name") or "").strip().lower()
                tag_id = str(pi.get("tag") or "0").strip()
                if "reserved" in tag_name or tag_id == "3":
                    state = "reserved"
                elif "unused" in tag_name or tag_id == "1":
                    state = "free"
                else:
                    state = "used"
                if state == "free":
                    rep["ips_unused_skip"] += 1
                    continue
                host = str(pi.get("hostname") or pi.get("dns") or "").strip() or None
                mac = str(pi.get("mac") or "").strip().lower() or None
                owner = str(pi.get("owner") or pi.get("device") or "").strip() or None
                row = our_ips.get(ip_str)
                if row is None:
                    rep["ips_new"] += 1
                    if do_apply:
                        r = Ip(ip=ip_str, ip_int=int(ip_obj), subnet_id=our_subnets[cidr].id,
                               state=state, hostname=host, mac=mac, owner=owner,
                               first_seen=utcnow(), last_seen=utcnow())
                        db.add(r)
                        our_ips[ip_str] = r
                else:
                    changed = (row.state != state) or (host and row.hostname != host) \
                        or (mac and row.mac != mac) or (owner and row.owner != owner)
                    if changed:
                        rep["ips_update"] += 1
                        if do_apply:
                            row.state = state
                            if host:
                                row.hostname = host
                            if mac:
                                row.mac = mac
                            if owner:
                                row.owner = owner
                            row.last_seen = utcnow()
                    else:
                        rep["ips_skip"] += 1

    if do_apply:
        audit(db, user, "phpipam_import", None, rep)
        await db.commit()
        log.info("phpipam import: %s — %s", user.username, rep)
    return {**rep, "issues": issues}


@router.post("/phpipam/preview")
async def phpipam_preview(data: PhpIPamIn, db: AsyncSession = Depends(get_db),
                          user=Depends(require_role("admin"))):
    root = _api_root(data.base_url, data.app)
    return await _run_import(root, data, False, db, user)


@router.post("/phpipam/apply")
async def phpipam_apply(data: PhpIPamIn, db: AsyncSession = Depends(get_db),
                        user=Depends(require_role("admin"))):
    root = _api_root(data.base_url, data.app)
    return await _run_import(root, data, True, db, user)

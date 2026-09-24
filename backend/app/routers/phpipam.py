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
        "subnets_relink": 0,
        "subnets_overlap_skip": 0,
        "ips_new": 0, "ips_update": 0, "ips_skip": 0, "ips_unused_skip": 0,
    }

    # --- VLAN: из phpIPAM берём внутренний id записи (в таблице vlans это PK
    # «vlans.vlanId», в API — поле «id») и номер 802.1Q (поле «number»).
    # Сопоставление с ядром: СНАЧАЛА по номеру (у нас vid уникален — номер = L2-
    # идентичность), при отсутствии номера — по имени.
    # ВАЖНО: РАЗНЫЕ номера с одинаковыми именами — РАЗНЫЕ VLAN и НЕ сливаются
    # (раньше сливались молча по совпадению имени — «много сетей объединились
    # в один vlan»). Совпадающие номера в phpIPAM (напр. один номер в разных
    # L2-доменах) — вынужденное слияние (номер у нас уникален), но с явным
    # предупреждением в отчёте.
    vlan_map: dict[str, int] = {}                  # str(id из phpIPAM) -> наш id (−1 = будет создан)
    claim_by_vid: dict[int, tuple[int, str]] = {}  # vid -> (наш id, имя) в этом прогоне
    claim_by_name: dict[str, tuple[int, int]] = {}  # имя -> (наш id, vid) в этом прогоне
    # прозрачность отчёта: по каждой записи phpIPAM-VLAN — что решено и куда попали сети
    pv_by_id: dict[str, dict] = {}                 # str(id) -> запись phpIPAM (для подписей)
    vlan_ourlabel: dict[str, str] = {}             # str(id из phpIPAM) -> «Имя» №vid в ядре
    label_by_id: dict[int, str] = {}               # наш id -> подпись (для вложенных решений)
    id_by_number: dict[int, int] = {}              # номер 802.1Q -> наш id (fallback по номеру)
    vlans_report: list[dict] = []                  # [{phpipam, action, our}]
    vlan_links: list[dict] = []                    # [{cidr, phpipam_vlan, our_vlan}]
    core_label: dict[int, str] = {v.id: f"«{v.name}» №{v.vid}" for v in our_vlans.values()}
    max_vid = max([v.vid for v in our_vids.values() if v is not None] + [1])

    def _sig(pv: dict) -> str:
        """Подпись записи VLAN phpIPAM для отчёта: имя, номер, id, L2-домен."""
        nm = str(pv.get("name") or "?").strip() or "?"
        num = pv.get("number")
        if num in (None, ""):
            num = pv.get("vid")
        dom = str(pv.get("domainId") or "").strip()
        desc = str(pv.get("description") or "").strip()
        sig = f"«{nm}» №{num or '—'} id={pv.get('id') or '?'}"
        if dom and dom != "1":
            sig += f", домен {dom}"
        if desc:
            sig += f", описание: «{desc[:60]}»"
        return sig

    # одинаковый номер 802.1Q у НЕСКОЛЬКИХ записей phpIPAM (обычно один номер на
    # разных устройствах / L2-доменах): в ядре номер уникален -> записи сольются
    # в один VLAN; объясняем это в отчёте заранее, со списком записей и доменов
    by_number: dict[int, list[str]] = {}
    for pv in vlans:
        nraw = pv.get("number")
        if nraw in (None, ""):
            nraw = pv.get("vid")
        try:
            n = int(nraw)
        except (TypeError, ValueError):
            continue
        if str(pv.get("id") or "").strip():
            by_number.setdefault(n, []).append(_sig(pv))
    for n, sigs in sorted(by_number.items()):
        if len(sigs) > 1:
            issue(f"VLAN №{n}: в phpIPAM {len(sigs)} записи с этим номером: {'; '.join(sigs)} — "
                  f"в ядре они объединены в один VLAN №{n} (номер в ядре уникален). Если это "
                  f"разные L2-домены/оборудование — различите записи в источнике (номер или имя), "
                  f"иначе сети разных устройств останутся в одном VLAN")

    def _free_name(base: str, vid) -> str:
        """Имя занято VLAN с ДРУГИМ номером — даём уточнённое имя, а не сливаем."""
        cand = f"{base} ({vid})" if vid else f"{base} (copy)"
        k = 2
        while cand in our_vlans or cand in claim_by_name:
            cand = f"{base} ({vid}-{k})" if vid else f"{base} (copy-{k})"
            k += 1
        return cand

    for pv in vlans:
        pid = str(pv.get("id") or "").strip()
        if not pid:
            # без id запись не на что замкнуть: стала бы «ловушкой» для сетей без VLAN
            issue(f"VLAN «{pv.get('name') or '?'}» без id в ответе phpIPAM — пропущен")
            continue
        pv_by_id[pid] = pv
        try:
            name = str(pv.get("name") or "").strip() or (f"VLAN {pv.get('number')}" if pv.get("number") else "")
            # ВАЖНО: в phpIPAM номер VLAN (802.1Q) — в поле «number»;
            # «id» — внутренний ID записи (vlans.vlanId), не номер VLAN!
            vid_raw = pv.get("number")
            if vid_raw in (None, ""):
                vid_raw = pv.get("vid")  # запасной вариант на другие версии
            vid = int(vid_raw) if vid_raw not in (None, "", "0") else None
        except (TypeError, ValueError):
            continue
        if not name:
            issue(f"VLAN id={pid or '?'} без имени и номера — пропущен, его сети будут «без VLAN»")
            continue

        our_id = None
        rep_key = None
        do_create = False
        action = ""
        label = ""
        if vid and vid in our_vids and our_vids[vid] is not None:
            # (A) номер уже есть в ядре — тот же VLAN (номер у нас уникален)
            v = our_vids[vid]
            our_id, rep_key = v.id, "vlans_existing"
            label = f"«{v.name}» №{v.vid}"
            action = "использован существующий (по номеру)"
            if v.name != name:
                action = "объединён с существующим (один номер)"
                issue(f"VLAN №{vid}: в phpIPAM «{name}», в ядре «{v.name}» — использован существующий (один номер)")
            pv_descr = str(pv.get("description") or "").strip()
            core_descr = (v.descr or "").strip()
            if pv_descr and core_descr and pv_descr != core_descr:
                # номер и имя совпали, но описания разные: вероятно, один номер
                # на разных устройствах — в ядре слито по номеру, фиксируем явно
                issue(f"VLAN №{vid}: имя «{name}» и номер совпадают, но описание в phpIPAM "
                      f"«{pv_descr}» ≠ описания в ядре «{core_descr}» — возможно, это VLAN на "
                      f"другом оборудовании; в ядре объединено по номеру (номер уникален)")
        elif vid and vid in claim_by_vid:
            # (B) один номер у двух записей phpIPAM (напр. разные L2-домены) — вынужденное слияние
            prev_id, prev_name = claim_by_vid[vid]
            our_id, rep_key = prev_id, "vlans_dup_skip"
            label = label_by_id.get(prev_id) or (f"«{prev_name}» (создаётся)" if prev_id == -1 else f"id={prev_id}")
            action = f"объединён с «{prev_name}» (один номер)"
            issue(f"VLAN «{name}» №{vid} и «{prev_name}» №{vid}: один номер — объединены в один "
                  f"(в phpIPAM это разные L2-домены?)")
        else:
            core = our_vlans.get(name)     # такой VLAN уже есть в ядре
            owner = claim_by_name.get(name)  # такой имя уже заявлено в этом прогоне
            busy_vid = core.vid if core is not None else (owner[1] if owner else None)
            if core is None and owner is None:
                do_create = True
            elif vid is None or busy_vid == vid:
                # (C/D) полный дубль: имя совпадает, номер совпадает либо отсутствует
                if core is not None:
                    our_id, rep_key = core.id, "vlans_existing"
                    label = f"«{core.name}» №{core.vid}"
                    action = "уже есть в ядре (по имени)"
                else:
                    our_id, rep_key = owner[0], "vlans_dup_skip"
                    label = label_by_id.get(owner[0]) or (f"«{name}» (создаётся)" if owner[0] == -1 else f"id={owner[0]}")
                    action = "дубль из этого импорта (по имени)"
                if vid is None and owner is not None:
                    issue(f"VLAN «{name}» без номера: объединён с одноимённым из этого импорта")
            else:
                # (E) имя занято VLAN с ДРУГИМ номером — это РАЗНЫЕ VLAN, не сливаем!
                orig, name = name, _free_name(name, vid)
                action = f"создан «{name}» (имя «{orig}» занято VLAN №{busy_vid})"
                issue(f"VLAN «{orig}» №{vid}: имя занято VLAN №{busy_vid} — создан «{name}» "
                      f"(разные номера = разные VLAN, слияние отменено)")
                do_create = True

        if do_create:
            rep["vlans_new"] += 1
            action = action or "создан"
            if vid is None:
                action += " (VID выдан)"
                issue(f"VLAN «{name}»: без номера в phpIPAM — будет выдан свободный VID")
            if do_apply:
                vvid = vid
                if not vvid or vvid in our_vids or vvid in claim_by_vid:
                    vvid = max_vid + 1
                    while vvid <= 4094 and (vvid in our_vids or vvid in claim_by_vid):
                        vvid += 1
                    if vvid > 4094:
                        vvid = next((x for x in range(1, 4095)
                                     if x not in our_vids and x not in claim_by_vid), None)
                    if vvid is None:
                        issue(f"VLAN «{name}»: свободных VID нет (1–4094) — пропущен")
                        continue
                    max_vid = max(max_vid, vvid)
                v = Vlan(vid=vvid, name=name, descr=str(pv.get("description") or "") or None)
                db.add(v)
                our_vlans[name] = v
                our_vids[vvid] = v
                await db.flush()
                our_id = v.id
                label = f"«{v.name}» №{v.vid}"
            else:
                our_id = -1  # preview: будет создан
                label = f"«{name}» (создаётся)"
        else:
            rep[rep_key] += 1

        # регистрируем претензии прогона — дубли дальше по списку находят этот же VLAN
        if vid and vid not in claim_by_vid:
            claim_by_vid[vid] = (our_id, name)
        if name not in claim_by_name:
            claim_by_name[name] = (our_id, vid)
        vlan_map[pid] = our_id
        # подписи для отчёта «прозрачность»
        label = label or label_by_id.get(our_id, f"id={our_id}")
        label_by_id.setdefault(our_id, label)
        if vid and vid not in id_by_number:
            id_by_number[vid] = our_id
        vlan_ourlabel[pid] = label
        phpipam_label = _sig(pv)
        vlans_report.append({"phpipam": phpipam_label, "action": action or "—", "our": label})

    # --- сети
    claimed_cidrs: list = []  # CIDR, уже заявленные в этом прогоне (точный предпросмотр пересечений)
    unmapped_vlans: set = set()  # vlanId без записи в списке VLAN — уже предупредили
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
        raw_v = ps.get("vlanId")
        raw_s = str(raw_v or "").strip()
        phpipam_vlan_label = ""
        if raw_s in ("", "0", "None"):
            vlan_id = None  # в phpIPAM привязки нет — «без VLAN», мимо карты (не ключ карты!)
        else:
            vlan_id = vlan_map.get(raw_s)
            if vlan_id == -1:
                vlan_id = None  # VLAN будет создан при apply
            elif vlan_id is None and raw_s.isdigit() and int(raw_s) in id_by_number:
                # запасной путь: в vlanId лежит НОМЕР 802.1Q, а не id записи
                vlan_id = id_by_number[int(raw_s)]
                phpipam_vlan_label = f"№{raw_s} (сопоставлено по номеру, не по id записи) "
                if raw_s not in unmapped_vlans:
                    unmapped_vlans.add(raw_s)
                    issue(f"сеть {cidr}: vlanId={raw_s} — не id записи VLAN, но номер {raw_s} — "
                          f"VLAN сопоставлен по номеру (в phpIPAM смешаны id и номера?)")
            elif vlan_id is None:
                # vlanId ссылается на запись, которой нет в списке VLAN (права API?)
                if raw_s not in unmapped_vlans:
                    unmapped_vlans.add(raw_s)
                    issue(f"vlanId={raw_v} из phpIPAM не найден в списке VLAN — сеть будет «без VLAN»")
        pv = pv_by_id.get(raw_s)
        if pv is not None:
            phpipam_vlan_label += _sig(pv)
        elif raw_s not in ("", "0", "None") and not phpipam_vlan_label:
            phpipam_vlan_label = f"id={raw_s} (запись не найдена)"
        mapped_label = (vlan_ourlabel.get(raw_s) or label_by_id.get(vlan_id, "")) if vlan_id else ""
        exists_s = our_subnets.get(cidr)
        if exists_s is not None:
            old_l = core_label.get(exists_s.vlan_id, "") if exists_s.vlan_id else ""
            if data.relink_vlans and (exists_s.vlan_id or None) != (vlan_id or None):
                # перепривязка: в колонке «в ядре» показываем переход старое -> новое
                our_label = f"{old_l or 'без VLAN'} → {mapped_label or 'без VLAN'}"
            else:
                our_label = old_l  # факт из БД: без перепривязки vlan_id существующей сети не трогаем
        else:
            our_label = mapped_label
        vlan_links.append({
            "cidr": cidr,
            "phpipam_vlan": phpipam_vlan_label,
            "our_vlan": our_label,
        })

        parsed.append((ps, cidr))
        if exists_s is not None:
            s = exists_s
            changed = name != s.name or (descr and descr != (s.descr or ""))
            relink = data.relink_vlans and (s.vlan_id or None) != (vlan_id or None)
            if relink:
                rep["subnets_relink"] += 1
            if changed:
                rep["subnets_update"] += 1
            if (changed or relink) and do_apply:
                s.name = name
                if descr:
                    s.descr = descr
                if relink:
                    s.vlan_id = vlan_id
            if not changed and not relink:
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
                    # IP: унаследованные от родительской сети переезжают сюда,
                    # отсутствующие — материализуем (иначе сеть «знает» только
                    # импортированные IP и показывает 100% занятости);
                    # крупные сети шире /20 — разреженные (без полной таблицы IP)
                    from ..service import is_sparse, resync_subnet_ips, subnet_write_lock
                    async with subnet_write_lock(db):
                        s = Subnet(cidr=cidr, name=name, vlan_id=vlan_id,
                                   descr=descr or cidr, sparse=is_sparse(cidr))
                        db.add(s)
                        await db.flush()
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
    return {**rep, "issues": issues, "vlans_report": vlans_report, "vlan_links": vlan_links}


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

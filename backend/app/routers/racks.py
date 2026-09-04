"""Rack Topology API: прокси к стороннему сервису (read-only).

Страница «Стойки» (frontend) не ходит напрямую во внешний ресурс:
* браузер не упирается в CORS (сервер->сервер CORS не нужен);
* внутренний адрес источника (admsrv…) не раскрывается клиентам;
* ответ кэшируется на короткое время, чтобы не дёргать источник каждым F5.

GET /api/racks/status  ->  {
    "configured": bool,
    "cached": bool,          // отдан из кэша
    "cachedAt": iso | null,
    "error": str | null,     // ошибка обращения к источнику
    "ttl_s": int,
    "data": {…} | null       // payload внешнего Rack Topology API
}

Адрес источника задаётся в Настройках → «Стойки (Rack Topology API)»
или через env RACK_TOPOLOGY_URL. Пусто = выключено (configured=false).
"""
import asyncio
import json
import logging
import time
import urllib.request
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_db
from ..security import get_current_user
from ..settings_store import get_all

log = logging.getLogger("ipam.racks")
router = APIRouter(prefix="/api/racks", tags=["racks"])

TTL_S = 30          # сколько держим кэш ответа источника
_cache: dict = {"at": 0.0, "payload": None, "error": None}


def _fetch(url: str, timeout: int) -> dict:
    """Блокирующий запрос к источнику — вызывать в потоке."""
    req = urllib.request.Request(url, headers={"User-Agent": "ipam-rack-topology-proxy/1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("источник вернул не-JSON объект")
    return data


async def _get_source(db: AsyncSession) -> str:
    rt = await get_all(db)
    return (rt.get("rack_topology_url") or "").strip()


@router.get("/status")
async def rack_topology_status(db: AsyncSession = Depends(get_db),
                               user=Depends(get_current_user)):
    url = await _get_source(db)
    if not url:
        return {"configured": False, "cached": False, "cachedAt": None,
                "error": None, "ttl_s": TTL_S, "data": None}

    now = time.time()
    cached = bool(_cache["payload"]) and (now - _cache["at"]) < TTL_S
    payload = _cache["payload"] if cached else None
    error = _cache["error"] if cached else None

    if not cached:
        try:
            payload = await asyncio.to_thread(_fetch, url, settings.RACK_TOPOLOGY_TIMEOUT)
            error = None
            _cache.update({"at": now, "payload": payload, "error": None})
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            log.warning("rack topology: не удалось получить %s: %s", url, error)
            # на время недоступности отдаём последний успешный кэш (если есть)
            payload = _cache["payload"] if (now - _cache["at"]) < TTL_S * 20 else None
            cached = payload is not None

    return {
        "configured": True,
        "cached": cached,
        "cachedAt": datetime.now(timezone.utc).isoformat() if cached else None,
        "error": error,
        "ttl_s": TTL_S,
        "data": payload,
    }

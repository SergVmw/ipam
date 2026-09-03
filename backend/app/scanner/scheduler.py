"""Планировщик: раз в 30 с находим "поздние" сети и запускаем их сканы."""
import logging
from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import or_, select

from ..db import SessionLocal
from ..models import Subnet, utcnow
from .engine import busy_ids, scan_subnet_now, spawn

log = logging.getLogger("ipam.scheduler")
scheduler = AsyncIOScheduler()


async def due_scan_job() -> None:
    try:
        async with SessionLocal() as db:
            now = utcnow()
            due = (await db.execute(
                select(Subnet).where(
                    Subnet.scan_enabled.is_(True),
                    or_(Subnet.next_scan_at.is_(None), Subnet.next_scan_at <= now),
                )
            )).scalars().all()
            for s in due:
                if s.id in busy_ids:
                    continue
                s.next_scan_at = now + timedelta(seconds=s.scan_interval_s or 3600)
            await db.commit()
            for s in due:
                if s.id not in busy_ids:
                    spawn(scan_subnet_now(s.id, trigger="schedule"))
    except Exception:
        log.exception("due_scan_job failed")


def start_scheduler() -> None:
    scheduler.add_job(due_scan_job, "interval", seconds=30, id="due-scans")
    scheduler.start()
    log.info("планировщик запущен (проверка из-под-скана каждые 30 с)")


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)

import logging
import shutil
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import init_db

# Метка сборки: должна печататься в логах при старте (проверка, что образ свежий)
BUILD = "2026-08-24 docs"

from .routers import admin, agents, auth_router, docs, events, ips, links, locations, overview, phpipam, settings_router, subnets, system, usage, users, vlans
from .scanner.scheduler import start_scheduler, stop_scheduler
from .seed import seed_admin, seed_demo

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    log = logging.getLogger("ipam")
    log.info("IPAM-lite build %s", BUILD)
    fping = shutil.which("fping")
    if fping:
        try:
            p = subprocess.run([fping, "-v"], capture_output=True, text=True, timeout=5)
            ver = (p.stderr or p.stdout).strip().splitlines()
            log.info("fping найден: %s", ver[-1] if ver else fping)
        except Exception:
            log.info("fping найден: %s", fping)
    else:
        log.warning("fping не найден — скан идёт TCP-пробой (PROBE_PORTS), только сети до /22")
    await init_db()
    await seed_admin()
    if settings.SEED_DEMO:
        await seed_demo()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="IPAM-lite", version="0.1.0", lifespan=lifespan)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

app.include_router(auth_router.router)
app.include_router(vlans.router)
app.include_router(locations.router)
app.include_router(links.router)
app.include_router(subnets.router)
app.include_router(ips.router)
app.include_router(usage.router)
app.include_router(overview.router)
app.include_router(events.router)
app.include_router(settings_router.router)
app.include_router(users.router)
app.include_router(agents.router)
app.include_router(docs.router)
app.include_router(system.router)
app.include_router(phpipam.router)
app.include_router(admin.router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logging.getLogger("ipam").exception("необработанная ошибка %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": f"Внутренняя ошибка: {exc}"})


@app.get("/api/health")
async def health():
    return {"status": "ok"}


# Frontend (vite build) — раздаётся из того же контейнера.
# Ищем static/ выше от каталога приложения (docker: /app/static, dev: ipam/static)
def _find_static() -> Path | None:
    here = Path(__file__).resolve().parent  # .../app
    for parent in (here.parent, here.parent.parent):
        d = parent / "static"
        if d.is_dir():
            return d
    return None


_static = _find_static()
if _static is not None:
    app.mount("/", StaticFiles(directory=str(_static), html=True), name="static")

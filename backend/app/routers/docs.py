"""Документация: разделы → документы (markdown) + файлы.

- Чтение: любой вошедший пользователь.
- Написание (создание/редактирование/удаление/загрузка): admin + operator.
- Файлы хранятся в DOCS_DIR под случайным именем; выдача — по id,
  авторизация заголовком Bearer или ?token= (для прямых ссылок и markdown-ссылок).
"""
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import delete as sa_delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_db
from ..models import DocFile, DocPage, DocSection
from ..schemas import DocFileContent, DocFileOut, DocPageFull, DocPageIn, DocPageUpdate, DocSectionIn, DocSectionUpdate
from ..security import get_current_user, require_role, valid_token
from ..service import audit

log = logging.getLogger("ipam.docs")

router = APIRouter(prefix="/api", tags=["docs"])

# ---------------------------------------------------------------------------
# хранилище файлов
# ---------------------------------------------------------------------------

def _docs_dir() -> Path:
    if settings.DOCS_DIR:
        d = Path(settings.DOCS_DIR)
    else:
        # корень проекта = каталог, где лежит static/ или ipam.db
        # (dev: ipam/, docker: /app) — как в _find_static() в main.py
        here = Path(__file__).resolve().parent  # .../app/routers
        root = None
        for p in (here.parent, here.parent.parent, here.parent.parent.parent):
            if (p / "static").is_dir() or (p / "ipam.db").is_file():
                root = p
                break
        d = (root or Path.cwd()) / "docs_files"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _user_from_request(request: Request):
    """Авторизация для прямой ссылки: Bearer-заголовок либо ?token= (для <a>)."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
    else:
        token = request.query_params.get("token", "")
    if not valid_token(token):
        raise HTTPException(401, "Не авторизован")
    # для выдачи файла достаточно валидности токена (24 ч TTL)
    return True


def _file_url(fid: int, token: str | None = None) -> str:
    base = f"/api/docs/files/{fid}"
    return f"{base}?token={token}" if token else base


# текстовые форматы — правятся в редакторе в UI; бинарные — только скачать/заменить
EDITABLE_EXT = {
    ".md", ".markdown", ".txt", ".text", ".log", ".csv", ".tsv", ".json",
    ".yaml", ".yml", ".xml", ".html", ".htm", ".css", ".js", ".mjs", ".ts",
    ".tsx", ".jsx", ".py", ".sh", ".ini", ".cfg", ".conf", ".toml", ".sql",
    ".env", ".properties",
}


def _is_editable(name: str, mime: str | None) -> bool:
    ext = Path(name).suffix.lower()
    return ext in EDITABLE_EXT or bool(mime and mime.startswith("text/"))


def _file_dict(f: DocFile) -> dict:
    return {
        "id": f.id, "name": f.name, "size": f.size, "mime": f.mime,
        "url": _file_url(f.id), "editable": _is_editable(f.name, f.mime),
        "uploaded_at": f.uploaded_at.isoformat() if f.uploaded_at else None,
    }


# ---------------------------------------------------------------------------
# дерево: разделы → страницы (+файлы)
# ---------------------------------------------------------------------------

@router.get("/docs")
async def docs_tree(db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    sections = (await db.execute(select(DocSection).order_by(DocSection.position, DocSection.id))).scalars().all()
    pages = (await db.execute(select(DocPage).order_by(DocPage.id))).scalars().all()
    files = (await db.execute(select(DocFile).order_by(DocFile.id))).scalars().all()

    files_by_page: dict[int, list[dict]] = {}
    files_by_section: dict[int, list[dict]] = {}
    for f in files:
        d = _file_dict(f)
        if f.page_id is not None:
            files_by_page.setdefault(f.page_id, []).append(d)
        elif f.section_id is not None:
            files_by_section.setdefault(f.section_id, []).append(d)

    out = []
    for s in sections:
        sp = [p for p in pages if p.section_id == s.id]
        out.append({
            "id": s.id, "title": s.title, "position": s.position,
            "files": files_by_section.get(s.id, []),
            "pages": [{
                "id": p.id, "title": p.title,
                "updated_at": p.updated_at.isoformat() if p.updated_at else None,
                "updated_by": p.updated_by,
                "files": files_by_page.get(p.id, []),
            } for p in sp],
        })
    return out


# ---------------------------------------------------------------------------
# разделы
# ---------------------------------------------------------------------------

@router.post("/docs/sections", status_code=201)
async def create_section(data: DocSectionIn, db: AsyncSession = Depends(get_db), user=Depends(require_role("admin", "operator"))):
    exists = (await db.execute(select(DocSection).where(DocSection.title == data.title.strip()))).scalar_one_or_none()
    if exists:
        raise HTTPException(409, "Раздел с таким названием уже есть")
    pos = (await db.execute(select(func.max(DocSection.position)))).scalar() or 0
    s = DocSection(title=data.title.strip(), position=pos + 1, updated_at=datetime.now(timezone.utc).replace(tzinfo=None))
    db.add(s)
    audit(db, user, "doc_section_create", s.title)
    await db.commit()
    await db.refresh(s)
    return {"id": s.id, "title": s.title, "position": s.position}


@router.put("/docs/sections/{sid}")
async def update_section(sid: int, data: DocSectionUpdate, db: AsyncSession = Depends(get_db), user=Depends(require_role("admin", "operator"))):
    s = await db.get(DocSection, sid)
    if not s:
        raise HTTPException(404, "Раздел не найден")
    if data.title is not None and data.title.strip() != s.title:
        clash = (await db.execute(select(DocSection).where(DocSection.title == data.title.strip(), DocSection.id != sid))).scalar_one_or_none()
        if clash:
            raise HTTPException(409, "Раздел с таким названием уже есть")
        s.title = data.title.strip()
    if data.position is not None:
        s.position = data.position
    s.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    audit(db, user, "doc_section_update", s.title)
    await db.commit()
    return {"id": s.id, "title": s.title, "position": s.position}


@router.delete("/docs/sections/{sid}")
async def delete_section(sid: int, db: AsyncSession = Depends(get_db), user=Depends(require_role("admin", "operator"))):
    s = await db.get(DocSection, sid)
    if not s:
        raise HTTPException(404, "Раздел не найден")
    title = s.title
    # файлы на диске: у страниц раздела + сами файлы раздела
    disk_files: list[DocFile] = list(
        (await db.execute(select(DocFile).where(DocFile.section_id == sid))).scalars().all()
    )
    for p in (await db.execute(select(DocPage).where(DocPage.section_id == sid))).scalars().all():
        disk_files.extend(
            (await db.execute(select(DocFile).where(DocFile.page_id == p.id))).scalars().all()
        )
    for f in disk_files:
        try:
            (_docs_dir() / f.stored).unlink(missing_ok=True)
        except Exception:
            pass
    # файлы раздела: ORM-каскада по section_id нет (sqlite не форсит DB-cascade) — удаляем явно
    await db.execute(sa_delete(DocFile).where(DocFile.section_id == sid))
    audit(db, user, "doc_section_delete", title)
    await db.delete(s)
    await db.commit()
    return {"deleted": True}


# ---------------------------------------------------------------------------
# страницы
# ---------------------------------------------------------------------------

@router.post("/docs/pages", status_code=201)
async def create_page(data: DocPageIn, db: AsyncSession = Depends(get_db), user=Depends(require_role("admin", "operator"))):
    s = await db.get(DocSection, data.section_id)
    if not s:
        raise HTTPException(404, "Раздел не найден")
    p = DocPage(section_id=s.id, title=data.title.strip(), body=data.body or "", updated_by=user.username)
    db.add(p)
    audit(db, user, "doc_page_create", p.title, {"section": s.title})
    await db.commit()
    await db.refresh(p)
    return {"id": p.id}


@router.get("/docs/pages/{pid}")
async def get_page(pid: int, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    p = await db.get(DocPage, pid)
    if not p:
        raise HTTPException(404, "Документ не найден")
    s = await db.get(DocSection, p.section_id)
    files = (await db.execute(select(DocFile).where(DocFile.page_id == pid).order_by(DocFile.id))).scalars().all()
    return DocPageFull(
        id=p.id, title=p.title, body=p.body, section_id=s.id, section_title=s.title,
        created_at=p.created_at.isoformat() if p.created_at else None,
        updated_at=p.updated_at.isoformat() if p.updated_at else None,
        updated_by=p.updated_by,
        files=[{"id": f.id, "name": f.name, "size": f.size, "mime": f.mime,
                "url": _file_url(f.id), "uploaded_at": f.uploaded_at.isoformat() if f.uploaded_at else None} for f in files],
    )


@router.put("/docs/pages/{pid}")
async def update_page(pid: int, data: DocPageUpdate, db: AsyncSession = Depends(get_db), user=Depends(require_role("admin", "operator"))):
    p = await db.get(DocPage, pid)
    if not p:
        raise HTTPException(404, "Документ не найден")
    if data.title is not None:
        p.title = data.title.strip() or p.title
    if data.body is not None:
        p.body = data.body
    p.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    p.updated_by = user.username
    audit(db, user, "doc_page_update", p.title)
    await db.commit()
    return {"id": p.id, "updated_at": p.updated_at.isoformat()}


@router.delete("/docs/pages/{pid}")
async def delete_page(pid: int, db: AsyncSession = Depends(get_db), user=Depends(require_role("admin", "operator"))):
    p = await db.get(DocPage, pid)
    if not p:
        raise HTTPException(404, "Документ не найден")
    title = p.title
    for f in (await db.execute(select(DocFile).where(DocFile.page_id == pid))).scalars().all():
        try:
            (_docs_dir() / f.stored).unlink(missing_ok=True)
        except Exception:
            pass
    audit(db, user, "doc_page_delete", title)
    await db.delete(p)
    await db.commit()
    return {"deleted": True}


# ---------------------------------------------------------------------------
# файлы
# ---------------------------------------------------------------------------

def _safe_name(name: str) -> str:
    name = Path(name).name  # без путей
    name = re.sub(r"[^\w.\- \u0400-\u04FF()\[\]#@&;=!%+]+", "_", name, flags=re.U).strip()
    return (name or "file")[:180]


@router.post("/docs/pages/{pid}/files", status_code=201)
async def upload_file(pid: int, request: Request,
                      file: UploadFile = File(...),
                      db: AsyncSession = Depends(get_db), user=Depends(require_role("admin", "operator"))):
    p = await db.get(DocPage, pid)
    if not p:
        raise HTTPException(404, "Документ не найден")

    limit = settings.DOCS_MAX_UPLOAD_MB * 1024 * 1024
    chunks: list[bytes] = []
    size = 0
    while chunk := await file.read(1024 * 1024):
        size += len(chunk)
        if size > limit:
            raise HTTPException(413, f"Файл больше лимита {settings.DOCS_MAX_UPLOAD_MB} МБ")
        chunks.append(chunk)
    data = b"".join(chunks)

    stored = uuid.uuid4().hex + "_" + _safe_name(file.filename or "file")
    (_docs_dir() / stored).write_bytes(data)

    f = DocFile(page_id=pid, section_id=None, name=_safe_name(file.filename or "file"), stored=stored,
                size=size, mime=file.content_type)
    db.add(f)
    audit(db, user, "doc_file_upload", f.name, {"page": p.title, "size": size})
    await db.commit()
    await db.refresh(f)
    log.info("docs upload: %s (%d байт) в «%s» — %s", f.name, size, p.title, user.username)
    return _file_dict(f)


@router.post("/docs/sections/{sid}/files", status_code=201)
async def upload_section_file(sid: int,
                              file: UploadFile = File(...),
                              db: AsyncSession = Depends(get_db), user=Depends(require_role("admin", "operator"))):
    s = await db.get(DocSection, sid)
    if not s:
        raise HTTPException(404, "Раздел не найден")

    limit = settings.DOCS_MAX_UPLOAD_MB * 1024 * 1024
    chunks: list[bytes] = []
    size = 0
    while chunk := await file.read(1024 * 1024):
        size += len(chunk)
        if size > limit:
            raise HTTPException(413, f"Файл больше лимита {settings.DOCS_MAX_UPLOAD_MB} МБ")
        chunks.append(chunk)
    data = b"".join(chunks)

    stored = uuid.uuid4().hex + "_" + _safe_name(file.filename or "file")
    (_docs_dir() / stored).write_bytes(data)

    f = DocFile(page_id=None, section_id=sid, name=_safe_name(file.filename or "file"), stored=stored,
                size=size, mime=file.content_type)
    db.add(f)
    audit(db, user, "doc_file_upload", f.name, {"section": s.title, "size": size})
    await db.commit()
    await db.refresh(f)
    log.info("docs upload: %s (%d байт) в раздел «%s» — %s", f.name, size, s.title, user.username)
    return _file_dict(f)


@router.get("/docs/files/{fid}")
async def get_file(fid: int, request: Request, db: AsyncSession = Depends(get_db)):
    _user_from_request(request)
    f = await db.get(DocFile, fid)
    if not f:
        raise HTTPException(404, "Файл не найден")
    path = _docs_dir() / f.stored
    if not path.is_file():
        raise HTTPException(410, "Файл отсутствует на диске")
    inline = (f.mime or "").startswith(("image/", "text/", "application/pdf"))
    return FileResponse(
        str(path),
        media_type=f.mime or "application/octet-stream",
        filename=f.name,
        content_disposition_type="inline" if inline else "attachment",
    )


@router.put("/docs/files/{fid}")
async def edit_file(fid: int, data: DocFileContent,
                    db: AsyncSession = Depends(get_db), user=Depends(require_role("admin", "operator"))):
    """Перезаписать содержимое текстового файла из редактора (UTF-8)."""
    f = await db.get(DocFile, fid)
    if not f:
        raise HTTPException(404, "Файл не найден")
    if not _is_editable(f.name, f.mime):
        raise HTTPException(415, "Правятся только текстовые файлы (md, txt, csv, json, код…); бинарный — скачайте и загрузите заново")
    path = _docs_dir() / f.stored
    if not path.is_file():
        raise HTTPException(410, "Файл отсутствует на диске")
    encoded = data.content.encode("utf-8")
    limit = settings.DOCS_MAX_UPLOAD_MB * 1024 * 1024
    if len(encoded) > limit:
        raise HTTPException(413, f"Больше лимита {settings.DOCS_MAX_UPLOAD_MB} МБ")
    path.write_bytes(encoded)
    f.size = len(encoded)
    audit(db, user, "doc_file_edit", f.name, {"size": len(encoded)})
    await db.commit()
    return {"id": f.id, "size": f.size}


@router.delete("/docs/files/{fid}")
async def delete_file(fid: int, db: AsyncSession = Depends(get_db), user=Depends(require_role("admin", "operator"))):
    f = await db.get(DocFile, fid)
    if not f:
        raise HTTPException(404, "Файл не найден")
    try:
        (_docs_dir() / f.stored).unlink(missing_ok=True)
    except Exception:
        pass
    audit(db, user, "doc_file_delete", f.name)
    await db.delete(f)
    await db.commit()
    return {"deleted": True}

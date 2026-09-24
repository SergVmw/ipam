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


async def check_docs_integrity() -> tuple[int, list[str]]:
    """Целостность вложений: (всего строк DocFile, [stored без файла на диске]).

    Вызывается при старте (lifespan): если том с вложениями не примонтирован
    или пересоздан, недостающие файлы видны в логе сразу, а не «потом».
    """
    from ..db import SessionLocal
    d = _docs_dir()
    async with SessionLocal() as db:
        rows = (await db.execute(select(DocFile))).scalars().all()
    missing = [f.stored for f in rows if not (d / f.stored).is_file()]
    return len(rows), missing


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
# проверка содержимого загружаемых файлов (magic bytes)
# ---------------------------------------------------------------------------
# Политика: заявленный Content-Type И реальное содержимое должны быть в
# allowlist и совпадать по классу; исполняемые файлы (ELF/PE/Mach-O/…) запрещены
# всегда — даже с поддельным Content-Type. Раньше проверялось (и сохранялось)
# только значение Content-Type из заголовка — его легко подделать.

ALLOWED_MIME_PREFIXES = ("text/",)  # текстовые/исходники — по содержимому
ALLOWED_MIMES = {
    # документы и разметка
    "application/pdf", "application/rtf", "application/x-rtf",
    "application/json", "application/xml", "application/yaml", "application/x-yaml",
    # архивы и офисные контейнеры (zip/gzip-сигнатура)
    "application/zip", "application/gzip", "application/x-gzip", "application/x-tar",
    "application/x-7z-compressed", "application/x-rar-compressed", "application/vnd.rar",
    "application/msword", "application/vnd.ms-excel", "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.oasis.opendocument.text", "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.oasis.opendocument.presentation", "application/vnd.oasis.opendocument.graphics",
    # скрипты как исходный текст (правятся в редакторе UI)
    "application/x-shellscript", "application/x-sh", "application/x-bat",
    # изображения
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp",
    "image/tiff", "image/svg+xml", "image/x-icon",
}

# сигнатуры бинарных форматов — контроль после определения типа (шаг 4 проверки)
MAGIC_SIGNATURES = {
    "application/pdf": (b"%PDF",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
    "image/webp": (b"RIFF",),  # RIFF....WEBP — дополнительно сверяем байты 8..12
    "application/zip": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
    "application/gzip": (b"\x1f\x8b",),
    "application/x-gzip": (b"\x1f\x8b",),
}

# исполняемый код — запрещён всегда (сырая сигнатура в начале файла)
FORBIDDEN_SIGNATURES = (
    (b"\x7fELF", "ELF"),
    (b"MZ", "PE/DOS"),
    (b"\xfe\xed\xfa\xce", "Mach-O"), (b"\xfe\xed\xfa\xcf", "Mach-O"),
    (b"\xce\xfa\xed\xfe", "Mach-O"), (b"\xcf\xfa\xed\xfe", "Mach-O"),
    (b"\xca\xfe\xba\xbe", "Mach-O fat/Java class"),
)

# MIME «текстового» класса вне префикса text/
_TEXTISH = {
    "application/json", "application/xml", "application/yaml", "application/x-yaml",
    "application/rtf", "application/x-rtf",
    "application/x-shellscript", "application/x-sh", "application/x-bat",
}
# zip-контейнеры и офисные форматы (ODT/DOCX — это zip по сигнатуре)
_CONTAINER = {
    "application/zip", "application/gzip", "application/x-gzip", "application/x-tar",
    "application/x-7z-compressed", "application/x-rar-compressed", "application/vnd.rar",
    "application/msword",
}


def _mime_allowed(mime: str) -> bool:
    return mime in ALLOWED_MIMES or mime.startswith(ALLOWED_MIME_PREFIXES)


def _mime_class(mime: str) -> str:
    if mime == "image/svg+xml":
        return "svg"
    if mime.startswith("image/"):
        return "image"
    if mime == "application/pdf":
        return "pdf"
    if mime.startswith("text/") or mime in _TEXTISH:
        return "text"
    if (mime in _CONTAINER or mime.startswith("application/vnd.openxmlformats")
            or mime.startswith("application/vnd.oasis") or mime.startswith("application/vnd.ms")):
        return "container"
    return "other"


def _text_like(content: bytes) -> bool:
    """Текстовый файл? (нет NUL; UTF-8 либо почти без управляющих символов — cp1251 и т.п.)."""
    head = content[:8192]
    if not head or b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
        return True
    except UnicodeDecodeError:
        pass
    printable = sum(1 for b in head if 0x20 <= b <= 0xFF or b in (9, 10, 13))
    return printable / len(head) >= 0.9


def _detect_mime(content: bytes, text_like: bool) -> str:
    """Реальный тип содержимого: python-magic (libmagic), иначе встроенный sniff."""
    try:
        import magic  # python-magic

        detected = (magic.from_buffer(content, mime=True) or "").strip().lower()
        if detected and detected != "application/octet-stream":
            return detected
    except Exception:
        pass  # libmagic/python-magic нет — сигнатуры + текстовый эвристический анализ
    # libmagic не распознал (или недоступен) — пробуем по сигнатурам
    for mime, sigs in MAGIC_SIGNATURES.items():
        if any(content.startswith(sig) for sig in sigs):
            return mime
    return "text/plain" if text_like else "application/octet-stream"


def _validate_upload(declared: str | None, content: bytes) -> str:
    """Проверка загружаемого файла: allowlist + magic bytes + согласованность типов.

    Возвращает нормализованный MIME для сохранения (заявленный — если он точнее
    и не противоречит содержимому). Бросает HTTPException при нарушении."""
    declared = (declared or "").split(";")[0].strip().lower()
    if not content:
        if declared and _mime_allowed(declared) and _mime_class(declared) == "text":
            return declared
        raise HTTPException(400, "Пустой файл")
    is_text = _text_like(content)
    detected = _detect_mime(content, is_text)

    # 1. исполняемый код запрещён всегда — даже с поддельным Content-Type
    if not is_text:
        for sig, what in FORBIDDEN_SIGNATURES:
            if content.startswith(sig):
                raise HTTPException(400, f"Исполняемый файл ({what}) запрещён")

    # 2. заявленный и реальный тип — в allowlist
    if not declared or not _mime_allowed(declared):
        raise HTTPException(400, f"Недопустимый тип файла: {declared or '—'}")
    if not _mime_allowed(detected):
        raise HTTPException(
            400,
            f"Содержимое файла — недопустимый тип: declared={declared}, actual={detected}")

    # 3. содержимое должно соответствовать заявленному типу
    dc, rc = _mime_class(declared), _mime_class(detected)
    if dc != rc and not (dc == "svg" and rc in ("text", "svg")):
        raise HTTPException(
            400,
            f"Содержимое не соответствует заявленному типу: declared={declared}, actual={detected}")

    # 4. сигнатуры для критичных форматов (pdf/png/jpeg/gif/webp/zip/gzip, SVG)
    if dc == "svg":
        if b"<svg" not in content[:4096].lower():
            raise HTTPException(400, "Неверная сигнатура файла (ожидается SVG)")
    else:
        sigs = MAGIC_SIGNATURES.get(declared) or MAGIC_SIGNATURES.get(detected)
        if sigs and not any(content.startswith(sig) for sig in sigs):
            raise HTTPException(400, f"Неверная сигнатура файла (ожидается {declared})")
        if declared == "image/webp" and content[8:12] != b"WEBP":
            raise HTTPException(400, "Неверная сигнатура файла (ожидается WebP)")
    return declared or detected


async def _read_upload(file: UploadFile) -> tuple[bytes, str]:
    """Чтение файла с лимитом размера + проверка содержимого. Возвращает (data, mime)."""
    limit = settings.DOCS_MAX_UPLOAD_MB * 1024 * 1024
    chunks: list[bytes] = []
    size = 0
    while chunk := await file.read(1024 * 1024):
        size += len(chunk)
        if size > limit:
            raise HTTPException(413, f"Файл больше лимита {settings.DOCS_MAX_UPLOAD_MB} МБ")
        chunks.append(chunk)
    data = b"".join(chunks)
    mime = _validate_upload(file.content_type, data)
    return data, mime


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

    # лимит размера + проверка типа содержимого (allowlist, magic bytes)
    data, mime = await _read_upload(file)
    size = len(data)

    stored = uuid.uuid4().hex + "_" + _safe_name(file.filename or "file")
    (_docs_dir() / stored).write_bytes(data)

    f = DocFile(page_id=pid, section_id=None, name=_safe_name(file.filename or "file"), stored=stored,
                size=size, mime=mime)
    db.add(f)
    audit(db, user, "doc_file_upload", f.name, {"page": p.title, "size": size, "mime": mime})
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

    # лимит размера + проверка типа содержимого (allowlist, magic bytes)
    data, mime = await _read_upload(file)
    size = len(data)

    stored = uuid.uuid4().hex + "_" + _safe_name(file.filename or "file")
    (_docs_dir() / stored).write_bytes(data)

    f = DocFile(page_id=None, section_id=sid, name=_safe_name(file.filename or "file"), stored=stored,
                size=size, mime=mime)
    db.add(f)
    audit(db, user, "doc_file_upload", f.name, {"section": s.title, "size": size, "mime": mime})
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
    # inline — только для безопасных типов; активный контент (html/svg/xml/js) —
    # всегда attachment + nosniff (защита от stored XSS через загруженный файл)
    base_mime = (f.mime or "").split(";")[0].strip().lower()
    active = {
        "text/html", "application/xhtml+xml", "image/svg+xml",
        "text/xml", "application/xml", "text/javascript", "application/javascript",
        "application/x-javascript",
    }
    inline = (
        (base_mime.startswith("image/") and base_mime != "image/svg+xml")
        or base_mime == "application/pdf"
        or (base_mime.startswith("text/") and base_mime not in active)
    )
    resp = FileResponse(
        str(path),
        media_type=f.mime or "application/octet-stream",
        filename=f.name,
        content_disposition_type="inline" if inline else "attachment",
    )
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


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

"""Smoke-тест фиксов: гонка создания подсетей, sparse-режим, счётчики, magic bytes, DNS retry."""
import asyncio
import os
import sys

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./smoke_test.db"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

if os.path.exists("smoke_test.db"):
    os.remove("smoke_test.db")




async def main():
    from fastapi import HTTPException
    from sqlalchemy import func, select

    from app.db import SessionLocal, init_db
    from app.models import Ip, Subnet
    from app.routers import docs as docs_r
    from app.routers.subnets import blocks, create_subnet
    from app.schemas import SubnetIn
    from app.scanner.dns import resolve_ptrs
    from app.service import (
        check_overlap, is_sparse, resync_subnet_ips, subnet_capacity, usage_counts,
    )

    await init_db()
    ok = []

    def check(name, cond, extra=""):
        ok.append((name, cond, extra))
        print(("PASS" if cond else "FAIL"), name, extra)

    async with SessionLocal() as db:
        # --- 1. dense /24: полная материализация ---
        s24 = await create_subnet(SubnetIn(name="dense", cidr="192.168.10.0/24",
                                           gateway="192.168.10.1"), db=db, user=None)
        rows24 = (await db.execute(select(func.count()).select_from(Ip)
                                   .where(Ip.subnet_id == s24["id"]))).scalar()
        check("dense /24 materialized 254 rows", rows24 == 254, f"rows={rows24}")

        # --- 2. sparse /16: только шлюз, ёмкость считается динамически ---
        assert is_sparse("10.0.0.0/16") and not is_sparse("10.0.0.0/20")
        s16 = await create_subnet(SubnetIn(name="sparse", cidr="10.0.0.0/16",
                                           gateway="10.0.0.1"), db=db, user=None)
        sub16 = await db.get(Subnet, s16["id"])
        rows16 = (await db.execute(select(func.count()).select_from(Ip)
                                   .where(Ip.subnet_id == s16["id"]))).scalar()
        check("sparse /16 has only gateway row", sub16.sparse and rows16 == 1, f"rows={rows16}")
        check("capacity /16", subnet_capacity("10.0.0.0/16") == 65534)

        c = await usage_counts(db, sub16)
        check("sparse counts: total=65534, free=capacity-occupied-cond",
              c["total"] == 65534 and c["free"] == 65534 - c["occupied"] - c["cond_free"],
              f"total={c['total']} free={c['free']} pct={c['pct']}")

        # --- 3. вложенная /24 внутрь sparse /16: строки переезжают/материализуются ---
        s_nested = await create_subnet(SubnetIn(name="nested", cidr="10.0.1.0/24"), db=db, user=None)
        rows_n = (await db.execute(select(func.count()).select_from(Ip)
                                   .where(Ip.subnet_id == s_nested["id"]))).scalar()
        check("nested /24 dense: 254 rows", rows_n == 254, f"rows={rows_n}")

        # --- 4. гонка: два конкурентных create одного CIDR -> ровно один победил ---
        async def mk(name, cidr):
            try:
                async with SessionLocal() as s:
                    await create_subnet(SubnetIn(name=name, cidr=cidr), db=s, user=None)
                return "created"
            except HTTPException as e:
                return f"http{e.status_code}"
            except Exception as e:
                return type(e).__name__

        r = await asyncio.gather(mk("r1", "172.16.0.0/24"), mk("r2", "172.16.0.0/24"),
                                 mk("r3", "172.16.0.0/24"))
        created = r.count("created")
        check("race: exactly one created", created == 1, f"results={r}")

        # пересечение (дубль) под локом — штатный 409
        try:
            async with SessionLocal() as s:
                await create_subnet(SubnetIn(name="dup", cidr="192.168.10.0/24"), db=s, user=None)
            check("duplicate -> 409", False)
        except HTTPException as e:
            check("duplicate -> 409", e.status_code == 409)

        # --- 5. blocks для sparse: все блоки /24 с ёмкостью ---
        # у /16 крайние /24-блоки содержат по 255 хостов (10.0.0.255 и 10.0.255.0 —
        # валидные адреса /16), средние — по 256 — как у dense по net.hosts()
        bl = await blocks(s16["id"], db=db, user=None)
        check("sparse blocks: 256 x /24", len(bl) == 256 and bl[0]["cidr"] == "10.0.0.0/24"
              and bl[0]["total"] == 255 and bl[128]["total"] == 256 and bl[-1]["total"] == 255
              and bl[0]["free"] == bl[0]["total"] - bl[0]["used"] - bl[0]["reserved"],
              f"first={bl[0]} mid_total={bl[128]['total']} n={len(bl)}")
        bl_n = await blocks(s_nested["id"], db=db, user=None)
        check("nested blocks: 1 x /24", len(bl_n) == 1 and bl_n[0]["cidr"] == "10.0.1.0/24",
              f"first={bl_n[0]}")

    # --- 6. magic bytes / allowlist (docs upload) ---
    V = docs_r._validate_upload
    check("pdf ok", V("application/pdf", b"%PDF-1.7 body") == "application/pdf")
    check("md ok (text)", V("text/markdown", "# hello\n".encode()) == "text/markdown")
    check("docx ok (zip sig)", V(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        b"PK\x03\x04" + b"\x00" * 30) ==
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    try:
        V("application/pdf", b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 30)
        check("ELF w/ fake pdf type rejected", False)
    except HTTPException as e:
        check("ELF w/ fake pdf type rejected", e.status_code == 400, str(e.detail))
    try:
        V("text/plain", b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 30)
        check("PE w/ fake text type rejected", False)
    except HTTPException as e:
        check("PE w/ fake text type rejected", e.status_code == 400, str(e.detail))
    try:
        V("image/png", b"not a png at all, just text")
        check("png w/o signature rejected", False)
    except HTTPException as e:
        check("png w/o signature rejected", e.status_code == 400, str(e.detail))
    try:
        V("image/png", b"%PDF-1.4 fake png")  # pdf-содержимое под png-типом
        check("content/declared mismatch rejected", False)
    except HTTPException as e:
        check("content/declared mismatch rejected", e.status_code == 400, str(e.detail))
    try:
        V("application/octet-stream", b"\x01\x02\x03\x04")
        check("unknown binary rejected", False)
    except HTTPException as e:
        check("unknown binary rejected", e.status_code == 400, str(e.detail))
    check("svg ok", V("image/svg+xml", b'<?xml version="1.0"?><svg xmlns="a"/>') == "image/svg+xml")

    # --- 7. DNS retry (несуществующий сервер -> таймауты/ошибки + retries, без падений) ---
    import socket as _sock
    _orig_ghba = _sock.gethostbyaddr
    _sock.gethostbyaddr = lambda ip: (_ for _ in ()).throw(OSError("test: no system resolver"))
    try:
        t0 = asyncio.get_event_loop().time()
        res, stats = await resolve_ptrs(["192.0.2.55"], servers=["203.0.113.1"], timeout=0.15, retries=1)
        dt = asyncio.get_event_loop().time() - t0
    finally:
        _sock.gethostbyaddr = _orig_ghba
    row = stats["by_server"][0]
    # в зависимости от сети песочницы неответ = timeout либо сетевая ошибка
    check("dns retries counted", (row["timeouts"] + row["errors"]) == 2 and row["retries"] == 1,
          f"timeouts={row['timeouts']} errors={row['errors']} retries={row['retries']} queries={row['queries']}")
    check("dns no result, fallback safe", res == {} and stats["unresolved"] == 1)
    check("dns retry delay elapsed", dt >= 0.5, f"dt={dt:.2f}s")

    # --- 8. очистка БД: статьи (документация) НЕ удаляются ---
    from app.models import DocPage, DocSection
    from app.routers.admin import clear_db

    class _Admin:
        id = None
        username = "admin"
        provider = "local"

    async with SessionLocal() as db:
        sec = DocSection(title="Регламенты", position=1)
        db.add(sec)
        await db.flush()
        page = DocPage(section_id=sec.id, title="Статья 1", body="текст статьи")
        db.add(page)
        sn = Subnet(cidr="198.51.100.0/24", name="под очистку", descr="d", sparse=False)
        db.add(sn)
        await db.flush()
        page_id, sec_id, sn_id = page.id, sec.id, sn.id
        await db.commit()
    async with SessionLocal() as db:
        await clear_db(db=db, user=_Admin())
    async with SessionLocal() as db:
        page_left = await db.get(DocPage, page_id)
        sec_left = await db.get(DocSection, sec_id)
        sn_left = await db.get(Subnet, sn_id)
    check("clear-db keeps docs, wipes subnets",
          page_left is not None and sec_left is not None and sn_left is None,
          f"page={page_left is not None} section={sec_left is not None} subnet={sn_left is not None}")

    # --- 9. целостность вложений: отсутствующий файл детектируется ---
    from app.models import DocFile
    from app.routers.docs import check_docs_integrity

    async with SessionLocal() as db:
        db.add(DocFile(page_id=page_id, name="lost.pdf",
                       stored="definitely-missing.bin", size=1, mime="application/pdf"))
        await db.commit()
    total9, missing9 = await check_docs_integrity()
    check("docs integrity detects missing file",
          total9 >= 1 and "definitely-missing.bin" in missing9,
          f"total={total9} missing={missing9}")

    os.remove("smoke_test.db")
    failed = [n for n, c_, _ in ok if not c_]
    print("\n===", "ALL PASS" if not failed else f"FAILED: {failed}", f"({len(ok)} checks)")
    return 1 if failed else 0


sys.exit(asyncio.run(main()))

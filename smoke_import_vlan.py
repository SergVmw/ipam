"""Smoke-тест маппинга VLAN при импорте phpIPAM: слияния быть не должно (кроме одного номера).

Сценарии (фиксированные баги импорта):
1. одно имя, РАЗНЫЕ номера — это разные VLAN, НЕ сливать (было: «много сетей в одном vlan»);
2. один номер, разные имена (L2-домены phpIPAM) — вынужденное слияние + предупреждение;
3. повторный импорт — идемпотентен, новых VLAN не появляется;
4. id vlan приходит int, vlanId в подсети — str (рассинхрон типов phpIPAM);
5. VLAN без имени — fallback «VLAN <номер>», не теряется.
"""
import asyncio
import os
import sys

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./smoke_vlan.db"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

if os.path.exists("smoke_vlan.db"):
    os.remove("smoke_vlan.db")


async def main():
    from sqlalchemy import select

    from app.db import SessionLocal, init_db
    from app.models import Subnet, Vlan
    from app.routers import phpipam as ph

    await init_db()
    ok = []

    def check(name, cond, extra=""):
        ok.append((name, cond))
        print(("PASS" if cond else "FAIL"), name, extra)

    class FakeData:
        app = "app"
        import_ips = False
        insecure = False
        relink_vlans = False

    class FakeUser:
        username = "tester"
        id = None

    async def run(vlans, subnets, do_apply, relink=False):
        async def fake_fetch(root, data):
            return vlans, subnets, "tok"
        orig = ph._fetch_all
        ph._fetch_all = fake_fetch
        fd = FakeData()
        fd.relink_vlans = relink
        try:
            return await ph._run_import("http://x/api/app/", fd, do_apply, *await _sess())
        finally:
            ph._fetch_all = orig

    # _run_import требует (root, data, do_apply, db, user) — держим сессию снаружи
    _holder = {}

    async def _sess():
        if "db" not in _holder:
            _holder["db"] = SessionLocal()
        return _holder["db"], FakeUser()

    try:
        # --- 1. одно имя, разные номера: НЕ сливать ---
        rep = await run(
            [{"id": "1", "name": "Users", "number": "10"},
             {"id": "2", "name": "Users", "number": "20"}],
            [{"id": "11", "subnet": "10.0.1.0", "mask": "24", "vlanId": "1", "name": "a"},
             {"id": "12", "subnet": "10.0.2.0", "mask": "24", "vlanId": "2", "name": "b"}],
            do_apply=True,
        )
        async with SessionLocal() as db:
            vl = {v.name: v.vid for v in (await db.execute(select(Vlan))).scalars()}
            subs = {s.cidr: s.vlan_id for s in (await db.execute(select(Subnet))).scalars()}
            vlan_ids = {v.id: (v.name, v.vid) for v in (await db.execute(select(Vlan))).scalars()}
        check("1a: два VLAN с одним именем — оба существуют",
              len(vl) == 2 and 10 in vl.values() and 20 in vl.values(), f"vlans={vl}")
        n1 = {n for n, v in vlan_ids.values()}
        check("1b: имена не потеряны (второй с уточнением)",
              "Users" in n1 and any(x.startswith("Users (") for x in n1), f"{n1}")
        sid_a, sid_b = subs["10.0.1.0/24"], subs["10.0.2.0/24"]
        check("1c: сети остались в РАЗНЫХ vlan", sid_a != sid_b,
              f"{vlan_ids.get(sid_a)} vs {vlan_ids.get(sid_b)}")
        check("1d: предупреждение в отчёте", any("имя занято" in i for i in rep.get("issues", [])),
              rep.get("issues"))

        # --- 2. один номер, разные имена (L2-домены) — слияние с предупреждением ---
        rep2 = await run(
            [{"id": "5", "name": "Voice", "number": "100"},
             {"id": "6", "name": "Voice-East", "number": "100"}],
            [{"id": "21", "subnet": "10.1.1.0", "mask": "24", "vlanId": "5", "name": "c"},
             {"id": "22", "subnet": "10.1.2.0", "mask": "24", "vlanId": "6", "name": "d"}],
            do_apply=True,
        )
        async with SessionLocal() as db:
            subs2 = {s.cidr: s.vlan_id for s in (await db.execute(select(Subnet))).scalars()}
        check("2: один номер -> один vlan (vid уникален), сети в нём",
              subs2["10.1.1.0/24"] == subs2["10.1.2.0/24"] is not None, f"{subs2}")
        check("2b: слияние объяснено в отчёте", any("один номер" in i for i in rep2.get("issues", [])),
              rep2.get("issues"))

        # --- 3. повторный импорт: идемпотентность ---
        rep3 = await run(
            [{"id": "1", "name": "Users", "number": "10"},
             {"id": "2", "name": "Users", "number": "20"}],
            [{"id": "11", "subnet": "10.0.1.0", "mask": "24", "vlanId": "1", "name": "a"},
             {"id": "12", "subnet": "10.0.2.0", "mask": "24", "vlanId": "2", "name": "b"}],
            do_apply=True,
        )
        async with SessionLocal() as db:
            n_vlans = len((await db.execute(select(Vlan))).scalars().all())
        # Users#10 + Users(20)#20 (сцен.1) + Voice#100 (сцен.2, слияние по номеру) = 3
        check("3: повторный импорт не плодит VLAN", rep3["vlans_new"] == 0 and n_vlans == 3,
              f"new={rep3['vlans_new']} total={n_vlans} issues={rep3.get('issues')}")

        # --- 4. рассинхрон типов id: int в vlans, str в subnets ---
        rep4 = await run(
            [{"id": 7, "name": "IoT", "number": "30"}],          # id — int
            [{"id": "31", "subnet": "10.2.1.0", "mask": "24",
              "vlanId": "7", "name": "e"}],                       # vlanId — str
            do_apply=True,
        )
        async with SessionLocal() as db:
            s4 = (await db.execute(select(Subnet).where(Subnet.cidr == "10.2.1.0/24"))).scalar_one()
            v4 = await db.get(Vlan, s4.vlan_id)
        check("4: str/int ключи нашли друг друга", v4 is not None and v4.name == "IoT",
              f"{v4 and v4.name} issues={rep4.get('issues')}")

        # --- 6. catch-all: битая запись VLAN без id + сети БЕЗ привязки — «без VLAN» ---
        rep6 = await run(
            [{"id": "", "name": "Ghost", "number": "70"},   # запись без id — мимо карты
             {"id": "9", "name": "Real", "number": "71"}],
            [{"id": "51", "subnet": "10.4.1.0", "mask": "24", "vlanId": None, "name": "g"},
             {"id": "52", "subnet": "10.4.2.0", "mask": "24", "vlanId": "0", "name": "h"},
             {"id": "53", "subnet": "10.4.3.0", "mask": "24", "vlanId": "9", "name": "i"}],
            do_apply=True,
        )
        async with SessionLocal() as db:
            rows6 = {s.cidr: s.vlan_id for s in (await db.execute(select(Subnet))).scalars()}
            ghost = (await db.execute(select(Vlan).where(Vlan.name == "Ghost"))).scalar_one_or_none()
        check("6: сети без привязки — «без VLAN», catch-all не сработал",
              rows6.get("10.4.1.0/24") is None and rows6.get("10.4.2.0/24") is None
              and rows6.get("10.4.3.0/24") is not None and ghost is None,
              f"{rows6} issues={rep6.get('issues')}")

        # --- 7. vlan_links: по каждой сети видно привязку из phpIPAM и результат ---
        links = {l["cidr"]: l for l in rep6.get("vlan_links", [])}
        check("7: vlan_links в отчёте (источник и результат)",
              "10.4.1.0/24" in links and links["10.4.1.0/24"]["our_vlan"] == ""
              and links["10.4.3.0/24"]["our_vlan"] != ""
              and any(v["action"].startswith("создан") for v in rep6.get("vlans_report", [])),
              f"links={links}")

        # --- 8. vlanId = НОМЕР 802.1Q (а не id записи) — fallback по номеру ---
        rep8 = await run(
            [{"id": "40", "name": "NumOnly", "number": "88"}],
            [{"id": "61", "subnet": "10.5.1.0", "mask": "24", "vlanId": "88", "name": "j"}],
            do_apply=True,
        )
        async with SessionLocal() as db:
            s8 = (await db.execute(select(Subnet).where(Subnet.cidr == "10.5.1.0/24"))).scalar_one()
            v8 = await db.get(Vlan, s8.vlan_id) if s8.vlan_id else None
        check("8: vlanId-номер сопоставлен по номеру", v8 is not None and v8.name == "NumOnly",
              f"{v8 and v8.name} issues={rep8.get('issues')}")

        # --- 9. режим перепривязки: vlan_id существующих сетей ---
        rep9a = await run([{"id": "7", "name": "A30", "number": "30"}],
                          [{"id": "71", "subnet": "10.9.1.0", "mask": "24", "vlanId": "7", "name": "k"}],
                          do_apply=True)
        async with SessionLocal() as db:
            first_id = (await db.execute(select(Subnet).where(Subnet.cidr == "10.9.1.0/24"))).scalar_one().vlan_id
        # привязку в phpIPAM сняли (vlanId=0), перепривязка ВЫКЛ: vlan_id не трогаем
        rep9b = await run([{"id": "7", "name": "A30", "number": "30"}],
                          [{"id": "71", "subnet": "10.9.1.0", "mask": "24", "vlanId": "0", "name": "k"}],
                          do_apply=True)
        async with SessionLocal() as db:
            s9 = (await db.execute(select(Subnet).where(Subnet.cidr == "10.9.1.0/24"))).scalar_one()
            link9b = {l["cidr"]: l for l in rep9b.get("vlan_links", [])}["10.9.1.0/24"]
        check("9a: без перепривязки vlan_id существующей сети не меняется",
              s9.vlan_id == first_id and rep9b.get("subnets_relink", 0) == 0 and link9b["our_vlan"] != "",
              f"vlan_id={s9.vlan_id} link={link9b}")
        # перепривязка ВКЛ: отвязать
        rep9c = await run([{"id": "7", "name": "A30", "number": "30"}],
                          [{"id": "71", "subnet": "10.9.1.0", "mask": "24", "vlanId": "0", "name": "k"}],
                          do_apply=True, relink=True)
        async with SessionLocal() as db:
            s9 = (await db.execute(select(Subnet).where(Subnet.cidr == "10.9.1.0/24"))).scalar_one()
        check("9b: перепривязка отвязывает сеть (vlanId=0 в phpIPAM)",
              s9.vlan_id is None and rep9c.get("subnets_relink") == 1, f"vlan_id={s9.vlan_id}")
        # перепривязка ВКЛ: привязать обратно
        rep9d = await run([{"id": "7", "name": "A30", "number": "30"}],
                          [{"id": "71", "subnet": "10.9.1.0", "mask": "24", "vlanId": "7", "name": "k"}],
                          do_apply=True, relink=True)
        async with SessionLocal() as db:
            s9 = (await db.execute(select(Subnet).where(Subnet.cidr == "10.9.1.0/24"))).scalar_one()
        check("9c: перепривязка привязывает сеть обратно",
              s9.vlan_id == first_id and rep9d.get("subnets_relink") == 1, f"vlan_id={s9.vlan_id}")

        # --- 10. две записи с одним номером и одним именем (разные домены) ---
        rep10 = await run(
            [{"id": "90", "name": "DupName", "number": "77", "domainId": "1"},
             {"id": "91", "name": "DupName", "number": "77", "domainId": "2"}],
            [{"id": "95", "subnet": "10.10.1.0", "mask": "24", "vlanId": "90", "name": "m"},
             {"id": "96", "subnet": "10.10.2.0", "mask": "24", "vlanId": "91", "name": "n"}],
            do_apply=True,
        )
        async with SessionLocal() as db:
            v77 = (await db.execute(select(Vlan).where(Vlan.vid == 77))).scalars().all()
            s10 = {x.cidr: x.vlan_id for x in (await db.execute(
                select(Subnet).where(Subnet.cidr.in_(["10.10.1.0/24", "10.10.2.0/24"])))).scalars()}
        check("10a: записи с одним номером слиты в один VLAN ядра, обе сети в нём",
              len(v77) == 1 and s10.get("10.10.1.0/24") == s10.get("10.10.2.0/24") == v77[0].id,
              f"vlans={[(v.name, v.vid) for v in v77]} subnets={s10}")
        check("10b: слияние объяснено в отчёте (дубли номера с доменами)",
              any("2 записи с этим номером" in i and "домен 2" in i for i in rep10.get("issues", [])),
              f"issues={rep10.get('issues')}")

        # --- 11. точность: номер и имя совпали, описания разные -> явный issue ---
        await run([{"id": "97", "name": "SameName", "number": "78", "description": "Тест МТБ"}],
                  [], do_apply=True)
        rep11 = await run(
            [{"id": "98", "name": "SameName", "number": "78", "description": "Латвия"}],
            [{"id": "99", "subnet": "10.11.1.0", "mask": "24", "vlanId": "98", "name": "o"}],
            do_apply=True,
        )
        async with SessionLocal() as db:
            s11 = (await db.execute(select(Subnet).where(Subnet.cidr == "10.11.1.0/24"))).scalar_one()
            v78 = (await db.execute(select(Vlan).where(Vlan.vid == 78))).scalar_one()
        check("11: расхождение описаний при одном номере объяснено в отчёте",
              s11.vlan_id == v78.id
              and any("описание в phpIPAM" in i and "Латвия" in i for i in rep11.get("issues", [])),
              f"issues={rep11.get('issues')}")

        # --- 5. VLAN без имени: fallback «VLAN <номер>», не теряется ---
        rep5 = await run(
            [{"id": "8", "name": "", "number": "55"}],
            [{"id": "41", "subnet": "10.3.1.0", "mask": "24", "vlanId": "8", "name": "f"}],
            do_apply=True,
        )
        async with SessionLocal() as db:
            s5 = (await db.execute(select(Subnet).where(Subnet.cidr == "10.3.1.0/24"))).scalar_one()
            v5 = await db.get(Vlan, s5.vlan_id) if s5.vlan_id else None
        check("5: VLAN без имени получил «VLAN 55» и привязан к сети",
              v5 is not None and v5.name == "VLAN 55" and v5.vid == 55,
              f"{v5 and (v5.name, v5.vid)} issues={rep5.get('issues')}")
    finally:
        if "db" in _holder:
            await _holder["db"].close()

    if os.path.exists("smoke_vlan.db"):
        os.remove("smoke_vlan.db")
    failed = [n for n, c in ok if not c]
    print("\n===", "ALL PASS" if not failed else f"FAILED: {failed}", f"({len(ok)} checks)")
    return 1 if failed else 0


sys.exit(asyncio.run(main()))

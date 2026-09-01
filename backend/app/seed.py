from datetime import timedelta

from sqlalchemy import func, insert, select

from .config import settings
from .db import SessionLocal
from .models import Ip, Subnet, UsageSnapshot, User, Vlan, utcnow
from .security import hash_password


async def seed_admin() -> None:
    """Локальный админ ВСЕГДА существует и может войти (даже если его удалили в UI)."""
    async with SessionLocal() as db:
        u = (await db.execute(select(User).where(User.username == settings.ADMIN_USER))).scalar_one_or_none()
        if u is None:
            db.add(User(username=settings.ADMIN_USER,
                        password_hash=hash_password(settings.ADMIN_PASSWORD),
                        role="admin", provider="local"))
            await db.commit()
            print(f"[seed] админ {settings.ADMIN_USER} создан (смените пароль!)")
        elif u.role != "admin":
            # защита: локальный админ всегда с ролью admin
            u.role = "admin"
            u.provider = "local"
            await db.commit()
            print("[seed] у локального админа восстановлена роль admin")


async def seed_demo() -> None:
    """Демо: 2 VLAN, 3 сети, часть "живых" IP и история заполняемости."""
    async with SessionLocal() as db:
        n = (await db.execute(select(func.count()).select_from(Subnet))).scalar() or 0
        if n > 0:
            return
        office = Vlan(vid=10, name="Office", color="#38bdf8", descr="Офисный LAN")
        servers = Vlan(vid=20, name="Servers", color="#f472b6", descr="Серверная зона")
        db.add_all([office, servers])
        await db.flush()

        from .service import materialize_ips

        async def mk(name: str, cidr: str, vlan: Vlan, gw: str, dh1: str | None, dh2: str | None, interval: int) -> Subnet:
            s = Subnet(name=name, cidr=cidr, vlan_id=vlan.id, gateway=gw,
                       dhcp_start=dh1, dhcp_end=dh2, scan_enabled=True,
                       scan_interval_s=interval, next_scan_at=utcnow() + timedelta(seconds=60),
                       descr="Демо-данные")
            db.add(s)
            await db.flush()
            await db.execute(insert(Ip), materialize_ips(s.id, cidr, gw, dh1, dh2))
            return s

        s1 = await mk("Office-LAN", "192.168.10.0/24", office, "192.168.10.1", "192.168.10.10", "192.168.10.200", 300)
        s2 = await mk("Office-LAN-2", "192.168.11.0/24", office, "192.168.11.1", "192.168.11.10", "192.168.11.200", 600)
        s3 = await mk("Servers", "10.20.1.0/24", servers, "10.20.1.1", None, None, 600)

        demo = [
            (s1.id, "192.168.10.15", "used", "office-pc-15", "Иванов"),
            (s1.id, "192.168.10.44", "used", "office-pc-44", "Петров"),
            (s1.id, "192.168.10.77", "reserved", None, "Служба ИТ"),
            (s1.id, "192.168.10.100", "offline", "old-pc", None),
            (s3.id, "10.20.1.10", "used", "srv-db", "DBA"),
            (s3.id, "10.20.1.11", "used", "srv-web", "DevOps"),
            (s3.id, "10.20.1.12", "used", "srv-nginx", None),
            (s3.id, "10.20.1.50", "reserved", None, "Бэкапы"),
        ]
        for _sid, ip_str, state, hostname, owner in demo:
            r = (await db.execute(select(Ip).where(Ip.ip == ip_str))).scalar_one()
            r.state = state
            r.hostname = hostname
            r.owner = owner
            r.last_seen = utcnow()
            r.first_seen = utcnow() - timedelta(days=10)

        # история заполняемости за 30 дней (плавный рост) — чтобы график не был пустым
        for s in (s1, s2, s3):
            st = (await db.execute(
                select(Ip.state, func.count()).where(Ip.subnet_id == s.id).group_by(Ip.state)
            )).all()
            c = {"free": 0, "used": 0, "reserved": 0, "offline": 0}
            for state_, n in st:
                if state_ in c:
                    c[state_] = n
            total = sum(c.values())
            for i in range(30, 0, -1):
                used = max(0, c["used"] - i // 4)
                db.add(UsageSnapshot(
                    subnet_id=s.id, at=utcnow() - timedelta(days=i),
                    total=total, used=used, reserved=c["reserved"], offline=c["offline"],
                    free=total - used - c["reserved"] - c["offline"],
                ))
        await db.commit()
        print("[seed] демо-данные: 2 VLAN, 3 сети")

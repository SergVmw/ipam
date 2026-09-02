from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    """Наивный UTC — единообразные сравнения в sqlite и postgres."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Vlan(Base):
    __tablename__ = "vlan"

    id: Mapped[int] = mapped_column(primary_key=True)
    vid: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    color: Mapped[str | None] = mapped_column(String(16))
    descr: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[str | None] = mapped_column(String(255))  # теги через запятую: "prod,finance"

    subnets: Mapped[list["Subnet"]] = relationship(back_populates="vlan")


class Location(Base):
    """Местоположение (точка) — точка начала/конца линии связи.
    Одна точка может использоваться несколькими линиями связи."""
    __tablename__ = "location"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    address: Mapped[str | None] = mapped_column(String(255))
    lat: Mapped[float | None] = mapped_column(Float)  # градусы; None = нет координат
    lng: Mapped[float | None] = mapped_column(Float)
    descr: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FiberLink(Base):
    """Линия связи (оптический канал): точка А — точка Б, ёмкость, статус."""
    __tablename__ = "fiber_link"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    a_id: Mapped[int] = mapped_column(ForeignKey("location.id", ondelete="RESTRICT"), index=True, nullable=False)
    b_id: Mapped[int] = mapped_column(ForeignKey("location.id", ondelete="RESTRICT"), index=True, nullable=False)
    capacity: Mapped[float | None] = mapped_column(Float)  # Гбит/с (общая, необязательно)
    fibers: Mapped[int | None] = mapped_column(Integer)  # ёмкость в волокнах (кол-во оптических волокон)
    length: Mapped[float | None] = mapped_column(Float)  # длина трассы, км
    fiber_usage: Mapped[str | None] = mapped_column(Text)  # JSON: [{"name":"LAN","count":10}] — назначение вручную
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    descr: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Subnet(Base):
    __tablename__ = "subnet"

    id: Mapped[int] = mapped_column(primary_key=True)
    cidr: Mapped[str] = mapped_column(String(19), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    vlan_id: Mapped[int | None] = mapped_column(ForeignKey("vlan.id", ondelete="SET NULL"))
    gateway: Mapped[str | None] = mapped_column(String(45))
    dhcp_start: Mapped[str | None] = mapped_column(String(45))
    dhcp_end: Mapped[str | None] = mapped_column(String(45))
    scan_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    scan_interval_s: Mapped[int | None] = mapped_column(Integer)
    scan_method: Mapped[str | None] = mapped_column(String(16))  # fping|nmap|tcp; None = глобальный из Настроек
    tags: Mapped[str | None] = mapped_column(String(255))  # теги через запятую: "prod,finance"
    next_scan_at: Mapped[datetime | None] = mapped_column(DateTime)
    descr: Mapped[str | None] = mapped_column(Text)

    vlan: Mapped["Vlan | None"] = relationship(back_populates="subnets")


class Ip(Base):
    __tablename__ = "ip"

    id: Mapped[int] = mapped_column(primary_key=True)
    ip: Mapped[str] = mapped_column(String(45), unique=True, nullable=False)
    ip_int: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)  # bigint! IPv4 int до 2^32
    subnet_id: Mapped[int] = mapped_column(ForeignKey("subnet.id", ondelete="CASCADE"), index=True, nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="free", nullable=False)  # free|used|reserved|offline
    mac: Mapped[str | None] = mapped_column(String(17))  # из nmap/агента на L2-сегменте
    mac_vendor: Mapped[str | None] = mapped_column(String(64))  # vendor MAC (nmap OUI): "VMware", "Cisco Systems", ...
    hostname: Mapped[str | None] = mapped_column(String(255))
    hostname_manual: Mapped[bool] = mapped_column(Boolean, default=False)  # ручной hostname не затирается PTR
    owner: Mapped[str | None] = mapped_column(String(128))
    note: Mapped[str | None] = mapped_column(Text)
    is_gateway: Mapped[bool] = mapped_column(Boolean, default=False)
    in_dhcp: Mapped[bool] = mapped_column(Boolean, default=False)
    misses: Mapped[int] = mapped_column(Integer, default=0)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime)


class ScanRun(Base):
    __tablename__ = "scan_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    subnet_id: Mapped[int] = mapped_column(ForeignKey("subnet.id", ondelete="CASCADE"), index=True, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    alive: Mapped[int | None] = mapped_column(Integer)
    new_ips: Mapped[int | None] = mapped_column(Integer)
    freed_ips: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)


class IpEvent(Base):
    __tablename__ = "ip_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    ip: Mapped[str | None] = mapped_column(String(45))
    subnet_id: Mapped[int | None] = mapped_column(ForeignKey("subnet.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)  # ip_seen|ip_freed|hostname_changed|reserved_alive|mac_changed|conflict
    detail: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class UsageSnapshot(Base):
    __tablename__ = "usage_snapshot"

    id: Mapped[int] = mapped_column(primary_key=True)
    subnet_id: Mapped[int] = mapped_column(ForeignKey("subnet.id", ondelete="CASCADE"), index=True, nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    used: Mapped[int] = mapped_column(Integer, default=0)
    free: Mapped[int] = mapped_column(Integer, default=0)
    reserved: Mapped[int] = mapped_column(Integer, default=0)
    offline: Mapped[int] = mapped_column(Integer, default=0)


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), default="")  # пусто у доменных пользователей
    role: Mapped[str] = mapped_column(String(16), default="viewer", nullable=False)  # admin|operator|viewer
    provider: Mapped[str] = mapped_column(String(16), default="local", nullable=False)  # local|ldap
    display_name: Mapped[str | None] = mapped_column(String(128))  # для доменных: Имя Фамилия из AD
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Agent(Base):
    """L2-агент: маленький скрипт на машине в VLAN, по cron шлёт отчёт живых хостов в ядро."""
    __tablename__ = "agent"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    subnet_ids: Mapped[str | None] = mapped_column(String(255))  # "1,2,3" или None = все сети
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_report_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_hosts: Mapped[int | None] = mapped_column(Integer)
    descr: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # удалённая установка по SSH
    ssh_host: Mapped[str | None] = mapped_column(String(128))
    ssh_port: Mapped[int | None] = mapped_column(Integer)
    ssh_user: Mapped[str | None] = mapped_column(String(64))
    ssh_password: Mapped[str | None] = mapped_column(String(255))
    poll_file: Mapped[str | None] = mapped_column(String(255))  # файл-триггер принудительного опроса
    report_interval_min: Mapped[int | None] = mapped_column(Integer)  # None/0 = глобальная настройка на агенте
    last_install_at: Mapped[datetime | None] = mapped_column(DateTime)
    install_log: Mapped[str | None] = mapped_column(Text)  # json: шаги последней установки


class AgentReport(Base):
    """Приём отчёта агентом — история «когда агент отработал» (карточка агента → «отчёты»)."""
    __tablename__ = "agent_report_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agent.id", ondelete="CASCADE"), index=True, nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)
    hosts: Mapped[int] = mapped_column(Integer, default=0)      # IP-записей в отчёте
    applied: Mapped[int] = mapped_column(Integer, default=0)    # применили к строкам ядра
    with_mac: Mapped[int] = mapped_column(Integer, default=0)   # хостов с MAC


class DocSection(Base):
    """Раздел документации (например: «Схемы», «Регламенты», «Справочное»)."""
    __tablename__ = "doc_section"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    pages: Mapped[list["DocPage"]] = relationship(back_populates="section", cascade="all, delete-orphan")


class DocPage(Base):
    """Документ: markdown-текст + прикреплённые файлы."""
    __tablename__ = "doc_page"

    id: Mapped[int] = mapped_column(primary_key=True)
    section_id: Mapped[int] = mapped_column(ForeignKey("doc_section.id", ondelete="CASCADE"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_by: Mapped[str | None] = mapped_column(String(128))

    section: Mapped["DocSection"] = relationship(back_populates="pages")
    files: Mapped[list["DocFile"]] = relationship(back_populates="page", cascade="all, delete-orphan")


class DocFile(Base):
    """Файл: прикреплён к документу (page_id) или сразу к разделу (section_id)."""
    __tablename__ = "doc_file"

    id: Mapped[int] = mapped_column(primary_key=True)
    page_id: Mapped[int | None] = mapped_column(ForeignKey("doc_page.id", ondelete="CASCADE"), index=True)
    section_id: Mapped[int | None] = mapped_column(ForeignKey("doc_section.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)      # исходное имя
    stored: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)  # имя на диске
    size: Mapped[int] = mapped_column(Integer, default=0)
    mime: Mapped[str | None] = mapped_column(String(128))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    page: Mapped["DocPage | None"] = relationship(back_populates="files")


class AppSetting(Base):
    """Runtime-настройки из UI (Настройки): переопределяют env-дефолты."""
    __tablename__ = "app_setting"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str | None] = mapped_column(String(128))
    detail: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

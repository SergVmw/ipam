from ipaddress import ip_network

from pydantic import BaseModel, Field, field_validator


class VlanIn(BaseModel):
    vid: int = Field(ge=1, le=4094)
    name: str = Field(min_length=1, max_length=64)
    color: str | None = None
    descr: str | None = None
    tags: str | None = None  # через запятую: "prod,finance"


class LocationIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    address: str | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    descr: str | None = None


class FiberUsageIn(BaseModel):
    """Назначение волокон линии: название вводится вручную (LAN, SAN, …) — не предопределено.
    speed — скорость/ёмкость назначения в Гбит/с; None или 0 → не выводится."""
    name: str = Field(min_length=1, max_length=64)
    count: int = Field(ge=1)
    speed: float | None = Field(default=None, ge=0)


class LinkIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    a_id: int
    b_id: int
    capacity: float | None = Field(default=None, ge=0)  # Гбит/с (общая, необязательно)
    fibers: int | None = Field(default=None, ge=1)  # число волокон
    length: float | None = Field(default=None, ge=0)  # длина трассы, км
    fiber_usage: list[FiberUsageIn] | None = None  # напр. [{"name":"LAN","count":10,"speed":10}]
    is_active: bool = True
    descr: str | None = None


class SubnetIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    cidr: str
    vlan_id: int | None = None
    gateway: str | None = None
    dhcp_start: str | None = None
    dhcp_end: str | None = None
    scan_enabled: bool = False
    scan_interval_s: int | None = Field(default=None, ge=60)
    scan_method: str | None = Field(default=None, pattern="^($|fping|nmap|tcp)$")  # пусто/None = по умолчанию из Настроек
    tags: str | None = None
    descr: str | None = None

    @field_validator("cidr")
    @classmethod
    def _valid_cidr(cls, v: str) -> str:
        try:
            net = ip_network(v)
        except ValueError:
            raise ValueError("Некорректный CIDR, например 192.168.1.0/24")
        if not (16 <= net.prefixlen <= 32):
            raise ValueError("Поддерживаются IPv4-сети от /16 до /32 (для больших — разреженный режим, не реализован)")
        return str(net)


class SubnetUpdate(BaseModel):
    name: str | None = None
    vlan_id: int | None = None  # 0 = снять VLAN
    gateway: str | None = None
    dhcp_start: str | None = None
    dhcp_end: str | None = None
    scan_enabled: bool | None = None
    scan_interval_s: int | None = Field(default=None, ge=60)
    scan_method: str | None = Field(default=None, pattern="^($|fping|nmap|tcp)$")
    tags: str | None = None
    descr: str | None = None


class IpUpdate(BaseModel):
    state: str | None = None  # free | reserved | used
    owner: str | None = None
    note: str | None = None
    hostname: str | None = None
    clear_hostname: bool = False


class LoginIn(BaseModel):
    username: str
    password: str


class UiLinkIn(BaseModel):
    """Ссылка в сайдбаре (Настройки → Ссылки)."""
    title: str = Field(min_length=1, max_length=128)
    url: str = Field(min_length=1, max_length=500)
    new_window: bool = True


class SettingsIn(BaseModel):
    dns_servers: str | None = None
    resolve_dns: bool | None = None
    scan_method: str | None = Field(default=None, pattern="^(auto|fping|nmap|tcp)$")
    scan_rate: int | None = Field(default=None, ge=1, le=100000)
    scan_timeout_ms: int | None = Field(default=None, ge=50, le=10000)
    tz_offset_min: int | None = Field(default=None, ge=-720, le=840)
    # домен AD/LDAP
    ldap_enabled: bool | None = None
    ldap_url: str | None = None
    ldap_base_dn: str | None = None
    ldap_user_dn_template: str | None = None
    ldap_search_filter: str | None = None
    ldap_bind_dn: str | None = None
    ldap_bind_password: str | None = None  # "****" = не менять
    ldap_default_role: str | None = Field(default=None, pattern="^(admin|operator|viewer)$")
    ldap_allow_list: str | None = Field(default=None, max_length=2000)  # логины через запятую; "" = все доменные
    # оформление сайта
    ui_logo: str | None = Field(default=None, max_length=500_000)  # dataURL; "" = убрать
    copyright: str | None = Field(default=None, max_length=255)
    admin_email: str | None = Field(default=None, max_length=255)
    ui_links: list["UiLinkIn"] | None = None
    show_no_dns: bool | None = None  # показывать секцию «IP без hostname — внести в DNS»
    search_mode: str | None = Field(default=None, pattern="^(page|live)$")  # поиск: страница / live-панель
    org_name: str | None = Field(default=None, max_length=128)  # название организации (title страницы)
    mail_enabled: bool | None = None
    smtp_host: str | None = None
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_user: str | None = None
    smtp_password: str | None = None  # "****" или пусто = не менять
    smtp_starttls: bool | None = None
    mail_from: str | None = None
    mail_to: str | None = None


class UserIn(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str | None = Field(default=None, min_length=4, max_length=128)
    role: str = "operator"
    provider: str = Field(default="local", pattern="^(local|ldap)$")


class UserUpdate(BaseModel):
    username: str | None = Field(default=None, min_length=2, max_length=64)
    password: str | None = Field(default=None, min_length=4, max_length=128)
    role: str | None = None


class AgentIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    subnet_ids: list[int] | None = None  # None = все сети
    enabled: bool = True
    descr: str | None = None
    poll_file: str | None = Field(default=None, max_length=255)
    ssh_host: str | None = Field(default=None, max_length=128)
    ssh_port: int | None = Field(default=None, ge=1, le=65535)
    ssh_user: str | None = Field(default=None, max_length=64)
    ssh_password: str | None = Field(default=None, max_length=255)


class AgentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    subnet_ids: list[int] | None = None
    enabled: bool | None = None
    descr: str | None = None
    poll_file: str | None = Field(default=None, max_length=255)
    ssh_host: str | None = Field(default=None, max_length=128)
    ssh_port: int | None = Field(default=None, ge=1, le=65535)
    ssh_user: str | None = Field(default=None, max_length=64)
    ssh_password: str | None = Field(default=None, max_length=255)  # "****" = не менять
    regenerate_key: bool = False


# ---------------------------------------------------------------------------
# Документация
# ---------------------------------------------------------------------------
class DocSectionIn(BaseModel):
    title: str = Field(min_length=1, max_length=128)


class DocSectionUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=128)
    position: int | None = Field(default=None, ge=0)


class DocPageIn(BaseModel):
    section_id: int
    title: str = Field(min_length=1, max_length=255)
    body: str = ""


class DocPageUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    body: str | None = None


class DocFileOut(BaseModel):
    id: int
    name: str
    size: int
    mime: str | None
    url: str
    editable: bool = False
    uploaded_at: str | None


class DocFileContent(BaseModel):
    content: str = Field(max_length=10_000_000)


class DocPageFull(BaseModel):
    id: int
    title: str
    body: str
    section_id: int
    section_title: str
    created_at: str | None
    updated_at: str | None
    updated_by: str | None
    files: list[DocFileOut]


class PhpIPamIn(BaseModel):
    base_url: str = Field(min_length=4, max_length=300)   # адрес phpIPAM (https://host/phpipam)
    app: str = Field(min_length=1, max_length=64)         # имя API-приложения в phpIPAM
    username: str = Field(min_length=1, max_length=64)    # пользователь phpIPAM (Basic-auth)
    password: str = Field(min_length=1, max_length=200)   # пароль (только для получения токена)
    import_ips: bool = False
    insecure: bool = False  # самоподписанный сертификат phpIPAM — не проверять SSL

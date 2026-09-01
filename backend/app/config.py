from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "sqlite+aiosqlite:///./ipam.db"
    SECRET_KEY: str = "dev-secret-change-me-0123456789abcdef"  # >= 32 байта, сменить в проде
    TOKEN_TTL_MIN: int = 1440  # 24 ч

    # Первый админинистратор (создаётся, только если таблица users пуста)
    ADMIN_USER: str = "admin"
    ADMIN_PASSWORD: str = "admin"
    SEED_DEMO: bool = False  # демо-данные: 2 VLAN, 3 сети

    # DNS для PTR-резолва (через запятую); пусто = системный резолвер + /etc/hosts
    DNS_SERVERS: str = ""

    # Сканер
    SCAN_CONCURRENCY: int = 8          # сколько сетей сканируем одновременно
    SCAN_TIMEOUT_MS: int = 500         # таймаут "живости"
    SCAN_RATE: int = 500               # ограничение pings/sec (fping -i)
    PROBE_PORTS: str = "22,80,443,3389"  # fallback TCP-проба, если нет fping
    FREE_AFTER_MISSES: int = 2         # used -> offline после N промахов подряд

    # DNS
    RESOLVE_DNS: bool = True           # разрешать hostname (PTR) при скане

    # Документация: каталог для загруженных файлов (по умолчанию <проект>/docs_files)
    DOCS_DIR: str = ""
    DOCS_MAX_UPLOAD_MB: int = 50       # лимит одного файла

    # Почта (уведомления о сканах); всё можно переопределить в UI -> Настройки
    MAIL_ENABLED: bool = False
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_STARTTLS: bool = True
    MAIL_FROM: str = ""
    MAIL_TO: str = ""

    @property
    def dns_servers(self) -> list[str]:
        return [s.strip() for s in self.DNS_SERVERS.split(",") if s.strip()]

    @property
    def probe_ports(self) -> list[int]:
        return [int(p) for p in self.PROBE_PORTS.split(",") if p.strip().isdigit()]


settings = Settings()

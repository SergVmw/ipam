from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    connect_args={"timeout": 30} if settings.DATABASE_URL.startswith("sqlite") else {},
)

# SQLite по умолчанию НЕ включает внешние ключи — включаем на каждом подключении,
# чтобы ondelete="CASCADE" (ip→subnet, agent→reports, …) реально работал
if settings.DATABASE_URL.startswith("sqlite"):
    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_fk_on(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    async with SessionLocal() as session:
        yield session


async def migrate_db() -> None:
    """Малые ручные миграции для уже созданных БД (idempotent)."""
    from sqlalchemy import text

    if settings.DATABASE_URL.startswith("postgresql"):
        async with engine.begin() as conn:
            # ip.ip_int: integer -> bigint (IPv4 как число до 2^32 не помещается в INT4)
            data_type = (await conn.execute(text(
                "select data_type from information_schema.columns where table_name = 'ip' and column_name = 'ip_int'"
            ))).scalar()
            if data_type == "integer":
                await conn.execute(text("alter table ip alter column ip_int type bigint"))
                print("[migrate] ip.ip_int: integer -> bigint")
            has_sm = (await conn.execute(text(
                "select 1 from information_schema.columns where table_name = 'subnet' and column_name = 'scan_method'"
            ))).scalar()
            if has_sm is None:
                await conn.execute(text("alter table subnet add column scan_method varchar(16)"))
                print("[migrate] subnet.scan_method добавлена")
            has_prov = (await conn.execute(text(
                "select 1 from information_schema.columns where table_name = 'user' and column_name = 'provider'"
            ))).scalar()
            if has_prov is None:
                # "user" — зарезервированное слово postgres: только в кавычках
                await conn.execute(text('alter table "user" add column provider varchar(16) default \'local\' not null'))
                print("[migrate] user.provider добавлена")
            has_dn = (await conn.execute(text(
                "select 1 from information_schema.columns where table_name = 'user' and column_name = 'display_name'"
            ))).scalar()
            if has_dn is None:
                await conn.execute(text('alter table "user" add column display_name varchar(128)'))
                print("[migrate] user.display_name добавлена")
            has_tags = (await conn.execute(text(
                "select 1 from information_schema.columns where table_name = 'subnet' and column_name = 'tags'"
            ))).scalar()
            if has_tags is None:
                await conn.execute(text("alter table subnet add column tags varchar(255)"))
                print("[migrate] subnet.tags добавлена")
            has_mv = (await conn.execute(text(
                "select 1 from information_schema.columns where table_name = 'ip' and column_name = 'mac_vendor'"
            ))).scalar()
            if has_mv is None:
                await conn.execute(text("alter table ip add column mac_vendor varchar(64)"))
                print("[migrate] ip.mac_vendor добавлена")
            for col, ddl in (
                ("ssh_host", "alter table agent add column ssh_host varchar(128)"),
                ("ssh_port", "alter table agent add column ssh_port integer"),
                ("ssh_user", "alter table agent add column ssh_user varchar(64)"),
                ("ssh_password", "alter table agent add column ssh_password varchar(255)"),
                ("poll_file", "alter table agent add column poll_file varchar(255)"),
                ("last_install_at", "alter table agent add column last_install_at timestamp"),
                ("install_log", "alter table agent add column install_log text"),
                ("report_interval_min", "alter table agent add column report_interval_min integer"),
            ):
                has = (await conn.execute(text(
                    "select 1 from information_schema.columns where table_name = 'agent' and column_name = :c"
                ), {"c": col})).scalar()
                if has is None:
                    await conn.execute(text(ddl))
                    print(f"[migrate] agent.{col} добавлена")
            df_exists = (await conn.execute(text(
                "select 1 from information_schema.tables where table_name = 'doc_file'"
            ))).scalar()
            if df_exists:
                has_dfsec = (await conn.execute(text(
                    "select 1 from information_schema.columns where table_name = 'doc_file' and column_name = 'section_id'"
                ))).scalar()
                if has_dfsec is None:
                    await conn.execute(text("alter table doc_file alter column page_id drop not null"))
                    await conn.execute(text("alter table doc_file add column section_id integer"))
                    await conn.execute(text(
                        "alter table doc_file add constraint df_section_fk "
                        "foreign key (section_id) references doc_section(id) on delete cascade"
                    ))
                    await conn.execute(text("create index ix_doc_file_section_id on doc_file(section_id)"))
                    print("[migrate] doc_file: section_id добавлена, page_id nullable")
            has_vtags = (await conn.execute(text(
                "select 1 from information_schema.columns where table_name = 'vlan' and column_name = 'tags'"
            ))).scalar()
            if has_vtags is None:
                await conn.execute(text("alter table vlan add column tags varchar(255)"))
                print("[migrate] vlan.tags добавлена")
            fl_exists = (await conn.execute(text(
                "select 1 from information_schema.tables where table_name = 'fiber_link'"
            ))).scalar()
            if fl_exists:
                flcols = {r[0] for r in (await conn.execute(text(
                    "select column_name from information_schema.columns where table_name = 'fiber_link'"
                ))).fetchall()}
                if "fibers" not in flcols:
                    await conn.execute(text("alter table fiber_link add column fibers integer"))
                    print("[migrate] fiber_link.fibers добавлена")
                if "fiber_usage" not in flcols:
                    await conn.execute(text("alter table fiber_link add column fiber_usage text"))
                    print("[migrate] fiber_link.fiber_usage добавлена")
                if "length" not in flcols:
                    await conn.execute(text("alter table fiber_link add column length double precision"))
                    print("[migrate] fiber_link.length добавлена")
                if "route" not in flcols:
                    await conn.execute(text("alter table fiber_link add column route text"))
                    print("[migrate] fiber_link.route добавлена (промежуточные точки)")
            loc_exists = (await conn.execute(text(
                "select 1 from information_schema.tables where table_name = 'location'"
            ))).scalar()
            if loc_exists:
                loccols = {r[0] for r in (await conn.execute(text(
                    "select column_name from information_schema.columns where table_name = 'location'"
                ))).fetchall()}
                if "is_transit" not in loccols:
                    await conn.execute(text(
                        "alter table location add column is_transit boolean default false not null"
                    ))
                    print("[migrate] location.is_transit добавлена (промежуточная точка)")
    elif settings.DATABASE_URL.startswith("sqlite"):
        async with engine.begin() as conn:
            cols = [r[1] for r in (await conn.execute(text("pragma table_info(subnet)"))).fetchall()]
            if "scan_method" not in cols:
                await conn.execute(text("alter table subnet add column scan_method varchar(16)"))
                print("[migrate] subnet.scan_method добавлена (sqlite)")
            ucols = [r[1] for r in (await conn.execute(text("pragma table_info(user)"))).fetchall()]
            if "provider" not in ucols:
                await conn.execute(text('alter table "user" add column provider varchar(16) default \'local\' not null'))
                print("[migrate] user.provider добавлена (sqlite)")
            if "display_name" not in ucols:
                await conn.execute(text('alter table "user" add column display_name varchar(128)'))
                print("[migrate] user.display_name добавлена (sqlite)")
            scols = [r[1] for r in (await conn.execute(text("pragma table_info(subnet)"))).fetchall()]
            if "tags" not in scols:
                await conn.execute(text("alter table subnet add column tags varchar(255)"))
                print("[migrate] subnet.tags добавлена (sqlite)")
            ipcols = [r[1] for r in (await conn.execute(text("pragma table_info(ip)"))).fetchall()]
            if "mac_vendor" not in ipcols:
                await conn.execute(text("alter table ip add column mac_vendor varchar(64)"))
                print("[migrate] ip.mac_vendor добавлена (sqlite)")
            acols = [r[1] for r in (await conn.execute(text("pragma table_info(agent)"))).fetchall()]
            for col, decl in (
                ("ssh_host", "varchar(128)"), ("ssh_port", "integer"),
                ("ssh_user", "varchar(64)"), ("ssh_password", "varchar(255)"),
                ("poll_file", "varchar(255)"),
                ("last_install_at", "timestamp"), ("install_log", "text"),
                ("report_interval_min", "integer"),
            ):
                if col not in acols:
                    await conn.execute(text(f"alter table agent add column {col} {decl}"))
                    print(f"[migrate] agent.{col} добавлена (sqlite)")
            df_exists = (await conn.execute(text(
                "select 1 from sqlite_master where type='table' and name='doc_file'"
            ))).fetchone()
            if df_exists:
                dfcols = [r[1] for r in (await conn.execute(text("pragma table_info(doc_file)"))).fetchall()]
                if "section_id" not in dfcols:
                    # sqlite: страница→раздел, page_id NOT NULL → nullable: пересобираем таблицу
                    await conn.execute(text("""
                        create table doc_file_new (
                            id integer primary key,
                            page_id integer references doc_page(id) on delete cascade,
                            section_id integer references doc_section(id) on delete cascade,
                            name varchar(255) not null,
                            stored varchar(64) not null unique,
                            size integer not null default 0,
                            mime varchar(128),
                            uploaded_at timestamp
                        )"""))
                    await conn.execute(text(
                        "insert into doc_file_new (id, page_id, section_id, name, stored, size, mime, uploaded_at) "
                        "select id, page_id, null, name, stored, size, mime, uploaded_at from doc_file"
                    ))
                    await conn.execute(text("drop table doc_file"))
                    await conn.execute(text("alter table doc_file_new rename to doc_file"))
                    await conn.execute(text("create index ix_doc_file_page_id on doc_file(page_id)"))
                    await conn.execute(text("create index ix_doc_file_section_id on doc_file(section_id)"))
                    print("[migrate] doc_file: section_id добавлена, page_id nullable (sqlite)")
            vcols = [r[1] for r in (await conn.execute(text("pragma table_info(vlan)"))).fetchall()]
            if "tags" not in vcols:
                await conn.execute(text("alter table vlan add column tags varchar(255)"))
                print("[migrate] vlan.tags добавлена (sqlite)")
            fl_exists = (await conn.execute(text(
                "select 1 from sqlite_master where type='table' and name='fiber_link'"
            ))).fetchone()
            if fl_exists:
                flcols = [r[1] for r in (await conn.execute(text("pragma table_info(fiber_link)"))).fetchall()]
                if "fibers" not in flcols:
                    await conn.execute(text("alter table fiber_link add column fibers integer"))
                    print("[migrate] fiber_link.fibers добавлена (sqlite)")
                if "fiber_usage" not in flcols:
                    await conn.execute(text("alter table fiber_link add column fiber_usage text"))
                    print("[migrate] fiber_link.fiber_usage добавлена (sqlite)")
                if "length" not in flcols:
                    await conn.execute(text("alter table fiber_link add column length float"))
                    print("[migrate] fiber_link.length добавлена (sqlite)")
                if "route" not in flcols:
                    await conn.execute(text("alter table fiber_link add column route text"))
                    print("[migrate] fiber_link.route добавлена (sqlite, промежуточные точки)")
            loc_exists = (await conn.execute(text(
                "select 1 from sqlite_master where type='table' and name='location'"
            ))).fetchone()
            if loc_exists:
                loccols = [r[1] for r in (await conn.execute(text("pragma table_info(location)"))).fetchall()]
                if "is_transit" not in loccols:
                    await conn.execute(text(
                        "alter table location add column is_transit boolean default 0 not null"
                    ))
                    print("[migrate] location.is_transit добавлена (sqlite, промежуточная точка)")


async def init_db() -> None:
    from . import models  # noqa: F401  (регистрация моделей)

    # Сначала создаём отсутствующие таблицы, потом доносим колонки в существующие
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await migrate_db()

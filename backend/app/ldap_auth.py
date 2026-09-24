"""Проверка пароля в домене AD/LDAP. Локальные пользователи сюда не ходят.

Возврат: (ok, error, info) где info = {"display_name": ..., "mail": ...} (или None).
"""
import logging

from ldap3 import BASE, SIMPLE, SUBTREE, Connection, Server
from ldap3.utils.conv import escape_filter_chars

log = logging.getLogger("ipam.ldap")

_NAME_ATTRS = ["displayName", "givenName", "sn", "mail"]


def _connect(server, user, password):
    """Создаём Connection с таймаутом; на старых ldap3 без receive_timeout — без него."""
    try:
        return Connection(server, user=user, password=password,
                          authentication=SIMPLE, receive_timeout=10)
    except TypeError:
        return Connection(server, user=user, password=password, authentication=SIMPLE)


def _bind(server, user, password) -> bool:
    conn = _connect(server, user, password)
    try:
        return bool(conn.bind())
    finally:
        try:
            conn.unbind()
        except Exception:
            pass


def _entry_name(entry) -> tuple[str | None, str | None]:
    display = str(entry["displayName"]) if "displayName" in entry else None
    given = str(entry["givenName"]) if "givenName" in entry else ""
    sn = str(entry["sn"]) if "sn" in entry else ""
    if not display:
        full = " ".join(x for x in (given, sn) if x)
        display = full or None
    mail = str(entry["mail"]) if "mail" in entry else None
    return display, mail


def _self_info(server, dn: str, password: str) -> dict:
    """После успешного bind читаем свои атрибуты (имя, фамилию, почту)."""
    conn = _connect(server, dn, password)
    try:
        if conn.bind() and conn.search(base_dn=dn, search_filter="(objectClass=*)",
                                       search_scope=BASE, attributes=_NAME_ATTRS):
            display, mail = _entry_name(conn.entries[0])
            return {"display_name": display, "mail": mail}
    except Exception:
        pass
    finally:
        try:
            conn.unbind()
        except Exception:
            pass
    return {}


def ldap_auth(rt: dict, username: str, password: str) -> tuple[bool, str, dict | None]:
    """Блокирующий — вызывать через asyncio.to_thread."""
    if not rt.get("ldap_enabled"):
        return False, "Вход через домен выключен", None
    url = (rt.get("ldap_url") or "").strip()
    if not url:
        return False, "Домен не настроен (ldap_url)", None
    try:
        server = Server(url, use_ssl=url.lower().startswith("ldaps://"), get_info=None)

        # 1) быстрый путь: прямой bind по шаблону DN
        template = (rt.get("ldap_user_dn_template") or "").strip()
        if template and "{username}" in template:
            dn = template.replace("{username}", username)
            if "=" in dn and _bind(server, dn, password):
                return True, "", _self_info(server, dn, password)

        # 2) поиск пользователя (служебная учётка) и bind от его имени
        bind_dn = (rt.get("ldap_bind_dn") or "").strip()
        if not bind_dn:
            return False, "не удалось проверить пароль (задайте ldap_user_dn_template или ldap_bind_dn)", None
        if not _bind(server, bind_dn, rt.get("ldap_bind_password") or ""):
            return False, "не удалось войти под служебной учёткой (ldap_bind_dn/пароль)", None
        flt = (rt.get("ldap_search_filter") or "(sAMAccountName={username})").replace(
            "{username}", escape_filter_chars(username))
        base = (rt.get("ldap_base_dn") or "").strip()
        conn = _connect(server, bind_dn, rt.get("ldap_bind_password") or "")
        found_dn = None
        display = mail = None
        try:
            if conn.bind() and conn.search(base, flt, search_scope=SUBTREE, attributes=_NAME_ATTRS):
                found_dn = conn.entries[0].entry_dn
                display, mail = _entry_name(conn.entries[0])
        finally:
            try:
                conn.unbind()
            except Exception:
                pass
        if not found_dn:
            return False, f"пользователь {username} не найден в домене", None
        if _bind(server, found_dn, password):
            return True, "", {"display_name": display, "mail": mail}
        return False, "неверный доменный пароль", None
    except Exception as e:
        log.warning("LDAP ошибка: %s", e)
        return False, f"LDAP ошибка: {e}", None

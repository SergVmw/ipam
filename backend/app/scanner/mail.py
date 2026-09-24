"""Почтовые уведомления о сканах (SMTP). Никогда не роняет скан — ошибки только в лог."""
import asyncio
import logging
import smtplib
from email.message import EmailMessage

log = logging.getLogger("ipam.mail")


def _send_sync(d: dict, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = d.get("mail_from") or d.get("smtp_user") or "ipam@localhost"
    msg["To"] = ", ".join(t.strip() for t in str(d.get("mail_to", "")).split(",") if t.strip())
    msg["Subject"] = subject
    msg.set_content(body)
    host = d.get("smtp_host")
    port = int(d.get("smtp_port") or 587)
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=15)
    else:
        server = smtplib.SMTP(host, port, timeout=15)
    if d.get("smtp_starttls") and port != 465:
        server.starttls()
    if d.get("smtp_user"):
        server.login(d["smtp_user"], d.get("smtp_password") or "")
    try:
        server.send_message(msg)
    finally:
        server.quit()


async def send_mail(d: dict, subject: str, body: str) -> tuple[bool, str]:
    if not str(d.get("mail_to", "")).strip():
        return False, "не указаны получатели (mail_to)"
    if not d.get("smtp_host"):
        return False, "не указан SMTP-сервер (smtp_host)"
    try:
        await asyncio.wait_for(asyncio.to_thread(_send_sync, d, subject, body), timeout=30)
        return True, ""
    except Exception as e:
        log.warning("не удалось отправить письмо: %s", e)
        return False, str(e)


async def send_test_mail(d: dict) -> tuple[bool, str]:
    return await send_mail(d, "IPAM: тест SMTP", "Тестовое письмо из IPAM. Почтовые настройки работают.")


def scan_mail_body(cidr: str, name: str, alive: int, new_ips: list[dict], freed: list[str], reserved_alive: list[str]) -> str:
    lines = [f"Сеть {name} ({cidr})", f"Живых хостов: {alive}", ""]
    if new_ips:
        lines.append(f"Новые IP ({len(new_ips)}):")
        for ip in new_ips[:50]:
            extra = "  ".join(x for x in (ip.get("hostname"), ip.get("owner")) if x)
            lines.append(f"  {ip['ip']}" + (f"  {extra}" if extra else ""))
    if freed:
        lines.append(f"Освобождено ({len(freed)}):")
        for ip in freed[:50]:
            lines.append(f"  {ip}")
    if reserved_alive:
        lines.append(f"Зарезервировано, но живо ({len(reserved_alive)}):")
        for ip in reserved_alive[:50]:
            lines.append(f"  {ip}")
    if not (new_ips or freed or reserved_alive):
        lines.append("Изменений нет.")
    return "\n".join(lines)


async def send_scan_mail(d: dict, cidr: str, name: str, alive: int,
                         new_ips: list[dict], freed: list[str], reserved_alive: list[str]) -> None:
    subject = f"IPAM: {name} {cidr} — новых {len(new_ips)}, освобождено {len(freed)}"
    await send_mail(d, subject, scan_mail_body(cidr, name, alive, new_ips, freed, reserved_alive))

"""Удалённая установка агента по SSH.

Последовательность:
  SSH-подключение → ОЧИСТКА старого агента (скрипт/env/cfg/состояние) →
  определение ОС → проверка curl/nmap/iproute2/cron →
  проверка интернета по репозиториям дистрибутива → установка недостающего →
  деплой /opt/ipam_agent.sh + /etc/ipam_agent.env (только URL + ключ;
  сети агент забирает с ядра с /api/agent/config) →
  cron (отчёт каждые 5 мин, идемпотентно) → запуск cron-сервиса → ручной тест.

Возврат: список шагов [{"step", "ok", "detail"}].
Блокирующий — вызывать через asyncio.to_thread.
"""
import logging
import re

log = logging.getLogger("ipam.installer")

SCRIPT_REMOTE = "/opt/ipam_agent.sh"
ENV_REMOTE = "/etc/ipam_agent.env"
CRON_MARKER = "ipam_agent"


def _local_script_path() -> str:
    from pathlib import Path
    for p in (
        Path("/agent/ipam_agent.sh"),  # docker-образ
        Path(__file__).resolve().parent.parent.parent / "agent" / "ipam_agent.sh",  # dev
    ):
        if p.is_file():
            return str(p)
    raise RuntimeError("не найден ipam_agent.sh (скрипт агента) в ядре")


def _run(ssh, cmd, timeout=60):
    _in, stdout, _err = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode(errors="ignore").strip()
    err = _err.read().decode(errors="ignore").strip()
    rc = stdout.channel.recv_exit_status()
    return rc, out, err


def _distro_actions(distro: str):
    if distro == "alpine":
        return {
            "update": "timeout 30 apk update",
            "install": "apk add --no-cache curl nmap iproute2 dcron",
            "cron_start": "rc-update add crond default 2>/dev/null; rc-service crond start 2>/dev/null || service crond start 2>/dev/null || true",
        }
    if distro == "debian":
        return {
            "update": "timeout 90 apt-get update -qq",
            "install": "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl nmap iproute2 cron",
            "cron_start": "systemctl enable cron 2>/dev/null; systemctl start cron 2>/dev/null || service cron start 2>/dev/null || true",
        }
    if distro == "rhel":
        return {
            "update": "timeout 90 bash -c 'dnf makecache -y 2>/dev/null || yum makecache'",
            "install": "bash -c 'dnf install -y curl nmap iproute cronie 2>/dev/null || yum install -y curl nmap iproute cronie'",
            "cron_start": "systemctl enable --now crond 2>/dev/null || service crond start 2>/dev/null || true",
        }
    return None


def install_agent(host, port, user, password, ipam_url, agent_key) -> list:
    # NOTE: сети НЕ передаются в .env — агент забирает список сетей с ядра
    # (GET /api/agent/config по ключу). Единый источник сетей = ядро.
    steps = []

    def step(name, ok, detail=""):
        steps.append({"step": name, "ok": bool(ok), "detail": str(detail)[:400]})
        log.info("installer %s: %s %s", name, "OK" if ok else "FAIL", str(detail)[:160])

    import paramiko

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(host, port=port or 22, username=user, password=password or "",
                    timeout=20, banner_timeout=20, auth_timeout=20,
                    allow_agent=False, look_for_keys=False)
        step("SSH-подключение", True, f"{user}@{host}:{port or 22}")
    except Exception as e:
        step("SSH-подключение", False, str(e))
        return steps

    try:
        # --- вычистить старое состояние агента ПЕРЕД установкой
        _rc, _out, werr = _run(ssh,
            "rm -f /opt/ipam_agent.sh /etc/ipam_agent.env /etc/ipam_agent.cfg; "
            "rm -rf /var/lib/ipam_agent; mkdir -p /var/lib/ipam_agent; echo OK")
        step("Очистка старого агента", _rc == 0, werr or "старый скрипт/env/состояние удалены")

        # --- ОС
        _rc, out, _ = _run(ssh, "cat /etc/os-release 2>/dev/null")
        m_id = re.search(r'^ID="?([A-Za-z0-9_]+)"?', out, re.M)
        m_ver = re.search(r'^VERSION_ID="?([^"\n]+?)"?\s*$', out, re.M)
        m_pretty = re.search(r'^PRETTY_NAME="?([^"\n]+?)"?\s*$', out, re.M)
        oid = m_id.group(1).lower() if m_id else "unknown"
        distro = ("alpine" if "alpine" in oid
                  else "debian" if oid in ("debian", "ubuntu")
                  else "rhel" if oid in ("rhel", "centos", "rocky", "almalinux", "fedora", "astra")
                  else oid)
        os_name = (m_pretty.group(1) if m_pretty else f"{oid} {m_ver.group(1)}".strip())
        step("ОС", True, os_name)
        acts = _distro_actions(distro)

        # --- компоненты
        _rc, out, _ = _run(ssh, "command -v curl >/dev/null 2>&1 && curl --version 2>/dev/null | head -1 || echo MISSING")
        need_curl = out.startswith("MISSING")
        step("curl", not need_curl, out if not need_curl else "не найден — нужен для отправки отчёта")
        _rc, out, _ = _run(ssh, "command -v nmap >/dev/null 2>&1 && nmap --version 2>/dev/null | head -1 || echo MISSING")
        have_nmap = not out.startswith("MISSING")
        step("nmap", have_nmap, out if have_nmap else "не найден — без него не будет MAC (будет только ARP-таблица)")
        _rc, out, _ = _run(ssh, "command -v ip >/dev/null 2>&1 && echo OK || echo MISSING")
        have_ip = out == "OK"
        step("iproute2", have_ip, "установлен" if have_ip else "не найден (нужен для ARP-таблицы)")
        _rc, out, _ = _run(ssh, "command -v crontab >/dev/null 2>&1 && echo OK || echo MISSING")
        have_cron = out == "OK"
        step("cron", have_cron, "установлен" if have_cron else "не найден")

        if not acts:
            step("Пакетный менеджер", False,
                 f"дистрибут '{oid}' не распознан — установите вручную: curl, nmap, iproute2, cron")
        elif need_curl or not have_nmap or not have_ip or not have_cron:
            # --- интернет / репозитории
            rc, out, err = _run(ssh, acts["update"], timeout=120)
            step("Интернет / репозитории", rc == 0, (err or out)[-200:] or "доступны")
            if rc == 0:
                rc, out, err = _run(ssh, acts["install"], timeout=900)
                step("Установка компонентов", rc == 0, (err or out)[-200:] or "OK")
                if rc == 0:
                    need_curl = False
                    have_nmap = have_ip = have_cron = True
            if rc != 0 and (need_curl or not have_nmap or not have_ip or not have_cron):
                step("Установка компонентов", False,
                     "интернет/репозитории недоступны — компоненты не установлены; установите вручную и повторите")
        else:
            step("Установка компонентов", True, "установить нечего — всё на месте")

        # --- деплой скрипта + env
        # NOTE: в .env НЕ пишем NETWORKS — единый источник сетей = конфиг ядра
        # (агент забирает его с /api/agent/config). В .env только URL + ключ.
        sftp = ssh.open_sftp()
        sftp.put(_local_script_path(), SCRIPT_REMOTE)
        sftp.chmod(SCRIPT_REMOTE, 0o755)
        with sftp.open(ENV_REMOTE, "w") as f:
            f.write(f"export IPAM_URL={ipam_url}\nexport IPAM_AGENT_KEY={agent_key}\n")
        sftp.chmod(ENV_REMOTE, 0o600)
        sftp.close()
        step("Деплой скрипта", True, f"{SCRIPT_REMOTE} + {ENV_REMOTE} (URL, ключ)")

        # --- права: SFTP-chmod не всегда приживается (монтированные ФС, umask,
        # каталоги с noexec-аналогами) — проверяем РЕАЛЬНУЮ исполняемость,
        # а не то, что «chmod отработал»
        _rc, out, err = _run(ssh,
            f"m=$(stat -c %a {ENV_REMOTE} 2>/dev/null || echo '?'); "
            f"if [ -x {SCRIPT_REMOTE} ]; then echo \"OK $m\"; else echo \"NO_EXEC $m\"; fi")
        if _rc == 0 and out.startswith("OK"):
            step("Права (chmod +x)", True,
                 f"{SCRIPT_REMOTE} исполняемый; {ENV_REMOTE} режим {out.split()[1]}")
        else:
            _rc2, out2, err2 = _run(ssh,
                f"chmod 755 {SCRIPT_REMOTE} 2>&1; chmod 600 {ENV_REMOTE} 2>&1; "
                f"m=$(stat -c %a {ENV_REMOTE} 2>/dev/null || echo '?'); "
                f"if [ -x {SCRIPT_REMOTE} ]; then echo \"OK $m\"; else echo \"NO_EXEC $m\"; fi")
            step("Права (chmod +x)", _rc2 == 0 and out2.startswith("OK"),
                 "SFTP-права потерялись — выставил 755/600 повторно, теперь исполняемый"
                 if out2.startswith("OK") else
                 f"скрипт не стал исполняемым даже после chmod: {err2 or err or out2}")

        # --- cron: часовой отчёт, идемпотентно, через временный файл
        # (без bash-обёрток: на Alpine нет bash, только busybox sh)
        cron_line = f"*/5 * * * * . {ENV_REMOTE} && {SCRIPT_REMOTE} >> /var/log/ipam_agent.log 2>&1"
        _rc, old_out, _ = _run(ssh, "crontab -l 2>/dev/null")
        old_lines = [l for l in old_out.splitlines() if l.strip() and CRON_MARKER not in l] if _rc == 0 else []
        old_lines.append(cron_line)
        sftp = ssh.open_sftp()
        with sftp.open("/tmp/ipam_crontab.new", "w") as f:
            f.write("\n".join(old_lines) + "\n")
        sftp.close()
        rc, out, err = _run(ssh, "crontab /tmp/ipam_crontab.new && rm -f /tmp/ipam_crontab.new")
        step("Cron (отчёт каждые 5 мин)", rc == 0, err or "строка cron добавлена")
        if acts:
            rc, out, err = _run(ssh, acts["cron_start"], timeout=60)
            step("Cron-сервис", rc == 0, (err or out)[-120:] or "запущен")

        # --- ручной тест: вывод в /var/log/ipam_agent.log ТАК ЖЕ, как у cron
        # (иначе «отчёт 16:25» из ядра не будет отражён в логе агента),
        # rc самого скрипта сохраняем (без pipe), хвост лога — в лог установки
        rc, out, err = _run(ssh,
            f"sh -c '. {ENV_REMOTE} && timeout 300 {SCRIPT_REMOTE} >> /var/log/ipam_agent.log 2>&1; "
            f"rc=$?; tail -5 /var/log/ipam_agent.log 2>/dev/null; exit $rc' 2>&1",
            timeout=360)
        detail = (out or err or "").strip()
        step("Ручной запуск", rc == 0, detail[-300:] or ("OK" if rc == 0 else "не удалось выполнить"))
    except Exception as e:
        step("Ошибка", False, str(e))
    finally:
        ssh.close()
    return steps

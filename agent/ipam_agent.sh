#!/bin/sh
# IPAM-агент: отчёт о живых хостах в ядро (cron — рекомендуется каждые 5 минут;
# скрипт сам троттлится: повторный отчёт не чаще 30 минут, если нет принудительного
# опроса из ядра).
#
# POSIX sh — работает в bash/ash/dash (включая busybox на Alpine).
# Зависимости: curl + (nmap для активного обхода и/или iproute2 для ARP-таблицы).
# Python НЕ нужен.
#
# Конфигурация (env, см. /etc/ipam_agent.env):
#   IPAM_URL        адрес ядра, напр. https://ipam.corp.local:8000
#   IPAM_AGENT_KEY  ключ агента (ядро: Настройки -> Агенты)
#   SOURCE          nmap | arp | auto (по умолчанию auto: nmap, если есть, иначе ARP-таблица)
#   TIMEOUT         секунд на запрос к ядру (по умолчанию 30)
#
# Сети: ЕДИНЫЙ ИСТОЧНИК — конфиг ядра. Агент забирает его с /api/agent/config
# перед каждым запуском и кэширует в /etc/ipam_agent.cfg. Список сетей меняется
# в ядре (Настройки -> Агенты) и прилетает на агента автоматически.
# (Статическая NETWORKS в env больше не используется — установщик её не пишет.)
#
# Что шлётся: {"hosts": [{"ip":"10.0.0.5","mac":"aa:bb:..","vendor":"..."}, ...]}
# POST /api/agent/report, заголовок X-Agent-Key.
#
# Принудительный опрос: в ядре «Настройки -> Агенты -> Принудительный опрос»
# ядро по SSH трогает файл-триггер (по умолчанию /var/lib/ipam_agent/force_poll);
# на ближайшем cron-цикле агент видит триггер и сканирует немедленно.

IPAM_URL="${IPAM_URL:-}"
IPAM_AGENT_KEY="${IPAM_AGENT_KEY:-}"
SOURCE="${SOURCE:-auto}"
TIMEOUT="${TIMEOUT:-30}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ipam-agent] $*"; }

[ -n "$IPAM_URL" ] || { log "нужен IPAM_URL"; exit 2; }
[ -n "$IPAM_AGENT_KEY" ] || { log "нужен IPAM_AGENT_KEY"; exit 2; }
command -v curl >/dev/null 2>&1 || { log "не найден curl — установите (apk add curl / apt install curl / yum install curl)"; exit 2; }

# --- сети локальных интерфейсов: "169.254.0.20/30 ..." (точные CIDR из таблицы маршрутов)
local_networks() {
    if command -v ip >/dev/null 2>&1; then
        nets=$(ip route show 2>/dev/null | awk '
            / scope link / && $1 ~ /^[0-9.]+\/[0-9]+$/ {
                split($1, a, "/");
                if (a[2] >= 8 && a[2] <= 30) print $1;
            }' | awk '!seen[$0]++')
        if [ -n "$nets" ]; then
            echo "$nets"
            return
        fi
    fi
    # fallback: первый внешний адрес -> /24
    ip1=$(hostname -I 2>/dev/null | awk '{print $1}')
    [ -n "$ip1" ] && echo "$ip1" | awk -F. '{print $1"."$2"."$3".0/24"}'
}

# --- сбор: строки "IP\tMAC\tVENDOR" (MAC/VENDOR могут отсутствовать)
collect_nmap() {
    # $1 = cidr. Обычный вывод nmap (НЕ -oG: в -oG нет MAC Address).
    if command -v timeout >/dev/null 2>&1; then
        timeout 300 nmap -sn -n --no-stylesheet --max-retries 1 "$1" 2>/dev/null
    else
        nmap -sn -n --no-stylesheet --max-retries 1 "$1" 2>/dev/null
    fi | awk '
        function flush() {
            if (up && ip != "") {
                if (mac != "") printf "%s\t%s\t%s\n", ip, mac, vendor;
                else printf "%s\t\t%s\n", ip, vendor;
            }
            ip = ""; up = 0; mac = ""; vendor = ""
        }
        /^Nmap scan report for / {
            flush();
            spec = $NF;
            if (spec ~ /^\(/) spec = substr(spec, 2, length(spec) - 2);
            ip = spec;
            next
        }
        /^Host is up/   { up = 1 }
        /^Host is down/ { up = 0; ip = "" }
        /^MAC Address:/ {
            if (up && $3 ~ /^[0-9a-fA-F:]{17}$/) {
                mac = tolower($3);
                vendor = "";
                for (i = 4; i <= NF; i++) vendor = vendor (i > 4 ? " " : "") $i;
                sub(/^\(/, "", vendor); sub(/\)$/, "", vendor);
            }
        }
        END { flush() }
    '
}

collect_arp() {
    # ARP-таблица ОС: только живые записи с реальным MAC (глобально, по всем интерфейсам)
    if command -v ip >/dev/null 2>&1; then
        ip -o neigh 2>/dev/null | awk '
            $4 == "lladdr" && $5 ~ /^[0-9a-fA-F:]{17}$/ {
                mac = tolower($5);
                if (mac != "00:00:00:00:00:00") printf "%s\t%s\t\n", $1, mac;
            }'
        return
    fi
    arp -a 2>/dev/null | awk '
        $3 == "at" && $4 ~ /^[0-9a-fA-F:]{17}$/ {
            mac = tolower($4);
            ip = $2; gsub(/[()]/, "", ip);
            if (mac != "00:00:00:00:00:00") printf "%s\t%s\t\n", ip, mac;
        }'
}

CONFIG_FILE="${CONFIG_FILE:-/etc/ipam_agent.cfg}"
LAST_REPORT_FILE="${LAST_REPORT_FILE:-/var/lib/ipam_agent/last_report}"
POLL_FILE_DEFAULT="/var/lib/ipam_agent/force_poll"

# --- TLS: самоподписанный сертификат ядра -> -k, либо IPAM_CA_FILE (путь к сертификату ядра)
CURL_TLS=""
case "$IPAM_URL" in
    https://*)
        if [ -n "${IPAM_CA_FILE:-}" ] && [ -f "$IPAM_CA_FILE" ]; then
            CURL_TLS="--cacert $IPAM_CA_FILE"
        else
            CURL_TLS="-k"
        fi
        ;;
esac

# --- конфиг с ядра: изменения настроек агента в ядре автоматически прилетают сюда
CFG_NETS=""
CFG_POLL_FILE=""
CFG_JSON=$(curl -fsS $CURL_TLS -m "$TIMEOUT" -H "X-Agent-Key: $IPAM_AGENT_KEY" \
    "$IPAM_URL/api/agent/config" 2>/dev/null) || CFG_JSON=""
if [ -n "$CFG_JSON" ]; then
    CFG_NETS=$(printf '%s' "$CFG_JSON" | sed -n 's/.*"networks"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
    CFG_POLL_FILE=$(printf '%s' "$CFG_JSON" | sed -n 's/.*"poll_file"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
    printf '%s' "$CFG_JSON" > "$CONFIG_FILE" 2>/dev/null || true
    log "конфиг получен с ядра (сети: ${CFG_NETS:-авто})"
fi
# запасной конфиг из локального файла, если ядро недоступно
if [ -z "$CFG_NETS" ] && [ -f "$CONFIG_FILE" ]; then
    CFG_NETS=$(sed -n 's/.*"networks"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$CONFIG_FILE" | head -1)
fi

# --- принудительный опрос: ядро через SSH трогает файл-триггер
POLL_FILE="${CFG_POLL_FILE:-$POLL_FILE_DEFAULT}"
FORCED=0
if [ -f "$POLL_FILE" ]; then
    NOW=$(date +%s)
    TS=$(stat -c %Y "$POLL_FILE" 2>/dev/null || stat -f %m "$POLL_FILE" 2>/dev/null || echo 0)
    case "$TS" in (*[!0-9]*|'') TS=0 ;; esac
    if [ "$TS" != "0" ] && [ $((NOW - TS)) -lt 1800 ]; then
        FORCED=1
        log "принудительный опрос (триггер с ядра)"
    fi
    rm -f "$POLL_FILE" 2>/dev/null || true
fi

# --- троттлинг: если свежий отчёт уже ушёл и нет принудительного триггера — цикл пропускаем
if [ "$FORCED" = "0" ] && [ -f "$LAST_REPORT_FILE" ]; then
    NOW=$(date +%s)
    LT=$(cat "$LAST_REPORT_FILE" 2>/dev/null || echo 0)
    case "$LT" in (*[!0-9]*|'') LT=0 ;; esac
    if [ "$LT" != "0" ] && [ $((NOW - LT)) -lt 1800 ]; then
        log "пропуск: последний отчёт моложе 30 минут (принудительный опрос с ядра — «Принудительный опрос»)"
        exit 0
    fi
fi

# --- список сетей: конфиг ядра (единый источник) > ручной env NETWORKS > автоопределение
NETS_SRC="${CFG_NETS:-${NETWORKS:-}}"
if [ -z "$NETS_SRC" ]; then
    NETS_SRC="$(local_networks)"
fi
if [ -n "$NETS_SRC" ]; then
    NETS=$(echo "$NETS_SRC" | tr ',' ' ')
else
    NETS=""
fi
[ -n "$(echo "$NETS" | tr -d ' ')" ] || { log "не определены сети для обхода"; exit 2; }

use_nmap=0
if [ "$SOURCE" = "nmap" ] || { [ "$SOURCE" = "auto" ] && command -v nmap >/dev/null 2>&1; }; then
    use_nmap=1
fi

ITEMS=""
if [ "$use_nmap" = "1" ]; then
    for net in $NETS; do
        log "обход $net (nmap)"
        ITEMS="${ITEMS}$(collect_nmap "$net")
"
    done
else
    log "чтение ARP-таблицы (nmap не найден или SOURCE=arp)"
    ITEMS="${ITEMS}$(collect_arp)
"
fi

# --- дедупликация по IP -> JSON
HOSTS=$(printf '%s' "$ITEMS" | awk -F'\t' '$1 != ""' | awk -F'\t' '!seen[$1]++' | awk -F'\t' '
    {
        item = sprintf("{\"ip\":\"%s\"", $1);
        if ($2 != "") item = item sprintf(",\"mac\":\"%s\"", $2);
        if (NF > 2 && $3 != "") {
            v = $3;
            gsub(/\\/, "\\\\", v);
            gsub(/"/, "\\\"", v);
            item = item sprintf(",\"vendor\":\"%s\"", v);
        }
        item = item "}";
        printf "%s%s", (n++ ? "," : ""), item;
    }')

COUNT=$(printf '%s' "$HOSTS" | tr ',' '\n' | grep -c '"ip"' || true)
log "отправка: хостов $COUNT"

PAYLOAD="{\"hosts\":[${HOSTS}]}"
RESP=$(curl -fsS $CURL_TLS -m "$TIMEOUT" -X POST \
    -H 'Content-Type: application/json' \
    -H "X-Agent-Key: $IPAM_AGENT_KEY" \
    -d "$PAYLOAD" \
    "$IPAM_URL/api/agent/report" 2>&1)
RC=$?
if [ $RC -ne 0 ]; then
    log "не удалось отправить отчёт: $RESP"
    exit 1
fi
APPLIED=$(echo "$RESP" | sed -n 's/.*"applied":\([0-9]*\).*/\1/p')
WITHMAC=$(echo "$RESP" | sed -n 's/.*"with_mac":\([0-9]*\).*/\1/p')
date +%s > "$LAST_REPORT_FILE" 2>/dev/null || true
log "отчёт отправлен: хостов $COUNT (с MAC: ${WITHMAC:-0}), применено ${APPLIED:-?}"

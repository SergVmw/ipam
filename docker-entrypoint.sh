#!/bin/sh
# Стартовый скрипт контейнера ядра:
#  - при первом старте генерирует самоподписанный TLS-сертификат (10 лет),
#    если его ещё нет в /certs (том сохраняется между пересборками);
#  - запускает uvicorn с TLS (или без, если SSL_ENABLED=0 — напр. за reverse-proxy).
set -e

CERT_DIR="${CERT_DIR:-/certs}"
DOMAIN="${IPAM_DOMAIN:-ipam.local}"
SSL_ENABLED="${SSL_ENABLED:-1}"
DOCS_DIR="${DOCS_DIR:-/docs}"

# --- вложения Документации: каталог должен жить ВНЕ слоя образа ---
mkdir -p "$DOCS_DIR" 2>/dev/null || true
case "$DOCS_DIR" in
  /app|/app/*)
    echo "[entrypoint] ВНИМАНИЕ: DOCS_DIR=$DOCS_DIR — внутри слоя образа:"
    echo "[entrypoint] прикреплённые файлы будут ПОТЕРЯНЫ при пересборке контейнера!"
    echo "[entrypoint] Задайте DOCS_DIR=/docs и смонтируйте том (см. docker-compose.yml)."
    ;;
esac
# спасение: файлы, оставшиеся в старом внутриобразном каталоге /app/docs_files
if [ -d /app/docs_files ] && [ "$DOCS_DIR" != "/app/docs_files" ] && [ -n "$(ls -A /app/docs_files 2>/dev/null)" ]; then
  echo "[entrypoint] копирую файлы Документации из старого каталога /app/docs_files -> $DOCS_DIR"
  cp -an /app/docs_files/. "$DOCS_DIR"/ 2>/dev/null || true
fi
echo "[entrypoint] Документация: $DOCS_DIR, файлов: $(ls -A "$DOCS_DIR" 2>/dev/null | wc -l | tr -d ' ')"

if [ "$SSL_ENABLED" = "1" ]; then
    if [ ! -f "$CERT_DIR/ipam.crt" ] || [ ! -f "$CERT_DIR/ipam.key" ]; then
        echo "[entrypoint] генерирую самоподписанный сертификат: CN=$DOMAIN (SAN: $DOMAIN, localhost, 127.0.0.1), срок 10 лет"
        mkdir -p "$CERT_DIR"
        openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
            -keyout "$CERT_DIR/ipam.key" -out "$CERT_DIR/ipam.crt" \
            -subj "/CN=$DOMAIN/O=IPAM" \
            -addext "subjectAltName=DNS:$DOMAIN,DNS:localhost,IP:127.0.0.1"
        chmod 600 "$CERT_DIR/ipam.key"
        chmod 644 "$CERT_DIR/ipam.crt"
    fi
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000 \
        --ssl-certfile "$CERT_DIR/ipam.crt" --ssl-keyfile "$CERT_DIR/ipam.key"
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8000

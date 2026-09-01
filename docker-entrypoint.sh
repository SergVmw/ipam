#!/bin/sh
# Стартовый скрипт контейнера ядра:
#  - при первом старте генерирует самоподписанный TLS-сертификат (10 лет),
#    если его ещё нет в /certs (том сохраняется между пересборками);
#  - запускает uvicorn с TLS (или без, если SSL_ENABLED=0 — напр. за reverse-proxy).
set -e

CERT_DIR="${CERT_DIR:-/certs}"
DOMAIN="${IPAM_DOMAIN:-ipam.local}"
SSL_ENABLED="${SSL_ENABLED:-1}"

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

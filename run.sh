#!/usr/bin/env bash
# Запуск IPAM БЕЗ Docker: один процесс, база в SQLite (отдельная БД не нужна).
#
#   ./run.sh                    # sqlite: ipam.db рядом с проектом, порт 8000
#   PORT=9000 ./run.sh          # другой порт
#   DATABASE_URL=postgresql+asyncpg://u:p@host:5432/ipam ./run.sh   # свой postgres
#
# Переменные: ADMIN_USER, ADMIN_PASSWORD, SEED_DEMO (0/1), DNS_SERVERS, PROBE_PORTS
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
PORT="${PORT:-8000}"

# 1) Python-окружение: нет интерпретатора — чиним (venv --upgrade возвращает
#    ссылки на python, сохраняя site-packages); нет каталога — создаём заново
if [ ! -x "$ROOT/backend/venv/bin/python" ] || ! "$ROOT/backend/venv/bin/python" -c "import fastapi" >/dev/null 2>&1; then
  echo "[setup] чиню/создаю venv..."
  if [ -d "$ROOT/backend/venv/lib" ]; then
    python3 -m venv --upgrade "$ROOT/backend/venv"
  else
    rm -rf "$ROOT/backend/venv"
    python3 -m venv "$ROOT/backend/venv"
  fi
fi
chmod +x "$ROOT/backend/venv/bin/"* 2>/dev/null || true
# 1.5) Зависимости: проверяем импортом, pip вызываем только если чего-то не хватает
if ! "$ROOT/backend/venv/bin/python" -c "
import importlib
for m in ['fastapi','uvicorn','sqlalchemy','aiosqlite','asyncpg','apscheduler','dns','jwt','multipart','pydantic_settings','ldap3','paramiko']:
    importlib.import_module(m)
" >/dev/null 2>&1; then
  "$ROOT/backend/venv/bin/pip" install -q -r "$ROOT/backend/requirements.txt"
fi

# 2) Frontend (один раз, если ещё не собран)
if [ ! -f "$ROOT/static/index.html" ]; then
  echo "[setup] собираю frontend..."
  (cd "$ROOT/frontend" && npm install --no-audit --no-fund && OUT_DIR=../static npm run build)
fi

# 3) Сканеры (необязательно, но желательно)
command -v fping >/dev/null 2>&1 || echo "[hint] fping не найден: sudo apt install fping  (иначе TCP-проба, только сети до /22)"
command -v nmap  >/dev/null 2>&1 || echo "[hint] nmap не найден: sudo apt install nmap  (без него нет MAC-адресов)"

# 4) Запуск
DB="${DATABASE_URL:-sqlite+aiosqlite:///$ROOT/ipam.db}"
cd "$ROOT/backend"
DATABASE_URL="$DB" \
ADMIN_USER="${ADMIN_USER:-admin}" \
ADMIN_PASSWORD="${ADMIN_PASSWORD:-admin}" \
SEED_DEMO="${SEED_DEMO:-0}" \
exec venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
# (python -m uvicorn — не зависит от console-scripts в venv/bin)

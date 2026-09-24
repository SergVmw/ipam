#!/bin/sh
# Деплой: сборка образа ОДИН раз + up обоих инстансов (org1, org2).
# Безопасность вложений Документации:
#  - проверка compose: том docs:/docs и DOCS_DIR: /docs (КОММЕНТАРИИ ИГНОРИРУЮТСЯ),
#    иначе деплой не запускается (файлы ушли бы в слой контейнера и сгорели);
#  - ПЕРЕД up: бэкап тома вложений (backup/docs_<org>_<штамп>.tar.gz);
#  - ПЕРЕД up: СПАСЕНИЕ файлов из старого внутриобразного каталога /app/docs_files
#    (docker cp работает и на остановленном контейнере) в backup/rescue_<org>;
#  - ПОСЛЕ up: спасённые файлы копируются в /docs нового контейнера БЕЗ перезаписи
#    уже существующих; контроль: том есть, число файлов в /docs.
# Использование: deploy.sh [путь-к-коду]   (по умолчанию ./src)
set -e
SRC="${1:-$(cd "$(dirname "$0")" && pwd)/src}"
ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKUP_DIR="$ROOT/backup"

echo "[deploy] build ipam:latest из $SRC"
docker build -t ipam:latest "$SRC"

for org in org1 org2; do
  COMP="$ROOT/$org/docker-compose.yml"
  if [ ! -f "$COMP" ]; then
    echo "[deploy] ОШИБКА: нет файла $COMP"
    exit 1
  fi

  # --- проверка compose: вложения ДОКУМЕНТАЦИИ должны жить в томе (без комментариев) ---
  if ! grep -v '^[[:space:]]*#' "$COMP" | grep -Eq -- "-[[:space:]]*docs:/docs|target:[[:space:]]*/docs"; then
    echo "[deploy] ОШИБКА: в $COMP нет тома docs:/docs (закомментированные строки не считаются) —"
    echo "[deploy] файлы Документации не будут сохраняться между пересборками."
    echo "[deploy] Добавьте в volumes сервиса app: 'docs:/docs' и в volumes в конце: 'docs:' — и повторите деплой."
    exit 1
  fi
  if ! grep -v '^[[:space:]]*#' "$COMP" | grep -Eq 'DOCS_DIR:[[:space:]]*["'"'"']?/docs'; then
    echo "[deploy] ОШИБКА: в $COMP нет DOCS_DIR: /docs в environment сервиса app."
    echo "[deploy] Без этого приложение пишет файлы в /app/docs_files (слой контейнера) — при пересборке они пропадут."
    exit 1
  fi

  # --- текущий контейнер app org-а (ps -a: подходит и остановленный) ---
  APP_CTR=$(docker ps -a --filter "label=com.docker.compose.project=$org" \
                  --filter "label=com.docker.compose.service=app" --format '{{.ID}}' | head -1 || true)

  # --- имя тома вложений: из контейнера (точечно по mount), иначе <org>_docs ---
  DOCS_VOL=""
  if [ -n "$APP_CTR" ]; then
    DOCS_VOL=$(docker inspect "$APP_CTR" \
      --format '{{range .Mounts}}{{if eq .Destination "/docs"}}{{.Name}}{{end}}{{end}}' || true)
  fi
  [ -n "$DOCS_VOL" ] || DOCS_VOL="${org}_docs"

  # --- 1) бэкап тома вложений перед пересозданием контейнера ---
  if docker volume inspect "$DOCS_VOL" >/dev/null 2>&1; then
    mkdir -p "$BACKUP_DIR"
    STAMP=$(date +%Y%m%d_%H%M%S)
    TAR="$BACKUP_DIR/docs_${org}_${STAMP}.tar.gz"
    docker run --rm -v "$DOCS_VOL":/data -v "$BACKUP_DIR":/backup alpine \
      tar czf "/backup/docs_${org}_${STAMP}.tar.gz" -C /data . \
      && echo "[deploy] $org: бэкап тома $DOCS_VOL -> $TAR" \
      || echo "[deploy] $org: ВНИМАНИЕ — бэкап тома $DOCS_VOL не удался"
  else
    echo "[deploy] $org: том $DOCS_VOL ещё не создан — бэкапить нечего"
  fi

  # --- 2) спасение файлов из старого внутриобразного каталога /app/docs_files ---
  STAGE="$BACKUP_DIR/rescue_${org}"
  rm -rf "$STAGE"
  if [ -n "$APP_CTR" ] && docker cp "$APP_CTR:/app/docs_files" "$STAGE" >/dev/null 2>&1 \
     && [ -n "$(ls -A "$STAGE" 2>/dev/null)" ]; then
    echo "[deploy] $org: в старом контейнере найден /app/docs_files ($(ls -A "$STAGE" | wc -l | tr -d ' ') файл(ов)) — после up будет перенесён в /docs"
  else
    rm -rf "$STAGE"
  fi

  echo "[deploy] $org: up"
  (cd "$ROOT/$org" && docker compose up -d)

  NEW_CTR=$(docker ps --filter "label=com.docker.compose.project=$org" \
                  --filter "label=com.docker.compose.service=app" --format '{{.ID}}' | head -1 || true)

  # --- 3) перенос спасённых файлов в /docs (существующие НЕ перезаписываются) ---
  if [ -n "$NEW_CTR" ] && [ -d "$STAGE" ]; then
    for f in "$STAGE"/*; do
      [ -e "$f" ] || continue
      base=$(basename "$f")
      if docker exec "$NEW_CTR" test -e "/docs/$base" 2>/dev/null; then
        echo "[deploy] $org: $base уже есть в /docs — НЕ перезаписан (копия: $STAGE/$base)"
      else
        docker cp "$f" "$NEW_CTR:/docs/" \
          && echo "[deploy] $org: спасён в /docs: $base" \
          || echo "[deploy] $org: ВНИМАНИЕ — не удалось скопировать $base в /docs (копия: $STAGE/$base)"
      fi
    done
  fi

  # --- 4) контроль после up: том создан, число файлов в /docs ---
  if ! docker volume inspect "$DOCS_VOL" >/dev/null 2>&1; then
    echo "[deploy] ВНИМАНИЕ: $org — том $DOCS_VOL не найден после up. Проверьте compose."
  elif [ -n "$NEW_CTR" ]; then
    echo "[deploy] $org: файлов в /docs после up: $(docker exec "$NEW_CTR" sh -c 'ls -A /docs 2>/dev/null | wc -l' | tr -d ' ')"
  fi
done

echo "[deploy] готово:"
docker ps --filter "label=com.docker.compose.service=app" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" || true

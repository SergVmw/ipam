# IPAM-lite

Лёгкая система учёта IP-адресов («только нужное», без лишнего IPAM-функционала):
сети + VLAN, визуальный IP-грид, графики заполняемости, автоматическое сканирование
сетей с авто-определением hostname по DNS и ручной ввод/изменение IP.

Полный дизайн: [design.md](design.md)

## Быстрый старт (Docker)

```
docker compose up --build -d
```

Открыть http://localhost:8000, вход **admin / admin** (сменить через `ADMIN_USER`/`ADMIN_PASSWORD`
в `docker-compose.yml`; пароль меняется только через БД, пока нет UI смены пароля).

Демо-данные (2 VLAN, 3 сети, «живые» IP, история графика): раскомментировать `SEED_DEMO: "1"`.

## Запуск без Docker

Отдельная база **не обязательна**: по умолчанию используется SQLite (файл `ipam.db`
рядом с проектом) — этого хватает на сотни сетей.

```
./run.sh            # venv + сборка фронта (один раз) + запуск на :8000
PORT=9000 ./run.sh  # другой порт
```

Свой PostgreSQL (если нужен):
```
DATABASE_URL="postgresql+asyncpg://user:pass@host:5432/ipam" ./run.sh
```

Сканеры берутся из системы: `sudo apt install fping nmap` (fping — быстрый ICMP,
nmap — даёт MAC-адреса на локальном L2-сегменте). Без них — TCP-проба + предупреждение.

## Вход в систему и домен (AD/LDAP)

- **Локальные пользователи** — пароль хранится в IPAM, добавляются в Настройках → Пользователи.
- **Доменные (AD/LDAP)** — в Настройках → «Домен (AD/LDAP)»: сервер, Base DN, шаблон DN
  (прямой bind, быстрый путь), фильтр поиска + служебная учётка (если bind по шаблону не
  подходит). После первого успешного входа доменный пользователь создаётся автоматически
  (JIT) с ролью по умолчанию; роль потом меняется в таблице «Пользователи».
  Доменных пользователей можно и **заранее** добавить админ (имя из домена, без пароля)
  и сразу назначить роль.
- **Локальный админ** (ADMIN_USER из env) гарантированно существует после каждого старта:
  если его удалили — он пересоздаётся с паролем из ADMIN_PASSWORD.
- Защита: имя доменного пользователя и его пароль менять нельзя (это данные домена),
  последнего админа нельзя удалить/понизить, себя — удалить.

## Агенты (L2-данные для удалённых VLAN)

Схема «маленький скрипт раз в час» (как обсуждали):
1. В ядре: **Настройки → Агенты** → «+ Добавить»: имя, какие сети покрывает, ключ.
2. `agent/ipam_agent.sh` (POSIX sh + curl, **без Python**) ставится на машину в VLAN.
   которая находится в нужном VLAN (коммутатор-хост, шлюз, любой сервер в сегменте).
3. Cron на машине (установщик делает сам):
   ```
   0 * * * * . /etc/ipam_agent.env && /opt/ipam_agent.sh >> /var/log/ipam_agent.log 2>&1
   ```
4. Агент обходит свои сети (nmap, если установлен — даёт IP+MAC+hostname; иначе ARP-таблица
   ОС — IP+MAC) и шлёт POST `/api/agent/report` с заголовком `X-Agent-Key`.
5. Ядро применяет отчёт: живые IP → `used` + MAC + hostname, события `ip_seen`/`mac_changed`,
   анти-флэппинг (IP без отчёта N раз → `offline`). Статус агента (последний отчёт, число
   хостов) виден в Настройках → Агенты.

MAC-адреса так же даёт **nmap в самом ядре** — но только для хостов на том же L2-сегменте,
где стоит приложение (ARP). Один MAC на двух IP — событие `conflict`. fping/TCP MAC не получают.

## Dev-режим (без Docker)

Backend:
```
cd backend
python -m venv .venv && .venv/bin/pip install -r requirements.txt
DATABASE_URL=sqlite+aiosqlite:///./ipam.db SEED_DEMO=1 \
  .venv/bin/uvicorn app.main:app --reload --port 8000
```

Frontend:
```
cd frontend
npm install
npm run dev          # http://localhost:5173, /api проксируется на :8000
```

Сборка фронтенда в каталог, который раздаёт backend: `OUT_DIR=../static npm run build`.

## Настройки (env)

| Переменная | По умолчанию | Описание |
|---|---|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///./ipam.db` | в проде: `postgresql+asyncpg://ipam:ipam@db:5432/ipam` |
| `SECRET_KEY` | dev-значение | ключ JWT — **обязательно сменить** |
| `ADMIN_USER` / `ADMIN_PASSWORD` | `admin` / `admin` | первый админ (создаётся, только если таблица users пуста) |
| `SEED_DEMO` | `0` | демо-данные при первом старте |
| `DNS_SERVERS` | системный резолвер | name-servers для PTR, через запятую |
| `SCAN_CONCURRENCY` | `8` | сколько сетей сканируем одновременно |
| `SCAN_TIMEOUT_MS` | `500` | таймаут «живости» |
| `SCAN_RATE` | `500` | лимит pings/sec (fping) |
| `PROBE_PORTS` | `22,80,443,3389` | fallback TCP-проба, если fping не установлен |
| `FREE_AFTER_MISSES` | `2` | used → offline после N промахов подряд (анти-флэппинг) |

## Как работает скан

1. **Сканер выбирается в Настройках**: `auto` (fping → nmap → TCP), `fping`, `nmap` (-sn),
   TCP-проба (PROBE_PORTS, сети до /22). fping и nmap ставятся в Docker-образе.
   Ошибка сканера (нет прав, нет ответа, тайм-аут) записывается в `scan_run.error`
   и показывается в UI — сканы не молчат.
2. **PTR** (dnspython + fallback на системный резолвер) → hostname подставляется
   автоматически (можно выключить в Настройках). Ручной hostname PTR не затирается.
3. **Diff с БД**: новый живой IP → `used` + событие `ip_seen`; 2 промаха подряд →
   `offline` (анти-флэппинг); живой «зарезервированный» IP → событие `reserved_alive` (раз в 24 ч).
4. Запись снапшота занятости → графики.

Автоскан: у каждой сети свой интервал, планировщик проверяет «поздние» сети каждые 30 с,
глобальный параллелизм ограничен. Время везде хранится в UTC; отображаемый часовой пояс
(±UTC, с шагом 30 мин) задаётся в Настройках.

## API (кратко)

```
POST /api/auth/login                 {username, password} -> {token}
GET  /api/auth/me
GET/POST /api/vlans ; PUT/DELETE /api/vlans/{id}
GET/POST /api/subnets ; GET/PUT/DELETE /api/subnets/{id}
POST /api/subnets/{id}/scan          # ручной запуск (202, фон)
GET  /api/subnets/{id}/scan-runs
GET  /api/subnets/{id}/ips?state=&q=&net=&page=&size=
GET  /api/subnets/{id}/blocks        # сводка по /24 для крупных сетей
GET  /api/subnets/{id}/usage?days=30 # снапшоты + текущее
GET  /api/ips/{ip}  (PUT)            # ручное изменение: state/owner/note/hostname
GET  /api/events?subnet_id=&type=&limit=
GET  /api/overview                   # VLAN -> сети + итоги
```

Роли: `admin` (всё), `operator` (сканы, сети, IP), `viewer` (чтение).
Все изменения пишутся в `audit_log`.

## Структура

```
backend/app/
  main.py          # FastAPI, lifespan (init_db, seed, scheduler), раздаёт static/
  config.py        # настройки (env)
  db.py            # async engine + session
  models.py        # vlan, subnet, ip, scan_run, ip_event, usage_snapshot, user, audit_log
  security.py      # JWT, pbkdf2, роли
  schemas.py       # pydantic-схемы (валидация CIDR)
  service.py       # материализация IP, подсчёты, overlap-чек, audit
  seed.py          # первый админ + демо-данные
  routers/         # auth, vlans, subnets, ips, usage, overview, events
  scanner/
    ping.py        # fping sweep / TCP-fallback
    dns.py         # PTR (dnspython + OS fallback)
    engine.py      # пайплайн: sweep -> PTR -> diff -> события -> снапшот
    scheduler.py   # APScheduler: «поздние» сети каждые 30 с
frontend/src/
  pages/           # Login, Overview, Subnets, SubnetDetail (грид+графики+события), Vlans
  components/      # Chart (ECharts), Modal, IpGrid (+BlockGrid для /21 и шире)
```

## Что намеренно НЕ сделано (TODO / следующие итерации)

- **MAC / L2**: агент с pull-связью (описание — design.md §8); колонка `ip.mac` и
  задел под endpoint уже есть. Детект конфликтов по MAC появится вместе с агентом.
- Webhook-алерты (Telegram/Slack) — события уже пишутся в `ip_event`, нужен только отсылатель.
- Разреженный режим для сетей шире /16 (сейчас материализуется полная таблица IP).
- Exclusion-констрейнт `btree_gist` в DDL (пересечение CIDR сейчас запрещено на уровне приложения).
- Смена пароля в UI, tree-shaking ECharts, экспорт/импорт CSV.

## HTTPS (самоподписанный сертификат)

По умолчанию ядро работает по **HTTPS с самоподписанным сертификатом**:
- при первом старте контейнер сам генерирует сертификат (RSA 2048, срок 10 лет)
  и хранит его в томе `ipam_certs` — он переживает пересборки;
- `IPAM_DOMAIN` в `docker-compose.yml` задаёт имя сертификата (CN + SAN) —
  смените на своё (`ipam.corp.local`), иначе браузер будет показывать
  имя по умолчанию (`ipam.local`);
- `SSL_ENABLED: "0"` — отключить встроенный TLS (если на фронте reverse-proxy
  с настоящим сертификатом).

Для агента при `https://` URL:
- проще всего: агент использует `-k` (принять самоподписанный сертификат);
- правильнее: скопировать `ipam.crt` на машину агента и указать `IPAM_CA_FILE=/path/ipam.crt`
  в env агента — тогда сертификат проверяется как настоящий CA.

Проверка: `openssl s_client -connect ipam:8000 -brief` или открыть
`https://ipam:8000` в браузере (предупреждение о самоподписанном сертификате — норма).

## Roadmap

- [x] **Импорт сетей и VLAN из PHPIPAM** — массовый импорт из REST API PHPIPAM
      (VLAN, сети, IP с hostname/MAC/примечаниями) в Настройках:
      адрес + имя API-приложения (создать в phpIPAM: Administration →
      Edit API settings → Apps, права read) + пользователь/пароль phpIPAM →
      «Проверить» → «Предпросмотр» (dry-run) → «Применить» → отчёт
      «импортировано N сетей, M VLAN, K IP». Проверено по формату v1.8.1.
- [ ] Вебхук-алерты (Telegram/Slack) — ядро готово (события в `ip_event`), нужен отсылатель.
- [ ] Подпись «локальный OUI» для MAC, чей vendor не найден в базе nmap.
- [ ] L2-агент: детект конфликтов MAC на стороне агента.

Уже сделано в текущей сборке: VLAN/сети/теги, IP-грид, графики заполняемости,
скан (fping/nmap/tcp, метод на сеть), MAC + vendor, агенты с удалённой установкой
по SSH, вход из домена AD/LDAP, настройки (DNS, скорость, почта, часовой пояс,
логотип, копирайт), HTTPS с самоподписанным сертификатом.

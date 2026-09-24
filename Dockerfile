# Stage 1: frontend (vite build)
FROM node:20-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# Stage 2: backend (fping + nmap — два варианта сканера, выбор в Настройках)
FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends fping nmap libmagic1 \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY agent /agent
COPY --from=frontend /build/dist ./static
COPY docker-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
# Вложения Документации по умолчанию — в /docs (вне слоя образа): без этой
# строки они писались бы в /app/docs_files и ГИБЛИ при пересборке образа
# (COPY backend/app ./app затирает каталог). VOLUME даёт anonymous-том даже
# при запуске без compose; compose монтирует именованный том (ipam_docs).
ENV DOCS_DIR=/docs
VOLUME /docs
VOLUME /certs
EXPOSE 8000
CMD ["/entrypoint.sh"]

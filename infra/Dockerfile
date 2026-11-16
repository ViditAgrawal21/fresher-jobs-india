# One image, two entrypoints:
#   web service (default CMD)  -> uvicorn api.main:app
#   daily Cloud Run job        -> python -m pipeline.run

# ---- build the React app ----
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---- python runtime ----
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY core/ core/
COPY pipeline/ pipeline/
COPY api/ api/
COPY db/ db/
COPY eval/results/ eval/results/
COPY --from=web /web/dist web/dist

RUN useradd --create-home --uid 10001 app
USER app

# Cloud Run injects $PORT.
CMD exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips="*"

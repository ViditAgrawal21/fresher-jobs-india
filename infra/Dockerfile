# Backend image, two entrypoints:
#   Cloud Run service (default CMD) -> uvicorn api.main:app
#   daily Cloud Run job             -> python -m pipeline.run
# The React app is deployed separately on Vercel (web/vercel.json proxies /api/* here).
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

RUN useradd --create-home --uid 10001 app
USER app

# Cloud Run injects $PORT.
CMD exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips="*"

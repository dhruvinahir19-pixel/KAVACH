# KAVACH-945 scanner — Render free tier (512MB / 0.1 CPU)
FROM python:3.13-slim

WORKDIR /app

# deps first (layer cache)
COPY scanner/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# code (repo root needed: scanner/ imports engine/features_core etc.)
COPY engine ./engine
COPY scanner ./scanner

WORKDIR /app/scanner
ENV PYTHONPATH=/app/scanner:/app/engine \
    PYTHONUNBUFFERED=1

# memory guard: gunicorn 1 worker, threaded flask app, ~350MB soft ceiling
CMD gunicorn --bind 0.0.0.0:${PORT:-8080} --workers 1 --threads 4 \
    --timeout 120 --graceful-timeout 30 --max-requests 200 \
    "kcore.app:create_app()"

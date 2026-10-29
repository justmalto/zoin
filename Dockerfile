FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    ZOIN_ENVIRONMENT=production \
    MAIL_SCHEDULER_ENABLED=true \
    DISCOVERY_ENABLED=true \
    DISCOVERY_DB_PATH=/data/discovery.sqlite3 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    TMPDIR=/tmp \
    ALLOWED_ORIGINS='["https://zoin-three.vercel.app"]'

WORKDIR /app
RUN python -m venv /opt/venv
COPY requirements.lock.txt requirements-playwright.lock.txt ./
RUN pip install --no-cache-dir -r requirements.lock.txt -r requirements-playwright.lock.txt \
    && python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 zoin \
    && mkdir -p /data /app/.local/tmp \
    && chown -R zoin:zoin /data /app/.local
COPY app ./app
COPY scripts/start_backend.py ./scripts/start_backend.py
USER zoin
CMD ["python", "scripts/start_backend.py"]

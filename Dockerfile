FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    ZOIN_ENVIRONMENT=production \
    MAIL_SCHEDULER_ENABLED=true

WORKDIR /app
RUN python -m venv /opt/venv
COPY requirements.lock.txt ./
RUN pip install --no-cache-dir -r requirements.lock.txt \
    && useradd --create-home --uid 10001 zoin
COPY app ./app
COPY scripts/start_backend.py ./scripts/start_backend.py
USER zoin
CMD ["python", "scripts/start_backend.py"]

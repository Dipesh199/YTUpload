FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py processor.py beat_engine.py alembic.ini ./
COPY automation ./automation
COPY migrations ./migrations
COPY tests ./tests
RUN mkdir -p /app/input /app/output /app/templates /app/previews

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app:app --host 0.0.0.0 --port 8000 --no-access-log"]

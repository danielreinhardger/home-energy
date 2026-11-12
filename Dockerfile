# Home energy dashboard: stdlib-only Python service + static web UI.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Berlin

WORKDIR /app
COPY homeenergy/ homeenergy/
COPY web/ web/
COPY run.py .

EXPOSE 8085
# config.json and data/ live in the mounted /config volume
CMD ["python", "/app/run.py", "--config", "/config/config.json", "--data", "/config/data", "serve", "--host", "0.0.0.0"]

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8085/api/state', timeout=8)" || exit 1

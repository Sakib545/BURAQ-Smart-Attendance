FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r requirements.txt

COPY . .
# Face models are baked in so no request ever waits on a download.
RUN python -c "from app.face_ai import ensure_models, models_present; ensure_models(); assert models_present()"
RUN mkdir -p /app/data /app/exports && chmod +x /app/start.sh
RUN test -f /app/static/css/app.css && test -f /app/templates/base.html

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)" || exit 1

CMD ["/app/start.sh"]
